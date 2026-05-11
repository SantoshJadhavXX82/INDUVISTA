"""CRUD endpoints for devices."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import handle_integrity_error, sql_col
from app.db import get_session

router = APIRouter(prefix="/api", tags=["devices"])


DutyRole = Literal["duty", "standby", "none"]


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


class DeviceUpdate(BaseModel):
    description: str | None = None
    protocol: str | None = None
    host: str | None = None
    port: int | None = Field(None, ge=1, le=65535)
    unit_id: int | None = Field(None, ge=0, le=255)
    # Optional here = "field not supplied" (PATCH semantics). Frontend
    # must send a valid value when it does include the field, never null.
    duty_role: DutyRole | None = None
    stale_after_sec: int | None = Field(None, ge=1)
    scan_interval_ms: int | None = Field(None, ge=10)
    secondary_host: str | None = None
    secondary_port: int | None = Field(None, ge=1, le=65535)
    secondary_unit_id: int | None = Field(None, ge=0, le=255)
    redundant_device_id: int | None = None
    enabled: bool | None = None


class DeviceResponse(BaseModel):
    id: int
    name: str
    channel_id: int
    channel_name: str
    description: str | None
    protocol: str
    host: str
    port: int
    unit_id: int
    duty_role: str | None
    stale_after_sec: int
    scan_interval_ms: int
    secondary_host: str | None
    secondary_port: int | None
    secondary_unit_id: int | None
    redundant_device_id: int | None
    enabled: bool


_DEVICE_SELECT = """
    SELECT d.id, d.name, d.channel_id, c.name AS channel_name,
           d.description, d.protocol, d.host, d.port, d.unit_id,
           d.duty_role, d.stale_after_sec, d.scan_interval_ms,
           d.secondary_host, d.secondary_port, d.secondary_unit_id,
           d.redundant_device_id, d.enabled
    FROM devices d
    JOIN channels c ON c.id = d.channel_id
"""


@router.get("/devices", response_model=list[DeviceResponse])
def list_devices(
    db: Annotated[Session, Depends(get_session)],
    channel_id: Annotated[int | None, Query(description="Filter by channel id")] = None,
):
    sql = _DEVICE_SELECT
    params: dict = {}
    if channel_id is not None:
        sql += " WHERE d.channel_id = :channel_id"
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


@router.post("/devices", response_model=DeviceResponse, status_code=201)
def create_device(
    body: DeviceCreate,
    db: Annotated[Session, Depends(get_session)],
):
    try:
        new_id = db.execute(
            text("""
                INSERT INTO devices (
                    channel_id, name, description, protocol,
                    host, port, unit_id,
                    duty_role, stale_after_sec, scan_interval_ms,
                    secondary_host, secondary_port, secondary_unit_id,
                    redundant_device_id
                )
                VALUES (
                    :channel_id, :name, :description, :protocol,
                    :host, :port, :unit_id,
                    :duty_role, :stale_after_sec, :scan_interval_ms,
                    :secondary_host, :secondary_port, :secondary_unit_id,
                    :redundant_device_id
                )
                RETURNING id
            """),
            body.model_dump(),
        ).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "device")

    return get_device(new_id, db)


@router.patch("/devices/{device_id}", response_model=DeviceResponse)
def update_device(
    device_id: int,
    body: DeviceUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "no fields to update")

    set_clauses = ", ".join(f"{sql_col(k)} = :{k}" for k in updates)
    updates["id"] = device_id

    try:
        result = db.execute(
            text(f"UPDATE devices SET {set_clauses} WHERE id = :id"),
            updates,
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"device {device_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "device")

    return get_device(device_id, db)


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(device_id: int, db: Annotated[Session, Depends(get_session)]):
    try:
        result = db.execute(
            text("DELETE FROM devices WHERE id = :id"),
            {"id": device_id},
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"device {device_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            409,
            f"device {device_id} cannot be deleted because other rows reference it "
            "(register_blocks, tags, or the redundant_device pair)",
        )
