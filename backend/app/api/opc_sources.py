"""OPC UA sources + mappings — Phase OPC-web.1.

ENDPOINTS
=========

  POST   /api/opc-sources                       Create source (+ synthetic chan/dev)
  GET    /api/opc-sources                       List all sources
  GET    /api/opc-sources/{id}                  Get one
  PATCH  /api/opc-sources/{id}                  Update tuning/auth/enabled
  DELETE /api/opc-sources/{id}                  Delete (cascades to chan/dev/tags)

  POST   /api/opc-sources/{id}/mappings         Add a tag mapping (auto-creates tag)
  GET    /api/opc-sources/{id}/mappings         List mappings for a source
  DELETE /api/opc-sources/{id}/mappings/{mid}   Delete one mapping (cascades to tag)

DESIGN — SYNTHETIC CHANNELS + DEVICES
======================================

The tag_values table requires device_id NOT NULL — a legacy from the
Modbus-only days. To make OPC tags fit without schema gymnastics, the
POST /api/opc-sources endpoint auto-creates a backing channel + device
when an OPC source is created:

  POST /api/opc-sources  (name=Plant-A-UA, endpoint=opc.tcp://...)
    │
    ├─ INSERT channels  (name="opc:Plant-A-UA", protocol_connector="opc_ua")
    │       │
    │       └─ INSERT devices  (name="opc:Plant-A-UA", protocol="opc_ua",
    │              │              channel_id=^^^, scan_interval_ms=publishing_interval_ms)
    │              │
    │              └─ INSERT opc_sources  (channel_id=^^^, device_id=^^^, ...)
    │
    └─ Return the full bundle to the caller.

Subsequent POST /api/opc-sources/{id}/mappings creates an INDUVISTA
tag (with the synthetic device_id) and links it to the OPC NodeId.

The synthetic channel + device are visible in the existing channels/
devices endpoints — operators see "opc:Plant-A-UA" in their device
list. That's intentional: from the data-flow perspective, an OPC
source IS a data source, conceptually the same as a Modbus device.

CASCADE
=======

DELETE /api/opc-sources/{id} drops the opc_source row, which the FK
constraints CASCADE to: synthetic device → synthetic channel → tags
linked to the device → tag_values for those tags. The operator is
warned in the response body that data will be lost. To preserve data,
they should PATCH is_enabled=false instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


router = APIRouter(prefix="/api/opc-sources", tags=["opc"])


# ── Pydantic schemas ─────────────────────────────────────────────────

SecurityPolicy = Literal[
    "None", "Basic128Rsa15", "Basic256", "Basic256Sha256",
    "Aes128_Sha256_RsaOaep", "Aes256_Sha256_RsaPss",
]


class OpcSourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    endpoint: str = Field(..., min_length=10, max_length=512,
                          description="opc.tcp://host:port/path")
    security_policy: SecurityPolicy = "None"
    username: str = Field("", max_length=128)
    password: str = Field("", max_length=256,
                          description="Plaintext for now. TODO Phase 21 encrypt at rest.")
    publishing_interval_ms: int = Field(1000, ge=50, le=60000)
    reconnect_min_sec: float = Field(1.0, gt=0)
    reconnect_max_sec: float = Field(60.0, gt=0)
    is_enabled: bool = True


class OpcSourceUpdate(BaseModel):
    description: str | None = None
    endpoint: str | None = Field(None, min_length=10, max_length=512)
    security_policy: SecurityPolicy | None = None
    username: str | None = Field(None, max_length=128)
    password: str | None = Field(None, max_length=256)
    publishing_interval_ms: int | None = Field(None, ge=50, le=60000)
    reconnect_min_sec: float | None = Field(None, gt=0)
    reconnect_max_sec: float | None = Field(None, gt=0)
    is_enabled: bool | None = None


class OpcSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None
    endpoint: str
    security_policy: str
    username: str
    # Password NEVER returned in GET responses — security boundary.
    publishing_interval_ms: int
    reconnect_min_sec: float
    reconnect_max_sec: float
    is_enabled: bool
    channel_id: int
    device_id: int
    created_at: datetime
    updated_at: datetime
    mapping_count: int = 0


class OpcMappingCreate(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=512,
                         description="OPC NodeId, e.g. 'ns=1;s=DoubleValue'")
    # Tag creation parameters — the API auto-creates an INDUVISTA tag
    # bound to the synthetic device + register_block=NULL.
    tag_name: str = Field(..., min_length=1, max_length=200)
    tag_description: str | None = None
    data_type: str = Field(..., description="float64/float32/int32/int64/int16/uint16/uint32/uint64/string/bool")
    engineering_unit: str | None = Field(None, max_length=64)
    decimal_places: int | None = Field(None, ge=0, le=15)
    scale: float = 1.0
    offset: float = 0.0
    min_value: float | None = None
    max_value: float | None = None


class OpcMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    opc_source_id: int
    node_id: str
    tag_id: int
    tag_name: str
    data_type: str
    created_at: datetime


# ── Helpers ──────────────────────────────────────────────────────────


def _synthetic_resource_name(opc_source_name: str) -> str:
    """The channel + device names we synthesize. Prefix `opc:` makes
    them visually distinct in the existing channels/devices lists."""
    return f"opc:{opc_source_name}"


def _load_source_response(db: Session, source_id: int) -> OpcSourceResponse:
    """Read one opc_source row + mapping count + return as the response
    shape. Raises 404 if not found. Centralizes the JOIN so every
    endpoint returns the same shape."""
    row = db.execute(text("""
        SELECT s.id, s.name, s.description, s.endpoint, s.security_policy,
               s.username, s.publishing_interval_ms, s.reconnect_min_sec,
               s.reconnect_max_sec, s.is_enabled, s.channel_id, s.device_id,
               s.created_at, s.updated_at,
               COALESCE((SELECT COUNT(*) FROM opc_tag_mappings m
                         WHERE m.opc_source_id = s.id), 0) AS mapping_count
        FROM opc_sources s WHERE s.id = :id
    """), {"id": source_id}).mappings().first()
    if row is None:
        raise HTTPException(404, f"OPC source id={source_id} not found")
    return OpcSourceResponse(**dict(row))


# ── Endpoints: opc_sources CRUD ──────────────────────────────────────


@router.post("", response_model=OpcSourceResponse,
             status_code=status.HTTP_201_CREATED)
def create_opc_source(
    body: OpcSourceCreate,
    db: Annotated[Session, Depends(get_session)],
):
    """Create an OPC UA source. Atomically creates the backing
    channel + device too — all three rows commit together or the
    whole operation aborts. The caller never has to think about
    the synthetic resources."""

    # Reject duplicate names up front (the unique index would catch
    # it anyway, but a clean 409 is friendlier than a 500).
    existing = db.execute(
        text("SELECT id FROM opc_sources WHERE name = :n"),
        {"n": body.name},
    ).scalar()
    if existing is not None:
        raise HTTPException(409, f"OPC source named {body.name!r} already exists")

    synth_name = _synthetic_resource_name(body.name)

    # Resolve / create the opc_ua protocol_connector. Matches the
    # idempotent upsert pattern used by app/seed.py and channels.py's
    # POST handler — one round-trip whether the row exists or not.
    # Columns are (code, name, description); the baseline pre-widened
    # the code allow-list to include 'opc_ua'.
    pc_id = db.execute(text("""
        INSERT INTO protocol_connectors (code, name, description)
        VALUES ('opc_ua', 'OPC UA', 'OPC Unified Architecture protocol connector')
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """)).scalar_one()

    # ── synthetic channel ────────────────────────────────────────
    channel_id = db.execute(text("""
        INSERT INTO channels (name, description, transport,
                              protocol_connector_id, enabled,
                              created_at, updated_at)
        VALUES (:name, :desc, 'tcp', :pc, TRUE, now(), now())
        RETURNING id
    """), {
        "name": synth_name,
        "desc": f"Synthetic channel for OPC UA source {body.name!r}. "
                f"Created automatically — do not edit.",
        "pc": pc_id,
    }).scalar()

    # ── synthetic device ─────────────────────────────────────────
    # devices has many columns; we set only what's strictly needed
    # and let the server defaults cover the rest. The Modbus-specific
    # fields (host/port/unit_id) are left NULL because an OPC source's
    # connection details live in opc_sources, not here.
    device_id = db.execute(text("""
        INSERT INTO devices (channel_id, name, description, protocol,
                             host, port, unit_id, scan_interval_ms,
                             duty_role, enabled, created_at, updated_at)
        VALUES (:channel_id, :name, :desc, 'opc_ua',
                NULL, NULL, NULL, :scan,
                'none', TRUE, now(), now())
        RETURNING id
    """), {
        "channel_id": channel_id,
        "name": synth_name,
        "desc": f"Synthetic device for OPC UA source {body.name!r}. "
                f"All connection details live in opc_sources; this row "
                f"exists only to satisfy tag_values.device_id NOT NULL.",
        "scan": body.publishing_interval_ms,
    }).scalar()

    # ── the opc_source row itself ────────────────────────────────
    source_id = db.execute(text("""
        INSERT INTO opc_sources (
            name, description, endpoint, security_policy,
            username, password, publishing_interval_ms,
            reconnect_min_sec, reconnect_max_sec, is_enabled,
            channel_id, device_id, created_at, updated_at
        ) VALUES (
            :name, :description, :endpoint, :security_policy,
            :username, :password, :pim, :rmin, :rmax, :enabled,
            :ch, :dev, now(), now()
        )
        RETURNING id
    """), {
        "name": body.name,
        "description": body.description,
        "endpoint": body.endpoint,
        "security_policy": body.security_policy,
        "username": body.username,
        "password": body.password,
        "pim": body.publishing_interval_ms,
        "rmin": body.reconnect_min_sec,
        "rmax": body.reconnect_max_sec,
        "enabled": body.is_enabled,
        "ch": channel_id,
        "dev": device_id,
    }).scalar()

    db.commit()
    return _load_source_response(db, source_id)


@router.get("", response_model=list[OpcSourceResponse])
def list_opc_sources(
    db: Annotated[Session, Depends(get_session)],
):
    rows = db.execute(text("""
        SELECT s.id, s.name, s.description, s.endpoint, s.security_policy,
               s.username, s.publishing_interval_ms, s.reconnect_min_sec,
               s.reconnect_max_sec, s.is_enabled, s.channel_id, s.device_id,
               s.created_at, s.updated_at,
               COALESCE((SELECT COUNT(*) FROM opc_tag_mappings m
                         WHERE m.opc_source_id = s.id), 0) AS mapping_count
        FROM opc_sources s
        ORDER BY s.name
    """)).mappings().all()
    return [OpcSourceResponse(**dict(r)) for r in rows]


@router.get("/{source_id}", response_model=OpcSourceResponse)
def get_opc_source(
    source_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    return _load_source_response(db, source_id)


@router.patch("/{source_id}", response_model=OpcSourceResponse)
def update_opc_source(
    source_id: int,
    body: OpcSourceUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    # Ensure the source exists (load+discard pattern keeps the 404
    # path identical to GET).
    _load_source_response(db, source_id)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields supplied to update")

    # Cross-field validity: if both reconnect bounds change, the
    # database's CHECK will catch an invalid pair, but we'd rather
    # return a 422 than a 500.
    if "reconnect_min_sec" in updates and "reconnect_max_sec" in updates:
        if updates["reconnect_max_sec"] < updates["reconnect_min_sec"]:
            raise HTTPException(422, "reconnect_max_sec must be >= reconnect_min_sec")

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = source_id
    db.execute(
        text(f"""UPDATE opc_sources
                 SET {set_clauses}, updated_at = now()
                 WHERE id = :id"""),
        updates,
    )
    db.commit()
    return _load_source_response(db, source_id)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_opc_source(
    source_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Delete the source. Cascades drop the synthetic device, channel,
    mapped tags, and their tag_values rows. To keep data, PATCH
    is_enabled=false instead."""
    src = db.execute(
        text("SELECT channel_id, device_id FROM opc_sources WHERE id = :id"),
        {"id": source_id},
    ).mappings().first()
    if src is None:
        raise HTTPException(404, f"OPC source id={source_id} not found")

    # Delete the channel — CASCADE drops device, mappings, tags, etc.
    # We delete the channel rather than the opc_source because the
    # synthetic channel has no purpose without the source, and we
    # want everything gone in one shot.
    db.execute(text("DELETE FROM channels WHERE id = :id"),
               {"id": src["channel_id"]})
    db.commit()


