"""CRUD endpoints for devices.
Phase 16.0h - audit() calls on every mutating endpoint.

Audited actions:
  device.create / device.update / device.toggle / device.delete
  device.pair (captures pair_tags_created from auto helper)
  device.unpair (captures pair_tags_deleted from auto helper)
  device.swap_duty (captures both sides' from->to + reason)
  device.set_pair_override (captures enable state + partner)

Diagnostic endpoints (test_read, scan_range) are NOT audited - they're
read-only commissioning helpers that open transient TCP connections but
don't change state.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import handle_integrity_error, sql_col
from app.db import get_session
from app.utils.audit import audit, AuditEvent

router = APIRouter(prefix="/api", tags=["devices"])


DutyRole = Literal["duty", "standby", "none"]


# ===========================================================================
# Schemas (unchanged from original)
# ===========================================================================


class DeviceCreate(BaseModel):
    channel_id: int
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    protocol: str = "modbus_tcp"
    host: str
    port: int = Field(502, ge=1, le=65535)
    unit_id: int = Field(1, ge=0, le=255)
    duty_role: DutyRole = "none"
    stale_after_sec: int = Field(30, ge=1)
    scan_interval_ms: int = Field(1000, ge=10)
    secondary_host: str | None = None
    secondary_port: int | None = Field(None, ge=1, le=65535)
    secondary_unit_id: int | None = Field(None, ge=0, le=255)
    redundant_device_id: int | None = None
    duty_status_tag_id: int | None = None
    manual_override: bool = False
    enabled: bool = True
    request_timeout_ms: int = Field(3000, ge=100, le=60000)
    retry_count: int = Field(1, ge=0, le=10)
    reconnect_initial_ms: int = Field(1000, ge=100, le=60000)
    reconnect_max_ms: int = Field(30000, ge=100, le=300000)


class DeviceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    protocol: str | None = None
    host: str | None = None
    port: int | None = Field(None, ge=1, le=65535)
    unit_id: int | None = Field(None, ge=0, le=255)
    duty_role: DutyRole | None = None
    stale_after_sec: int | None = Field(None, ge=1)
    scan_interval_ms: int | None = Field(None, ge=10)
    secondary_host: str | None = None
    secondary_port: int | None = Field(None, ge=1, le=65535)
    secondary_unit_id: int | None = Field(None, ge=0, le=255)
    redundant_device_id: int | None = None
    duty_status_tag_id: int | None = None
    manual_override: bool | None = None
    enabled: bool | None = None
    request_timeout_ms: int | None = Field(None, ge=100, le=60000)
    retry_count: int | None = Field(None, ge=0, le=10)
    reconnect_initial_ms: int | None = Field(None, ge=100, le=60000)
    reconnect_max_ms: int | None = Field(None, ge=100, le=300000)


class DeviceResponse(BaseModel):
    id: int
    name: str
    channel_id: int
    channel_name: str
    description: str | None
    protocol: str
    host: str | None
    port: int | None
    unit_id: int | None
    duty_role: str | None
    stale_after_sec: int
    scan_interval_ms: int
    secondary_host: str | None
    secondary_port: int | None
    secondary_unit_id: int | None
    redundant_device_id: int | None
    duty_status_tag_id: int | None
    manual_override: bool
    enabled: bool
    request_timeout_ms: int
    retry_count: int
    reconnect_initial_ms: int
    reconnect_max_ms: int


_DEVICE_SELECT = """
    SELECT d.id, d.name, d.channel_id, c.name AS channel_name,
           d.description, d.protocol, d.host, d.port, d.unit_id,
           d.duty_role, d.stale_after_sec, d.scan_interval_ms,
           d.secondary_host, d.secondary_port, d.secondary_unit_id,
           d.redundant_device_id, d.duty_status_tag_id, d.manual_override, d.enabled,
           d.request_timeout_ms, d.retry_count,
           d.reconnect_initial_ms, d.reconnect_max_ms
    FROM devices d
    JOIN channels c ON c.id = d.channel_id
