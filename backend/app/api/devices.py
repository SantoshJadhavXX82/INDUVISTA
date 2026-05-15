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
    # Phase 12.2 — which tag on this device reports its self-assessed
    # duty/standby role. NULL = manual-only (current behavior).
    duty_status_tag_id: int | None = None
    # Phase 12.5 — suspends worker reconciliation for this pair
    manual_override: bool = False
    # Phase 12.6 — operator may create a device in disabled state (useful
    # for staging a config before going live, or for test fixtures that
    # need a config row but no polling). DB default is TRUE; this surfaces
    # it as a proper API field instead of silently defaulting.
    enabled: bool = True
    # Phase 8.5 — Modbus hardening config
    request_timeout_ms: int = Field(3000, ge=100, le=60000,
        description="Per-request timeout in ms (100-60000)")
    retry_count: int = Field(1, ge=0, le=10,
        description="Retries on failed block reads (0-10)")
    reconnect_initial_ms: int = Field(1000, ge=100, le=60000,
        description="Initial reconnect backoff (ms)")
    reconnect_max_ms: int = Field(30000, ge=100, le=300000,
        description="Maximum reconnect backoff after exponential doubling (ms)")


class DeviceUpdate(BaseModel):
    # Phase 11 — name editable. Device identity is the integer id (FK from
    # tags, register_blocks, worker_device_status). Renaming preserves all
    # references; the unique constraint (channel_id, name) is enforced at
    # the DB level.
    name: str | None = Field(None, min_length=1, max_length=100)
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
    duty_status_tag_id: int | None = None
    manual_override: bool | None = None
    enabled: bool | None = None
    # Phase 8.5 — Modbus hardening config (all optional for PATCH semantics)
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
    duty_status_tag_id: int | None
    manual_override: bool
    enabled: bool
    # Phase 8.5 — hardening config (always returned)
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


# ---------------------------------------------------------------------------
# Phase 12 — Duty/standby pairing (configuration + manual swap)
# ---------------------------------------------------------------------------
#
# Schema invariants enforced by ck_devices_duty_role_consistency:
#   duty_role = 'none'        ⇔  redundant_device_id IS NULL
#   duty_role IN ('duty','standby')  ⇔  redundant_device_id IS NOT NULL
#
# Both rows of a pair MUST satisfy the constraint after every transaction.
# These endpoints wrap the multi-row UPDATEs in a single transaction so
# we never leave the database in a state that would fail the constraint.

class PairRequest(BaseModel):
    """Pair this device with a partner. This device becomes the duty role
    given (default 'duty'); the partner becomes the opposite role.
    Both `redundant_device_id` columns are set to point at each other."""
    partner_device_id: int
    this_role: Literal["duty", "standby"] = "duty"


