"""Global default report style (Theme Manager).

A single system-wide default for report theme / time basis / page setup, stored
as one JSON row in the existing ``system_settings`` key-value table. Reports
inherit this default; a per-report ``report_style`` block overrides it key by
key (see report_blocks._merge_style). Kept in a standalone service so both the
API (reports_config) and the scheduler worker can read it without importing
each other.
"""
from __future__ import annotations

import json

from sqlalchemy import text

DEFAULT_STYLE_KEY = "report.default_style"


def get_default_style(db) -> dict:
    """Return the global default style dict ({theme,time,page}) or {} if unset."""
    row = db.execute(
        text("SELECT value FROM system_settings WHERE key = :k"),
        {"k": DEFAULT_STYLE_KEY},
    ).first()
    if not row or not row[0]:
        return {}
    try:
        v = json.loads(row[0])
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def set_default_style(db, payload: dict) -> dict:
    """Upsert the global default style. Caller controls authorization."""
    db.execute(
        text(
            """
            INSERT INTO system_settings (key, value, updated_at)
            VALUES (:k, :v, NOW())
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
            """
        ),
        {"k": DEFAULT_STYLE_KEY, "v": json.dumps(payload)},
    )
    db.commit()
    return payload
