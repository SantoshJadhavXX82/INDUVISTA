"""Report Manager API — chronological history of generated report files.

Phase RM.1. Backs the Report Manager screen: a date-grouped list of every
file the report engine has written (one row per `report_records` row), with
authenticated download / inline preview and a reversible soft-archive.

Data source
-----------
`report_records` already logs every rendered+delivered file:
    generated_at, report_name, category, trigger_kind, fmt, file_path,
    byte_size, status ('ok'|'error'), error, and (Phase RM.1) archived_at.
This router is read-mostly over that table; it adds NO new write path for
report generation — that stays in workers/report_scheduler.py.

Time zone
---------
Rows are stored in UTC. Grouping into calendar days uses the display
timezone (settings.app_timezone) via `AT TIME ZONE`, consistent with the
project rule that app_timezone is display-only and never coerces stored
timestamps.

File serving & safety
---------------------
`report_records.file_path` is written by the scheduler (system-controlled,
not user input); the only user input here is the numeric record_id, from
which we look the path up. Before streaming we still:
  * resolve the real path and require it to be an existing regular file
    (a row can be 'ok' while the file was later moved/deleted -> 410),
  * optionally constrain it to an allow-list of roots
    (REPORT_MANAGER_ALLOWED_ROOTS, os.pathsep-separated). If unset, the
    recorded path is served as-is (with the existence/realpath check) and
    a one-time warning is logged so operators can lock it down.
Reads are viewer-gated by RBAC; archive/unarchive are writes (engineer).
"""
from __future__ import annotations

import logging
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/report-manager", tags=["report-manager"])

# --------------------------------------------------------------------------- tz
def _app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return ZoneInfo("UTC")


# ----------------------------------------------------------------- file safety
_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".xml": "application/xml",
    ".json": "application/json",
    ".html": "text/html",
}

_allowed_roots_warned = False


def _allowed_roots() -> list[Path]:
    raw = os.getenv("REPORT_MANAGER_ALLOWED_ROOTS", "").strip()
    if not raw:
        return []
    out: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if part:
            try:
                out.append(Path(part).resolve())
            except Exception:
                continue
    return out


