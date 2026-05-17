"""Phase 15.1 - Calculation tag evaluator.

A long-running worker, mirrored on alarm_evaluator's loop shape:

  1. Reload calc definitions from DB every RELOAD_SEC (cache).
  2. Every TICK_SEC, topologically sort the dependency graph and
     evaluate every enabled calc tag in order.
  3. Write each block's output to tag_values with source='calc'.

The worker is stateless - every tick re-reads the latest input
values from tag_values. Stateful blocks (rolling buffers, edge
detection) compute their state from the recent tag_values history,
not from worker memory. This keeps restarts clean and the model
simple.

Cycle detection runs at save time (in the API), but as a defensive
second layer the worker's topological sort also detects cycles and
skips the offending block(s) with a logged error rather than
crashing.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal
from app.workers.calc_blocks import (
    BLOCK_REGISTRY, get_block, InputSample, BlockResult,
)


log = logging.getLogger("calc_evaluator")


TICK_SEC = float(os.getenv("CALC_TICK_SEC", "1.0"))
RELOAD_SEC = float(os.getenv("CALC_RELOAD_SEC", "30.0"))

# How recent does an input value need to be to count? Inputs older
# than this are treated as quality=BAD with value=None.
MAX_INPUT_AGE_SEC = float(os.getenv("CALC_MAX_INPUT_AGE_SEC", "300.0"))

GOOD_QUALITY = 128


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
    device_id: int   # needed for the tag_values insert


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def load_definitions(db) -> list[CalcDef]:
    """Return all enabled calc definitions, joined with tag metadata."""
    rows = db.execute(text("""
        SELECT cd.id, cd.tag_id, cd.block_type, cd.block_config, cd.enabled,
               t.device_id
        FROM calc_definitions cd
        JOIN tags t ON t.id = cd.tag_id
        WHERE cd.enabled = true
    """)).mappings().all()
    out: list[CalcDef] = []
    for r in rows:
        cfg = r["block_config"]
        # JSONB comes back as dict already via psycopg2's json type
        if isinstance(cfg, str):
            import json
            cfg = json.loads(cfg)
        out.append(CalcDef(
            id=r["id"], tag_id=r["tag_id"],
            block_type=r["block_type"], block_config=cfg or {},
            enabled=r["enabled"], device_id=r["device_id"],
        ))
    return out


def latest_inputs(db, tag_ids: list[int]) -> dict[int, InputSample]:
    """Fetch the most recent value (within MAX_INPUT_AGE_SEC) for
    each requested tag. Returns a dict keyed by tag_id. Missing
    tags get an entry with value=None, quality=0."""
    if not tag_ids:
        return {}
    rows = db.execute(text("""
        SELECT DISTINCT ON (tag_id)
               tag_id, value_double, st
        FROM tag_values
        WHERE tag_id = ANY(:ids)
          AND time >= NOW() - make_interval(secs => :max_age)
        ORDER BY tag_id, time DESC
    """), {
        "ids": tag_ids,
        "max_age": MAX_INPUT_AGE_SEC,
    }).mappings().all()

    out: dict[int, InputSample] = {}
    for r in rows:
        out[r["tag_id"]] = InputSample(
            tag_id=r["tag_id"],
            value=float(r["value_double"]) if r["value_double"] is not None else None,
            quality=int(r["st"]) if r["st"] is not None else 0,
        )
    # Fill in missing tags with bad-quality stub so the block sees
    # a complete inputs list and can compute worst-quality cleanly.
    for tid in tag_ids:
        if tid not in out:
            out[tid] = InputSample(tag_id=tid, value=None, quality=0)
    return out


def write_output(db, defn: CalcDef, result: BlockResult, when: datetime) -> None:
    """Persist a block result to tag_values.

    Source label: 'estimated'. Discovered during 15.1 bring-up that
    the pre-existing ck_tag_values_source CHECK constraint allows
    {modbus, csv, manual, estimated, store_forward, opc_ua, mqtt}
    but not 'calc'. The constraint propagates to all chunks via
    TimescaleDB and modifying it on a populated hypertable requires
    a chunk-aware migration we don't want to ship inside 15.1.
    'estimated' fits the semantics: a calc-derived value IS an
    estimate of process state derived from other measurements.
    Future migration may add 'calc' as a first-class source value.
    """
    db.execute(text("""
        INSERT INTO tag_values
            (time, tag_id, device_id, value_double, st, source)
        VALUES
            (:time, :tag_id, :device_id, :value, :st, 'estimated')
    """), {
        "time": when,
        "tag_id": defn.tag_id,
        "device_id": defn.device_id,
        "value": result.value,
        "st": result.quality,
    })


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

def topological_order(defs: list[CalcDef]) -> tuple[list[CalcDef], list[int]]:
    """Return (sorted_defs, cyclic_calc_ids).

    Edges: A -> B means "B reads from A's tag_id". B evaluates after A.
    Kahn's algorithm. Any def left over after the queue drains is
    part of a cycle and is reported back to the caller so the worker
    can skip it with a clear log message.
    """
    # tag_id -> CalcDef (the def that PRODUCES that tag)
    by_output_tag: dict[int, CalcDef] = {d.tag_id: d for d in defs}

    # Build edges from each def to defs that consume its output.
    # in_degree counts how many calc-produced inputs each def depends on.
    in_degree: dict[int, int] = {d.id: 0 for d in defs}
    edges: dict[int, list[int]] = defaultdict(list)  # calc_id -> [downstream calc_ids]

    for d in defs:
        block_cls = get_block(d.block_type)
        if block_cls is None:
            # Unknown block - leave in_degree at 0 so it sorts to the
            # front. Worker will log and skip during evaluate.
            continue
        try:
            inputs = block_cls.inputs(d.block_config)
        except Exception as e:
            log.warning("Block %s on calc_def id=%d returned bad inputs: %s",
                        d.block_type, d.id, e)
            continue
        for inp_tag in inputs:
            upstream = by_output_tag.get(inp_tag)
            if upstream is None:
                # Input is a modbus tag, not produced by another calc.
                # No edge in the calc graph.
                continue
            edges[upstream.id].append(d.id)
            in_degree[d.id] += 1

    # Kahn's
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
# Main loop
# ---------------------------------------------------------------------------

def evaluate_once(db) -> int:
    """One pass over all enabled calc definitions. Returns the number
    of successful evaluations."""
    defs = load_definitions(db)
    if not defs:
        return 0

    sorted_defs, cyclic_ids = topological_order(defs)
    if cyclic_ids:
        log.error("Calc cycle detected; skipping defs: %s", cyclic_ids)

    when = datetime.now(timezone.utc)
    success = 0

    for d in sorted_defs:
        block_cls = get_block(d.block_type)
        if block_cls is None:
            log.warning("Skipping calc_def id=%d: unknown block_type %s",
                        d.id, d.block_type)
            continue

        try:
            wanted = block_cls.inputs(d.block_config)
            samples_by_tag = latest_inputs(db, wanted)
            samples = [samples_by_tag[tid] for tid in wanted]
            result = block_cls.evaluate(d.block_config, samples)
            write_output(db, d, result, when)
            success += 1
        except Exception as e:
            log.exception("calc_def id=%d (%s) failed: %s",
                          d.id, d.block_type, e)

    db.commit()
    return success


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info(
        "calc_evaluator starting: tick=%.1fs reload=%.1fs max_age=%.1fs "
        "blocks_registered=%s",
        TICK_SEC, RELOAD_SEC, MAX_INPUT_AGE_SEC,
        sorted(BLOCK_REGISTRY.keys()),
    )

    while True:
        cycle_start = time.time()
        try:
            with SessionLocal() as db:
                n = evaluate_once(db)
                if n > 0:
                    log.debug("tick: %d evaluations", n)
        except Exception as e:
            log.exception("calc_evaluator tick failed: %s", e)

        elapsed = time.time() - cycle_start
        if elapsed < TICK_SEC:
            time.sleep(TICK_SEC - elapsed)
        elif elapsed > 2 * TICK_SEC:
            log.warning("calc tick took %.2fs (target %.2fs)",
                        elapsed, TICK_SEC)


if __name__ == "__main__":
    main()
