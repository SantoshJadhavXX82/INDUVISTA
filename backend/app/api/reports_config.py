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
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Response
from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _ZoneInfo
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent
from app.config import settings

def _iv_watermark(db, ctx) -> str | None:
    """Watermark text for a render: DRAFT until approved, else the configured
    print watermark (system_settings 'report.print_watermark'); blank = none."""
    so = ctx.get("signoff") or {}
    status = str(so.get("status") or "").lower()
    if not (status in ("approved", "active") or so.get("approved_by")):
        return "DRAFT"
    try:
        v = db.execute(text("SELECT value FROM system_settings WHERE key = :k"),
                       {"k": "report.print_watermark"}).scalar()
        return (v or "").strip() or None
    except Exception:
        return None
from app.api.signatures import signing_state, report_signoff_required, signoff_block_reason  # Phase B-ESIG.2
from app.services.report_render import build_live_context, render_report
from app.services.report_formats import render_html, build_report_data, to_json, to_xml
from app.services.report_blocks import compile_blocks, build_block_context, collect_block_tag_ids
from app.services.report_aggregate import (
    resolve_report_context, DATA_FUNCTIONS, QUALITY_RULES, MISSING_ACTIONS,
    BAD_ACTIONS, VALUE_FORMATS,
)
from app.services.report_period import (
    PERIOD_TYPES, PERIOD_RULES, MISSING_PERIOD_HANDLING,
)
from app.services.report_validation import validate_report
from app.services.report_revisions import (
    create_draft, list_revisions, get_revision, activate_revision,
    effective_definition, document_header,
)
from app.services.report_jobs import record_job, list_jobs
from app.auth import get_current_user, CurrentUser, require_role, Role
from app.services.report_defaults import get_default_style, set_default_style

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
    template_mode: str = Field("html", pattern="^(html|blocks)$")
    template_blocks: list[dict[str, Any]] | None = None
    page_size: str = Field("A4", max_length=16)
    orientation: str = Field("portrait", pattern="^(portrait|landscape)$")
    report_code: str | None = Field(None, max_length=40)
    area: str | None = Field(None, max_length=64)
    equipment: str | None = Field(None, max_length=64)
    owner_dept: str | None = Field(None, max_length=64)
    enabled: bool = True


class DefinitionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    category: str | None = Field(None, pattern="^(event|periodic|on_demand)$")
    report_type: str | None = Field(None, max_length=32)
    template_html: str | None = None
    template_mode: str | None = Field(None, pattern="^(html|blocks)$")
    template_blocks: list[dict[str, Any]] | None = None
    page_size: str | None = Field(None, max_length=16)
    orientation: str | None = Field(None, pattern="^(portrait|landscape)$")
    report_code: str | None = Field(None, max_length=40)
    area: str | None = Field(None, max_length=64)
    equipment: str | None = Field(None, max_length=64)
    owner_dept: str | None = Field(None, max_length=64)
    enabled: bool | None = None


class DefinitionResponse(BaseModel):
    id: int
    name: str
    description: str | None
    category: str
    report_type: str | None
    template_html: str
    template_mode: str = "html"
    template_blocks: list[dict[str, Any]] | None = None
    page_size: str
    orientation: str
    report_code: str | None = None
    area: str | None = None
    equipment: str | None = None
    owner_dept: str | None = None
    enabled: bool
    active_revision_id: int | None = None
    status: str | None = None
    created_at: datetime
    updated_at: datetime
    trigger_ids: list[int] = []
    destination_ids: list[int] = []
    destination_fmts: dict[int, str] = {}


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
    days_of_week: str | None = Field(None, max_length=32)   # "0,2,4" = Mon,Wed,Fri (multi-day weekly)
    interval_minutes: int | None = Field(None, ge=1)         # every-N-minutes
    cron_expr: str | None = Field(None, max_length=120)
    # tag
    tag_id: int | None = None
    tag_edge: str = Field("to_nonzero", pattern="^(to_nonzero|rising|any_change)$")
    tag_op: str | None = Field(None, pattern="^(=|==|!=|>|>=|<|<=)$")
    tag_value: float | None = None
    tag_expr: str | None = Field(None, max_length=200)
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
    days_of_week: str | None = Field(None, max_length=32)
    interval_minutes: int | None = Field(None, ge=1)
    cron_expr: str | None = Field(None, max_length=120)
    tag_id: int | None = None
    tag_edge: str | None = Field(None, pattern="^(to_nonzero|rising|any_change)$")
    tag_op: str | None = Field(None, pattern="^(=|==|!=|>|>=|<|<=)$")
    tag_value: float | None = None
    tag_expr: str | None = Field(None, max_length=200)
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
    days_of_week: str | None
    interval_minutes: int | None
    cron_expr: str | None
    tag_id: int | None
    tag_edge: str | None
    tag_op: str | None
    tag_value: float | None
    tag_expr: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DestinationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    dest_type: str = Field(..., pattern="^(folder|network_drive|printer)$")
    target: str = Field(..., min_length=1)
    enabled: bool = True
    owner_report_id: int | None = None          # null = Global, set = private to a report
    default_fmts: str = Field("pdf", max_length=64)  # comma-separated default set


class DestinationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    dest_type: str | None = Field(None, pattern="^(folder|network_drive|printer)$")
    target: str | None = Field(None, min_length=1)
    enabled: bool | None = None
    owner_report_id: int | None = None
    default_fmts: str | None = Field(None, max_length=64)


