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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

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
        SELECT COUNT(*) FROM tags t1
        JOIN tags t2
          ON t1.device_id = t2.device_id
         AND t1.function_code = t2.function_code
         AND t1.id < t2.id
        WHERE t1.address < t2.address + t2.register_count
          AND t2.address < t1.address + t1.register_count
    """)).scalar()

    fit_count = db.execute(text("""
        SELECT COUNT(*) FROM tags t
        JOIN register_blocks b ON b.id = t.register_block_id
        WHERE t.function_code <> b.function_code
           OR t.address < b.start_address
           OR t.address + t.register_count > b.start_address + b.count
    """)).scalar()

    stale_count = db.execute(text("""
        SELECT COUNT(*) FROM latest_tag_values lv
        JOIN tags t ON t.id = lv.tag_id
        JOIN devices d ON d.id = t.device_id
        WHERE lv.st < 128
           OR EXTRACT(EPOCH FROM (NOW() - lv.time)) > d.stale_after_sec
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
    """Find every pair of tags whose address ranges intersect on the same FC."""
    rows = db.execute(text("""
        SELECT
          t1.device_id, d.name as device_name, t1.function_code,
          t1.id as tag1_id, t1.name as tag1_name,
          t1.address as tag1_address, t1.register_count as tag1_register_count,
          t2.id as tag2_id, t2.name as tag2_name,
          t2.address as tag2_address, t2.register_count as tag2_register_count
        FROM tags t1
        JOIN tags t2
          ON t1.device_id = t2.device_id
         AND t1.function_code = t2.function_code
         AND t1.id < t2.id
        JOIN devices d ON d.id = t1.device_id
        WHERE t1.address < t2.address + t2.register_count
          AND t2.address < t1.address + t1.register_count
        ORDER BY t1.device_id, t1.function_code, t1.address
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/tag-block-fit", response_model=list[TagBlockFitIssue])
def list_tag_block_fit_issues(db: Annotated[Session, Depends(get_session)]):
    """Tags whose declared register_block can't contain them.

    Three possible problems, surfaced in a combined `issue` string:
      - function_code mismatch between tag and block
      - tag's start address is below the block's start_address
      - tag's range extends past the block's end
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
           OR t.address + t.register_count > b.start_address + b.count
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
        WHERE lv.st < 128
           OR EXTRACT(EPOCH FROM (NOW() - lv.time)) > d.stale_after_sec
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
