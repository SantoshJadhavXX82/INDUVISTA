"""Diagnostic endpoints — read-only views of system health.

Five endpoints under /api/diagnostics:

  /summary          : counts of all problem types (good for monitoring dashboards)
  /tag-overlaps     : tag pairs with overlapping address ranges
  /tag-block-fit    : tags whose linkage to their declared register_block is broken
  /stale-tags       : tags currently flagged stale (ST < 128 or age > stale_after_sec)
  /data-gaps/{id}   : time gaps in tag_values for a specific tag

The first four are O(n) over the tags/register_blocks tables — fast even with
thousands of tags. /data-gaps reads from the tag_values hypertable; bound the
time range with ?since=<ISO timestamp> to keep it cheap on long-running systems.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.config import settings

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TagOverlap(BaseModel):
    device_id: int
    device_name: str
    function_code: int
    tag1_id: int
    tag1_name: str
    tag1_address: int
    tag1_register_count: int
    tag2_id: int
    tag2_name: str
    tag2_address: int
    tag2_register_count: int


class TagBlockFitIssue(BaseModel):
    tag_id: int
    tag_name: str
    device_id: int
    device_name: str
    block_id: int
    block_name: str
    issue: str


class StaleTag(BaseModel):
    tag_id: int
    tag_name: str
    device_id: int
    device_name: str
    last_seen: datetime
    age_seconds: float
    stale_after_sec: int
    st: int
    st_reason: str | None


class DataGap(BaseModel):
    tag_id: int
    gap_start: datetime
    gap_end: datetime
    gap_seconds: float


class DiagnosticsSummary(BaseModel):
    enabled_tag_count: int
    enabled_device_count: int
    overlap_count: int
    block_fit_issue_count: int
    stale_tag_count: int
    # Phase 5b additions
    workers_healthy: int
    workers_unhealthy: int
    buffer_backlog: int


class WorkerDeviceStatus(BaseModel):
    device_id: int
    device_name: str
    last_cycle_at: datetime | None
    last_cycle_samples_total: int | None
    last_cycle_samples_good: int | None
    cumulative_samples_total: int
    cumulative_samples_good: int
    consecutive_failures: int
    connection_state: str
    updated_at: datetime
    seconds_since_last_cycle: float | None


class BufferHealth(BaseModel):
    backlog: int
    oldest_sample_at: datetime | None
    oldest_sample_age_seconds: float | None
    last_replay_at: datetime | None
    last_replay_count: int | None
    updated_at: datetime
    status: str  # "healthy" | "buffering" | "stuck"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=DiagnosticsSummary)
def diagnostics_summary(db: Annotated[Session, Depends(get_session)]):
    """One-shot health snapshot. Hit this from monitoring dashboards.

    Zero overlap_count, zero block_fit_issue_count, zero stale_tag_count
    is the green-light condition.
    """
    overlap_count = db.execute(text("""
        WITH tag_span AS (
          SELECT t.*,
                 d.protocol AS device_protocol,
                 CASE
                   WHEN b.addressing_mode IN ('ENRON_HOLDING', 'ENRON_INPUT')
                     THEN 1
                   ELSE t.register_count
                 END AS effective_span
            FROM tags t
            JOIN devices d ON d.id = t.device_id
            LEFT JOIN register_blocks b ON b.id = t.register_block_id
        )
        -- Phase 17 — exclude computed devices. Their tags share the sentinel
        -- (function_code=3, address=0) so a naive overlap query would emit
        -- N*(N-1)/2 phantom pairs per computed device. The per-tag overlap
        -- validation in app/api/_validation.py already short-circuits for
        -- protocol='computed'; this matches that behavior at the bulk-query
        -- level so the diagnostics summary count is meaningful.
        SELECT COUNT(*) FROM tag_span t1
        JOIN tag_span t2
          ON t1.device_id = t2.device_id
         AND t1.function_code = t2.function_code
         AND t1.id < t2.id
        -- OPC UA tags have no register address; they all carry the
        -- placeholder (function_code=3, address=0), so a naive overlap
        -- query emits N*(N-1)/2 phantom pairs per OPC source. Excluded
        -- here exactly as computed-device tags are.
        WHERE t1.device_protocol NOT IN ('computed', 'opc_ua')
          AND t2.device_protocol NOT IN ('computed', 'opc_ua')
          AND t1.address < t2.address + t2.effective_span
          AND t2.address < t1.address + t1.effective_span
    """)).scalar()

    fit_count = db.execute(text("""
        SELECT COUNT(*) FROM tags t
        JOIN register_blocks b ON b.id = t.register_block_id
        WHERE t.function_code <> b.function_code
           OR t.address < b.start_address
           OR t.address + (
                CASE
                  WHEN b.addressing_mode IN ('ENRON_HOLDING', 'ENRON_INPUT')
                    THEN 1
                  ELSE t.register_count
                END
              ) > b.start_address + b.count
    """)).scalar()

    stale_count = db.execute(text("""
        SELECT COUNT(*) FROM latest_tag_values lv
        JOIN tags t ON t.id = lv.tag_id
        JOIN devices d ON d.id = t.device_id
        WHERE
          -- Phase 17 — writable tags are command/setpoint registers. They
          -- sit at the same value indefinitely by design (a start command
          -- stays at 1 until cleared). The worker often doesn't read them
          -- back, so their last-seen timestamp is the initial seed value.
          -- Counting them as "stale" produces false positives that bury
          -- the real issues. Heartbeat-monitored writable tags still get
          -- their own freshness check via the HEARTBEAT_FROZEN code path.
          t.writable = false
          -- Computed-tag st reflects INPUT quality (quality propagates),
          -- not freshness. A calc block with bad inputs correctly emits
          -- st<128; that is not a stale reading. Exclude computed devices
          -- here, mirroring the overlap query's computed exclusion.
          AND d.protocol != 'computed'
          AND (
            lv.st < 128
            OR EXTRACT(EPOCH FROM (NOW() - lv.time)) > d.stale_after_sec
          )
    """)).scalar()

    tag_count = db.execute(
        text("SELECT COUNT(*) FROM tags WHERE enabled = true")
    ).scalar()
    device_count = db.execute(
        text("SELECT COUNT(*) FROM devices WHERE enabled = true")
    ).scalar()

    # Phase 5b: pull worker health and buffer state from the operational
    # tables the supervisor writes to. "Healthy" = connected, last cycle
    # within stale_after_sec, no consecutive failures.
    worker_rows = db.execute(text("""
        SELECT
            wds.connection_state,
            wds.consecutive_failures,
            EXTRACT(EPOCH FROM (NOW() - wds.last_cycle_at))::float AS age_sec,
            d.stale_after_sec
        FROM worker_device_status wds
        JOIN devices d ON d.id = wds.device_id
        WHERE d.enabled = true
    """)).mappings().all()
    workers_healthy = sum(
        1 for r in worker_rows
        if r["connection_state"] == "connected"
        and r["consecutive_failures"] == 0
        and r["age_sec"] is not None
        and r["age_sec"] <= r["stale_after_sec"]
    )
    workers_unhealthy = len(worker_rows) - workers_healthy

    buffer_backlog = db.execute(
        text("SELECT backlog FROM worker_buffer_status WHERE id = 1")
    ).scalar() or 0

    return DiagnosticsSummary(
        enabled_tag_count=tag_count or 0,
        enabled_device_count=device_count or 0,
        overlap_count=overlap_count or 0,
        block_fit_issue_count=fit_count or 0,
        stale_tag_count=stale_count or 0,
        workers_healthy=workers_healthy,
        workers_unhealthy=workers_unhealthy,
        buffer_backlog=buffer_backlog,
    )


@router.get("/worker-status", response_model=list[WorkerDeviceStatus])
def list_worker_device_status(db: Annotated[Session, Depends(get_session)]):
    """Per-device runtime state: last cycle time, samples written, connection state.

    Updated by the supervisor at the end of every poll cycle. A row not
    showing up here means the worker has never reported for that device —
    either it's never been polled or it can't reach Postgres at all.
    """
    rows = db.execute(text("""
        SELECT
            wds.device_id, d.name AS device_name,
            wds.last_cycle_at,
            wds.last_cycle_samples_total, wds.last_cycle_samples_good,
            wds.cumulative_samples_total, wds.cumulative_samples_good,
            wds.consecutive_failures, wds.connection_state,
            wds.updated_at,
            EXTRACT(EPOCH FROM (NOW() - wds.last_cycle_at))::float
                AS seconds_since_last_cycle
        FROM worker_device_status wds
        JOIN devices d ON d.id = wds.device_id
        ORDER BY wds.device_id
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/buffer-health", response_model=BufferHealth)
def buffer_health(db: Annotated[Session, Depends(get_session)]):
    """Store-and-forward buffer state. Updated every 10s by the supervisor.

    Status interpretation:
      - "healthy"  : backlog is 0, nothing stuck
      - "buffering": backlog > 0 with a recent oldest_sample_at (current outage)
      - "stuck"    : backlog > 0 with oldest_sample_at older than 5 minutes
                     (Postgres has been unreachable for a long time, or
                     there's an FK-violation pattern the recovery isn't draining)
    """
    row = db.execute(text("""
        SELECT
            backlog, oldest_sample_at,
            last_replay_at, last_replay_count, updated_at,
            EXTRACT(EPOCH FROM (NOW() - oldest_sample_at))::float
                AS oldest_age_sec
        FROM worker_buffer_status
        WHERE id = 1
    """)).mappings().first()

    if not row:
        # The migration seeds id=1, so this should never happen — but if
        # someone deleted the row manually, fall back to "unknown".
        raise HTTPException(503, "worker_buffer_status singleton missing")

    backlog = row["backlog"] or 0
    oldest_age = row["oldest_age_sec"]
    if backlog == 0:
        status = "healthy"
    elif oldest_age is not None and oldest_age > 300:
        status = "stuck"
    else:
        status = "buffering"

    return BufferHealth(
        backlog=backlog,
        oldest_sample_at=row["oldest_sample_at"],
        oldest_sample_age_seconds=oldest_age,
        last_replay_at=row["last_replay_at"],
        last_replay_count=row["last_replay_count"],
        updated_at=row["updated_at"],
        status=status,
    )


