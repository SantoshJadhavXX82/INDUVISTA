"""Reporting foundation API — report definitions, triggers, destinations.

Implements CRUD + linking for the DanPac reporting model (see migration
0059_report_foundation). Raw-SQL style consistent with the rest of the API.

  /api/report-config/definitions            report definitions (the templates)
  /api/report-config/triggers               timed/tag triggers (global + custom)
  /api/report-config/destinations           file/printer/network destinations
  /api/report-config/definitions/{id}/triggers/{tid}        link/unlink
  /api/report-config/definitions/{id}/destinations/{did}    link/unlink

This is configuration only — the trigger engine and render pipeline consume
this schema and are built next.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent

router = APIRouter(prefix="/api/report-config", tags=["report-config"])


# ===========================================================================
# Pydantic shapes
# ===========================================================================
class DefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    category: str = Field("periodic", pattern="^(event|periodic|on_demand)$")
    report_type: str | None = Field(None, max_length=32)
    template_html: str = ""
    page_size: str = Field("A4", max_length=16)
    orientation: str = Field("portrait", pattern="^(portrait|landscape)$")
    enabled: bool = True


class DefinitionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    category: str | None = Field(None, pattern="^(event|periodic|on_demand)$")
    report_type: str | None = Field(None, max_length=32)
    template_html: str | None = None
    page_size: str | None = Field(None, max_length=16)
    orientation: str | None = Field(None, pattern="^(portrait|landscape)$")
    enabled: bool | None = None


class DefinitionResponse(BaseModel):
    id: int
    name: str
    description: str | None
    category: str
    report_type: str | None
    template_html: str
    page_size: str
    orientation: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    trigger_ids: list[int] = []
    destination_ids: list[int] = []


class TriggerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    trigger_type: str = Field(..., pattern="^(timed|tag)$")
    owner_report_id: int | None = None  # NULL = global, set = custom
    # timed
    period: str | None = Field(None, max_length=32)  # open vocabulary
    at_minute: int | None = Field(None, ge=0, le=59)
    at_time_min: int | None = Field(None, ge=0, le=1439)
    day_of_month: int | None = Field(None, ge=1, le=28)
    month_of_year: int | None = Field(None, ge=1, le=12)
    day_of_week: int | None = Field(None, ge=0, le=6)        # 0=Mon..6=Sun (weekly)
    interval_minutes: int | None = Field(None, ge=1)         # every-N-minutes
    cron_expr: str | None = Field(None, max_length=120)
    # tag
    tag_id: int | None = None
    tag_edge: str = Field("to_nonzero", pattern="^(to_nonzero|rising|any_change)$")
    enabled: bool = True


class TriggerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    period: str | None = Field(None, max_length=32)  # open vocabulary
    at_minute: int | None = Field(None, ge=0, le=59)
    at_time_min: int | None = Field(None, ge=0, le=1439)
    day_of_month: int | None = Field(None, ge=1, le=28)
    month_of_year: int | None = Field(None, ge=1, le=12)
    day_of_week: int | None = Field(None, ge=0, le=6)
    interval_minutes: int | None = Field(None, ge=1)
    cron_expr: str | None = Field(None, max_length=120)
    tag_id: int | None = None
    tag_edge: str | None = Field(None, pattern="^(to_nonzero|rising|any_change)$")
    enabled: bool | None = None


class TriggerResponse(BaseModel):
    id: int
    name: str
    description: str | None
    trigger_type: str
    owner_report_id: int | None
    period: str | None
    at_minute: int | None
    at_time_min: int | None
    day_of_month: int | None
    month_of_year: int | None
    day_of_week: int | None
    interval_minutes: int | None
    cron_expr: str | None
    tag_id: int | None
    tag_edge: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DestinationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    dest_type: str = Field(..., pattern="^(folder|network_drive|printer)$")
    target: str = Field(..., min_length=1)
    enabled: bool = True


class DestinationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    dest_type: str | None = Field(None, pattern="^(folder|network_drive|printer)$")
    target: str | None = Field(None, min_length=1)
    enabled: bool | None = None


class DestinationResponse(BaseModel):
    id: int
    name: str
    description: str | None
    dest_type: str
    target: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Helpers
# ===========================================================================
def _integrity(e: IntegrityError, what: str) -> HTTPException:
    msg = str(getattr(e, "orig", e)).lower()
    if "unique" in msg or "duplicate" in msg:
        return HTTPException(409, f"A {what} with that name already exists.")
    if "foreign key" in msg:
        return HTTPException(400, f"{what}: referenced record does not exist.")
    if "check constraint" in msg or "violates check" in msg:
        return HTTPException(400, f"{what}: invalid field combination.")
    return HTTPException(400, f"{what}: database constraint violation.")


# ===========================================================================
# Definitions
# ===========================================================================
def _def_row(db: Session, def_id: int) -> DefinitionResponse:
    row = db.execute(text("SELECT * FROM report_definitions WHERE id = :id"),
                     {"id": def_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Report definition {def_id} not found.")
    tids = [r[0] for r in db.execute(text(
        "SELECT trigger_id FROM report_trigger_links WHERE report_id = :id"),
        {"id": def_id}).all()]
    dids = [r[0] for r in db.execute(text(
        "SELECT destination_id FROM report_destination_links WHERE report_id = :id"),
        {"id": def_id}).all()]
    return DefinitionResponse(**dict(row), trigger_ids=tids, destination_ids=dids)


@router.get("/definitions", response_model=list[DefinitionResponse])
def list_definitions(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text(
        "SELECT id FROM report_definitions ORDER BY name")).all()
    return [_def_row(db, r[0]) for r in rows]


@router.get("/definitions/{def_id}", response_model=DefinitionResponse)
def get_definition(def_id: int, db: Annotated[Session, Depends(get_session)]):
    return _def_row(db, def_id)


@router.post("/definitions", response_model=DefinitionResponse, status_code=201)
def create_definition(body: DefinitionCreate, request: Request,
                      db: Annotated[Session, Depends(get_session)]):
    try:
        new_id = db.execute(text("""
            INSERT INTO report_definitions
                (name, description, category, report_type, template_html,
                 page_size, orientation, enabled)
            VALUES (:name, :description, :category, :report_type, :template_html,
                    :page_size, :orientation, :enabled)
            RETURNING id
        """), body.model_dump()).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "report definition")
    audit(AuditEvent(action="report_def.create", target_type="report_definition",
                     target_id=new_id, target_label=body.name,
                     summary=f"Created report '{body.name}' ({body.category})",
                     details=body.model_dump()), request)
    return _def_row(db, new_id)


@router.patch("/definitions/{def_id}", response_model=DefinitionResponse)
def update_definition(def_id: int, body: DefinitionUpdate, request: Request,
                      db: Annotated[Session, Depends(get_session)]):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not fields:
        return _def_row(db, def_id)
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = def_id
    try:
        res = db.execute(text(
            f"UPDATE report_definitions SET {sets}, updated_at = NOW() "
            f"WHERE id = :id RETURNING id"), fields).first()
        if not res:
            raise HTTPException(404, f"Report definition {def_id} not found.")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "report definition")
    audit(AuditEvent(action="report_def.update", target_type="report_definition",
                     target_id=def_id, summary=f"Updated report definition {def_id}",
                     details=fields), request)
    return _def_row(db, def_id)


@router.delete("/definitions/{def_id}", status_code=204)
def delete_definition(def_id: int, request: Request,
                      db: Annotated[Session, Depends(get_session)]):
    res = db.execute(text("DELETE FROM report_definitions WHERE id = :id RETURNING name"),
                     {"id": def_id}).first()
    if not res:
        raise HTTPException(404, f"Report definition {def_id} not found.")
    db.commit()
    audit(AuditEvent(action="report_def.delete", target_type="report_definition",
                     target_id=def_id, target_label=res[0],
                     summary=f"Deleted report '{res[0]}'"), request)


# ===========================================================================
# Triggers
# ===========================================================================
@router.get("/triggers", response_model=list[TriggerResponse])
def list_triggers(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text(
        "SELECT * FROM report_triggers ORDER BY owner_report_id NULLS FIRST, name"
    )).mappings().all()
    return [TriggerResponse(**dict(r)) for r in rows]


@router.post("/triggers", response_model=TriggerResponse, status_code=201)
def create_trigger(body: TriggerCreate, request: Request,
                   db: Annotated[Session, Depends(get_session)]):
    try:
        new_id = db.execute(text("""
            INSERT INTO report_triggers
                (name, description, trigger_type, owner_report_id, period,
                 at_minute, at_time_min, day_of_month, month_of_year,
                 day_of_week, interval_minutes, cron_expr,
                 tag_id, tag_edge, enabled)
            VALUES (:name, :description, :trigger_type, :owner_report_id, :period,
                    :at_minute, :at_time_min, :day_of_month, :month_of_year,
                    :day_of_week, :interval_minutes, :cron_expr,
                    :tag_id, :tag_edge, :enabled)
            RETURNING id
        """), body.model_dump()).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "trigger")
    audit(AuditEvent(action="report_trigger.create", target_type="report_trigger",
                     target_id=new_id, target_label=body.name,
                     summary=f"Created {body.trigger_type} trigger '{body.name}'",
                     details=body.model_dump()), request)
    row = db.execute(text("SELECT * FROM report_triggers WHERE id = :id"),
                     {"id": new_id}).mappings().first()
    return TriggerResponse(**dict(row))


@router.patch("/triggers/{trig_id}", response_model=TriggerResponse)
def update_trigger(trig_id: int, body: TriggerUpdate, request: Request,
                   db: Annotated[Session, Depends(get_session)]):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not fields:
        row = db.execute(text("SELECT * FROM report_triggers WHERE id = :id"),
                         {"id": trig_id}).mappings().first()
        if not row:
            raise HTTPException(404, f"Trigger {trig_id} not found.")
        return TriggerResponse(**dict(row))
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = trig_id
    try:
        res = db.execute(text(
            f"UPDATE report_triggers SET {sets}, updated_at = NOW() "
            f"WHERE id = :id RETURNING id"), fields).first()
        if not res:
            raise HTTPException(404, f"Trigger {trig_id} not found.")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "trigger")
    audit(AuditEvent(action="report_trigger.update", target_type="report_trigger",
                     target_id=trig_id, summary=f"Updated trigger {trig_id}",
                     details=fields), request)
    row = db.execute(text("SELECT * FROM report_triggers WHERE id = :id"),
                     {"id": trig_id}).mappings().first()
    return TriggerResponse(**dict(row))


@router.delete("/triggers/{trig_id}", status_code=204)
def delete_trigger(trig_id: int, request: Request,
                   db: Annotated[Session, Depends(get_session)]):
    res = db.execute(text("DELETE FROM report_triggers WHERE id = :id RETURNING name"),
                     {"id": trig_id}).first()
    if not res:
        raise HTTPException(404, f"Trigger {trig_id} not found.")
    db.commit()
    audit(AuditEvent(action="report_trigger.delete", target_type="report_trigger",
                     target_id=trig_id, target_label=res[0],
                     summary=f"Deleted trigger '{res[0]}'"), request)


# ===========================================================================
# Destinations
# ===========================================================================
@router.get("/destinations", response_model=list[DestinationResponse])
def list_destinations(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text("SELECT * FROM report_destinations ORDER BY name")).mappings().all()
    return [DestinationResponse(**dict(r)) for r in rows]


@router.post("/destinations", response_model=DestinationResponse, status_code=201)
def create_destination(body: DestinationCreate, request: Request,
                       db: Annotated[Session, Depends(get_session)]):
    try:
        new_id = db.execute(text("""
            INSERT INTO report_destinations (name, description, dest_type, target, enabled)
            VALUES (:name, :description, :dest_type, :target, :enabled)
            RETURNING id
        """), body.model_dump()).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "destination")
    audit(AuditEvent(action="report_dest.create", target_type="report_destination",
                     target_id=new_id, target_label=body.name,
                     summary=f"Created {body.dest_type} destination '{body.name}'",
                     details=body.model_dump()), request)
    row = db.execute(text("SELECT * FROM report_destinations WHERE id = :id"),
                     {"id": new_id}).mappings().first()
    return DestinationResponse(**dict(row))


@router.patch("/destinations/{dest_id}", response_model=DestinationResponse)
def update_destination(dest_id: int, body: DestinationUpdate, request: Request,
                       db: Annotated[Session, Depends(get_session)]):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not fields:
        row = db.execute(text("SELECT * FROM report_destinations WHERE id = :id"),
                         {"id": dest_id}).mappings().first()
        if not row:
            raise HTTPException(404, f"Destination {dest_id} not found.")
        return DestinationResponse(**dict(row))
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = dest_id
    try:
        res = db.execute(text(
            f"UPDATE report_destinations SET {sets}, updated_at = NOW() "
            f"WHERE id = :id RETURNING id"), fields).first()
        if not res:
            raise HTTPException(404, f"Destination {dest_id} not found.")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "destination")
    audit(AuditEvent(action="report_dest.update", target_type="report_destination",
                     target_id=dest_id, summary=f"Updated destination {dest_id}",
                     details=fields), request)
    row = db.execute(text("SELECT * FROM report_destinations WHERE id = :id"),
                     {"id": dest_id}).mappings().first()
    return DestinationResponse(**dict(row))


@router.delete("/destinations/{dest_id}", status_code=204)
def delete_destination(dest_id: int, request: Request,
                       db: Annotated[Session, Depends(get_session)]):
    res = db.execute(text("DELETE FROM report_destinations WHERE id = :id RETURNING name"),
                     {"id": dest_id}).first()
    if not res:
        raise HTTPException(404, f"Destination {dest_id} not found.")
    db.commit()
    audit(AuditEvent(action="report_dest.delete", target_type="report_destination",
                     target_id=dest_id, target_label=res[0],
                     summary=f"Deleted destination '{res[0]}'"), request)


# ===========================================================================
# Linking (report <-> trigger, report <-> destination)
# ===========================================================================
@router.put("/definitions/{def_id}/triggers/{trig_id}", status_code=204)
def link_trigger(def_id: int, trig_id: int, request: Request,
                 db: Annotated[Session, Depends(get_session)]):
    try:
        db.execute(text("""
            INSERT INTO report_trigger_links (report_id, trigger_id)
            VALUES (:r, :t) ON CONFLICT DO NOTHING
        """), {"r": def_id, "t": trig_id})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "trigger link")
    audit(AuditEvent(action="report_def.link_trigger", target_type="report_definition",
                     target_id=def_id, summary=f"Linked trigger {trig_id} to report {def_id}"),
          request)


@router.delete("/definitions/{def_id}/triggers/{trig_id}", status_code=204)
def unlink_trigger(def_id: int, trig_id: int, request: Request,
                   db: Annotated[Session, Depends(get_session)]):
    db.execute(text("DELETE FROM report_trigger_links WHERE report_id = :r AND trigger_id = :t"),
               {"r": def_id, "t": trig_id})
    db.commit()
    audit(AuditEvent(action="report_def.unlink_trigger", target_type="report_definition",
                     target_id=def_id, summary=f"Unlinked trigger {trig_id} from report {def_id}"),
          request)


@router.put("/definitions/{def_id}/destinations/{dest_id}", status_code=204)
def link_destination(def_id: int, dest_id: int, request: Request,
                     db: Annotated[Session, Depends(get_session)], fmt: str = "pdf"):
    if fmt not in ("pdf", "csv"):
        raise HTTPException(400, "fmt must be 'pdf' or 'csv'.")
    try:
        db.execute(text("""
            INSERT INTO report_destination_links (report_id, destination_id, fmt)
            VALUES (:r, :d, :f)
            ON CONFLICT (report_id, destination_id) DO UPDATE SET fmt = :f
        """), {"r": def_id, "d": dest_id, "f": fmt})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "destination link")
    audit(AuditEvent(action="report_def.link_dest", target_type="report_definition",
                     target_id=def_id, summary=f"Linked destination {dest_id} ({fmt}) to report {def_id}"),
          request)


@router.delete("/definitions/{def_id}/destinations/{dest_id}", status_code=204)
def unlink_destination(def_id: int, dest_id: int, request: Request,
                       db: Annotated[Session, Depends(get_session)]):
    db.execute(text("DELETE FROM report_destination_links WHERE report_id = :r AND destination_id = :d"),
               {"r": def_id, "d": dest_id})
    db.commit()
    audit(AuditEvent(action="report_def.unlink_dest", target_type="report_definition",
                     target_id=def_id, summary=f"Unlinked destination {dest_id} from report {def_id}"),
          request)