"""


# ===========================================================================
# Audit helpers
# ===========================================================================


def _summarize_dev(row) -> dict[str, Any]:
    """Compact before-state used on update/toggle audits."""
    return {
        "name": row["name"],
        "channel_id": row.get("channel_id"),
        "channel_name": row.get("channel_name"),
        "host": row.get("host"),
        "port": row.get("port"),
        "unit_id": row.get("unit_id"),
        "protocol": row.get("protocol"),
        "duty_role": row.get("duty_role"),
        "redundant_device_id": row.get("redundant_device_id"),
        "manual_override": row.get("manual_override"),
        "enabled": row.get("enabled"),
    }


def _full_dev(row) -> dict[str, Any]:
    """Full before-state used on delete audits."""
    return {
        "id": row["id"],
        "name": row["name"],
        "channel_id": row.get("channel_id"),
        "channel_name": row.get("channel_name"),
        "description": row.get("description"),
        "protocol": row.get("protocol"),
        "host": row.get("host"),
        "port": row.get("port"),
        "unit_id": row.get("unit_id"),
        "duty_role": row.get("duty_role"),
        "stale_after_sec": row.get("stale_after_sec"),
        "scan_interval_ms": row.get("scan_interval_ms"),
        "secondary_host": row.get("secondary_host"),
        "secondary_port": row.get("secondary_port"),
        "secondary_unit_id": row.get("secondary_unit_id"),
        "redundant_device_id": row.get("redundant_device_id"),
        "duty_status_tag_id": row.get("duty_status_tag_id"),
        "manual_override": row.get("manual_override"),
        "enabled": row.get("enabled"),
        "request_timeout_ms": row.get("request_timeout_ms"),
        "retry_count": row.get("retry_count"),
        "reconnect_initial_ms": row.get("reconnect_initial_ms"),
        "reconnect_max_ms": row.get("reconnect_max_ms"),
    }


# ===========================================================================
# List + get (read-only, not audited)
# ===========================================================================


@router.get("/devices", response_model=list[DeviceResponse])
def list_devices(
    db: Annotated[Session, Depends(get_session)],
    channel_id: Annotated[int | None, Query(description="Filter by channel id")] = None,
):
    # Stage 6 (Phase OPC-web.2.2.b): hide soft-deleted devices from LIST.
    # Admin paths (GET/PATCH/DELETE /devices/{id}) bypass this filter.
    sql = _DEVICE_SELECT + " WHERE d.deleted_at IS NULL"
    params: dict = {}
    if channel_id is not None:
        sql += " AND d.channel_id = :channel_id"
        params["channel_id"] = channel_id
    sql += " ORDER BY d.id"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/devices/{device_id}", response_model=DeviceResponse)
def get_device(device_id: int, db: Annotated[Session, Depends(get_session)]):
    row = db.execute(
        text(_DEVICE_SELECT + " WHERE d.id = :id"),
        {"id": device_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"device {device_id} not found")
    return dict(row)


# ===========================================================================
# Create / update / delete (audited)
# ===========================================================================


@router.post("/devices", response_model=DeviceResponse, status_code=201)
def create_device(
    body: DeviceCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.name

    try:
        new_id = db.execute(
            text("""
                INSERT INTO devices (
                    channel_id, name, description, protocol,
                    host, port, unit_id,
                    duty_role, stale_after_sec, scan_interval_ms,
                    secondary_host, secondary_port, secondary_unit_id,
                    redundant_device_id, duty_status_tag_id,
                    manual_override, enabled,
                    request_timeout_ms, retry_count,
                    reconnect_initial_ms, reconnect_max_ms
                )
                VALUES (
                    :channel_id, :name, :description, :protocol,
                    :host, :port, :unit_id,
                    :duty_role, :stale_after_sec, :scan_interval_ms,
                    :secondary_host, :secondary_port, :secondary_unit_id,
                    :redundant_device_id, :duty_status_tag_id,
                    :manual_override, :enabled,
                    :request_timeout_ms, :retry_count,
                    :reconnect_initial_ms, :reconnect_max_ms
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
                action="device.create",
                target_type="device",
                target_label=target_label,
                summary=f"Denied: device '{body.name}' already exists on channel {body.channel_id}",
                status="denied",
                error_message="duplicate (channel_id, name)",
                details={"request": body.model_dump()},
            ), request)
        elif "foreign key" in msg:
            audit(AuditEvent(
                action="device.create",
                target_type="device",
                target_label=target_label,
                summary=f"Denied: FK violation (likely channel_id {body.channel_id} doesn't exist)",
                status="denied",
                error_message=f"FK violation: {e.orig}",
                details={"request": body.model_dump()},
            ), request)
        else:
            audit(AuditEvent(
                action="device.create",
                target_type="device",
                target_label=target_label,
                summary="INSERT failed (IntegrityError)",
                status="error",
                error_message=str(e.orig),
                details={"request": body.model_dump()},
            ), request)
        try:
            handle_integrity_error(e, "device")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    audit(AuditEvent(
        action="device.create",
        target_type="device",
        target_id=new_id,
        target_label=target_label,
        summary=f"Created device '{body.name}' (host={body.host}:{body.port}, protocol={body.protocol}, channel={body.channel_id})",
        details=body.model_dump(),
    ), request)

    return get_device(new_id, db)


