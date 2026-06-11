"""Input-status lookup for the calc-blocks UI (suggestion #3).

For one computed tag, returns each INPUT tag the block reads, with its
latest value, quality (the `st` byte), source device, and timestamp -
plus the worst (rolled-up) input quality. This lets the UI trace a
BAD-quality output to the offending input.

It reuses BLOCK_REGISTRY[block_type].inputs(block_config) - the SAME
extractor calc_evaluator.py calls before latest_inputs() - so the inputs
listed here are exactly the ones the calc actually reads. Constant
operands are not tags and are intentionally omitted (they're always GOOD).

Quality scale (matches modbus/opc `st` and calc base.py):
    >= 128 GOOD,  64..127 UNCERTAIN,  < 64 BAD,  null = no data.

Mounted in main.py:  app.include_router(calc_input_status.router)
"""

import json

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db import SessionLocal
from app.workers.calc_blocks import BLOCK_REGISTRY


router = APIRouter(tags=["calc"])


@router.get("/api/calc/definitions/{def_id}/inputs")
def get_input_status(def_id: int) -> dict:
    """Latest value/quality/age for each input tag of one computed tag.

    Response shape:
        {
          "definition_id": 123,
          "block_type": "DIV",
          "worst_quality": 0,
          "inputs": [
            {
              "order": 0, "tag_id": 45, "tag_name": "FT101.flow",
              "device_name": "Plant-A", "data_type": "float",
              "resolved": true, "value": 12.3, "quality": 192,
              "ts": "2026-06-11T12:00:00+05:30"
            },
            ...
          ]
        }

    `resolved` is false when the input tag id no longer exists (deleted),
    which is a common silent cause of a BAD output. `quality`/`value`/`ts`
    are null when the tag has never been written.
    """
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT id, block_type, block_config "
                 "FROM computed_tags WHERE id = :id"),
            {"id": def_id},
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404,
                                detail=f"Computed tag {def_id} not found")

        block_type = row["block_type"]
        cfg = row["block_config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        cfg = cfg or {}

        cls = BLOCK_REGISTRY.get(block_type)
        if cls is None:
            return {"definition_id": def_id, "block_type": block_type,
                    "inputs": [], "worst_quality": None,
                    "_note": f"Unknown block type {block_type!r}."}

        # Reuse the evaluator's own input extractor.
        try:
            raw_ids = list(cls.inputs(cfg))
        except Exception as e:  # malformed config shouldn't 500 the UI
            return {"definition_id": def_id, "block_type": block_type,
                    "inputs": [], "worst_quality": None,
                    "_error": f"{type(e).__name__}: {e}",
                    "_note": "Could not extract inputs from block_config."}

        # De-duplicate while preserving declared order.
        ordered: list[int] = []
        seen: set[int] = set()
        for t in raw_ids:
            if isinstance(t, int) and t not in seen:
                seen.add(t)
                ordered.append(t)

        if not ordered:
            return {"definition_id": def_id, "block_type": block_type,
                    "inputs": [], "worst_quality": None}

        meta = {
            r["id"]: r
            for r in db.execute(
                text("SELECT t.id, t.name, t.data_type, d.name AS device_name "
                     "FROM tags t LEFT JOIN devices d ON d.id = t.device_id "
                     "WHERE t.id = ANY(:ids)"),
                {"ids": ordered},
            ).mappings().all()
        }

        latest = {
            r["tag_id"]: r
            for r in db.execute(
                text("SELECT DISTINCT ON (tag_id) tag_id, value_double, st, time "
                     "FROM tag_values WHERE tag_id = ANY(:ids) "
                     "ORDER BY tag_id, time DESC"),
                {"ids": ordered},
            ).mappings().all()
        }

        inputs = []
        worst: int | None = None
        for idx, tid in enumerate(ordered):
            m = meta.get(tid)
            lv = latest.get(tid)
            q = int(lv["st"]) if (lv and lv["st"] is not None) else None
            # Roll up worst quality; a missing/never-written input counts
            # as BAD (0) since the calc would treat it that way.
            eff_q = q if q is not None else 0
            worst = eff_q if worst is None else min(worst, eff_q)
            ts = lv["time"] if lv else None
            inputs.append({
                "order": idx,
                "tag_id": tid,
                "tag_name": m["name"] if m else None,
                "device_name": m["device_name"] if m else None,
                "data_type": m["data_type"] if m else None,
                "resolved": m is not None,
                "value": (float(lv["value_double"])
                          if (lv and lv["value_double"] is not None) else None),
                "quality": q,
                "ts": ts.isoformat() if ts else None,
            })

        return {
            "definition_id": def_id,
            "block_type": block_type,
            "worst_quality": worst,
            "inputs": inputs,
        }
