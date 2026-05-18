"""Phase 14.12 - alarm rule bulk export rendering.

Standalone module - no FastAPI/HTTP imports. The API layer wraps these
functions; smoke tests can call them directly.

Column shape is identical to the import template (we import the same
TEMPLATE_COLUMNS constant). This means an export file can be edited
and re-imported through the Phase 14.11 endpoint without column drift.

Resolution: alarm_rules.tag_id -> tags.name via JOIN, so the export
is human-readable. The matching import lookup is by tag_name.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.orm import Session

# Reuse the import template's column order so round-trip works.
from app.services.alarm_rule_import import TEMPLATE_COLUMNS


def query_all_rules(db: Session) -> list[dict[str, Any]]:
    """Return every alarm_rule joined with its tag name.

    Sorted by tag_name then rule_type so the exported file is easy
    to scan and diff against versioned config.
    """
    rows = db.execute(text("""
        SELECT
            t.name           AS tag_name,
            r.rule_type      AS rule_type,
            r.severity       AS severity,
            r.threshold      AS threshold,
            r.deadband       AS deadband,
            r.on_delay_sec   AS on_delay_sec,
            r.off_delay_sec  AS off_delay_sec,
            r.latched        AS latched,
            r.window_seconds AS window_seconds,
            r.message_template AS message_template,
            r.enabled        AS enabled
        FROM alarm_rules r
        JOIN tags t ON t.id = r.tag_id
        ORDER BY t.name, r.rule_type
    """)).mappings().all()
    return [dict(r) for r in rows]


def _format_cell_csv(value: Any) -> str:
    """Render a value for CSV output. NULL -> empty string, bool -> 'true'/'false',
    numerics as-is. Matches what the import parser tolerates."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_export_csv(rows: list[dict[str, Any]]) -> bytes:
    """Render rules as CSV bytes."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TEMPLATE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: _format_cell_csv(row.get(col)) for col in TEMPLATE_COLUMNS})
    return buf.getvalue().encode("utf-8")


def render_export_xlsx(rows: list[dict[str, Any]]) -> bytes:
    """Render rules as XLSX bytes. Booleans become native True/False
    cells (not strings) which is what openpyxl produces for Python bools
    and what users expect when opening in Excel. The import parser
    handles both string and native bool via _as_bool()."""
    wb = Workbook()
    ws = wb.active
    ws.title = "alarm_rules"
    ws.append(TEMPLATE_COLUMNS)
    for row in rows:
        out_row = []
        for col in TEMPLATE_COLUMNS:
            v = row.get(col)
            # Leave bools as native bool for XLSX (round-trip-safe via _as_bool).
            # Leave numerics as native. None -> empty string.
            if v is None:
                out_row.append("")
            else:
                out_row.append(v)
        ws.append(out_row)

    # Freeze header row for usability when the sheet is large.
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