@router.patch("/devices/{device_id}", response_model=DeviceResponse)
def update_device(
    device_id: int,
    body: DeviceUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "device.toggle" if is_toggle else "device.update"

    # Pre-fetch for before-snapshot + 404 audit.
    existing = db.execute(
        text(_DEVICE_SELECT + " WHERE d.id = :id"),
        {"id": device_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="device",
            target_id=device_id,
            summary=f"Denied: device {device_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"device {device_id} not found")

    target_label = existing["name"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="Denied: no fields to update",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "no fields to update")

    set_clauses = ", ".join(f"{sql_col(k)} = :{k}" for k in updates)
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
                target_type="device",
                target_id=device_id,
                target_label=target_label,
                summary="Denied: update caused unique-constraint violation",
                status="denied",
                error_message="duplicate value",
                details={"request": updates, "before": _summarize_dev(existing)},
            ), request)
        else:
            audit(AuditEvent(
                action=action,
                target_type="device",
                target_id=device_id,
                target_label=target_label,
                summary="UPDATE failed (IntegrityError)",
                status="error",
                error_message=str(e.orig),
                details={"request": updates, "before": _summarize_dev(existing)},
            ), request)
        try:
            handle_integrity_error(e, "device")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action=action,
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": updates, "before": _summarize_dev(existing)},
        ), request)
        raise

    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} device '{existing['name']}' (channel={existing['channel_name']})"
    else:
        summary = f"Updated device '{existing['name']}' ({', '.join(updates.keys())})"

    audit(AuditEvent(
        action=action,
        target_type="device",
        target_id=device_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_dev(existing),
        },
    ), request)

    return get_device(device_id, db)


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(
    device_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text(_DEVICE_SELECT + " WHERE d.id = :id"),
        {"id": device_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="device.delete",
            target_type="device",
            target_id=device_id,
            summary=f"Denied: device {device_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"device {device_id} not found")

    target_label = existing["name"]

    # Count blockers for friendlier audit detail. tags, register_blocks,
    # and the redundant_device pair all hold FKs into devices.
    blockers = db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM register_blocks WHERE device_id = :id) AS register_blocks,
            (SELECT COUNT(*) FROM tags WHERE device_id = :id) AS tags,
            (SELECT COUNT(*) FROM devices WHERE redundant_device_id = :id) AS pair_partners
    """), {"id": device_id}).mappings().first()

    try:
        db.execute(
            text("DELETE FROM devices WHERE id = :id"),
            {"id": device_id},
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        audit(AuditEvent(
            action="device.delete",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary=(
                f"Denied: '{existing['name']}' has dependents "
                f"(register_blocks={blockers['register_blocks']}, "
                f"tags={blockers['tags']}, "
                f"pair_partners={blockers['pair_partners']})"
            ),
            status="denied",
            error_message="FK violation from dependent rows",
            details={"before": _full_dev(existing), "blockers": dict(blockers)},
        ), request)
        raise HTTPException(
            409,
            f"device {device_id} cannot be deleted because other rows reference it "
            "(register_blocks, tags, or the redundant_device pair)",
        )
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="device.delete",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_dev(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="device.delete",
        target_type="device",
        target_id=device_id,
        target_label=target_label,
        summary=f"Deleted device '{existing['name']}' (host={existing.get('host')}:{existing.get('port')}, channel={existing.get('channel_name')})",
        details={"before": _full_dev(existing)},
    ), request)


# ===========================================================================
# Phase 12 - duty/standby pairing (configuration + manual swap)
# ===========================================================================


class PairRequest(BaseModel):
    partner_device_id: int
    this_role: Literal["duty", "standby"] = "duty"


@router.post("/devices/{device_id}/pair", response_model=DeviceResponse)
def pair_devices(
    device_id: int,
    body: PairRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a duty/standby pair between two devices in one transaction.

    Audit captures:
      - this_role + partner role
      - any old partners that got broken up as a side-effect
      - the auto_create_pair_tags() return value (number of pair_tags made)
    """
    if device_id == body.partner_device_id:
        audit(AuditEvent(
            action="device.pair",
            target_type="device",
            target_id=device_id,
            summary="Denied: self-pair attempt",
            status="denied",
            error_message="device cannot be its own partner",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, "a device cannot be its own partner")

    # Verify both devices exist (FOR UPDATE locks the rows).
    rows = db.execute(
        text("SELECT id, name, duty_role, redundant_device_id FROM devices "
             "WHERE id IN (:a, :b) FOR UPDATE"),
        {"a": device_id, "b": body.partner_device_id},
    ).mappings().all()
    if len(rows) != 2:
        audit(AuditEvent(
            action="device.pair",
            target_type="device",
            target_id=device_id,
            summary=f"Denied: one or both devices not found (requested {device_id} + {body.partner_device_id})",
            status="denied",
            error_message="device(s) not found",
            details={"request": body.model_dump(), "found_ids": [r["id"] for r in rows]},
        ), request)
        raise HTTPException(404, "one or both devices not found")

    me = next(r for r in rows if r["id"] == device_id)
    partner = next(r for r in rows if r["id"] == body.partner_device_id)
    target_label = me["name"]
    partner_role: Literal["duty", "standby"] = (
        "standby" if body.this_role == "duty" else "duty"
    )

    old_partner_ids = {
        me["redundant_device_id"],
        partner["redundant_device_id"],
    } - {None, device_id, body.partner_device_id}

    if old_partner_ids:
        db.execute(
            text("UPDATE devices SET duty_role='none', redundant_device_id=NULL "
                 "WHERE id = ANY(:ids)"),
            {"ids": list(old_partner_ids)},
        )

    try:
        db.execute(
            text("UPDATE devices SET duty_role=:r, redundant_device_id=:p WHERE id=:id"),
            {"r": body.this_role, "p": body.partner_device_id, "id": device_id},
        )
        db.execute(
            text("UPDATE devices SET duty_role=:r, redundant_device_id=:p WHERE id=:id"),
            {"r": partner_role, "p": device_id, "id": body.partner_device_id},
        )
        db.execute(
            text("""INSERT INTO device_duty_history
                    (device_id, paired_device_id, switched_at, reason, notes)
                    VALUES (:d, :p, NOW(), 'startup', :note)"""),
            {
                "d": device_id if body.this_role == "duty" else body.partner_device_id,
                "p": body.partner_device_id if body.this_role == "duty" else device_id,
                "note": f"paired via API: {me['name']} ({body.this_role}) <-> {partner['name']} ({partner_role})",
            },
        )
        from app.api.pair_tags import auto_create_pair_tags
        pair_tags_created = auto_create_pair_tags(db, device_id, body.partner_device_id)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        audit(AuditEvent(
            action="device.pair",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="pair failed (IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={
                "request": body.model_dump(),
                "this_before": dict(me),
                "partner_before": dict(partner),
            },
        ), request)
        try:
            handle_integrity_error(e, "device")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="device.pair",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="pair failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={
                "request": body.model_dump(),
                "this_before": dict(me),
                "partner_before": dict(partner),
            },
        ), request)
        raise

    audit(AuditEvent(
        action="device.pair",
        target_type="device",
        target_id=device_id,
        target_label=target_label,
        summary=(
            f"Paired '{me['name']}' ({body.this_role}) with '{partner['name']}' "
            f"({partner_role}); created {pair_tags_created} pair_tag(s)"
            + (f"; broke up old partner(s): {sorted(old_partner_ids)}" if old_partner_ids else "")
        ),
        details={
            "this_device_id": device_id,
            "this_device_name": me["name"],
            "this_role": body.this_role,
            "partner_device_id": body.partner_device_id,
            "partner_device_name": partner["name"],
            "partner_role": partner_role,
            "pair_tags_created": pair_tags_created,
            "old_partners_broken_up": sorted(old_partner_ids) if old_partner_ids else [],
            "this_before": dict(me),
            "partner_before": dict(partner),
        },
    ), request)

    return get_device(device_id, db)


