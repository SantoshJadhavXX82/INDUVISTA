"""CRUD endpoints for the engineering_units master.
Phase 16.0h - audit() calls on every mutating endpoint.

The master is a global, reusable list of unit-of-measure entries.

Protection model:
- Seeded entries have is_system=true. They may be disabled but NOT deleted.
- User-created entries can be edited and deleted freely.
- Deletion is blocked if any tag references this unit.

Endpoints:
  GET    /api/engineering-units                  list
  GET    /api/engineering-units/{id}             one
  POST   /api/engineering-units                  create   [engineering_unit.create]
  PATCH  /api/engineering-units/{id}             update   [engineering_unit.update or .toggle]
  DELETE /api/engineering-units/{id}             delete   [engineering_unit.delete]
  GET    /api/engineering-units/_meta/quantity-kinds   (meta, not audited)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api/engineering-units", tags=["engineering-units"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EngineeringUnitCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    label: str = Field(..., min_length=1, max_length=128)
    quantity_kind: str | None = Field(None, max_length=32)
    enabled: bool = True
    description: str | None = None


class EngineeringUnitUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32)
    label: str | None = Field(None, min_length=1, max_length=128)
    quantity_kind: str | None = Field(None, max_length=32)
    enabled: bool | None = None
    description: str | None = None


class EngineeringUnitResponse(BaseModel):
    id: int
    code: str
    label: str
    quantity_kind: str | None
    enabled: bool
    is_system: bool
    description: str | None
    created_at: datetime
    updated_at: datetime
    in_use_count: int = Field(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_LIST_SQL = """
    SELECT eu.id, eu.code, eu.label, eu.quantity_kind, eu.enabled,
           eu.is_system, eu.description, eu.created_at, eu.updated_at,
           COALESCE(usage.cnt, 0) AS in_use_count
    FROM engineering_units eu
    LEFT JOIN (
        SELECT engineering_unit_id, COUNT(*) AS cnt
        FROM tags
        WHERE engineering_unit_id IS NOT NULL
        GROUP BY engineering_unit_id
    ) usage ON usage.engineering_unit_id = eu.id
