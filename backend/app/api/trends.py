"""Trend module API — Phase 13.1.

Three endpoints power the Trend page:

  GET /api/trends/tags     — searchable tag list with metadata + latest CV
  GET /api/trends/history  — historical points, auto-aggregated by span
  GET /api/trends/summary  — availability + quality breakdown per tag

Real-time mode reuses /tags + /history with a narrow window and client-side
polling — no separate live endpoint needed today. (We'll add WebSocket
streaming in a later phase if the polling load justifies it.)

Spec references throughout: §5.1 (tag browser fields), §9.2 (quality bands),
§10.3 (availability summary), §16 (aggregation), §27 (API shape).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.config import settings


router = APIRouter(prefix="/api/trends", tags=["trends"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum points returned per series, regardless of aggregation. Above this
# the response would be wasteful (chart can't show 10k pixels of detail
# anyway) and would hammer the API. Configurable via query string up to
# this hard cap.
HARD_MAX_POINTS = 5000

# Quality band thresholds (spec §9.2). Names match the spec language —
# we deliberately avoid control-system jargon.
QUALITY_BANDS = {
    "good":      (128, 256),   # 128-255 — both 'Good' tiers from spec
    "uncertain": (64,  128),
    "bad":       (0,   64),
}


# ---------------------------------------------------------------------------
# Aggregation selection
# ---------------------------------------------------------------------------

def _pick_aggregation(start: datetime, end: datetime, requested: str) -> str:
    """Return the table/view name to query and the bucket label.

    'auto' = pick the smallest grain that keeps row count manageable for
    typical multi-tag queries. Operators usually don't think about
    aggregation; we should pick well by default.
    """
    if requested == "raw":
        return "raw"
    if requested in ("1m", "1h", "1d"):
        return requested
    if requested != "auto":
        raise HTTPException(400, f"Unknown aggregation '{requested}'")

    span = end - start
    if span <= timedelta(minutes=30):
        return "raw"           # raw values; max ~1800 pts at 1s polling
    if span <= timedelta(hours=4):
        return "1m"            # 240 buckets/tag
    if span <= timedelta(days=5):
        return "1h"            # ≤120 buckets/tag
    return "1d"                # ≥120 buckets per 5-day window if 1h; 1d keeps it ≤30/month


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TrendTag(BaseModel):
    """Single row in the tag browser. Maps to spec §5.1."""
    id: int
    name: str
    description: str | None
    device_id: int
    device_name: str
    channel_id: int
    channel_name: str               # spec calls this 'group'
    protocol: str
    register_block_id: int | None
    register_block_name: str | None
    address: int | None
    data_type: str
    engineering_unit: str | None
    logging_enabled: bool
    min_value: float | None
    max_value: float | None
    # Latest sample (may be null if the tag has never polled)
    current_value_double: float | None = None
    current_value_text: str | None = None
    current_st: int | None = None
    current_quality: str | None = None     # 'good' | 'uncertain' | 'bad' (derived from current_st)
    last_update_utc: datetime | None = None


class TrendPoint(BaseModel):
    """Short field names keep the JSON payload small — a 1000-point series
    is ~80 KB this way instead of ~200 KB with verbose names."""
    t: datetime                       # timestamp (UTC, ISO-8601)
    v: float | None = None            # value_double
    vt: str | None = None             # value_text (for non-numeric tags)
    # For aggregated rows, also include the envelope so the chart can draw
    # min/max bands without re-fetching:
    mn: float | None = None           # min in bucket
    mx: float | None = None           # max in bucket
    st: int | None = None             # quality status (raw rows only)
    src: str | None = None            # source ('modbus' etc., raw rows only)
    # Aggregated rows carry good/bad counts for quality coloring per bucket:
    g: int | None = None              # good_count
    b: int | None = None              # bad_count


class TrendSeries(BaseModel):
    """One tag's worth of data within the queried window."""
    tag_id: int
    tag_name: str
    engineering_unit: str | None
    data_type: str
    min_value: float | None
    max_value: float | None
    decimal_places: int | None = None     # Phase 23.9 — display precision
    aggregation: str                  # 'raw' | '1m' | '1h' | '1d'
    raw_count: int                    # total samples in window (pre-downsample)
    returned_count: int               # points in this response
    points: list[TrendPoint]


class TrendHistoryResponse(BaseModel):
    start: datetime
    end: datetime
    aggregation: str
    series: list[TrendSeries]


class TagAvailability(BaseModel):
    """Per-tag availability + quality summary. Maps to spec §10.3."""
    tag_id: int
    tag_name: str
    expected_samples: int
    actual_samples: int
    good_samples: int
    uncertain_samples: int
    bad_samples: int
    missing_samples: int              # max(0, expected - actual)
    availability_pct: float
    good_availability_pct: float
    longest_gap_sec: int | None
    longest_gap_start: datetime | None
    first_sample: datetime | None
    last_sample: datetime | None
    # Phase 13.11c — value statistics over the GOOD samples only (st >= 128).
    # Computing over good samples avoids the bad-data pollution that would
    # otherwise skew mean/stddev. Null for non-numeric tags (no value_double)
    # and for series with fewer than two good samples (stddev undefined).
    engineering_unit: str | None = None
    decimal_places: int | None = None     # Phase 23.9 — display precision
    mean_value: float | None = None
    stddev_value: float | None = None
    observed_min: float | None = None
    observed_max: float | None = None


class TrendSummaryResponse(BaseModel):
    start: datetime
    end: datetime
    tags: list[TagAvailability]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tag_ids(tag_ids: str) -> list[int]:
    """Parse '12,34,56' → [12, 34, 56], with validation."""
    if not tag_ids.strip():
        raise HTTPException(400, "tag_ids cannot be empty")
    try:
        ids = [int(x.strip()) for x in tag_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "tag_ids must be a comma-separated list of integers")
    if not ids:
        raise HTTPException(400, "tag_ids cannot be empty")
    if len(ids) > 20:
        raise HTTPException(400, "tag_ids may not exceed 20")
    return ids


def _quality_class(st: int | None) -> str | None:
    """Map an integer ST value to the spec's three named bands.

    The bands collapse the spec's §9.2 two 'Good' tiers (128-191 and 192-255)
    into a single 'good' since the trend UI doesn't distinguish them.
    """
    if st is None:
        return None
    for name, (lo, hi) in QUALITY_BANDS.items():
        if lo <= st < hi:
            return name
    return None


# ===========================================================================
# Endpoint 1 — GET /api/trends/tags
# ===========================================================================