class DestinationResponse(BaseModel):
    id: int
    name: str
    description: str | None
    dest_type: str
    target: str
    enabled: bool
    owner_report_id: int | None
    default_fmts: str
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
    drows = db.execute(text(
        "SELECT destination_id, fmt FROM report_destination_links WHERE report_id = :id"),
        {"id": def_id}).all()
    dids = [r[0] for r in drows]
    dfmts = {r[0]: (r[1] or "") for r in drows}   # NULL fmt = no override → ""
    data = dict(row)
    # Derived status (no stored column): a report is "active" once it has an
    # activated revision and is enabled; "disabled" if activated but turned off;
    # "draft" until first activation. The lifecycle that sets active_revision_id
    # lives in the revision endpoints below (Phase B3a).
    if data.get("active_revision_id"):
        status = "active" if data.get("enabled") else "disabled"
    else:
        status = "draft"
    return DefinitionResponse(**data, status=status, trigger_ids=tids,
                              destination_ids=dids, destination_fmts=dfmts)


@router.get("/definitions", response_model=list[DefinitionResponse])
def list_definitions(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text(
        "SELECT id FROM report_definitions ORDER BY name")).all()
    return [_def_row(db, r[0]) for r in rows]


# --- Theme Manager: global default report style --------------------------------
class DefaultStyleBody(BaseModel):
    theme: dict | None = None
    time: dict | None = None
    page: dict | None = None


@router.get("/default-style")
def read_default_style(db: Annotated[Session, Depends(get_session)],
                       user: CurrentUser = Depends(get_current_user)):
    """The system-wide default report style. Reports inherit this unless they
    add a report_style block that overrides specific keys."""
    return get_default_style(db)


@router.put("/default-style")
def write_default_style(body: DefaultStyleBody,
                        db: Annotated[Session, Depends(get_session)],
                        user: CurrentUser = Depends(require_role(Role.ADMIN))):
    payload = {k: v for k, v in
               {"theme": body.theme, "time": body.time, "page": body.page}.items()
               if v is not None}
    return set_default_style(db, payload)


@router.get("/definitions/{def_id}", response_model=DefinitionResponse)
def get_definition(def_id: int, db: Annotated[Session, Depends(get_session)]):
    return _def_row(db, def_id)


@router.post("/definitions", response_model=DefinitionResponse, status_code=201)
def create_definition(body: DefinitionCreate, request: Request,
                      db: Annotated[Session, Depends(get_session)]):
    try:
        params = body.model_dump()
        params["template_blocks"] = (
            json.dumps(params.get("template_blocks"))
            if params.get("template_blocks") is not None else None
        )
        new_id = db.execute(text("""
            INSERT INTO report_definitions
                (name, description, category, report_type, template_html,
                 template_mode, template_blocks,
                 page_size, orientation, report_code, area, equipment,
                 owner_dept, enabled)
            VALUES (:name, :description, :category, :report_type, :template_html,
                    :template_mode, CAST(:template_blocks AS JSONB),
                    :page_size, :orientation, :report_code, :area, :equipment,
                    :owner_dept, :enabled)
            RETURNING id
        """), params).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "report definition")
    # Auto-bind any tags referenced in the blocks into the Data tab so they
    # resolve at render time (keeps the Data tab a complete tag manifest).
    if _ensure_report_tags(db, new_id, collect_block_tag_ids(body.template_blocks or [])):
        db.commit()
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
    if "template_blocks" in fields:
        fields["template_blocks"] = (
            json.dumps(fields["template_blocks"])
            if fields["template_blocks"] is not None else None
        )
    sets = ", ".join(
        ("template_blocks = CAST(:template_blocks AS JSONB)" if k == "template_blocks"
         else f"{k} = :{k}")
        for k in fields
    )
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
    # Auto-bind newly referenced tags when the blocks were part of this update.
    if "template_blocks" in body.model_dump(exclude_unset=True) and body.template_blocks:
        if _ensure_report_tags(db, def_id, collect_block_tag_ids(body.template_blocks)):
            db.commit()
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
                 day_of_week, days_of_week, interval_minutes, cron_expr,
                 tag_id, tag_edge, tag_op, tag_value, tag_expr, enabled)
            VALUES (:name, :description, :trigger_type, :owner_report_id, :period,
                    :at_minute, :at_time_min, :day_of_month, :month_of_year,
                    :day_of_week, :days_of_week, :interval_minutes, :cron_expr,
                    :tag_id, :tag_edge, :tag_op, :tag_value, :tag_expr, :enabled)
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
            INSERT INTO report_destinations
                (name, description, dest_type, target, enabled, owner_report_id, default_fmts)
            VALUES (:name, :description, :dest_type, :target, :enabled, :owner_report_id, :default_fmts)
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
                     db: Annotated[Session, Depends(get_session)], fmt: str | None = None):
    # `fmt` is an OPTIONAL per-report override set, comma-separated (e.g.
    # "pdf,html"). Empty or missing means "no override" → the destination's
    # own default_fmts are used, and NULL is stored (see migration 0066).
    # A non-empty value must be a comma-separated subset of the allowed
    # formats; every token is validated individually.
    allowed = ("pdf", "html", "json", "xml", "csv")
    tokens = [t.strip().lower() for t in (fmt or "").split(",") if t.strip()]
    bad = [t for t in tokens if t not in allowed]
    if bad:
        raise HTTPException(
            400, "fmt must be a comma-separated subset of: pdf, html, json, xml, csv.")
    fmt_val = ",".join(tokens) if tokens else None   # None = use destination default
    try:
        db.execute(text("""
            INSERT INTO report_destination_links (report_id, destination_id, fmt)
            VALUES (:r, :d, :f)
            ON CONFLICT (report_id, destination_id) DO UPDATE SET fmt = :f
        """), {"r": def_id, "d": dest_id, "f": fmt_val})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "destination link")
    audit(AuditEvent(action="report_def.link_dest", target_type="report_definition",
                     target_id=def_id, summary=f"Linked destination {dest_id} (override={fmt_val or 'default'}) to report {def_id}"),
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



# ===========================================================================
# Render (on-demand) — DanPac category C
# ===========================================================================


# --------------------------------------------------------------- report tags
class ReportTagsBody(BaseModel):
    tag_ids: list[int] = Field(default_factory=list)


@router.get("/definitions/{def_id}/tags")
def get_report_tags(def_id: int, db: Annotated[Session, Depends(get_session)]):
    """Ordered tag ids (+ names) that this report includes."""
    rows = db.execute(text("""
        SELECT rt.tag_id, t.name, rt.position
        FROM report_tags rt
        LEFT JOIN tags t ON t.id = rt.tag_id
        WHERE rt.report_id = :rid
        ORDER BY rt.position, rt.tag_id
    """), {"rid": def_id}).mappings().all()
    return [{"tag_id": r["tag_id"], "name": r["name"], "position": r["position"]}
            for r in rows]


@router.put("/definitions/{def_id}/tags", status_code=204)
def set_report_tags(def_id: int, body: ReportTagsBody, request: Request,
                    db: Annotated[Session, Depends(get_session)]):
    """Replace the report's full tag list (ordered by the array order)."""
    exists = db.execute(text("SELECT 1 FROM report_definitions WHERE id = :id"),
                        {"id": def_id}).first()
    if not exists:
        raise HTTPException(404, f"Report definition {def_id} not found.")
    try:
        # Binding-aware: drop only tags no longer present (so the per-tag
        # binding columns on rows that remain are preserved), then upsert
        # positions. The richer binding config is set via PUT .../bindings.
        if body.tag_ids:
            db.execute(text(
                "DELETE FROM report_tags "
                "WHERE report_id = :rid AND NOT (tag_id = ANY(:keep))"
            ), {"rid": def_id, "keep": list(body.tag_ids)})
        else:
            db.execute(text("DELETE FROM report_tags WHERE report_id = :rid"),
                       {"rid": def_id})
        for pos, tid in enumerate(body.tag_ids):
            db.execute(text("""
                INSERT INTO report_tags (report_id, tag_id, position)
                VALUES (:r, :t, :p) ON CONFLICT (report_id, tag_id) DO UPDATE
                  SET position = EXCLUDED.position
            """), {"r": def_id, "t": tid, "p": pos})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "report tags")
    audit(AuditEvent(action="report_def.set_tags", target_type="report_definition",
                     target_id=def_id,
                     summary=f"Set {len(body.tag_ids)} tag(s) on report {def_id}"),
          request)


