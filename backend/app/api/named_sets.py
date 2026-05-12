"""CRUD endpoints for the named_sets master + per-set values.

Named sets translate raw integer/boolean register values into human-readable
text — e.g. a tag returning `1` displays as "Running" when assigned to the
MOTOR_STATE named set. The raw value remains the canonical CV; the named
set only changes how it is rendered in UI / reports / trends.

Architecture:
  GET    /api/named-sets            list (includes value count)
  GET    /api/named-sets/{id}       single (includes full values list)
  POST   /api/named-sets            create the parent only (values via PUT)
  PATCH  /api/named-sets/{id}       update metadata only
  DELETE /api/named-sets/{id}       refused if is_system or in_use_count>0
  PUT    /api/named-sets/{id}/values   atomic replacement of the values list

The PUT-replace pattern for values keeps the API simple: the UI sends the
desired full list, the server reconciles. No partial-update endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api/named-sets", tags=["named-sets"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NamedSetValueInput(BaseModel):
    raw_value: int = Field(..., description="Integer raw value from the device")
    display_text: str = Field(..., min_length=1, max_length=128)
    display_order: int = 0
    color: str | None = Field(None, max_length=16,
                              description="Optional UI color hint (e.g. 'red', '#ef4444')")


class NamedSetValueResponse(NamedSetValueInput):
    id: int


class NamedSetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128,
                      description="UPPERCASE_SNAKE_CASE conventional, e.g. MOTOR_STATE")
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
    value_count: int = Field(0, description="Number of values in the set")
    in_use_count: int = Field(0, description="Number of tags using this set")
    values: list[NamedSetValueResponse] = Field(
        default_factory=list,
        description="Full list of value→text mappings (only populated on GET-by-id)",
    )


class NamedSetValuesReplaceRequest(BaseModel):
    values: list[NamedSetValueInput]


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


@router.get("", response_model=list[NamedSetResponse])
def list_named_sets(
    db: Annotated[Session, Depends(get_session)],
    enabled: bool | None = Query(None),
    search: str | None = Query(None),
    include_values: bool = Query(
        False,
        description="If true, every set includes its full values list. Off by "
                    "default — list responses are kept small.",
    ),
):
    """List all named sets, with counts. Sorted by name."""
    sql = """
        SELECT ns.id, ns.name, ns.description, ns.is_system, ns.enabled,
               ns.created_at, ns.updated_at,
               COALESCE(v.value_count, 0) AS value_count,
               COALESCE(u.in_use_count, 0) AS in_use_count
        FROM named_sets ns
        LEFT JOIN (
            SELECT named_set_id, COUNT(*) AS value_count
            FROM named_set_values
            GROUP BY named_set_id
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
        # Single batch query for values keyed by named_set_id
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
# Create / update / delete
# ---------------------------------------------------------------------------


@router.post("", response_model=NamedSetResponse, status_code=201)
def create_named_set(
    body: NamedSetCreate,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a new (non-system) named set. Values are added separately via PUT."""
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
            raise HTTPException(409, f"A named set called '{body.name}' already exists")
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    return get_named_set(new_id, db)


@router.patch("/{set_id}", response_model=NamedSetResponse)
def update_named_set(
    set_id: int,
    body: NamedSetUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text("SELECT id FROM named_sets WHERE id = :id"),
        {"id": set_id},
    ).mappings().first()
    if not existing:
        raise HTTPException(404, f"Named set {set_id} not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No changes provided")

    set_clauses = [f"{k} = :{k}" for k in updates]
    set_clauses.append("updated_at = NOW()")
    sql = (
        f"UPDATE named_sets SET {', '.join(set_clauses)} WHERE id = :id"
    )
    try:
        db.execute(text(sql), {**updates, "id": set_id})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(409, f"Another named set with name '{updates.get('name')}' exists")
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    return get_named_set(set_id, db)


@router.delete("/{set_id}", status_code=204)
def delete_named_set(set_id: int, db: Annotated[Session, Depends(get_session)]):
    """Delete a named set.

    Refused if:
      - is_system is true (seeded set — disable instead)
      - any tag uses this set (reassign first)
    """
    row = db.execute(text("""
        SELECT ns.is_system,
               COALESCE((SELECT COUNT(*) FROM tags
                         WHERE named_set_id = ns.id), 0) AS in_use_count
        FROM named_sets ns WHERE ns.id = :id
    """), {"id": set_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Named set {set_id} not found")

    if row["is_system"]:
        raise HTTPException(
            409,
            "System-seeded named sets cannot be deleted. Disable it instead.",
        )
    if row["in_use_count"] > 0:
        raise HTTPException(
            409,
            f"{row['in_use_count']} tag(s) still reference this named set. "
            f"Reassign or clear them first.",
        )

    db.execute(text("DELETE FROM named_sets WHERE id = :id"), {"id": set_id})
    db.commit()


# ---------------------------------------------------------------------------
# Atomic values replacement
# ---------------------------------------------------------------------------


@router.put("/{set_id}/values", response_model=list[NamedSetValueResponse])
def replace_named_set_values(
    set_id: int,
    body: NamedSetValuesReplaceRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Replace the full set of values atomically.

    The UI sends the desired complete list; the server deletes existing rows
    and inserts the new ones in one transaction. No incremental updates —
    keeps the API simple and avoids partial-state bugs.

    Duplicate raw_value within the request is rejected with 400.
    """
    existing = db.execute(
        text("SELECT id FROM named_sets WHERE id = :id"),
        {"id": set_id},
    ).mappings().first()
    if not existing:
        raise HTTPException(404, f"Named set {set_id} not found")

    # Reject duplicate raw_values in the request
    seen = set()
    for v in body.values:
        if v.raw_value in seen:
            raise HTTPException(
                400, f"Duplicate raw_value {v.raw_value} in request — each must be unique"
            )
        seen.add(v.raw_value)

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
        """), {
            "id": set_id,
            "values": __import__("json").dumps([v.model_dump() for v in body.values]),
        })
    db.commit()

    rows = db.execute(text("""
        SELECT id, raw_value, display_text, display_order, color
        FROM named_set_values
        WHERE named_set_id = :id
        ORDER BY display_order, raw_value
    """), {"id": set_id}).mappings().all()
    return [dict(r) for r in rows]