@router.get("/tags", response_model=list[TrendTag])
def list_tags(
    db: Annotated[Session, Depends(get_session)],
    q: str | None = Query(None, description="Substring match on tag name or description"),
    device_id: int | None = None,
    channel_id: int | None = None,
    protocol: str | None = None,
    data_type: str | None = None,
    enabled_only: bool = Query(True, description="Hide disabled tags by default"),
    limit: int = Query(500, ge=1, le=2000),
) -> list[TrendTag]:
    """Searchable tag picker for the Trend page.

    Joins tags + devices + channels + register_blocks + latest_tag_values
    so the picker can show current value, ST, and last-update timestamp
    without a second round-trip. Maps to spec §5.1 + §5.2.
    """
    # Single SQL with all the joins. We use raw text() for readability and
    # because the latest_tag_values join is to a materialized view that
    # SQLAlchemy ORM doesn't model.
    sql = text("""
        SELECT
            t.id, t.name, t.description, t.data_type, t.address,
            t.engineering_unit_id,
            -- Some tags use the engineering_unit_id FK, older ones use the
            -- inline string column. Fall back so the response is never null
            -- just because one side or the other isn't populated.
            COALESCE(eu.label, t.engineering_unit) AS engineering_unit,
            t.min_value, t.max_value, t.enabled AS logging_enabled,
            t.register_block_id, rb.name AS register_block_name,
            d.id AS device_id, d.name AS device_name,
            c.id AS channel_id, c.name AS channel_name,
            pc.code AS protocol,
            lv.value_double AS current_value_double,
            lv.value_text   AS current_value_text,
            lv.st           AS current_st,
            lv.time         AS last_update_utc
        FROM tags t
        JOIN register_blocks rb       ON rb.id = t.register_block_id
        JOIN devices d                ON d.id = rb.device_id
        JOIN channels c               ON c.id = d.channel_id
        JOIN protocol_connectors pc   ON pc.id = c.protocol_connector_id
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        LEFT JOIN latest_tag_values lv ON lv.tag_id = t.id
        WHERE (:q IS NULL OR t.name ILIKE :q_pat OR t.description ILIKE :q_pat)
          AND (:device_id IS NULL OR d.id = :device_id)
          AND (:channel_id IS NULL OR c.id = :channel_id)
          AND (:protocol IS NULL OR pc.code = :protocol)
          AND (:data_type IS NULL OR t.data_type = :data_type)
          AND (NOT :enabled_only OR t.enabled = TRUE)
        ORDER BY d.name, t.name
        LIMIT :limit
    """)

    rows = db.execute(sql, {
        "q": q,
        "q_pat": f"%{q}%" if q else None,
        "device_id": device_id,
        "channel_id": channel_id,
        "protocol": protocol,
        "data_type": data_type,
        "enabled_only": enabled_only,
        "limit": limit,
    }).mappings().all()

    return [
        TrendTag(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            device_id=r["device_id"],
            device_name=r["device_name"],
            channel_id=r["channel_id"],
            channel_name=r["channel_name"],
            protocol=r["protocol"],
            register_block_id=r["register_block_id"],
            register_block_name=r["register_block_name"],
            address=r["address"],
            data_type=r["data_type"],
            engineering_unit=r["engineering_unit"],
            logging_enabled=r["logging_enabled"],
            min_value=r["min_value"],
            max_value=r["max_value"],
            current_value_double=r["current_value_double"],
            current_value_text=r["current_value_text"],
            current_st=r["current_st"],
            current_quality=_quality_class(r["current_st"]),
            last_update_utc=r["last_update_utc"],
        )
        for r in rows
    ]


# ===========================================================================
# Endpoint 2 — GET /api/trends/history
# ===========================================================================

@router.get("/history", response_model=TrendHistoryResponse)
def get_history(
    db: Annotated[Session, Depends(get_session)],
    tag_ids: str = Query(..., description="Comma-separated tag ids"),
    start: datetime = Query(..., description="ISO-8601 UTC"),
    end: datetime = Query(..., description="ISO-8601 UTC"),
    aggregation: Literal["auto", "raw", "1m", "1h", "1d"] = "auto",
    agg_mode: Literal["last", "first", "avg", "min", "max"] = Query(
        "last",
        description="When buckets are returned (any non-raw aggregation), "
                    "this selects which pre-computed column from the CA "
                    "becomes the line value. Ignored for raw rows.",
    ),
    max_points: int = Query(2000, ge=10, le=HARD_MAX_POINTS),
) -> TrendHistoryResponse:
    """Historical trend data with auto-aggregation.

    Per spec §16: above a span threshold we serve buckets from a continuous
    aggregate; under it we serve raw points. The chart always knows what it
    got because the response carries `aggregation` and `raw_count`.
    """
    if start >= end:
        raise HTTPException(400, "start must be before end")
    if (end - start) > timedelta(days=365):
        raise HTTPException(400, "Time range cannot exceed 365 days")

    ids = _parse_tag_ids(tag_ids)
    grain = _pick_aggregation(start, end, aggregation)

    # Tag metadata lookup — one query, returned in the same order as `ids`.
    meta_sql = text("""
        SELECT t.id, t.name, t.data_type, t.min_value, t.max_value,
               t.decimal_places,
               COALESCE(eu.label, t.engineering_unit) AS engineering_unit
        FROM tags t
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        WHERE t.id = ANY(:ids)
    """)
    meta_rows = {r["id"]: r for r in db.execute(meta_sql, {"ids": ids}).mappings().all()}
    missing = [i for i in ids if i not in meta_rows]
    if missing:
        raise HTTPException(404, f"Tag(s) not found: {missing}")

    series: list[TrendSeries] = []

    # Phase 24 — perf: cache the entire response by request signature so
    # rapid navigation between historical presets ("Current month" →
    # "Previous month" → back) is instant for the second hit. TTL is 60s
    # which matches React Query's refetchInterval. Live-mode queries
    # change start/end every second so they're effectively never cached;
    # that's intentional, freshness matters there.
    cache_key = (
        "history",
        tuple(ids),
        start.isoformat(),
        end.isoformat(),
        grain,
        agg_mode,
        max_points,
    )
    cached = _history_cache_get(cache_key)
    if cached is not None:
        return cached

    # Population count for the "downsampled from N" badge. The query
    # shape differs by grain:
    #   - raw: we genuinely need count(*) over tag_values; the chart
    #     uses it to compute stride.
    #   - 1m/1h/1d: read the pre-computed sum(sample_count) from the
    #     cagg itself. For a 30-day query this changes a ~26M-row scan
    #     into a ~30-row sum — orders of magnitude faster, and exactly
    #     the same answer (the cagg's sample_count IS the row count
    #     from tag_values for that bucket).
    if grain == "raw":
        raw_count_rows = db.execute(text("""
            SELECT tag_id, count(*)::bigint AS n
            FROM tag_values
            WHERE tag_id = ANY(:ids) AND time >= :s AND time < :e
            GROUP BY tag_id
        """), {"ids": ids, "s": start, "e": end}).mappings().all()
    else:
        view = {"1m": "tag_values_1m", "1h": "tag_values_1h", "1d": "tag_values_1d"}[grain]
        bucket_interval = {
            "tag_values_1m": "1 minute",
            "tag_values_1h": "1 hour",
            "tag_values_1d": "1 day",
        }[view]
        raw_count_rows = db.execute(text(f"""
            SELECT tag_id, COALESCE(SUM(sample_count), 0)::bigint AS n
            FROM {view}
            WHERE tag_id = ANY(:ids)
              AND bucket + INTERVAL '{bucket_interval}' > :s
              AND bucket < :e
            GROUP BY tag_id
        """), {"ids": ids, "s": start, "e": end}).mappings().all()
    raw_counts: dict[int, int] = {r["tag_id"]: int(r["n"]) for r in raw_count_rows}

    # Batch the actual points. For raw queries we keep the per-tag loop
    # (stride sampling needs per-tag row count). For cagg queries we
    # fetch all tags in a single statement keyed on tag_id IN (...).
    if grain == "raw":
        points_by_tag: dict[int, list[TrendPoint]] = {
            tid: _query_raw(db, tid, start, end, max_points) for tid in ids
        }
    else:
        view = {"1m": "tag_values_1m", "1h": "tag_values_1h", "1d": "tag_values_1d"}[grain]
        points_by_tag = _query_aggregated_batch(db, view, ids, start, end, max_points, agg_mode)

    for tag_id in ids:
        meta = meta_rows[tag_id]
        points = points_by_tag.get(tag_id, [])
        series.append(TrendSeries(
            tag_id=tag_id,
            tag_name=meta["name"],
            engineering_unit=meta["engineering_unit"],
            data_type=meta["data_type"],
            min_value=meta["min_value"],
            max_value=meta["max_value"],
            decimal_places=meta["decimal_places"],
            aggregation=grain,
            raw_count=raw_counts.get(tag_id, 0),
            returned_count=len(points),
            points=points,
        ))

    response = TrendHistoryResponse(start=start, end=end, aggregation=grain, series=series)
    _history_cache_set(cache_key, response)
    return response


