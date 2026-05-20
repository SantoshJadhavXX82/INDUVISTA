"""Phase 14.6 - Alarm severities admin API.
Phase 16.0h - audit() calls on every mutating endpoint.

CRUD endpoints under /api/alarms/severities for managing the severity
master list. Five system severities are seeded by migration 0029 and
cannot be deleted (they CAN be re-labelled / re-coloured / re-ranked).

Endpoints:
  GET    /api/alarms/severities         - list all, sorted by rank
  GET    /api/alarms/severities/{id}    - one row
  POST   /api/alarms/severities         - create   [alarm_severity.create]
  PATCH  /api/alarms/severities/{id}    - update   [alarm_severity.update]
  DELETE /api/alarms/severities/{id}    - delete   [alarm_severity.delete]

Validation:
  - code must match ^[a-z][a-z0-9_]*$ (DB enforces too)
  - color_hex must match ^#[0-9a-fA-F]{6}$
  - rank must be 1..1000, unique
  - code is immutable after creation
  - system rows: code and is_system can't be changed; everything else can
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


router = APIRouter(prefix="/api/alarms/severities", tags=["alarms"])


_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SeverityResponse(BaseModel):
    id: int
    code: str
    label: str
    color_hex: str
    rank: int
    is_system: bool
    in_use_count: int
    created_at: datetime
    updated_at: datetime


class SeverityCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    label: str = Field(..., min_length=1, max_length=100)
    color_hex: str = Field(..., min_length=7, max_length=7)
    rank: int = Field(..., ge=1, le=1000)


class SeverityUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    color_hex: str | None = Field(None, min_length=7, max_length=7)
    rank: int | None = Field(None, ge=1, le=1000)


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


def _summarize_sev(row) -> dict[str, Any]:
    return {
        "code": row["code"],
        "label": row.get("label"),
        "color_hex": row.get("color_hex"),
        "rank": row["rank"],
        "is_system": row.get("is_system"),
    }


def _full_sev(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "label": row.get("label"),
        "color_hex": row.get("color_hex"),
        "rank": row.get("rank"),
        "is_system": row["is_system"],
        "in_use_count": row.get("in_use_count"),
    }


@router.get("", response_model=list[SeverityResponse])
def list_severities(db: Annotated[Session, Depends(get_session)]):
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
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    target_label = body.code

    try:
        _validate_create_format(body)
    except HTTPException as e:
        audit(AuditEvent(
            action="alarm_severity.create",
            target_type="alarm_severity",
            target_label=target_label,
            summary="Denied: format validation failed",
            status="denied",
            error_message=str(e.detail),
            details={"request": body.model_dump()},
        ), request)
        raise

    existing = db.execute(
        text("SELECT id FROM alarm_severities WHERE code = :code"),
        {"code": body.code},
    ).first()
    if existing:
        audit(AuditEvent(
            action="alarm_severity.create",
            target_type="alarm_severity",
            target_label=target_label,
            summary=f"Denied: severity code '{body.code}' already exists",
            status="denied",
            error_message="duplicate code",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(409, f"Severity code '{body.code}' already exists")

    rank_conflict = db.execute(
        text("SELECT id, code FROM alarm_severities WHERE rank = :rank"),
        {"rank": body.rank},
    ).mappings().first()
    if rank_conflict:
        audit(AuditEvent(
            action="alarm_severity.create",
            target_type="alarm_severity",
            target_label=target_label,
            summary=f"Denied: rank {body.rank} already taken by '{rank_conflict['code']}'",
            status="denied",
            error_message=f"rank collision with severity #{rank_conflict['id']}",
            details={"request": body.model_dump(), "rank_owner": dict(rank_conflict)},
        ), request)
        raise HTTPException(
            409, f"Rank {body.rank} is already assigned to another severity"
        )

    try:
        db.execute(text("""
            INSERT INTO alarm_severities (code, label, color_hex, rank, is_system)
            VALUES (:code, :label, :color_hex, :rank, false)
        """), body.model_dump())
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm_severity.create",
            target_type="alarm_severity",
            target_label=target_label,
            summary="INSERT failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump()},
        ), request)
        raise

    new_id = db.execute(
        text("SELECT id FROM alarm_severities WHERE code = :code"),
        {"code": body.code},
    ).scalar_one()

    audit(AuditEvent(
        action="alarm_severity.create",
        target_type="alarm_severity",
        target_id=new_id,
        target_label=target_label,
        summary=f"Created severity '{body.code}' ({body.label}, rank={body.rank})",
        details=body.model_dump(),
    ), request)

    return get_severity(severity_id=new_id, db=db)


@router.patch("/{severity_id}", response_model=SeverityResponse)
def update_severity(
    severity_id: int,
    body: SeverityUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text(_LIST_SQL + " WHERE s.id = :sid"),
        {"sid": severity_id},
    ).mappings().first()
    if existing is None:
        audit(AuditEvent(
            action="alarm_severity.update",
            target_type="alarm_severity",
            target_id=severity_id,
            summary=f"Denied: severity {severity_id} not found",
            status="denied",
            error_message="not found",
            details={"request": body.model_dump(exclude_unset=True)},
        ), request)
        raise HTTPException(404, f"Severity {severity_id} not found")

    target_label = existing["code"]
    fields = body.model_dump(exclude_unset=True)

    try:
        _validate_update_format(body)
    except HTTPException as e:
        audit(AuditEvent(
            action="alarm_severity.update",
            target_type="alarm_severity",
            target_id=severity_id,
            target_label=target_label,
            summary="Denied: format validation failed",
            status="denied",
            error_message=str(e.detail),
            details={"request": fields, "before": _summarize_sev(existing)},
        ), request)
        raise

    if not fields:
        return get_severity(severity_id, db)

    if "rank" in fields and fields["rank"] != existing["rank"]:
        conflict = db.execute(
            text("SELECT id, code FROM alarm_severities WHERE rank = :rank AND id != :sid"),
            {"rank": fields["rank"], "sid": severity_id},
        ).mappings().first()
        if conflict:
            audit(AuditEvent(
                action="alarm_severity.update",
                target_type="alarm_severity",
                target_id=severity_id,
                target_label=target_label,
                summary=f"Denied: rank {fields['rank']} already taken by '{conflict['code']}'",
                status="denied",
                error_message=f"rank collision with severity #{conflict['id']}",
                details={"request": fields, "rank_owner": dict(conflict),
                         "before": _summarize_sev(existing)},
            ), request)
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
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm_severity.update",
            target_type="alarm_severity",
            target_id=severity_id,
            target_label=target_label,
            summary="UPDATE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": fields, "before": _summarize_sev(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm_severity.update",
        target_type="alarm_severity",
        target_id=severity_id,
        target_label=target_label,
        summary=f"Updated severity '{existing['code']}' ({', '.join(fields.keys())})",
        details={
            "changed_fields": list(fields.keys()),
            "request": fields,
            "before": _summarize_sev(existing),
        },
    ), request)

    return get_severity(severity_id, db)


@router.delete("/{severity_id}", status_code=204)
def delete_severity(
    severity_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text("""
            SELECT s.id, s.code, s.label, s.color_hex, s.rank, s.is_system,
                   (SELECT count(*) FROM alarm_rules
                    WHERE severity = s.code) AS in_use_count
            FROM alarm_severities s WHERE s.id = :sid
        """),
        {"sid": severity_id},
    ).mappings().first()

    if row is None:
        audit(AuditEvent(
            action="alarm_severity.delete",
            target_type="alarm_severity",
            target_id=severity_id,
            summary=f"Denied: severity {severity_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"Severity {severity_id} not found")

    target_label = row["code"]

    if row["is_system"]:
        audit(AuditEvent(
            action="alarm_severity.delete",
            target_type="alarm_severity",
            target_id=severity_id,
            target_label=target_label,
            summary=f"Denied: '{row['code']}' is a system severity",
            status="denied",
            error_message="system severities cannot be deleted",
            details={"before": _full_sev(row)},
        ), request)
        raise HTTPException(
            409,
            f"Severity '{row['code']}' is a system severity and cannot "
            f"be deleted. Edit its label / color / rank instead.",
        )

    if row["in_use_count"] > 0:
        audit(AuditEvent(
            action="alarm_severity.delete",
            target_type="alarm_severity",
            target_id=severity_id,
            target_label=target_label,
            summary=f"Denied: '{row['code']}' is referenced by {row['in_use_count']} rule(s)",
            status="denied",
            error_message=f"in_use_count={row['in_use_count']}",
            details={"before": _full_sev(row)},
        ), request)
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
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm_severity.delete",
            target_type="alarm_severity",
            target_id=severity_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_sev(row)},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm_severity.delete",
        target_type="alarm_severity",
        target_id=severity_id,
        target_label=target_label,
        summary=f"Deleted severity '{row['code']}' ({row.get('label') or 'no label'})",
        details={"before": _full_sev(row)},
    ), request)

    return None
