"""Live view endpoints — current tag values for the dashboard.

GET /api/live          — all enabled tags with their current value + status
                          + groups[] (names of all groups they belong to)
GET /api/live/groups   — distinct group names (filter dropdown options)

Groups are a proper many-to-many: a tag can belong to multiple groups via
the tag_group_memberships table. The /live response aggregates each tag's
group names into an array via a correlated subquery + array_agg.

Refresh cadence on the frontend is 2 seconds. With ~160 tags this is
~17 KB/s of traffic and a couple of indexed queries per refresh.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["live"])


class LiveTag(BaseModel):
    tag_id: int
    tag_name: str
    description: str | None
    engineering_unit: str | None
    groups: list[str]
    data_type: str
    device_id: int
    device_name: str
    register_block_id: int | None
    register_block_name: str | None
    # Addressing / decoding (added in slice 3 so Tag Explorer can edit without
    # a second per-row fetch). The frontend's filters and edits both read these.
    function_code: int
    address: int
    register_count: int
    byte_order: str
    scale: float
    offset: float
    min_value: float | None
    max_value: float | None
    enabled: bool
    # All four nullable: a writable tag with no register_block has no
    # latest_tag_values row, so these come back as NULL via the LEFT JOIN.
    value_double: float | None
    value_text: str | None
    st: int | None
    st_reason: str | None
    time: datetime | None
    age_seconds: float | None


_LIVE_SELECT = """
    SELECT
        t.id              AS tag_id,
        t.name            AS tag_name,
        t.description,
        t.engineering_unit,
        t.data_type,
        t.device_id,
        d.name            AS device_name,
        t.register_block_id,
        rb.name           AS register_block_name,
        t.function_code,
        t.address,
        t.register_count,
        t.byte_order,
        t.scale,
        t."offset"        AS "offset",
        t.min_value,
        t.max_value,
        t.enabled,
        lv.value_double,
        lv.value_text,
        lv.st,
        lv.st_reason,
        lv.time,
        CASE
            WHEN lv.time IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (NOW() - lv.time))::float
        END AS age_seconds,
        COALESCE(
            (
                SELECT array_agg(g.name ORDER BY g.display_order, g.name)
                FROM tag_group_memberships m
                JOIN groups g ON g.id = m.group_id
                WHERE m.tag_id = t.id AND g.enabled = true
            ),
            ARRAY[]::text[]
        ) AS groups
    FROM tags t
    JOIN devices d ON d.id = t.device_id
    LEFT JOIN register_blocks rb ON rb.id = t.register_block_id
    LEFT JOIN latest_tag_values lv ON lv.tag_id = t.id
    WHERE t.enabled = true
"""


@router.get("/live", response_model=list[LiveTag])
def list_live_tags(
    db: Annotated[Session, Depends(get_session)],
    device_id: Annotated[int | None, Query(description="Filter by device id")] = None,
    group: Annotated[str | None, Query(description="Filter by group name (tag is in this group)")] = None,
):
    """Return all enabled tags with their current value, ST, age, and groups.

    Filter by ?device_id=N or ?group=Name. The group filter uses an
    EXISTS subquery against tag_group_memberships so a tag in multiple
    groups still appears when filtering by any of them.

    Sort order: tags with values first (by device, name), then tags
    without values at the end.
    """
    sql = _LIVE_SELECT
    params: dict = {}
    if device_id is not None:
        sql += " AND t.device_id = :device_id"
        params["device_id"] = device_id
    if group is not None:
        sql += """ AND EXISTS (
            SELECT 1 FROM tag_group_memberships m
            JOIN groups g ON g.id = m.group_id
            WHERE m.tag_id = t.id AND g.name = :group AND g.enabled = true
        )"""
        params["group"] = group
    sql += """
        ORDER BY
            CASE WHEN lv.time IS NULL THEN 1 ELSE 0 END,
            t.device_id, t.name
    """
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/live/groups", response_model=list[str])
def list_live_groups(db: Annotated[Session, Depends(get_session)]):
    """Distinct names of enabled groups that have at least one enabled tag."""
    rows = db.execute(text("""
        SELECT DISTINCT g.name
        FROM groups g
        JOIN tag_group_memberships m ON m.group_id = g.id
        JOIN tags t ON t.id = m.tag_id
        WHERE g.enabled = true AND t.enabled = true
        ORDER BY g.name
    """)).scalars().all()
    return list(rows)


class SparklinePoint(BaseModel):
    time: datetime
    value: float


class TagSparkline(BaseModel):
    tag_id: int
    points: list[SparklinePoint]


@router.get("/live/sparklines", response_model=list[TagSparkline])
def list_sparklines(
    db: Annotated[Session, Depends(get_session)],
    window_seconds: Annotated[int, Query(ge=60, le=3600)] = 300,
    bucket_seconds: Annotated[int, Query(ge=5, le=300)] = 10,
):
    """Return per-tag downsampled recent history for sparklines.

    Uses Timescale's time_bucket() to aggregate raw samples into fixed-width
    intervals (default 10s buckets over a 5-minute window → 30 points per
    tag). Skips tags with no numeric data — the dashboard's sparkline
    component renders nothing for those.

    Refresh cadence is on the client side; recommend every 10 seconds (much
    slower than /api/live's 2-second cadence). This query is heavier — one
    pass over the tag_values hypertable plus aggregation — but still cheap
    because Timescale chunks recent data in memory.
    """
    rows = db.execute(text("""
        SELECT
            tag_id,
            time_bucket(make_interval(secs => :bucket_seconds), time) AS bucket_time,
            avg(value_double) AS value
        FROM tag_values
        WHERE time >= NOW() - make_interval(secs => :window_seconds)
          AND value_double IS NOT NULL
        GROUP BY tag_id, bucket_time
        ORDER BY tag_id, bucket_time
    """), {
        "bucket_seconds": bucket_seconds,
        "window_seconds": window_seconds,
    }).mappings().all()

    # Group by tag_id into TagSparkline structures
    by_tag: dict[int, list[SparklinePoint]] = {}
    for r in rows:
        by_tag.setdefault(r["tag_id"], []).append(
            SparklinePoint(time=r["bucket_time"], value=float(r["value"])),
        )
    return [TagSparkline(tag_id=tid, points=pts) for tid, pts in by_tag.items()]