@router.get("/tag-overlaps", response_model=list[TagOverlap])
def list_tag_overlaps(db: Annotated[Session, Depends(get_session)]):
    """Find every pair of tags whose address ranges intersect on the same FC.

    For tags in Enron-mode blocks the effective address span is 1 (each
    Enron logical address holds one value), so neighbouring tags at
    addresses N and N+1 do NOT overlap even though their register_counts
    suggest a byte-range collision.
    """
    rows = db.execute(text("""
        WITH tag_span AS (
          SELECT t.*,
                 CASE
                   WHEN b.addressing_mode IN ('ENRON_HOLDING', 'ENRON_INPUT')
                     THEN 1
                   ELSE t.register_count
                 END AS effective_span
            FROM tags t
            LEFT JOIN register_blocks b ON b.id = t.register_block_id
        )
        SELECT
          t1.device_id, d.name as device_name, t1.function_code,
          t1.id as tag1_id, t1.name as tag1_name,
          t1.address as tag1_address, t1.register_count as tag1_register_count,
          t2.id as tag2_id, t2.name as tag2_name,
          t2.address as tag2_address, t2.register_count as tag2_register_count
        FROM tag_span t1
        JOIN tag_span t2
          ON t1.device_id = t2.device_id
         AND t1.function_code = t2.function_code
         AND t1.id < t2.id
        JOIN devices d ON d.id = t1.device_id
        -- OPC UA tags have no register address (see overlap_count note in
        -- diagnostics_summary); exclude them alongside computed tags.
        WHERE d.protocol NOT IN ('computed', 'opc_ua')
        AND t1.address < t2.address + t2.effective_span
          AND t2.address < t1.address + t1.effective_span
        ORDER BY t1.device_id, t1.function_code, t1.address
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/tag-block-fit", response_model=list[TagBlockFitIssue])
def list_tag_block_fit_issues(db: Annotated[Session, Depends(get_session)]):
    """Tags whose declared register_block can't contain them.

    For Enron blocks, fit is measured in logical addresses (span=1 per tag),
    not byte ranges — block count of 16 holds 16 Enron values regardless of
    each value's wire width.
    """
    rows = db.execute(text("""
        SELECT
          t.id as tag_id, t.name as tag_name,
          t.device_id, d.name as device_name,
          t.function_code as tag_fc,
          t.address as tag_addr, t.register_count as tag_rc,
          b.id as block_id, b.name as block_name,
          b.function_code as block_fc,
          b.start_address as block_start, b.count as block_count
        FROM tags t
        JOIN register_blocks b ON b.id = t.register_block_id
        JOIN devices d ON d.id = t.device_id
        WHERE t.function_code <> b.function_code
           OR t.address < b.start_address
           OR t.address + (
                CASE
                  WHEN b.addressing_mode IN ('ENRON_HOLDING', 'ENRON_INPUT')
                    THEN 1
                  ELSE t.register_count
                END
              ) > b.start_address + b.count
        ORDER BY t.device_id, t.function_code, t.address
    """)).mappings().all()

    out: list[dict] = []
    for r in rows:
        problems: list[str] = []
        if r["tag_fc"] != r["block_fc"]:
            problems.append(
                f"function_code mismatch (tag={r['tag_fc']}, block={r['block_fc']})"
            )
        if r["tag_addr"] < r["block_start"]:
            problems.append(
                f"tag address {r['tag_addr']} below block start {r['block_start']}"
            )
        tag_end = r["tag_addr"] + r["tag_rc"]
        block_end = r["block_start"] + r["block_count"]
        if tag_end > block_end:
            problems.append(
                f"tag spans past block end (tag end {tag_end}, block end {block_end})"
            )
        out.append({
            "tag_id": r["tag_id"],
            "tag_name": r["tag_name"],
            "device_id": r["device_id"],
            "device_name": r["device_name"],
            "block_id": r["block_id"],
            "block_name": r["block_name"],
            "issue": "; ".join(problems),
        })
    return out


@router.get("/stale-tags", response_model=list[StaleTag])
def list_stale_tags(db: Annotated[Session, Depends(get_session)]):
    """Tags currently marked stale or with an ST class below VALID.

    Combines two definitions of "stale":
      1. The device's stale_detection_loop already flagged it (st < 128 in
         latest_tag_values).
      2. Its last-seen time is older than its device's stale_after_sec, even
         if the loop hasn't run yet for it.
    """
    rows = db.execute(text("""
        SELECT
          lv.tag_id, t.name as tag_name,
          t.device_id, d.name as device_name,
          lv.time as last_seen,
          EXTRACT(EPOCH FROM (NOW() - lv.time))::float as age_seconds,
          d.stale_after_sec, lv.st, lv.st_reason
        FROM latest_tag_values lv
        JOIN tags t ON t.id = lv.tag_id
        JOIN devices d ON d.id = t.device_id
        WHERE
          -- Phase 17 — writable tags exempted from stale detection. See
          -- the matching filter in diagnostics_summary's stale_count.
          t.writable = false
          -- See diagnostics_summary stale_count: computed-tag st reflects
          -- input quality, not freshness. Exclude computed devices.
          AND d.protocol != 'computed'
          AND (
            lv.st < 128
            OR EXTRACT(EPOCH FROM (NOW() - lv.time)) > d.stale_after_sec
          )
        ORDER BY age_seconds DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/data-gaps/{tag_id}", response_model=list[DataGap])
def find_data_gaps(
    tag_id: int,
    db: Annotated[Session, Depends(get_session)],
    since: Annotated[
        datetime | None,
        Query(description="UTC timestamp; defaults to 24h ago"),
    ] = None,
    min_gap_sec: Annotated[
        float,
        Query(ge=1.0, description="Minimum gap to report (seconds)"),
    ] = 10.0,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
):
    """Find time gaps in tag_values for one tag, longest first.

    A "gap" is the interval between two consecutive samples that exceeds
    `min_gap_sec`. Defaults: last 24 hours, gaps > 10 seconds.

    Use this to spot polling outages, network blips, or replay backlogs
    that never caught up. The default min_gap_sec of 10s is roughly 10x
    a 1Hz scan rate — small enough to catch real outages, large enough
    to ignore micro-jitter from cycle scheduling.
    """
    tag = db.execute(
        text("SELECT id, name FROM tags WHERE id = :id"),
        {"id": tag_id},
    ).mappings().first()
    if not tag:
        raise HTTPException(404, f"tag {tag_id} not found")

    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    rows = db.execute(text("""
        WITH samples AS (
          SELECT time,
                 LAG(time) OVER (ORDER BY time) AS prev_time
          FROM tag_values
          WHERE tag_id = :tag_id AND time >= :since
        )
        SELECT prev_time AS gap_start,
               time      AS gap_end,
               EXTRACT(EPOCH FROM (time - prev_time))::float AS gap_seconds
        FROM samples
        WHERE prev_time IS NOT NULL
          AND time - prev_time > make_interval(secs => :min_gap_sec)
        ORDER BY gap_seconds DESC
        LIMIT :limit
    """), {
        "tag_id": tag_id,
        "since": since,
        "min_gap_sec": min_gap_sec,
        "limit": limit,
    }).mappings().all()

    return [
        {
            "tag_id": tag_id,
            "gap_start": r["gap_start"],
            "gap_end": r["gap_end"],
            "gap_seconds": r["gap_seconds"],
        }
        for r in rows
    ]


# ===========================================================================
# Phase 12.6 — system resources + operator limit warnings
# ===========================================================================

class CpuStats(BaseModel):
    percent: float                # 0-100 across all cores
    count_logical: int
    count_physical: int | None
    load_average: list[float] | None   # [1m, 5m, 15m] — None on Windows


class MemoryStats(BaseModel):
    total_bytes: int
    used_bytes: int               # = total - available
    available_bytes: int
    cached_bytes: int             # OS file cache + buffers (reclaimable)
    percent: float                # 0-100


class DiskUsage(BaseModel):
    mountpoint: str               # "C:\\" on Windows, "/" on Linux
    device: str | None = None     # e.g. "/dev/sda1" or "\\\\?\\Volume{...}"
    fstype: str | None = None     # ntfs / ext4 / xfs / apfs ...
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float                # 0-100 — over-90 warrants attention


class GpuStats(BaseModel):
    """Per-GPU snapshot. NVIDIA only today (via pynvml on the host agent)."""
    index: int
    name: str
    utilization_percent: float
    memory_total_bytes: int
    memory_used_bytes: int
    memory_percent: float
    temperature_c: int | None


class ProcessInfo(BaseModel):
    pid: int
    name: str                     # process command name (truncated)
    cpu_percent: float
    memory_bytes: int
    memory_percent: float
    threads: int
    started_at: datetime | None
    is_self: bool                 # True for the backend's own process (container scope only)


class SystemStats(BaseModel):
    """System resource snapshot — either real host or backend container.

    `scope` tells you which:
      * "host"      — pushed in by the host-side agent (psutil running natively
                       on Windows/Linux). Real Task Manager / top parity.
      * "container" — fallback when no host agent has reported recently.
                       Numbers reflect the backend container's namespaced view
                       (typically: 1 disk, a few processes, CPU%/RAM% relative
                       to the container's limits, not the physical machine).

    Operators want the "host" reading. The "container" reading still exists so
    the page never breaks and so dev environments without the agent installed
    aren't blank.
    """
    scope: str = "container"
    hostname: str | None = None         # set when scope='host'
    platform: str | None = None         # "Windows" / "Linux" / "Darwin"
    host_agent_last_seen_sec: int | None = None  # age of last push, None if never
    timestamp: datetime
    uptime_sec: int                     # backend uptime (container) or host boot age
    cpu: CpuStats
    memory: MemoryStats
    disks: list[DiskUsage]              # one entry per mount/drive
    gpus: list[GpuStats] = []           # empty if no GPUs detected
    top_processes: list[ProcessInfo]    # by CPU descending, capped at 10


class OutOfRangeTag(BaseModel):
    """A tag whose current value violates its operator-defined min/max."""
    tag_id: int
    tag_name: str
    device_id: int
    device_name: str
    value_double: float | None
    engineering_unit: str | None
    min_value: float | None
    max_value: float | None
    violation: str                # 'LOW' or 'HIGH'
    last_seen: datetime
    st: int
    st_reason: str | None


# ---------------------------------------------------------------------------
# psutil module-level import + process handle. Module-import is preferred
# over per-request because psutil.cpu_percent() needs a baseline interval —
# the first call after import returns 0.0; subsequent calls compute the
# delta against the previous one. Calling at import time primes that.
# ---------------------------------------------------------------------------
import os
import time as _time

try:
    import psutil  # type: ignore
    _PSUTIL_OK = True
    psutil.cpu_percent(interval=None)  # prime the running counter
    _BACKEND_PROCESS = psutil.Process(os.getpid())
    _BACKEND_PROCESS.cpu_percent(interval=None)
except Exception:  # pragma: no cover — psutil may not be importable
    _PSUTIL_OK = False
    psutil = None  # type: ignore
    _BACKEND_PROCESS = None  # type: ignore


@router.get("/system-stats", response_model=SystemStats)
def system_stats():
    """System resources for the Diagnostics page.

    Two-tier read:
      1. If the host-side agent has POSTed within the last 30 seconds,
         return that cached snapshot with scope='host'. These are real
         host metrics — Windows Task Manager / Linux top parity.
      2. Otherwise, fall back to in-container psutil readings labelled
         scope='container' so the page never breaks and devs without the
         agent installed still see something useful.

    See host_agent/README.md for how to run the agent.
    """
    if not _PSUTIL_OK:
        raise HTTPException(503, "psutil unavailable — install psutil>=6.0")

    # ---- Tier 1: host-agent push, if fresh ---------------------------------
    cached = _HOST_STATS_CACHE.get("payload")
    cached_at = _HOST_STATS_CACHE.get("received_at_mono")
    if cached and cached_at is not None:
        age = _time.monotonic() - cached_at
        if age < _HOST_STATS_MAX_AGE_SEC:
            # Re-stamp the timestamp with the receive-time so age in the UI is
            # based on "when the backend got it", not "when the agent built it"
            # (which could be skewed by clock drift). Everything else passes
            # through unchanged.
            payload = dict(cached)
            payload["host_agent_last_seen_sec"] = int(age)
            return SystemStats(**payload)

    # ---- Tier 2: container fallback ----------------------------------------
    # CPU. interval=None returns the % since the last call. We primed at
    # import and every UI refresh updates the baseline for the next read.
    cpu_pct = psutil.cpu_percent(interval=None)
    try:
        load_avg = list(os.getloadavg())  # raises on Windows
    except (AttributeError, OSError):
        load_avg = None
    cpu = CpuStats(
        percent=cpu_pct,
        count_logical=psutil.cpu_count(logical=True) or 1,
        count_physical=psutil.cpu_count(logical=False),
        load_average=load_avg,
    )

    # Memory
    vm = psutil.virtual_memory()
    cached_mem = (getattr(vm, "cached", 0) or 0) + (getattr(vm, "buffers", 0) or 0)
    memory = MemoryStats(
        total_bytes=vm.total,
        used_bytes=vm.total - vm.available,
        available_bytes=vm.available,
        cached_bytes=int(cached_mem),
        percent=vm.percent,
    )

    # Disks: best-effort. Inside a container we typically only see "/" and
    # whatever bind-mounts are wired in. That's fine for a fallback.
    candidate_mounts = ["/", "/var/lib/postgresql/data", "/mnt/data", "/data"]
    seen_disks: set[str] = set()
    disks: list[DiskUsage] = []
    for mp in candidate_mounts:
        try:
            usage = psutil.disk_usage(mp)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        key = f"{mp}:{usage.total}"
        if key in seen_disks:
            continue
        seen_disks.add(key)
        disks.append(DiskUsage(
            mountpoint=mp,
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            percent=usage.percent,
        ))
    if not disks:
        try:
            usage = psutil.disk_usage(os.getcwd())
            disks.append(DiskUsage(
                mountpoint=os.getcwd(),
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
                percent=usage.percent,
            ))
        except OSError:
            pass

    # Top processes
    procs: list[ProcessInfo] = []
    self_pid = os.getpid()
    for p in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_info", "memory_percent",
         "num_threads", "create_time"]
    ):
        try:
            info = p.info
            mem = info["memory_info"]
            procs.append(ProcessInfo(
                pid=info["pid"],
                name=(info["name"] or "?")[:40],
                cpu_percent=float(info["cpu_percent"] or 0),
                memory_bytes=int(mem.rss) if mem else 0,
                memory_percent=float(info["memory_percent"] or 0),
                threads=int(info["num_threads"] or 0),
                started_at=(
                    datetime.fromtimestamp(info["create_time"], tz=timezone.utc)
                    if info["create_time"] else None
                ),
                is_self=(info["pid"] == self_pid),
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: (-x.cpu_percent, -x.memory_bytes))
    top_procs = procs[:10]
    if not any(p.is_self for p in top_procs):
        self_proc = next((p for p in procs if p.is_self), None)
        if self_proc:
            top_procs = top_procs[:9] + [self_proc]

    # Backend uptime
    try:
        uptime = int(_time.time() - _BACKEND_PROCESS.create_time())
    except Exception:
        uptime = 0

    # If the agent has reported BEFORE but is now stale, surface its age so
    # the UI can say "host agent last seen 47s ago" rather than just hiding it.
    if cached_at is not None:
        agent_age = int(_time.monotonic() - cached_at)
    else:
        agent_age = None

    return SystemStats(
        scope="container",
        hostname=None,
        platform=None,
        host_agent_last_seen_sec=agent_age,
        timestamp=datetime.now(timezone.utc),
        uptime_sec=uptime,
        cpu=cpu,
        memory=memory,
        disks=disks,
        gpus=[],
        top_processes=top_procs,
    )


# ---------------------------------------------------------------------------
# Host-stats push endpoint
# ---------------------------------------------------------------------------
#
# The host_agent/agent.py script POSTs here every ~5 seconds with real host
# metrics (Task Manager / top parity). We hold the most recent payload in
# memory; if the agent dies or is never started, the cache simply ages out
# and GET /system-stats falls back to container readings.
#
# This is fine to keep in process memory (not Postgres) because:
#   * one snapshot is ~10 KB
#   * losing it on backend restart is harmless — the agent will re-post in 5s
#   * we never need to query it historically; the trend module owns history

_HOST_STATS_CACHE: dict = {}            # {"payload": dict, "received_at_mono": float}
_HOST_STATS_MAX_AGE_SEC = 30            # treat older pushes as stale


@router.post("/host-stats", status_code=204)
def receive_host_stats(payload: SystemStats):
    """Accept a host-stats push from the host-side agent.

    Validation is automatic via the SystemStats schema. We don't authenticate
    this endpoint today — InduVista's backend isn't exposed to untrusted
    networks in its current deployment shape. If you put it behind a reverse
    proxy on the public internet, lock this path down (mTLS or a shared
    secret) before exposing /host-stats.
    """
    _HOST_STATS_CACHE["payload"] = payload.model_dump(mode="json")
    _HOST_STATS_CACHE["received_at_mono"] = _time.monotonic()


@router.get("/out-of-range-tags", response_model=list[OutOfRangeTag])
def list_out_of_range_tags(db: Annotated[Session, Depends(get_session)]):
    """Tags whose current value violates the operator-defined min/max limits.

    Two ways a tag lands here:
      1. Worker tagged it ST_RANGE_WARN (st=68) with reason RANGE_LOW/HIGH.
      2. The value is currently outside [min_value, max_value] regardless of
         what st says — catches the brief window between configuring a limit
         and the next poll cycle.

    Both definitions are unioned so the page shows everything currently
    out-of-bounds. Returned sorted by violation magnitude (worst first) so
    operators see the biggest deviations at the top.
    """
    rows = db.execute(text("""
        SELECT
            t.id              AS tag_id,
            t.name            AS tag_name,
            t.device_id,
            d.name            AS device_name,
            lv.value_double,
            t.engineering_unit,
            t.min_value,
            t.max_value,
            CASE
                WHEN t.min_value IS NOT NULL AND lv.value_double < t.min_value THEN 'LOW'
                WHEN t.max_value IS NOT NULL AND lv.value_double > t.max_value THEN 'HIGH'
                ELSE NULL
            END AS violation,
            lv.time AS last_seen,
            lv.st,
            lv.st_reason
        FROM tags t
        JOIN devices d ON d.id = t.device_id
        JOIN latest_tag_values lv ON lv.tag_id = t.id
        WHERE t.enabled = TRUE
          AND lv.value_double IS NOT NULL
          AND (
              (t.min_value IS NOT NULL AND lv.value_double < t.min_value)
              OR
              (t.max_value IS NOT NULL AND lv.value_double > t.max_value)
          )
        ORDER BY
            -- Worst deviation first: distance outside the band, normalized
            -- by band width so a 10% overshoot on a 0-100 tag ranks the same
            -- as a 10% overshoot on a 0-10 tag.
            GREATEST(
                CASE WHEN t.min_value IS NOT NULL
                     THEN (t.min_value - lv.value_double) /
                          NULLIF(ABS(COALESCE(t.max_value, t.min_value)
                                 - t.min_value) + 1, 0)
                     ELSE 0 END,
                CASE WHEN t.max_value IS NOT NULL
                     THEN (lv.value_double - t.max_value) /
                          NULLIF(ABS(t.max_value
                                 - COALESCE(t.min_value, t.max_value)) + 1, 0)
                     ELSE 0 END
            ) DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Phase 19 — Quality heatmap
#
# Time-binned quality per tag. Answers "is my data healthy across all tags
# over the recent window?" in one glance. Lives on the Diagnostics page.
#
# Y-axis: each tag (sorted by device, then name)
# X-axis: time bins (e.g., 96 × 15-minute bins for 24h)
# Color : quality class in that bin
#   0 = no data (no samples in bin) — gray
#   1 = invalid (worst sample ST < 64) — red
#   2 = uncertain (worst sample 64 ≤ ST < 128) — amber
#   3 = good (every sample ST ≥ 128) — green
#
# The "worst-quality-wins" aggregation matches operator intuition: if even
# one sample in a 15-minute window was BAD, the bin reads BAD. This makes
# transient issues visible rather than averaged-away.
#
# Payload size: ~300 tags × 96 bins × 1 int = ~28K. Cheap.
# ---------------------------------------------------------------------------

class HeatmapTag(BaseModel):
    tag_id: int
    tag_name: str
    device_id: int
    device_name: str


class HeatmapBin(BaseModel):
    start: datetime  # ISO timestamp marking the bucket's start


class QualityHeatmapResponse(BaseModel):
    window_hours: int
    bin_minutes: int
    tags: list[HeatmapTag]
    bins: list[HeatmapBin]
    # cells[i][j] = quality class for tags[i] in bins[j]; length: tags × bins
    cells: list[list[int]]


@router.get("/quality-heatmap", response_model=QualityHeatmapResponse)
def quality_heatmap(
    db: Annotated[Session, Depends(get_session)],
    window_hours: Annotated[int, Query(ge=1, le=168)] = 6,
    bin_minutes: Annotated[int, Query(ge=1, le=240)] = 15,
    device_id: Annotated[int | None, Query()] = None,
):
    """Aggregate tag-quality bytes into time bins for a heatmap view.

    Performance design (Phase 19.3):
      1. TimescaleDB continuous aggregate `tag_quality_5m_cagg` pre-bins
         min(st) per (tag, 5-min bucket) and auto-refreshes every minute.
         The heatmap query reads from the aggregate, so 6h/1d/3d/1w
         windows all complete in well under a second.
      2. The endpoint falls back to scanning raw tag_values if the cagg
         doesn't exist yet (migration not applied) or if bin_minutes < 5.
      3. In-process TTL cache (60s) absorbs back-to-back React Query
         refetches without hitting the DB at all.
      4. Default cell value GOOD (3); only bad/uncertain bins need overrides.
      5. latest_tag_values marks trailing bins as no-data for tags that
         stopped reporting.
    """
    cache_key = ("quality_heatmap", window_hours, bin_minutes, device_id)
    cached = _heatmap_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        result = _build_quality_heatmap(db, window_hours, bin_minutes, device_id)
    except Exception as exc:
        # Log the full traceback to docker logs so we can diagnose.
        import logging, traceback
        logging.getLogger(__name__).exception(
            "quality_heatmap failed: window=%dh bin=%dm device=%s",
            window_hours, bin_minutes, device_id,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Quality heatmap query failed: {type(exc).__name__}: {exc}",
        )

    _heatmap_cache_set(cache_key, result)
    return result


def _build_quality_heatmap(
    db: Session,
    window_hours: int,
    bin_minutes: int,
    device_id: int | None,
) -> "QualityHeatmapResponse":
    """The cache-miss branch of the endpoint. SQL-heavy body extracted.

    # Heatmap full fix: PDF-compliant no-data, soft-delete, bucket alignment
    # See fix_heatmap_full.py for the full rationale. Summary:
    #   - cells default to 0 (no-data), not 3 (green) - so empty bins
    #     render grey, not green. Aligns with PDF guidance: never hide
    #     missing data as good.
    #   - aggregation returns ALL samples (no st<128 filter); the cell
    #     value is classified per-row by worst_st: <64 INVALID, <128 SUSPECT,
    #     >=128 VALID. Matches InduVista status.py tier ranges exactly.
    #   - tag axis filters t.deleted_at IS NULL (Phase OPC-web.2.2.b)
    #   - bucket lookup falls back to floor-snap walk when SQL/Python
    #     timestamps disagree (timezone-bucketing edge case)
    """

    # -- Tags axis --------------------------------------------------------
    tag_sql = """
        SELECT t.id AS tag_id, t.name AS tag_name,
               t.device_id, d.name AS device_name
        FROM tags t
        JOIN devices d ON d.id = t.device_id
        WHERE t.enabled = true
          AND t.deleted_at IS NULL
    """
    tag_params: dict = {}
    if device_id is not None:
        tag_sql += " AND t.device_id = :device_id"
        tag_params["device_id"] = device_id
    tag_sql += " ORDER BY d.name, t.name"

    tag_rows = db.execute(text(tag_sql), tag_params).mappings().all()
    tags = [HeatmapTag(**r) for r in tag_rows]
    tag_index = {t.tag_id: i for i, t in enumerate(tags)}

    # -- Time-bin axis ----------------------------------------------------
    now = datetime.now(timezone.utc)
    local_tz = ZoneInfo(settings.app_timezone)
    offset_secs = int(now.astimezone(local_tz).utcoffset().total_seconds())

    bin_width = timedelta(minutes=bin_minutes)
    bucket_secs = bin_minutes * 60
    epoch_secs = int((now - timedelta(hours=window_hours)).timestamp())
    snap = epoch_secs - ((epoch_secs + offset_secs) % bucket_secs)
    aligned_start = datetime.fromtimestamp(snap, tz=timezone.utc)

    bins: list[HeatmapBin] = []
    cursor = aligned_start
    while cursor < now:
        bins.append(HeatmapBin(start=cursor))
        cursor += bin_width
    bin_index: dict[datetime, int] = {b.start: i for i, b in enumerate(bins)}

    # -- Per-tag last-seen ----------------------------------------------
    last_seen: dict[int, datetime | None] = {}
    for r in db.execute(text("SELECT tag_id, time FROM latest_tag_values")).mappings():
        last_seen[r["tag_id"]] = r["time"]

    # -- Initialize cells: ALL NO-DATA (0) by default --------------------
    # Per PDF section 8, bins without samples must render distinctly from
    # bins with good samples. Default 0 = grey; aggregation promotes to
    # 1/2/3 only when data exists.
    cells: list[list[int]] = [[0] * len(bins) for _ in tags]

    # -- Aggregate ALL samples (no quality filter) -----------------------
    # Stage 19 filtered `st < 128` to skip good samples for performance.
    # We return all rows now so we can explicitly mark "data present"
    # vs "no data" - the missing-data class would otherwise be hidden.
    cagg_exists = db.execute(text(
        "SELECT to_regclass('tag_quality_5m_cagg') IS NOT NULL AS exists"
    )).scalar()

    use_cagg = cagg_exists and bin_minutes >= 5 and bin_minutes % 5 == 0
    if use_cagg:
        agg_sql = """
            SELECT
                tag_id,
                time_bucket(make_interval(mins => :bin_minutes), bucket, :tz) AS bucket,
                min(min_st) AS worst_st
            FROM tag_quality_5m_cagg
            WHERE bucket >= :since
        """
        if device_id is not None:
            agg_sql += " AND tag_id IN (SELECT id FROM tags WHERE device_id = :device_id)"
        agg_sql += " GROUP BY tag_id, bucket"
    else:
        agg_sql = """
            SELECT
                tv.tag_id,
                time_bucket(make_interval(mins => :bin_minutes), tv.time, :tz) AS bucket,
                min(tv.st) AS worst_st
            FROM tag_values tv
            WHERE tv.time >= :since
        """
        if device_id is not None:
            agg_sql += " AND tv.device_id = :device_id"
        agg_sql += " GROUP BY tv.tag_id, bucket"

    agg_params: dict = {
        "bin_minutes": bin_minutes,
        "since": aligned_start,
        "tz": settings.app_timezone,
    }
    if device_id is not None:
        agg_params["device_id"] = device_id

    for row in db.execute(text(agg_sql), agg_params).mappings():
        ti = tag_index.get(row["tag_id"])
        if ti is None:
            continue
        bucket_ts = row["bucket"]
        if bucket_ts.tzinfo is None:
            bucket_ts = bucket_ts.replace(tzinfo=timezone.utc)
        bi = bin_index.get(bucket_ts)
        if bi is None:
            # SQL's time_bucket(...,:tz) produces UTC timestamps aligned to
            # local-tz bucket boundaries; Python's aligned_start computes
            # slightly different starts. Floor-snap by walking bins.
            for j, b in enumerate(bins):
                if b.start <= bucket_ts < b.start + bin_width:
                    bi = j
                    break
            if bi is None:
                continue
        worst = row["worst_st"]
        # InduVista status tiers from status.py:
        #   0-63   INVALID  -> 1 (red)
        #   64-127 SUSPECT  -> 2 (orange)
        #   128+   VALID    -> 3 (green)
        # NULL: treat as no-data (leave cell at 0 - shouldn't happen in
        # practice since aggregation only returns rows where samples exist)
        if worst is None:
            continue
        if worst < 64:
            cells[ti][bi] = 1
        elif worst < 128:
            cells[ti][bi] = 2
        else:
            cells[ti][bi] = 3

    # -- Mark trailing bins as no-data for stale tags --------------------
    # If a tag's last sample is before bin.start, bins after that point
    # are genuinely no-data regardless of what aggregation said. This also
    # handles tags that have never been seen (last_seen is None).
    for i, t in enumerate(tags):
        last = last_seen.get(t.tag_id)
        if last is None:
            cells[i] = [0] * len(bins)
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        for j, b in enumerate(bins):
            if last < b.start:
                cells[i][j] = 0

    return QualityHeatmapResponse(
        window_hours=window_hours,
        bin_minutes=bin_minutes,
        tags=tags,
        bins=bins,
        cells=cells,
    )


# ── In-process TTL cache for heatmaps ───────────────────────────────────
# A tiny dict + monotonic-clock TTL. The cache key includes window/bin/
# device so different requests get separate entries. 25s TTL is slightly
# shorter than React Query's 30s staleTime, so the client always sees a
# warm cache for back-to-back requests but we don't serve very stale data.

import time as _heatmap_time
_HEATMAP_CACHE: dict[tuple, tuple[float, Any]] = {}
_HEATMAP_CACHE_TTL = 60.0
_HEATMAP_CACHE_MAX = 64


def _heatmap_cache_get(key: tuple):
    entry = _HEATMAP_CACHE.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if _heatmap_time.monotonic() > expiry:
        _HEATMAP_CACHE.pop(key, None)
        return None
    return value


def _heatmap_cache_set(key: tuple, value: Any) -> None:
    _HEATMAP_CACHE[key] = (_heatmap_time.monotonic() + _HEATMAP_CACHE_TTL, value)
    if len(_HEATMAP_CACHE) > _HEATMAP_CACHE_MAX:
        oldest = min(_HEATMAP_CACHE.items(), key=lambda kv: kv[1][0])
        _HEATMAP_CACHE.pop(oldest[0], None)


# ===========================================================================
# Phase OPC-web.2.5 OPC diagnostics endpoint
#
# OPC UA sources are subscription-driven (push), not polled like Modbus, so
# they don't fit the worker_device_status / Workers-table model. This gives
# the Diagnostics page a dedicated OPC panel with OPC-native fields. Reads
# only small tables (opc_sources + latest_tag_values via opc_tag_mappings),
# no tag_values hypertable scan.
# ===========================================================================

class OpcSourceDiag(BaseModel):
    source_id: int
    name: str
    endpoint: str
    enabled: bool
    mapping_count: int
    publishing_interval_ms: int | None
    last_sample_at: datetime | None
    seconds_since_last_sample: float | None
    last_server_clock_drift_sec: float | None
    last_server_clock_check_at: datetime | None
    state: str  # disabled | live | idle | stale | lost


def _derive_opc_state(enabled: bool, age_sec: float | None) -> str:
    """Live/Idle/Stale/Lost derivation — mirrors the OPC Sources page."""
    if not enabled:
        return "disabled"
    if age_sec is None:
        return "lost"
    if age_sec <= 30:
        return "live"
    if age_sec <= 300:
        return "idle"
    if age_sec <= 3600:
        return "stale"
    return "lost"


@router.get("/opc-sources", response_model=list[OpcSourceDiag])
def list_opc_source_diagnostics(db: Annotated[Session, Depends(get_session)]):
    """Per-OPC-source runtime state for the Diagnostics OPC panel.

    last_sample_at reads from latest_tag_values (small table) joined via
    opc_tag_mappings — sub-millisecond, no hypertable scan (see the
    _load_source_response perf note in opc_sources.py).
    """
    rows = db.execute(text("""
        SELECT
            s.id              AS source_id,
            s.name            AS name,
            s.endpoint        AS endpoint,
            s.is_enabled      AS enabled,
            s.publishing_interval_ms        AS publishing_interval_ms,
            s.last_server_clock_drift_sec   AS last_server_clock_drift_sec,
            s.last_server_clock_check_at    AS last_server_clock_check_at,
            COALESCE((SELECT COUNT(*) FROM opc_tag_mappings m
                      WHERE m.opc_source_id = s.id), 0) AS mapping_count,
            (SELECT MAX(ltv.time)
               FROM latest_tag_values ltv
               JOIN opc_tag_mappings m ON ltv.tag_id = m.tag_id
              WHERE m.opc_source_id = s.id) AS last_sample_at
        FROM opc_sources s
        ORDER BY s.name
    """)).mappings().all()

    now = datetime.now(timezone.utc)
    out: list[OpcSourceDiag] = []
    for r in rows:
        last = r["last_sample_at"]
        age = (now - last).total_seconds() if last is not None else None
        out.append(OpcSourceDiag(
            source_id=r["source_id"],
            name=r["name"],
            endpoint=r["endpoint"],
            enabled=r["enabled"],
            mapping_count=r["mapping_count"],
            publishing_interval_ms=r["publishing_interval_ms"],
            last_sample_at=last,
            seconds_since_last_sample=age,
            last_server_clock_drift_sec=r["last_server_clock_drift_sec"],
            last_server_clock_check_at=r["last_server_clock_check_at"],
            state=_derive_opc_state(r["enabled"], age),
        ))
    return out
