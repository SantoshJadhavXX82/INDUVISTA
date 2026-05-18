"""Phase 16.0c - Current-value lookup for the calc-blocks UI.

Returns the latest written value per tag. Reads from tag_values (the
TimescaleDB hypertable) using DISTINCT ON, since that's where the
calc evaluator writes its outputs (line 207 of calc_evaluator.py).

We don't read latest_tag_values - that table is populated by the
Modbus polling path and stays empty for calc-output tags, so it
can't tell us calc results.

Query shape matches the user's tag_values schema:
    columns: time, tag_id, device_id, value_double, value_text, st, source

For numeric calc outputs, value_double has the result and value_text
is NULL. For boolean/text outputs the worker still writes to
value_double (0.0/1.0 for booleans).
"""

from fastapi import APIRouter
from sqlalchemy import text

from app.db import SessionLocal


router = APIRouter(tags=["calc"])


@router.get("/api/calc/current-values")
def get_current_values():
    """Returns the latest value per tag from tag_values. Response shape:

        {
          "values": {
            "<tag_id>": {
              "value": <num | null>,
              "value_text": <str | null>,
              "quality": <int | null>,
              "ts": <iso8601 | null>,
              "source": <str | null>
            }
          },
          "_source": "tag_values.time"
        }

    The DISTINCT ON tag_id with ORDER BY time DESC uses the natural
    hypertable index on (tag_id, time DESC) so this is fast even on a
    large history.
    """
    with SessionLocal() as db:
        try:
            rows = db.execute(text("""
                SELECT DISTINCT ON (tag_id)
                    tag_id, value_double, value_text, st, time, source
                FROM tag_values
                ORDER BY tag_id, time DESC
            """)).mappings().all()
        except Exception as e:
            return {
                "values": {},
                "_error": f"{type(e).__name__}: {e}",
                "_note": (
                    "Query against tag_values failed. If the column names "
                    "have drifted from (tag_id, value_double, value_text, "
                    "st, time, source), update calc_current_values.py."
                ),
            }

        values: dict[str, dict] = {}
        for row in rows:
            vd = row["value_double"]
            ts = row["time"]
            values[str(row["tag_id"])] = {
                "value": float(vd) if vd is not None else None,
                "value_text": row["value_text"],
                "quality": row["st"],
                "ts": ts.isoformat() if ts else None,
                "source": row["source"],
            }

        return {"values": values, "_source": "tag_values.time"}
