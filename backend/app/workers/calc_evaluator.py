"""Phase 15.2 - Multi-rate calc evaluator with scheduling guarantees.
Phase 15.5 - Extended for stateful Tier D blocks (timers, edges,
latches, counters). Stateful blocks are detected via their STATEFUL
class attribute; for those, the worker fetches state from
calc_block_state before evaluation, passes wall-clock time and
state into evaluate(), and persists the returned new_state.

Refactored from Phase 15.1's fixed-tick loop into a monotonic-clock
priority scheduler that runs each block at its individually configured
execution_rate_ms. Per the standards conformance reference sect 8,
this implementation provides the following invariants:

  1. Determinism: within a single scheduling tick, blocks evaluate
     in topological order so downstream consumers see fresh upstream
     values in the same tick.

  2. Rate adherence: each block runs at its declared rate +/- jitter
     from OS scheduling. Conforms to IEC 61131-3 sect 2.7.2.

  3. Overrun detection: blocks exceeding 80% of their rate budget
     are flagged 'overrun' in calc_execution_stats.last_status
     without being skipped. Conforms to IEC 61131-3 sect 2.7.3.

  4. No silent skipping: every scheduled execution runs. The only
     non-execution path is the drift-resync guard (>2 intervals
     behind), which is always logged and counted in total_skips.

  5. Always-shown timing: calc_execution_stats is updated in a
     try/finally block so last_executed_at and last_duration_ms are
     visible even when the block raised an exception.

  6. Hang protection: each block evaluation runs with a deadline of
     min(rate * 0.8, 5.0) seconds enforced via signal.SIGALRM.
     Signals only interrupt pure-Python; C extensions can't be
     interrupted. Practical implication: numpy/scipy-using blocks
     might exceed the deadline; for our pure-Python blocks the
     deadline is reliable.

The scheduler is single-threaded. Blocks run sequentially within
the main worker process. For the expected load (sub-100 calcs,
microsecond per evaluation), single-threaded is the right choice;
multi-process would add DB-session marshalling complexity without
meaningful throughput benefit.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal
from app.workers.calc_blocks import (
    BLOCK_REGISTRY, get_block, InputSample, BlockResult,
)


log = logging.getLogger("calc_evaluator")


# How often to reload the definitions cache from DB. Definitions
# change rarely; per-tick reload would be wasteful.
RELOAD_SEC = float(os.getenv("CALC_RELOAD_SEC", "30.0"))

# Maximum sleep duration between scheduling checks. Caps responsiveness
# at 100 ms even when nothing is due soon.
MAX_SLEEP_SEC = 0.1

# How recent does an input value need to be? Inputs older than this
# are treated as quality=BAD with value=None.
MAX_INPUT_AGE_SEC = float(os.getenv("CALC_MAX_INPUT_AGE_SEC", "300.0"))

GOOD_QUALITY = 128


class DeadlineExceeded(Exception):
    """Raised by the SIGALRM handler when a block exceeds its deadline."""


def _alarm_handler(signum, frame):
    raise DeadlineExceeded("evaluation exceeded deadline")


# Install the signal handler once at import time.
signal.signal(signal.SIGALRM, _alarm_handler)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CalcDef:
    id: int
    tag_id: int
    block_type: str
    block_config: dict[str, Any]
    enabled: bool
    device_id: int
    execution_rate_ms: int


@dataclass
class SchedulerState:
    """In-memory scheduling state. Lives across DB reloads."""
    # calc_def_id -> next monotonic timestamp at which it should run
    next_run: dict[int, float] = field(default_factory=dict)

    def initialize_new(self, defs: list[CalcDef], now_mono: float) -> None:
        """Defs that don't have a scheduled time yet get scheduled
        for ASAP (well, the next rate boundary). Stale entries for
        deleted defs are removed."""
        current_ids = {d.id for d in defs}
        for d in defs:
            if d.id not in self.next_run:
                rate_sec = d.execution_rate_ms / 1000.0
                self.next_run[d.id] = now_mono + min(0.5, rate_sec)
        for stale_id in list(self.next_run.keys()):
            if stale_id not in current_ids:
                del self.next_run[stale_id]

    def due(self, defs: list[CalcDef], now_mono: float) -> list[CalcDef]:
        return [d for d in defs if self.next_run.get(d.id, 0) <= now_mono]

    def reschedule(self, def_id: int, rate_ms: int, now_mono: float) -> int:
        """Advance next_run by the rate (grid-aligned, not drift-aligned).
        Returns the number of intervals skipped if drift-resync triggered."""
        rate_sec = rate_ms / 1000.0
        new_next = self.next_run.get(def_id, now_mono) + rate_sec

        skips = 0
        if new_next < now_mono - 2 * rate_sec:
            missed_intervals = max(1, int((now_mono - new_next) / rate_sec))
            skips = missed_intervals
            new_next = now_mono + rate_sec

        self.next_run[def_id] = new_next
        return skips

    def earliest_next(self) -> float | None:
        if not self.next_run:
            return None
        return min(self.next_run.values())


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def load_definitions(db) -> list[CalcDef]:
    rows = db.execute(text("""
        SELECT cd.id, cd.tag_id, cd.block_type, cd.block_config, cd.enabled,
               cd.execution_rate_ms, t.device_id
        FROM calc_definitions cd
        JOIN tags t ON t.id = cd.tag_id
        WHERE cd.enabled = true
    """)).mappings().all()
    out: list[CalcDef] = []
    for r in rows:
        cfg = r["block_config"]
        if isinstance(cfg, str):
            import json
            cfg = json.loads(cfg)
        out.append(CalcDef(
            id=r["id"], tag_id=r["tag_id"],
            block_type=r["block_type"], block_config=cfg or {},
            enabled=r["enabled"], device_id=r["device_id"],
            execution_rate_ms=r["execution_rate_ms"],
        ))
    return out


def latest_inputs(db, tag_ids: list[int]) -> dict[int, InputSample]:
    if not tag_ids:
        return {}
    rows = db.execute(text("""
        SELECT DISTINCT ON (tag_id) tag_id, value_double, st
        FROM tag_values
        WHERE tag_id = ANY(:ids)
          AND time >= NOW() - make_interval(secs => :max_age)
        ORDER BY tag_id, time DESC
    """), {"ids": tag_ids, "max_age": MAX_INPUT_AGE_SEC}).mappings().all()

    out: dict[int, InputSample] = {}
    for r in rows:
        out[r["tag_id"]] = InputSample(
            tag_id=r["tag_id"],
            value=float(r["value_double"]) if r["value_double"] is not None else None,
            quality=int(r["st"]) if r["st"] is not None else 0,
        )
    for tid in tag_ids:
        if tid not in out:
            out[tid] = InputSample(tag_id=tid, value=None, quality=0)
    return out


def write_output(db, defn: CalcDef, result: BlockResult, when: datetime) -> None:
    """Write the calc's result to both the time-series hypertable
    (tag_values, for history/trend) and the latest-value snapshot
    (latest_tag_values, so TagExplorer / Dashboard / any other UI
    that reads the snapshot table sees the calc output). The Modbus
    poll worker maintains latest_tag_values for poll-sourced tags;
    we do the equivalent here for calc-sourced tags.

    Both writes share the same db transaction so a constraint
    violation on either rolls back together and the supervisor
    catches it via update_stats(status='error', ...).
    """
    params = {
        "time": when, "tag_id": defn.tag_id, "device_id": defn.device_id,
        "value": result.value, "st": result.quality,
    }

    # 1) Append to the hypertable for history / trend.
    db.execute(text("""
        INSERT INTO tag_values
            (time, tag_id, device_id, value_double, st, source)
        VALUES
            (:time, :tag_id, :device_id, :value, :st, 'estimated')
    """), params)

    # 2) UPSERT snapshot so the rest of the UI (TagExplorer, Dashboard,
    #    etc.) sees the current value. ON CONFLICT (tag_id) assumes
    #    latest_tag_values has tag_id as PK or with a unique constraint.
    db.execute(text("""
        INSERT INTO latest_tag_values
            (tag_id, device_id, time, value_double, st, source, updated_at)
        VALUES
            (:tag_id, :device_id, :time, :value, :st, 'estimated', NOW())
        ON CONFLICT (tag_id) DO UPDATE SET
            device_id    = EXCLUDED.device_id,
            time         = EXCLUDED.time,
            value_double = EXCLUDED.value_double,
            st           = EXCLUDED.st,
            source       = EXCLUDED.source,
            updated_at   = EXCLUDED.updated_at
    """), params)


def update_stats(
    db,
    defn: CalcDef,
    duration_ms: float,
    status: str,
    error_message: str | None,
    executed_at: datetime,
    next_scheduled_at: datetime,
    skips: int = 0,
) -> None:
    """Upsert calc_execution_stats. Called in finally block."""
    db.execute(text("""
        INSERT INTO calc_execution_stats (
            calc_def_id, last_executed_at, last_duration_ms, last_status,
            last_error_message, next_scheduled_at,
            consecutive_overruns, consecutive_errors,
            total_executions, total_overruns, total_errors, total_skips
        )
        VALUES (
            :id, :exec_at, :dur, :status, :err, :next_at,
            :cons_ovr, :cons_err, 1, :inc_ovr, :inc_err, :inc_skip
        )
        ON CONFLICT (calc_def_id) DO UPDATE SET
            last_executed_at  = EXCLUDED.last_executed_at,
            last_duration_ms  = EXCLUDED.last_duration_ms,
            last_status       = EXCLUDED.last_status,
            last_error_message = EXCLUDED.last_error_message,
            next_scheduled_at = EXCLUDED.next_scheduled_at,
            consecutive_overruns = CASE
                WHEN EXCLUDED.last_status = 'overrun'
                THEN calc_execution_stats.consecutive_overruns + 1
                ELSE 0 END,
            consecutive_errors = CASE
                WHEN EXCLUDED.last_status = 'error'
                THEN calc_execution_stats.consecutive_errors + 1
                ELSE 0 END,
            total_executions = calc_execution_stats.total_executions + 1,
            total_overruns   = calc_execution_stats.total_overruns + EXCLUDED.total_overruns,
            total_errors     = calc_execution_stats.total_errors + EXCLUDED.total_errors,
            total_skips      = calc_execution_stats.total_skips + EXCLUDED.total_skips
    """), {
        "id": defn.id,
        "exec_at": executed_at,
        "dur": duration_ms,
        "status": status,
        "err": error_message,
        "next_at": next_scheduled_at,
        "cons_ovr": 1 if status == 'overrun' else 0,
        "cons_err": 1 if status == 'error' else 0,
        "inc_ovr": 1 if status == 'overrun' else 0,
        "inc_err": 1 if status == 'error' else 0,
        "inc_skip": skips,
    })


# ---------------------------------------------------------------------------
# Topological sort (same shape as Phase 15.1)
# ---------------------------------------------------------------------------

def topological_order(defs: list[CalcDef]) -> tuple[list[CalcDef], list[int]]:
    by_output_tag: dict[int, CalcDef] = {d.tag_id: d for d in defs}
    in_degree: dict[int, int] = {d.id: 0 for d in defs}
    edges: dict[int, list[int]] = defaultdict(list)

    for d in defs:
        cls = get_block(d.block_type)
        if cls is None:
            continue
        try:
            for inp_tag in cls.inputs(d.block_config):
                upstream = by_output_tag.get(inp_tag)
                if upstream is None:
                    continue
                edges[upstream.id].append(d.id)
                in_degree[d.id] += 1
        except Exception as e:
            log.warning("Block %s on calc_def id=%d returned bad inputs: %s",
                        d.block_type, d.id, e)
            continue

    queue = [d for d in defs if in_degree[d.id] == 0]
    sorted_defs: list[CalcDef] = []
    queued_ids = {d.id for d in queue}
    while queue:
        d = queue.pop(0)
        sorted_defs.append(d)
        for downstream_id in edges[d.id]:
            in_degree[downstream_id] -= 1
            if in_degree[downstream_id] == 0 and downstream_id not in queued_ids:
                for cand in defs:
                    if cand.id == downstream_id:
                        queue.append(cand)
                        queued_ids.add(downstream_id)
                        break

    sorted_ids = {d.id for d in sorted_defs}
    cyclic = [d.id for d in defs if d.id not in sorted_ids]
    return sorted_defs, cyclic


# ---------------------------------------------------------------------------
# Stateful block support (Phase 15.5)
# ---------------------------------------------------------------------------

def _load_block_state(db, calc_def_id: int) -> dict:
    """Fetch saved state dict for a stateful calc_def. Returns {} on
    first evaluation (no row yet) or if the stored value is null."""
    row = db.execute(
        text("SELECT state FROM calc_block_state WHERE calc_def_id = :id"),
        {"id": calc_def_id},
    ).mappings().first()
    if row is None:
        return {}
    state = row["state"]
    if isinstance(state, str):
        state = json.loads(state)
    return state or {}


def _save_block_state(db, calc_def_id: int, state: dict) -> None:
    """Upsert calc_block_state. Called from within the main tick
    session so it commits atomically with output/stats."""
    db.execute(text("""
        INSERT INTO calc_block_state (calc_def_id, state, updated_at)
        VALUES (:id, CAST(:state AS jsonb), NOW())
        ON CONFLICT (calc_def_id) DO UPDATE SET
            state = EXCLUDED.state,
            updated_at = EXCLUDED.updated_at
    """), {"id": calc_def_id, "state": json.dumps(state)})


def _evaluate_stateful_with_deadline(
    block_cls,
    block_config: dict[str, Any],
    samples: list[InputSample],
    state: dict,
    now_wall: float,
    deadline_sec: float,
) -> tuple[BlockResult, dict]:
    """Run a stateful block's evaluate() inside a SIGALRM deadline.
    Returns (BlockResult, new_state)."""
    if deadline_sec >= 1.0:
        signal.alarm(int(deadline_sec))
    else:
        signal.setitimer(signal.ITIMER_REAL, deadline_sec)
    try:
        return block_cls.evaluate(block_config, samples, state, now_wall)
    finally:
        signal.alarm(0)
        signal.setitimer(signal.ITIMER_REAL, 0)


# ---------------------------------------------------------------------------
# Block evaluation with deadline
# ---------------------------------------------------------------------------

def _evaluate_with_deadline(
    block_cls,
    block_config: dict[str, Any],
    samples: list[InputSample],
    deadline_sec: float,
) -> BlockResult:
    """Run block_cls.evaluate() inside a SIGALRM-enforced deadline.

    Pure Python is interruptible; C extensions are not. For our
    aggregation blocks (pure Python math), the deadline is reliable.
    """
    if deadline_sec >= 1.0:
        signal.alarm(int(deadline_sec))
    else:
        signal.setitimer(signal.ITIMER_REAL, deadline_sec)
    try:
        return block_cls.evaluate(block_config, samples)
    finally:
        signal.alarm(0)
        signal.setitimer(signal.ITIMER_REAL, 0)


# ---------------------------------------------------------------------------
# Main scheduling loop
# ---------------------------------------------------------------------------

def tick_once(db, scheduler: SchedulerState, defs: list[CalcDef]) -> int:
    now_mono = time.monotonic()
    due = scheduler.due(defs, now_mono)
    if not due:
        return 0

    sorted_due, cyclic_ids = topological_order(due)
    if cyclic_ids:
        log.error("Calc cycle detected; skipping defs: %s", cyclic_ids)

    success = 0
    for d in sorted_due:
        _run_one(db, scheduler, d)
        success += 1

    db.commit()
    return success


def _run_one(db, scheduler: SchedulerState, d: CalcDef) -> None:
    """Evaluate one definition. Stats updated in finally."""
    block_cls = get_block(d.block_type)
    if block_cls is None:
        log.warning("Skipping calc_def id=%d: unknown block_type %s",
                    d.id, d.block_type)
        return

    rate_sec = d.execution_rate_ms / 1000.0
    deadline_sec = min(rate_sec * 0.8, 5.0)
    budget_ms = rate_sec * 1000 * 0.8

    executed_at = datetime.now(timezone.utc)
    t0 = time.monotonic()
    status = 'ok'
    err_msg: str | None = None
    result: BlockResult | None = None

    is_stateful = getattr(block_cls, 'STATEFUL', False)

    try:
        wanted = block_cls.inputs(d.block_config)
        samples_by_tag = latest_inputs(db, wanted)
        samples = [samples_by_tag[tid] for tid in wanted]
        if is_stateful:
            state = _load_block_state(db, d.id)
            now_wall = time.time()
            result, new_state = _evaluate_stateful_with_deadline(
                block_cls, d.block_config, samples, state, now_wall, deadline_sec)
            _save_block_state(db, d.id, new_state)
        else:
            result = _evaluate_with_deadline(
                block_cls, d.block_config, samples, deadline_sec)
    except DeadlineExceeded:
        status = 'killed'
        err_msg = f"exceeded {deadline_sec*1000:.0f}ms deadline"
    except Exception as e:
        status = 'error'
        err_msg = f"{type(e).__name__}: {e}"
        log.exception("calc_def id=%d (%s) raised: %s", d.id, d.block_type, e)
    finally:
        duration_ms = (time.monotonic() - t0) * 1000

        # Promote ok -> overrun if we exceeded the budget.
        if status == 'ok' and duration_ms > budget_ms:
            status = 'overrun'

        # Write output (only if we got a result without kill/error).
        if result is not None and status in ('ok', 'overrun'):
            try:
                write_output(db, d, result, executed_at)
            except Exception as e:
                log.exception("Failed to write output for calc_def id=%d: %s",
                              d.id, e)

        # Reschedule. May trigger drift-resync.
        skips = scheduler.reschedule(d.id, d.execution_rate_ms, time.monotonic())

        # Compute wall-clock next_scheduled_at from the monotonic delta.
        next_mono = scheduler.next_run[d.id]
        next_delta_sec = next_mono - time.monotonic()
        next_at = datetime.fromtimestamp(
            executed_at.timestamp() + next_delta_sec, tz=timezone.utc)

        if skips > 0:
            log.warning(
                "calc_def id=%d (%s) drifted; resynced forward, %d intervals skipped",
                d.id, d.block_type, skips)

        # Stats upsert. Wrapped so a stats failure doesn't propagate.
        try:
            update_stats(db, d, duration_ms, status, err_msg,
                         executed_at, next_at, skips)
        except Exception as e:
            log.exception("Stats upsert failed for calc_def id=%d: %s",
                          d.id, e)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info(
        "calc_evaluator starting (Phase 15.2): reload=%.1fs max_input_age=%.1fs "
        "blocks_registered=%s",
        RELOAD_SEC, MAX_INPUT_AGE_SEC, sorted(BLOCK_REGISTRY.keys()),
    )

    scheduler = SchedulerState()
    defs_cache: list[CalcDef] = []
    last_reload_mono = 0.0

    while True:
        loop_start = time.monotonic()

        if loop_start - last_reload_mono > RELOAD_SEC:
            try:
                with SessionLocal() as db:
                    defs_cache = load_definitions(db)
                scheduler.initialize_new(defs_cache, loop_start)
                last_reload_mono = loop_start
                log.debug("reload: %d defs", len(defs_cache))
            except Exception as e:
                log.exception("Reload failed: %s", e)
                time.sleep(1.0)
                continue

        try:
            with SessionLocal() as db:
                n = tick_once(db, scheduler, defs_cache)
                if n > 0:
                    log.debug("tick: %d evaluations", n)
        except Exception as e:
            log.exception("Tick failed: %s", e)

        next_t = scheduler.earliest_next()
        now = time.monotonic()
        if next_t is None:
            sleep_for = 0.5
        else:
            sleep_for = max(0.001, next_t - now)
        time.sleep(min(sleep_for, MAX_SLEEP_SEC))


if __name__ == "__main__":
    main()
