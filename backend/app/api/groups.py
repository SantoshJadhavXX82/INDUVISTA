"""CRUD endpoints for the groups master + per-tag membership management.
Phase 16.0h - audit() calls on every mutating endpoint.

Groups are user-defined logical classifications for tags. A tag belongs to
exactly one register_block (a Modbus polling unit) but can belong to many
groups - they're orthogonal.

Protection model:
  - Deleting a group with members is refused by default; pass ?force=true
    to delete anyway. The force=true audit explicitly notes the number of
    memberships destroyed - operations gold for "who blew away the
    PROD_GROUP at 02:18?".
  - Soft-disable (set enabled=false) is the safer move.

Endpoints:
  GET    /api/groups               list
  GET    /api/groups/{id}          one
  POST   /api/groups               create   [group.create]
  PATCH  /api/groups/{id}          update   [group.update or .toggle]
  DELETE /api/groups/{id}          delete   [group.delete]   (?force=bool)
  GET    /api/groups/_meta/group-types   (meta, not audited)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api/groups", tags=["groups"])


GroupType = Literal["AREA", "EQUIPMENT", "UNIT", "PACKAGE", "REPORT", "CUSTOM"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    group_type: GroupType = "CUSTOM"
    parent_group_id: int | None = None
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
    in_use_count: int = Field(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FETCH_SQL = """
    SELECT g.id, g.name, g.description, g.group_type,
           g.parent_group_id, p.name AS parent_group_name,
           g.display_order, g.enabled,
           g.created_at, g.updated_at,
           COALESCE((SELECT COUNT(*) FROM tag_group_memberships
                     WHERE group_id = g.id), 0) AS in_use_count
    FROM groups g
    LEFT JOIN groups p ON p.id = g.parent_group_id
