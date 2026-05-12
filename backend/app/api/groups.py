"""CRUD endpoints for the groups master + per-tag membership management.

Groups are user-defined logical classifications for tags. A tag belongs to
exactly one register_block (a Modbus polling unit) but can belong to many
groups (Area, Equipment, Report, etc.) — they're orthogonal.

Schema already exists from Phase 1 baseline (migration 0001). This module
just exposes the CRUD layer; no schema change needed.

  Group types are constrained by a DB CHECK:
    AREA | EQUIPMENT | UNIT | PACKAGE | REPORT | CUSTOM

Protection model:
  - Deleting a group with members is allowed (cascade removes memberships,
    nothing else); the API returns in_use_count so the UI can warn first.
  - Soft-disable (set enabled=false) hides the group from filters and
    dropdowns without breaking memberships — usually the better move.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api/groups", tags=["groups"])


# The DB CHECK constraint requires uppercase exactly these tokens.
GroupType = Literal["AREA", "EQUIPMENT", "UNIT", "PACKAGE", "REPORT", "CUSTOM"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128,
                      description="Display name, unique across all groups")
    description: str | None = None
    group_type: GroupType = Field(
        "CUSTOM",
        description="Logical classification (AREA / EQUIPMENT / UNIT / PACKAGE / REPORT / CUSTOM)",
    )
    parent_group_id: int | None = Field(
        None, description="Optional parent for nesting (e.g. UNIT under AREA)"
    )
    display_order: int = 0
    enabled: bool = True


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    group_type: GroupType | None = None
    parent_group_id: int | None = None
    display_order: int | None = None
    enabled: bool | None = None


class GroupResponse(BaseModel):
    id: int
    name: str
    description: str | None
    group_type: str
    parent_group_id: int | None
    parent_group_name: str | None
    display_order: int
    enabled: bool
    created_at: datetime
    updated_at: datetime
    in_use_count: int = Field(
        0, description="Number of tags currently in this group"
    )


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


@router.get("", response_model=list[GroupResponse])
def list_groups(
    db: Annotated[Session, Depends(get_session)],
    group_type: str | None = Query(None, description="Filter by group_type"),
    enabled: bool | None = Query(None, description="Filter by enabled flag"),
    search: str | None = Query(None, description="Match on name or description (case-insensitive)"),
    parent_group_id: int | None = Query(None, description="Filter by parent group"),
):
    """List all groups, optionally filtered.

    Ordered by group_type, then display_order, then name. The in_use_count
    is a left-join aggregate so empty groups appear as 0 (not missing).
    """
    sql = """
        SELECT g.id, g.name, g.description, g.group_type,
               g.parent_group_id, p.name AS parent_group_name,
               g.display_order, g.enabled,
               g.created_at, g.updated_at,
               COALESCE(usage.cnt, 0) AS in_use_count
        FROM groups g
        LEFT JOIN groups p ON p.id = g.parent_group_id
        LEFT JOIN (
            SELECT group_id, COUNT(*) AS cnt
            FROM tag_group_memberships
            GROUP BY group_id
        ) usage ON usage.group_id = g.id
        WHERE TRUE
    """
    params: dict[str, object] = {}
    if group_type is not None:
        sql += " AND g.group_type = :group_type"
        params["group_type"] = group_type
    if enabled is not None:
        sql += " AND g.enabled = :enabled"
        params["enabled"] = enabled
    if parent_group_id is not None:
        sql += " AND g.parent_group_id = :parent_group_id"
        params["parent_group_id"] = parent_group_id
    if search:
        sql += " AND (LOWER(g.name) LIKE :q OR LOWER(COALESCE(g.description, '')) LIKE :q)"
        params["q"] = f"%{search.lower()}%"
    sql += " ORDER BY g.group_type, g.display_order, LOWER(g.name)"

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{group_id}", response_model=GroupResponse)
def get_group(group_id: int, db: Annotated[Session, Depends(get_session)]):
    row = db.execute(text("""
        SELECT g.id, g.name, g.description, g.group_type,
               g.parent_group_id, p.name AS parent_group_name,
               g.display_order, g.enabled,
               g.created_at, g.updated_at,
               COALESCE((
                   SELECT COUNT(*) FROM tag_group_memberships
                   WHERE group_id = g.id
               ), 0) AS in_use_count
        FROM groups g
        LEFT JOIN groups p ON p.id = g.parent_group_id
        WHERE g.id = :id
    """), {"id": group_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Group {group_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------


@router.post("", response_model=GroupResponse, status_code=201)
def create_group(
    body: GroupCreate,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a new group.

    parent_group_id must reference an existing group (if provided). The DB
    CHECK constraint enforces it can't be self.
    """
    try:
        new_id = db.execute(text("""
            INSERT INTO groups (
                name, description, group_type, parent_group_id,
                display_order, enabled
            )
            VALUES (
                :name, :description, :group_type, :parent_group_id,
                :display_order, :enabled
            )
            RETURNING id
        """), body.model_dump()).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(409, f"A group named '{body.name}' already exists")
        if "foreign key" in msg or "violates foreign key" in msg:
            raise HTTPException(400, f"parent_group_id {body.parent_group_id} doesn't exist")
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    return get_group(new_id, db)


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: int,
    body: GroupUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    """Update a group. Cannot set parent_group_id to self (DB CHECK)."""
    existing = db.execute(
        text("SELECT id FROM groups WHERE id = :id"),
        {"id": group_id},
    ).mappings().first()
    if not existing:
        raise HTTPException(404, f"Group {group_id} not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No changes provided")

    # Self-reference guard at API level (DB also has CHECK, but friendlier here)
    if updates.get("parent_group_id") == group_id:
        raise HTTPException(400, "A group cannot be its own parent")

    set_clauses = [f"{k} = :{k}" for k in updates]
    set_clauses.append("updated_at = NOW()")
    sql = (
        f"UPDATE groups SET {', '.join(set_clauses)} "
        f"WHERE id = :id RETURNING id"
    )

    try:
        db.execute(text(sql), {**updates, "id": group_id})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(409, f"Another group with name '{updates.get('name')}' already exists")
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    return get_group(group_id, db)


@router.delete("/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    db: Annotated[Session, Depends(get_session)],
    force: bool = Query(False, description="If true, delete even if the group has members"),
):
    """Delete a group.

    By default, refuses if the group has tag memberships — the UI should
    warn first. Pass ?force=true to delete anyway (memberships cascade,
    no tags are lost, only the group association).
    """
    row = db.execute(text("""
        SELECT COALESCE((SELECT COUNT(*) FROM tag_group_memberships
                         WHERE group_id = :id), 0) AS in_use_count
        FROM groups WHERE id = :id
    """), {"id": group_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Group {group_id} not found")

    if row["in_use_count"] > 0 and not force:
        raise HTTPException(
            409,
            f"{row['in_use_count']} tag(s) are members of this group. "
            f"Either remove them first, disable the group, or call with ?force=true to "
            f"delete anyway (memberships cascade — no tags are deleted).",
        )

    db.execute(text("DELETE FROM groups WHERE id = :id"), {"id": group_id})
    db.commit()


# ---------------------------------------------------------------------------
# Auxiliary: list distinct group_types in use (for filter dropdowns)
# ---------------------------------------------------------------------------


@router.get("/_meta/group-types", response_model=list[str])
def list_group_types():
    """Return the canonical list of group types.

    Static (matches the DB CHECK constraint). Returned as an endpoint so
    the UI doesn't have to hardcode them and changes here propagate.
    """
    return ["AREA", "EQUIPMENT", "UNIT", "PACKAGE", "REPORT", "CUSTOM"]
