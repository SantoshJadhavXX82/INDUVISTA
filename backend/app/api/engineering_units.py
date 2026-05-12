"""CRUD endpoints for the engineering_units master.

The master is a global, reusable list of unit-of-measure entries. Tags
reference a unit either by FK (engineering_unit_id) or by a free-text
override (engineering_unit) — see migration 0005 and tags API.

Protection model:
- Seeded entries have is_system=true. They may be disabled but NOT deleted.
- User-created entries can be edited and deleted freely.
- Deletion is blocked if any tag references this unit (the FK has
  ON DELETE SET NULL so it wouldn't lose data, but a sudden silent
  un-assignment is bad UX — better to require the user to confirm and
  reassign first).
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

router = APIRouter(prefix="/api/engineering-units", tags=["engineering-units"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EngineeringUnitCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=32,
                      description="Symbol shown next to values (e.g. 'kg/h')")
    label: str = Field(..., min_length=1, max_length=128,
                       description="Human-readable name (e.g. 'Kilograms per hour')")
    quantity_kind: str | None = Field(
        None, max_length=32,
        description="Grouping key for the dropdown (e.g. 'flow_mass')",
    )
    enabled: bool = True
    description: str | None = None


class EngineeringUnitUpdate(BaseModel):
    # is_system is NOT updateable — once a seed, always a seed.
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
    in_use_count: int = Field(
        0, description="Number of tags currently referencing this unit by FK",
    )


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


@router.get("", response_model=list[EngineeringUnitResponse])
def list_units(
    db: Annotated[Session, Depends(get_session)],
    quantity_kind: str | None = Query(None, description="Filter by kind"),
    enabled: bool | None = Query(None, description="Filter by enabled flag"),
    search: str | None = Query(None, description="Match on code or label (case-insensitive)"),
    include_usage: bool = Query(
        True,
        description="Include in_use_count (one extra query per row — keep on)",
    ),
):
    """List all engineering units, optionally filtered.

    Sorted by quantity_kind, then code — matches the grouped-dropdown UX.
    """
    sql = """
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
        WHERE TRUE
    """
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
    row = db.execute(text("""
        SELECT eu.id, eu.code, eu.label, eu.quantity_kind, eu.enabled,
               eu.is_system, eu.description, eu.created_at, eu.updated_at,
               COALESCE((
                   SELECT COUNT(*) FROM tags
                   WHERE engineering_unit_id = eu.id
               ), 0) AS in_use_count
        FROM engineering_units eu
        WHERE eu.id = :id
    """), {"id": unit_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Engineering unit {unit_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------


@router.post("", response_model=EngineeringUnitResponse, status_code=201)
def create_unit(
    body: EngineeringUnitCreate,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a new (non-system) engineering unit.

    is_system defaults to false — users cannot create system entries through
    the API. Seeded entries come from the migration only.
    """
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
        if "unique" in str(e.orig).lower() or "duplicate" in str(e.orig).lower():
            raise HTTPException(
                409, f"Engineering unit with code '{body.code}' already exists"
            )
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    return {**dict(row), "in_use_count": 0}


@router.patch("/{unit_id}", response_model=EngineeringUnitResponse)
def update_unit(
    unit_id: int,
    body: EngineeringUnitUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    """Update a unit. System entries CAN be edited (e.g. fix a typo in label,
    change the quantity_kind for better grouping) but cannot be deleted or
    have their is_system flag flipped.
    """
    existing = db.execute(
        text("SELECT id, is_system FROM engineering_units WHERE id = :id"),
        {"id": unit_id},
    ).mappings().first()
    if not existing:
        raise HTTPException(404, f"Engineering unit {unit_id} not found")

    # Build a partial UPDATE — only the fields actually provided.
    updates = body.model_dump(exclude_unset=True)
    if not updates:
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
        if "unique" in str(e.orig).lower() or "duplicate" in str(e.orig).lower():
            raise HTTPException(
                409, f"Another unit with code '{updates.get('code')}' already exists"
            )
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    # Include current usage count in the response
    in_use = db.execute(text(
        "SELECT COUNT(*) FROM tags WHERE engineering_unit_id = :id"
    ), {"id": unit_id}).scalar() or 0

    return {**dict(row), "in_use_count": int(in_use)}


@router.delete("/{unit_id}", status_code=204)
def delete_unit(unit_id: int, db: Annotated[Session, Depends(get_session)]):
    """Delete a non-system, unused unit.

    Refused if:
      - is_system is true (seed entries are protected — disable instead)
      - any tag references this unit (would silently un-assign without warning)
    """
    row = db.execute(text("""
        SELECT eu.is_system,
               COALESCE((SELECT COUNT(*) FROM tags
                         WHERE engineering_unit_id = eu.id), 0) AS in_use_count
        FROM engineering_units eu
        WHERE eu.id = :id
    """), {"id": unit_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Engineering unit {unit_id} not found")

    if row["is_system"]:
        raise HTTPException(
            409,
            "System-seeded units cannot be deleted. Disable it instead "
            "(set enabled=false) — it will be hidden from dropdowns but "
            "existing tag references remain valid.",
        )
    if row["in_use_count"] > 0:
        raise HTTPException(
            409,
            f"{row['in_use_count']} tag(s) still reference this unit. "
            f"Reassign or clear them first.",
        )

    db.execute(text("DELETE FROM engineering_units WHERE id = :id"), {"id": unit_id})
    db.commit()


# ---------------------------------------------------------------------------
# Auxiliary — list quantity kinds in use, useful for filter chips
# ---------------------------------------------------------------------------


@router.get("/_meta/quantity-kinds", response_model=list[str])
def list_quantity_kinds(db: Annotated[Session, Depends(get_session)]):
    """List all distinct quantity_kind values currently in the master.

    Used by the UI to render the grouped dropdown without hardcoding the
    list — if a new kind is added (e.g. user creates a custom 'radiation'
    unit), it appears automatically.
    """
    rows = db.execute(text("""
        SELECT DISTINCT quantity_kind
        FROM engineering_units
        WHERE quantity_kind IS NOT NULL
        ORDER BY quantity_kind
    """)).scalars().all()
    return list(rows)
