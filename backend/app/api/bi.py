"""BI explorer API — semantic model discovery + query execution.

  GET  /api/bi/model                 -> available dimensions + measures + fields
  POST /api/bi/query                 -> run a shelf spec, return columnar data

The frontend explorer renders the field list from /model, lets the user drag
fields onto shelves, and POSTs the resulting spec to /query for live charting.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.bi_query import (
    DIMENSIONS, MEASURE_AGGS, QuerySpec, Measure, Filter, run_query,
)

router = APIRouter(prefix="/api/bi", tags=["bi"])


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------
@router.get("/model")
def get_model(db: Annotated[Session, Depends(get_session)]):
    """Return the semantic model: dimensions, measure aggregations, and the
    concrete fields (tags/devices/groups) the user can filter on."""
    dims = [{"key": k, "label": v["label"],
             "kind": "time" if v.get("time") else "dimension"}
            for k, v in DIMENSIONS.items()]
    aggs = list(MEASURE_AGGS.keys())

    # Concrete pickable values for common filters (kept modest).
    devices = [r[0] for r in db.execute(text(
        "SELECT name FROM devices ORDER BY name")).all()]
    groups = [r[0] for r in db.execute(text(
        "SELECT name FROM groups ORDER BY name")).all()]
    units = [r[0] for r in db.execute(text(
        "SELECT DISTINCT engineering_unit FROM tags "
        "WHERE engineering_unit IS NOT NULL AND deleted_at IS NULL ORDER BY 1")).all()]

    return {
        "dimensions": dims,
        "measures": [{"agg": a} for a in aggs],
        # The "measure field" is value_double; aggregation makes it a measure.
        "value_field": {"key": "value", "label": "Tag Value"},
        "filter_values": {"device": devices, "group": groups, "unit": units},
    }


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------
class MeasureIn(BaseModel):
    agg: str = Field(..., description="avg|min|max|sum|first|last|count")
    label: str | None = None


class FilterIn(BaseModel):
    field: str
    op: str = "="
    value: Any = None
    values: list[Any] | None = None


class QueryIn(BaseModel):
    measures: list[MeasureIn]
    dimensions: list[str] = []
    filters: list[FilterIn] = []
    limit: int = 5000


@router.post("/query")
def run_bi_query(body: QueryIn, db: Annotated[Session, Depends(get_session)]):
    if not body.measures:
        raise HTTPException(400, "At least one measure is required.")
    try:
        spec = QuerySpec(
            measures=[Measure(agg=m.agg, label=m.label) for m in body.measures],
            dimensions=list(body.dimensions),
            filters=[Filter(field=f.field, op=f.op, value=f.value, values=f.values)
                     for f in body.filters],
            limit=body.limit,
        )
        return run_query(db, spec)
    except ValueError as e:
        raise HTTPException(400, str(e))