def _query_raw(db: Session, tag_id: int, start: datetime, end: datetime,
               max_points: int) -> list[TrendPoint]:
    """Raw points from tag_values, capped at max_points via stride.

    If the raw count exceeds max_points, we do a deterministic stride
    sample rather than aggregation — operator sees actual recorded values,
    not synthesized buckets. (For "I want the real numbers", aggregation
    lies and stride sampling doesn't.)
    """
    # Use a window function to assign row numbers, then sample by stride.
    # Stride = ceil(raw_count / max_points). PG's NTILE could also work
    # but it'd flatten timing distribution; stride preserves it.
    sql = text("""
        WITH numbered AS (
            SELECT
                time, value_double, value_text, st, source,
                row_number() OVER (ORDER BY time) - 1 AS rn,
                count(*) OVER () AS total
            FROM tag_values
            WHERE tag_id = :tid AND time >= :s AND time < :e
        )
        SELECT time, value_double, value_text, st, source
        FROM numbered
        WHERE total <= :max_pts
           OR rn % GREATEST(1, (total / :max_pts)::int) = 0
        ORDER BY time
        LIMIT :max_pts
    """)
    rows = db.execute(sql, {
        "tid": tag_id, "s": start, "e": end, "max_pts": max_points,
    }).all()
    return [
        TrendPoint(
            t=r.time,
            v=r.value_double,
            vt=r.value_text,
            st=r.st or 0,
            src=r.source,
        )
        for r in rows
    ]


def _query_aggregated(db: Session, view: str, tag_id: int,
                      start: datetime, end: datetime,
                      max_points: int,
                      agg_mode: str = "last") -> list[TrendPoint]:
    """Bucket-aggregated points from a continuous aggregate.

    Each row carries the envelope (min/max) plus first/last/avg so the
    chart can draw whichever line style operators prefer. `agg_mode` picks
    which pre-computed column becomes the primary `v` value:

        last  -> last_value  (default, "most-recently-seen")
        first -> first_value ("oldest value in bucket")
        avg   -> avg_value   ("bucket mean")
        min   -> min_value   ("bucket trough")
        max   -> max_value   ("bucket peak")

    mn/mx still travel with every point so the frontend can render an
    envelope/band overlay regardless of which column drives the line.

    Window-overlap semantics: include any bucket whose interval overlaps
    the user's window, not just buckets whose start is inside it. Without
    this, a "Last 6 hours" query at 11:30 (start=05:30) would exclude the
    05:00 1h bucket — even though the bucket's data from 05:30-06:00 is
    fully within the user's window. Operators would silently miss up to
    one bucket interval's worth of data at the leading edge.
    """
    # Bucket size for the overlap WHERE clause. View names are internal
    # constants so the f-string interpolation is safe (no user input).
    bucket_interval = {
        "tag_values_1m": "1 minute",
        "tag_values_1h": "1 hour",
        "tag_values_1d": "1 day",
    }[view]
    sql = text(f"""
        SELECT bucket, first_value, last_value, min_value, max_value, avg_value,
               sample_count, good_count, bad_count
        FROM {view}
        WHERE tag_id = :tid
          AND bucket + INTERVAL '{bucket_interval}' > :s
          AND bucket < :e
        ORDER BY bucket
        LIMIT :max_pts
    """)
    rows = db.execute(sql, {
        "tid": tag_id, "s": start, "e": end, "max_pts": max_points,
    }).all()
    # Map mode -> column accessor on each row. Mode is already validated
    # by the FastAPI Literal type, so a missing key would be a coding bug.
    pick = {
        "last":  lambda r: r.last_value,
        "first": lambda r: r.first_value,
        "avg":   lambda r: r.avg_value,
        "min":   lambda r: r.min_value,
        "max":   lambda r: r.max_value,
    }[agg_mode]
    return [
        TrendPoint(
            t=r.bucket,
            v=pick(r),                # selected by agg_mode
            mn=r.min_value,
            mx=r.max_value,
            g=r.good_count,
            b=r.bad_count,
        )
        for r in rows
    ]


