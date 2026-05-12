"""Phase 8.5 — Tag writes via REST + the audit journal.

POST /api/tags/{tag_id}/write
    body: { "value": <stringified value>, "verify": true|false }
    Triggers a Modbus write to the named tag. Logs to write_journal.

GET /api/writes
    Returns the journal, newest-first, with optional filters:
      - since         — ISO datetime
      - tag_id        — filter to a single tag
      - source        — 'cli' or 'rest'
      - success_only  — exclude failures
      - limit         — default 100, max 1000

Writes are intentionally a separate API surface from /api/tags PATCH because
they trigger device I/O, not metadata changes. Auth gates differ (in a future
slice; for now anonymous is allowed but logged).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modbus.writer import write_tag

router = APIRouter(prefix="/api", tags=["writes"])


# ---------------------------------------------------------------------------
# POST /api/tags/{tag_id}/write
# ---------------------------------------------------------------------------


class TagWriteRequest(BaseModel):
    value: str = Field(..., description="Value to write, as a string. "
                                        "Parsed per the tag's data_type.")
    verify: bool = Field(True, description="Read back and verify after write.")
    user_label: str | None = Field(
        None, max_length=128,
        description="Optional label for the audit journal. If omitted, the "
                    "request remote IP is used.",
    )


class TagWriteResponse(BaseModel):
    success: bool
    error: str | None
    function_code: int | None
    latency_ms: float | None
    verify_value: str | None
    journal_id: int | None


@router.post("/tags/{tag_id}/write", response_model=TagWriteResponse)
async def write_tag_endpoint(
    tag_id: int,
    body: TagWriteRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Write a value to the named tag via Modbus.

    Resolves tag_id → tag.name (the writer module's API is name-keyed) and
    then delegates to app.modbus.writer.write_tag. Every call is logged to
    write_journal regardless of outcome.

    Returns 404 if the tag doesn't exist or is disabled, 400 if the tag
    sits on a read-only Modbus space (FC2/FC4). Other failures return 200
    with `success: false` because Modbus errors are operationally normal
    (network blip, slave busy, etc.) and the journal carries the audit.
    """
    # Resolve tag_id → name. The writer module is name-keyed (legacy decision)
    # and we keep that surface stable; the REST layer is the only place id
    # → name mapping happens.
    row = db.execute(
        text("SELECT name, enabled FROM tags WHERE id = :id"),
        {"id": tag_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"Tag {tag_id} not found")
    if not row["enabled"]:
        raise HTTPException(409, f"Tag {tag_id} is disabled — enable before writing")

    user_label = body.user_label or _request_label(request)
    result = await write_tag(
        row["name"], body.value,
        verify=body.verify,
        source="rest", user_label=user_label,
    )
    # The writer returns dict; map to response model
    return TagWriteResponse(
        success=result["success"],
        error=result["error"],
        function_code=result["function_code"],
        latency_ms=result["latency_ms"],
        verify_value=(
            str(result["verify_value"]) if result["verify_value"] is not None else None
        ),
        journal_id=result["journal_id"],
    )


def _request_label(request: Request) -> str:
    """Build a reasonable user_label from request metadata when none provided.

    Until auth lands, fall back to remote address. This is far from
    auth-grade but at least an IP shows up in the audit trail.
    """
    addr = request.client.host if request.client else "unknown"
    return f"rest@{addr}"


# ---------------------------------------------------------------------------
# GET /api/writes — audit journal
# ---------------------------------------------------------------------------


class WriteJournalEntry(BaseModel):
    id: int
    time: datetime
    tag_id: int | None
    tag_name: str
    source: str
    user_label: str | None
    function_code: int
    address: int
    requested_value: str
    success: bool
    error: str | None
    verify_value: str | None
    latency_ms: float | None
    # Phase 8.5.1 — value from latest_tag_values at write time. Lets the audit
    # answer "what was this before?" alongside "what was requested?".
    value_before: str | None


@router.get("/writes", response_model=list[WriteJournalEntry])
def list_writes(
    db: Annotated[Session, Depends(get_session)],
    since: datetime | None = Query(None,
        description="Only include writes after this timestamp (ISO)"),
    tag_id: int | None = Query(None, description="Filter to a single tag"),
    source: str | None = Query(None, regex="^(cli|rest)$",
        description="Filter by source: 'cli' or 'rest'"),
    success_only: bool = Query(False, description="Exclude failed writes"),
    device_id: int | None = Query(None, description="Filter by device id"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Audit-journal viewer. Newest-first."""
    sql = """
        SELECT j.id, j.time, j.tag_id, j.tag_name_snapshot AS tag_name,
               j.source, j.user_label, j.function_code, j.address,
               j.requested_value, j.success, j.error, j.verify_value,
               j.latency_ms, j.value_before
        FROM write_journal j
        LEFT JOIN tags t ON t.id = j.tag_id
        WHERE TRUE
    """
    params: dict[str, object] = {}
    if since is not None:
        sql += " AND j.time >= :since"
        params["since"] = since
    if tag_id is not None:
        sql += " AND j.tag_id = :tag_id"
        params["tag_id"] = tag_id
    if source is not None:
        sql += " AND j.source = :source"
        params["source"] = source
    if success_only:
        sql += " AND j.success = TRUE"
    if device_id is not None:
        # Match writes against the current tag's device — note this misses
        # writes to tags that have since been deleted (tag_id is NULL in
        # those rows). For the Phase 8.5.1 UI this is the right trade-off:
        # "show writes belonging to device X" naturally excludes orphans.
        sql += " AND t.device_id = :device_id"
        params["device_id"] = device_id
    sql += " ORDER BY j.time DESC LIMIT :limit"
    params["limit"] = limit

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]
