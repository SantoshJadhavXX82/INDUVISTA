"""InduVista host-stats agent.

Runs OUTSIDE Docker (directly on the host OS — Windows or Linux) so the
Diagnostics page can show real host stats instead of the Docker container's
namespaced view. Reads CPU/RAM/disks/processes via psutil and (optionally)
NVIDIA GPU stats via pynvml, then POSTs to the backend every N seconds.

The backend caches the most recent push in memory and serves it back from
GET /api/diagnostics/system-stats with scope='host'. If this agent isn't
running, the backend falls back to its own container-level psutil readings
with scope='container' so the page still shows something — just labelled
clearly so operators know what they're looking at.

Run on Windows from PowerShell:
    cd D:\\INDUVISTA\\host_agent
    pip install -r requirements.txt
    python agent.py

Run on Linux:
    cd /opt/induvista/host_agent
    pip install -r requirements.txt
    python3 agent.py

Or run via systemd / Windows Task Scheduler / NSSM — see README.md.

Network: the agent posts to http://localhost:8000 by default. Override with
INDUVISTA_URL env var if the backend listens elsewhere. The push payload
follows the same shape as the API endpoint returns (with the addition of
top-level `hostname` and `scope='host'` so the backend can pass it through
unchanged to the GET).
"""
from __future__ import annotations

import os
import platform
import socket
import sys
import time
from datetime import datetime, timezone
from urllib import error as _urlerr
from urllib import request as _urlreq
import json

try:
    import psutil  # type: ignore
except ImportError:
    print("ERROR: psutil not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

# Optional NVIDIA GPU support. If pynvml is missing or no NVIDIA drivers
# are present, we just report an empty gpus array — the rest still works.
try:
    import pynvml  # type: ignore
    pynvml.nvmlInit()
    _NVML_OK = True
except Exception:
    _NVML_OK = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BACKEND_URL = os.environ.get("INDUVISTA_URL", "http://localhost:8000").rstrip("/")
PUSH_INTERVAL_SEC = float(os.environ.get("INDUVISTA_PUSH_SEC", "5.0"))
ENDPOINT = f"{BACKEND_URL}/api/diagnostics/host-stats"

# Prime CPU counter — first call returns 0.0; subsequent calls compute the
# delta since the previous call. We get one bogus reading; everything after
# is honest.
psutil.cpu_percent(interval=None)


# ---------------------------------------------------------------------------
# Stat collection
# ---------------------------------------------------------------------------

def collect_cpu() -> dict:
    """CPU snapshot — percent across all cores plus load averages on Unix."""
    try:
        load_avg = list(os.getloadavg())  # AttributeError on Windows
    except (AttributeError, OSError):
        load_avg = None
    return {
        "percent": float(psutil.cpu_percent(interval=None)),
        "count_logical": psutil.cpu_count(logical=True) or 1,
        "count_physical": psutil.cpu_count(logical=False),
        "load_average": load_avg,
    }


def collect_memory() -> dict:
    """Memory snapshot — total/used/available + cached for Task Manager parity.

    psutil's `vm.available` already accounts for OS caches that can be
    reclaimed under pressure (matches what Task Manager calls "Available").
    `used = total - available` is the line operators care about.
    """
    vm = psutil.virtual_memory()
    cached = (getattr(vm, "cached", 0) or 0) + (getattr(vm, "buffers", 0) or 0)
    return {
        "total_bytes": int(vm.total),
        "used_bytes": int(vm.total - vm.available),
        "available_bytes": int(vm.available),
        "cached_bytes": int(cached),
        "percent": float(vm.percent),
    }


def collect_disks() -> list[dict]:
    """One entry per real, non-pseudo filesystem.

    psutil.disk_partitions() returns every drive letter on Windows
    (C:, D:, E:, ...) and every mountpoint on Linux. We skip pseudo
    filesystems (tmpfs, overlay, proc) and CD-ROM-style devices that
    don't have a usable size.
    """
    skip_fstypes = {"tmpfs", "devtmpfs", "overlay", "squashfs", "proc",
                    "sysfs", "cgroup", "cgroup2", "autofs", "fuse.gvfsd-fuse"}
    out: list[dict] = []
    seen: set[str] = set()

    for part in psutil.disk_partitions(all=False):
        if part.fstype in skip_fstypes:
            continue
        # CD/DVD with no medium → access denied or zero size. Skip.
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        if usage.total == 0:
            continue
        # Some filesystems show up under multiple paths (e.g. bind mounts);
        # de-dupe by (device, total). The first one wins (usually the canonical
        # mountpoint).
        dedup_key = f"{part.device}:{usage.total}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        out.append({
            "mountpoint": part.mountpoint,
            "device": part.device,
            "fstype": part.fstype,
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "percent": float(usage.percent),
        })

    # Sort: largest total first (operators usually care about the data drive,
    # which is the biggest).
    out.sort(key=lambda d: -d["total_bytes"])
    return out


