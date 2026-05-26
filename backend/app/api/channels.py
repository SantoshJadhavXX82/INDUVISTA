"""CRUD endpoints for channels (and a read-only protocol_connectors helper).
Phase 16.0h - audit() calls on every mutating endpoint.

Channels reference protocol_connectors by code (string). The POST endpoint
auto-upserts the connector if it doesn't exist - the audit details note
whether the connector was 'created' or 'reused' so a future "where did this
mystery protocol_connector come from?" query has an answer.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import handle_integrity_error, sql_col
from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api", tags=["channels"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

Transport = Literal["tcp", "rtu", "serial"]


class ChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    transport: Transport = "tcp"
    protocol_connector: str = Field("modbus", description="Connector code, auto-created if missing")


class ChannelUpdate(BaseModel):
    description: str | None = None
    transport: Transport | None = None
    enabled: bool | None = None


class ChannelResponse(BaseModel):
    id: int
    name: str
    description: str | None
    transport: str
    protocol_connector_id: int
    protocol_connector: str
    enabled: bool


class ProtocolConnectorResponse(BaseModel):
    id: int
    code: str
    name: str
    description: str | None


_CHANNEL_SELECT = """
    SELECT c.id, c.name, c.description, c.transport,
           c.protocol_connector_id, pc.code AS protocol_connector, c.enabled
    FROM channels c
    JOIN protocol_connectors pc ON pc.id = c.protocol_connector_id
