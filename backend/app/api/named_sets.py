"""CRUD endpoints for the named_sets master + per-set values.
Phase 16.0h - audit() calls on every mutating endpoint.

Named sets translate raw integer/boolean register values into human-readable
text. The raw value remains canonical; the named set only changes display.

Architecture:
  GET    /api/named-sets            list
  GET    /api/named-sets/{id}       one (with full values)
  POST   /api/named-sets            create   [named_set.create]
  PATCH  /api/named-sets/{id}       update   [named_set.update or .toggle]
  DELETE /api/named-sets/{id}       delete   [named_set.delete]
  PUT    /api/named-sets/{id}/values   atomic values replace [named_set.values_replace]

The values replace audit captures both old AND new value lists so the
change can be reconstructed at any future time.
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


router = APIRouter(prefix="/api/named-sets", tags=["named-sets"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NamedSetValueInput(BaseModel):
    raw_value: int
    display_text: str = Field(..., min_length=1, max_length=128)
    display_order: int = 0
    color: str | None = Field(None, max_length=16)


class NamedSetValueResponse(NamedSetValueInput):
    id: int


class NamedSetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    enabled: bool = True


class NamedSetUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    enabled: bool | None = None


class NamedSetResponse(BaseModel):
    id: int
    name: str
    description: str | None
    is_system: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime
    value_count: int = Field(0)
    in_use_count: int = Field(0)
    values: list[NamedSetValueResponse] = Field(default_factory=list)


class NamedSetValuesReplaceRequest(BaseModel):
    values: list[NamedSetValueInput]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize_ns(row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "description": row.get("description"),
        "enabled": row.get("enabled"),
        "is_system": row.get("is_system"),
    }


def _full_ns(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "enabled": row.get("enabled"),
        "is_system": row["is_system"],
        "value_count": row.get("value_count"),
        "in_use_count": row.get("in_use_count"),
    }


def _fetch_values_list(db: Session, set_id: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT raw_value, display_text, display_order, color
        FROM named_set_values
        WHERE named_set_id = :id
        ORDER BY display_order, raw_value
    """), {"id": set_id}).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# List + get (read-only)
# ---------------------------------------------------------------------------


@router.get("", response_model=list[NamedSetResponse])
def list_named_sets(
    db: Annotated[Session, Depends(get_session)],
    enabled: bool | None = Query(None),
    search: str | None = Query(None),
    include_values: bool = Query(False),
):
    sql = """
        SELECT ns.id, ns.name, ns.description, ns.is_system, ns.enabled,
               ns.created_at, ns.updated_at,
               COALESCE(v.value_count, 0) AS value_count,
               COALESCE(u.in_use_count, 0) AS in_use_count
        FROM named_sets ns
        LEFT JOIN (
            SELECT named_set_id, COUNT(*) AS value_count
            FROM named_set_values GROUP BY named_set_id
        ) v ON v.named_set_id = ns.id
        LEFT JOIN (
            SELECT named_set_id, COUNT(*) AS in_use_count
            FROM tags WHERE named_set_id IS NOT NULL
            GROUP BY named_set_id
        ) u ON u.named_set_id = ns.id
        WHERE TRUE
    """
    params: dict[str, object] = {}
    if enabled is not None:
        sql += " AND ns.enabled = :enabled"
        params["enabled"] = enabled
    if search:
        sql += " AND (LOWER(ns.name) LIKE :q OR LOWER(COALESCE(ns.description, '')) LIKE :q)"
        params["q"] = f"%{search.lower()}%"
    sql += " ORDER BY ns.name"

    rows = [dict(r) for r in db.execute(text(sql), params).mappings().all()]

    if include_values and rows:
        ids = [r["id"] for r in rows]
        vrows = db.execute(text("""
            SELECT id, named_set_id, raw_value, display_text,
                   display_order, color
            FROM named_set_values
            WHERE named_set_id = ANY(:ids)
            ORDER BY named_set_id, display_order, raw_value
        """), {"ids": ids}).mappings().all()
        by_set: dict[int, list[dict]] = {}
        for v in vrows:
            by_set.setdefault(v["named_set_id"], []).append({
                "id": v["id"], "raw_value": v["raw_value"],
                "display_text": v["display_text"],
                "display_order": v["display_order"], "color": v["color"],
            })
        for r in rows:
            r["values"] = by_set.get(r["id"], [])

    return rows


