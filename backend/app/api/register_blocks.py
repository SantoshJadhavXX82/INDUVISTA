"""CRUD endpoints for register_blocks.
Phase 16.0h - audit() calls on every mutating endpoint.

Audited actions:
  register_block.create
  register_block.update            (multi-field PATCH or non-toggle single-field)
  register_block.toggle            (single-field PATCH on `enabled`)
  register_block.set_writable      (single-field PATCH on `writable` - security gold)
  register_block.delete
  register_block.bulk_create       (one event per batch, with per-row failures in details)

The writable flag is security-relevant: flipping it from false->true is what
allows writes through writes.py to reach the PLC. Discriminating it as its
own audit action makes "who enabled writability on which block?" a 1-click
filter for compliance review.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import handle_integrity_error, sql_col
from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api", tags=["register-blocks"])


# ---------------------------------------------------------------------------
# Schemas (unchanged)
# ---------------------------------------------------------------------------


class RegisterBlockCreate(BaseModel):
    device_id: int
    name: str = Field(..., min_length=1, max_length=100)
    function_code: int = Field(..., ge=1, le=4)
    start_address: int = Field(..., ge=0, le=65535)
    count: int = Field(..., ge=1, le=125)
    scan_interval_ms: int = Field(1000, ge=10)
    phase_ms: int = Field(0, ge=0)
    writable: bool = Field(False)
    addressing_mode: str = Field(
        "STANDARD",
        pattern="^(STANDARD|ENRON_HOLDING|ENRON_INPUT)$",
    )


class RegisterBlockUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    count: int | None = Field(None, ge=1, le=125)
    scan_interval_ms: int | None = Field(None, ge=10)
    phase_ms: int | None = Field(None, ge=0)
    enabled: bool | None = None
    writable: bool | None = None
    addressing_mode: str | None = Field(
        None, pattern="^(STANDARD|ENRON_HOLDING|ENRON_INPUT)$",
    )


class RegisterBlockResponse(BaseModel):
    id: int
    device_id: int
    device_name: str
    name: str
    function_code: int
    start_address: int
    count: int
    scan_interval_ms: int | None
    phase_ms: int | None
    enabled: bool
    writable: bool
    addressing_mode: str


_BLOCK_SELECT = """
    SELECT b.id, b.device_id, d.name AS device_name, b.name,
           b.function_code, b.start_address, b.count,
           b.scan_interval_ms, b.phase_ms, b.enabled, b.writable,
           b.addressing_mode
    FROM register_blocks b
    JOIN devices d ON d.id = b.device_id