# ── Endpoints: mappings CRUD ─────────────────────────────────────────


@router.post(
    "/{source_id}/mappings",
    response_model=OpcMappingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_mapping(
    source_id: int,
    body: OpcMappingCreate,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a mapping. Auto-creates an INDUVISTA tag with the
    synthetic device_id from the opc_source."""
    src = db.execute(text("""
        SELECT device_id FROM opc_sources WHERE id = :id
    """), {"id": source_id}).mappings().first()
    if src is None:
        raise HTTPException(404, f"OPC source id={source_id} not found")

    # Reject duplicate (source, node_id) up front.
    dup = db.execute(text("""
        SELECT id FROM opc_tag_mappings
        WHERE opc_source_id = :s AND node_id = :n
    """), {"s": source_id, "n": body.node_id}).scalar()
    if dup is not None:
        raise HTTPException(
            409,
            f"node_id={body.node_id!r} is already mapped on this source",
        )

    # Create the tag. data_type/scale/offset etc. mirror the existing
    # Modbus tag creation, but we omit Modbus-only fields where the
    # column allows defaults (register_count defaults to 1; is_heartbeat
    # and writable default to FALSE). function_code=3 (Holding Registers)
    # and address=0 are placeholders — they're NOT NULL with no defaults,
    # and the OPC supervisor never reads them, but the Modbus CHECK
    # constraints require both to be valid values.
    #
    # `"offset"` MUST be double-quoted in this SQL — it's a PostgreSQL
    # reserved word (used in `SELECT ... OFFSET N`), and raw-SQL writers
    # have to escape it themselves. SQLAlchemy's ORM/migrations escape
    # automatically; we're using text() so we don't get that.
    tag_id = db.execute(text("""
        INSERT INTO tags (
            device_id, register_block_id, name, description, data_type,
            byte_order, function_code, address,
            engineering_unit, scale, "offset", min_value, max_value,
            decimal_places, created_at, updated_at
        ) VALUES (
            :device_id, NULL, :name, :desc, :dtype,
            'ABCD', 3, 0,
            :eu, :scale, :offset, :min_v, :max_v,
            :dp, now(), now()
        )
        RETURNING id
    """), {
        "device_id": src["device_id"],
        "name": body.tag_name,
        "desc": body.tag_description or f"OPC node {body.node_id}",
        "dtype": body.data_type,
        "eu": body.engineering_unit,
        "scale": body.scale,
        "offset": body.offset,
        "min_v": body.min_value,
        "max_v": body.max_value,
        "dp": body.decimal_places,
    }).scalar()

    # And the mapping row.
    mapping_id = db.execute(text("""
        INSERT INTO opc_tag_mappings
            (opc_source_id, node_id, tag_id, created_at)
        VALUES (:s, :n, :t, now())
        RETURNING id
    """), {"s": source_id, "n": body.node_id, "t": tag_id}).scalar()
    db.commit()

    row = db.execute(text("""
        SELECT m.id, m.opc_source_id, m.node_id, m.tag_id, m.created_at,
               t.name AS tag_name, t.data_type
        FROM opc_tag_mappings m
        JOIN tags t ON t.id = m.tag_id
        WHERE m.id = :id
    """), {"id": mapping_id}).mappings().first()
    return OpcMappingResponse(**dict(row))


@router.get(
    "/{source_id}/mappings",
    response_model=list[OpcMappingResponse],
)
def list_mappings(
    source_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    # 404 if the source doesn't exist (don't quietly return an empty
    # list for a typo'd id).
    exists = db.execute(
        text("SELECT 1 FROM opc_sources WHERE id = :id"),
        {"id": source_id},
    ).scalar()
    if exists is None:
        raise HTTPException(404, f"OPC source id={source_id} not found")

    rows = db.execute(text("""
        SELECT m.id, m.opc_source_id, m.node_id, m.tag_id, m.created_at,
               t.name AS tag_name, t.data_type
        FROM opc_tag_mappings m
        JOIN tags t ON t.id = m.tag_id
        WHERE m.opc_source_id = :s
        ORDER BY t.name
    """), {"s": source_id}).mappings().all()
    return [OpcMappingResponse(**dict(r)) for r in rows]


@router.delete(
    "/{source_id}/mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_mapping(
    source_id: int,
    mapping_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Drop the mapping AND the tag it points at. The tag is
    synthetic-OPC-only (no Modbus origin) so it can't outlive its
    mapping. Cascade through the FK on tag_id."""
    row = db.execute(text("""
        SELECT tag_id FROM opc_tag_mappings
        WHERE id = :mid AND opc_source_id = :sid
    """), {"mid": mapping_id, "sid": source_id}).mappings().first()
    if row is None:
        raise HTTPException(
            404,
            f"mapping id={mapping_id} on source id={source_id} not found",
        )

    # Delete the tag. The mapping row goes with it via ON DELETE CASCADE
    # on opc_tag_mappings.tag_id.
    db.execute(text("DELETE FROM tags WHERE id = :id"),
               {"id": row["tag_id"]})
    db.commit()
