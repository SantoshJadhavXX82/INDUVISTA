"""Single source of truth for reading the CURRENT value/quality of tags.

Current values live in `latest_tag_values` — one indexed row per tag (PK on
tag_id) — so reading them is a point lookup. The large `tag_values` hypertable
is for HISTORY only and must NEVER be scanned merely to obtain a current value:
doing so is what made the calc evaluator fall ~140s behind its 1s cadence under
write load (Phase 2c.2) and is the read anti-pattern called out in the
performance audit. Every current-value reader goes through this helper so the
pattern cannot drift back into individual call sites.

History/aggregation over a time window is a different concern and still queries
`tag_values` (bounded by tag_id + time) or a continuous aggregate.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text


def latest_values_by_tag(
    db,
    tag_ids: Iterable[int] | None = None,
    max_age_sec: float | None = None,
) -> dict[int, Mapping[str, Any]]:
    """Return ``{tag_id: row}`` of current values from ``latest_tag_values``.

    Each row exposes ``value_double``, ``value_text``, ``st``, ``time`` and
    ``source``; callers map to whatever shape they need.

    - ``tag_ids=None`` → every tag (the table is one row per tag).
    - ``tag_ids=[]``   → empty result (no query).
    - ``max_age_sec`` set → tags whose latest sample is older than this are
      omitted, i.e. treated as having no current value.
    """
    sql = (
        "SELECT tag_id, value_double, value_text, st, time, source "
        "FROM latest_tag_values"
    )
    params: dict[str, Any] = {}
    conds: list[str] = []

    if tag_ids is not None:
        ids = list(tag_ids)
        if not ids:
            return {}
        conds.append("tag_id = ANY(:ids)")
        params["ids"] = ids

    if max_age_sec is not None:
        conds.append("time >= NOW() - make_interval(secs => :max_age)")
        params["max_age"] = max_age_sec

    if conds:
        sql += " WHERE " + " AND ".join(conds)

    rows = db.execute(text(sql), params).mappings().all()
    return {r["tag_id"]: r for r in rows}
