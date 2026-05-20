"""Phase 17.0b - Multi-rate computed-tag evaluator with dual output mode.

Refactor of Phase 17.0a's evaluator. Adds external output routing:
each computed tag can now write its value to a different target tag
instead of its own anchor row.

Routing rules:
  - CalcDef.output_tag_id IS NULL (default, "internal mode"):
      write_output writes to defn.tag_id (== defn.id, Option C anchor)
      using defn.device_id as the device_id.
  - CalcDef.output_tag_id IS NOT NULL ("external mode"):
      write_output writes to defn.output_tag_id using defn.output_device_id.
      The internal anchor row receives no values; it exists for metadata.

Topological order uses "effective output tag" (output_tag_id if set,
else id), so downstream calcs that read from an external tag still
detect the dependency and run in the correct order within a tick.

Schema mapping (post-Migration 0043):
  computed_tags.output_tag_id  ->  optional FK to tags.id
  CalcDef.output_tag_id, output_device_id  ->  derived from LEFT JOIN

All other behavior is identical to Phase 17.0a.
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


RELOAD_SEC = float(os.getenv("CALC_RELOAD_SEC", "30.0"))
MAX_SLEEP_SEC = 0.1
MAX_INPUT_AGE_SEC = float(os.getenv("CALC_MAX_INPUT_AGE_SEC", "300.0"))
GOOD_QUALITY = 128


class DeadlineExceeded(Exception):
    """Raised by the SIGALRM handler when a block exceeds its deadline."""


def _alarm_handler(signum, frame):
    raise DeadlineExceeded("evaluation exceeded deadline")


signal.signal(signal.SIGALRM, _alarm_handler)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CalcDef:
    """In-memory representation of one computed_tags row joined with its
    parent tag (and optionally with the external output tag's device).

    Under Option C the calc id IS the tag id of the internal anchor;
    tag_id = id.

    Phase 17.0b: output_tag_id is the externally-targeted tag (None for
    internal mode). output_device_id is its device, resolved via a
    LEFT JOIN at load time.
    """
    id: int                              # = computed_tags.id
    tag_id: int                          # = computed_tags.id (= tags.id) — internal anchor
    block_type: str
    block_config: dict[str, Any]
    enabled: bool
    device_id: int                       # the COMPUTED device hosting the calc
    execution_rate_ms: int
    # Phase 17.0b
    output_tag_id: int | None = None     # external output target, or None
    output_device_id: int | None = None  # external tag's device_id, or None

    def effective_output_tag(self) -> int:
        """The tag id this calc actually writes to. External if set, else internal."""
        return self.output_tag_id if self.output_tag_id is not None else self.tag_id

    def effective_output_device(self) -> int:
        """The device_id matching effective_output_tag()."""
        return (
            self.output_device_id
            if self.output_tag_id is not None and self.output_device_id is not None
            else self.device_id
        )


@dataclass
class SchedulerState:
    """In-memory scheduling state. Lives across DB reloads."""
    next_run: dict[int, float] = field(default_factory=dict)

    def initialize_new(self, defs: list[CalcDef], now_mono: float) -> None:
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
    """Load active computed tags joined to their parent tag rows AND
    (optionally) to the external output tag's device for routing."""
    rows = db.execute(text("""
        SELECT ct.id,
               ct.block_type, ct.block_config, ct.enabled,
               ct.execution_rate_ms,
               t.device_id,
               ct.output_tag_id,
               ot.device_id AS output_device_id
        FROM computed_tags ct
        JOIN tags t ON t.id = ct.id
        LEFT JOIN tags ot ON ot.id = ct.output_tag_id
        WHERE ct.enabled = true
    """)).mappings().all()

    out: list[CalcDef] = []
    n_external = 0
    for r in rows:
        cfg = r["block_config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        if r["output_tag_id"] is not None:
            n_external += 1
        out.append(CalcDef(
            id=r["id"],
            tag_id=r["id"],
            block_type=r["block_type"],
            block_config=cfg or {},
            enabled=r["enabled"],
            device_id=r["device_id"],
            execution_rate_ms=r["execution_rate_ms"],
            output_tag_id=r["output_tag_id"],
            output_device_id=r["output_device_id"],
        ))

    if n_external > 0:
        log.debug("loaded %d defs (%d external-output)", len(out), n_external)
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
    """Write the calc's result to both tag_values and latest_tag_values,
    routing to either the internal anchor (default) or the external tag
    (Phase 17.0b).

    The destination tables already FK to tags(id); both internal and
    external tag ids are valid tag rows so no schema change was needed
    in tag_values / latest_tag_values for this feature.
    """
    target_tag_id = defn.effective_output_tag()
    target_device_id = defn.effective_output_device()

    params = {
        "time": when,
        "tag_id": target_tag_id,
        "device_id": target_device_id,
        "value": result.value,
        "st": result.quality,
    }

    db.execute(text("""
        INSERT INTO tag_values
            (time, tag_id, device_id, value_double, st, source)
        VALUES
            (:time, :tag_id, :device_id, :value, :st, 'estimated')
    """), params)

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
    """Upsert computed_tag_execution_stats. Keyed on defn.id (the calc's
    internal anchor id), regardless of whether output routing is internal
    or external. Stats are about the CALC, not its destination."""
    db.execute(text("""
        INSERT INTO computed_tag_execution_stats (
            id, last_executed_at, last_duration_ms, last_status,
            last_error_message, next_scheduled_at,
            consecutive_overruns, consecutive_errors,
            total_executions, total_overruns, total_errors, total_skips
        )
        VALUES (
            :id, :exec_at, :dur, :status, :err, :next_at,
            :cons_ovr, :cons_err, 1, :inc_ovr, :inc_err, :inc_skip
        )
        ON CONFLICT (id) DO UPDATE SET
            last_executed_at  = EXCLUDED.last_executed_at,
            last_duration_ms  = EXCLUDED.last_duration_ms,
            last_status       = EXCLUDED.last_status,
            last_error_message = EXCLUDED.last_error_message,
            next_scheduled_at = EXCLUDED.next_scheduled_at,
            consecutive_overruns = CASE
                WHEN EXCLUDED.last_status = 'overrun'
                THEN computed_tag_execution_stats.consecutive_overruns + 1
                ELSE 0 END,
            consecutive_errors = CASE
                WHEN EXCLUDED.last_status = 'error'
                THEN computed_tag_execution_stats.consecutive_errors + 1
                ELSE 0 END,
            total_executions = computed_tag_execution_stats.total_executions + 1,
            total_overruns   = computed_tag_execution_stats.total_overruns + EXCLUDED.total_overruns,
            total_errors     = computed_tag_execution_stats.total_errors + EXCLUDED.total_errors,
            total_skips      = computed_tag_execution_stats.total_skips + EXCLUDED.total_skips
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
# Topological sort
# ---------------------------------------------------------------------------

def topological_order(defs: list[CalcDef]) -> tuple[list[CalcDef], list[int]]:
    """Sort defs so that any calc reading from another calc's effective
    output tag runs after that producer. Uses effective_output_tag()
    so external-output calcs are correctly visible as producers."""
    by_output_tag: dict[int, CalcDef] = {d.effective_output_tag(): d for d in defs}
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
            log.warning("Block %s on computed_tag id=%d returned bad inputs: %s",
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
# Stateful block support
# ---------------------------------------------------------------------------

def _load_block_state(db, calc_def_id: int) -> dict:
    row = db.execute(
        text("SELECT state FROM computed_tag_state WHERE id = :id"),
        {"id": calc_def_id},
    ).mappings().first()
    if row is None:
        return {}
    state = row["state"]
    if isinstance(state, str):
        state = json.loads(state)
    return state or {}


def _save_block_state(db, calc_def_id: int, state: dict) -> None:
    db.execute(text("""
        INSERT INTO computed_tag_state (id, state, updated_at)
        VALUES (:id, CAST(:state AS jsonb), NOW())
        ON CONFLICT (id) DO UPDATE SET
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
    if deadline_sec >= 1.0:
        signal.alarm(int(deadline_sec))
    else:
        signal.setitimer(signal.ITIMER_REAL, deadline_sec)
    try:
        return block_cls.evaluate(block_config, samples, state, now_wall)
    finally:
        signal.alarm(0)
        signal.setitimer(signal.ITIMER_REAL, 0)


def _evaluate_with_deadline(
    block_cls,
    block_config: dict[str, Any],
    samples: list[InputSample],
    deadline_sec: float,
) -> BlockResult:
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
        log.error("Computed-tag cycle detected; skipping ids: %s", cyclic_ids)

    success = 0
    for d in sorted_due:
        _run_one(db, scheduler, d)
        success += 1

    db.commit()
    return success


def _run_one(db, scheduler: SchedulerState, d: CalcDef) -> None:
    block_cls = get_block(d.block_type)
    if block_cls is None:
        log.warning("Skipping computed_tag id=%d: unknown block_type %s",
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
        log.exception("computed_tag id=%d (%s) raised: %s", d.id, d.block_type, e)
    finally:
        duration_ms = (time.monotonic() - t0) * 1000

        if status == 'ok' and duration_ms > budget_ms:
            status = 'overrun'

        if result is not None and status in ('ok', 'overrun'):
            try:
                write_output(db, d, result, executed_at)
            except Exception as e:
                log.exception("Failed to write output for computed_tag id=%d (target_tag=%d): %s",
                              d.id, d.effective_output_tag(), e)

        skips = scheduler.reschedule(d.id, d.execution_rate_ms, time.monotonic())

        next_mono = scheduler.next_run[d.id]
        next_delta_sec = next_mono - time.monotonic()
        next_at = datetime.fromtimestamp(
            executed_at.timestamp() + next_delta_sec, tz=timezone.utc)

        if skips > 0:
            log.warning(
                "computed_tag id=%d (%s) drifted; resynced forward, %d intervals skipped",
                d.id, d.block_type, skips)

        try:
            update_stats(db, d, duration_ms, status, err_msg,
                         executed_at, next_at, skips)
        except Exception as e:
            log.exception("Stats upsert failed for computed_tag id=%d: %s",
                          d.id, e)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info(
        "calc_evaluator starting (Phase 17.0b / Migration 0043): "
        "reload=%.1fs max_input_age=%.1fs blocks_registered=%s",
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
                n_ext = sum(1 for d in defs_cache if d.output_tag_id is not None)
                log.debug("reload: %d defs (%d external-output)", len(defs_cache), n_ext)
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