def _query_aggregated_batch(
    db: Session,
    view: str,
    tag_ids: list[int],
    start: datetime,
    end: datetime,
    max_points: int,
    agg_mode: str = "last",
) -> dict[int, list[TrendPoint]]:
    """Same as _query_aggregated but for many tags in ONE SQL round trip.

    Phase 24 — perf: replaces the per-tag loop in get_history. For a
    10-tag query that previously did 10 separate SELECTs (≈ 10 × 30ms
    round-trip), this is a single query that returns rows for all tags
    interleaved. We split them back into a {tag_id: [points]} dict in
    Python.

    Per-tag LIMIT is enforced by a window function (ROW_NUMBER OVER
    PARTITION BY tag_id) so each tag still respects max_points. In
    practice the bucket count per tag is small for typical windows so
    this rarely truncates.
    """
    bucket_interval = {
        "tag_values_1m": "1 minute",
        "tag_values_1h": "1 hour",
        "tag_values_1d": "1 day",
    }[view]

    sql = text(f"""
        WITH ranked AS (
            SELECT tag_id, bucket, first_value, last_value, min_value, max_value,
                   avg_value, sample_count, good_count, bad_count,
                   ROW_NUMBER() OVER (PARTITION BY tag_id ORDER BY bucket) AS rn
            FROM {view}
            WHERE tag_id = ANY(:ids)
              AND bucket + INTERVAL '{bucket_interval}' > :s
              AND bucket < :e
        )
        SELECT * FROM ranked
        WHERE rn <= :max_pts
        ORDER BY tag_id, bucket
    """)
    rows = db.execute(sql, {
        "ids": tag_ids, "s": start, "e": end, "max_pts": max_points,
    }).all()

    pick = {
        "last":  lambda r: r.last_value,
        "first": lambda r: r.first_value,
        "avg":   lambda r: r.avg_value,
        "min":   lambda r: r.min_value,
        "max":   lambda r: r.max_value,
    }[agg_mode]

    out: dict[int, list[TrendPoint]] = {tid: [] for tid in tag_ids}
    for r in rows:
        out[r.tag_id].append(TrendPoint(
            t=r.bucket,
            v=pick(r),
            mn=r.min_value,
            mx=r.max_value,
            g=r.good_count,
            b=r.bad_count,
        ))
    return out


# ===========================================================================
# Raw historical data table (spec section 7.4)
# ===========================================================================
# Flat row list of raw tag_values joined to all metadata an operator might
# want to inspect: device, protocol, channel, register block, address,
# engineering unit, raw ST integer and derived quality class. Distinct from
# /history (which is series-grouped and stride-sampled) - this endpoint
# returns true raw rows, capped at `limit`.
#
# Insert time (spec field 13) is omitted until migration 0028 adds an
# `inserted_at` column to tag_values. The 12 fields below cover the rest.


class RawTableRow(BaseModel):
    t: datetime                       # sample timestamp (UTC)
    tag_id: int
    tag_name: str
    v: float | None = None
    vt: str | None = None
    engineering_unit: str | None = None
    decimal_places: int | None = None   # Phase 23.9 — display precision
    st: int | None = None
    st_class: str | None = None       # derived: good/uncertain/bad
    device_name: str
    protocol: str | None = None
    channel_name: str
    register_block_name: str | None = None
    address: int | None = None
    data_type: str
    source: str | None = None


class RawTableResponse(BaseModel):
    start: datetime
    end: datetime
    rows: list[RawTableRow]
    returned: int
    limit: int
    truncated: bool                   # True if the underlying query hit `limit`


