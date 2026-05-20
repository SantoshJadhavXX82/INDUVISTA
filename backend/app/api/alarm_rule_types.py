"""Phase 14.6b - Alarm rule-types admin API.
Phase 16.0h - audit() calls on every mutating endpoint.

CRUD endpoints under /api/alarms/rule-types for managing the alarm
rule-type master list. Six system types are seeded by migration 0031
and cannot be deleted (they CAN be re-labelled / re-described / re-ranked).

Endpoints:
  GET    /api/alarms/rule-types          - list all
  GET    /api/alarms/rule-types/{id}     - one row
  POST   /api/alarms/rule-types          - create   [alarm_rule_type.create]
  PATCH  /api/alarms/rule-types/{id}     - update   [alarm_rule_type.update]
  DELETE /api/alarms/rule-types/{id}     - delete   [alarm_rule_type.delete]
"""

import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api/alarms/rule-types", tags=["alarms"])


_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class RuleTypeResponse(BaseModel):
    id: int
    code: str
    label: str
    description: str | None
    rank: int
    is_system: bool
    is_evaluable: bool
    in_use_count: int
    created_at: datetime
    updated_at: datetime


class RuleTypeCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    label: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    rank: int = Field(..., ge=1, le=1000)


class RuleTypeUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    rank: int | None = Field(None, ge=1, le=1000)


_LIST_SQL = """
    SELECT t.id, t.code, t.label, t.description, t.rank, t.is_system,
           t.is_evaluable,
           COALESCE(r.in_use_count, 0) AS in_use_count,
           t.created_at, t.updated_at
    FROM alarm_rule_types t
    LEFT JOIN (
        SELECT rule_type, count(*)::int AS in_use_count
        FROM alarm_rules
        GROUP BY rule_type
    ) r ON r.rule_type = t.code
"""


def _validate_create_format(body: RuleTypeCreate) -> None:
    if not _CODE_RE.match(body.code):
        raise HTTPException(
            400, f"Invalid code '{body.code}'. Must match ^[a-z][a-z0-9_]*$"
        )


def _summarize_rt(row) -> dict[str, Any]:
    return {
        "code": row["code"],
        "label": row.get("label"),
        "description": row.get("description"),
        "rank": row["rank"],
        "is_system": row.get("is_system"),
        "is_evaluable": row.get("is_evaluable"),
    }


def _full_rt(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "label": row.get("label"),
        "description": row.get("description"),
        "rank": row.get("rank"),
        "is_system": row["is_system"],
        "is_evaluable": row.get("is_evaluable"),
        "in_use_count": row.get("in_use_count"),
    }


