"""Phase 14.2 - Alarms API.
Phase 16.0h - audit() calls on every mutating endpoint.

CRUD on alarm rules plus the operational views that drive the Alarms
page: currently-active list, historical event log, and the
acknowledge action.

The evaluator (Phase 14.3) is the writer of state transitions and
events. This module is read/write for rule config, read-only for state
(except for the `ack` action), and read-only for the event log
(except `ack` again, which inserts an `acked` event row).

Endpoints
---------

  POST   /api/alarms/rules                 create rule         [alarm_rule.create]
  GET    /api/alarms/rules                 list rules
  GET    /api/alarms/rules/{rule_id}       get one rule
  PATCH  /api/alarms/rules/{rule_id}       update rule         [alarm_rule.update or .toggle]
  DELETE /api/alarms/rules/{rule_id}       delete rule         [alarm_rule.delete]
  GET    /api/alarms/active                currently active alarms
  GET    /api/alarms/history               event log
  POST   /api/alarms/rules/{rule_id}/ack       acknowledge     [alarm.ack]
  GET    /api/alarms/shelved               currently shelved rules
  POST   /api/alarms/rules/{rule_id}/shelve    mute rule       [alarm.shelve]
  POST   /api/alarms/rules/{rule_id}/unshelve  unmute rule     [alarm.unshelve]

Notes
-----

  - severity vocabulary: critical / high / medium / low / info
  - rule types: hi_hi, hi, lo, lo_lo, deviation, rate_of_change
  - user_id on ack is accepted but not authenticated - auth lands in a
    later phase. For now it's a hint that gets stored on the event
    and on alarm_state.last_ack_user_id.
  - updated_at is bumped automatically by a DB trigger.

  16.0h: every mutating endpoint writes to audit_log (separate DB) in
  addition to its existing behavior. alarm_events keeps the operational
  per-rule history; audit_log is the cross-resource compliance log.
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


router = APIRouter(prefix="/api/alarms", tags=["alarms"])


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

RuleType = str
Severity = str
EventType = Literal[
    "activated", "cleared",
    "acked", "shelved", "unshelved",
    "disabled", "enabled",
]
StateValue = Literal[
    "normal",
    "active_unack", "active_ack",
    "inactive_unack",
    "shelved", "disabled",
]

ACTIVE_STATES = ("active_unack", "active_ack", "inactive_unack")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AlarmRuleCreate(BaseModel):
    tag_id:   int
    rule_type: RuleType
    severity:  Severity = "high"
    threshold: float
    deadband:  float = Field(0.0, ge=0.0)
    on_delay_sec:  int = Field(0, ge=0)
    off_delay_sec: int = Field(0, ge=0)
    latched: bool = False
    enabled: bool = True
    message_template: str | None = Field(None, max_length=500)
    window_seconds: int | None = Field(None, ge=1, le=86400)


class AlarmRuleUpdate(BaseModel):
    rule_type: RuleType | None = None
    severity:  Severity  | None = None
    threshold: float | None = None
    deadband:  float | None = Field(None, ge=0.0)
    on_delay_sec:  int | None = Field(None, ge=0)
    off_delay_sec: int | None = Field(None, ge=0)
    latched: bool | None = None
    enabled: bool | None = None
    message_template: str | None = Field(None, max_length=500)
    window_seconds: int | None = Field(None, ge=1, le=86400)


class AlarmRuleResponse(BaseModel):
    id: int
    tag_id: int
    tag_name: str | None = None
    rule_type: RuleType
    severity: Severity
    threshold: float
    deadband: float
    on_delay_sec: int
    off_delay_sec: int
    latched: bool
    enabled: bool
    message_template: str | None
    window_seconds: int | None
    created_at: datetime
    updated_at: datetime


class AlarmActive(BaseModel):
    rule_id: int
    tag_id: int
    tag_name: str
    engineering_unit: str | None
    rule_type: RuleType
    severity: Severity
    threshold: float
    state: StateValue
    last_change_time: datetime
    current_value: float | None
    current_quality: int | None
    last_ack_user_id: int | None
    last_ack_time: datetime | None
    shelved_until: datetime | None = None
    shelve_user_id: int | None = None
    message_template: str | None


class AlarmEventResponse(BaseModel):
    id: int
    rule_id: int
    tag_id: int
    tag_name: str | None
    event_time: datetime
    event_type: EventType
    value: float | None
    quality: int | None
    user_id: int | None
    comment: str | None


class AckRequest(BaseModel):
    user_id: int | None = Field(None)
    comment: str | None = Field(None, max_length=500)


class ShelveRequest(BaseModel):
    duration_minutes: int = Field(..., ge=1, le=43_200)
    user_id: int | None = None
    comment: str | None = Field(None, max_length=500)


class UnshelveRequest(BaseModel):
    user_id: int | None = None
    comment: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# Rules CRUD (audited - Phase 16.0h)
# ---------------------------------------------------------------------------

@router.post("/rules", response_model=AlarmRuleResponse, status_code=201)
def create_rule(
    body: AlarmRuleCreate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Create an alarm rule. DB trigger auto-creates alarm_state row."""

    # Pre-fetch tag name for audit context. Soft-fail if missing.
    tag_row = db.execute(
        text("SELECT name FROM tags WHERE id = :id"),
        {"id": body.tag_id},
    ).mappings().first()
    tag_name = tag_row["name"] if tag_row else f"tag#{body.tag_id} (missing)"
    target_label = f"{tag_name} {body.rule_type}"

    try:
        row = db.execute(text("""
            INSERT INTO alarm_rules (
                tag_id, rule_type, severity, threshold, deadband,
                on_delay_sec, off_delay_sec, latched, enabled,
                message_template, window_seconds
            )
            VALUES (
                :tag_id, :rule_type, :severity, :threshold, :deadband,
                :on_delay_sec, :off_delay_sec, :latched, :enabled,
                :message_template, :window_seconds
            )
            RETURNING id, tag_id, rule_type, severity, threshold, deadband,
                      on_delay_sec, off_delay_sec, latched, enabled,
                      message_template, window_seconds, created_at, updated_at
        """), body.model_dump()).mappings().first()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower()

        if "alarm_rules_unique_basic" in msg:
            audit(AuditEvent(
                action="alarm_rule.create",
                target_type="alarm_rule",
                target_label=target_label,
                summary=f"Denied: '{body.rule_type}' rule already exists on {tag_name}",
                status="denied",
                error_message="duplicate rule for tag",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(
                409,
                f"A '{body.rule_type}' rule already exists for tag "
                f"{body.tag_id}. The four level types (hi_hi, hi, lo, lo_lo) "
                f"are mutually exclusive per tag.",
            )

        if "alarm_rules_severity_fk" in msg or (
            "foreign key" in msg and "severity" in msg
        ):
            audit(AuditEvent(
                action="alarm_rule.create",
                target_type="alarm_rule",
                target_label=target_label,
                summary=f"Denied: unknown severity '{body.severity}'",
                status="denied",
                error_message=f"unknown severity: {body.severity}",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(
                400,
                f"Unknown severity '{body.severity}'. Add it under "
                f"Setup > Alarm Severities first, or pick an existing one.",
            )

        if "alarm_rules_rule_type_fk" in msg or (
            "foreign key" in msg and "rule_type" in msg
        ):
            audit(AuditEvent(
                action="alarm_rule.create",
                target_type="alarm_rule",
                target_label=target_label,
                summary=f"Denied: unknown rule_type '{body.rule_type}'",
                status="denied",
                error_message=f"unknown rule_type: {body.rule_type}",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(
                400,
                f"Unknown rule type '{body.rule_type}'. Add it under "
                f"Setup > Alarm Types first, or pick an existing one.",
            )

        if "foreign key" in msg or "tags" in msg:
            audit(AuditEvent(
                action="alarm_rule.create",
                target_type="alarm_rule",
                target_label=target_label,
                summary=f"Denied: tag {body.tag_id} does not exist",
                status="denied",
                error_message="tag not found",
                details={"request": body.model_dump()},
            ), request)
            raise HTTPException(
                422, f"tag_id {body.tag_id} does not exist",
            )

        # Unclassified IntegrityError.
        audit(AuditEvent(
            action="alarm_rule.create",
            target_type="alarm_rule",
            target_label=target_label,
            summary="INSERT failed (unclassified IntegrityError)",
            status="error",
            error_message=str(e.orig),
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(400, f"Constraint violation: {e.orig}")

    # Success.
    audit(AuditEvent(
        action="alarm_rule.create",
        target_type="alarm_rule",
        target_id=row["id"],
        target_label=target_label,
        summary=f"Created {body.rule_type} alarm on {tag_name} "
                f"(severity={body.severity}, threshold={body.threshold})",
        details=body.model_dump() | {"tag_name": tag_name},
    ), request)

    return _attach_tag_name(db, dict(row))


@router.get("/rules", response_model=list[AlarmRuleResponse])
def list_rules(
    db: Annotated[Session, Depends(get_session)],
    tag_id:   int | None = Query(None),
    severity: Severity | None = Query(None),
    enabled:  bool | None = Query(None),
):
    sql = """
        SELECT r.id, r.tag_id, t.name AS tag_name, r.rule_type, r.severity,
               r.threshold, r.deadband, r.on_delay_sec, r.off_delay_sec,
               r.latched, r.enabled, r.message_template, r.window_seconds,
               r.created_at, r.updated_at
        FROM alarm_rules r
        LEFT JOIN tags t ON t.id = r.tag_id
        WHERE 1=1
    """
    params: dict = {}
    if tag_id is not None:
        sql += " AND r.tag_id = :tag_id"
        params["tag_id"] = tag_id
    if severity is not None:
        sql += " AND r.severity = :severity"
        params["severity"] = severity
    if enabled is not None:
        sql += " AND r.enabled = :enabled"
        params["enabled"] = enabled
    sql += " ORDER BY t.name NULLS LAST, r.rule_type, r.id"

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/rules/{rule_id}", response_model=AlarmRuleResponse)
def get_rule(
    rule_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(text("""
        SELECT r.id, r.tag_id, t.name AS tag_name, r.rule_type, r.severity,
               r.threshold, r.deadband, r.on_delay_sec, r.off_delay_sec,
               r.latched, r.enabled, r.message_template, r.window_seconds,
               r.created_at, r.updated_at
        FROM alarm_rules r
        LEFT JOIN tags t ON t.id = r.tag_id
        WHERE r.id = :id
    """), {"id": rule_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"Alarm rule {rule_id} not found")
    return dict(row)


@router.patch("/rules/{rule_id}", response_model=AlarmRuleResponse)
def update_rule(
    rule_id: int,
    body: AlarmRuleUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Partial update. Anything you don't include stays as it was."""
    updates = body.model_dump(exclude_unset=True)
    is_toggle = (len(updates) == 1 and "enabled" in updates)
    action = "alarm_rule.toggle" if is_toggle else "alarm_rule.update"

    # Pre-fetch full existing row for 404 + before-snapshot.
    existing = db.execute(text("""
        SELECT r.id, r.tag_id, t.name AS tag_name, r.rule_type, r.severity,
               r.threshold, r.deadband, r.on_delay_sec, r.off_delay_sec,
               r.latched, r.enabled, r.message_template, r.window_seconds
        FROM alarm_rules r
        LEFT JOIN tags t ON t.id = r.tag_id
        WHERE r.id = :id
    """), {"id": rule_id}).mappings().first()
    if existing is None:
        audit(AuditEvent(
            action=action,
            target_type="alarm_rule",
            target_id=rule_id,
            summary=f"Denied: alarm rule {rule_id} not found",
            status="denied",
            error_message="not found",
            details={"request": updates},
        ), request)
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    target_label = f"{existing['tag_name']} {existing['rule_type']}"

    if not updates:
        audit(AuditEvent(
            action=action,
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary="Denied: no fields provided",
            status="denied",
            error_message="empty PATCH body",
        ), request)
        raise HTTPException(400, "No fields provided")

    set_clauses = [f"{k} = :{k}" for k in updates]
    sql = (
        f"UPDATE alarm_rules SET {', '.join(set_clauses)} "
        f"WHERE id = :id "
        f"RETURNING id, tag_id, rule_type, severity, threshold, deadband, "
        f"          on_delay_sec, off_delay_sec, latched, enabled, "
        f"          message_template, window_seconds, created_at, updated_at"
    )

    try:
        row = db.execute(text(sql), {**updates, "id": rule_id}).mappings().first()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower()

        denied_reason = None
        http_status = 400
        http_detail = f"Constraint violation: {e.orig}"

        if "alarm_rules_unique_basic" in msg:
            denied_reason = "rule_type collision with existing rule"
            http_status = 409
            http_detail = (
                "Updating rule_type would collide with an existing rule of "
                "the same type on this tag."
            )
        elif "alarm_rules_severity_fk" in msg or (
            "foreign key" in msg and "severity" in msg
        ):
            denied_reason = "unknown severity"
            http_detail = (
                "Unknown severity. Add it under Setup > Alarm "
                "Severities first, or pick an existing one."
            )
        elif "alarm_rules_rule_type_fk" in msg or (
            "foreign key" in msg and "rule_type" in msg
        ):
            denied_reason = "unknown rule_type"
            http_detail = (
                "Unknown rule type. Add it under Setup > Alarm "
                "Types first, or pick an existing one."
            )

        audit(AuditEvent(
            action=action,
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary=f"Denied: {denied_reason or 'constraint violation'}",
            status="denied" if denied_reason else "error",
            error_message=str(e.orig),
            details={"request": updates, "before": _summarize_rule(existing)},
        ), request)
        raise HTTPException(http_status, http_detail)

    # Success.
    if is_toggle:
        new_state = "Enabled" if updates["enabled"] else "Disabled"
        summary = f"{new_state} {existing['rule_type']} alarm on {existing['tag_name']}"
    else:
        summary = (
            f"Updated {existing['rule_type']} alarm on {existing['tag_name']} "
            f"({', '.join(updates.keys())})"
        )

    audit(AuditEvent(
        action=action,
        target_type="alarm_rule",
        target_id=rule_id,
        target_label=target_label,
        summary=summary,
        details={
            "changed_fields": list(updates.keys()),
            "request": updates,
            "before": _summarize_rule(existing),
        },
    ), request)

    return _attach_tag_name(db, dict(row))


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Delete a rule. alarm_state cascades; alarm_events history is preserved."""

    # Pre-fetch full row for compliance before-state.
    existing = db.execute(text("""
        SELECT r.id, r.tag_id, t.name AS tag_name, r.rule_type, r.severity,
               r.threshold, r.deadband, r.on_delay_sec, r.off_delay_sec,
               r.latched, r.enabled, r.message_template, r.window_seconds,
               r.created_at, r.updated_at
        FROM alarm_rules r
        LEFT JOIN tags t ON t.id = r.tag_id
        WHERE r.id = :id
    """), {"id": rule_id}).mappings().first()
    if existing is None:
        audit(AuditEvent(
            action="alarm_rule.delete",
            target_type="alarm_rule",
            target_id=rule_id,
            summary=f"Denied: alarm rule {rule_id} not found",
            status="denied",
            error_message="not found",
        ), request)
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    target_label = f"{existing['tag_name']} {existing['rule_type']}"

    try:
        db.execute(
            text("DELETE FROM alarm_rules WHERE id = :id"),
            {"id": rule_id},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm_rule.delete",
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary="DELETE failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"before": _full_rule(existing)},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm_rule.delete",
        target_type="alarm_rule",
        target_id=rule_id,
        target_label=target_label,
        summary=f"Deleted {existing['rule_type']} alarm on {existing['tag_name']} "
                f"(severity={existing['severity']})",
        details={"before": _full_rule(existing)},
    ), request)


# ---------------------------------------------------------------------------
# Active alarms view (read-only, not audited)
# ---------------------------------------------------------------------------

@router.get("/active", response_model=list[AlarmActive])
def list_active(
    db: Annotated[Session, Depends(get_session)],
    severity: Severity | None = Query(None),
):
    sql = """
        SELECT s.rule_id,
               r.tag_id, t.name AS tag_name,
               COALESCE(eu.label, t.engineering_unit) AS engineering_unit,
               r.rule_type, r.severity, r.threshold,
               s.state, s.last_change_time,
               s.current_value, s.current_quality,
               s.last_ack_user_id, s.last_ack_time,
               s.shelved_until, s.shelve_user_id,
               r.message_template
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        LEFT JOIN tags t ON t.id = r.tag_id
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        WHERE s.state = ANY(:states)
    """
    params: dict = {"states": list(ACTIVE_STATES)}
    if severity is not None:
        sql += " AND r.severity = :severity"
        params["severity"] = severity

    sql += """
        ORDER BY
            CASE r.severity
                WHEN 'critical' THEN 1
                WHEN 'high'     THEN 2
                WHEN 'medium'   THEN 3
                WHEN 'low'      THEN 4
                ELSE                  5
            END,
            s.last_change_time DESC
    """

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Event history (read-only)
# ---------------------------------------------------------------------------

@router.get("/history", response_model=list[AlarmEventResponse])
def list_history(
    db: Annotated[Session, Depends(get_session)],
    rule_id:    int | None = Query(None),
    tag_id:     int | None = Query(None),
    event_type: EventType | None = Query(None),
    start:      datetime | None = Query(None),
    end:        datetime | None = Query(None),
    limit:      int = Query(100, ge=1, le=1000),
):
    sql = """
        SELECT e.id, e.rule_id, e.tag_id, t.name AS tag_name,
               e.event_time, e.event_type, e.value, e.quality,
               e.user_id, e.comment
        FROM alarm_events e
        LEFT JOIN tags t ON t.id = e.tag_id
        WHERE 1=1
    """
    params: dict = {"limit": limit}
    if rule_id is not None:
        sql += " AND e.rule_id = :rule_id"
        params["rule_id"] = rule_id
    if tag_id is not None:
        sql += " AND e.tag_id = :tag_id"
        params["tag_id"] = tag_id
    if event_type is not None:
        sql += " AND e.event_type = :event_type"
        params["event_type"] = event_type
    if start is not None:
        sql += " AND e.event_time >= :start"
        params["start"] = start
    if end is not None:
        sql += " AND e.event_time < :end"
        params["end"] = end
    sql += " ORDER BY e.event_time DESC, e.id DESC LIMIT :limit"

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Operational actions: ack / shelve / unshelve - all audited
# ---------------------------------------------------------------------------

@router.post("/rules/{rule_id}/ack", response_model=AlarmEventResponse, status_code=201)
def ack_rule(
    rule_id: int,
    body: AckRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Acknowledge an active alarm.

    State machine transitions:
      active_unack   -> active_ack        (alarm still on, now acked)
      inactive_unack -> normal            (alarm cleared and now acked)
      anything else  -> 409 Conflict      (nothing to ack)

    Both transitions audit as `alarm.ack` with details.transition
    capturing which state moved to which.
    """
    state_row = db.execute(text("""
        SELECT s.state, s.current_value, s.current_quality,
               r.tag_id, r.rule_type, r.severity,
               t.name AS tag_name
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        LEFT JOIN tags t ON t.id = r.tag_id
        WHERE s.rule_id = :rule_id
    """), {"rule_id": rule_id}).mappings().first()

    if state_row is None:
        audit(AuditEvent(
            action="alarm.ack",
            target_type="alarm_rule",
            target_id=rule_id,
            summary=f"Denied: alarm rule {rule_id} not found",
            status="denied",
            error_message="not found",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    target_label = f"{state_row['tag_name']} {state_row['rule_type']}"
    current = state_row["state"]

    if current == "active_unack":
        new_state = "active_ack"
    elif current == "inactive_unack":
        new_state = "normal"
    else:
        audit(AuditEvent(
            action="alarm.ack",
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary=f"Denied: nothing to ack (state={current})",
            status="denied",
            error_message=f"state '{current}' is not ackable",
            details={"request": body.model_dump(), "state": current},
        ), request)
        raise HTTPException(
            409,
            f"Rule {rule_id} is in state '{current}'; nothing to acknowledge.",
        )

    try:
        db.execute(text("""
            UPDATE alarm_state
            SET state            = :new_state,
                last_change_time = NOW(),
                last_ack_user_id = :user_id,
                last_ack_time    = NOW()
            WHERE rule_id = :rule_id
        """), {
            "rule_id":   rule_id,
            "new_state": new_state,
            "user_id":   body.user_id,
        })

        event = db.execute(text("""
            INSERT INTO alarm_events (
                rule_id, tag_id, event_type, value, quality, user_id, comment
            )
            VALUES (
                :rule_id, :tag_id, 'acked', :value, :quality, :user_id, :comment
            )
            RETURNING id, rule_id, tag_id, event_time, event_type,
                      value, quality, user_id, comment
        """), {
            "rule_id": rule_id,
            "tag_id":  state_row["tag_id"],
            "value":   state_row["current_value"],
            "quality": state_row["current_quality"],
            "user_id": body.user_id,
            "comment": body.comment,
        }).mappings().first()
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm.ack",
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary="ack failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump(), "transition": f"{current}->{new_state}"},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm.ack",
        target_type="alarm_rule",
        target_id=rule_id,
        target_label=target_label,
        summary=f"Acknowledged {state_row['rule_type']} alarm on "
                f"{state_row['tag_name']} ({current} -> {new_state})",
        details={
            "transition": f"{current} -> {new_state}",
            "value_at_ack": state_row["current_value"],
            "quality_at_ack": state_row["current_quality"],
            "operator_note": body.comment,
            "user_id": body.user_id,
            "severity": state_row["severity"],
            "alarm_event_id": event["id"],
        },
    ), request)

    return _attach_tag_name(db, dict(event), tag_id_field="tag_id")


@router.get("/shelved", response_model=list[AlarmActive])
def list_shelved(db: Annotated[Session, Depends(get_session)]):
    sql = """
        SELECT s.rule_id,
               r.tag_id, t.name AS tag_name,
               COALESCE(eu.label, t.engineering_unit) AS engineering_unit,
               r.rule_type, r.severity, r.threshold,
               s.state, s.last_change_time,
               s.current_value, s.current_quality,
               s.last_ack_user_id, s.last_ack_time,
               s.shelved_until, s.shelve_user_id,
               r.message_template
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        LEFT JOIN tags t ON t.id = r.tag_id
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        WHERE s.state = 'shelved'
        ORDER BY s.shelved_until ASC NULLS LAST, s.last_change_time DESC
    """
    rows = db.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/rules/{rule_id}/shelve",
             response_model=AlarmEventResponse, status_code=201)
def shelve_rule(
    rule_id: int,
    body: ShelveRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Mute a rule for `duration_minutes`."""
    state_row = db.execute(text("""
        SELECT s.state, s.current_value, s.current_quality,
               r.tag_id, r.rule_type, r.severity,
               t.name AS tag_name
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        LEFT JOIN tags t ON t.id = r.tag_id
        WHERE s.rule_id = :rule_id
    """), {"rule_id": rule_id}).mappings().first()

    if state_row is None:
        audit(AuditEvent(
            action="alarm.shelve",
            target_type="alarm_rule",
            target_id=rule_id,
            summary=f"Denied: alarm rule {rule_id} not found",
            status="denied",
            error_message="not found",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    target_label = f"{state_row['tag_name']} {state_row['rule_type']}"

    if state_row["state"] == "disabled":
        audit(AuditEvent(
            action="alarm.shelve",
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary="Denied: rule is disabled (can't shelve)",
            status="denied",
            error_message="rule disabled",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(
            409,
            f"Rule {rule_id} is disabled; re-enable before shelving.",
        )

    try:
        db.execute(text("""
            UPDATE alarm_state
            SET state                = 'shelved',
                last_change_time     = NOW(),
                shelved_until        = NOW() + make_interval(mins => :dur),
                shelve_user_id       = :user_id,
                pending_active_since = NULL,
                pending_clear_since  = NULL
            WHERE rule_id = :rule_id
        """), {
            "rule_id": rule_id,
            "dur":     body.duration_minutes,
            "user_id": body.user_id,
        })
        event = db.execute(text("""
            INSERT INTO alarm_events (
                rule_id, tag_id, event_type, value, quality, user_id, comment
            )
            VALUES (
                :rule_id, :tag_id, 'shelved', :value, :quality, :user_id, :comment
            )
            RETURNING id, rule_id, tag_id, event_time, event_type,
                      value, quality, user_id, comment
        """), {
            "rule_id": rule_id,
            "tag_id":  state_row["tag_id"],
            "value":   state_row["current_value"],
            "quality": state_row["current_quality"],
            "user_id": body.user_id,
            "comment": body.comment
                       or f"shelved for {body.duration_minutes} min",
        }).mappings().first()
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm.shelve",
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary="shelve failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump()},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm.shelve",
        target_type="alarm_rule",
        target_id=rule_id,
        target_label=target_label,
        summary=f"Shelved {state_row['rule_type']} alarm on "
                f"{state_row['tag_name']} for {body.duration_minutes} min",
        details={
            "duration_minutes": body.duration_minutes,
            "previous_state": state_row["state"],
            "operator_note": body.comment,
            "user_id": body.user_id,
            "severity": state_row["severity"],
            "alarm_event_id": event["id"],
        },
    ), request)

    return _attach_tag_name(db, dict(event), tag_id_field="tag_id")


@router.post("/rules/{rule_id}/unshelve",
             response_model=AlarmEventResponse, status_code=201)
def unshelve_rule(
    rule_id: int,
    body: UnshelveRequest,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """End a shelve early."""
    state_row = db.execute(text("""
        SELECT s.state, s.current_value, s.current_quality,
               r.tag_id, r.rule_type, r.severity,
               t.name AS tag_name
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        LEFT JOIN tags t ON t.id = r.tag_id
        WHERE s.rule_id = :rule_id
    """), {"rule_id": rule_id}).mappings().first()

    if state_row is None:
        audit(AuditEvent(
            action="alarm.unshelve",
            target_type="alarm_rule",
            target_id=rule_id,
            summary=f"Denied: alarm rule {rule_id} not found",
            status="denied",
            error_message="not found",
            details={"request": body.model_dump()},
        ), request)
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    target_label = f"{state_row['tag_name']} {state_row['rule_type']}"

    if state_row["state"] != "shelved":
        audit(AuditEvent(
            action="alarm.unshelve",
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary=f"Denied: rule not shelved (state={state_row['state']})",
            status="denied",
            error_message=f"state '{state_row['state']}' is not shelved",
            details={"request": body.model_dump(), "state": state_row["state"]},
        ), request)
        raise HTTPException(
            409,
            f"Rule {rule_id} is in state '{state_row['state']}'; "
            f"nothing to unshelve.",
        )

    try:
        db.execute(text("""
            UPDATE alarm_state
            SET state                = 'normal',
                last_change_time     = NOW(),
                shelved_until        = NULL,
                shelve_user_id       = NULL,
                pending_active_since = NULL,
                pending_clear_since  = NULL
            WHERE rule_id = :rule_id
        """), {"rule_id": rule_id})
        event = db.execute(text("""
            INSERT INTO alarm_events (
                rule_id, tag_id, event_type, value, quality, user_id, comment
            )
            VALUES (
                :rule_id, :tag_id, 'unshelved', :value, :quality, :user_id, :comment
            )
            RETURNING id, rule_id, tag_id, event_time, event_type,
                      value, quality, user_id, comment
        """), {
            "rule_id": rule_id,
            "tag_id":  state_row["tag_id"],
            "value":   state_row["current_value"],
            "quality": state_row["current_quality"],
            "user_id": body.user_id,
            "comment": body.comment,
        }).mappings().first()
        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="alarm.unshelve",
            target_type="alarm_rule",
            target_id=rule_id,
            target_label=target_label,
            summary="unshelve failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"request": body.model_dump()},
        ), request)
        raise

    audit(AuditEvent(
        action="alarm.unshelve",
        target_type="alarm_rule",
        target_id=rule_id,
        target_label=target_label,
        summary=f"Unshelved {state_row['rule_type']} alarm on "
                f"{state_row['tag_name']}",
        details={
            "operator_note": body.comment,
            "user_id": body.user_id,
            "severity": state_row["severity"],
            "alarm_event_id": event["id"],
        },
    ), request)

    return _attach_tag_name(db, dict(event), tag_id_field="tag_id")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _attach_tag_name(
    db: Session,
    row: dict,
    tag_id_field: str = "tag_id",
) -> dict:
    """Populate `tag_name` on a row dict from the tags table."""
    tag_id = row.get(tag_id_field)
    if tag_id is None:
        row["tag_name"] = None
        return row
    name = db.execute(
        text("SELECT name FROM tags WHERE id = :id"),
        {"id": tag_id},
    ).scalar()
    row["tag_name"] = name
    return row


def _summarize_rule(row) -> dict[str, Any]:
    """Compact before-state for update audits."""
    return {
        "tag_id": row["tag_id"],
        "tag_name": row["tag_name"],
        "rule_type": row["rule_type"],
        "severity": row["severity"],
        "threshold": float(row["threshold"]) if row["threshold"] is not None else None,
        "enabled": row["enabled"],
        "latched": row["latched"],
    }


def _full_rule(row) -> dict[str, Any]:
    """Full before-state for delete audits. 365-day retention in audit DB."""
    return {
        "id": row["id"],
        "tag_id": row["tag_id"],
        "tag_name": row["tag_name"],
        "rule_type": row["rule_type"],
        "severity": row["severity"],
        "threshold": float(row["threshold"]) if row["threshold"] is not None else None,
        "deadband": float(row["deadband"]) if row["deadband"] is not None else None,
        "on_delay_sec": row["on_delay_sec"],
        "off_delay_sec": row["off_delay_sec"],
        "latched": row["latched"],
        "enabled": row["enabled"],
        "message_template": row["message_template"],
        "window_seconds": row["window_seconds"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
