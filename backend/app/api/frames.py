"""Phase 7 Batch 2 — Frame Inspector API.

Two endpoints:
- GET  /api/devices/{id}/frames       → retrieve last N captured frames
- POST /api/devices/{id}/frame-capture → toggle capture on/off

State is in Valkey, shared with the worker process. See
app.workers.frame_capture for the storage layout.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.workers import frame_capture

router = APIRouter(prefix="/api", tags=["frames"])


class CaptureRequest(BaseModel):
    enabled: bool


class CaptureState(BaseModel):
    device_id: int
    enabled: bool


class FrameModel(BaseModel):
    """One captured Modbus frame. Direction 'tx' is what the worker sent;
    'rx' is what came back. Paired by transaction_id."""
    seq: int
    timestamp: str
    direction: str  # 'tx' | 'rx'
    function_code: int
    address: int
    register_count: int
    unit_id: int
    block_name: str
    transaction_id: int
    hex_bytes: str
    byte_count: int
    latency_ms: float | None
    error: str | None
    summary: str | None = None


class FramesResponse(BaseModel):
    device_id: int
    capture_enabled: bool
    frames: list[FrameModel]


def _ensure_device(db: Session, device_id: int) -> None:
    row = db.execute(
        text("SELECT id FROM devices WHERE id = :id"), {"id": device_id}
    ).first()
    if not row:
        raise HTTPException(404, "device not found")


@router.get("/devices/{device_id}/frames", response_model=FramesResponse)
def get_frames(
    device_id: int,
    db: Annotated[Session, Depends(get_session)],
    limit: int = Query(200, ge=1, le=200),
):
    """Return the most-recent frames, newest first."""
    _ensure_device(db, device_id)
    return FramesResponse(
        device_id=device_id,
        capture_enabled=frame_capture.get_capture_state(device_id),
        frames=frame_capture.get_frames(device_id, limit=limit),
    )


@router.post("/devices/{device_id}/frame-capture", response_model=CaptureState)
def toggle_capture(
    device_id: int,
    body: CaptureRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Toggle frame capture for this device. Disabling also clears the
    ring buffer so the UI doesn't show stale data on next enable."""
    _ensure_device(db, device_id)
    try:
        frame_capture.set_capture(device_id, body.enabled)
    except Exception as e:
        raise HTTPException(503, f"frame-capture backend unavailable: {e}")
    return CaptureState(device_id=device_id, enabled=body.enabled)