@router.get("", response_model=list[RuleTypeResponse])
def list_rule_types(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text(_LIST_SQL + " ORDER BY t.rank ASC")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{rule_type_id}", response_model=RuleTypeResponse)
def get_rule_type(
    rule_type_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text(_LIST_SQL + " WHERE t.id = :rid"),
        {"rid": rule_type_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, f"Rule type {rule_type_id} not found")
    return dict(row)


@router.post("", response_model=RuleTypeResponse, status_code=201)
def create_rule_type(
    body: RuleTypeCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a custom (non-system) rule type. is_evaluable=false."""
    target_label = body.code

    try:
        _validate_create_format(body)
    except HTTPException as e:
        audit(AuditEvent(
            action="alarm_rule_type.create",
            target_type="alarm_rule_type",
            target_label=target_label,
            summary="Denied: format validation failed",
            status="denied",
            error_message=str(e.detail),
            details={"request": body.model_dump()},
        ), request)
        raise

    if db.execute(
        text("SELECT id FROM alarm_rule_types WHERE code = :code"),
        {"code": body.code},
    ).first():
        audit(AuditEvent(
            action="alarm_rule_type.create",
            target_type="alarm_rule_type",
            target_label=target_label,
            summary=f"Denied: rule type code '{body.code}' already exists",
            status="denied",
            error_message="duplicate code",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(409, f"Rule type code '{body.code}' already exists")

    rank_conflict = db.execute(
        text("SELECT id, code FROM alarm_rule_types WHERE rank = :rank"),
        {"rank": body.rank},
    ).mappings().first()
    if rank_conflict:
        audit(AuditEvent(
            action="alarm_rule_type.create",
            target_type="alarm_rule_type",
            target_label=target_label,
            summary=f"Denied: rank {body.rank} already taken by '{rank_conflict['code']}'",
            status="denied",
            error_message=f"rank collision with rule_type #{rank_conflict['id']}",
            details={"request": body.model_dump(), "rank_owner": dict(rank_conflict)},
        ), request)
        raise HTTPException(
            409, f"Rank {body.rank} is already assigned to another rule type"
        )

    try:
        db.execute(text("""
            INSERT INTO alarm_rule_types
                (code, label, description, rank, is_system, is_evaluable)
            VALUES
                (:code, :label, :description, :rank, false, false)
        """), body.model_dump())
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm_rule_type.create",
            target_type="alarm_rule_type",
            target_label=target_label,
            summary="INSERT failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump()},
        ), request)
        raise

    new_id = db.execute(
        text("SELECT id FROM alarm_rule_types WHERE code = :code"),
        {"code": body.code},
    ).scalar_one()

    audit(AuditEvent(
        action="alarm_rule_type.create",
        target_type="alarm_rule_type",
        target_id=new_id,
        target_label=target_label,
        summary=f"Created rule type '{body.code}' ({body.label}, rank={body.rank}, is_evaluable=false)",
        details=body.model_dump() | {"is_evaluable": False},
    ), request)

    return get_rule_type(rule_type_id=new_id, db=db)


@router.patch("/{rule_type_id}", response_model=RuleTypeResponse)
def update_rule_type(
    rule_type_id: int,
    body: RuleTypeUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text(_LIST_SQL + " WHERE t.id = :rid"),
        {"rid": rule_type_id},
    ).mappings().first()
    if existing is None:
        audit(AuditEvent(
            action="alarm_rule_type.update",
            target_type="alarm_rule_type",
            target_id=rule_type_id,
            summary=f"Denied: rule type {rule_type_id} not found",
            status="denied",
            error_message="not found",
            details={"request": body.model_dump(exclude_unset=True)},
        ), request)
        raise HTTPException(404, f"Rule type {rule_type_id} not found")

    target_label = existing["code"]
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_rule_type(rule_type_id, db)

    if "rank" in fields and fields["rank"] != existing["rank"]:
        conflict = db.execute(
            text("SELECT id, code FROM alarm_rule_types WHERE rank = :rank AND id != :rid"),
            {"rank": fields["rank"], "rid": rule_type_id},
        ).mappings().first()
        if conflict:
            audit(AuditEvent(
                action="alarm_rule_type.update",
                target_type="alarm_rule_type",
                target_id=rule_type_id,
                target_label=target_label,
                summary=f"Denied: rank {fields['rank']} already taken by '{conflict['code']}'",
                status="denied",
                error_message=f"rank collision with rule_type #{conflict['id']}",
                details={"request": fields, "rank_owner": dict(conflict),
                         "before": _summarize_rt(existing)},
            ), request)
            raise HTTPException(
                409, f"Rank {fields['rank']} is already assigned to another rule type"
            )

    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "rid": rule_type_id}

    try:
        db.execute(
            text(f"UPDATE alarm_rule_types SET {set_clauses} WHERE id = :rid"),
            params,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm_rule_type.update",
            target_type="alarm_rule_type",
            target_id=rule_type_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": fields, "before": _summarize_rt(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm_rule_type.update",
        target_type="alarm_rule_type",
        target_id=rule_type_id,
        target_label=target_label,
        summary=f"Updated rule type '{existing['code']}' ({', '.join(fields.keys())})",
        details={
            "changed_fields": list(fields.keys()),
            "request": fields,
            "before": _summarize_rt(existing),
        },
    ), request)

    return get_rule_type(rule_type_id, db)


@router.delete("/{rule_type_id}", status_code=204)
def delete_rule_type(
    rule_type_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text("""
            SELECT t.id, t.code, t.label, t.description, t.rank,
                   t.is_system, t.is_evaluable,
                   (SELECT count(*) FROM alarm_rules
                    WHERE rule_type = t.code) AS in_use_count
            FROM alarm_rule_types t WHERE t.id = :rid
        """),
        {"rid": rule_type_id},
    ).mappings().first()

    if row is None:
        audit(AuditEvent(
            action="alarm_rule_type.delete",
            target_type="alarm_rule_type",
            target_id=rule_type_id,
            summary=f"Denied: rule type {rule_type_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"Rule type {rule_type_id} not found")

    target_label = row["code"]

    if row["is_system"]:
        audit(AuditEvent(
            action="alarm_rule_type.delete",
            target_type="alarm_rule_type",
            target_id=rule_type_id,
            target_label=target_label,
            summary=f"Denied: '{row['code']}' is a system rule type",
            status="denied",
            error_message="system rule types cannot be deleted",
            details={"before": _full_rt(row)},
        ), request)
        raise HTTPException(
            409,
            f"Rule type '{row['code']}' is a system type and cannot "
            f"be deleted. Edit its label / description / rank instead.",
        )

    if row["in_use_count"] > 0:
        audit(AuditEvent(
            action="alarm_rule_type.delete",
            target_type="alarm_rule_type",
            target_id=rule_type_id,
            target_label=target_label,
            summary=f"Denied: '{row['code']}' is referenced by {row['in_use_count']} rule(s)",
            status="denied",
            error_message=f"in_use_count={row['in_use_count']}",
            details={"before": _full_rt(row)},
        ), request)
        raise HTTPException(
            409,
            f"Rule type '{row['code']}' is referenced by {row['in_use_count']} "
            f"alarm rule(s). Reassign or delete those rules first.",
        )

    try:
        db.execute(
            text("DELETE FROM alarm_rule_types WHERE id = :rid"),
            {"rid": rule_type_id},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm_rule_type.delete",
            target_type="alarm_rule_type",
            target_id=rule_type_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_rt(row)},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm_rule_type.delete",
        target_type="alarm_rule_type",
        target_id=rule_type_id,
        target_label=target_label,
        summary=f"Deleted rule type '{row['code']}' ({row.get('label') or 'no label'})",
        details={"before": _full_rt(row)},
    ), request)

    return None
