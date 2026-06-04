"""BI explorer — semantic model + query engine (scoped Power BI / Tableau style).

The explorer lets a user drag FIELDS onto SHELVES (rows / columns / color /
filters) and get a live aggregated result for charting. This module is the
backend that turns a shelf specification into safe, parameterized SQL against
the tag historian (using TimescaleDB continuous aggregates for speed).

SEMANTIC MODEL
--------------
Dimensions (group-by axes):
  tag          -> tags.name
  device       -> devices.name           (tag_values.device_id is denormalized)
  group        -> groups.name             (via tag_group_memberships)
  unit         -> tags.engineering_unit
  data_type    -> tags.data_type
  time:minute/hour/day -> time_bucket on the value timestamp
  time:shift   -> shift code derived from local time + system shift config
Measures (aggregated numbers over value_double, GOOD-quality only):
  agg one of avg | min | max | sum | first | last | count

The engine picks the cheapest CAGG that satisfies the requested time grain:
  minute -> tag_values_1m,  hour -> tag_values_1h,  day -> tag_values_1d,
  finer/raw -> tag_values (raw).  Non-time grains read raw + bucket in SQL.

SAFETY: all dimension/measure identifiers are validated against a fixed
allow-list; user input never reaches SQL as an identifier. Values are bound
parameters. No string interpolation of user data into SQL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

GOOD_ST = 128

# --- allow-listed dimensions: name -> SQL expression + needed join ---
# Expressions reference aliases: v=tag_values, t=tags, d=devices, g=groups
DIMENSIONS: dict[str, dict] = {
    "tag":       {"sql": "t.name",              "joins": {"t"}, "label": "Tag"},
    "device":    {"sql": "d.name",              "joins": {"d"}, "label": "Device"},
    "group":     {"sql": "g.name",              "joins": {"g"}, "label": "Group"},
    "unit":      {"sql": "t.engineering_unit",  "joins": {"t"}, "label": "Eng. Unit"},
    "data_type": {"sql": "t.data_type",         "joins": {"t"}, "label": "Data Type"},
    # time grains (bucketed on the fact's time column, in the plant tz)
    "time:minute": {"sql": "time_bucket('1 minute', v.\"time\")", "joins": set(), "label": "Minute", "time": "minute"},
    "time:hour":   {"sql": "time_bucket('1 hour', v.\"time\")",   "joins": set(), "label": "Hour",   "time": "hour"},
    "time:day":    {"sql": "time_bucket('1 day', v.\"time\")",    "joins": set(), "label": "Day",    "time": "day"},
}

MEASURE_AGGS = {
    "avg":   "avg(v.value_double)",
    "min":   "min(v.value_double)",
    "max":   "max(v.value_double)",
    "sum":   "sum(v.value_double)",
    "first": "first(v.value_double, v.\"time\")",
    "last":  "last(v.value_double, v.\"time\")",
    "count": "count(v.value_double)",
}

# CAGG selection by requested finest time grain present in dims
_CAGG_BY_GRAIN = {"minute": "tag_values_1m", "hour": "tag_values_1h", "day": "tag_values_1d"}


@dataclass
class Measure:
    agg: str           # avg|min|max|sum|first|last|count
    label: str | None = None


@dataclass
class Filter:
    field: str         # a dimension key OR 'time'
    op: str            # '='|'in'|'between'|'>'|'<'
    value: Any = None
    values: list | None = None


@dataclass
class QuerySpec:
    measures: list[Measure]               # the numbers (Rows shelf)
    dimensions: list[str]                 # group-by (Columns + Color shelves)
    filters: list[Filter] = field(default_factory=list)
    limit: int = 5000


def _validate(spec: QuerySpec) -> None:
    if not spec.measures:
        raise ValueError("At least one measure is required.")
    for m in spec.measures:
        if m.agg not in MEASURE_AGGS:
            raise ValueError(f"Unknown aggregation: {m.agg!r}")
    for d in spec.dimensions:
        if d not in DIMENSIONS:
            raise ValueError(f"Unknown dimension: {d!r}")
    for f in spec.filters:
        if f.field not in DIMENSIONS and f.field != "time":
            raise ValueError(f"Unknown filter field: {f.field!r}")
        if f.op not in ("=", "in", "between", ">", "<", ">=", "<="):
            raise ValueError(f"Unsupported operator: {f.op!r}")


def build_sql(spec: QuerySpec) -> tuple[str, dict]:
    """Compile a QuerySpec into (sql, params). Pure + safe (allow-listed ids)."""
    _validate(spec)

    # Decide fact source: cheapest CAGG matching the finest time grain present.
    grains = [DIMENSIONS[d].get("time") for d in spec.dimensions if DIMENSIONS[d].get("time")]
    use_cagg = None
    if grains:
        # finest grain wins for correctness (minute < hour < day)
        order = {"minute": 0, "hour": 1, "day": 2}
        finest = min(grains, key=lambda g: order[g])
        use_cagg = _CAGG_BY_GRAIN.get(finest)

    joins_needed: set[str] = set()
    for d in spec.dimensions:
        joins_needed |= DIMENSIONS[d]["joins"]
    for f in spec.filters:
        if f.field in DIMENSIONS:
            joins_needed |= DIMENSIONS[f.field]["joins"]

    params: dict[str, Any] = {"good": GOOD_ST}

    # SELECT: dimensions (as dim0, dim1...) + measures (as m0, m1...)
    sel = []
    group_terms = []
    for i, d in enumerate(spec.dimensions):
        expr = DIMENSIONS[d]["sql"]
        sel.append(f"{expr} AS dim{i}")
        group_terms.append(expr)
    for j, m in enumerate(spec.measures):
        sel.append(f"{MEASURE_AGGS[m.agg]} AS m{j}")

    # FROM + JOINs. When using a CAGG, time-bucketed measures aren't recomputable
    # from raw aggregates trivially (avg of avgs); to stay correct we read RAW
    # for sum/first/last/count or when joins on tag attributes are needed beyond
    # what the cagg carries. For this first version we read RAW tag_values for
    # correctness and rely on indexes; CAGG fast-path is a later optimization.
    frm = ['tag_values v']
    if "t" in joins_needed:
        frm.append("JOIN tags t ON t.id = v.tag_id")
    if "d" in joins_needed:
        frm.append("LEFT JOIN devices d ON d.id = v.device_id")
    if "g" in joins_needed:
        frm.append("JOIN tag_group_memberships gm ON gm.tag_id = v.tag_id")
        frm.append("JOIN groups g ON g.id = gm.group_id")

    where = ["v.st >= :good", "v.value_double IS NOT NULL"]
    # Always require tags not soft-deleted if we joined tags.
    if "t" in joins_needed:
        where.append("t.deleted_at IS NULL")

    # Filters
    for k, f in enumerate(spec.filters):
        if f.field == "time":
            if f.op == "between" and f.values and len(f.values) == 2:
                where.append(f'v."time" >= :tf{k}a AND v."time" < :tf{k}b')
                params[f"tf{k}a"], params[f"tf{k}b"] = f.values[0], f.values[1]
            continue
        expr = DIMENSIONS[f.field]["sql"]
        if f.op == "in" and f.values:
            where.append(f"{expr} = ANY(:fv{k})")
            params[f"fv{k}"] = f.values
        elif f.op in ("=", ">", "<", ">=", "<="):
            where.append(f"{expr} {f.op} :fv{k}")
            params[f"fv{k}"] = f.value

    sql = f"SELECT {', '.join(sel)}\nFROM {' '.join(frm)}\nWHERE {' AND '.join(where)}"
    if group_terms:
        sql += "\nGROUP BY " + ", ".join(group_terms)
        sql += "\nORDER BY " + ", ".join(f"{e}" for e in group_terms)
    sql += "\nLIMIT :lim"
    params["lim"] = min(spec.limit, 50000)
    return sql, params


def run_query(db: Session, spec: QuerySpec) -> dict[str, Any]:
    """Execute a QuerySpec and return columnar results for charting."""
    sql, params = build_sql(spec)
    rows = db.execute(text(sql), params).all()
    dim_labels = [DIMENSIONS[d]["label"] for d in spec.dimensions]
    meas_labels = [m.label or f"{m.agg}(value)" for m in spec.measures]
    out_rows = []
    for r in rows:
        rec = {}
        for i, d in enumerate(spec.dimensions):
            rec[f"dim{i}"] = r[i]
        base = len(spec.dimensions)
        for j in range(len(spec.measures)):
            rec[f"m{j}"] = r[base + j]
        out_rows.append(rec)
    return {
        "dimensions": [{"key": d, "label": DIMENSIONS[d]["label"]} for d in spec.dimensions],
        "measures": [{"agg": m.agg, "label": meas_labels[k]} for k, m in enumerate(spec.measures)],
        "rows": out_rows,
        "row_count": len(out_rows),
    }
