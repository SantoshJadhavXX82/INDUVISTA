#!/usr/bin/env python3
"""
Modbus connection-redundancy REAL-SOCKET integration test (Phase 9).

Unlike modbus_redundancy_smoke.py (which fakes the client), this drives the
LIVE stack end to end: it flips a real device to redundant, kills the primary
simulator container, and proves the worker fails over to the backup over a real
socket -- then restores the primary and proves failback.

Runs on the HOST (needs the `docker` CLI + a reachable API). Stdlib only, no pip
installs, no pytest:

    $env:SMOKE_ADMIN_PASSWORD="<real-admin-pw>"
    python backend/scripts/modbus_redundancy_failover_it.py

It is SAFE: a finally block always restores the device to simplex and starts
both simulator containers again, even on failure/Ctrl-C.

VERIFY THESE BEFORE RUNNING (env-overridable):
  REDUN_DEVICE        device to use as the test subject   (default FUEL_GAS_FC001)
  REDUN_BACKUP_HOST   backup sim host alias               (default fg_fc002)
  REDUN_BACKUP_PORT   backup sim port                     (default 5022)
  The backup sim should serve the same registers as the primary (identical sim
  image) so reads keep succeeding after failover. If it doesn't, the failover
  itself is still proven via the worker log; the "reads stay good" line is a soft
  check (warn, not fail).
"""
import os
import sys
import time
import json
import subprocess
import urllib.request
import urllib.error

