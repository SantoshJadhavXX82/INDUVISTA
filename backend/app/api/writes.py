"""Phase 8.5 - Tag writes via REST + the audit journal.
Phase 16.0h - audit() calls on every write operation.

POST /api/tags/{tag_id}/write
    body: { "value": <stringified value>, "verify": true|false }
    Triggers a Modbus write. Logged to BOTH write_journal (operational)
    and audit_log (compliance) - see double-logging design note below.

GET /api/writes
    Returns the write_journal, newest-first. Read-only, not audited.


# Double-logging design note (Phase 16.0h)

Every successful or failed write to a PLC produces TWO log entries:

  1. write_journal row  - the OPERATIONAL record. Captures full PLC-level
     detail: requested_value, value_before, verify_value, function_code,
     address, latency_ms, error. Used by the writer's verify-after-write
     logic, by the GET /writes audit-viewer page, and by debugging
     workflows ("did this write actually reach the PLC?").

  2. audit_log event ('tag.write') - the COMPLIANCE record. Captures
     who/when/what alongside every other audit-tracked action in the
     system (config changes, alarm acks, etc). Carries journal_id so
     drill-down to PLC-level detail is a single lookup.

The two stores are NOT transactional with each other:
  - write_journal commits inside the writer module's session
  - audit_log commits inside the audit() helper's separate session

If they ever diverge (e.g. audit DB unreachable while write succeeds),
write_journal remains authoritative for "did the PLC write happen?" and
audit_log carries journal_id for cross-reference.

The design accepts this divergence because (1) writes are infrequent
enough that the practical risk is low, (2) the audit() helper never
raises so audit failures don't break write operations, and (3) the
audit_log being a separate DB means a corrupted audit store can't
take down the operational system.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.auth.deps import CurrentUser, get_current_user
from app.modbus.writer import write_tag
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api", tags=["writes"])


# ---------------------------------------------------------------------------
# POST /api/tags/{tag_id}/write
# ---------------------------------------------------------------------------


class TagWriteRequest(BaseModel):
    value: str = Field(..., description="Value to write, as a string. Parsed per the tag's data_type.")
    verify: bool = Field(True, description="Read back and verify after write.")
    user_label: str | None = Field(
        None, max_length=128,
        description="Optional label for the audit journal. If omitted, the request remote IP is used.",
    )


class TagWriteResponse(BaseModel):
    success: bool
    error: str | None
    function_code: int | None
    latency_ms: float | None
    verify_value: str | None
    journal_id: int | None


def _request_label(request: Request) -> str:
    """Build a reasonable user_label from request metadata when none provided."""
    addr = request.client.host if request.client else "unknown"
    return f"rest@{addr}"


@router.post("/tags/{tag_id}/write", response_model=TagWriteResponse)
async def write_tag_endpoint(
    tag_id: int,
    body: TagWriteRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """Write a value to the named tag via Modbus.

    Audited as 'tag.write' with status:
      - 'denied'   if the tag doesn't exist or is disabled
      - 'success'  if the Modbus write succeeded
      - 'error'    if the Modbus write failed (network, slave busy, etc.)
                   or if the writer raised an unhandled exception.

    Both the success path and the error path carry journal_id in the audit
    details so compliance can drill into the write_journal row for full
    PLC-level detail.
    """

    # ----- Pre-flight: resolve tag and validate -----
    row = db.execute(
        text("SELECT id, name, enabled, device_id FROM tags WHERE id = :id"),
        {"id": tag_id},
    ).mappings().first()
    if not row:
        audit(AuditEvent(
            action="tag.write",
            target_type="tag",
            target_id=tag_id,
            summary=f"Denied: tag {tag_id} not found",
            status="denied",
            error_message="tag not found",
            details={
                "requested_value": body.value,
                "verify": body.verify,
                "user_label": body.user_label,
            },
        ), request)
        raise HTTPException(404, f"Tag {tag_id} not found")

    target_label = row["name"]

    if not row["enabled"]:
        audit(AuditEvent(
            action="tag.write",
            target_type="tag",
            target_id=tag_id,
            target_label=target_label,
            summary=f"Denied: tag '{target_label}' is disabled",
            status="denied",
            error_message="tag is disabled",
            details={
                "requested_value": body.value,
                "verify": body.verify,
                "user_label": body.user_label,
                "device_id": row["device_id"],
            },
        ), request)
        raise HTTPException(409, f"Tag {tag_id} is disabled - enable before writing")

    # Prefer an explicit label, then the authenticated username, then the
    # request IP. After Phase 21 the JWT identity is the real actor.
    user_label = body.user_label or user.username or _request_label(request)

    # ----- Execute the write -----
    try:
        result = await write_tag(
            row["name"], body.value,
            verify=body.verify,
            source="rest", user_label=user_label,
        )
    except Exception as e:
        audit(AuditEvent(
            action="tag.write",
            target_type="tag",
            target_id=tag_id,
            target_label=target_label,
            summary=f"Write to '{target_label}' raised exception",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={
                "requested_value": body.value,
                "verify": body.verify,
                "user_label": user_label,
                "device_id": row["device_id"],
            },
        ), request)
        raise

    # ----- Fetch value_before from write_journal for audit context -----
    # The journal row is written inside the writer module; we re-query it
    # to pick up value_before (the live PLC value captured pre-write).
    # One extra small query per write operation, acceptable given writes
    # are infrequent.
    value_before: str | None = None
    address: int | None = None
    if result.get("journal_id"):
        jrow = db.execute(
            text("""
                SELECT value_before, address
                FROM write_journal WHERE id = :id
            """),
            {"id": result["journal_id"]},
        ).mappings().first()
        if jrow:
            value_before = jrow["value_before"]
            address = jrow["address"]

    # ----- Audit success or error -----
    if result["success"]:
        verify_suffix = ""
        if result.get("verify_value") is not None:
            verify_suffix = f", verified={result['verify_value']}"

        before_suffix = ""
        if value_before is not None and str(value_before) != body.value:
            before_suffix = f" (was {value_before})"

        audit(AuditEvent(
            action="tag.write",
            target_type="tag",
            target_id=tag_id,
            target_label=target_label,
            summary=(
                f"Wrote {body.value} to '{target_label}'{before_suffix}"
                f" (FC={result.get('function_code')}, "
                f"latency={result.get('latency_ms')}ms{verify_suffix})"
            ),
            details={
                "requested_value": body.value,
                "value_before": value_before,
                "verify_value": (
                    str(result["verify_value"])
                    if result.get("verify_value") is not None else None
                ),
                "verify_requested": body.verify,
                "function_code": result.get("function_code"),
                "address": address,
                "latency_ms": result.get("latency_ms"),
                "journal_id": result.get("journal_id"),
                "user_label": user_label,
                "device_id": row["device_id"],
            },
        ), request)
    else:
        audit(AuditEvent(
            action="tag.write",
            target_type="tag",
            target_id=tag_id,
            target_label=target_label,
            summary=(
                f"Write to '{target_label}' FAILED: {result.get('error') or 'unknown error'}"
                f" (requested={body.value}"
                + (f", was={value_before}" if value_before is not None else "")
                + ")"
            ),
            status="error",
            error_message=result.get("error") or "modbus write failed",
            details={
                "requested_value": body.value,
                "value_before": value_before,
                "function_code": result.get("function_code"),
                "address": address,
                "latency_ms": result.get("latency_ms"),
                "journal_id": result.get("journal_id"),
                "user_label": user_label,
                "device_id": row["device_id"],
            },
        ), request)

    # ----- Build response -----
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


# ---------------------------------------------------------------------------
# GET /api/writes - write_journal viewer (read-only, not audited)
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
    """Audit-journal viewer. Newest-first.

    This endpoint reads write_journal (operational store). For compliance
    audit, prefer /api/audit-log?action=tag.write which carries the same
    information plus actor context, alongside every other audit-tracked
    action.
    """
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
        sql += " AND t.device_id = :device_id"
        params["device_id"] = device_id
    sql += " ORDER BY j.time DESC LIMIT :limit"
    params["limit"] = limit

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]
