"""Phase 14.6b — Alarm rule-types admin API.

CRUD endpoints under /api/alarms/rule-types for managing the alarm
rule-type master list. Six system types are seeded by migration 0031
and cannot be deleted (they CAN be re-labelled / re-described /
re-ranked).

Operators can add custom rule types via this API for taxonomy /
documentation, but new types are always created with
`is_evaluable=false` — the evaluator only has logic for the system
types. Custom-type rules will be accepted into the database but
skipped by the evaluator. This is by design: real "new alarm logic"
requires shipping evaluator code, not just a row in a table.

Endpoints:
  GET    /api/alarms/rule-types          - list all, sorted by rank
  GET    /api/alarms/rule-types/{id}     - one row
  POST   /api/alarms/rule-types          - create custom rule type
  PATCH  /api/alarms/rule-types/{id}     - update label / description / rank
  DELETE /api/alarms/rule-types/{id}     - delete; 409 if is_system or in use

Validation:
  - code must match ^[a-z][a-z0-9_]*$, immutable, unique
  - rank must be 1..1000, unique
  - is_evaluable is system-managed (migrations flip it; API ignores it)
  - System rows: code / is_system / is_evaluable can't change; label,
    description, rank can
"""

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


router = APIRouter(prefix="/api/alarms/rule-types", tags=["alarms"])


_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------

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
    code: str = Field(
        ..., min_length=1, max_length=50,
        description="Stable identifier. Lowercase letters, digits, "
                    "underscores. Must start with a letter.",
    )
    label: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    rank: int = Field(..., ge=1, le=1000)


class RuleTypeUpdate(BaseModel):
    """Only label / description / rank are operator-editable. code,
    is_system and is_evaluable are immutable from the API — only
    migrations change them."""
    label: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    rank: int | None = Field(None, ge=1, le=1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[RuleTypeResponse])
def list_rule_types(db: Annotated[Session, Depends(get_session)]):
    """All rule types sorted by rank. Includes in_use_count per row
    so the admin UI can warn before delete."""
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
    db: Annotated[Session, Depends(get_session)],
):
    """Create a custom (non-system) rule type. New types are always
    created with is_evaluable=false — the evaluator has no logic for
    user-defined types. Rules using a non-evaluable type will be
    accepted by the API but skipped by the evaluator."""
    _validate_create_format(body)

    if db.execute(
        text("SELECT id FROM alarm_rule_types WHERE code = :code"),
        {"code": body.code},
    ).first():
        raise HTTPException(409, f"Rule type code '{body.code}' already exists")

    if db.execute(
        text("SELECT id FROM alarm_rule_types WHERE rank = :rank"),
        {"rank": body.rank},
    ).first():
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
    except Exception:
        db.rollback()
        raise

    new_id = db.execute(
        text("SELECT id FROM alarm_rule_types WHERE code = :code"),
        {"code": body.code},
    ).scalar_one()
    return get_rule_type(rule_type_id=new_id, db=db)


@router.patch("/{rule_type_id}", response_model=RuleTypeResponse)
def update_rule_type(
    rule_type_id: int,
    body: RuleTypeUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    """Update label / description / rank. code, is_system, is_evaluable
    are immutable from the API."""
    existing = db.execute(
        text("SELECT id, rank FROM alarm_rule_types WHERE id = :rid"),
        {"rid": rule_type_id},
    ).mappings().first()
    if existing is None:
        raise HTTPException(404, f"Rule type {rule_type_id} not found")

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_rule_type(rule_type_id, db)

    if "rank" in fields and fields["rank"] != existing["rank"]:
        if db.execute(
            text("""
                SELECT id FROM alarm_rule_types
                WHERE rank = :rank AND id != :rid
            """),
            {"rank": fields["rank"], "rid": rule_type_id},
        ).first():
            raise HTTPException(
                409,
                f"Rank {fields['rank']} is already assigned to another rule type",
            )

    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "rid": rule_type_id}

    try:
        db.execute(
            text(f"UPDATE alarm_rule_types SET {set_clauses} WHERE id = :rid"),
            params,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_rule_type(rule_type_id, db)


@router.delete("/{rule_type_id}", status_code=204)
def delete_rule_type(
    rule_type_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text("""
            SELECT t.id, t.code, t.is_system,
                   (SELECT count(*) FROM alarm_rules
                    WHERE rule_type = t.code) AS in_use_count
            FROM alarm_rule_types t WHERE t.id = :rid
        """),
        {"rid": rule_type_id},
    ).mappings().first()

    if row is None:
        raise HTTPException(404, f"Rule type {rule_type_id} not found")

    if row["is_system"]:
        raise HTTPException(
            409,
            f"Rule type '{row['code']}' is a system type and cannot "
            f"be deleted. Edit its label / description / rank instead.",
        )

    if row["in_use_count"] > 0:
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
    except Exception:
        db.rollback()
        raise

    return None
