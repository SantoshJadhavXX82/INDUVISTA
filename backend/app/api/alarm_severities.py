"""Phase 14.6 — Alarm severities admin API.

CRUD endpoints under /api/alarms/severities for managing the severity
master list. Five system severities are seeded by migration 0029 and
cannot be deleted (they CAN be re-labelled / re-coloured / re-ranked).

Operators can add custom severities (e.g. 'warning' between high and
medium, or 'emergency_stop' as priority-zero) and assign them to alarm
rules from the rule form.

Endpoints:
  GET    /api/alarms/severities         - list all, sorted by rank
  GET    /api/alarms/severities/{id}    - one row
  POST   /api/alarms/severities         - create a new (non-system) severity
  PATCH  /api/alarms/severities/{id}    - update label / color / rank
  DELETE /api/alarms/severities/{id}    - delete; 409 if is_system or in use

Validation:
  - code must match ^[a-z][a-z0-9_]*$ (DB enforces too)
  - color_hex must match ^#[0-9a-fA-F]{6}$ (DB enforces too)
  - rank must be 1..1000, unique (DB enforces unique)
  - code is immutable after creation
  - system rows: code and is_system can't be changed; everything else can
"""

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


router = APIRouter(prefix="/api/alarms/severities", tags=["alarms"])


_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------

class SeverityResponse(BaseModel):
    id: int
    code: str
    label: str
    color_hex: str
    rank: int
    is_system: bool
    in_use_count: int        # number of alarm_rules referencing this severity
    created_at: datetime
    updated_at: datetime


class SeverityCreate(BaseModel):
    code: str = Field(
        ..., min_length=1, max_length=50,
        description="Stable identifier. Lowercase letters, digits, "
                    "underscores. Must start with a letter.",
    )
    label: str = Field(..., min_length=1, max_length=100,
                       description="Human-readable name shown in dropdowns")
    color_hex: str = Field(
        ..., min_length=7, max_length=7,
        description="Hex color in #rrggbb form (e.g. '#dc2626' for red)",
    )
    rank: int = Field(
        ..., ge=1, le=1000,
        description="Priority order; lower = more urgent. Must be unique.",
    )


