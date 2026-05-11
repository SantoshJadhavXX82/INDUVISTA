"""CRUD endpoints for channels (and a read-only protocol_connectors helper).

Channels reference protocol_connectors by code (string). The POST endpoint
auto-upserts the connector if it doesn't exist — saves the caller from a
pre-flight POST against a near-fixed list.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import handle_integrity_error, sql_col
from app.db import get_session

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
# Protocol connectors (read-only listing for ergonomics)
# ---------------------------------------------------------------------------

@router.get("/protocol-connectors", response_model=list[ProtocolConnectorResponse])
def list_protocol_connectors(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(
        text("SELECT id, code, name, description FROM protocol_connectors ORDER BY id")
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

@router.get("/channels", response_model=list[ChannelResponse])
def list_channels(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text(_CHANNEL_SELECT + " ORDER BY c.id")).mappings().all()
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


@router.post("/channels", response_model=ChannelResponse, status_code=201)
def create_channel(
    body: ChannelCreate,
    db: Annotated[Session, Depends(get_session)],
):
    # Auto-upsert the protocol_connector if it doesn't exist yet.
    pc_id = db.execute(
        text("""
            INSERT INTO protocol_connectors (code, name, description)
            VALUES (:code, :title, :title || ' protocol connector')
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """),
        {"code": body.protocol_connector, "title": body.protocol_connector.title()},
    ).scalar_one()

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
        handle_integrity_error(e, "channel")

    return get_channel(new_id, db)


@router.patch("/channels/{channel_id}", response_model=ChannelResponse)
def update_channel(
    channel_id: int,
    body: ChannelUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "no fields to update")

    set_clauses = ", ".join(f"{sql_col(k)} = :{k}" for k in updates)
    updates["id"] = channel_id

    try:
        result = db.execute(
            text(f"UPDATE channels SET {set_clauses} WHERE id = :id"),
            updates,
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"channel {channel_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "channel")

    return get_channel(channel_id, db)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: int, db: Annotated[Session, Depends(get_session)]):
    try:
        result = db.execute(
            text("DELETE FROM channels WHERE id = :id"),
            {"id": channel_id},
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"channel {channel_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        # Channel has devices referencing it — 409 conflict.
        raise HTTPException(
            409,
            f"channel {channel_id} cannot be deleted because devices reference it",
        )
