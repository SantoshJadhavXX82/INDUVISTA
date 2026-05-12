"""CRUD endpoints for register_blocks."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import handle_integrity_error, sql_col
from app.db import get_session

router = APIRouter(prefix="/api", tags=["register-blocks"])


class RegisterBlockCreate(BaseModel):
    device_id: int
    name: str = Field(..., min_length=1, max_length=100)
    function_code: int = Field(..., ge=1, le=4, description="1=coils, 2=DI, 3=HR, 4=IR")
    start_address: int = Field(..., ge=0, le=65535)
    count: int = Field(..., ge=1, le=125, description="Modbus PDU max is 125 registers")
    # Both are NOT NULL in the DB. Defaults here mean "poll at 1 Hz, no
    # phase offset" — same defaults the seeder writes for every block.
    scan_interval_ms: int = Field(1000, ge=10)
    phase_ms: int = Field(0, ge=0)
    # Phase 8.5.1 — engineering policy: is this block RW-capable?
    # Only meaningful for FC 1 (Coil) and FC 3 (HR). DB CHECK enforces this.
    writable: bool = Field(False, description="Allow writes to tags in this block")


class RegisterBlockUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    count: int | None = Field(None, ge=1, le=125)
    scan_interval_ms: int | None = Field(None, ge=10)
    phase_ms: int | None = Field(None, ge=0)
    enabled: bool | None = None
    writable: bool | None = None


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


_BLOCK_SELECT = """
    SELECT b.id, b.device_id, d.name AS device_name, b.name,
           b.function_code, b.start_address, b.count,
           b.scan_interval_ms, b.phase_ms, b.enabled, b.writable
    FROM register_blocks b
    JOIN devices d ON d.id = b.device_id
"""


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
    """Nested listing — same as /api/register-blocks?device_id=N but reads naturally."""
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


@router.post("/register-blocks", response_model=RegisterBlockResponse, status_code=201)
def create_register_block(
    body: RegisterBlockCreate,
    db: Annotated[Session, Depends(get_session)],
):
    try:
        new_id = db.execute(
            text("""
                INSERT INTO register_blocks (
                    device_id, name, function_code, start_address, count,
                    scan_interval_ms, phase_ms, writable
                )
                VALUES (
                    :device_id, :name, :function_code, :start_address, :count,
                    :scan_interval_ms, :phase_ms, :writable
                )
                RETURNING id
            """),
            body.model_dump(),
        ).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "register_block")

    return get_register_block(new_id, db)


@router.patch("/register-blocks/{block_id}", response_model=RegisterBlockResponse)
def update_register_block(
    block_id: int,
    body: RegisterBlockUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "no fields to update")

    set_clauses = ", ".join(f"{sql_col(k)} = :{k}" for k in updates)
    updates["id"] = block_id

    try:
        result = db.execute(
            text(f"UPDATE register_blocks SET {set_clauses} WHERE id = :id"),
            updates,
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"register_block {block_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "register_block")

    return get_register_block(block_id, db)


@router.delete("/register-blocks/{block_id}", status_code=204)
def delete_register_block(
    block_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    try:
        result = db.execute(
            text("DELETE FROM register_blocks WHERE id = :id"),
            {"id": block_id},
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"register_block {block_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            409,
            f"register_block {block_id} cannot be deleted because tags reference it",
        )


# ---------------------------------------------------------------------------
# Bulk create (Phase 6 enhancement — CSV import)
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
    db: Annotated[Session, Depends(get_session)],
):
    """Create many register blocks in one request. Per-row error reporting.

    Same pattern as /api/tags/bulk: nested transactions isolate row failures
    so one bad block doesn't poison the batch. Returns a list aligned to
    input order with success/error per row.
    """
    results: list[BulkBlockResult] = []
    for idx, blk_in in enumerate(body.blocks):
        try:
            with db.begin_nested():
                result = db.execute(
                    text("""
                        INSERT INTO register_blocks (
                            device_id, name, function_code,
                            start_address, count,
                            scan_interval_ms, phase_ms, writable
                        ) VALUES (
                            :device_id, :name, :function_code,
                            :start_address, :count,
                            :scan_interval_ms, :phase_ms, :writable
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
    return results