@router.post("/devices/{device_id}/unpair", response_model=DeviceResponse)
def unpair_device(
    device_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Break the pair this device is part of. Captures partner name and
    pair_tags_deleted count before the destruction so the audit is
    self-contained."""

    # Pre-fetch with partner name for audit.
    existing = db.execute(text("""
        SELECT d.id, d.name, d.duty_role, d.redundant_device_id,
               p.name AS partner_name, p.duty_role AS partner_duty_role
        FROM devices d
        LEFT JOIN devices p ON p.id = d.redundant_device_id
        WHERE d.id = :id
    """), {"id": device_id}).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="device.unpair",
            target_type="device",
            target_id=device_id,
            summary=f"Denied: device {device_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"device {device_id} not found")

    target_label = existing["name"]
    partner_id = existing["redundant_device_id"]
    partner_name = existing["partner_name"]

    if partner_id is None:
        # Not actually paired - record but don't reject (current code
        # accepts this silently; preserve behavior, just audit it).
        audit(AuditEvent(
            action="device.unpair",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary=f"Unpaired '{existing['name']}' (was not paired - no-op)",
            details={
                "before": dict(existing),
                "pair_tags_deleted": 0,
                "noop_reason": "device was not paired",
            },
        ), request)
        # Still execute the UPDATE harmlessly to preserve the original
        # behavior (it's a no-op).
        try:
            db.execute(
                text("UPDATE devices SET duty_role='none', redundant_device_id=NULL "
                     "WHERE id IN (:a, :b)"),
                {"a": device_id, "b": device_id},
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return get_device(device_id, db)

    try:
        from app.api.pair_tags import auto_delete_pair_tags
        pair_tags_deleted = auto_delete_pair_tags(db, device_id, partner_id)

        db.execute(
            text("UPDATE devices SET duty_role='none', redundant_device_id=NULL "
                 "WHERE id IN (:a, :b)"),
            {"a": device_id, "b": partner_id},
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        audit(AuditEvent(
            action="device.unpair",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="unpair failed (IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"before": dict(existing)},
        ), request)
        try:
            handle_integrity_error(e, "device")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="device.unpair",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="unpair failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": dict(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="device.unpair",
        target_type="device",
        target_id=device_id,
        target_label=target_label,
        summary=(
            f"Unpaired '{existing['name']}' from '{partner_name}'; "
            f"removed {pair_tags_deleted} pair_tag(s)"
        ),
        details={
            "this_device_id": device_id,
            "this_device_name": existing["name"],
            "this_old_role": existing["duty_role"],
            "partner_device_id": partner_id,
            "partner_device_name": partner_name,
            "partner_old_role": existing["partner_duty_role"],
            "pair_tags_deleted": pair_tags_deleted,
        },
    ), request)

    return get_device(device_id, db)


class SwapDutyRequest(BaseModel):
    reason: Literal["manual", "primary_failed", "partner_channel_failover",
                    "scheduled", "failback", "startup"] = "manual"
    notes: str | None = None


@router.post("/devices/{device_id}/swap-duty", response_model=DeviceResponse)
def swap_duty(
    device_id: int,
    body: SwapDutyRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Atomically swap duty/standby roles between this device and its
    partner. Audit captures both sides' from->to roles plus the reason."""

    existing = db.execute(text("""
        SELECT d.id, d.name, d.duty_role, d.redundant_device_id,
               p.name AS partner_name, p.duty_role AS partner_duty_role
        FROM devices d
        LEFT JOIN devices p ON p.id = d.redundant_device_id
        WHERE d.id = :id
    """), {"id": device_id}).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="device.swap_duty",
            target_type="device",
            target_id=device_id,
            summary=f"Denied: device {device_id} not found",
            status="denied",
            error_message="not found",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(404, f"device {device_id} not found")

    target_label = existing["name"]

    if existing["redundant_device_id"] is None or existing["duty_role"] == "none":
        audit(AuditEvent(
            action="device.swap_duty",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary=f"Denied: '{existing['name']}' is not part of a duty/standby pair",
            status="denied",
            error_message="not paired",
            details={"request": body.model_dump(), "before": dict(existing)},
        ), request)
        raise HTTPException(400, "device is not part of a duty/standby pair")

    partner_id = existing["redundant_device_id"]
    partner_name = existing["partner_name"]
    my_old_role = existing["duty_role"]
    partner_old_role = existing["partner_duty_role"]
    my_new_role = "standby" if my_old_role == "duty" else "duty"
    partner_new_role = "duty" if my_old_role == "duty" else "standby"

    try:
        db.execute(
            text("UPDATE devices SET duty_role=:r WHERE id=:id"),
            {"r": my_new_role, "id": device_id},
        )
        db.execute(
            text("UPDATE devices SET duty_role=:r WHERE id=:id"),
            {"r": partner_new_role, "id": partner_id},
        )
        became_duty = partner_id if my_new_role == "standby" else device_id
        became_standby = device_id if my_new_role == "standby" else partner_id
        db.execute(
            text("""INSERT INTO device_duty_history
                    (device_id, paired_device_id, switched_at, reason, notes)
                    VALUES (:d, :p, NOW(), :reason, :notes)"""),
            {
                "d": became_duty,
                "p": became_standby,
                "reason": body.reason,
                "notes": body.notes,
            },
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        audit(AuditEvent(
            action="device.swap_duty",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="swap_duty failed (IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": body.model_dump(), "before": dict(existing)},
        ), request)
        try:
            handle_integrity_error(e, "device")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="device.swap_duty",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="swap_duty failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump(), "before": dict(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="device.swap_duty",
        target_type="device",
        target_id=device_id,
        target_label=target_label,
        summary=(
            f"Duty swap (reason={body.reason}): "
            f"'{existing['name']}' {my_old_role}->{my_new_role}, "
            f"'{partner_name}' {partner_old_role}->{partner_new_role}"
        ),
        details={
            "reason": body.reason,
            "notes": body.notes,
            "this_device_id": device_id,
            "this_device_name": existing["name"],
            "this_old_role": my_old_role,
            "this_new_role": my_new_role,
            "partner_device_id": partner_id,
            "partner_device_name": partner_name,
            "partner_old_role": partner_old_role,
            "partner_new_role": partner_new_role,
            "became_duty_device_id": became_duty,
            "became_standby_device_id": became_standby,
        },
    ), request)

    return get_device(device_id, db)