"""


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _summarize_ch(row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "description": row.get("description"),
        "transport": row.get("transport"),
        "protocol_connector": row.get("protocol_connector"),
        "enabled": row.get("enabled"),
    }


def _full_ch(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "transport": row.get("transport"),
        "protocol_connector_id": row.get("protocol_connector_id"),
        "protocol_connector": row.get("protocol_connector"),
        "enabled": row.get("enabled"),
    }


# ---------------------------------------------------------------------------
# Protocol connectors (read-only)
# ---------------------------------------------------------------------------

@router.get("/protocol-connectors", response_model=list[ProtocolConnectorResponse])
def list_protocol_connectors(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(
        text("SELECT id, code, name, description FROM protocol_connectors ORDER BY id")
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Channels: list + get (read-only)
# ---------------------------------------------------------------------------

@router.get("/channels", response_model=list[ChannelResponse])
def list_channels(db: Annotated[Session, Depends(get_session)]):
    # Phase OPC-web.2.2.b: hide orphan channels (all devices soft-deleted).
    # Channels with no devices at all stay visible. Admin lookups by ID
    # (GET/PATCH/DELETE /channels/{id}) bypass this filter intentionally.
    rows = db.execute(text(f"""
        SELECT * FROM ({_CHANNEL_SELECT}) ch
        WHERE (
            NOT EXISTS (SELECT 1 FROM devices d WHERE d.channel_id = ch.id)
            OR EXISTS (SELECT 1 FROM devices d WHERE d.channel_id = ch.id AND d.deleted_at IS NULL)
        )
        ORDER BY ch.id
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/channels/{channel_id}", response_model=ChannelResponse)
def get_channel(channel_id: int, db: Annotated[Session, Depends(get_session)]):
    row = db.execute(
        text(_CHANNEL_SELECT + " WHERE c.id = :id"),
        {"id": channel_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"channel {channel_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Channels: create / update / delete (audited)
# ---------------------------------------------------------------------------

@router.post("/channels", response_model=ChannelResponse, status_code=201)
def create_channel(
    body: ChannelCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.name

    # Protocol_connector auto-upsert split into explicit SELECT-then-INSERT
    # so we know whether we created a new one or reused an existing row.
    existing_pc = db.execute(
        text("SELECT id FROM protocol_connectors WHERE code = :code"),
        {"code": body.protocol_connector},
    ).scalar()

    if existing_pc is not None:
        pc_id = existing_pc
        pc_action = "reused"
    else:
        try:
            pc_id = db.execute(
                text("""
                    INSERT INTO protocol_connectors (code, name, description)
                    VALUES (:code, :title, :title || ' protocol connector')
                    RETURNING id
                """),
                {"code": body.protocol_connector, "title": body.protocol_connector.title()},
            ).scalar_one()
            pc_action = "created"
        except Exception as e:
            db.rollback()
            audit(AuditEvent(
                action="channel.create",
                target_type="channel",
                target_label=target_label,
                summary=f"INSERT failed during protocol_connector upsert",
                status="error",
                error_message=f"{type(e).__name__}: {e}",
                details={"request": body.model_dump()},
            ), request)
            raise

    try:
        new_id = db.execute(
            text("""
                INSERT INTO channels (protocol_connector_id, name, description, transport)
                VALUES (:pc_id, :name, :description, :transport)
                RETURNING id
            """),
            {
                "pc_id": pc_id,
                "name": body.name,
                "description": body.description,
                "transport": body.transport,
            },
        ).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        # Categorize for audit before letting handle_integrity_error raise.
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action="channel.create",
                target_type="channel",
                target_label=target_label,
                summary=f"Denied: channel '{body.name}' already exists",
                status="denied",
                error_message="duplicate name",
                details={"request": body.model_dump(), "protocol_connector_action": pc_action},
            ), request)
        else:
            audit(AuditEvent(
                action="channel.create",
                target_type="channel",
                target_label=target_label,
                summary="INSERT failed (IntegrityError)",
                status="error",
                error_message=str(e.orig),
                details={"request": body.model_dump(), "protocol_connector_action": pc_action},
            ), request)
        try:
            handle_integrity_error(e, "channel")
        except HTTPException:
            raise
        # If the helper returned without raising, raise a generic 400.
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    audit(AuditEvent(
        action="channel.create",
        target_type="channel",
        target_id=new_id,
        target_label=target_label,
        summary=f"Created channel '{body.name}' "
                f"(transport={body.transport}, connector={body.protocol_connector}/{pc_action})",
        details={**body.model_dump(), "protocol_connector_action": pc_action,
                 "protocol_connector_id": pc_id},
    ), request)

    return get_channel(new_id, db)


@router.patch("/channels/{channel_id}", response_model=ChannelResponse)
def update_channel(
    channel_id: int,
    body: ChannelUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "channel.toggle" if is_toggle else "channel.update"

    # Pre-fetch existing for before-snapshot AND to handle 404 cleanly.
    existing = db.execute(
        text(_CHANNEL_SELECT + " WHERE c.id = :id"),
        {"id": channel_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="channel",
            target_id=channel_id,
            summary=f"Denied: channel {channel_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"channel {channel_id} not found")

    target_label = existing["name"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="channel",
            target_id=channel_id,
            target_label=target_label,
            summary="Denied: no fields to update",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "no fields to update")

    set_clauses = ", ".join(f"{sql_col(k)} = :{k}" for k in updates)
    params = {**updates, "id": channel_id}

    try:
        db.execute(
            text(f"UPDATE channels SET {set_clauses} WHERE id = :id"),
            params,
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action=action,
                target_type="channel",
                target_id=channel_id,
                target_label=target_label,
                summary="Denied: update caused unique-constraint violation",
                status="denied",
                error_message="duplicate value",
                details={"request": updates, "before": _summarize_ch(existing)},
            ), request)
        else:
            audit(AuditEvent(
                action=action,
                target_type="channel",
                target_id=channel_id,
                target_label=target_label,
                summary="UPDATE failed (IntegrityError)",
                status="error",
                error_message=str(e.orig),
                details={"request": updates, "before": _summarize_ch(existing)},
            ), request)
        try:
            handle_integrity_error(e, "channel")
        except HTTPException:
            raise
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action=action,
            target_type="channel",
            target_id=channel_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": updates, "before": _summarize_ch(existing)},
        ), request)
        raise

    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} channel '{existing['name']}'"
    else:
        summary = f"Updated channel '{existing['name']}' ({', '.join(updates.keys())})"

    audit(AuditEvent(
        action=action,
        target_type="channel",
        target_id=channel_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_ch(existing),
        },
    ), request)

    return get_channel(channel_id, db)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(
    channel_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    # Pre-fetch for 404 + before-snapshot.
    existing = db.execute(
        text(_CHANNEL_SELECT + " WHERE c.id = :id"),
        {"id": channel_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="channel.delete",
            target_type="channel",
            target_id=channel_id,
            summary=f"Denied: channel {channel_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"channel {channel_id} not found")

    target_label = existing["name"]

    # Pre-count devices referencing this channel for friendlier audit detail.
    device_count = db.execute(
        text("SELECT COUNT(*) FROM devices WHERE channel_id = :id"),
        {"id": channel_id},
    ).scalar() or 0

    try:
        db.execute(
            text("DELETE FROM channels WHERE id = :id"),
            {"id": channel_id},
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        audit(AuditEvent(
            action="channel.delete",
            target_type="channel",
            target_id=channel_id,
            target_label=target_label,
            summary=f"Denied: channel '{existing['name']}' has {device_count} device(s) referencing it",
            status="denied",
            error_message=f"FK violation: devices_count={device_count}",
            details={"before": _full_ch(existing), "device_count": device_count},
        ), request)
        raise HTTPException(
            409,
            f"channel {channel_id} cannot be deleted because devices reference it",
        )
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="channel.delete",
            target_type="channel",
            target_id=channel_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_ch(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="channel.delete",
        target_type="channel",
        target_id=channel_id,
        target_label=target_label,
        summary=f"Deleted channel '{existing['name']}' (transport={existing.get('transport')})",
        details={"before": _full_ch(existing)},
    ), request)