@router.get("/raw_table", response_model=RawTableResponse)
def get_raw_table(
    db: Annotated[Session, Depends(get_session)],
    tag_ids: str = Query(..., description="Comma-separated tag ids"),
    start: datetime = Query(...),
    end: datetime = Query(...),
    limit: int = Query(1000, ge=1, le=10000),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> RawTableResponse:
    """Raw rows from tag_values with joined tag/device/channel/block metadata.

    Used by the Raw Historical Data Table on the Trend page. Always returns
    raw (non-aggregated) rows regardless of the chart's current aggregation
    selection - the operator looking at this table wants the actual records,
    not bucket summaries.

    Truncation: when more than `limit` rows exist in the window, returns the
    most recent (or oldest, per `order`) `limit` rows and sets `truncated=True`.
    Operator narrows the window or raises the limit to see more.
    """
    ids = _parse_tag_ids(tag_ids)

    # ORDER BY direction injected as identifier (not bindable). Validated by
    # the `pattern` on the Query param above.
    direction = "DESC" if order == "desc" else "ASC"

    sql = text(f"""
        SELECT
            v.time, v.tag_id, v.value_double, v.value_text,
            v.st, v.source,
            t.name AS tag_name, t.data_type, t.address, t.decimal_places,
            COALESCE(eu.label, t.engineering_unit) AS engineering_unit,
            d.name AS device_name,
            pc.code AS protocol,
            c.name AS channel_name,
            rb.name AS register_block_name
        FROM tag_values v
        JOIN tags t                   ON t.id = v.tag_id
        JOIN register_blocks rb       ON rb.id = t.register_block_id
        JOIN devices d                ON d.id = rb.device_id
        JOIN channels c               ON c.id = d.channel_id
        JOIN protocol_connectors pc   ON pc.id = c.protocol_connector_id
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        WHERE v.tag_id = ANY(:ids)
          AND v.time >= :start AND v.time < :end
        ORDER BY v.time {direction}
        LIMIT :limit_plus_one
    """)
    # Fetch limit+1 to detect truncation without a second COUNT query.
    rows = db.execute(sql, {
        "ids": ids, "start": start, "end": end,
        "limit_plus_one": limit + 1,
    }).all()
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    out = [
        RawTableRow(
            t=r.time,
            tag_id=r.tag_id,
            tag_name=r.tag_name,
            v=r.value_double,
            vt=r.value_text,
            engineering_unit=r.engineering_unit,
            decimal_places=r.decimal_places,
            st=r.st,
            st_class=_quality_class(r.st),
            device_name=r.device_name,
            protocol=r.protocol,
            channel_name=r.channel_name,
            register_block_name=r.register_block_name,
            address=r.address,
            data_type=r.data_type,
            source=r.source,
        )
        for r in rows
    ]
    return RawTableResponse(
        start=start, end=end,
        rows=out, returned=len(out),
        limit=limit, truncated=truncated,
    )


# ===========================================================================
# Summary (spec section 9.3)
# ===========================================================================

@router.get("/summary", response_model=TrendSummaryResponse)
def get_summary(
    db: Annotated[Session, Depends(get_session)],
    tag_ids: str = Query(..., description="Comma-separated tag ids"),
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> TrendSummaryResponse:
    """Availability + quality summary per tag for the window. Spec §10.3.

    "Expected samples" comes from the register_block's scan_interval_ms —
    we know how often a tag *should* have been polled, so missing samples
    = expected - actual. (For tags with no logging interval configured,
    expected_samples is reported as the actual count and availability
    naturally becomes 100% — we can't claim missing data we didn't expect.)

    Performance design (Phase 23.5):
      For windows ≤ 30 min we scan raw `tag_values` — accurate stddev
      and exact gap detection matter most at short ranges where they're
      affordable. For longer windows we read from a continuous aggregate
      (1m / 1h / 1d depending on span) — 200×–1000× fewer rows scanned,
      same count breakdown (the caggs have good/uncertain/bad columns).
      stddev and exact gap detection are not available from caggs so
      those fields return NULL on the cagg path (frontend renders "—").

      A 30-second in-process TTL cache absorbs React Query's refetch
      cycle and multi-component re-renders on the trend page.
    """
    if start >= end:
        raise HTTPException(400, "start must be before end")

    ids = _parse_tag_ids(tag_ids)
    duration_sec = (end - start).total_seconds()

    # Cache key. Normalize the tag id list so order doesn't matter, and
    # round timestamps to whole seconds so jitter from the frontend's
    # Date.now() doesn't break the cache.
    cache_key = (
        "summary",
        tuple(sorted(ids)),
        start.replace(microsecond=0).isoformat(),
        end.replace(microsecond=0).isoformat(),
    )
    cached = _summary_cache_get(cache_key)
    if cached is not None:
        return cached

    cagg = _pick_summary_cagg(start, end)
    if cagg is None:
        rows = _summary_query_raw(db, ids, start, end)
    else:
        rows = _summary_query_cagg(db, ids, start, end, cagg)

    out: list[TagAvailability] = []
    for r in rows:
        scan_ms = r["scan_interval_ms"]
        if scan_ms and scan_ms > 0:
            expected = int(duration_sec * 1000 / scan_ms)
        else:
            # No scan interval configured — treat actual as expected so
            # availability == 100% rather than 0% / undefined.
            expected = r["actual_samples"]

        actual = r["actual_samples"]
        good = r["good_samples"]
        missing = max(0, expected - actual)
        avail = (actual / expected * 100) if expected > 0 else 0.0
        good_avail = (good / expected * 100) if expected > 0 else 0.0

        out.append(TagAvailability(
            tag_id=r["tag_id"],
            tag_name=r["tag_name"],
            expected_samples=expected,
            actual_samples=actual,
            good_samples=good,
            uncertain_samples=r["uncertain_samples"],
            bad_samples=r["bad_samples"],
            missing_samples=missing,
            availability_pct=round(avail, 2),
            good_availability_pct=round(good_avail, 2),
            longest_gap_sec=r["longest_gap_sec"],
            longest_gap_start=r["longest_gap_start"],
            first_sample=r["first_sample"],
            last_sample=r["last_sample"],
            engineering_unit=r["engineering_unit"],
            decimal_places=r["decimal_places"],
            mean_value=r["mean_value"],
            stddev_value=r["stddev_value"],
            observed_min=r["observed_min"],
            observed_max=r["observed_max"],
        ))

    response = TrendSummaryResponse(start=start, end=end, tags=out)
    _summary_cache_set(cache_key, response)
    return response


def _pick_summary_cagg(start: datetime, end: datetime) -> str | None:
    """Return the cagg view name to use, or None for raw scan.

    Thresholds mirror `_pick_aggregation` (used by /history) so summary
    and trend chart switch to caggs at the same window sizes — they
    look at the same data through similar lenses.

      ≤ 30 min   : raw       — accurate stddev + per-second gap detection
      ≤ 4 hours  : 1m cagg   — ≤240 buckets/tag
      ≤ 5 days   : 1h cagg   — ≤120 buckets/tag
      else       : 1d cagg   — ≤365 buckets/year
    """
    span = end - start
    if span <= timedelta(minutes=30):
        return None
    if span <= timedelta(hours=4):
        return "tag_values_1m"
    if span <= timedelta(days=5):
        return "tag_values_1h"
    return "tag_values_1d"


def _summary_query_raw(
    db: Session, ids: list[int], start: datetime, end: datetime,
) -> list[dict]:
    """Raw-scan summary path — current behavior, used only for ≤30-min windows.

    Computes stddev and exact gap detection (LAG window function) from
    raw samples. Filters mean/stddev/min/max by the tag's engineering
    range to keep bad-decode outliers from polluting the statistics.
    """
    sql = text("""
        WITH counts AS (
            SELECT
                t.id AS tag_id, t.name AS tag_name,
                COALESCE(eu.label, t.engineering_unit) AS engineering_unit,
                t.decimal_places,
                rb.scan_interval_ms,
                COALESCE(SUM(1) FILTER (WHERE tv.st >= 128), 0) AS good_samples,
                COALESCE(SUM(1) FILTER (WHERE tv.st >= 64 AND tv.st < 128), 0) AS uncertain_samples,
                COALESCE(SUM(1) FILTER (WHERE tv.st < 64), 0) AS bad_samples,
                COALESCE(SUM(1), 0) AS actual_samples,
                MIN(tv.time) AS first_sample,
                MAX(tv.time) AS last_sample,
                AVG(tv.value_double) FILTER (
                    WHERE tv.st >= 128 AND tv.value_double IS NOT NULL
                      AND (t.min_value IS NULL OR tv.value_double >= t.min_value)
                      AND (t.max_value IS NULL OR tv.value_double <= t.max_value)
                ) AS mean_value,
                STDDEV_SAMP(tv.value_double) FILTER (
                    WHERE tv.st >= 128 AND tv.value_double IS NOT NULL
                      AND (t.min_value IS NULL OR tv.value_double >= t.min_value)
                      AND (t.max_value IS NULL OR tv.value_double <= t.max_value)
                ) AS stddev_value,
                MIN(tv.value_double) FILTER (
                    WHERE tv.st >= 128
                      AND (t.min_value IS NULL OR tv.value_double >= t.min_value)
                      AND (t.max_value IS NULL OR tv.value_double <= t.max_value)
                ) AS observed_min,
                MAX(tv.value_double) FILTER (
                    WHERE tv.st >= 128
                      AND (t.min_value IS NULL OR tv.value_double >= t.min_value)
                      AND (t.max_value IS NULL OR tv.value_double <= t.max_value)
                ) AS observed_max
            FROM tags t
            JOIN register_blocks rb ON rb.id = t.register_block_id
            LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
            LEFT JOIN tag_values tv
              ON tv.tag_id = t.id AND tv.time >= :s AND tv.time < :e
            WHERE t.id = ANY(:ids)
            GROUP BY t.id, t.name, eu.label, t.engineering_unit, t.decimal_places, rb.scan_interval_ms
        ),
        gaps AS (
            SELECT tag_id,
                   MAX(gap_sec) AS longest_gap_sec,
                   (ARRAY_AGG(prev_time ORDER BY gap_sec DESC))[1] AS longest_gap_start
            FROM (
                SELECT
                    tag_id,
                    LAG(time) OVER (PARTITION BY tag_id ORDER BY time) AS prev_time,
                    EXTRACT(EPOCH FROM (time - LAG(time) OVER (PARTITION BY tag_id ORDER BY time)))::bigint AS gap_sec
                FROM tag_values
                WHERE tag_id = ANY(:ids) AND time >= :s AND time < :e
            ) g
            WHERE gap_sec IS NOT NULL
            GROUP BY tag_id
        )
        SELECT c.*, g.longest_gap_sec, g.longest_gap_start
        FROM counts c
        LEFT JOIN gaps g ON g.tag_id = c.tag_id
        ORDER BY c.tag_name
    """)
    return db.execute(sql, {"ids": ids, "s": start, "e": end}).mappings().all()


def _summary_query_cagg(
    db: Session, ids: list[int], start: datetime, end: datetime, cagg_name: str,
) -> list[dict]:
    """Cagg-based summary path — used for windows > 30 minutes.

    cagg_name is whitelisted (NEVER user-controlled) to one of:
      'tag_values_1m', 'tag_values_1h', 'tag_values_1d'
    so it's safe to f-string into the SQL.

    The caggs (built in migration 0024_trend_aggregates) carry the count
    breakdown we need: good_count / uncertain_count / bad_count plus
    sample_count, avg_value, min_value, max_value per bucket. That lets us
    compute everything except stddev (no sum-of-squares column) and
    sub-bucket gap detection — both NULL on this path.

    Mean is a sample-count-weighted average of bucket avg_values, which
    is the mathematically correct sample-weighted mean across the window.

    Phase 23.5 perf rewrite — the cagg aggregation is now a clean CTE
    with WHERE tag_id = ANY(:ids) AND bucket BETWEEN ... so the
    composite (tag_id, bucket) index added by migration 0048 is used
    directly. The engineering-range filter moved out of the FILTER
    aggregate and into a CASE on the result (much cheaper, and with
    Phase 18.0 sanity checks upstream individual bucket extrema
    outside the range are vanishingly rare).
    """
    if cagg_name not in {"tag_values_1m", "tag_values_1h", "tag_values_1d"}:
        # Defense-in-depth — _pick_summary_cagg only returns whitelisted
        # names, but a future refactor must not let this become an SQL
        # injection vector.
        raise HTTPException(500, f"Internal: invalid cagg '{cagg_name}'")

    sql = text(f"""
        WITH cagg_agg AS (
            -- Single index-scan over the cagg restricted to the selected
            -- tag IDs and time window. Uses ix_<cagg>_tag_bucket from
            -- migration 0048 for direct per-tag lookups.
            SELECT
                c.tag_id,
                SUM(c.good_count)::bigint       AS good_samples,
                SUM(c.uncertain_count)::bigint  AS uncertain_samples,
                SUM(c.bad_count)::bigint        AS bad_samples,
                SUM(c.sample_count)::bigint     AS actual_samples,
                MIN(c.bucket)                    AS first_sample,
                MAX(c.bucket)                    AS last_sample,
                CASE WHEN SUM(c.sample_count) > 0
                     THEN (SUM(c.avg_value * c.sample_count)
                           / SUM(c.sample_count))::float
                END                              AS mean_value,
                -- Phase 23.5 stddev approximation, v2 — uses the LAW OF
                -- TOTAL VARIANCE to combine BETWEEN-bucket and WITHIN-
                -- bucket variation into a single estimate.
                --
                --   σ² ≈ VAR(bucket_avg)                              ← between
                --      + AVG( ((bucket_max - bucket_min) / 4)² )      ← within
                --
                -- Range/4 is the standard non-parametric σ estimator for
                -- moderate n (for n=60-3600 it's accurate within ~5-10%
                -- of true σ for roughly-Normal data). The two terms
                -- combine via E[Var(X|bucket)] + Var(E[X|bucket]) = Var(X).
                --
                -- Without the within-bucket term, signals with strong
                -- intra-bucket noise (e.g. atmospheric pressure varying
                -- 0.5 kPa over a day, while hourly averages barely move)
                -- would report σ orders of magnitude too small — making
                -- downstream stats (sigma popover, ±2σ warn lines) wrong.
                --
                -- Returns NULL when fewer than 2 total samples present.
                -- FILTER on min/max NOT NULL guards against pathological
                -- buckets where everything was bad-quality (cagg may have
                -- written nulls for min/max if no good samples).
                CASE WHEN SUM(c.sample_count) > 1
                     THEN SQRT(
                            COALESCE(VAR_SAMP(c.avg_value), 0)
                            + COALESCE(
                                AVG(
                                    POWER((c.max_value - c.min_value) / 4.0, 2)
                                ) FILTER (
                                    WHERE c.min_value IS NOT NULL
                                      AND c.max_value IS NOT NULL
                                      AND c.max_value >= c.min_value
                                ),
                                0
                              )
                          )::float
                END                              AS stddev_value,
                MIN(c.min_value)                 AS observed_min_raw,
                MAX(c.max_value)                 AS observed_max_raw
            FROM {cagg_name} c
            WHERE c.tag_id = ANY(:ids)
              AND c.bucket >= :s
              AND c.bucket <  :e
            GROUP BY c.tag_id
        )
        SELECT
            t.id   AS tag_id,
            t.name AS tag_name,
            COALESCE(eu.label, t.engineering_unit) AS engineering_unit,
            t.decimal_places,
            rb.scan_interval_ms,
            COALESCE(a.good_samples,      0)::bigint AS good_samples,
            COALESCE(a.uncertain_samples, 0)::bigint AS uncertain_samples,
            COALESCE(a.bad_samples,       0)::bigint AS bad_samples,
            COALESCE(a.actual_samples,    0)::bigint AS actual_samples,
            a.first_sample,
            a.last_sample,
            a.mean_value,
            a.stddev_value,
            CASE WHEN a.observed_min_raw IS NOT NULL
                      AND (t.min_value IS NULL OR a.observed_min_raw >= t.min_value)
                      AND (t.max_value IS NULL OR a.observed_min_raw <= t.max_value)
                 THEN a.observed_min_raw::float
            END AS observed_min,
            CASE WHEN a.observed_max_raw IS NOT NULL
                      AND (t.min_value IS NULL OR a.observed_max_raw >= t.min_value)
                      AND (t.max_value IS NULL OR a.observed_max_raw <= t.max_value)
                 THEN a.observed_max_raw::float
            END AS observed_max,
            NULL::bigint        AS longest_gap_sec,
            NULL::timestamptz   AS longest_gap_start
        FROM tags t
        JOIN register_blocks rb ON rb.id = t.register_block_id
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        LEFT JOIN cagg_agg a ON a.tag_id = t.id
        WHERE t.id = ANY(:ids)
        ORDER BY t.name
    """)
    return db.execute(sql, {"ids": ids, "s": start, "e": end}).mappings().all()


# ── In-process TTL cache for the summary endpoint ─────────────────────
# Same pattern as the calendar heatmap cache. 30s TTL is short enough
# that "live" panels feel current and long enough to absorb the React
# Query refetch cycle on the trend page (a couple of components all
# request /summary back-to-back during page navigation).
import time as _summary_time
_SUMMARY_CACHE: dict[tuple, tuple[float, Any]] = {}
_SUMMARY_CACHE_TTL = 30.0
_SUMMARY_CACHE_MAX = 64


def _summary_cache_get(key: tuple):
    entry = _SUMMARY_CACHE.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if _summary_time.monotonic() > expiry:
        _SUMMARY_CACHE.pop(key, None)
        return None
    return value


def _summary_cache_set(key: tuple, value: Any) -> None:
    _SUMMARY_CACHE[key] = (_summary_time.monotonic() + _SUMMARY_CACHE_TTL, value)
    if len(_SUMMARY_CACHE) > _SUMMARY_CACHE_MAX:
        oldest = min(_SUMMARY_CACHE.items(), key=lambda kv: kv[1][0])
        _SUMMARY_CACHE.pop(oldest[0], None)


# ===========================================================================
# Phase 13.4 — Saved trend views
# ===========================================================================
# A saved view is a named tag-set + window configuration that an operator
# can recall instantly. Stored in trend_views.config_json (JSONB) so the
# schema can evolve without migrations as the chart gains features.


class TrendViewConfig(BaseModel):
    """Stored view configuration. All time-axis information in one place
    so a saved view can fully reconstruct chart state on reload."""
    tag_ids: list[int]
    mode: Literal["historical", "live"]
    preset_minutes: int | None = None   # rolling window (live) or relative preset (historical)
    preset_label: str | None = None     # human display label, e.g. "Last 1 h"
    start: datetime | None = None       # absolute, for custom historical ranges
    end: datetime | None = None


class TrendViewBase(BaseModel):
    name: str
    description: str | None = None
    config: TrendViewConfig


class TrendViewCreate(TrendViewBase):
    pass


class TrendView(TrendViewBase):
    id: int
    created_at: datetime
    updated_at: datetime


@router.get("/views", response_model=list[TrendView])
def list_views(
    db: Annotated[Session, Depends(get_session)],
) -> list[TrendView]:
    """List saved views, most recently updated first."""
    sql = text("""
        SELECT id, name, description, config_json, created_at, updated_at
        FROM trend_views
        ORDER BY updated_at DESC
    """)
    rows = db.execute(sql).mappings().all()
    return [
        TrendView(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            config=TrendViewConfig.model_validate(r["config_json"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


@router.post("/views", response_model=TrendView, status_code=201)
def create_view(
    view: TrendViewCreate,
    db: Annotated[Session, Depends(get_session)],
) -> TrendView:
    """Save a new view. Until Auth lands, owner_user_id is NULL — the
    unique index on (owner_user_id, name) makes names unique in this
    single namespace."""
    if not view.name.strip():
        raise HTTPException(400, "View name is required")
    if not view.config.tag_ids:
        raise HTTPException(400, "View must include at least one tag")

    import json
    sql = text("""
        INSERT INTO trend_views (name, description, config_json, owner_user_id)
        VALUES (:name, :description, CAST(:config AS jsonb), NULL)
        RETURNING id, created_at, updated_at
    """)
    try:
        row = db.execute(sql, {
            "name": view.name.strip(),
            "description": view.description,
            "config": json.dumps(view.config.model_dump(mode="json")),
        }).mappings().one()
        db.commit()
    except Exception as e:
        db.rollback()
        # Most likely cause: unique violation on (owner_user_id, name).
        # Any other DB error also surfaces as 409 here so callers get one
        # actionable response code for "couldn't save".
        raise HTTPException(409, f"Could not save view: {e!s}")

    return TrendView(
        id=row["id"],
        name=view.name.strip(),
        description=view.description,
        config=view.config,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.delete("/views/{view_id}")
def delete_view(
    view_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Delete a saved view. Returns 200 with `{"ok": true}` on success;
    we don't use 204 here because FastAPI's response-body assertion is
    strict about no-content status codes having no return annotation."""
    result = db.execute(
        text("DELETE FROM trend_views WHERE id = :id"),
        {"id": view_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, f"View {view_id} not found")
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Phase 23.3 — Calendar heatmap
#
# Visualizes one tag's behavior aggregated by (day-of-week, hour-of-day).
# 7 rows × 24 columns = 168 cells. Each cell is the average value of that
# tag observed during that (DoW, hour) bucket over the last N weeks.
#
# Reveals patterns invisible in a line chart:
#   - "Process always upsets at 06:00 on Mondays" → batch startup issue
#   - "Pressure drops every Saturday afternoon" → weekend maintenance window
#   - "Flow is bimodal between 07-09 and 17-19" → shift transitions
#
# We aggregate value_double (numeric tags). Boolean / text tags get a count
# instead. ISO day-of-week is used: 1=Monday, 7=Sunday — matches Python's
# isoweekday() and most operator intuition.
# ---------------------------------------------------------------------------

class CalendarCell(BaseModel):
    """One (date, hour) bucket of the heatmap.

    Phase 23.3 redesign — was (dow, hour) aggregating across all
    Mondays/Tuesdays/etc, which obscured WHEN the data was collected.
    Now each row is a specific calendar date, so operators can see
    "what happened on May 23 at 06:00" not just "what tends to happen
    on Saturdays".
    """
    date: str    # ISO YYYY-MM-DD in the configured app timezone
    hour: int    # 0-23 local
    avg: float | None
    min: float | None
    max: float | None
    count: int


class CalendarHeatmapResponse(BaseModel):
    tag_id: int
    tag_name: str
    engineering_unit: str | None
    data_type: str
    decimal_places: int | None = None     # Phase 23.9 — display precision
    weeks: int
    # Phase 23.3 redesign — date metadata for the frontend to render
    # the full calendar grid (including dates with zero samples).
    timezone: str
    start_date: str        # ISO YYYY-MM-DD inclusive (oldest date in grid)
    end_date: str          # ISO YYYY-MM-DD inclusive (most recent date in grid)
    dates: list[str]       # All dates in [start_date, end_date], oldest first
    cells: list[CalendarCell]
    global_min: float | None
    global_max: float | None
    global_avg: float | None
    total_samples: int


@router.get("/calendar-heatmap", response_model=CalendarHeatmapResponse)
def calendar_heatmap(
    db: Annotated[Session, Depends(get_session)],
    tag_id: Annotated[int, Query(ge=1)],
    weeks: Annotated[int, Query(ge=1, le=12)] = 4,
):
    """Aggregate one tag's values by (day-of-week, hour-of-day) over N weeks.

    Performance design (Phase 23.3.1):
      1. Reads from `tag_values_1h` continuous aggregate (built in
         migration 0024). Hourly buckets are exactly the granularity this
         heatmap needs, so 4-12 weeks × 1 tag = at most ~2,016 pre-aggregated
         rows — orders of magnitude fewer than the raw ~2.4M-row scan.
      2. In-process TTL cache (60s) to absorb the React Query refetch
         cycle and rapid week-window switching.
      3. Fallback path scans raw tag_values for installations where the
         cagg hasn't been built (defensive, shouldn't trigger).
    """
    cache_key = ("calendar_heatmap", tag_id, weeks)
    cached = _calendar_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        result = _build_calendar_heatmap(db, tag_id, weeks)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception(
            "calendar_heatmap failed: tag_id=%d weeks=%d", tag_id, weeks,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Calendar heatmap query failed: {type(exc).__name__}: {exc}",
        )

    _calendar_cache_set(cache_key, result)
    return result


def _build_calendar_heatmap(
    db: Session,
    tag_id: int,
    weeks: int,
) -> "CalendarHeatmapResponse":
    """Cache-miss branch of calendar_heatmap, extracted for cleanliness.

    Phase 23.3 redesign — groups by (local-date, local-hour) instead of
    (day-of-week, hour). Each row in the response is a specific
    calendar date, not a weekday aggregate. Restores investigative
    utility ("what happened on May 23?") that the previous weekly-
    rhythm view lost.
    """
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo

    # Tag metadata first (small, cached by FK joins)
    tag = db.execute(text("""
        SELECT t.id, t.name, t.data_type, t.decimal_places,
               COALESCE(eu.label, t.engineering_unit) AS engineering_unit
        FROM tags t
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        WHERE t.id = :tag_id
    """), {"tag_id": tag_id}).mappings().first()

    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")

    # Compute the precise [start_date, end_date] window in LOCAL time.
    # This gives us a known N-day-tall calendar regardless of how the
    # TZ offset shifts the UTC bucket boundaries.
    tz_name = settings.app_timezone
    tz = ZoneInfo(tz_name)
    days_count = weeks * 7
    today_local: date = datetime.now(tz).date()
    start_date: date = today_local - timedelta(days=days_count - 1)
    end_date: date = today_local
    all_dates: list[str] = [
        (start_date + timedelta(days=i)).isoformat()
        for i in range(days_count)
    ]

    # Aggregate. Reads from tag_values_1h cagg (one row per tag per UTC
    # hour); after grouping by local date+hour we get at most
    # days_count × 24 = 168-2016 rows. Falls back to raw tag_values if
    # the cagg is missing (defensive — shouldn't trigger on Phase 13.1+).
    cagg_exists = db.execute(text(
        "SELECT to_regclass('tag_values_1h') IS NOT NULL AS exists"
    )).scalar()

    if cagg_exists:
        rows = db.execute(text("""
            SELECT
                to_char(bucket AT TIME ZONE :tz, 'YYYY-MM-DD')      AS day,
                extract(hour from bucket AT TIME ZONE :tz)::int     AS hour,
                avg(avg_value)::float                                AS avg_val,
                min(min_value)::float                                AS min_val,
                max(max_value)::float                                AS max_val,
                sum(sample_count)::int                               AS sample_count
            FROM tag_values_1h
            WHERE tag_id = :tag_id
              AND bucket >= NOW() - make_interval(days => :days)
              AND good_count > 0
            GROUP BY day, hour
            ORDER BY day, hour
        """), {
            "tag_id": tag_id,
            "days": days_count + 1,  # +1 day buffer for TZ offset
            "tz": tz_name,
        }).mappings().all()
    else:
        rows = db.execute(text("""
            SELECT
                to_char(time AT TIME ZONE :tz, 'YYYY-MM-DD')        AS day,
                extract(hour from time AT TIME ZONE :tz)::int       AS hour,
                avg(value_double)::float                             AS avg_val,
                min(value_double)::float                             AS min_val,
                max(value_double)::float                             AS max_val,
                count(*)::int                                        AS sample_count
            FROM tag_values
            WHERE tag_id = :tag_id
              AND time >= NOW() - make_interval(days => :days)
              AND st >= 128
            GROUP BY day, hour
            ORDER BY day, hour
        """), {
            "tag_id": tag_id,
            "days": days_count + 1,
            "tz": tz_name,
        }).mappings().all()

    # Filter to the precise local-date window. The +1-day buffer above
    # can pull in samples just outside our window after TZ conversion,
    # so we drop anything outside [start_date, end_date] here.
    allowed_dates = set(all_dates)
    cells = [
        CalendarCell(
            date=r["day"],
            hour=r["hour"],
            avg=r["avg_val"],
            min=r["min_val"],
            max=r["max_val"],
            count=r["sample_count"],
        )
        for r in rows
        if r["day"] in allowed_dates
    ]

    # Global stats for color-scale normalization on the client.
    avgs = [c.avg for c in cells if c.avg is not None]
    mins = [c.min for c in cells if c.min is not None]
    maxs = [c.max for c in cells if c.max is not None]
    total = sum(c.count for c in cells)

    return CalendarHeatmapResponse(
        tag_id=tag["id"],
        tag_name=tag["name"],
        engineering_unit=tag["engineering_unit"],
        data_type=tag["data_type"],
        decimal_places=tag["decimal_places"],
        weeks=weeks,
        timezone=tz_name,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        dates=all_dates,
        cells=cells,
        global_min=min(mins) if mins else None,
        global_max=max(maxs) if maxs else None,
        global_avg=(sum(avgs) / len(avgs)) if avgs else None,
        total_samples=total,
    )


# ── In-process TTL cache for calendar heatmap ──────────────────────────
# Same pattern as the other heatmap caches. 60s TTL matches React Query's
# refetchInterval so back-to-back polls are free.
import time as _calendar_time
_CALENDAR_CACHE: dict[tuple, tuple[float, Any]] = {}
_CALENDAR_CACHE_TTL = 60.0
_CALENDAR_CACHE_MAX = 32


def _calendar_cache_get(key: tuple):
    entry = _CALENDAR_CACHE.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if _calendar_time.monotonic() > expiry:
        _CALENDAR_CACHE.pop(key, None)
        return None
    return value


def _calendar_cache_set(key: tuple, value: Any) -> None:
    _CALENDAR_CACHE[key] = (_calendar_time.monotonic() + _CALENDAR_CACHE_TTL, value)
    if len(_CALENDAR_CACHE) > _CALENDAR_CACHE_MAX:
        oldest = min(_CALENDAR_CACHE.items(), key=lambda kv: kv[1][0])
        _CALENDAR_CACHE.pop(oldest[0], None)


# ── In-process TTL cache for /history ──────────────────────────────────
# Caches the full TrendHistoryResponse keyed on the request parameters.
# Effective for historical-mode navigation (stable start/end). Less
# effective for live-mode polling (moving start/end means cache misses
# every poll). 60s TTL — long enough to absorb back-to-back navigation,
# short enough that operators never see stale data on a static window.
import time as _history_time
_HISTORY_CACHE: dict[tuple, tuple[float, Any]] = {}
_HISTORY_CACHE_TTL = 60.0
_HISTORY_CACHE_MAX = 32


def _history_cache_get(key: tuple):
    entry = _HISTORY_CACHE.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if _history_time.monotonic() > expiry:
        _HISTORY_CACHE.pop(key, None)
        return None
    return value


def _history_cache_set(key: tuple, value: Any) -> None:
    _HISTORY_CACHE[key] = (_history_time.monotonic() + _HISTORY_CACHE_TTL, value)
    if len(_HISTORY_CACHE) > _HISTORY_CACHE_MAX:
        oldest = min(_HISTORY_CACHE.items(), key=lambda kv: kv[1][0])
        _HISTORY_CACHE.pop(oldest[0], None)
