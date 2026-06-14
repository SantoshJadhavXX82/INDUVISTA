"""Current-value lookup for the calc-blocks UI.

Returns the latest value/quality per tag from latest_tag_values (one
indexed row per tag, PK on tag_id) via the shared
latest_values_by_tag() service - never a DISTINCT ON scan of the
tag_values history hypertable (that anti-pattern made the evaluator
fall behind its cadence under write load). Both the acquisition
workers and the calc evaluator upsert into latest_tag_values, so it
holds current values for calc-output tags too.

Scoped to the tags the Calc tags page uses: each computed tag's
anchor id plus any external output target.
"""

from fastapi import APIRouter

from sqlalchemy import text
from app.db import SessionLocal
from app.services.latest_values import latest_values_by_tag


router = APIRouter(tags=["calc"])


@router.get("/api/calc/current-values")
def get_current_values():
    """Returns the latest value per tag from latest_tag_values.
    Response shape:
        {
          "values": {
            "<tag_id>": {
              "value": <num | null>,
              "value_text": <str | null>,
              "quality": <int | null>,    # the st byte (0..255)
              "ts": <iso8601 | null>,
              "source": <str | null>
            }
          },
          "_source": "latest_tag_values"
        }
    Values come from latest_tag_values (PK lookup per tag), scoped to
    the calc tags + external output targets this page renders.
    """
    with SessionLocal() as db:
        try:
            # Current values come from latest_tag_values (one indexed row per
            # tag) via the shared reader, never a DISTINCT ON scan of the
            # tag_values history hypertable.
            #
            # Scope to the tags this page uses: each computed tag's anchor
            # id, plus any external output target. Keeps the payload to
            # calc-relevant tags instead of every tag in the plant.
            id_rows = db.execute(text(
                "SELECT id AS tag_id FROM computed_tags "
                "UNION "
                "SELECT output_tag_id AS tag_id FROM computed_tags "
                "WHERE output_tag_id IS NOT NULL"
            )).mappings().all()
            calc_tag_ids = [r["tag_id"] for r in id_rows]
            rows = latest_values_by_tag(db, tag_ids=calc_tag_ids)
        except Exception as e:
            return {
                "values": {},
                "_error": f"{type(e).__name__}: {e}",
                "_note": (
                    "Query against latest_tag_values failed. If the column "
                    "names have drifted from (tag_id, value_double, "
                    "value_text, st, time, source), update "
                    "calc_current_values.py / latest_values.py."
                ),
            }

        values: dict[str, dict] = {}
        for tid, row in rows.items():
            vd = row["value_double"]
            ts = row["time"]
            values[str(tid)] = {
                "value": float(vd) if vd is not None else None,
                "value_text": row["value_text"],
                "quality": row["st"],
                "ts": ts.isoformat() if ts else None,
                "source": row["source"],
            }

        return {"values": values, "_source": "latest_tag_values"}
