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
    # Phase OPC-web.3 — most recent tag_value.time across any tag
    # bound to this source's synthetic device. Lets the frontend show
    # "● Live / ● Stale / ● Idle" state without a separate roundtrip.
    # NULL means no samples have ever landed for this source.
    last_sample_at: datetime | None = None


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


# Phase OPC-web.2.2 — browse + bulk import schemas.

class OpcBrowseNode(BaseModel):
    """One entry returned by the browse endpoint. Represents either a
    folder (Object node — clickable to expand) or a leaf variable
    (tickable for import).

    `is_mapped`: true when this NodeId is already present in
    opc_tag_mappings for the source. The UI greys out the checkbox.

    `is_system`: heuristic flag — true when the browse_name starts
    with `_` (Kepware-injected folders like _System, _Statistics) or
    when the NodeId is the UA-standard Server node (i=2253). The UI
    collapses these by default behind a 'show system folders' toggle.

    `data_type` / `induvista_data_type` are only populated for
    Variable nodes. `data_type` is the raw UA type name (e.g.
    'Double', 'UInt32'); `induvista_data_type` is the mapped
    INDUVISTA type ('float64', 'uint32') so the UI can pre-fill the
    bulk import form."""

    node_id: str = Field(..., description="OPC NodeId string form, e.g. 'ns=2;s=CONDENSATE1.FLC1.MTR1.KPW_CUR_DAILY_GSVOL'")
    browse_name: str
    display_name: str
    node_class: Literal["Object", "Variable", "Method", "View", "DataType", "ReferenceType", "ObjectType", "VariableType", "Unspecified"]
    is_system: bool = False
    is_mapped: bool = False
    # Variable-only fields:
    data_type: str | None = None
    induvista_data_type: str | None = None


class OpcBrowseResponse(BaseModel):
    """Result of browsing one node. Returns its children plus the
    node's own NodeId for client-side cache keying."""
    parent_node_id: str
    children: list[OpcBrowseNode]


class OpcBulkMappingItem(BaseModel):
    """One row in the bulk-import request body."""
    node_id: str = Field(..., min_length=1, max_length=512)
    tag_name: str = Field(..., min_length=1, max_length=200)
    data_type: str = Field(..., description="INDUVISTA data type: float64/int32/etc.")
    tag_description: str | None = None
    engineering_unit: str | None = Field(None, max_length=64)
    decimal_places: int | None = Field(None, ge=0, le=15)


class OpcBulkMappingResult(BaseModel):
    """Per-row outcome from the bulk endpoint. `success=false` means
    the row was rejected (duplicate, validation error, etc.) — the
    rest of the batch was still attempted."""
    node_id: str
    tag_name: str
    success: bool
    mapping_id: int | None = None
    error: str | None = None


class OpcBulkMappingResponse(BaseModel):
    """Bulk endpoint return shape. Always 200 even on partial failure;
    the per-row results tell the UI what succeeded."""
    total: int
    succeeded: int
    failed: int
    results: list[OpcBulkMappingResult]


class OpcBulkMappingRequest(BaseModel):
    """Wrapper around the items list. Matches the existing
    /register-blocks/bulk request shape (`{ blocks: [...] }`) so all
    bulk endpoints have the same body convention. Leaves room to add
    request-level options later (dry_run, commit_mode, etc.) without
    breaking the contract."""
    items: list[OpcBulkMappingItem] = Field(..., min_length=1, max_length=500)


# ── Helpers ──────────────────────────────────────────────────────────


def _synthetic_resource_name(opc_source_name: str) -> str:
    """The channel + device names we synthesize. Prefix `opc:` makes
    them visually distinct in the existing channels/devices lists."""
    return f"opc:{opc_source_name}"