"""


def _summarize_eu(row) -> dict[str, Any]:
    return {
        "code": row["code"],
        "label": row.get("label"),
        "quantity_kind": row.get("quantity_kind"),
        "enabled": row.get("enabled"),
        "is_system": row.get("is_system"),
    }


def _full_eu(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "label": row.get("label"),
        "quantity_kind": row.get("quantity_kind"),
        "enabled": row.get("enabled"),
        "is_system": row["is_system"],
        "description": row.get("description"),
        "in_use_count": row.get("in_use_count"),
    }


# ---------------------------------------------------------------------------
# List + get (read-only)
# ---------------------------------------------------------------------------


@router.get("", response_model=list[EngineeringUnitResponse])
def list_units(
    db: Annotated[Session, Depends(get_session)],
    quantity_kind: str | None = Query(None),
    enabled: bool | None = Query(None),
    search: str | None = Query(None),
    include_usage: bool = Query(True),
):
    sql = _LIST_SQL + " WHERE TRUE"
    params: dict[str, object] = {}
    if quantity_kind is not None:
        sql += " AND eu.quantity_kind = :quantity_kind"
        params["quantity_kind"] = quantity_kind
    if enabled is not None:
        sql += " AND eu.enabled = :enabled"
        params["enabled"] = enabled
    if search:
        sql += " AND (LOWER(eu.code) LIKE :q OR LOWER(eu.label) LIKE :q)"
        params["q"] = f"%{search.lower()}%"
    sql += " ORDER BY eu.quantity_kind NULLS LAST, LOWER(eu.code)"

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{unit_id}", response_model=EngineeringUnitResponse)
def get_unit(unit_id: int, db: Annotated[Session, Depends(get_session)]):
    row = db.execute(
        text(_LIST_SQL + " WHERE eu.id = :id"),
        {"id": unit_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"Engineering unit {unit_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Create / update / delete (all audited)
# ---------------------------------------------------------------------------


@router.post("", response_model=EngineeringUnitResponse, status_code=201)
def create_unit(
    body: EngineeringUnitCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.code

    try:
        row = db.execute(text("""
            INSERT INTO engineering_units (
                code, label, quantity_kind, enabled, is_system, description
            )
            VALUES (
                :code, :label, :quantity_kind, :enabled, FALSE, :description
            )
            RETURNING id, code, label, quantity_kind, enabled, is_system,
                      description, created_at, updated_at
        """), body.model_dump()).mappings().first()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action="engineering_unit.create",
                target_type="engineering_unit",
                target_label=target_label,
                summary=f"Denied: engineering unit code '{body.code}' already exists",
                status="denied",
                error_message="duplicate code",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(
                409, f"Engineering unit with code '{body.code}' already exists"
            )
        audit(AuditEvent(
            action="engineering_unit.create",
            target_type="engineering_unit",
            target_label=target_label,
            summary="INSERT failed (unclassified IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="engineering_unit.create",
            target_type="engineering_unit",
            target_label=target_label,
            summary="INSERT failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump()},
        ), request)
        raise

    audit(AuditEvent(
        action="engineering_unit.create",
        target_type="engineering_unit",
        target_id=row["id"],
        target_label=target_label,
        summary=f"Created engineering unit '{body.code}' ({body.label})",
        details=body.model_dump(),
    ), request)

    return {**dict(row), "in_use_count": 0}


@router.patch("/{unit_id}", response_model=EngineeringUnitResponse)
def update_unit(
    unit_id: int,
    body: EngineeringUnitUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "engineering_unit.toggle" if is_toggle else "engineering_unit.update"

    # Pre-fetch existing for before-snapshot.
    existing = db.execute(
        text(_LIST_SQL + " WHERE eu.id = :id"),
        {"id": unit_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="engineering_unit",
            target_id=unit_id,
            summary=f"Denied: engineering unit {unit_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"Engineering unit {unit_id} not found")

    target_label = existing["code"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="engineering_unit",
            target_id=unit_id,
            target_label=target_label,
            summary="Denied: no fields provided",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "No changes provided")

    set_clauses = [f"{k} = :{k}" for k in updates]
    set_clauses.append("updated_at = NOW()")
    sql = (
        f"UPDATE engineering_units SET {', '.join(set_clauses)} "
        f"WHERE id = :id "
        f"RETURNING id, code, label, quantity_kind, enabled, is_system, "
        f"          description, created_at, updated_at"
    )

    try:
        row = db.execute(text(sql), {**updates, "id": unit_id}).mappings().first()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action=action,
                target_type="engineering_unit",
                target_id=unit_id,
                target_label=target_label,
                summary=f"Denied: code '{updates.get('code')}' collides with another unit",
                status="denied",
                error_message="duplicate code",
                details={"request": updates, "before": _summarize_eu(existing)},
            ), request)
            raise HTTPException(
                409, f"Another unit with code '{updates.get('code')}' already exists"
            )
        audit(AuditEvent(
            action=action,
            target_type="engineering_unit",
            target_id=unit_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=str(e.orig),
            details={"request": updates, "before": _summarize_eu(existing)},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    in_use = db.execute(text(
        "SELECT COUNT(*) FROM tags WHERE engineering_unit_id = :id"
    ), {"id": unit_id}).scalar() or 0

    # Success.
    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} engineering unit '{existing['code']}'"
    else:
        summary = f"Updated engineering unit '{existing['code']}' ({', '.join(updates.keys())})"

    audit(AuditEvent(
        action=action,
        target_type="engineering_unit",
        target_id=unit_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_eu(existing),
        },
    ), request)

    return {**dict(row), "in_use_count": int(in_use)}


@router.delete("/{unit_id}", status_code=204)
def delete_unit(
    unit_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text(_LIST_SQL + " WHERE eu.id = :id"),
        {"id": unit_id},
    ).mappings().first()
    if not row:
        audit(AuditEvent(
            action="engineering_unit.delete",
            target_type="engineering_unit",
            target_id=unit_id,
            summary=f"Denied: engineering unit {unit_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"Engineering unit {unit_id} not found")

    target_label = row["code"]

    if row["is_system"]:
        audit(AuditEvent(
            action="engineering_unit.delete",
            target_type="engineering_unit",
            target_id=unit_id,
            target_label=target_label,
            summary=f"Denied: '{row['code']}' is a system-seeded unit",
            status="denied",
            error_message="system-seeded units cannot be deleted",
            details={"before": _full_eu(row)},
        ), request)
        raise HTTPException(
            409,
            "System-seeded units cannot be deleted. Disable it instead "
            "(set enabled=false) - it will be hidden from dropdowns but "
            "existing tag references remain valid.",
        )

    if row["in_use_count"] > 0:
        audit(AuditEvent(
            action="engineering_unit.delete",
            target_type="engineering_unit",
            target_id=unit_id,
            target_label=target_label,
            summary=f"Denied: '{row['code']}' is referenced by {row['in_use_count']} tag(s)",
            status="denied",
            error_message=f"in_use_count={row['in_use_count']}",
            details={"before": _full_eu(row)},
        ), request)
        raise HTTPException(
            409,
            f"{row['in_use_count']} tag(s) still reference this unit. "
            f"Reassign or clear them first.",
        )

    try:
        db.execute(text("DELETE FROM engineering_units WHERE id = :id"), {"id": unit_id})
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="engineering_unit.delete",
            target_type="engineering_unit",
            target_id=unit_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_eu(row)},
        ), request)
        raise

    audit(AuditEvent(
        action="engineering_unit.delete",
        target_type="engineering_unit",
        target_id=unit_id,
        target_label=target_label,
        summary=f"Deleted engineering unit '{row['code']}' ({row.get('label') or 'no label'})",
        details={"before": _full_eu(row)},
    ), request)


# ---------------------------------------------------------------------------
# Auxiliary - quantity kinds (read-only)
# ---------------------------------------------------------------------------


@router.get("/_meta/quantity-kinds", response_model=list[str])
def list_quantity_kinds(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text("""
        SELECT DISTINCT quantity_kind
        FROM engineering_units
        WHERE quantity_kind IS NOT NULL
        ORDER BY quantity_kind
    """)).scalars().all()
    return list(rows)
