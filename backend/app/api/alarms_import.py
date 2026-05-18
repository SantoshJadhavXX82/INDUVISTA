"""Phase 14.11 - bulk import endpoints for alarm rules.

Endpoints:
  POST /api/alarms/rules/import      multipart file upload
  GET  /api/alarms/rules/import/template?format=csv|xlsx

POST query parameters:
  dry_run (default true)  - parse + validate only, no DB writes
                            Always returns full per-row outcome.
  strict  (default true)  - on dry_run=false, refuse to commit any row
                            unless ALL rows are status='ok'. When false,
                            commits only the OK rows and reports errors
                            for the rest (still atomic per OK row).

Response shape: ImportSummary - total counts + per-row detail.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, UploadFile,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.alarm_rule_import import (
    ImportSummary, commit_rows, parse_csv, parse_xlsx,
    render_template_csv, render_template_xlsx, validate_rows,
)


log = logging.getLogger("alarms_import")

router = APIRouter(prefix="/api/alarms/rules/import", tags=["alarms-import"])


# 5 MB cap on uploads. Real-world templates are far smaller; this is
# protection against a misconfigured client streaming a huge file.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.get("/template")
def download_template(format: str = Query("csv", pattern="^(csv|xlsx)$")):
    """Returns a CSV or XLSX template with the column headers and two
    example rows so operators see the expected shape."""
    if format == "csv":
        return Response(
            content=render_template_csv(),
            media_type="text/csv",
            headers={
                "Content-Disposition":
                    'attachment; filename="alarm_rules_template.csv"',
            },
        )
    else:
        return Response(
            content=render_template_xlsx(),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition":
                    'attachment; filename="alarm_rules_template.xlsx"',
            },
        )


@router.post("")
async def bulk_import(
    db: Annotated[Session, Depends(get_session)],
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    strict: bool = Query(True),
):
    """Parse, validate, optionally commit a CSV or XLSX of alarm rules.

    File type detection is by extension (case-insensitive). Unknown
    extensions trigger a 400.
    """
    if not file.filename:
        raise HTTPException(400, "no filename provided")

    fname_lower = file.filename.lower()
    if fname_lower.endswith(".csv"):
        kind = "csv"
    elif fname_lower.endswith(".xlsx"):
        kind = "xlsx"
    else:
        raise HTTPException(
            400,
            f"unsupported file type: {file.filename!r}. "
            f"Supported: .csv, .xlsx"
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"upload too large ({len(content)} bytes; max {MAX_UPLOAD_BYTES})"
        )
    if not content:
        raise HTTPException(400, "uploaded file is empty")

    # ---- Parse ----
    try:
        if kind == "csv":
            raw_rows = parse_csv(content)
        else:
            raw_rows = parse_xlsx(content)
    except Exception as e:
        log.exception("Parse failure for %s", file.filename)
        raise HTTPException(400, f"failed to parse {kind.upper()}: {e}")

    if not raw_rows:
        raise HTTPException(
            400,
            "file contains no data rows (only headers, or empty)"
        )

    # ---- Validate ----
    summary: ImportSummary = validate_rows(db, raw_rows)

    # ---- Commit if not dry-run ----
    if not dry_run:
        if strict and summary.error_count > 0:
            # Strict mode: refuse to commit if any row has errors.
            # Return the dry-run-style response so the operator sees
            # exactly what they would have to fix.
            summary.dry_run = False
            summary.committed = False
            return summary.to_dict()

        if summary.ok_count > 0:
            try:
                commit_rows(db, summary)
                db.commit()
                summary.committed = True
            except Exception as e:
                db.rollback()
                log.exception("Commit failed during bulk import")
                # Attach a synthetic error to the summary so the UI
                # surfaces the DB-level failure, then re-raise so the
                # response is a 500 (commit promised but didn't deliver).
                raise HTTPException(
                    500,
                    f"all {summary.ok_count} OK rows validated but commit "
                    f"failed: {e}. Database state unchanged (rolled back)."
                )

    summary.dry_run = dry_run
    return summary.to_dict()