def _resolve_report_file(file_path: Optional[str]) -> Path:
    """Resolve a recorded file_path to a safe, existing file or raise.

    404 if no path recorded; 410 if the path is recorded but the file is no
    longer on disk; 403 if an allow-list is configured and the file is
    outside it.
    """
    global _allowed_roots_warned
    if not file_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No file recorded for this report.")

    try:
        resolved = Path(file_path).resolve()
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid file path.")

    roots = _allowed_roots()
    if roots:
        if not any(_is_within(resolved, root) for root in roots):
            log.warning("report-manager: refused path outside allow-list: %s", resolved)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "File is outside the allowed report roots.")
    elif not _allowed_roots_warned:
        _allowed_roots_warned = True
        log.warning(
            "report-manager: REPORT_MANAGER_ALLOWED_ROOTS is not set; serving "
            "recorded report paths as-is (existence-checked). Set it to lock "
            "downloads to specific folders."
        )

    if not resolved.is_file():
        raise HTTPException(status.HTTP_410_GONE, "The file is no longer available on disk.")
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _media_type(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in _MIME_BY_EXT:
        return _MIME_BY_EXT[ext]
    guessed, _ = mimetypes.guess_type(str(p))
    return guessed or "application/octet-stream"


# ------------------------------------------------------------------- humanize
_TRIGGER_LABELS = {
    "timed": "Schedule",
    "tag": "Tag trigger",
    "manual": "Manual",
    "on_demand": "On-demand",
}


def _triggered_by(trigger_kind: Optional[str]) -> str:
    if not trigger_kind:
        return "—"
    return _TRIGGER_LABELS.get(trigger_kind, trigger_kind.replace("_", " ").title())


def _status_label(record_status: Optional[str], file_exists: bool) -> str:
    if record_status == "error":
        return "FAILED"
    if not file_exists:
        return "MISSING"
    return "SUCCESS"


# ----------------------------------------------------------------- response
class FileRow(BaseModel):
    record_id: int
    generated_at: datetime          # UTC, aware
    file_name: str
    report_name: str
    report_type: Optional[str]      # report_records.category
    fmt: str                        # uppercased
    byte_size: Optional[int]
    triggered_by: str
    status: str                     # SUCCESS | FAILED | MISSING
    file_exists: bool
    archived: bool
    error: Optional[str]


class DateGroup(BaseModel):
    date: str                       # local YYYY-MM-DD
    total_files: int
    total_storage_bytes: int
    rows: list[FileRow]


class FilesResponse(BaseModel):
    timezone: str
    range: str
    total_files: int
    total_storage_bytes: int
    truncated: bool
    groups: list[DateGroup]


# --------------------------------------------------------------------- list
def _window_utc(rng: str, start: Optional[str], end: Optional[str]) -> tuple[Optional[datetime], Optional[datetime]]:
    """Return (start_utc, end_utc) bounds for the requested range, computed in
    the display timezone then converted to UTC. None means unbounded."""
    tz = _app_tz()
    now_local = datetime.now(tz)
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    if rng == "today":
        return midnight.astimezone(timezone.utc), None
    if rng == "7_days":
        return (midnight - timedelta(days=6)).astimezone(timezone.utc), None
    if rng == "30_days":
        return (midnight - timedelta(days=29)).astimezone(timezone.utc), None
    if rng == "custom":
        s = u = None
        if start:
            try:
                s = datetime.fromisoformat(start).replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                raise HTTPException(422, "Invalid 'start' date (expected YYYY-MM-DD).")
        if end:
            try:
                # inclusive end day -> next local midnight
                e_local = datetime.fromisoformat(end).replace(tzinfo=tz) + timedelta(days=1)
                u = e_local.astimezone(timezone.utc)
            except ValueError:
                raise HTTPException(422, "Invalid 'end' date (expected YYYY-MM-DD).")
        return s, u
    # default: last 30 days
    return (midnight - timedelta(days=29)).astimezone(timezone.utc), None


@router.get("/files", response_model=FilesResponse)
def list_files(
    db: Annotated[Session, Depends(get_session)],
    range: str = Query("30_days", pattern="^(today|7_days|30_days|custom)$"),
    start: Optional[str] = Query(None, description="YYYY-MM-DD (custom range)"),
    end: Optional[str] = Query(None, description="YYYY-MM-DD (custom range)"),
    search: Optional[str] = Query(None, description="matches file name + report name"),
    status_filter: str = Query("all", alias="status", pattern="^(all|success|failed)$"),
    fmt: Optional[str] = Query(None, description="filter by format e.g. pdf"),
    include_archived: bool = Query(False),
    limit: int = Query(1000, ge=1, le=5000),
) -> FilesResponse:
    """Date-grouped list of generated report files (newest first)."""
    tz = _app_tz()
    start_utc, end_utc = _window_utc(range, start, end)

    where = ["1=1"]
    params: dict[str, Any] = {"tz": settings.app_timezone, "lim": limit + 1}
    if start_utc is not None:
        where.append("generated_at >= :start_utc")
        params["start_utc"] = start_utc
    if end_utc is not None:
        where.append("generated_at < :end_utc")
        params["end_utc"] = end_utc
    if not include_archived:
        where.append("archived_at IS NULL")
    if status_filter == "success":
        where.append("status = 'ok'")
    elif status_filter == "failed":
        where.append("status = 'error'")
    if fmt:
        where.append("lower(fmt) = lower(:fmt)")
        params["fmt"] = fmt
    if search:
        where.append("(file_path ILIKE :q OR report_name ILIKE :q)")
        params["q"] = f"%{search}%"

    sql = text(f"""
        SELECT id, report_name, category, trigger_kind, fmt,
               file_path, byte_size, status, error, generated_at,
               (archived_at IS NOT NULL) AS archived,
               to_char(generated_at AT TIME ZONE :tz, 'YYYY-MM-DD') AS local_date
        FROM report_records
        WHERE {' AND '.join(where)}
        ORDER BY generated_at DESC
        LIMIT :lim
    """)
    rows = db.execute(sql, params).mappings().all()

    truncated = len(rows) > limit
    rows = rows[:limit]

    groups: list[DateGroup] = []
    index: dict[str, DateGroup] = {}
    grand_files = 0
    grand_bytes = 0
    for r in rows:
        fp = r["file_path"]
        file_name = Path(fp).name if fp else f"(report #{r['id']})"
        file_exists = bool(fp) and Path(fp).is_file()
        size = r["byte_size"] or 0
        row = FileRow(
            record_id=r["id"],
            generated_at=r["generated_at"],
            file_name=file_name,
            report_name=r["report_name"],
            report_type=r["category"],
            fmt=(r["fmt"] or "").upper(),
            byte_size=r["byte_size"],
            triggered_by=_triggered_by(r["trigger_kind"]),
            status=_status_label(r["status"], file_exists),
            file_exists=file_exists,
            archived=bool(r["archived"]),
            error=r["error"],
        )
        g = index.get(r["local_date"])
        if g is None:
            g = DateGroup(date=r["local_date"], total_files=0, total_storage_bytes=0, rows=[])
            index[r["local_date"]] = g
            groups.append(g)
        g.rows.append(row)
        g.total_files += 1
        g.total_storage_bytes += size
        grand_files += 1
        grand_bytes += size

    return FilesResponse(
        timezone=settings.app_timezone,
        range=range,
        total_files=grand_files,
        total_storage_bytes=grand_bytes,
        truncated=truncated,
        groups=groups,
    )


# --------------------------------------------------------------- download/preview
def _record(db: Session, record_id: int) -> Any:
    row = db.execute(
        text("SELECT id, file_path, fmt FROM report_records WHERE id = :id"),
        {"id": record_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report record not found.")
    return row


@router.get("/files/{record_id}/download")
def download_file(record_id: int, db: Annotated[Session, Depends(get_session)]):
    row = _record(db, record_id)
    path = _resolve_report_file(row["file_path"])
    return FileResponse(
        path=str(path),
        media_type=_media_type(path),
        filename=path.name,
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@router.get("/files/{record_id}/preview")
def preview_file(record_id: int, db: Annotated[Session, Depends(get_session)]):
    row = _record(db, record_id)
    path = _resolve_report_file(row["file_path"])
    # inline so PDFs/HTML render in the browser tab instead of downloading.
    return FileResponse(
        path=str(path),
        media_type=_media_type(path),
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


# ------------------------------------------------------------------- archive
class ArchiveResponse(BaseModel):
    record_id: int
    archived: bool


@router.post("/files/{record_id}/archive", response_model=ArchiveResponse)
def archive_file(record_id: int, db: Annotated[Session, Depends(get_session)]):
    """Soft-hide a record (does NOT delete the file or the history row)."""
    res = db.execute(
        text("UPDATE report_records SET archived_at = now() "
             "WHERE id = :id AND archived_at IS NULL RETURNING id"),
        {"id": record_id},
    ).first()
    if res is None:
        # Either it doesn't exist or it's already archived; disambiguate.
        exists = db.execute(text("SELECT 1 FROM report_records WHERE id = :id"),
                            {"id": record_id}).first()
        db.commit()
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Report record not found.")
        return ArchiveResponse(record_id=record_id, archived=True)
    db.commit()
    return ArchiveResponse(record_id=record_id, archived=True)


@router.post("/files/{record_id}/unarchive", response_model=ArchiveResponse)
def unarchive_file(record_id: int, db: Annotated[Session, Depends(get_session)]):
    res = db.execute(
        text("UPDATE report_records SET archived_at = NULL "
             "WHERE id = :id RETURNING id"),
        {"id": record_id},
    ).first()
    db.commit()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report record not found.")
    return ArchiveResponse(record_id=record_id, archived=False)