@router.post("/devices/{device_id}/pair", response_model=DeviceResponse)
def pair_devices(
    device_id: int,
    body: PairRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a duty/standby pair between two devices in one transaction.

    Either device may already be in a pair; if so, the old partner is
    unpaired first (set duty_role='none', redundant_device_id=NULL). This
    keeps the schema invariant intact at every commit boundary.
    """
    if device_id == body.partner_device_id:
        raise HTTPException(400, "a device cannot be its own partner")

    # Verify both devices exist
    rows = db.execute(
        text("SELECT id, name, duty_role, redundant_device_id FROM devices "
             "WHERE id IN (:a, :b) FOR UPDATE"),
        {"a": device_id, "b": body.partner_device_id},
    ).mappings().all()
    if len(rows) != 2:
        raise HTTPException(404, "one or both devices not found")

    me = next(r for r in rows if r["id"] == device_id)
    partner = next(r for r in rows if r["id"] == body.partner_device_id)
    partner_role: Literal["duty", "standby"] = (
        "standby" if body.this_role == "duty" else "duty"
    )

    # Unpair any existing partners (avoids constraint violation when our
    # update lands on a row whose old partner now has duty_role IN
    # ('duty','standby') but redundant_device_id NULL).
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

    # Apply the new pairing.
    try:
        db.execute(
            text("UPDATE devices SET duty_role=:r, redundant_device_id=:p WHERE id=:id"),
            {"r": body.this_role, "p": body.partner_device_id, "id": device_id},
        )
        db.execute(
            text("UPDATE devices SET duty_role=:r, redundant_device_id=:p WHERE id=:id"),
            {"r": partner_role, "p": device_id, "id": body.partner_device_id},
        )
        # Record the pairing as a "startup"-reason history row so reports
        # can answer "who was duty when?" from the very first moment.
        db.execute(
            text("""INSERT INTO device_duty_history
                    (device_id, paired_device_id, switched_at, reason, notes)
                    VALUES (:d, :p, NOW(), 'startup', :note)"""),
            {
                "d": device_id if body.this_role == "duty" else body.partner_device_id,
                "p": body.partner_device_id if body.this_role == "duty" else device_id,
                "note": f"paired via API: {me['name']} ({body.this_role}) ↔ {partner['name']} ({partner_role})",
            },
        )
        # Phase 12.3 — auto-generate pair tags for name-matching tags
        # across the two devices. Same transaction as the pairing itself
        # so a constraint failure here rolls back the whole pair.
        from app.api.pair_tags import auto_create_pair_tags
        auto_create_pair_tags(db, device_id, body.partner_device_id)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "device")

    return get_device(device_id, db)


@router.post("/devices/{device_id}/unpair", response_model=DeviceResponse)
def unpair_device(
    device_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Break the pair this device is part of. Both devices revert to
    duty_role='none', redundant_device_id=NULL."""
    row = db.execute(
        text("SELECT redundant_device_id FROM devices WHERE id=:id"),
        {"id": device_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"device {device_id} not found")
    partner_id = row["redundant_device_id"]

    try:
        # Phase 12.3 — remove auto-generated pair tags before unpairing.
        # Done before the device UPDATE so we still have the FK references
        # in place. Done inside the same transaction so the change is atomic.
        if partner_id is not None:
            from app.api.pair_tags import auto_delete_pair_tags
            auto_delete_pair_tags(db, device_id, partner_id)
        db.execute(
            text("UPDATE devices SET duty_role='none', redundant_device_id=NULL "
                 "WHERE id IN (:a, :b)"),
            {"a": device_id, "b": partner_id or device_id},
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "device")

    return get_device(device_id, db)


class SwapDutyRequest(BaseModel):
    reason: Literal["manual", "primary_failed", "partner_channel_failover",
                    "scheduled", "failback", "startup"] = "manual"
    notes: str | None = None


@router.post("/devices/{device_id}/swap-duty", response_model=DeviceResponse)
def swap_duty(
    device_id: int,
    body: SwapDutyRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Atomically swap duty/standby roles between this device and its partner.

    Records the swap in device_duty_history so reports can later answer
    "who was duty at time T?" for fiscal audit purposes.

    Caller can be either the current duty or the current standby — we
    just flip both rows. Requires that the device is part of a pair.
    """
    row = db.execute(
        text("SELECT id, name, duty_role, redundant_device_id FROM devices WHERE id=:id"),
        {"id": device_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"device {device_id} not found")
    if row["redundant_device_id"] is None or row["duty_role"] == "none":
        raise HTTPException(400, "device is not part of a duty/standby pair")

    partner_id = row["redundant_device_id"]
    # After the swap, whoever was 'standby' becomes 'duty' and vice versa.
    new_role_for_me = "standby" if row["duty_role"] == "duty" else "duty"
    new_role_for_partner = "duty" if row["duty_role"] == "duty" else "standby"

    try:
        db.execute(
            text("UPDATE devices SET duty_role=:r WHERE id=:id"),
            {"r": new_role_for_me, "id": device_id},
        )
        db.execute(
            text("UPDATE devices SET duty_role=:r WHERE id=:id"),
            {"r": new_role_for_partner, "id": partner_id},
        )
        # The 'device_id' in history is the device that BECAME duty.
        # 'paired_device_id' is the one that became standby.
        became_duty = partner_id if new_role_for_me == "standby" else device_id
        became_standby = device_id if new_role_for_me == "standby" else partner_id
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
        handle_integrity_error(e, "device")

    return get_device(device_id, db)


class DutyHistoryRow(BaseModel):
    id: int
    device_id: int
    device_name: str
    paired_device_id: int
    paired_device_name: str
    switched_at: str  # ISO 8601 string
    reason: str
    notes: str | None


@router.get("/devices/{device_id}/duty-history", response_model=list[DutyHistoryRow])
def get_duty_history(
    device_id: int,
    db: Annotated[Session, Depends(get_session)],
    limit: int = Query(50, ge=1, le=500),
):
    """Recent duty switches involving this device, newest first.

    Returns rows where this device was on EITHER side of the swap, so
    operators can see every transition the device participated in.
    """
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


# ---------------------------------------------------------------------------
# Test read (Phase 7 commissioning helper — C2)
# ---------------------------------------------------------------------------
#
# Issues a one-shot Modbus read on demand and returns the raw bytes plus a
# decoded matrix across every data type / byte order combination that fits
# the byte count. Engineers use this to find the right interpretation by
# eye — "the float32 ABCD column reads 19.71, matches the device display,
# I'll pick that combination for the tag".

# Phase 12.5 — Manual override for duty/standby pairs.
# Setting manual_override=TRUE on either side of a pair suspends worker
# reconciliation for that pair. Operators use this to perform sticky
# manual swaps during commissioning, maintenance, or controlled failover
# tests. Toggling via this endpoint sets BOTH sides atomically so the
# pair has a single coherent mode at all times.

class SetPairOverrideRequest(BaseModel):
    enable: bool


@router.post("/devices/{device_id}/set-pair-override", response_model=DeviceResponse)
def set_pair_override(
    device_id: int,
    body: SetPairOverrideRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Toggle manual_override on both sides of a duty/standby pair.

    When enabled, the worker's reconciliation loop excludes this pair, so
    any manual swap (via /swap-duty or the UI) sticks until override is
    disabled. When disabled, the worker resumes reconciliation on the
    next cycle and will sync duty_role with the device-reported value.
    """
    row = db.execute(
        text("SELECT id, redundant_device_id FROM devices WHERE id = :id"),
        {"id": device_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "device not found")
    if row["redundant_device_id"] is None:
        raise HTTPException(400, "device is not paired — manual override only applies to pairs")

    ids = [device_id, row["redundant_device_id"]]
    db.execute(
        text("UPDATE devices SET manual_override = :v WHERE id = ANY(:ids)"),
        {"v": body.enable, "ids": ids},
    )
    db.commit()

    updated = db.execute(text(_DEVICE_SELECT + " WHERE d.id = :id"),
                         {"id": device_id}).mappings().first()
    return dict(updated)


# ----------------------------------------------------------------------
# Modbus diagnostics — raw register reads + scan
# ----------------------------------------------------------------------

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
    decoded: dict  # nested: data_type -> byte_order -> value (as str so JSON survives big ints)


def _decode_matrix(raw_bytes: bytes) -> dict:
    """Try every data type / byte order combination that fits the byte count.

    Returns nested dict: {data_type: {byte_order_label: stringified value}}.
    Values are stringified because JSON spec can't carry full uint64 fidelity
    and the UI just renders them as text anyway.
    """
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
        # Four byte-order permutations
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
            "ABCD…HG (big-endian)": b,
            "DCBA…FE (little-endian)": b[::-1],
        }
        result["int64"] = {
            k: str(_struct.unpack(">q", v)[0]) for k, v in orderings_64.items()
        }
        result["float64"] = {
            k: _format_float(_struct.unpack(">d", v)[0]) for k, v in orderings_64.items()
        }
    return result


def _format_float(f: float) -> str:
    """Render a float in a way that distinguishes plausible-looking values
    from garbage (denormals, infinities, etc). Engineers scan visually for
    sensible numbers."""
    import math
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    abs_f = abs(f)
    if abs_f == 0:
        return "0"
    if abs_f < 1e-6 or abs_f > 1e9:
        return f"{f:.3e}"  # likely garbage byte order
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
    """One-shot read of a device register for commissioning.

    Opens a transient TCP connection to the device, issues the read, decodes
    the response in every plausible data type / byte order, returns the
    matrix. Connection is closed after. For devices that only allow one TCP
    client, the worker will briefly disconnect and reconnect on its next
    cycle — acceptable for a manual operation.
    """
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
            else:  # fc == 4
                resp = client.read_input_registers(body.address, count=body.register_count, slave=unit_id)
        except Exception as e:
            raise HTTPException(502, f"read failed: {e}")

        if resp is None or resp.isError():
            raise HTTPException(502, f"modbus error: {resp}")

        if fc in (1, 2):
            # Bit reads
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


# ---------------------------------------------------------------------------
# Scan range (Phase 7 — C4 Register Browser)
# ---------------------------------------------------------------------------
#
# Reads a contiguous address range from a device and returns one entry per
# register. The frontend Register Browser uses this to "discover" what data
# lives at unmapped addresses — invaluable when working from a vendor manual
# whose register map you don't fully trust yet.
#
# Reads are chunked at the Modbus protocol cap (125 holding/input registers,
# 2000 coils/discrete-inputs per request). Total range is capped at 1000
# addresses to bound response size and request duration.


class ScanRangeRequest(BaseModel):
    function_code: int = Field(..., ge=1, le=4)
    start_address: int = Field(..., ge=0, le=65535)
    end_address: int = Field(..., ge=0, le=65535)
    # Phase 10.2 — Enron-style read. When set, the scan routes through the
    # persistent EnronChannel (permissive byte_count parser, supports
    # Daniel's 4N+3 framing). Each logical address holds one value; width
    # is the value's on-wire byte count (2, 4, or 8).
    addressing_mode: str | None = Field(
        None, description="STANDARD (default) | ENRON_HOLDING | ENRON_INPUT",
    )
    value_width_bytes: int | None = Field(
        None, ge=2, le=8,
        description="2, 4, or 8 — bytes per logical address (Enron only).",
    )


class ScanRow(BaseModel):
    address: int
    hex: str           # full byte width: 2 hex pairs for 16-bit, 4 for 32-bit, 8 for 64-bit
    value: int         # first uint16 (or 0/1 for bits) — for back-compat display

    # Phase 10.2 — decoded interpretations baked in when Enron-mode reads
    # know the width. Standard reads leave these None and let the frontend
    # do consecutive-row pairing as before.
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


# Modbus protocol caps per FC
_MAX_CHUNK_BY_FC = {1: 2000, 2: 2000, 3: 125, 4: 125}
_MAX_RANGE = 1000


@router.post("/devices/{device_id}/scan-range", response_model=ScanRangeResponse)
def scan_range(
    device_id: int,
    body: ScanRangeRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Scan a contiguous address range for register-discovery workflows.

    Two paths share this endpoint:
      * STANDARD — pymodbus sync client, returns one row per 16-bit register
      * ENRON_HOLDING/INPUT — persistent EnronChannel, returns one row per
        logical address (with full-width hex + decoded float32/int32) so
        you can see what's at each Daniel/Emerson 700XA address without
        having to first map out byte widths by trial and error.
    """
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

    # Phase 10.2 — Enron path. We delegate to the same persistent-socket
    # channel the worker uses, which means Daniel's "4N + trailing" framing
    # is handled exactly the way it is for live polling.
    is_enron = body.addressing_mode in ("ENRON_HOLDING", "ENRON_INPUT")
    if is_enron:
        if body.value_width_bytes not in (2, 4, 8):
            raise HTTPException(
                400,
                "value_width_bytes must be 2, 4, or 8 for Enron reads "
                "(uint16/int16 → 2, uint32/int32/float32 → 4, "
                "uint64/int64/float64 → 8).",
            )
        if body.function_code not in (3, 4):
            raise HTTPException(
                400,
                "Enron reads only support FC=3 (holding) or FC=4 (input). "
                "Coil/discrete-input scans use STANDARD addressing.",
            )
        return _scan_enron(row, body)

    # Standard pymodbus path (the original Phase 7 C4 behavior, unchanged).
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
                else:  # fc == 4
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


# ===========================================================================
# Phase 10.2 — Enron-mode scan helper
# ===========================================================================
def _scan_enron(device_row, body: ScanRangeRequest) -> ScanRangeResponse:
    """Run the address-range scan through the EnronChannel.

    Each logical address is decoded once into every plausible interpretation
    for its width: float32 ABCD + DCBA, signed/unsigned 32-bit ints (width=4),
    or float64 ABCD (width=8). For width=2 the standard uint16/int16
    interpretations are produced by the frontend (same as the STANDARD path).

    Retry behavior (Phase 10.2-hotfix): Daniel/Emerson GCs sometimes drop the
    first TCP connection from a new client within the first ~100 ms, especially
    when the worker already holds a persistent connection. We retry each chunk
    up to 3 times with a short backoff before giving up — long enough to ride
    out a single bad open, short enough that a genuinely unreachable device
    fails the user's scan within a few seconds.
    """
    import asyncio
    import struct
    import time as _time

    from app.workers.enron_channel import EnronChannel

    width = body.value_width_bytes
    assert width in (2, 4, 8)
    addresses_per_chunk = 50          # safe under Modbus's 125-register cap
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
                # Force a fresh socket on retry — the existing one is probably
                # half-closed if the peer dropped us mid-handshake.
                try:
                    await ch.close()
                except Exception:
                    pass
                await asyncio.sleep(retry_backoff_s)
        # Reraise with a hint about retries so the UI message is informative.
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

    # words_per_addr = width / 2
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
        # Pack words → bytes (big-endian) for hex display
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

        # NaN/Inf serialize fine to JSON only if we sanitize: float() over a
        # struct-unpacked NaN is still NaN. Replace with None so JSON is valid.
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
            value=words[0],                # first uint16 — keeps the existing UI happy
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
