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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


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
    if span <= timedelta(days=7):
        return "1h"            # 168 buckets/tag
    return "1d"


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

    for tag_id in ids:
        meta = meta_rows[tag_id]

        # First, the raw count in the window (for the "downsampled from N"
        # badge). Cheap because of the (tag_id, time) PK.
        raw_count = db.execute(
            text("SELECT count(*) FROM tag_values "
                 "WHERE tag_id = :tid AND time >= :s AND time < :e"),
            {"tid": tag_id, "s": start, "e": end},
        ).scalar() or 0

        # Then the points themselves. The query shape depends on whether
        # we're hitting raw or a continuous aggregate.
        if grain == "raw":
            points = _query_raw(db, tag_id, start, end, max_points)
        else:
            view = {"1m": "tag_values_1m", "1h": "tag_values_1h",
                    "1d": "tag_values_1d"}[grain]
            points = _query_aggregated(db, view, tag_id, start, end, max_points)

        series.append(TrendSeries(
            tag_id=tag_id,
            tag_name=meta["name"],
            engineering_unit=meta["engineering_unit"],
            data_type=meta["data_type"],
            min_value=meta["min_value"],
            max_value=meta["max_value"],
            aggregation=grain,
            raw_count=raw_count,
            returned_count=len(points),
            points=points,
        ))

    return TrendHistoryResponse(start=start, end=end, aggregation=grain, series=series)


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
                      max_points: int) -> list[TrendPoint]:
    """Bucket-aggregated points from a continuous aggregate.

    Each row carries the envelope (min/max) plus first/last/avg so the
    chart can draw whichever line style operators prefer without another
    query. We default to first_value for the line (most-recently-seen
    behavior), with mn/mx for the spread band.

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
    return [
        TrendPoint(
            t=r.bucket,
            v=r.last_value,           # default line value = bucket's last sample
            mn=r.min_value,
            mx=r.max_value,
            st=0,                     # aggregated rows don't have a single ST
            g=r.good_count,
            b=r.bad_count,
        )
        for r in rows
    ]


# ===========================================================================
# Endpoint 3 — GET /api/trends/summary
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
    """
    if start >= end:
        raise HTTPException(400, "start must be before end")

    ids = _parse_tag_ids(tag_ids)
    duration_sec = (end - start).total_seconds()

    sql = text("""
        WITH counts AS (
            SELECT
                t.id AS tag_id, t.name AS tag_name,
                rb.scan_interval_ms,
                COALESCE(SUM(1) FILTER (WHERE tv.st >= 128), 0) AS good_samples,
                COALESCE(SUM(1) FILTER (WHERE tv.st >= 64 AND tv.st < 128), 0) AS uncertain_samples,
                COALESCE(SUM(1) FILTER (WHERE tv.st < 64), 0) AS bad_samples,
                COALESCE(SUM(1), 0) AS actual_samples,
                MIN(tv.time) AS first_sample,
                MAX(tv.time) AS last_sample
            FROM tags t
            JOIN register_blocks rb ON rb.id = t.register_block_id
            LEFT JOIN tag_values tv
              ON tv.tag_id = t.id AND tv.time >= :s AND tv.time < :e
            WHERE t.id = ANY(:ids)
            GROUP BY t.id, t.name, rb.scan_interval_ms
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

    rows = db.execute(sql, {"ids": ids, "s": start, "e": end}).mappings().all()

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
        ))

    return TrendSummaryResponse(start=start, end=end, tags=out)


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
