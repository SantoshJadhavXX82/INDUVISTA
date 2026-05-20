"""Phase 17.0b - CRUD endpoints for Computed Tags with dual output mode.

A "computed tag" is a composite resource:
  - A row in `tags` table (with device_id pointing to a protocol='computed' device)
  - A row in `computed_tags` table with the same id, carrying the calc definition

Both rows are created/updated/deleted in one transaction. The frontend sees
ONE flat object with merged fields from both tables.

Phase 17.0b adds `output_tag_id`:
  - NULL (default, "internal mode"): calc writes to its own anchor tag
  - NOT NULL ("external mode"): calc writes to the referenced external tag.
    The internal anchor row exists for metadata; it receives no values.

Validation rules for output_tag_id (enforced here, since Migration 0043's
schema only enforces the basic FK + self-reference check):
  - Target tag must exist
  - Target tag's device must NOT be protocol='computed' (no chaining in v1)
  - Target tag must not already be another calc's output (unique index)
  - Target tag's id must not equal this computed_tag's id (CHECK)

Audited actions (unchanged from 17.0a):
  computed_tag.create
  computed_tag.update    (multi-field PATCH, or non-enabled single-field)
  computed_tag.toggle    (single-field PATCH on `enabled`)
  computed_tag.delete    (cascade via tags.id DELETE)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api/computed-tags", tags=["computed-tags"])


ALLOWED_EXECUTION_RATES_MS = (
    100, 250, 500, 1000, 5000, 10000, 30000, 60000, 300000, 900000, 3600000,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ComputedTagCreate(BaseModel):
    # Tag-level fields
    device_id: int = Field(..., description="Must be a device with protocol='computed'")
    name: str = Field(..., min_length=1, max_length=128)
    data_type: str = Field(...)
    description: str | None = None
    engineering_unit_id: int | None = None
    engineering_unit: str | None = Field(None, max_length=32)
    named_set_id: int | None = None
    min_value: float | None = None
    max_value: float | None = None
    # Computed-tags fields
    block_type: str = Field(..., max_length=64)
    block_config: dict = Field(default_factory=dict)
    execution_rate_ms: int = Field(1000)
    enabled: bool = True
    # Phase 17.0b: external output target (None = internal mode)
    output_tag_id: int | None = Field(
        None,
        description="If set, calc writes to this external tag instead of the "
                    "internal anchor. Tag's device must not be 'computed'.",
    )


class ComputedTagUpdate(BaseModel):
    # Tag-level
    name: str | None = Field(None, min_length=1, max_length=128)
    data_type: str | None = None
    description: str | None = None
    engineering_unit_id: int | None = None
    engineering_unit: str | None = Field(None, max_length=32)
    named_set_id: int | None = None
    min_value: float | None = None
    max_value: float | None = None
    # Computed-tags
    block_type: str | None = Field(None, max_length=64)
    block_config: dict | None = None
    execution_rate_ms: int | None = None
    enabled: bool | None = None
    # Phase 17.0b
    output_tag_id: int | None = None


class ComputedTagResponse(BaseModel):
    id: int
    device_id: int
    device_name: str
    name: str
    data_type: str
    description: str | None
    engineering_unit: str | None
    engineering_unit_id: int | None
    named_set_id: int | None
    min_value: float | None
    max_value: float | None
    block_type: str
    block_config: dict
    execution_rate_ms: int
    enabled: bool
    created_at: datetime
    updated_at: datetime
    # Execution stats
    last_executed_at: datetime | None = None
    last_duration_ms: float | None = None
    last_status: str | None = None
    last_error_message: str | None = None
    # Phase 17.0b: external output info
    output_tag_id: int | None = None
    output_tag_name: str | None = None
    output_device_id: int | None = None
    output_device_name: str | None = None


# Field partitioning - which body fields go to which table on UPDATE.
_TAG_FIELDS = {
    "name", "data_type", "description", "engineering_unit_id",
    "engineering_unit", "named_set_id", "min_value", "max_value",
}
_COMPUTED_FIELDS = {
    "block_type", "block_config", "execution_rate_ms", "enabled",
    "output_tag_id",
}


_SELECT = """
    SELECT
        t.id, t.device_id, d.name AS device_name,
        t.name, t.data_type, t.description,
        t.engineering_unit, t.engineering_unit_id, t.named_set_id,
        t.min_value, t.max_value,
        ct.block_type, ct.block_config, ct.execution_rate_ms,
        ct.enabled,
        ct.created_at, ct.updated_at,
        es.last_executed_at, es.last_duration_ms,
        es.last_status, es.last_error_message,
        ct.output_tag_id,
        ot.name AS output_tag_name,
        ot.device_id AS output_device_id,
        od.name AS output_device_name
    FROM computed_tags ct
    JOIN tags t ON t.id = ct.id
    JOIN devices d ON d.id = t.device_id
    LEFT JOIN computed_tag_execution_stats es ON es.id = ct.id
    LEFT JOIN tags ot ON ot.id = ct.output_tag_id
    LEFT JOIN devices od ON od.id = ot.device_id