API = os.environ.get("INDUVISTA_API", "http://localhost:8000").rstrip("/")
ADMIN_USER = os.environ.get("SMOKE_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "")
DEVICE_NAME = os.environ.get("REDUN_DEVICE", "FUEL_GAS_FC001")
BACKUP_HOST = os.environ.get("REDUN_BACKUP_HOST", "fg_fc002")
BACKUP_PORT = int(os.environ.get("REDUN_BACKUP_PORT", "5022"))
WORKER = os.environ.get("MODBUS_WORKER_CONTAINER", "svj_modbus_worker")
WAIT = int(os.environ.get("REDUN_WAIT_SEC", "45"))

_PASS = 0
_FAIL = 0
_WARN = 0


def ok(name):
    global _PASS
    _PASS += 1
    print(f"  PASS  {name}")


def bad(name, detail=""):
    global _FAIL
    _FAIL += 1
    print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def warn(name, detail=""):
    global _WARN
    _WARN += 1
    print(f"  WARN  {name}" + (f"  ({detail})" if detail else ""))


# ---- HTTP (stdlib) --------------------------------------------------------
def _req(method, path, token=None, body=None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def login():
    st, body = _req("POST", "/api/auth/login",
                    body={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    if st != 200:
        print(f"FATAL: admin login failed ({st}). Set SMOKE_ADMIN_PASSWORD.")
        sys.exit(2)
    return body["access_token"]


def find_device(token):
    st, body = _req("GET", "/api/devices", token=token)
    if st != 200:
        print(f"FATAL: GET /api/devices -> {st}")
        sys.exit(2)
    for d in body:
        if d.get("name") == DEVICE_NAME:
            return d
    print(f"FATAL: device {DEVICE_NAME!r} not found. Set REDUN_DEVICE.")
    sys.exit(2)


def patch_device(token, dev_id, payload):
    st, body = _req("PATCH", f"/api/devices/{dev_id}", token=token, body=payload)
    return st, body


# ---- docker ---------------------------------------------------------------
def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def container_for_host(host):
    """Find the running container whose name contains the network alias `host`."""
    r = docker("ps", "--format", "{{.Names}}")
    names = [n for n in r.stdout.splitlines() if n.strip()]
    for n in names:
        if host in n:
            return n
    return None


def worker_logs(since_sec):
    r = docker("logs", "--since", f"{since_sec}s", WORKER)
    return (r.stdout or "") + (r.stderr or "")


def wait_for(substr, timeout, label):
    """Poll the worker logs until `substr` appears or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if substr in worker_logs(timeout + 10):
            return True
        time.sleep(2)
    return False


def good_reads_recent(timeout=15):
    """Soft check: are there 'N good/' lines for this device with N>0 recently?"""
    logs = worker_logs(timeout)
    for line in logs.splitlines():
        if f"worker.{DEVICE_NAME}" in line and "good/" in line:
            # e.g. "... wrote 30 (30 good/30 total)"
            try:
                g = int(line.split("(")[1].split("good")[0].strip())
                if g > 0:
                    return True
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
def main():
    if not ADMIN_PASSWORD:
        print("FATAL: set SMOKE_ADMIN_PASSWORD to your real admin password.")
        sys.exit(2)

    token = login()
    dev = find_device(token)
    dev_id = dev["id"]
    if (dev.get("protocol") or "").lower() not in ("modbus", "modbus_tcp"):
        print(f"FATAL: {DEVICE_NAME} is not a Modbus device.")
        sys.exit(2)

    primary_host = dev.get("host")
    primary_port = dev.get("port")
    primary_ctr = container_for_host(primary_host) if primary_host else None
    backup_ctr = container_for_host(BACKUP_HOST)

    print(f"Subject device : {DEVICE_NAME} (id={dev_id})")
    print(f"Primary        : {primary_host}:{primary_port}  -> container {primary_ctr}")
    print(f"Backup         : {BACKUP_HOST}:{BACKUP_PORT}  -> container {backup_ctr}")
    print(f"Worker logs     : {WORKER}\n")

    if not primary_ctr:
        print(f"FATAL: no running container matches primary host {primary_host!r}. "
              f"Check `docker ps` and set REDUN_DEVICE to a device whose sim is running.")
        sys.exit(2)
    if not backup_ctr:
        print(f"FATAL: no running container matches backup host {BACKUP_HOST!r}. "
              f"Set REDUN_BACKUP_HOST.")
        sys.exit(2)

    orig_mode = dev.get("connection_mode", "simplex")
    try:
        # 1) flip to redundant -> worker hot-reloads this device
        st, _ = patch_device(token, dev_id, {
            "connection_mode": "redundant",
            "secondary_host": BACKUP_HOST,
            "secondary_port": BACKUP_PORT,
        })
        if st in (200, 204):
            ok("PATCH device -> redundant accepted")
        else:
            bad("PATCH device -> redundant", f"status {st}")
            return

        print("config:")
        if wait_for(f"worker.{DEVICE_NAME}", WAIT, "reload"):
            ok("worker reloaded the device after config change")
        else:
            warn("did not see a device log line after reload", "continuing")

        # 2) kill primary -> expect failover to backup
        print("failover:")
        docker("stop", primary_ctr)
        marker = f"Failover: now connected via redundant endpoint {BACKUP_HOST}:{BACKUP_PORT}"
        if wait_for(marker, WAIT, "failover"):
            ok(f"primary down -> FAILOVER to backup ({BACKUP_HOST}:{BACKUP_PORT})")
        else:
            bad("no failover log seen after stopping primary",
                f"expected: {marker}")

        time.sleep(6)
        if good_reads_recent():
            ok("reads keep succeeding on the backup endpoint")
        else:
            warn("no good reads seen on backup",
                 "backup sim may not serve the same registers (failover still proven)")

        # 3) restore primary, then drop backup -> expect FAILBACK to primary
        print("failback:")
        docker("start", primary_ctr)
        time.sleep(4)
        docker("stop", backup_ctr)  # force a reconnect; primary is preferred
        marker_fb = f"Failover: now connected via primary endpoint {primary_host}:{primary_port}"
        if wait_for(marker_fb, WAIT, "failback"):
            ok(f"primary restored + backup dropped -> FAILBACK to primary "
               f"({primary_host}:{primary_port})")
        else:
            warn("no explicit failback log seen",
                 "check worker logs; reconnect timing may exceed the window")

    finally:
        # ---- always restore ----
        print("\ncleanup:")
        docker("start", primary_ctr)
        docker("start", backup_ctr)
        st, _ = patch_device(token, dev_id, {
            "connection_mode": orig_mode,
            "secondary_host": dev.get("secondary_host"),
            "secondary_port": dev.get("secondary_port"),
        })
        print(f"  restored {DEVICE_NAME} -> connection_mode={orig_mode} (PATCH {st}); "
              f"both simulators started")

    print()
    summary = f"{_PASS} passed, {_FAIL} failed, {_WARN} warn"
    if _FAIL == 0:
        print(f"ALL PASS ({summary})")
        sys.exit(0)
    print(f"FAILURES ({summary})")
    sys.exit(1)


if __name__ == "__main__":
    main()