class DutyHistoryRow(BaseModel):
    id: int
    device_id: int
    device_name: str
    paired_device_id: int
    paired_device_name: str
    switched_at: str
    reason: str
    notes: str | None


@router.get("/devices/{device_id}/duty-history", response_model=list[DutyHistoryRow])
def get_duty_history(
    device_id: int,
    db: Annotated[Session, Depends(get_session)],
    limit: int = Query(50, ge=1, le=500),
):
    rows = db.execute(
        text("""
            SELECT h.id, h.device_id, d.name AS device_name,
                   h.paired_device_id, p.name AS paired_device_name,
                   h.switched_at, h.reason, h.notes
            FROM device_duty_history h
            JOIN devices d ON d.id = h.device_id
            JOIN devices p ON p.id = h.paired_device_id
            WHERE h.device_id = :id OR h.paired_device_id = :id
            ORDER BY h.switched_at DESC
            LIMIT :limit
        """),
        {"id": device_id, "limit": limit},
    ).mappings().all()
    return [
        {**dict(r), "switched_at": r["switched_at"].isoformat()}
        for r in rows
    ]


# Phase 12.5 - manual override for duty/standby pairs.

class SetPairOverrideRequest(BaseModel):
    enable: bool


@router.post("/devices/{device_id}/set-pair-override", response_model=DeviceResponse)
def set_pair_override(
    device_id: int,
    body: SetPairOverrideRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Toggle manual_override on both sides of a duty/standby pair.
    Audited as device.set_pair_override."""

    existing = db.execute(text("""
        SELECT d.id, d.name, d.redundant_device_id, d.manual_override,
               p.name AS partner_name, p.manual_override AS partner_override
        FROM devices d
        LEFT JOIN devices p ON p.id = d.redundant_device_id
        WHERE d.id = :id
    """), {"id": device_id}).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="device.set_pair_override",
            target_type="device",
            target_id=device_id,
            summary=f"Denied: device {device_id} not found",
            status="denied",
            error_message="not found",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(404, "device not found")

    target_label = existing["name"]

    if existing["redundant_device_id"] is None:
        audit(AuditEvent(
            action="device.set_pair_override",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary=f"Denied: '{existing['name']}' is not paired",
            status="denied",
            error_message="not paired",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, "device is not paired - manual override only applies to pairs")

    ids = [device_id, existing["redundant_device_id"]]

    try:
        db.execute(
            text("UPDATE devices SET manual_override = :v WHERE id = ANY(:ids)"),
            {"v": body.enable, "ids": ids},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="device.set_pair_override",
            target_type="device",
            target_id=device_id,
            target_label=target_label,
            summary="set_pair_override failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump()},
        ), request)
        raise

    state = "Enabled" if body.enable else "Disabled"
    audit(AuditEvent(
        action="device.set_pair_override",
        target_type="device",
        target_id=device_id,
        target_label=target_label,
        summary=(
            f"{state} manual override on pair "
            f"'{existing['name']}' + '{existing['partner_name']}'"
        ),
        details={
            "enable": body.enable,
            "this_device_id": device_id,
            "this_device_name": existing["name"],
            "this_old_override": existing["manual_override"],
            "partner_device_id": existing["redundant_device_id"],
            "partner_device_name": existing["partner_name"],
            "partner_old_override": existing["partner_override"],
        },
    ), request)

    updated = db.execute(text(_DEVICE_SELECT + " WHERE d.id = :id"),
                         {"id": device_id}).mappings().first()
    return dict(updated)


# ===========================================================================
# Modbus diagnostics - test_read + scan_range
# NOT AUDITED: read-only commissioning helpers, no state mutation.
# Everything below is preserved verbatim from the original source.
# ===========================================================================

import struct as _struct
from pymodbus.client import ModbusTcpClient as _ModbusTcpClient


class TestReadRequest(BaseModel):
    function_code: int = Field(..., ge=1, le=4)
    address: int = Field(..., ge=0, le=65535)
    register_count: int = Field(2, ge=1, le=4)


class TestReadResponse(BaseModel):
    raw_bytes_hex: str
    register_count: int
    function_code: int
    decoded: dict


def _decode_matrix(raw_bytes: bytes) -> dict:
    n = len(raw_bytes)
    result: dict = {}

    if n >= 2:
        b2 = raw_bytes[0:2]
        result["int16"] = {
            "AB (big-endian)": str(int.from_bytes(b2, "big", signed=True)),
            "BA (little-endian)": str(int.from_bytes(b2, "little", signed=True)),
        }
        result["uint16"] = {
            "AB (big-endian)": str(int.from_bytes(b2, "big", signed=False)),
            "BA (little-endian)": str(int.from_bytes(b2, "little", signed=False)),
        }

    if n >= 4:
        b = raw_bytes[0:4]
        orderings = {
            "ABCD (big-endian)":            b,
            "CDAB (word-swap)":             b[2:4] + b[0:2],
            "BADC (byte-swap)":             bytes((b[1], b[0], b[3], b[2])),
            "DCBA (little-endian)":         b[::-1],
        }
        result["int32"] = {k: str(_struct.unpack(">i", v)[0]) for k, v in orderings.items()}
        result["uint32"] = {k: str(_struct.unpack(">I", v)[0]) for k, v in orderings.items()}
        result["float32"] = {
            k: _format_float(_struct.unpack(">f", v)[0]) for k, v in orderings.items()
        }

    if n >= 8:
        b = raw_bytes[0:8]
        orderings_64 = {
            "ABCD...HG (big-endian)": b,
            "DCBA...FE (little-endian)": b[::-1],
        }
        result["int64"] = {
            k: str(_struct.unpack(">q", v)[0]) for k, v in orderings_64.items()
        }
        result["float64"] = {
            k: _format_float(_struct.unpack(">d", v)[0]) for k, v in orderings_64.items()
        }
    return result


def _format_float(f: float) -> str:
    import math
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    abs_f = abs(f)
    if abs_f == 0:
        return "0"
    if abs_f < 1e-6 or abs_f > 1e9:
        return f"{f:.3e}"
    if abs_f >= 1000:
        return f"{f:.2f}"
    if abs_f >= 1:
        return f"{f:.4f}"
    return f"{f:.6f}"


@router.post("/devices/{device_id}/test-read", response_model=TestReadResponse)
def test_read(
    device_id: int,
    body: TestReadRequest,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text("SELECT id, host, port, unit_id FROM devices WHERE id = :id"),
        {"id": device_id},
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "device not found")

    client = _ModbusTcpClient(row["host"], port=row["port"], timeout=3)
    try:
        if not client.connect():
            raise HTTPException(502, f"could not connect to {row['host']}:{row['port']}")

        unit_id = row["unit_id"]
        fc = body.function_code

        try:
            if fc == 1:
                resp = client.read_coils(body.address, count=body.register_count, slave=unit_id)
            elif fc == 2:
                resp = client.read_discrete_inputs(body.address, count=body.register_count, slave=unit_id)
            elif fc == 3:
                resp = client.read_holding_registers(body.address, count=body.register_count, slave=unit_id)
            else:
                resp = client.read_input_registers(body.address, count=body.register_count, slave=unit_id)
        except Exception as e:
            raise HTTPException(502, f"read failed: {e}")

        if resp is None or resp.isError():
            raise HTTPException(502, f"modbus error: {resp}")

        if fc in (1, 2):
            bits = list(resp.bits)[: body.register_count]
            decoded = {"bool": {"-": " ".join("1" if b else "0" for b in bits)}}
            raw_hex = " ".join(f"{int(b):02x}" for b in bits)
        else:
            registers = list(resp.registers)
            raw_bytes = b"".join(reg.to_bytes(2, "big") for reg in registers)
            raw_hex = " ".join(f"{b:02x}" for b in raw_bytes)
            decoded = _decode_matrix(raw_bytes)

        return TestReadResponse(
            raw_bytes_hex=raw_hex,
            register_count=body.register_count,
            function_code=fc,
            decoded=decoded,
        )
    finally:
        client.close()


# Scan range (Phase 7 C4 Register Browser).
class ScanRangeRequest(BaseModel):
    function_code: int = Field(..., ge=1, le=4)
    start_address: int = Field(..., ge=0, le=65535)
    end_address: int = Field(..., ge=0, le=65535)
    addressing_mode: str | None = Field(None)
    value_width_bytes: int | None = Field(None, ge=2, le=8)


class ScanRow(BaseModel):
    address: int
    hex: str
    value: int
    decoded_float32_abcd: float | None = None
    decoded_float32_dcba: float | None = None
    decoded_int32: int | None = None
    decoded_uint32: int | None = None
    decoded_float64_abcd: float | None = None


class ScanRangeResponse(BaseModel):
    device_id: int
    function_code: int
    start_address: int
    end_address: int
    elapsed_ms: float
    chunks: int
    rows: list[ScanRow]


_MAX_CHUNK_BY_FC = {1: 2000, 2: 2000, 3: 125, 4: 125}
_MAX_RANGE = 1000


@router.post("/devices/{device_id}/scan-range", response_model=ScanRangeResponse)
def scan_range(
    device_id: int,
    body: ScanRangeRequest,
    db: Annotated[Session, Depends(get_session)],
):
    if body.end_address < body.start_address:
        raise HTTPException(400, "end_address must be >= start_address")

    total = body.end_address - body.start_address + 1
    if total > _MAX_RANGE:
        raise HTTPException(
            400,
            f"range too large: {total} addresses (max {_MAX_RANGE}). "
            f"Scan in smaller chunks.",
        )

    row = db.execute(
        text("SELECT id, host, port, unit_id FROM devices WHERE id = :id"),
        {"id": device_id},
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "device not found")

    is_enron = body.addressing_mode in ("ENRON_HOLDING", "ENRON_INPUT")
    if is_enron:
        if body.value_width_bytes not in (2, 4, 8):
            raise HTTPException(
                400,
                "value_width_bytes must be 2, 4, or 8 for Enron reads "
                "(uint16/int16 -> 2, uint32/int32/float32 -> 4, "
                "uint64/int64/float64 -> 8).",
            )
        if body.function_code not in (3, 4):
            raise HTTPException(
                400,
                "Enron reads only support FC=3 (holding) or FC=4 (input). "
                "Coil/discrete-input scans use STANDARD addressing.",
            )
        return _scan_enron(row, body)

    fc = body.function_code
    max_chunk = _MAX_CHUNK_BY_FC[fc]
    is_bits = fc in (1, 2)

    import time as _time
    client = _ModbusTcpClient(row["host"], port=row["port"], timeout=5)
    try:
        if not client.connect():
            raise HTTPException(502, f"could not connect to {row['host']}:{row['port']}")

        rows: list[ScanRow] = []
        chunks = 0
        t_start = _time.monotonic()

        cursor = body.start_address
        while cursor <= body.end_address:
            remaining = body.end_address - cursor + 1
            chunk_count = min(remaining, max_chunk)

            try:
                if fc == 1:
                    resp = client.read_coils(cursor, count=chunk_count, slave=row["unit_id"])
                elif fc == 2:
                    resp = client.read_discrete_inputs(cursor, count=chunk_count, slave=row["unit_id"])
                elif fc == 3:
                    resp = client.read_holding_registers(cursor, count=chunk_count, slave=row["unit_id"])
                else:
                    resp = client.read_input_registers(cursor, count=chunk_count, slave=row["unit_id"])
            except Exception as e:
                raise HTTPException(502, f"read failed at address {cursor}: {e}")

            if resp is None or resp.isError():
                raise HTTPException(
                    502,
                    f"modbus error at address {cursor} (chunk {chunks + 1}): {resp}",
                )

            if is_bits:
                bits = list(resp.bits)[:chunk_count]
                for i, b in enumerate(bits):
                    rows.append(ScanRow(
                        address=cursor + i,
                        hex="1" if b else "0",
                        value=1 if b else 0,
                    ))
            else:
                regs = list(resp.registers)[:chunk_count]
                for i, r in enumerate(regs):
                    hi = (r >> 8) & 0xFF
                    lo = r & 0xFF
                    rows.append(ScanRow(
                        address=cursor + i,
                        hex=f"{hi:02X} {lo:02X}",
                        value=int(r),
                    ))

            chunks += 1
            cursor += chunk_count

        elapsed_ms = (_time.monotonic() - t_start) * 1000

        return ScanRangeResponse(
            device_id=device_id,
            function_code=fc,
            start_address=body.start_address,
            end_address=body.end_address,
            elapsed_ms=round(elapsed_ms, 2),
            chunks=chunks,
            rows=rows,
        )
    finally:
        client.close()


def _scan_enron(device_row, body: ScanRangeRequest) -> ScanRangeResponse:
    import asyncio
    import struct
    import time as _time

    from app.workers.enron_channel import EnronChannel

    width = body.value_width_bytes
    assert width in (2, 4, 8)
    addresses_per_chunk = 50
    request_timeout_s = 5.0
    max_attempts = 3
    retry_backoff_s = 0.4

    async def read_chunk_with_retry(ch: EnronChannel, cursor: int, chunk: int) -> list[int]:
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await ch.read_enron(
                    unit_id=device_row["unit_id"],
                    function_code=body.function_code,
                    start_address=cursor,
                    count=chunk,
                    value_width_bytes=width,
                    request_timeout_s=request_timeout_s,
                )
            except Exception as e:
                last_err = e
                if attempt == max_attempts:
                    break
                try:
                    await ch.close()
                except Exception:
                    pass
                await asyncio.sleep(retry_backoff_s)
        raise RuntimeError(
            f"after {max_attempts} attempts at address {cursor}: {last_err}"
        )

    async def run_scan() -> tuple[list[int], int, float]:
        ch = EnronChannel(host=device_row["host"], port=device_row["port"])
        try:
            all_words: list[int] = []
            chunks = 0
            t_start = _time.monotonic()
            cursor = body.start_address
            while cursor <= body.end_address:
                remaining = body.end_address - cursor + 1
                chunk = min(remaining, addresses_per_chunk)
                words = await read_chunk_with_retry(ch, cursor, chunk)
                all_words.extend(words)
                chunks += 1
                cursor += chunk
            elapsed_ms = (_time.monotonic() - t_start) * 1000
            return all_words, chunks, elapsed_ms
        finally:
            await ch.close()

    try:
        all_words, chunks, elapsed_ms = asyncio.run(run_scan())
    except Exception as e:
        raise HTTPException(502, f"enron scan failed: {e}")

    wpa = width // 2
    total_addrs = body.end_address - body.start_address + 1
    expected_words = total_addrs * wpa
    if len(all_words) != expected_words:
        raise HTTPException(
            502,
            f"enron read returned {len(all_words)} words, expected {expected_words}",
        )

    rows: list[ScanRow] = []
    for i in range(total_addrs):
        addr = body.start_address + i
        words = all_words[i * wpa:(i + 1) * wpa]
        raw_bytes = b"".join(w.to_bytes(2, "big") for w in words)
        hex_str = " ".join(f"{b:02X}" for b in raw_bytes)

        decoded_f32_abcd: float | None = None
        decoded_f32_dcba: float | None = None
        decoded_i32: int | None = None
        decoded_u32: int | None = None
        decoded_f64: float | None = None

        if width == 4:
            decoded_f32_abcd = struct.unpack(">f", raw_bytes)[0]
            decoded_f32_dcba = struct.unpack("<f", raw_bytes)[0]
            decoded_i32 = struct.unpack(">i", raw_bytes)[0]
            decoded_u32 = struct.unpack(">I", raw_bytes)[0]
        elif width == 8:
            decoded_f64 = struct.unpack(">d", raw_bytes)[0]

        for name, v in (
            ("decoded_f32_abcd", decoded_f32_abcd),
            ("decoded_f32_dcba", decoded_f32_dcba),
            ("decoded_f64", decoded_f64),
        ):
            if v is not None and (v != v or v in (float("inf"), float("-inf"))):
                if name == "decoded_f32_abcd":  decoded_f32_abcd = None
                if name == "decoded_f32_dcba":  decoded_f32_dcba = None
                if name == "decoded_f64":       decoded_f64 = None

        rows.append(ScanRow(
            address=addr,
            hex=hex_str,
            value=words[0],
            decoded_float32_abcd=decoded_f32_abcd,
            decoded_float32_dcba=decoded_f32_dcba,
            decoded_int32=decoded_i32,
            decoded_uint32=decoded_u32,
            decoded_float64_abcd=decoded_f64,
        ))

    return ScanRangeResponse(
        device_id=device_row["id"],
        function_code=body.function_code,
        start_address=body.start_address,
        end_address=body.end_address,
        elapsed_ms=round(elapsed_ms, 2),
        chunks=chunks,
        rows=rows,
    )