@router.delete("/definitions/{def_id}/tags", status_code=204)
def clear_report_tags(def_id: int, request: Request,
                      db: Annotated[Session, Depends(get_session)]):
    db.execute(text("DELETE FROM report_tags WHERE report_id = :rid"),
               {"rid": def_id})
    db.commit()
    audit(AuditEvent(action="report_def.clear_tags", target_type="report_definition",
                     target_id=def_id, summary=f"Cleared tags on report {def_id}"),
          request)


def _saved_report_tag_ids(db, def_id: int) -> list[int]:
    """Tag ids saved for a report (used as render fallback)."""
    rows = db.execute(text(
        "SELECT tag_id FROM report_tags WHERE report_id = :rid ORDER BY position, tag_id"
    ), {"rid": def_id}).fetchall()
    return [r[0] for r in rows]


def _ensure_report_tags(db, def_id: int, tag_ids: list[int]) -> int:
    """Auto-bind block-referenced tags that are missing from the Data tab.

    Adds rows to report_tags with the server defaults (data_function='latest',
    quality_rule='all'). Never removes or alters existing bindings, and skips
    ids that aren't real, non-deleted tags so a save can't fail on a stale id.
    Returns the number of bindings added. Caller commits.
    """
    if not tag_ids:
        return 0
    valid = {r[0] for r in db.execute(
        text("SELECT id FROM tags WHERE id = ANY(:ids) AND deleted_at IS NULL"),
        {"ids": list(set(tag_ids))}).fetchall()}
    existing = {r[0] for r in db.execute(
        text("SELECT tag_id FROM report_tags WHERE report_id = :r"),
        {"r": def_id}).fetchall()}
    seen: set[int] = set()
    missing: list[int] = []
    for t in tag_ids:
        if t in valid and t not in existing and t not in seen:
            seen.add(t)
            missing.append(t)
    if not missing:
        return 0
    pos = int(db.execute(text(
        "SELECT COALESCE(MAX(position), -1) FROM report_tags WHERE report_id = :r"),
        {"r": def_id}).scalar() or -1)
    for t in missing:
        pos += 1
        db.execute(text("""
            INSERT INTO report_tags (report_id, tag_id, position)
            VALUES (:r, :t, :p) ON CONFLICT (report_id, tag_id) DO NOTHING
        """), {"r": def_id, "t": t, "p": pos})
    return len(missing)


