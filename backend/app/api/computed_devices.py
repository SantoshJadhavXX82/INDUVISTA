"""Phase 17.0a - CRUD endpoints for Computed Devices.

A Computed Device is a row in the devices table with protocol='computed',
always living on the internal 'COMPUTED' channel (created by Migration
0042). The channel is not user-selectable - the backend auto-resolves it
on every create, and PATCH does not accept channel_id changes.

Phase 16.0h compliance: every mutating endpoint emits an audit event.

Audited actions:
  computed_device.create
  computed_device.update
  computed_device.toggle  (single-field PATCH on `enabled`)
  computed_device.delete  (cascades to tags + computed_tags rows underneath)

Endpoints:
  GET    /api/computed-devices                     list all
  GET    /api/computed-devices/{id}                one
  POST   /api/computed-devices                     create
  PATCH  /api/computed-devices/{id}                update / toggle
  DELETE /api/computed-devices/{id}                delete (cascades)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api/computed-devices", tags=["computed-devices"])


# Name of the internal channel created by Migration 0042. All computed
# devices live here. Lookup happens on each create so we don't need to
# cache it; misses are reported with an actionable error message.
INTERNAL_CHANNEL_NAME = "COMPUTED"


def _resolve_internal_channel_id(db: Session) -> int:
    """Return the id of the COMPUTED channel. Raises 500 if missing."""
    channel_id = db.execute(
        text("SELECT id FROM channels WHERE name = :n"),
        {"n": INTERNAL_CHANNEL_NAME},
    ).scalar()
    if channel_id is None:
        raise HTTPException(
            500,
            f"Internal channel '{INTERNAL_CHANNEL_NAME}' not found. "
            f"Run Migration 0042_computed_internal_channel.",
        )
    return channel_id


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ComputedDeviceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    enabled: bool = True
    scan_interval_ms: int = Field(
        1000, ge=10,
        description="Evaluator default scan interval. Per-tag execution_rate_ms overrides this.",
    )


class ComputedDeviceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    scan_interval_ms: int | None = Field(None, ge=10)
    enabled: bool | None = None


class ComputedDeviceResponse(BaseModel):
    id: int
    channel_id: int
    channel_name: str
    name: str
    description: str | None
    protocol: str  # always 'computed'
    enabled: bool
    scan_interval_ms: int
    computed_tag_count: int
    created_at: datetime
    updated_at: datetime


_SELECT = """
    SELECT d.id, d.channel_id, c.name AS channel_name,
           d.name, d.description, d.protocol, d.enabled, d.scan_interval_ms,
           d.created_at, d.updated_at,
           COALESCE((
               SELECT COUNT(*) FROM tags t
               JOIN computed_tags ct ON ct.id = t.id
               WHERE t.device_id = d.id
           ), 0) AS computed_tag_count
    FROM devices d
    JOIN channels c ON c.id = d.channel_id
    WHERE d.protocol = 'computed'