def collect_gpus() -> list[dict]:
    """NVIDIA GPU snapshot via pynvml. Empty list if no GPU or pynvml missing.

    AMD/Intel GPUs aren't covered here — pynvml is NVIDIA-only and the
    cross-vendor libraries (GPUtil, etc.) have inconsistent support. For
    those use cases, extend this function with another library. Today
    most industrial servers either have no GPU or have an NVIDIA card.
    """
    if not _NVML_OK:
        return []
    gpus: list[dict] = []
    try:
        n = pynvml.nvmlDeviceGetCount()
        for i in range(n):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            name = pynvml.nvmlDeviceGetName(h)
            # On newer pynvml this is already str; on older bindings it's bytes.
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            try:
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            gpus.append({
                "index": i,
                "name": name,
                "utilization_percent": float(util.gpu),
                "memory_total_bytes": int(mem.total),
                "memory_used_bytes": int(mem.used),
                "memory_percent": float(mem.used / mem.total * 100) if mem.total else 0.0,
                "temperature_c": temp,
            })
    except Exception as e:
        # Don't crash the agent if pynvml hiccups — just report empty.
        print(f"[gpu] pynvml error: {e}", file=sys.stderr)
        return []
    return gpus


def collect_top_processes(limit: int = 10) -> list[dict]:
    """Top processes by CPU. Two-pass because cpu_percent needs an interval."""
    # First pass: prime per-process CPU counters
    procs = list(psutil.process_iter(["pid", "name"]))
    for p in procs:
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # Brief sample window — anything shorter and most processes read 0.
    time.sleep(0.3)
    # Second pass: read deltas
    out: list[dict] = []
    for p in procs:
        try:
            with p.oneshot():
                cpu = p.cpu_percent(interval=None)
                mem_info = p.memory_info()
                out.append({
                    "pid": p.pid,
                    "name": (p.name() or "?")[:40],
                    "cpu_percent": float(cpu),
                    "memory_bytes": int(mem_info.rss),
                    "memory_percent": float(p.memory_percent()),
                    "threads": p.num_threads(),
                    "started_at": datetime.fromtimestamp(
                        p.create_time(), tz=timezone.utc,
                    ).isoformat(),
                    "is_self": False,  # this agent isn't relevant on the host
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    out.sort(key=lambda x: (-x["cpu_percent"], -x["memory_bytes"]))
    return out[:limit]


def collect_uptime() -> int:
    """Seconds since the host OS booted."""
    try:
        return int(time.time() - psutil.boot_time())
    except Exception:
        return 0


def build_payload() -> dict:
    """One full snapshot, ready to POST."""
    return {
        "scope": "host",
        "hostname": socket.gethostname(),
        "platform": platform.system(),  # 'Windows', 'Linux', 'Darwin'
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_sec": collect_uptime(),
        "cpu": collect_cpu(),
        "memory": collect_memory(),
        "disks": collect_disks(),
        "gpus": collect_gpus(),
        "top_processes": collect_top_processes(),
    }


# ---------------------------------------------------------------------------
# Push loop
# ---------------------------------------------------------------------------

def post_payload(payload: dict) -> None:
    """POST to the backend. Network errors logged but don't kill the agent."""
    body = json.dumps(payload).encode("utf-8")
    req = _urlreq.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlreq.urlopen(req, timeout=5) as resp:
            if resp.status >= 400:
                print(f"[push] backend returned {resp.status}", file=sys.stderr)
    except _urlerr.URLError as e:
        # Backend down? That's normal during startup or restarts. Don't spam.
        print(f"[push] backend unreachable: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[push] error: {e}", file=sys.stderr)


def main() -> int:
    print(f"InduVista host-stats agent")
    print(f"  Reporting to : {ENDPOINT}")
    print(f"  Push interval: {PUSH_INTERVAL_SEC}s")
    print(f"  Hostname     : {socket.gethostname()}")
    print(f"  Platform     : {platform.system()}")
    print(f"  GPU support  : {'NVIDIA via pynvml' if _NVML_OK else 'disabled'}")
    print()

    while True:
        try:
            payload = build_payload()
            post_payload(payload)
        except KeyboardInterrupt:
            print("\nShutting down (Ctrl-C).")
            return 0
        except Exception as e:
            print(f"[agent] unexpected error: {e}", file=sys.stderr)

        try:
            time.sleep(PUSH_INTERVAL_SEC)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