"""


def _summarize_grp(row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "group_type": row["group_type"],
        "parent_group_id": row.get("parent_group_id"),
        "parent_group_name": row.get("parent_group_name"),
        "display_order": row.get("display_order"),
        "enabled": row.get("enabled"),
    }


def _full_grp(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "group_type": row["group_type"],
        "parent_group_id": row.get("parent_group_id"),
        "parent_group_name": row.get("parent_group_name"),
        "display_order": row.get("display_order"),
        "enabled": row.get("enabled"),
        "in_use_count": row.get("in_use_count"),
    }


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------


@router.get("", response_model=list[GroupResponse])
def list_groups(
    db: Annotated[Session, Depends(get_session)],
    group_type: str | None = Query(None),
    enabled: bool | None = Query(None),
    search: str | None = Query(None),
    parent_group_id: int | None = Query(None),
):
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
    row = db.execute(
        text(_FETCH_SQL + " WHERE g.id = :id"),
        {"id": group_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"Group {group_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Create / update / delete (audited)
# ---------------------------------------------------------------------------


@router.post("", response_model=GroupResponse, status_code=201)
def create_group(
    body: GroupCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.name

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
            audit(AuditEvent(
                action="group.create",
                target_type="group",
                target_label=target_label,
                summary=f"Denied: group '{body.name}' already exists",
                status="denied",
                error_message="duplicate name",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(409, f"A group named '{body.name}' already exists")

        if "foreign key" in msg or "violates foreign key" in msg:
            audit(AuditEvent(
                action="group.create",
                target_type="group",
                target_label=target_label,
                summary=f"Denied: parent_group_id {body.parent_group_id} doesn't exist",
                status="denied",
                error_message="FK violation: missing parent",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(400, f"parent_group_id {body.parent_group_id} doesn't exist")

        audit(AuditEvent(
            action="group.create",
            target_type="group",
            target_label=target_label,
            summary="INSERT failed (unclassified IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    audit(AuditEvent(
        action="group.create",
        target_type="group",
        target_id=new_id,
        target_label=target_label,
        summary=f"Created group '{body.name}' (type={body.group_type})",
        details=body.model_dump(),
    ), request)

    return get_group(new_id, db)


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: int,
    body: GroupUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "group.toggle" if is_toggle else "group.update"

    # Pre-fetch with parent name.
    existing = db.execute(
        text(_FETCH_SQL + " WHERE g.id = :id"),
        {"id": group_id},
    ).mappings().first()
    if not existing:
        audit(AuditEvent(
            action=action,
            target_type="group",
            target_id=group_id,
            summary=f"Denied: group {group_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"Group {group_id} not found")

    target_label = existing["name"]

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="group",
            target_id=group_id,
            target_label=target_label,
            summary="Denied: no fields provided",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "No changes provided")

    # Self-reference guard.
    if updates.get("parent_group_id") == group_id:
        audit(AuditEvent(
            action=action,
            target_type="group",
            target_id=group_id,
            target_label=target_label,
            summary="Denied: group cannot be its own parent",
            status="denied",
            error_message="self-reference parent_group_id",
            details={"request": updates, "before": _summarize_grp(existing)},
        ), request)
        raise HTTPException(400, "A group cannot be its own parent")

    set_clauses = [f"{k} = :{k}" for k in updates]
    set_clauses.append("updated_at = NOW()")
    sql = f"UPDATE groups SET {', '.join(set_clauses)} WHERE id = :id RETURNING id"

    try:
        db.execute(text(sql), {**updates, "id": group_id})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower() if hasattr(e, "orig") else str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            audit(AuditEvent(
                action=action,
                target_type="group",
                target_id=group_id,
                target_label=target_label,
                summary=f"Denied: name '{updates.get('name')}' collides with another group",
                status="denied",
                error_message="duplicate name",
                details={"request": updates, "before": _summarize_grp(existing)},
            ), request)
            raise HTTPException(409, f"Another group with name '{updates.get('name')}' already exists")
        audit(AuditEvent(
            action=action,
            target_type="group",
            target_id=group_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=str(e.orig),
            details={"request": updates, "before": _summarize_grp(existing)},
        ), request)
        raise HTTPException(400, f"Database constraint violation: {e.orig}")

    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} group '{existing['name']}'"
    else:
        summary = f"Updated group '{existing['name']}' ({', '.join(updates.keys())})"

    audit(AuditEvent(
        action=action,
        target_type="group",
        target_id=group_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_grp(existing),
        },
    ), request)

    return get_group(group_id, db)


@router.delete("/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    force: bool = Query(False, description="If true, delete even if the group has members"),
):
    """Delete a group. By default refuses if it has tag memberships.
    force=true bypasses that check and gets audited explicitly so
    compliance can find every forced deletion."""

    # Pre-fetch full row including in_use_count.
    row = db.execute(
        text(_FETCH_SQL + " WHERE g.id = :id"),
        {"id": group_id},
    ).mappings().first()
    if not row:
        audit(AuditEvent(
            action="group.delete",
            target_type="group",
            target_id=group_id,
            summary=f"Denied: group {group_id} not found",
            status="denied",
            error_message="not found",
            details={"force": force},
        ), request)
        raise HTTPException(404, f"Group {group_id} not found")

    target_label = row["name"]
    in_use = row["in_use_count"]

    if in_use > 0 and not force:
        audit(AuditEvent(
            action="group.delete",
            target_type="group",
            target_id=group_id,
            target_label=target_label,
            summary=f"Denied: '{row['name']}' has {in_use} tag membership(s); pass force=true to delete anyway",
            status="denied",
            error_message=f"in_use_count={in_use}, force=false",
            details={"before": _full_grp(row), "force": False},
        ), request)
        raise HTTPException(
            409,
            f"{in_use} tag(s) are members of this group. "
            f"Either remove them first, disable the group, or call with ?force=true to "
            f"delete anyway (memberships cascade - no tags are deleted).",
        )

    try:
        db.execute(text("DELETE FROM groups WHERE id = :id"), {"id": group_id})
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="group.delete",
            target_type="group",
            target_id=group_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_grp(row), "force": force},
        ), request)
        raise

    # Success - call out force flag explicitly when membership was destroyed.
    if force and in_use > 0:
        summary = (
            f"FORCE-deleted group '{row['name']}' "
            f"(removed {in_use} tag membership(s); tags not deleted)"
        )
    else:
        summary = f"Deleted group '{row['name']}'"

    audit(AuditEvent(
        action="group.delete",
        target_type="group",
        target_id=group_id,
        target_label=target_label,
        summary=summary,
        details={
            "before": _full_grp(row),
            "force": force,
            "memberships_destroyed": in_use if force else 0,
        },
    ), request)


# ---------------------------------------------------------------------------
# Auxiliary
# ---------------------------------------------------------------------------


@router.get("/_meta/group-types", response_model=list[str])
def list_group_types():
    return ["AREA", "EQUIPMENT", "UNIT", "PACKAGE", "REPORT", "CUSTOM"]