"""


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _summarize_ct(row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "device_id": row.get("device_id"),
        "device_name": row.get("device_name"),
        "data_type": row.get("data_type"),
        "block_type": row.get("block_type"),
        "block_config": row.get("block_config"),
        "execution_rate_ms": row.get("execution_rate_ms"),
        "enabled": row.get("enabled"),
        "output_tag_id": row.get("output_tag_id"),
        "output_tag_name": row.get("output_tag_name"),
    }


def _full_ct(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "device_id": row.get("device_id"),
        "device_name": row.get("device_name"),
        "data_type": row.get("data_type"),
        "description": row.get("description"),
        "engineering_unit": row.get("engineering_unit"),
        "engineering_unit_id": row.get("engineering_unit_id"),
        "named_set_id": row.get("named_set_id"),
        "min_value": row.get("min_value"),
        "max_value": row.get("max_value"),
        "block_type": row.get("block_type"),
        "block_config": row.get("block_config"),
        "execution_rate_ms": row.get("execution_rate_ms"),
        "enabled": row.get("enabled"),
        "output_tag_id": row.get("output_tag_id"),
        "output_tag_name": row.get("output_tag_name"),
        "output_device_id": row.get("output_device_id"),
        "output_device_name": row.get("output_device_name"),
    }


# ---------------------------------------------------------------------------
# Output-tag validation (Phase 17.0b)
# ---------------------------------------------------------------------------


def _validate_output_tag(
    db: Session,
    output_tag_id: int | None,
    exclude_computed_tag_id: int | None = None,
) -> tuple[str | None, int | None, str | None]:
    """Validate a proposed output_tag_id.

    Returns (error_message, http_status_code, error_code_for_audit) - all
    None if valid (or if output_tag_id is None / unset).
    """
    if output_tag_id is None:
        return None, None, None

    row = db.execute(text("""
        SELECT t.id, t.name AS tag_name, t.device_id,
               d.protocol, d.name AS device_name
        FROM tags t
        JOIN devices d ON d.id = t.device_id
        WHERE t.id = :id
    """), {"id": output_tag_id}).mappings().first()

    if row is None:
        return (
            f"output_tag_id={output_tag_id} does not exist",
            404,
            "output_tag_not_found",
        )

    if row["protocol"] == "computed":
        return (
            f"output_tag_id={output_tag_id} ({row['tag_name']!r} on "
            f"{row['device_name']!r}) is a computed tag - chaining "
            f"computed-to-computed isn't supported in v1",
            400,
            "output_tag_is_computed",
        )

    # Already used as another calc's output?
    sql = "SELECT id FROM computed_tags WHERE output_tag_id = :id"
    params: dict = {"id": output_tag_id}
    if exclude_computed_tag_id is not None:
        sql += " AND id <> :exclude_id"
        params["exclude_id"] = exclude_computed_tag_id
    other = db.execute(text(sql), params).scalar()
    if other is not None:
        return (
            f"output_tag_id={output_tag_id} is already the output target "
            f"of computed tag id={other}",
            409,
            "output_tag_already_taken",
        )

    return None, None, None


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ComputedTagResponse])
def list_computed_tags(
    db: Annotated[Session, Depends(get_session)],
    device_id: int | None = Query(None, description="Filter to a single device"),
    enabled: bool | None = Query(None),
):
    sql = _SELECT + " WHERE TRUE"
    params: dict = {}
    if device_id is not None:
        sql += " AND t.device_id = :device_id"
        params["device_id"] = device_id
    if enabled is not None:
        sql += " AND ct.enabled = :enabled"
        params["enabled"] = enabled
    sql += " ORDER BY d.name, t.name"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{computed_tag_id}", response_model=ComputedTagResponse)
def get_computed_tag(
    computed_tag_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text(_SELECT + " WHERE ct.id = :id"),
        {"id": computed_tag_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"computed tag {computed_tag_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------


@router.post("", response_model=ComputedTagResponse, status_code=201)
def create_computed_tag(
    body: ComputedTagCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Atomic two-table create. Trigger from 0041 enforces device protocol;
    we surface trigger errors as 'denied'. Phase 17.0b: also validates
    output_tag_id before inserting."""
    target_label = body.name

    # execution_rate_ms validation
    if body.execution_rate_ms not in ALLOWED_EXECUTION_RATES_MS:
        audit(AuditEvent(
            action="computed_tag.create",
            target_type="computed_tag",
            target_label=target_label,
            summary=f"Denied: execution_rate_ms={body.execution_rate_ms} not in allowed values",
            status="denied",
            error_message=f"execution_rate_ms must be in {ALLOWED_EXECUTION_RATES_MS}",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(
            400,
            f"execution_rate_ms must be one of {list(ALLOWED_EXECUTION_RATES_MS)}",
        )

    # Phase 17.0b: output_tag_id validation
    err, status_code, code = _validate_output_tag(db, body.output_tag_id)
    if err:
        audit(AuditEvent(
            action="computed_tag.create",
            target_type="computed_tag",
            target_label=target_label,
            summary=f"Denied: {err}",
            status="denied",
            error_message=code or err,
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(status_code or 400, err)

    try:
        # 1. Insert into tags with sentinel values for Modbus-only fields.
        new_id = db.execute(text("""
            INSERT INTO tags (
                device_id, name, data_type, description,
                engineering_unit, engineering_unit_id, named_set_id,
                min_value, max_value,
                function_code, address, register_count, byte_order,
                enabled, writable, is_heartbeat
            )
            VALUES (
                :device_id, :name, :data_type, :description,
                :engineering_unit, :engineering_unit_id, :named_set_id,
                :min_value, :max_value,
                3, 0, 1, 'ABCD',
                :enabled, false, false
            )
            RETURNING id
        """), {
            "device_id": body.device_id,
            "name": body.name,
            "data_type": body.data_type,
            "description": body.description,
            "engineering_unit": body.engineering_unit,
            "engineering_unit_id": body.engineering_unit_id,
            "named_set_id": body.named_set_id,
            "min_value": body.min_value,
            "max_value": body.max_value,
            "enabled": body.enabled,
        }).scalar_one()

        # 2. Insert into computed_tags. Trigger validates device protocol here.
        db.execute(text("""
            INSERT INTO computed_tags (
                id, block_type, block_config, execution_rate_ms,
                enabled, output_tag_id
            )
            VALUES (
                :id, :block_type, CAST(:block_config AS jsonb),
                :execution_rate_ms, :enabled, :output_tag_id
            )
        """), {
            "id": new_id,
            "block_type": body.block_type,
            "block_config": json.dumps(body.block_config),
            "execution_rate_ms": body.execution_rate_ms,
            "enabled": body.enabled,
            "output_tag_id": body.output_tag_id,
        })

        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()

        # Trigger: device not protocol='computed'
        if "must live on a device with protocol='computed'" in msg \
           or "computed_tags must live" in str(e.orig):
            audit(AuditEvent(
                action="computed_tag.create",
                target_type="computed_tag",
                target_label=target_label,
                summary=f"Denied: device {body.device_id} is not a Computed Device",
                status="denied",
                error_message="device protocol is not 'computed'",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(
                400,
                f"device {body.device_id} is not a Computed Device "
                f"(protocol must be 'computed')",
            )

        # Phase 17.0b: partial unique index on output_tag_id
        if "ux_computed_tags_output_tag_id" in msg:
            audit(AuditEvent(
                action="computed_tag.create",
                target_type="computed_tag",
                target_label=target_label,
                summary=f"Denied: output_tag_id={body.output_tag_id} already in use (race)",
                status="denied",
                error_message="output_tag_id unique violation",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(
                409,
                f"output_tag_id={body.output_tag_id} is already another calc's output",
            )

        if "ck_computed_tags_no_self_externalize" in msg:
            audit(AuditEvent(
                action="computed_tag.create",
                target_type="computed_tag",
                target_label=target_label,
                summary="Denied: output_tag_id cannot equal own id",
                status="denied",
                error_message="self-externalize",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(400, "output_tag_id cannot equal the tag's own id")

        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action="computed_tag.create",
                target_type="computed_tag",
                target_label=target_label,
                summary=f"Denied: tag name '{body.name}' already exists on device {body.device_id}",
                status="denied",
                error_message="duplicate (device_id, name)",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(409, f"a tag named '{body.name}' already exists on device {body.device_id}")

        if "foreign key" in msg:
            audit(AuditEvent(
                action="computed_tag.create",
                target_type="computed_tag",
                target_label=target_label,
                summary="Denied: FK violation",
                status="denied",
                error_message=f"FK violation: {e.orig}",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(400, f"FK violation: {e.orig}")

        if "ck_tags_data_type" in msg:
            audit(AuditEvent(
                action="computed_tag.create",
                target_type="computed_tag",
                target_label=target_label,
                summary=f"Denied: data_type='{body.data_type}' is not valid",
                status="denied",
                error_message="invalid data_type",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(400, f"data_type '{body.data_type}' is not one of the allowed values")

        if "ck_tags_engineering_unit_exclusive" in msg:
            audit(AuditEvent(
                action="computed_tag.create",
                target_type="computed_tag",
                target_label=target_label,
                summary="Denied: engineering_unit and engineering_unit_id are mutually exclusive",
                status="denied",
                error_message="engineering_unit exclusivity",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(400, "set either engineering_unit or engineering_unit_id, not both")

        audit(AuditEvent(
            action="computed_tag.create",
            target_type="computed_tag",
            target_label=target_label,
            summary="INSERT failed (IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="computed_tag.create",
            target_type="computed_tag",
            target_label=target_label,
            summary="INSERT failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump()},
        ), request)
        raise

    output_note = (
        f", output→tag_id={body.output_tag_id}"
        if body.output_tag_id is not None
        else ""
    )
    audit(AuditEvent(
        action="computed_tag.create",
        target_type="computed_tag",
        target_id=new_id,
        target_label=target_label,
        summary=(
            f"Created computed tag '{body.name}' on device {body.device_id} "
            f"(block={body.block_type}, rate={body.execution_rate_ms}ms{output_note})"
        ),
        details=body.model_dump(),
    ), request)

    return get_computed_tag(new_id, db)


@router.patch("/{computed_tag_id}", response_model=ComputedTagResponse)
def update_computed_tag(
    computed_tag_id: int,
    body: ComputedTagUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "computed_tag.toggle" if is_toggle else "computed_tag.update"

    existing = db.execute(
        text(_SELECT + " WHERE ct.id = :id"),
        {"id": computed_tag_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="computed_tag",
            target_id=computed_tag_id,
            summary=f"Denied: computed tag {computed_tag_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"computed tag {computed_tag_id} not found")

    target_label = existing["name"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="computed_tag",
            target_id=computed_tag_id,
            target_label=target_label,
            summary="Denied: no fields to update",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "no fields to update")

    # execution_rate_ms validation
    if "execution_rate_ms" in updates and updates["execution_rate_ms"] not in ALLOWED_EXECUTION_RATES_MS:
        audit(AuditEvent(
            action=action,
            target_type="computed_tag",
            target_id=computed_tag_id,
            target_label=target_label,
            summary=f"Denied: execution_rate_ms={updates['execution_rate_ms']} not in allowed values",
            status="denied",
            error_message=f"execution_rate_ms must be in {ALLOWED_EXECUTION_RATES_MS}",
            details={"request": updates, "before": _summarize_ct(existing)},
        ), request)
        raise HTTPException(400, f"execution_rate_ms must be one of {list(ALLOWED_EXECUTION_RATES_MS)}")

    # Phase 17.0b: output_tag_id validation if changed
    if "output_tag_id" in updates:
        err, status_code, code = _validate_output_tag(
            db, updates["output_tag_id"], exclude_computed_tag_id=computed_tag_id,
        )
        if err:
            audit(AuditEvent(
                action=action,
                target_type="computed_tag",
                target_id=computed_tag_id,
                target_label=target_label,
                summary=f"Denied: {err}",
                status="denied",
                error_message=code or err,
                details={"request": updates, "before": _summarize_ct(existing)},
            ), request)
            raise HTTPException(status_code or 400, err)

    # Partition fields by destination table
    tag_updates = {k: v for k, v in updates.items() if k in _TAG_FIELDS}
    computed_updates = {k: v for k, v in updates.items() if k in _COMPUTED_FIELDS}

    try:
        if tag_updates:
            set_clauses = ", ".join(f"{k} = :{k}" for k in tag_updates)
            db.execute(
                text(f"UPDATE tags SET {set_clauses}, updated_at = NOW() WHERE id = :id"),
                {**tag_updates, "id": computed_tag_id},
            )
        if computed_updates:
            params = dict(computed_updates)
            if "block_config" in params:
                params["block_config"] = json.dumps(params["block_config"])
                set_clauses = ", ".join(
                    f"{k} = CAST(:{k} AS jsonb)" if k == "block_config" else f"{k} = :{k}"
                    for k in computed_updates
                )
            else:
                set_clauses = ", ".join(f"{k} = :{k}" for k in computed_updates)
            db.execute(
                text(f"UPDATE computed_tags SET {set_clauses} WHERE id = :id"),
                {**params, "id": computed_tag_id},
            )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()

        if "ux_computed_tags_output_tag_id" in msg:
            audit(AuditEvent(
                action=action,
                target_type="computed_tag",
                target_id=computed_tag_id,
                target_label=target_label,
                summary=f"Denied: output_tag_id={updates.get('output_tag_id')} already in use",
                status="denied",
                error_message="output_tag_id unique violation",
                details={"request": updates, "before": _summarize_ct(existing)},
            ), request)
            raise HTTPException(409, f"output_tag_id={updates.get('output_tag_id')} is already another calc's output")

        if "ck_computed_tags_no_self_externalize" in msg:
            audit(AuditEvent(
                action=action,
                target_type="computed_tag",
                target_id=computed_tag_id,
                target_label=target_label,
                summary="Denied: output_tag_id cannot equal own id",
                status="denied",
                error_message="self-externalize",
                details={"request": updates, "before": _summarize_ct(existing)},
            ), request)
            raise HTTPException(400, "output_tag_id cannot equal the tag's own id")

        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action=action,
                target_type="computed_tag",
                target_id=computed_tag_id,
                target_label=target_label,
                summary="Denied: name collides with another tag on the same device",
                status="denied",
                error_message="duplicate (device_id, name)",
                details={"request": updates, "before": _summarize_ct(existing)},
            ), request)
            raise HTTPException(409, "another tag on this device already has this name")

        if "foreign key" in msg:
            audit(AuditEvent(
                action=action,
                target_type="computed_tag",
                target_id=computed_tag_id,
                target_label=target_label,
                summary="Denied: FK violation",
                status="denied",
                error_message=f"FK violation: {e.orig}",
                details={"request": updates, "before": _summarize_ct(existing)},
            ), request)
            raise HTTPException(400, f"FK violation: {e.orig}")

        audit(AuditEvent(
            action=action,
            target_type="computed_tag",
            target_id=computed_tag_id,
            target_label=target_label,
            summary="UPDATE failed (IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": updates, "before": _summarize_ct(existing)},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action=action,
            target_type="computed_tag",
            target_id=computed_tag_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": updates, "before": _summarize_ct(existing)},
        ), request)
        raise

    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} computed tag '{existing['name']}'"
    else:
        # Mention output mode change explicitly if it happened
        bits = list(updates.keys())
        if "output_tag_id" in updates:
            if updates["output_tag_id"] is None:
                bits = [b for b in bits if b != "output_tag_id"] + ["output→internal"]
            else:
                bits = [b for b in bits if b != "output_tag_id"] + [f"output→tag_id={updates['output_tag_id']}"]
        summary = f"Updated computed tag '{existing['name']}' ({', '.join(bits)})"

    audit(AuditEvent(
        action=action,
        target_type="computed_tag",
        target_id=computed_tag_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_ct(existing),
        },
    ), request)

    return get_computed_tag(computed_tag_id, db)


@router.delete("/{computed_tag_id}", status_code=204)
def delete_computed_tag(
    computed_tag_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text(_SELECT + " WHERE ct.id = :id"),
        {"id": computed_tag_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="computed_tag.delete",
            target_type="computed_tag",
            target_id=computed_tag_id,
            summary=f"Denied: computed tag {computed_tag_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"computed tag {computed_tag_id} not found")

    target_label = existing["name"]

    try:
        db.execute(
            text("DELETE FROM tags WHERE id = :id"),
            {"id": computed_tag_id},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="computed_tag.delete",
            target_type="computed_tag",
            target_id=computed_tag_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_ct(existing)},
        ), request)
        raise

    output_note = ""
    if existing.get("output_tag_id") is not None:
        output_note = (
            f" (was writing to external tag '{existing.get('output_tag_name')}' "
            f"on {existing.get('output_device_name')})"
        )

    audit(AuditEvent(
        action="computed_tag.delete",
        target_type="computed_tag",
        target_id=computed_tag_id,
        target_label=target_label,
        summary=(
            f"Deleted computed tag '{existing['name']}' "
            f"(device={existing['device_name']}, block={existing['block_type']}{output_note})"
        ),
        details={"before": _full_ct(existing)},
    ), request)
