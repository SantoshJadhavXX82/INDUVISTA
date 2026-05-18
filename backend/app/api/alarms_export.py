"""Phase 14.12 - bulk export endpoint for alarm rules.

  GET /api/alarms/rules/export/csv
  GET /api/alarms/rules/export/xlsx

The format is in the URL path rather than a query param so the path
has 2+ segments after `/api/alarms/rules/` and therefore cannot ever
collide with the existing alarms router's `GET /api/alarms/rules/{rule_id}`
parametric route (which only matches single-segment paths).

Returns the full set of configured alarm rules in a downloadable file
with the same column shape as the import template (Phase 14.11), so
operators can export -> edit -> re-import without column drift.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.alarm_rule_export import (
    query_all_rules, render_export_csv, render_export_xlsx,
)


log = logging.getLogger("alarms_export")

router = APIRouter(prefix="/api/alarms/rules/export", tags=["alarms-export"])


@router.get("/csv")
def export_rules_csv(
    db: Annotated[Session, Depends(get_session)],
):
    """Download all configured alarm rules as CSV."""
    rows = query_all_rules(db)
    log.info("alarm rule CSV export: %d rows", len(rows))
    return Response(
        content=render_export_csv(rows),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                'attachment; filename="alarm_rules_export.csv"',
        },
    )


@router.get("/xlsx")
def export_rules_xlsx(
    db: Annotated[Session, Depends(get_session)],
):
    """Download all configured alarm rules as XLSX."""
    rows = query_all_rules(db)
    log.info("alarm rule XLSX export: %d rows", len(rows))
    return Response(
        content=render_export_xlsx(rows),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                'attachment; filename="alarm_rules_export.xlsx"',
        },
    )