@router.post("/definitions/{def_id}/render")
def render_definition(
    def_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    tag_ids: Annotated[list[int], Query(description="Tags to bind into the report context")] = [],
    format: Annotated[str, Query(pattern="^(pdf|html|json|xml)$")] = "pdf",
):
    """Render a report definition's template to PDF using current live values."""
    # Phase B3b: if the report has an active revision, render from its snapshot;
    # otherwise from the live definition (unchanged).
    row = effective_definition(db, def_id)
    if not row:
        raise HTTPException(404, f"Report definition {def_id} not found.")
    active_rev_id = db.execute(text(
        "SELECT active_revision_id FROM report_definitions WHERE id = :id"),
        {"id": def_id}).scalar()
    tz_name = settings.app_timezone
    snapshot_at = _dt.now(_ZoneInfo("UTC"))
    # Fall back to the report's saved tags when none are passed explicitly,
    # so scheduled + on-demand renders use the same tag set.
    effective_tag_ids = list(tag_ids) if tag_ids else _saved_report_tag_ids(db, def_id)
    try:
        ctx, window = resolve_report_context(db, def_id, tz_name, snapshot_at, effective_tag_ids)
    except ValueError as e:
        # Period can't be resolved (e.g. batch/current with no open batch,
        # shift with no schedule, invalid custom offsets) — a client problem,
        # not a server error.
        raise HTTPException(400, f"Period cannot be resolved: {e}")
    # Let templates reference the report's own name/metadata.
    ctx["report"]["name"] = row["name"]
    ctx["report"]["category"] = row["category"]
    ctx["report"]["report_type"] = row["report_type"]
    # Phase C: document identity (B2 fields) + sign-off provenance.
    _hdr = document_header(db, def_id)
    ctx["report"].update(_hdr["identity"])
    ctx["signoff"] = _hdr["revision"]

    # data formats (json/xml) need only the computed data; html/pdf need a template.
    # A report authored in 'blocks' mode is compiled to HTML on the fly; 'html'
    # mode renders the raw Jinja2 template as before.
    mode = (row.get("template_mode") or "html")
    blocks = row.get("template_blocks") or None
    needs_template = format in ("pdf", "html")
    if needs_template:
        if mode == "blocks":
            if not blocks:
                raise HTTPException(400, "This report is in blocks mode but has no blocks to render.")
        elif not (row["template_html"] or "").strip():
            raise HTTPException(400, "This report has no template_html to render.")

    template_str = row["template_html"]
    if mode == "blocks" and blocks:
        template_str = compile_blocks(blocks, row["page_size"], row["orientation"], get_default_style(db), watermark=_iv_watermark(db, ctx))
        _bc = build_block_context(blocks, ctx.get("tags_list") or [], db=db, window=window)
        ctx["tables"] = _bc["tables"]
        ctx["charts"] = _bc["charts"]
        ctx["stats"] = _bc["stats"]

    out_bytes: bytes = b""
    media = "application/pdf"
    ext = "pdf"
    try:
        if format == "pdf":
            out_bytes = render_report(template_str, ctx, row["page_size"], row["orientation"])
            media, ext = "application/pdf", "pdf"
        elif format == "html":
            html = render_html(template_str, ctx, row["page_size"], row["orientation"])
            out_bytes = html.encode("utf-8")
            media, ext = "text/html; charset=utf-8", "html"
        elif format == "json":
            out_bytes = to_json(build_report_data(ctx))
            media, ext = "application/json", "json"
        elif format == "xml":
            out_bytes = to_xml(build_report_data(ctx))
            media, ext = "application/xml", "xml"
        status, err = "ok", None
    except Exception as e:  # render/serialize error -> record + 400
        status, err = "error", str(e)

    # Record the attempt either way (audit + history), with the actual fmt.
    db.execute(text("""
        INSERT INTO report_records
            (report_id, report_name, category, trigger_kind, snapshot_at,
             period_start, period_end, fmt, byte_size, status, error)
        VALUES (:rid, :name, :cat, 'manual', :snap, :ps, :pe, :fmt, :size, :status, :err)
    """), {
        "rid": def_id, "name": row["name"], "cat": row["category"],
        "snap": snapshot_at,
        "ps": (window[0] if window else None),
        "pe": (window[1] if window else None),
        "fmt": format,
        "size": (len(out_bytes) if status == "ok" else None),
        "status": status, "err": err,
    })
    record_job(db, report_id=def_id, report_name=row["name"], trigger_kind="on_demand",
               revision_id=active_rev_id, formats=format,
               status=("succeeded" if status == "ok" else "failed"),
               snapshot_at=snapshot_at,
               period_start=(window[0] if window else None),
               period_end=(window[1] if window else None),
               error=err, started_at=snapshot_at,
               finished_at=_dt.now(_ZoneInfo("UTC")))
    db.commit()

    audit(AuditEvent(action="report.render", target_type="report_definition",
                     target_id=def_id, target_label=row["name"],
                     summary=f"Rendered report '{row['name']}' on demand ({format}, {status})",
                     status=("success" if status == "ok" else "error"),
                     error_message=err), request)

    if status != "ok":
        raise HTTPException(400, f"Render failed: {err}")

    fname = f"{row['name'].replace(' ', '_')}_{snapshot_at.strftime('%Y%m%d_%H%M%S')}.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'} if format != "json" else {}
    return Response(content=out_bytes, media_type=media, headers=headers)