class SeverityUpdate(BaseModel):
    """All fields optional; only provided fields are updated. Code and
    is_system cannot be changed via this endpoint."""
    label: str | None = Field(None, min_length=1, max_length=100)
    color_hex: str | None = Field(None, min_length=7, max_length=7)
    rank: int | None = Field(None, ge=1, le=1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LIST_SQL = """
    SELECT s.id, s.code, s.label, s.color_hex, s.rank, s.is_system,
           COALESCE(r.in_use_count, 0) AS in_use_count,
           s.created_at, s.updated_at
    FROM alarm_severities s
    LEFT JOIN (
        SELECT severity, count(*)::int AS in_use_count
        FROM alarm_rules
        GROUP BY severity
    ) r ON r.severity = s.code
"""


def _validate_create_format(body: SeverityCreate) -> None:
    """Belt-and-braces: Pydantic Field constraints can't enforce regex
    precisely, so we double-check before INSERT. The DB CHECK
    constraints would also reject, but this gives a cleaner 400 error
    than letting it bubble up as a generic IntegrityError."""
    if not _CODE_RE.match(body.code):
        raise HTTPException(
            400, f"Invalid code '{body.code}'. Must match ^[a-z][a-z0-9_]*$"
        )
    if not _COLOR_RE.match(body.color_hex):
        raise HTTPException(
            400, f"Invalid color '{body.color_hex}'. Must match ^#[0-9a-fA-F]{{6}}$"
        )


def _validate_update_format(body: SeverityUpdate) -> None:
    if body.color_hex is not None and not _COLOR_RE.match(body.color_hex):
        raise HTTPException(
            400, f"Invalid color '{body.color_hex}'. Must match ^#[0-9a-fA-F]{{6}}$"
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[SeverityResponse])
def list_severities(db: Annotated[Session, Depends(get_session)]):
    """All severities sorted by rank (most-urgent first). Each row
    carries an in_use_count so the admin UI can warn before delete."""
    rows = db.execute(text(_LIST_SQL + " ORDER BY s.rank ASC")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{severity_id}", response_model=SeverityResponse)
def get_severity(
    severity_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text(_LIST_SQL + " WHERE s.id = :sid"),
        {"sid": severity_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, f"Severity {severity_id} not found")
    return dict(row)


@router.post("", response_model=SeverityResponse, status_code=201)
def create_severity(
    body: SeverityCreate,
    db: Annotated[Session, Depends(get_session)],
):
    """Create a new (non-system) severity. The code must be unique;
    duplicate code → 409. Rank must also be unique."""
    _validate_create_format(body)

    # Pre-check for code collision so we return a clean 409 instead of
    # an IntegrityError bubble. Race-condition-safe: the unique index
    # will still catch a concurrent insert.
    existing = db.execute(
        text("SELECT id FROM alarm_severities WHERE code = :code"),
        {"code": body.code},
    ).first()
    if existing:
        raise HTTPException(409, f"Severity code '{body.code}' already exists")

    rank_conflict = db.execute(
        text("SELECT id FROM alarm_severities WHERE rank = :rank"),
        {"rank": body.rank},
    ).first()
    if rank_conflict:
        raise HTTPException(
            409, f"Rank {body.rank} is already assigned to another severity"
        )

    try:
        db.execute(text("""
            INSERT INTO alarm_severities (code, label, color_hex, rank, is_system)
            VALUES (:code, :label, :color_hex, :rank, false)
        """), body.model_dump())
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_severity(
        severity_id=db.execute(
            text("SELECT id FROM alarm_severities WHERE code = :code"),
            {"code": body.code},
        ).scalar_one(),
        db=db,
    )


@router.patch("/{severity_id}", response_model=SeverityResponse)
def update_severity(
    severity_id: int,
    body: SeverityUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    """Update label / color / rank. code and is_system are immutable —
    even system rows can have label/color/rank edited (operators may
    want to rename 'Critical' to 'Trip' in a particular install)."""
    _validate_update_format(body)

    existing = db.execute(
        text("SELECT id, rank FROM alarm_severities WHERE id = :sid"),
        {"sid": severity_id},
    ).mappings().first()
    if existing is None:
        raise HTTPException(404, f"Severity {severity_id} not found")

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        # Nothing to update — return the current row.
        return get_severity(severity_id, db)

    # Rank uniqueness pre-check
    if "rank" in fields and fields["rank"] != existing["rank"]:
        conflict = db.execute(
            text("""
                SELECT id FROM alarm_severities
                WHERE rank = :rank AND id != :sid
            """),
            {"rank": fields["rank"], "sid": severity_id},
        ).first()
        if conflict:
            raise HTTPException(
                409, f"Rank {fields['rank']} is already assigned to another severity"
            )

    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "sid": severity_id}

    try:
        db.execute(
            text(f"UPDATE alarm_severities SET {set_clauses} WHERE id = :sid"),
            params,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_severity(severity_id, db)


@router.delete("/{severity_id}", status_code=204)
def delete_severity(
    severity_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Delete a severity. Rejected with 409 if:
      - The row is_system (one of the seeded 5)
      - Any alarm_rule references this severity (FK ON DELETE RESTRICT
        would reject too, but we give a friendlier error message)"""
    row = db.execute(
        text("""
            SELECT s.id, s.code, s.is_system,
                   (SELECT count(*) FROM alarm_rules
                    WHERE severity = s.code) AS in_use_count
            FROM alarm_severities s WHERE s.id = :sid
        """),
        {"sid": severity_id},
    ).mappings().first()

    if row is None:
        raise HTTPException(404, f"Severity {severity_id} not found")

    if row["is_system"]:
        raise HTTPException(
            409,
            f"Severity '{row['code']}' is a system severity and cannot "
            f"be deleted. Edit its label / color / rank instead.",
        )

    if row["in_use_count"] > 0:
        raise HTTPException(
            409,
            f"Severity '{row['code']}' is referenced by {row['in_use_count']} "
            f"alarm rule(s). Reassign those rules to a different severity first.",
        )

    try:
        db.execute(
            text("DELETE FROM alarm_severities WHERE id = :sid"),
            {"sid": severity_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return None