def _load_source_response(db: Session, source_id: int) -> OpcSourceResponse:
    """Read one opc_source row + mapping count + last_sample_at, return
    as the response shape. Raises 404 if not found. Centralizes the
    JOIN so every endpoint returns the same shape.

    Phase OPC-web.2.1 hotfix: last_sample_at originally read from the
    tag_values hypertable via tags.device_id. That JOIN scanned the
    entire hypertable per source, took minutes, hung the pool, and
    eventually exhausted it under the page's 5s polling cadence
    (12 hung connections observed in pg_stat_activity at the failure
    point). Switched to latest_tag_values (small table, one row per
    tag) joined directly via opc_tag_mappings — sub-millisecond, no
    hypertable scan, no synthetic-device indirection."""
    row = db.execute(text("""
        SELECT s.id, s.name, s.description, s.endpoint, s.security_policy,
               s.username, s.publishing_interval_ms, s.reconnect_min_sec,
               s.reconnect_max_sec, s.is_enabled, s.channel_id, s.device_id,
               s.created_at, s.updated_at,
               COALESCE((SELECT COUNT(*) FROM opc_tag_mappings m
                         WHERE m.opc_source_id = s.id), 0) AS mapping_count,
               (SELECT MAX(ltv.time)
                FROM latest_tag_values ltv
                JOIN opc_tag_mappings m ON ltv.tag_id = m.tag_id
                WHERE m.opc_source_id = s.id) AS last_sample_at
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
    # Phase OPC-web.2.1 hotfix: see _load_source_response docstring above
    # for why last_sample_at reads from latest_tag_values rather than
    # the tag_values hypertable.
    rows = db.execute(text("""
        SELECT s.id, s.name, s.description, s.endpoint, s.security_policy,
               s.username, s.publishing_interval_ms, s.reconnect_min_sec,
               s.reconnect_max_sec, s.is_enabled, s.channel_id, s.device_id,
               s.created_at, s.updated_at,
               COALESCE((SELECT COUNT(*) FROM opc_tag_mappings m
                         WHERE m.opc_source_id = s.id), 0) AS mapping_count,
               (SELECT MAX(ltv.time)
                FROM latest_tag_values ltv
                JOIN opc_tag_mappings m ON ltv.tag_id = m.tag_id
                WHERE m.opc_source_id = s.id) AS last_sample_at
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
    """Delete the source plus its synthetic channel, synthetic device,
    all opc_tag_mappings, all mapped tags, and all sample history for
    those tags. To stop sampling without losing history, PATCH
    is_enabled=false instead.

    Phase OPC-web.2.1.b hotfix: the original handler did a single
    DELETE FROM channels WHERE id = :id and relied on CASCADE. But
    the devices.channel_id foreign key was never given ON DELETE
    CASCADE in any migration, so Postgres rejected the parent
    delete with a ForeignKeyViolation. Until we ship a migration
    that adds the cascade, the handler walks the dependency graph
    explicitly: mappings → tag_values (via the tag IDs) → tags →
    opc_sources row → device → channel.
    """
    src = db.execute(
        text("SELECT channel_id, device_id FROM opc_sources WHERE id = :id"),
        {"id": source_id},
    ).mappings().first()
    if src is None:
        raise HTTPException(404, f"OPC source id={source_id} not found")

    channel_id = src["channel_id"]
    device_id = src["device_id"]

    # Snapshot the tag IDs owned by this source's synthetic device.
    # We grab them via the device_id rather than the mappings table so
    # that any tag created against this device — even one whose mapping
    # was deleted earlier — is included. tag_values rows for these tags
    # are dropped by the tags table's ON DELETE CASCADE.
    tag_ids = [
        r["id"]
        for r in db.execute(
            text("SELECT id FROM tags WHERE device_id = :did"),
            {"did": device_id},
        ).mappings().all()
    ]

    # 1. opc_tag_mappings — references opc_sources(id) AND tags(id).
    #    Drop these first so the tag deletes below don't trip the
    #    mappings_tag_id_fkey.
    db.execute(
        text("DELETE FROM opc_tag_mappings WHERE opc_source_id = :sid"),
        {"sid": source_id},
    )

    # 2. tags — drops tag_values rows via cascade. latest_tag_values
    #    has ON DELETE CASCADE on tag_id (migration 0050), and
    #    tag_values is a hypertable with the same cascade. If a tag
    #    is referenced by something else (alarm rule, computed
    #    definition input, etc.), the FK there decides whether to
    #    block or cascade — we propagate any error to the caller.
    if tag_ids:
        # Soft-delete: UPDATE instead of DELETE. Cascade-DELETE on
        # tag_values (33GB hypertable) was the cause of pool
        # exhaustion in Phase OPC-web.2.2.b. See migration 0053.
        db.execute(
            text("UPDATE tags SET deleted_at = NOW() "
                 "WHERE id = ANY(:ids) AND deleted_at IS NULL"),
            {"ids": tag_ids},
        )

    # 3. opc_sources row itself. Note: opc_sources.device_id and
    #    opc_sources.channel_id are ON DELETE CASCADE pointing AT the
    #    parent, meaning deleting the device or channel would cascade
    #    to delete this row — but we want explicit control over order
    #    so we delete the source row first and then the device/channel
    #    in known-safe order below. If the row was already gone via
    #    some other cascade path, the DELETE is a no-op.
    db.execute(
        text("DELETE FROM opc_sources WHERE id = :sid"),
        {"sid": source_id},
    )

    # 4. device — soft-delete. Hard DELETE here cascades to all tags
    #    via tags.device_id FK, which in turn cascades to tag_values
    #    (hypertable, 33GB). See migration 0054 for why this is
    #    soft-delete now. The synthetic device is single-use; rows
    #    pointing at it (already-soft-deleted tags) stay intact.
    db.execute(
        text("UPDATE devices SET deleted_at = NOW() "
             "WHERE id = :did AND deleted_at IS NULL"),
        {"did": device_id},
    )

    # 5. channel — left as hard DELETE; the channels table has no
    #    hypertable cascades and no other devices point at it (the
    #    synthetic channel is 1:1 with its source). Safe to DELETE
    #    physically. If this becomes a problem in future, add
    #    deleted_at to channels too.
    # 5. channel - LEFT IN PLACE (was hard-delete, now obsolete).
    #    Stage 3 made devices a soft-delete. The synthetic device row
    #    still references this channel via channel_id FK, so DELETE
    #    here would raise ForeignKeyViolation. The orphan channel row
    #    is 1 row, no operational cost. A future cleanup migration can
    #    sweep orphans (channels with no active device) if needed.
    _ = channel_id  # silence unused-variable lint; kept for future cleanup

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

    # Phase OPC-web.2.1 — bump the source's updated_at so the worker's
    # config_reloader fingerprint changes and the source's subscription
    # is restarted to pick up the new node. The fingerprint ALSO tracks
    # mapping_count + last_mapping_change as a backstop, but bumping
    # updated_at keeps the contract "any config change touches the row".
    db.execute(
        text("UPDATE opc_sources SET updated_at = now() WHERE id = :id"),
        {"id": source_id},
    )
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

    # Phase OPC-web.2.2.b: soft-delete the tag. Since tags is no
    # longer being DELETE'd, the opc_tag_mappings.tag_id CASCADE
    # FK won't fire — we explicitly remove the mapping row first.
    db.execute(text("DELETE FROM opc_tag_mappings WHERE id = :mid"),
               {"mid": mapping_id})
    db.execute(text("UPDATE tags SET deleted_at = NOW() "
                    "WHERE id = :id AND deleted_at IS NULL"),
               {"id": row["tag_id"]})

    # Phase OPC-web.2.1 — bump the source's updated_at so the worker's
    # config_reloader fingerprint changes and the source's subscription
    # is restarted with the now-shorter node list.
    db.execute(
        text("UPDATE opc_sources SET updated_at = now() WHERE id = :id"),
        {"id": source_id},
    )
    db.commit()


# ── Phase OPC-web.2.2: browse + bulk import ──────────────────────────


# UA scalar types (asyncua's ua.VariantType enum) → INDUVISTA data_type
# strings. Anything not in this map falls through to None → the UI lets
# the operator pick manually (rare path for exotic types).
_UA_TO_INDUVISTA_DTYPE: dict[str, str] = {
    # Numeric
    "Boolean":  "bool",
    "Byte":     "uint16",     # 8-bit unsigned → smallest INDUVISTA int
    "SByte":    "int16",      # 8-bit signed
    "Int16":    "int16",
    "UInt16":   "uint16",
    "Int32":    "int32",
    "UInt32":   "uint32",
    "Int64":    "int64",
    "UInt64":   "uint64",
    "Float":    "float32",
    "Double":   "float64",
    # Text
    "String":           "string",
    "DateTime":         "string",  # No native DateTime in INDUVISTA tags
    "ByteString":       "string",
    "LocalizedText":    "string",
    "QualifiedName":    "string",
    "XmlElement":       "string",
    "NodeId":           "string",
    "ExpandedNodeId":   "string",
    "StatusCode":       "uint32",
    "Guid":             "string",
}


def _ua_dtype_to_induvista(ua_name: str) -> str | None:
    """Map an asyncua VariantType enum name to an INDUVISTA data_type.
    Returns None for types we don't auto-map (caller can let the
    operator choose manually)."""
    return _UA_TO_INDUVISTA_DTYPE.get(ua_name)


def _is_system_folder(browse_name: str, nodeid_str: str) -> bool:
    """Heuristic for hiding Kepware/UA system folders by default.

    Triggers:
      - browse_name starts with '_' (Kepware convention for system
        folders: _System, _Statistics, _CommunicationSerialization,
        _AdvancedTags, _DataLogger, _IoT_Gateway, etc.)
      - NodeId is the UA-standard Server folder (i=2253), which sits
        in Objects alongside the Kepware project but is OPC UA spec
        infrastructure (server diagnostics, capabilities, etc.).
    """
    if nodeid_str == "i=2253":
        return True
    return browse_name.startswith("_")


@router.get(
    "/{source_id}/browse",
    response_model=OpcBrowseResponse,
)
async def browse_opc_node(
    source_id: int,
    db: Annotated[Session, Depends(get_session)],
    node_id: str = "ObjectsFolder",
):
    """Browse one node on an OPC source's server. Returns the node's
    children with metadata (NodeClass, data type for Variables,
    is_system flag, is_mapped flag).

    `node_id` defaults to 'ObjectsFolder' which is asyncua's name for
    the UA-standard Objects folder root (`i=85`). Subsequent calls
    pass an explicit node_id like 'ns=2;s=CONDENSATE1' to drill down.

    Spawns a short-lived asyncua client per request. This is
    deliberately separate from the worker's long-lived subscription
    client — Kepware and most servers handle concurrent sessions
    fine, and the alternative (RPC to the worker) is much more
    architectural churn for negligible benefit. The request returns
    in <500ms typical, ~1s for nodes with many children due to
    per-Variable data_type reads.

    NOTE: this handler is `async def` deliberately. An earlier draft
    used a sync handler with `asyncio.run(_do_browse())`, which created
    and destroyed an event loop per request. That worked for isolated
    curl tests but produced BadNodeIdUnknown errors when the React UI
    issued 3-4 browse calls in rapid succession — asyncua's background
    tasks (KeepAlive watchdog, channel publish) didn't unwind cleanly
    during the brief loop-teardown window, leaving Kepware with
    half-closed sessions that misrouted subsequent NodeId reads.
    Running on FastAPI's main loop avoids the churn.
    """
    from asyncua import Client, ua
    import asyncio as _asyncio

    # Look up the source's endpoint + security to build the client.
    # We run the SQL via asyncio.to_thread because get_session() yields
    # a synchronous SQLAlchemy Session. Without to_thread, db.execute()
    # blocks the FastAPI event loop, which means under concurrent load
    # the loop can stall and asyncio.timeout below never gets to fire.
    # Symptoms: browse requests hang forever, frontend stuck on
    # "Loading...", connection pool exhausted from queued requests.
    def _fetch_source():
        return db.execute(text("""
            SELECT id, name, endpoint, security_policy, username, password
            FROM opc_sources WHERE id = :id
        """), {"id": source_id}).mappings().first()

    src = await _asyncio.to_thread(_fetch_source)
    if src is None:
        raise HTTPException(404, f"OPC source id={source_id} not found")

    # Pre-fetch the set of already-mapped NodeIds for this source so we
    # can flag children that have an existing mapping. Also off-loop.
    def _fetch_mapped():
        return set(
            r["node_id"]
            for r in db.execute(text("""
                SELECT node_id FROM opc_tag_mappings WHERE opc_source_id = :s
            """), {"s": source_id}).mappings().all()
        )

    already_mapped: set[str] = await _asyncio.to_thread(_fetch_mapped)

    # CRITICAL: wrap the entire connect+browse phase in a single timeout.
    # Without this, asyncua's connect() can hang indefinitely on a
    # misbehaving OPC server (Kepware busy, network blip, certificate
    # mismatch). A hung handler holds its DB connection forever, and
    # if multiple browse requests pile up the entire connection pool
    # gets depleted - so even cheap /api/opc-sources list calls time
    # out. 8 seconds is generous for legitimate OPC traffic and short
    # enough that pool depletion can't cascade.
    BROWSE_TIMEOUT_SEC = 8.0
    client = None
    try:
        async with _asyncio.timeout(BROWSE_TIMEOUT_SEC):
            client = Client(src["endpoint"], timeout=5)
            if src["security_policy"] and src["security_policy"] != "None":
                # Match the worker's security setup. The worker's TODO Phase
                # 21 (encrypt at rest) applies here too - for now we read the
                # plaintext password from the row.
                await client.set_security_string(
                    f"{src['security_policy']},SignAndEncrypt,"
                    f"/data/certs/client_cert.der,/data/certs/client_key.pem"
                )
            if src["username"]:
                client.set_user(src["username"])
                client.set_password(src["password"] or "")

            await client.connect()

            # Translate 'ObjectsFolder' shorthand -> real Objects node
            if node_id == "ObjectsFolder":
                target = client.get_objects_node()
            else:
                target = client.get_node(node_id)

            children = await target.get_children()

            out: list[OpcBrowseNode] = []
            for c in children:
                try:
                    bn = await c.read_browse_name()
                    nc = await c.read_node_class()
                    try:
                        dn_obj = await c.read_display_name()
                        dn = dn_obj.Text or bn.Name
                    except Exception:
                        dn = bn.Name

                    nodeid_str = c.nodeid.to_string()
                    is_sys = _is_system_folder(bn.Name, nodeid_str)
                    is_mapped = nodeid_str in already_mapped

                    # Variable nodes: also read the DataType so the UI
                    # can pre-fill the bulk-import form.
                    raw_dtype: str | None = None
                    ind_dtype: str | None = None
                    if nc == ua.NodeClass.Variable:
                        try:
                            vt = await c.read_data_type_as_variant_type()
                            raw_dtype = vt.name
                            ind_dtype = _ua_dtype_to_induvista(raw_dtype)
                        except Exception:
                            pass

                    out.append(OpcBrowseNode(
                        node_id=nodeid_str,
                        browse_name=bn.Name,
                        display_name=dn,
                        node_class=nc.name,
                        is_system=is_sys,
                        is_mapped=is_mapped,
                        data_type=raw_dtype,
                        induvista_data_type=ind_dtype,
                    ))
                except Exception as e:
                    # One bad child shouldn't kill the whole browse.
                    # is_mapped is still computed from node_id (the only
                    # field that's reliably valid in the error path).
                    err_node_id = c.nodeid.to_string() if c.nodeid else "(unknown)"
                    out.append(OpcBrowseNode(
                        node_id=err_node_id,
                        browse_name=f"(browse error: {type(e).__name__})",
                        display_name=f"(browse error: {type(e).__name__})",
                        node_class="Unspecified",
                        is_system=True,
                        is_mapped=err_node_id in already_mapped,
                    ))

            # Sort: non-system folders first, then variables, then
            # system folders. Within each group, alphabetical by
            # browse_name.
            def _sort_key(n: OpcBrowseNode):
                return (
                    1 if n.is_system else 0,
                    0 if n.node_class == "Object" else 1,
                    n.browse_name.casefold(),
                )
            out.sort(key=_sort_key)
            return OpcBrowseResponse(parent_node_id=node_id, children=out)

    except TimeoutError:
        # asyncio.timeout fired. Most likely Kepware is overloaded or
        # the OPC connection got stuck mid-handshake. Don't 500 -
        # this is upstream stress, return 502 with a clear message.
        raise HTTPException(
            504,
            f"OPC browse timed out (>{BROWSE_TIMEOUT_SEC}s) for source "
            f"{src['name']!r}. The OPC server may be overloaded or "
            f"unreachable; try again in a moment.",
        )
    except HTTPException:
        # Re-raise our own HTTPExceptions cleanly (e.g. from invalid
        # input). Don't wrap them in another 502.
        raise
    except Exception as e:
        raise HTTPException(
            502,
            f"OPC browse failed for source {src['name']!r}: "
            f"{type(e).__name__}: {e}",
        )
    finally:
        # Always disconnect, even on timeout. This frees Kepware's
        # session slot for the next request.
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        # Brief cooldown to let Kepware finish releasing the session
        # before the next browse call opens a new one. asyncua's
        # disconnect() sends CloseSession but doesn't wait for ack.
        # 100ms is enough for normal pacing; the BROWSE_TIMEOUT_SEC
        # wrapper above handles the case where this isn't enough.
        await _asyncio.sleep(0.1)


@router.post(
    "/{source_id}/mappings/bulk",
    response_model=OpcBulkMappingResponse,
)
def bulk_create_mappings(
    source_id: int,
    body: OpcBulkMappingRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Create many mappings in one request. Best-effort: each row is
    committed in its own savepoint so a single failure (duplicate
    NodeId, invalid data_type, tag name conflict) doesn't roll back
    the others. Returns a per-row result.

    The endpoint bumps opc_sources.updated_at once at the end if at
    least one row succeeded — the worker's reloader picks up the
    full new mapping set on its next poll (~30s).

    Request shape: { "items": [ {node_id, tag_name, data_type, ...}, ... ] }
    matching the existing /register-blocks/bulk convention.
    """
    items = body.items

    src = db.execute(text("""
        SELECT device_id, name FROM opc_sources WHERE id = :id
    """), {"id": source_id}).mappings().first()
    if src is None:
        raise HTTPException(404, f"OPC source id={source_id} not found")

    # min_length=1 on the Pydantic model already rejects empty arrays
    # at validation time, so this guard is defensive only.
    if not items:
        return OpcBulkMappingResponse(total=0, succeeded=0, failed=0, results=[])

    # max_length=500 on the Pydantic model rejects oversized batches at
    # validation. The 413 below is unreachable from the API but stays
    # as documentation of the soft cap.

    if len(items) > 500:
        # Soft cap. The endpoint can handle thousands but bulk requests
        # that large usually indicate a misclick or runaway script —
        # better to fail fast and have the operator confirm than to
        # silently create 5000 tags.
        raise HTTPException(
            413,
            f"Bulk request too large ({len(items)} items, max 500). "
            f"Split into smaller batches.",
        )

    # Pre-fetch existing mappings + tag names once for fast dup detection.
    existing_node_ids: set[str] = set(
        r["node_id"]
        for r in db.execute(text("""
            SELECT node_id FROM opc_tag_mappings WHERE opc_source_id = :s
        """), {"s": source_id}).mappings().all()
    )
    # tag names are unique across the WHOLE tags table (global UNIQUE
    # constraint). Pre-fetch only the relevant slice — names that
    # match any of our incoming candidates — so we can flag clashes
    # cleanly. Empty IN () is invalid SQL, hence the guard.
    requested_names = [it.tag_name for it in items]
    existing_tag_names: set[str] = set()
    if requested_names:
        existing_tag_names = set(
            r["name"]
            for r in db.execute(
                text("SELECT name FROM tags WHERE name = ANY(:names)"),
                {"names": requested_names},
            ).mappings().all()
        )

    results: list[OpcBulkMappingResult] = []
    succeeded = 0
    failed = 0

    for item in items:
        # ── Per-row validation (cheap, before SAVEPOINT) ────────────
        if item.node_id in existing_node_ids:
            results.append(OpcBulkMappingResult(
                node_id=item.node_id, tag_name=item.tag_name,
                success=False,
                error=f"node_id already mapped on this source",
            ))
            failed += 1
            continue
        if item.tag_name in existing_tag_names:
            results.append(OpcBulkMappingResult(
                node_id=item.node_id, tag_name=item.tag_name,
                success=False,
                error=f"tag name {item.tag_name!r} already exists",
            ))
            failed += 1
            continue

        # ── SAVEPOINT-wrapped insert ────────────────────────────────
        # Each row gets its own savepoint so a failure (FK violation,
        # CHECK constraint, etc.) is rolled back to just that row
        # without losing the others. We hold one open transaction
        # for the whole request and commit at the end.
        sp = db.begin_nested()  # SAVEPOINT
        try:
            tag_id = db.execute(text("""
                INSERT INTO tags (
                    device_id, register_block_id, name, description, data_type,
                    byte_order, function_code, address,
                    engineering_unit, scale, "offset", min_value, max_value,
                    decimal_places, created_at, updated_at
                ) VALUES (
                    :device_id, NULL, :name, :desc, :dtype,
                    'ABCD', 3, 0,
                    :eu, 1.0, 0.0, NULL, NULL,
                    :dp, now(), now()
                )
                RETURNING id
            """), {
                "device_id": src["device_id"],
                "name": item.tag_name,
                "desc": item.tag_description or f"OPC node {item.node_id}",
                "dtype": item.data_type,
                "eu": item.engineering_unit,
                "dp": item.decimal_places,
            }).scalar()

            mapping_id = db.execute(text("""
                INSERT INTO opc_tag_mappings
                    (opc_source_id, node_id, tag_id, created_at)
                VALUES (:s, :n, :t, now())
                RETURNING id
            """), {"s": source_id, "n": item.node_id, "t": tag_id}).scalar()

            sp.commit()
            # Track these so duplicates within the same request batch
            # also fail cleanly (instead of going through to Postgres
            # and getting a UNIQUE violation that's harder to explain).
            existing_node_ids.add(item.node_id)
            existing_tag_names.add(item.tag_name)

            results.append(OpcBulkMappingResult(
                node_id=item.node_id, tag_name=item.tag_name,
                success=True, mapping_id=mapping_id,
            ))
            succeeded += 1

        except Exception as e:
            sp.rollback()
            # Surface the DB error in a developer-readable form. CHECK
            # constraint violations and FK errors aren't always pretty
            # but they tell the operator what's wrong.
            results.append(OpcBulkMappingResult(
                node_id=item.node_id, tag_name=item.tag_name,
                success=False,
                error=f"{type(e).__name__}: {str(e)[:200]}",
            ))
            failed += 1

    # Bump source updated_at IFF anything succeeded — so the reloader
    # rebuilds the subscription. If everything failed, don't perturb.
    if succeeded > 0:
        db.execute(
            text("UPDATE opc_sources SET updated_at = now() WHERE id = :id"),
            {"id": source_id},
        )

    db.commit()
    return OpcBulkMappingResponse(
        total=len(items),
        succeeded=succeeded,
        failed=failed,
        results=results,
    )