class PreviewBody(BaseModel):
    template_mode: str = Field("html", pattern="^(html|blocks)$")
    template_blocks: list[dict[str, Any]] | None = None
    template_html: str = ""
    page_size: str = Field("A4", max_length=16)
    orientation: str = Field("portrait", pattern="^(portrait|landscape)$")
    tag_ids: list[int] | None = None


@router.post("/definitions/{def_id}/preview")
def preview_definition(def_id: int, body: PreviewBody,
                       db: Annotated[Session, Depends(get_session)]):
    """Render IN-PROGRESS template edits to HTML without saving or activating.

    Uses the report's LIVE period/bindings (force_live) so editors see their
    current data configuration, then renders the template supplied in the
    request body. Never writes the definition, snapshot, history, or audit log —
    it is a pure read used by the Content tab's live preview.
    """
    meta = db.execute(text(
        "SELECT name, category, report_type FROM report_definitions WHERE id = :id"),
        {"id": def_id}).mappings().first()
    if not meta:
        raise HTTPException(404, f"Report definition {def_id} not found.")

    tz_name = settings.app_timezone
    ref = _dt.now(_ZoneInfo("UTC"))
    tag_ids = list(body.tag_ids) if body.tag_ids else _saved_report_tag_ids(db, def_id)
    # Resolve tags referenced in the (possibly unsaved) blocks too, so editors
    # see live data for cells they just added before binding them in the Data tab.
    if body.template_blocks:
        for tid in collect_block_tag_ids(body.template_blocks):
            if tid not in tag_ids:
                tag_ids.append(tid)
    try:
        ctx, _window = resolve_report_context(db, def_id, tz_name, ref, tag_ids, force_live=True)
    except ValueError as e:
        raise HTTPException(400, f"Period cannot be resolved: {e}")

    ctx["report"]["name"] = meta["name"]
    ctx["report"]["category"] = meta["category"]
    ctx["report"]["report_type"] = meta["report_type"]
    _hdr = document_header(db, def_id)
    ctx["report"].update(_hdr["identity"])
    ctx["signoff"] = _hdr["revision"]

    mode = body.template_mode or "html"
    blocks = body.template_blocks or None
    if mode == "blocks":
        if not blocks:
            raise HTTPException(400, "Nothing to preview — this report has no blocks yet.")
        template_str = compile_blocks(blocks, body.page_size, body.orientation, get_default_style(db), watermark=_iv_watermark(db, ctx))
        _bc = build_block_context(blocks, ctx.get("tags_list") or [], db=db, window=_window)
        ctx["tables"] = _bc["tables"]
        ctx["charts"] = _bc["charts"]
        ctx["stats"] = _bc["stats"]
    else:
        template_str = body.template_html or ""
        if not template_str.strip():
            raise HTTPException(400, "Nothing to preview — the template is empty.")

    try:
        html = render_html(template_str, ctx, body.page_size, body.orientation)
    except Exception as e:
        raise HTTPException(400, f"Preview render failed: {e}")
    return Response(content=html, media_type="text/html; charset=utf-8")


# ===========================================================================
# Period rule (data window) — Phase A. Absence => legacy live-snapshot mode.
# ===========================================================================
class PeriodRuleBody(BaseModel):
    period_type: str = Field(..., pattern="^(hourly|daily|weekly|monthly|shift|batch|custom)$")
    period_rule: str = Field("previous_completed",
                             pattern="^(previous_completed|current|custom)$")
    boundary_offset_min: int = 0
    custom_start_offset_min: int | None = None
    custom_end_offset_min: int | None = None
    allow_partial: bool = False
    late_data_wait_sec: int = 0
    grace_sec: int = 0
    missing_period_handling: str = Field("warn", pattern="^(warn|hold|fail)$")
    label_format: str = Field("yyyy-MM-dd HH:mm", max_length=32)
    filename_format: str = Field("yyyyMMdd_HHmm", max_length=32)
    enabled: bool = True


def _require_definition(db: Session, def_id: int) -> None:
    if not db.execute(text("SELECT 1 FROM report_definitions WHERE id = :id"),
                      {"id": def_id}).first():
        raise HTTPException(404, f"Report definition {def_id} not found.")


@router.get("/definitions/{def_id}/period-rule")
def get_period_rule(def_id: int, db: Annotated[Session, Depends(get_session)]):
    """The report's data-window rule, or null if it uses live-snapshot mode."""
    row = db.execute(text("SELECT * FROM report_period_rule WHERE report_id = :r"),
                     {"r": def_id}).mappings().first()
    return dict(row) if row else None