@router.get("/{set_id}", response_model=NamedSetResponse)
def get_named_set(set_id: int, db: Annotated[Session, Depends(get_session)]):
    row = db.execute(text("""
        SELECT ns.id, ns.name, ns.description, ns.is_system, ns.enabled,
               ns.created_at, ns.updated_at,
               COALESCE((SELECT COUNT(*) FROM named_set_values
                         WHERE named_set_id = ns.id), 0) AS value_count,
               COALESCE((SELECT COUNT(*) FROM tags
                         WHERE named_set_id = ns.id), 0) AS in_use_count
        FROM named_sets ns WHERE ns.id = :id
    """), {"id": set_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Named set {set_id} not found")

    values = list(db.execute(text("""
        SELECT id, raw_value, display_text, display_order, color
        FROM named_set_values
        WHERE named_set_id = :id
        ORDER BY display_order, raw_value
    """), {"id": set_id}).mappings().all())

    return {**dict(row), "values": [dict(v) for v in values]}


# ---------------------------------------------------------------------------
# Create / update / delete (audited)
# ---------------------------------------------------------------------------


@router.post("", response_model=NamedSetResponse, status_code=201)
def create_named_set(
    body: NamedSetCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.name

    try:
        new_id = db.execute(text("""
            INSERT INTO named_sets (name, description, is_system, enabled)
            VALUES (:name, :description, FALSE, :enabled)
            RETURNING id
        """), body.model_dump()).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action="named_set.create",
                target_type="named_set",
                target_label=target_label,
                summary=f"Denied: named set '{body.name}' already exists",
                status="denied",
                error_message="duplicate name",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(409, f"A named set called '{body.name}' already exists")
        audit(AuditEvent(
            action="named_set.create",
            target_type="named_set",
            target_label=target_label,
            summary="INSERT failed (unclassified IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    audit(AuditEvent(
        action="named_set.create",
        target_type="named_set",
        target_id=new_id,
        target_label=target_label,
        summary=f"Created named set '{body.name}'",
        details=body.model_dump(),
    ), request)

    return get_named_set(new_id, db)


@router.patch("/{set_id}", response_model=NamedSetResponse)
def update_named_set(
    set_id: int,
    body: NamedSetUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "named_set.toggle" if is_toggle else "named_set.update"

    existing = db.execute(text("""
        SELECT id, name, description, enabled, is_system
        FROM named_sets WHERE id = :id
    """), {"id": set_id}).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="named_set",
            target_id=set_id,
            summary=f"Denied: named set {set_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"Named set {set_id} not found")

    target_label = existing["name"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="named_set",
            target_id=set_id,
            target_label=target_label,
            summary="Denied: no fields provided",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "No changes provided")

    set_clauses = [f"{k} = :{k}" for k in updates]
    set_clauses.append("updated_at = NOW()")
    sql = f"UPDATE named_sets SET {', '.join(set_clauses)} WHERE id = :id"
    try:
        db.execute(text(sql), {**updates, "id": set_id})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action=action,
                target_type="named_set",
                target_id=set_id,
                target_label=target_label,
                summary=f"Denied: name '{updates.get('name')}' collides with another named set",
                status="denied",
                error_message="duplicate name",
                details={"request": updates, "before": _summarize_ns(existing)},
            ), request)
            raise HTTPException(409, f"Another named set with name '{updates.get('name')}' exists")
        audit(AuditEvent(
            action=action,
            target_type="named_set",
            target_id=set_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=str(e.orig),
            details={"request": updates, "before": _summarize_ns(existing)},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} named set '{existing['name']}'"
    else:
        summary = f"Updated named set '{existing['name']}' ({', '.join(updates.keys())})"

    audit(AuditEvent(
        action=action,
        target_type="named_set",
        target_id=set_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_ns(existing),
        },
    ), request)

    return get_named_set(set_id, db)


@router.delete("/{set_id}", status_code=204)
def delete_named_set(
    set_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(text("""
        SELECT ns.id, ns.name, ns.description, ns.is_system, ns.enabled,
               COALESCE((SELECT COUNT(*) FROM named_set_values
                         WHERE named_set_id = ns.id), 0) AS value_count,
               COALESCE((SELECT COUNT(*) FROM tags
                         WHERE named_set_id = ns.id), 0) AS in_use_count
        FROM named_sets ns WHERE ns.id = :id
    """), {"id": set_id}).mappings().first()
    if not row:
        audit(AuditEvent(
            action="named_set.delete",
            target_type="named_set",
            target_id=set_id,
            summary=f"Denied: named set {set_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"Named set {set_id} not found")

    target_label = row["name"]

    if row["is_system"]:
        audit(AuditEvent(
            action="named_set.delete",
            target_type="named_set",
            target_id=set_id,
            target_label=target_label,
            summary=f"Denied: '{row['name']}' is a system-seeded named set",
            status="denied",
            error_message="system-seeded named sets cannot be deleted",
            details={"before": _full_ns(row)},
        ), request)
        raise HTTPException(
            409,
            "System-seeded named sets cannot be deleted. Disable it instead.",
        )

    if row["in_use_count"] > 0:
        audit(AuditEvent(
            action="named_set.delete",
            target_type="named_set",
            target_id=set_id,
            target_label=target_label,
            summary=f"Denied: '{row['name']}' is referenced by {row['in_use_count']} tag(s)",
            status="denied",
            error_message=f"in_use_count={row['in_use_count']}",
            details={"before": _full_ns(row)},
        ), request)
        raise HTTPException(
            409,
            f"{row['in_use_count']} tag(s) still reference this named set. "
            f"Reassign or clear them first.",
        )

    # Capture values list for the audit before they cascade-delete.
    values_before = _fetch_values_list(db, set_id)

    try:
        db.execute(text("DELETE FROM named_sets WHERE id = :id"), {"id": set_id})
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="named_set.delete",
            target_type="named_set",
            target_id=set_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_ns(row), "values_before": values_before},
        ), request)
        raise

    audit(AuditEvent(
        action="named_set.delete",
        target_type="named_set",
        target_id=set_id,
        target_label=target_label,
        summary=f"Deleted named set '{row['name']}' ({len(values_before)} value mappings)",
        details={"before": _full_ns(row), "values_before": values_before},
    ), request)


# ---------------------------------------------------------------------------
# Atomic values replacement (audited with full before/after)
# ---------------------------------------------------------------------------


@router.put("/{set_id}/values", response_model=list[NamedSetValueResponse])
def replace_named_set_values(
    set_id: int,
    body: NamedSetValuesReplaceRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Replace the full set of values atomically.

    Audit captures both the OLD values list and the NEW values list so
    the change can be diffed and reconstructed at any future time.
    """
    existing = db.execute(text("""
        SELECT id, name FROM named_sets WHERE id = :id
    """), {"id": set_id}).mappings().first()
    if not existing:
        audit(AuditEvent(
            action="named_set.values_replace",
            target_type="named_set",
            target_id=set_id,
            summary=f"Denied: named set {set_id} not found",
            status="denied",
            error_message="not found",
            details={"request": [v.model_dump() for v in body.values]},
        ), request)
        raise HTTPException(404, f"Named set {set_id} not found")

    target_label = existing["name"]
    new_values_dump = [v.model_dump() for v in body.values]

    # Reject duplicate raw_values in the request.
    seen = set()
    for v in body.values:
        if v.raw_value in seen:
            audit(AuditEvent(
                action="named_set.values_replace",
                target_type="named_set",
                target_id=set_id,
                target_label=target_label,
                summary=f"Denied: duplicate raw_value {v.raw_value} in request",
                status="denied",
                error_message=f"duplicate raw_value {v.raw_value}",
                details={"request": new_values_dump},
            ), request)
            raise HTTPException(
                400, f"Duplicate raw_value {v.raw_value} in request - each must be unique"
            )
        seen.add(v.raw_value)

    # Capture old values for audit before destroying them.
    values_before = _fetch_values_list(db, set_id)

    try:
        db.execute(
            text("DELETE FROM named_set_values WHERE named_set_id = :id"),
            {"id": set_id},
        )
        if body.values:
            db.execute(text("""
                INSERT INTO named_set_values
                    (named_set_id, raw_value, display_text, display_order, color)
                SELECT :id,
                       v.raw_value, v.display_text, v.display_order, v.color
                FROM jsonb_to_recordset(CAST(:values AS jsonb)) AS v(
                    raw_value INT, display_text TEXT,
                    display_order INT, color TEXT
                )
            """), {"id": set_id, "values": json.dumps(new_values_dump)})
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="named_set.values_replace",
            target_type="named_set",
            target_id=set_id,
            target_label=target_label,
            summary="values_replace failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={
                "request": new_values_dump,
                "values_before": values_before,
            },
        ), request)
        raise

    rows = db.execute(text("""
        SELECT id, raw_value, display_text, display_order, color
        FROM named_set_values
        WHERE named_set_id = :id
        ORDER BY display_order, raw_value
    """), {"id": set_id}).mappings().all()
    values_after = [dict(r) for r in rows]

    audit(AuditEvent(
        action="named_set.values_replace",
        target_type="named_set",
        target_id=set_id,
        target_label=target_label,
        summary=f"Replaced values of '{existing['name']}': "
                f"{len(values_before)} -> {len(values_after)} mapping(s)",
        details={
            "values_before": values_before,
            "values_after": [
                {k: v for k, v in r.items() if k != "id"} for r in values_after
            ],
            "count_before": len(values_before),
            "count_after": len(values_after),
        },
    ), request)

    return values_after