"""


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _classify_update(updates: dict) -> str:
    """Pick the most specific audit action for a PATCH body."""
    if len(updates) == 1:
        if "enabled" in updates:
            return "register_block.toggle"
        if "writable" in updates:
            return "register_block.set_writable"
    return "register_block.update"


def _summarize_rb(row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "device_id": row.get("device_id"),
        "device_name": row.get("device_name"),
        "function_code": row.get("function_code"),
        "start_address": row.get("start_address"),
        "count": row.get("count"),
        "enabled": row.get("enabled"),
        "writable": row.get("writable"),
        "addressing_mode": row.get("addressing_mode"),
    }


def _full_rb(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "device_id": row.get("device_id"),
        "device_name": row.get("device_name"),
        "function_code": row.get("function_code"),
        "start_address": row.get("start_address"),
        "count": row.get("count"),
        "scan_interval_ms": row.get("scan_interval_ms"),
        "phase_ms": row.get("phase_ms"),
        "enabled": row.get("enabled"),
        "writable": row.get("writable"),
        "addressing_mode": row.get("addressing_mode"),
    }


# ---------------------------------------------------------------------------
# List + get (read-only)
# ---------------------------------------------------------------------------


@router.get("/register-blocks", response_model=list[RegisterBlockResponse])
def list_register_blocks(
    db: Annotated[Session, Depends(get_session)],
    device_id: Annotated[int | None, Query(description="Filter by device id")] = None,
):
    sql = _BLOCK_SELECT
    params: dict = {}
    if device_id is not None:
        sql += " WHERE b.device_id = :device_id"
        params["device_id"] = device_id
    sql += " ORDER BY b.device_id, b.function_code, b.start_address"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get(
    "/devices/{device_id}/register-blocks",
    response_model=list[RegisterBlockResponse],
    tags=["devices"],
)
def list_device_blocks(
    device_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    return list_register_blocks(db, device_id)


@router.get("/register-blocks/{block_id}", response_model=RegisterBlockResponse)
def get_register_block(block_id: int, db: Annotated[Session, Depends(get_session)]):
    row = db.execute(
        text(_BLOCK_SELECT + " WHERE b.id = :id"),
        {"id": block_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"register_block {block_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Create / update / delete (audited)
# ---------------------------------------------------------------------------


@router.post("/register-blocks", response_model=RegisterBlockResponse, status_code=201)
def create_register_block(
    body: RegisterBlockCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.name

    try:
        new_id = db.execute(
            text("""
                INSERT INTO register_blocks (
                    device_id, name, function_code, start_address, count,
                    scan_interval_ms, phase_ms, writable, addressing_mode
                )
                VALUES (
                    :device_id, :name, :function_code, :start_address, :count,
                    :scan_interval_ms, :phase_ms, :writable, :addressing_mode
                )
                RETURNING id
            """),
            body.model_dump(),
        ).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action="register_block.create",
                target_type="register_block",
                target_label=target_label,
                summary=f"Denied: register_block '{body.name}' already exists on device {body.device_id}",
                status="denied",
                error_message="duplicate (device_id, name) or overlapping range",
                details={"request": body.model_dump()},
            ), request)
        elif "foreign key" in msg:
            audit(AuditEvent(
                action="register_block.create",
                target_type="register_block",
                target_label=target_label,
                summary=f"Denied: FK violation (likely device_id {body.device_id} doesn't exist)",
                status="denied",
                error_message=f"FK violation: {e.orig}",
                details={"request": body.model_dump()},
            ), request)
        elif "check" in msg:
            audit(AuditEvent(
                action="register_block.create",
                target_type="register_block",
                target_label=target_label,
                summary=f"Denied: CHECK constraint violated (likely ENRON_* with wrong FC, or writable on read-only FC)",
                status="denied",
                error_message=str(e.orig),
                details={"request": body.model_dump()},
            ), request)
        else:
            audit(AuditEvent(
                action="register_block.create",
                target_type="register_block",
                target_label=target_label,
                summary="INSERT failed (IntegrityError)",
                status="error",
                error_message=str(e.orig),
                details={"request": body.model_dump()},
            ), request)
        try:
            handle_integrity_error(e, "register_block")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    audit(AuditEvent(
        action="register_block.create",
        target_type="register_block",
        target_id=new_id,
        target_label=target_label,
        summary=(
            f"Created register_block '{body.name}' on device {body.device_id} "
            f"(FC={body.function_code}, addr={body.start_address}..{body.start_address + body.count - 1}, "
            f"writable={body.writable}, mode={body.addressing_mode})"
        ),
        details=body.model_dump(),
    ), request)

    return get_register_block(new_id, db)


@router.patch("/register-blocks/{block_id}", response_model=RegisterBlockResponse)
def update_register_block(
    block_id: int,
    body: RegisterBlockUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    action = _classify_update(updates)

    existing = db.execute(
        text(_BLOCK_SELECT + " WHERE b.id = :id"),
        {"id": block_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="register_block",
            target_id=block_id,
            summary=f"Denied: register_block {block_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"register_block {block_id} not found")

    target_label = existing["name"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="register_block",
            target_id=block_id,
            target_label=target_label,
            summary="Denied: no fields to update",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "no fields to update")

    set_clauses = ", ".join(f"{sql_col(k)} = :{k}" for k in updates)
    params = {**updates, "id": block_id}

    try:
        db.execute(
            text(f"UPDATE register_blocks SET {set_clauses} WHERE id = :id"),
            params,
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action=action,
                target_type="register_block",
                target_id=block_id,
                target_label=target_label,
                summary="Denied: update caused unique-constraint violation",
                status="denied",
                error_message="duplicate value",
                details={"request": updates, "before": _summarize_rb(existing)},
            ), request)
        elif "check" in msg:
            audit(AuditEvent(
                action=action,
                target_type="register_block",
                target_id=block_id,
                target_label=target_label,
                summary="Denied: CHECK constraint violated by update",
                status="denied",
                error_message=str(e.orig),
                details={"request": updates, "before": _summarize_rb(existing)},
            ), request)
        else:
            audit(AuditEvent(
                action=action,
                target_type="register_block",
                target_id=block_id,
                target_label=target_label,
                summary="UPDATE failed (IntegrityError)",
                status="error",
                error_message=str(e.orig),
                details={"request": updates, "before": _summarize_rb(existing)},
            ), request)
        try:
            handle_integrity_error(e, "register_block")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action=action,
            target_type="register_block",
            target_id=block_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": updates, "before": _summarize_rb(existing)},
        ), request)
        raise

    # Per-action summary.
    if action == "register_block.toggle":
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} register_block '{existing['name']}' (device={existing['device_name']})"
    elif action == "register_block.set_writable":
        verb = "ENABLED" if updates["writable"] else "DISABLED"
        summary = (
            f"{verb} writability on register_block '{existing['name']}' "
            f"(device={existing['device_name']}, FC={existing['function_code']}, "
            f"addr={existing['start_address']})"
        )
    else:
        summary = f"Updated register_block '{existing['name']}' ({', '.join(updates.keys())})"

    audit(AuditEvent(
        action=action,
        target_type="register_block",
        target_id=block_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_rb(existing),
        },
    ), request)

    return get_register_block(block_id, db)


@router.delete("/register-blocks/{block_id}", status_code=204)
def delete_register_block(
    block_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text(_BLOCK_SELECT + " WHERE b.id = :id"),
        {"id": block_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="register_block.delete",
            target_type="register_block",
            target_id=block_id,
            summary=f"Denied: register_block {block_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"register_block {block_id} not found")

    target_label = existing["name"]

    tag_count = db.execute(
        text("SELECT COUNT(*) FROM tags WHERE register_block_id = :id"),
        {"id": block_id},
    ).scalar() or 0

    try:
        db.execute(
            text("DELETE FROM register_blocks WHERE id = :id"),
            {"id": block_id},
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        audit(AuditEvent(
            action="register_block.delete",
            target_type="register_block",
            target_id=block_id,
            target_label=target_label,
            summary=f"Denied: '{existing['name']}' has {tag_count} tag(s) referencing it",
            status="denied",
            error_message=f"FK violation: tag_count={tag_count}",
            details={"before": _full_rb(existing), "tag_count": tag_count},
        ), request)
        raise HTTPException(
            409,
            f"register_block {block_id} cannot be deleted because tags reference it",
        )
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="register_block.delete",
            target_type="register_block",
            target_id=block_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_rb(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="register_block.delete",
        target_type="register_block",
        target_id=block_id,
        target_label=target_label,
        summary=(
            f"Deleted register_block '{existing['name']}' "
            f"(device={existing['device_name']}, FC={existing['function_code']}, "
            f"addr={existing['start_address']}..{existing['start_address'] + existing['count'] - 1})"
        ),
        details={"before": _full_rb(existing)},
    ), request)


# ---------------------------------------------------------------------------
# Bulk create - one audit event per batch (NOT per row)
# ---------------------------------------------------------------------------

class BulkBlockResult(BaseModel):
    row: int
    block_id: int | None = None
    name: str | None = None
    error: str | None = None


class BulkBlocksRequest(BaseModel):
    blocks: list[RegisterBlockCreate]


@router.post("/register-blocks/bulk", response_model=list[BulkBlockResult])
def bulk_create_register_blocks(
    body: BulkBlocksRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Create many register blocks in one request. Per-row error reporting.

    One audit event covers the entire batch: per-row events would flood the
    log when an operator imports a 200-row CSV. The batch audit details
    carry the list of created block_ids and the failure list so compliance
    can drill into specifics without an explosion of records.
    """
    submitted = len(body.blocks)
    results: list[BulkBlockResult] = []

    for idx, blk_in in enumerate(body.blocks):
        try:
            with db.begin_nested():
                result = db.execute(
                    text("""
                        INSERT INTO register_blocks (
                            device_id, name, function_code,
                            start_address, count,
                            scan_interval_ms, phase_ms, writable,
                            addressing_mode
                        ) VALUES (
                            :device_id, :name, :function_code,
                            :start_address, :count,
                            :scan_interval_ms, :phase_ms, :writable,
                            :addressing_mode
                        )
                        RETURNING id
                    """),
                    blk_in.model_dump(),
                )
                new_id = result.scalar_one()
            results.append(BulkBlockResult(
                row=idx, block_id=new_id, name=blk_in.name,
            ))
        except IntegrityError as e:
            msg = str(e.orig).split("\n")[0] if hasattr(e, "orig") else str(e)
            results.append(BulkBlockResult(
                row=idx, name=blk_in.name, error=msg,
            ))
        except Exception as e:
            results.append(BulkBlockResult(
                row=idx, name=blk_in.name, error=str(e),
            ))
    db.commit()

    created_ids = [r.block_id for r in results if r.block_id is not None]
    failures = [
        {"row": r.row, "name": r.name, "error": r.error}
        for r in results if r.error is not None
    ]

    audit(AuditEvent(
        action="register_block.bulk_create",
        target_type="register_block",
        target_id=created_ids[0] if created_ids else None,
        target_label=f"batch of {submitted}",
        summary=(
            f"Bulk register_block create: {submitted} submitted, "
            f"{len(created_ids)} succeeded, {len(failures)} failed"
        ),
        status="success" if not failures else ("denied" if not created_ids else "success"),
        error_message=(
            f"all {submitted} rows failed" if not created_ids and failures else None
        ),
        details={
            "submitted": submitted,
            "succeeded": len(created_ids),
            "failed": len(failures),
            "created_block_ids": created_ids,
            "failures": failures,
            # Sample of first 10 requests for context without flooding details.
            "request_sample": [blk.model_dump() for blk in body.blocks[:10]],
            "request_sample_truncated": submitted > 10,
        },
    ), request)

    return results