@router.put("/definitions/{def_id}/period-rule", status_code=204)
def set_period_rule(def_id: int, body: PeriodRuleBody, request: Request,
                    db: Annotated[Session, Depends(get_session)]):
    """Create/replace the report's data-window rule (enables period aggregation)."""
    _require_definition(db, def_id)
    if body.period_type == "custom" and (
        body.custom_start_offset_min is None or body.custom_end_offset_min is None
    ):
        raise HTTPException(
            422, "Custom period requires custom_start_offset_min and custom_end_offset_min.")
    db.execute(text("""
        INSERT INTO report_period_rule
            (report_id, period_type, period_rule, boundary_offset_min,
             custom_start_offset_min, custom_end_offset_min, allow_partial,
             late_data_wait_sec, grace_sec, missing_period_handling,
             label_format, filename_format, enabled, updated_at)
        VALUES (:r, :pt, :pr, :bo, :cs, :ce, :ap, :lw, :gr, :mh, :lf, :ff, :en, now())
        ON CONFLICT (report_id) DO UPDATE SET
            period_type = EXCLUDED.period_type,
            period_rule = EXCLUDED.period_rule,
            boundary_offset_min = EXCLUDED.boundary_offset_min,
            custom_start_offset_min = EXCLUDED.custom_start_offset_min,
            custom_end_offset_min = EXCLUDED.custom_end_offset_min,
            allow_partial = EXCLUDED.allow_partial,
            late_data_wait_sec = EXCLUDED.late_data_wait_sec,
            grace_sec = EXCLUDED.grace_sec,
            missing_period_handling = EXCLUDED.missing_period_handling,
            label_format = EXCLUDED.label_format,
            filename_format = EXCLUDED.filename_format,
            enabled = EXCLUDED.enabled,
            updated_at = now()
    """), {"r": def_id, "pt": body.period_type, "pr": body.period_rule,
           "bo": body.boundary_offset_min, "cs": body.custom_start_offset_min,
           "ce": body.custom_end_offset_min, "ap": body.allow_partial,
           "lw": body.late_data_wait_sec, "gr": body.grace_sec,
           "mh": body.missing_period_handling, "lf": body.label_format,
           "ff": body.filename_format, "en": body.enabled})
    db.commit()
    audit(AuditEvent(action="report_def.set_period_rule",
                     target_type="report_definition", target_id=def_id,
                     summary=f"Set period rule ({body.period_type}/{body.period_rule}) "
                             f"on report {def_id}"), request)


@router.delete("/definitions/{def_id}/period-rule", status_code=204)
def clear_period_rule(def_id: int, request: Request,
                      db: Annotated[Session, Depends(get_session)]):
    """Remove the data-window rule — report reverts to live-snapshot mode."""
    db.execute(text("DELETE FROM report_period_rule WHERE report_id = :r"),
               {"r": def_id})
    db.commit()
    audit(AuditEvent(action="report_def.clear_period_rule",
                     target_type="report_definition", target_id=def_id,
                     summary=f"Cleared period rule on report {def_id}"), request)


# ===========================================================================
# Data bindings — the rich per-tag config (function, quality rule, units, ...).
# ===========================================================================
class BindingItem(BaseModel):
    tag_id: int
    alias: str | None = Field(None, max_length=64)
    display_name: str | None = Field(None, max_length=120)
    unit_id: int | None = None
    data_function: str = "latest"
    quality_rule: str = "all"
    missing_action: str = "blank"
    bad_action: str = "blank"
    decimal_places: int | None = None
    value_format: str | None = None
    low_limit: float | None = None
    high_limit: float | None = None
    group_name: str | None = Field(None, max_length=64)
    required: bool = False


class BindingsBody(BaseModel):
    bindings: list[BindingItem] = Field(default_factory=list)


@router.get("/definitions/{def_id}/bindings")
def get_bindings(def_id: int, db: Annotated[Session, Depends(get_session)]):
    """Full per-tag binding config for the report, in display order."""
    rows = db.execute(text("""
        SELECT rt.tag_id, t.name AS tag_name, d.name AS device_name, rt.position, rt.alias, rt.display_name,
               rt.unit_id, rt.data_function, rt.quality_rule, rt.missing_action,
               rt.bad_action, rt.decimal_places, rt.value_format, rt.low_limit,
               rt.high_limit, rt.group_name, rt.required
        FROM report_tags rt
        LEFT JOIN tags t ON t.id = rt.tag_id
        LEFT JOIN devices d ON d.id = t.device_id
        WHERE rt.report_id = :r
        ORDER BY rt.position, rt.tag_id
    """), {"r": def_id}).mappings().all()
    return [dict(r) for r in rows]


@router.put("/definitions/{def_id}/bindings", status_code=204)
def set_bindings(def_id: int, body: BindingsBody, request: Request,
                 db: Annotated[Session, Depends(get_session)]):
    """Replace the report's full binding set (ordered by the array order)."""
    _require_definition(db, def_id)
    # Validate enums against the engine's canonical sets (single source of truth).
    for b in body.bindings:
        if b.data_function not in DATA_FUNCTIONS:
            raise HTTPException(422, f"Invalid data_function '{b.data_function}'.")
        if b.quality_rule not in QUALITY_RULES:
            raise HTTPException(422, f"Invalid quality_rule '{b.quality_rule}'.")
        if b.missing_action not in MISSING_ACTIONS:
            raise HTTPException(422, f"Invalid missing_action '{b.missing_action}'.")
        if b.bad_action not in BAD_ACTIONS:
            raise HTTPException(422, f"Invalid bad_action '{b.bad_action}'.")
        if b.value_format is not None and b.value_format not in VALUE_FORMATS:
            raise HTTPException(422, f"Invalid value_format '{b.value_format}'.")
    try:
        keep = [b.tag_id for b in body.bindings]
        if keep:
            db.execute(text(
                "DELETE FROM report_tags "
                "WHERE report_id = :r AND NOT (tag_id = ANY(:keep))"
            ), {"r": def_id, "keep": keep})
        else:
            db.execute(text("DELETE FROM report_tags WHERE report_id = :r"),
                       {"r": def_id})
        for pos, b in enumerate(body.bindings):
            db.execute(text("""
                INSERT INTO report_tags
                    (report_id, tag_id, position, alias, display_name, unit_id,
                     data_function, quality_rule, missing_action, bad_action,
                     decimal_places, value_format, low_limit, high_limit,
                     group_name, required)
                VALUES (:r, :t, :p, :al, :dn, :ui, :df, :qr, :ma, :ba, :dp, :vf,
                        :ll, :hl, :gn, :rq)
                ON CONFLICT (report_id, tag_id) DO UPDATE SET
                    position = EXCLUDED.position, alias = EXCLUDED.alias,
                    display_name = EXCLUDED.display_name, unit_id = EXCLUDED.unit_id,
                    data_function = EXCLUDED.data_function,
                    quality_rule = EXCLUDED.quality_rule,
                    missing_action = EXCLUDED.missing_action,
                    bad_action = EXCLUDED.bad_action,
                    decimal_places = EXCLUDED.decimal_places,
                    value_format = EXCLUDED.value_format,
                    low_limit = EXCLUDED.low_limit, high_limit = EXCLUDED.high_limit,
                    group_name = EXCLUDED.group_name, required = EXCLUDED.required
            """), {"r": def_id, "t": b.tag_id, "p": pos, "al": b.alias,
                   "dn": b.display_name, "ui": b.unit_id, "df": b.data_function,
                   "qr": b.quality_rule, "ma": b.missing_action, "ba": b.bad_action,
                   "dp": b.decimal_places, "vf": b.value_format, "ll": b.low_limit,
                   "hl": b.high_limit, "gn": b.group_name, "rq": b.required})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "report bindings")
    audit(AuditEvent(action="report_def.set_bindings",
                     target_type="report_definition", target_id=def_id,
                     summary=f"Set {len(body.bindings)} binding(s) on report {def_id}"),
          request)