"""


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _summarize_cdev(row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "channel_id": row.get("channel_id"),
        "channel_name": row.get("channel_name"),
        "scan_interval_ms": row.get("scan_interval_ms"),
        "enabled": row.get("enabled"),
    }


def _full_cdev(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "channel_id": row.get("channel_id"),
        "channel_name": row.get("channel_name"),
        "description": row.get("description"),
        "protocol": row.get("protocol"),
        "enabled": row.get("enabled"),
        "scan_interval_ms": row.get("scan_interval_ms"),
        "computed_tag_count": row.get("computed_tag_count"),
    }


# ---------------------------------------------------------------------------
# List + get (read-only)
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ComputedDeviceResponse])
def list_computed_devices(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text(_SELECT + " ORDER BY d.name")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{device_id}", response_model=ComputedDeviceResponse)
def get_computed_device(
    device_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text(_SELECT + " AND d.id = :id"),
        {"id": device_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"computed device {device_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Create / update / delete (audited)
# ---------------------------------------------------------------------------


@router.post("", response_model=ComputedDeviceResponse, status_code=201)
def create_computed_device(
    body: ComputedDeviceCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.name

    # Always use the internal COMPUTED channel. User cannot pick.
    channel_id = _resolve_internal_channel_id(db)

    try:
        new_id = db.execute(
            text("""
                INSERT INTO devices (
                    channel_id, name, description, protocol,
                    host, port, unit_id,
                    scan_interval_ms, enabled
                )
                VALUES (
                    :channel_id, :name, :description, 'computed',
                    NULL, NULL, NULL,
                    :scan_interval_ms, :enabled
                )
                RETURNING id
            """),
            {
                "channel_id": channel_id,
                "name": body.name,
                "description": body.description,
                "scan_interval_ms": body.scan_interval_ms,
                "enabled": body.enabled,
            },
        ).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action="computed_device.create",
                target_type="computed_device",
                target_label=target_label,
                summary=f"Denied: computed device '{body.name}' already exists",
                status="denied",
                error_message="duplicate name",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(409, f"a device named '{body.name}' already exists")
        audit(AuditEvent(
            action="computed_device.create",
            target_type="computed_device",
            target_label=target_label,
            summary="INSERT failed (IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    audit(AuditEvent(
        action="computed_device.create",
        target_type="computed_device",
        target_id=new_id,
        target_label=target_label,
        summary=f"Created computed device '{body.name}'",
        details={**body.model_dump(), "protocol": "computed", "channel_id": channel_id},
    ), request)

    return get_computed_device(device_id=new_id, db=db)


@router.patch("/{device_id}", response_model=ComputedDeviceResponse)
def update_computed_device(
    device_id: int,
    body: ComputedDeviceUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "computed_device.toggle" if is_toggle else "computed_device.update"

    existing = db.execute(
        text(_SELECT + " AND d.id = :id"),
        {"id": device_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="computed_device",
            target_id=device_id,
            summary=f"Denied: computed device {device_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"computed device {device_id} not found")

    target_label = existing["name"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="computed_device",
            target_id=device_id,
            target_label=target_label,
            summary="Denied: no fields to update",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "no fields to update")

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    params = {**updates, "id": device_id}

    try:
        db.execute(
            text(f"UPDATE devices SET {set_clauses} WHERE id = :id"),
            params,
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action=action,
                target_type="computed_device",
                target_id=device_id,
                target_label=target_label,
                summary="Denied: name collision",
                status="denied",
                error_message="duplicate name",
                details={"request": updates, "before": _summarize_cdev(existing)},
            ), request)
            raise HTTPException(409, "a device with this name already exists")
        audit(AuditEvent(
            action=action,
            target_type="computed_device",
            target_id=device_id,
            target_label=target_label,
            summary="UPDATE failed (IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": updates, "before": _summarize_cdev(existing)},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action=action,
            target_type="computed_device",
            target_id=device_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": updates, "before": _summarize_cdev(existing)},
        ), request)
        raise

    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} computed device '{existing['name']}'"
    else:
        summary = f"Updated computed device '{existing['name']}' ({', '.join(updates.keys())})"

    audit(AuditEvent(
        action=action,
        target_type="computed_device",
        target_id=device_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_cdev(existing),
        },
    ), request)

    return get_computed_device(device_id, db)


@router.delete("/{device_id}", status_code=204)
def delete_computed_device(
    device_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text(_SELECT + " AND d.id = :id"),
        {"id": device_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="computed_device.delete",
            target_type="computed_device",
            target_id=device_id,
            summary=f"Denied: computed device {device_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"computed device {device_id} not found")

    target_label = existing["name"]
    tag_count = db.execute(
        text("SELECT COUNT(*) FROM tags WHERE device_id = :id"),
        {"id": device_id},
    ).scalar() or 0

    try:
        db.execute(
            text("DELETE FROM devices WHERE id = :id"),
            {"id": device_id},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="computed_device.delete",
            target_type="computed_device",
            target_id=device_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_cdev(existing), "tag_count": tag_count},
        ), request)
        raise

    audit(AuditEvent(
        action="computed_device.delete",
        target_type="computed_device",
        target_id=device_id,
        target_label=target_label,
        summary=(
            f"Deleted computed device '{existing['name']}' "
            f"(cascaded {tag_count} tag(s) + their computed_tags rows)"
        ),
        details={
            "before": _full_cdev(existing),
            "cascaded_tag_count": tag_count,
        },
    ), request)