# ===========================================================================
# Validation (Phase B1) — read-only readiness checks; the activation gate
# (Phase B3) will consult this before allowing a report to go active.
# ===========================================================================
@router.post("/definitions/{def_id}/validate")
def validate_definition(def_id: int, request: Request,
                        db: Annotated[Session, Depends(get_session)]):
    """Run configuration checks; return {overall, results[]} (passed/warning/failed/info)."""
    res = validate_report(db, def_id, settings.app_timezone)
    if res is None:
        raise HTTPException(404, f"Report definition {def_id} not found.")
    audit(AuditEvent(action="report.validate", target_type="report_definition",
                     target_id=def_id,
                     summary=f"Validated report {def_id}: {res['overall']}"), request)
    return res


# ===========================================================================
# Revisions (Phase B3a) — immutable config snapshots + activation lifecycle.
# Additive and runtime-dormant: render/scheduler still read the live config;
# making the active revision authoritative is Phase B3b.
# ===========================================================================
class RevisionCreate(BaseModel):
    notes: str | None = Field(None, max_length=500)


@router.post("/definitions/{def_id}/revisions", status_code=201)
def create_revision(def_id: int, body: RevisionCreate, request: Request,
                    db: Annotated[Session, Depends(get_session)],
                    user: CurrentUser = Depends(get_current_user)):
    """Snapshot the report's current config into a new draft revision."""
    rev = create_draft(db, def_id, user.username, body.notes, settings.app_timezone)
    if rev is None:
        raise HTTPException(404, f"Report definition {def_id} not found.")
    audit(AuditEvent(action="report.revision.create", target_type="report_definition",
                     target_id=def_id,
                     summary=f"Drafted revision {rev['revision_no']} of report {def_id}"),
          request)
    return rev


@router.get("/definitions/{def_id}/revisions")
def list_report_revisions(def_id: int, db: Annotated[Session, Depends(get_session)]):
    return list_revisions(db, def_id)


@router.get("/definitions/{def_id}/revisions/{rev_id}")
def get_report_revision(def_id: int, rev_id: int,
                        db: Annotated[Session, Depends(get_session)]):
    rev = get_revision(db, rev_id)
    if rev is None or rev["report_id"] != def_id:
        raise HTTPException(404, f"Revision {rev_id} not found for report {def_id}.")
    return rev


@router.post("/definitions/{def_id}/revisions/{rev_id}/activate")
def activate_report_revision(def_id: int, rev_id: int, request: Request,
                             db: Annotated[Session, Depends(get_session)],
                             user: CurrentUser = Depends(require_role(Role.APPROVER))):
    """Activate a draft revision (validation gate blocks hard failures only).

    Phase B5: activation requires the 'approver' role (or higher). Drafting and
    editing remain at engineer+; only the act of making a revision live is gated.
    """
    # Phase B-ESIG.2 - block activation unless the revision carries a complete,
    # intact, content-bound e-signature chain. Enabled via system_settings
    # 'esig.require_report_signoff' (default off preserves prior behavior).
    _ex = get_revision(db, rev_id)  # Phase B-ESIG.3 - existence check before the sign-off gate
    if _ex is None or _ex.get("report_id") != def_id:
        raise HTTPException(404, f"Revision {rev_id} not found for report {def_id}.")
    if report_signoff_required(db):
        _st = signing_state(db, "report_revision", rev_id)
        if not (_st["fully_signed"] and _st["chain_intact"] and _st["content_unchanged"]):
            _why = signoff_block_reason(_st)
            audit(AuditEvent(action="report.revision.activate.denied",
                             target_type="report_definition", target_id=def_id,
                             summary=f"Activation blocked (e-sig): {_why}",
                             status="denied", error_message=_why), request)
            raise HTTPException(409, f"Activation blocked: {_why}")
    rev, err = activate_revision(db, def_id, rev_id, user.username, settings.app_timezone)
    if err == "not_found":
        raise HTTPException(404, f"Revision {rev_id} not found for report {def_id}.")
    if err == "validation_failed":
        raise HTTPException(409, "Revision cannot be activated: validation has hard failures. "
                                 "Fix them and re-validate first.")
    if err:
        raise HTTPException(409, err)
    audit(AuditEvent(action="report.revision.activate", target_type="report_definition",
                     target_id=def_id,
                     summary=f"Activated revision {rev['revision_no']} of report {def_id}"),
          request)
    return rev


# ===========================================================================
# Jobs (Phase B4a) — trigger-level run log. On-demand renders are recorded
# here in addition to the per-format report_records; the scheduler joins in B4b.
# ===========================================================================
@router.get("/definitions/{def_id}/jobs")
def list_report_jobs(def_id: int, db: Annotated[Session, Depends(get_session)],
                     limit: Annotated[int, Query(ge=1, le=500)] = 50):
    return list_jobs(db, def_id, limit)


@router.get("/jobs")
def list_recent_jobs(db: Annotated[Session, Depends(get_session)],
                     limit: Annotated[int, Query(ge=1, le=500)] = 50):
    return list_jobs(db, None, limit)


# ===========================================================================
# Batch runs (override) — manual batch start/stop so the 'batch' period type
# can be exercised today with simulated tags. Later a tag-threshold worker can
# insert source='tag' runs; the period resolver (report_period.load_batch_window)
# treats both the same. At most one batch is open at a time (DB-enforced).
# ===========================================================================
class BatchStart(BaseModel):
    batch_no: str | None = Field(None, max_length=64)
    note: str | None = None


class BatchResponse(BaseModel):
    id: int
    batch_no: str | None
    started_at: datetime
    ended_at: datetime | None
    source: str
    note: str | None
    created_by: str | None
    created_at: datetime
    status: str  # 'open' | 'closed'


def _batch_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["status"] = "open" if d.get("ended_at") is None else "closed"
    return d


@router.post("/batches/start", response_model=BatchResponse, status_code=201)
def start_batch(body: BatchStart, request: Request,
                db: Annotated[Session, Depends(get_session)],
                user: CurrentUser = Depends(get_current_user)):
    """Override: open a batch run now. 409 if a batch is already open."""
    open_row = db.execute(text(
        "SELECT id FROM report_batches WHERE ended_at IS NULL LIMIT 1"
    )).first()
    if open_row:
        raise HTTPException(409, f"A batch is already open (id={open_row[0]}). "
                                 "Stop it before starting another.")
    try:
        row = db.execute(text("""
            INSERT INTO report_batches (batch_no, started_at, source, note, created_by)
            VALUES (:bn, now(), 'manual', :note, :by)
            RETURNING id, batch_no, started_at, ended_at, source, note, created_by, created_at
        """), {"bn": body.batch_no, "note": body.note, "by": user.username}).mappings().first()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise _integrity(e, "batch")
    audit(AuditEvent(action="report.batch.start", target_type="report_batch",
                     target_id=row["id"], summary=f"Started batch {row['batch_no'] or row['id']}"),
          request)
    return _batch_row(row)


@router.post("/batches/stop", response_model=BatchResponse)
def stop_batch(request: Request,
               db: Annotated[Session, Depends(get_session)],
               user: CurrentUser = Depends(get_current_user)):
    """Override: close the currently open batch run. 404 if none is open."""
    row = db.execute(text("""
        UPDATE report_batches SET ended_at = now()
        WHERE id = (SELECT id FROM report_batches WHERE ended_at IS NULL
                    ORDER BY started_at DESC LIMIT 1)
        RETURNING id, batch_no, started_at, ended_at, source, note, created_by, created_at
    """)).mappings().first()
    if not row:
        raise HTTPException(404, "No open batch to stop.")
    db.commit()
    audit(AuditEvent(action="report.batch.stop", target_type="report_batch",
                     target_id=row["id"], summary=f"Stopped batch {row['batch_no'] or row['id']}"),
          request)
    return _batch_row(row)


@router.get("/batches/current", response_model=BatchResponse | None)
def current_batch(db: Annotated[Session, Depends(get_session)]):
    """The currently open batch run, or null if none is open."""
    row = db.execute(text("""
        SELECT id, batch_no, started_at, ended_at, source, note, created_by, created_at
        FROM report_batches WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1
    """)).mappings().first()
    return _batch_row(row) if row else None


@router.get("/batches", response_model=list[BatchResponse])
def list_batches(db: Annotated[Session, Depends(get_session)],
                 limit: Annotated[int, Query(ge=1, le=500)] = 50):
    """Recent batch runs, newest first."""
    rows = db.execute(text("""
        SELECT id, batch_no, started_at, ended_at, source, note, created_by, created_at
        FROM report_batches ORDER BY started_at DESC LIMIT :lim
    """), {"lim": limit}).mappings().all()
    return [_batch_row(r) for r in rows]


@router.delete("/batches/{batch_id}", status_code=204)
def delete_batch(batch_id: int, request: Request,
                 db: Annotated[Session, Depends(get_session)],
                 user: CurrentUser = Depends(get_current_user)):
    """Delete a batch run (e.g. to remove a simulated/override run)."""
    res = db.execute(text("DELETE FROM report_batches WHERE id = :id"), {"id": batch_id})
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(404, f"Batch {batch_id} not found.")
    audit(AuditEvent(action="report.batch.delete", target_type="report_batch",
                     target_id=batch_id, summary=f"Deleted batch {batch_id}"), request)
    return Response(status_code=204)


