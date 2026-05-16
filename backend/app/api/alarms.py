"""Phase 14.2 — Alarms API.

CRUD on alarm rules plus the operational views that drive the Alarms
page: currently-active list, historical event log, and the
acknowledge action.

The evaluator (Phase 14.3) is the writer of state transitions and
events. This module is read/write for rule config, read-only for state
(except for the `ack` action), and read-only for the event log
(except `ack` again, which inserts an `acked` event row).

Endpoints
---------

  POST   /api/alarms/rules                 create a rule
  GET    /api/alarms/rules                 list rules (filter: tag, severity, enabled)
  GET    /api/alarms/rules/{rule_id}       get one rule
  PATCH  /api/alarms/rules/{rule_id}       update rule
  DELETE /api/alarms/rules/{rule_id}       delete rule (cascades state row,
                                           preserves event history)
  GET    /api/alarms/active                currently active alarms
  GET    /api/alarms/history               event log (paginated, filterable)
  POST   /api/alarms/rules/{rule_id}/ack   acknowledge an active alarm

Notes
-----

  - severity vocabulary: critical / high / medium / low / info
  - rule types: hi_hi, hi, lo, lo_lo, deviation, rate_of_change
  - user_id on ack is accepted but not authenticated — auth lands in a
    later phase. For now it's a hint that gets stored on the event
    and on alarm_state.last_ack_user_id.
  - updated_at is bumped automatically by a DB trigger; the API never
    has to set it manually on UPDATE.
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


router = APIRouter(prefix="/api/alarms", tags=["alarms"])


# ---------------------------------------------------------------------------
# Vocabularies — must match the CHECK constraints in migration 0028
# ---------------------------------------------------------------------------

RuleType = Literal[
    "hi_hi", "hi", "lo", "lo_lo", "deviation", "rate_of_change",
]
Severity = Literal["critical", "high", "medium", "low", "info"]
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

# Which states count as "active" for the operator-facing list. Excludes
# `normal` (boring), `shelved` (operator muted them), and `disabled`
# (operator turned the rule off).
ACTIVE_STATES = ("active_unack", "active_ack", "inactive_unack")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AlarmRuleCreate(BaseModel):
    tag_id:   int
    rule_type: RuleType
    severity:  Severity = "high"
    threshold: float
    deadband:  float = Field(0.0, ge=0.0,
                             description="Process value must move beyond "
                                         "(threshold +/- deadband) before "
                                         "the state can flip")
    on_delay_sec:  int = Field(0, ge=0,
                               description="Hold the trigger condition for this "
                                           "many seconds before activating "
                                           "(chatter filter)")
    off_delay_sec: int = Field(0, ge=0,
                               description="Hold the clear condition for this "
                                           "many seconds before deactivating")
    latched: bool = Field(False,
                          description="If true, an active alarm stays active "
                                      "until acknowledged even if the value "
                                      "returns to normal")
    enabled: bool = True
    message_template: str | None = Field(
        None, max_length=500,
        description="Optional message rendered when the alarm fires. "
                    "Supports {tag_name}, {value}, {threshold} substitutions "
                    "in the evaluator.",
    )


class AlarmRuleUpdate(BaseModel):
    # tag_id is intentionally NOT updatable — changing the tag would
    # invalidate the event history. Delete + recreate instead.
    rule_type: RuleType | None = None
    severity:  Severity  | None = None
    threshold: float | None = None
    deadband:  float | None = Field(None, ge=0.0)
    on_delay_sec:  int | None = Field(None, ge=0)
    off_delay_sec: int | None = Field(None, ge=0)
    latched: bool | None = None
    enabled: bool | None = None
    message_template: str | None = Field(None, max_length=500)


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
    created_at: datetime
    updated_at: datetime


class AlarmActive(BaseModel):
    """One row in the operator's currently-active alarms list."""
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
    # Phase 14.4 — shelve fields, NULL for non-shelved rows. Including
    # them in the active shape too (not just /shelved) so a single client
    # type covers both views.
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
    user_id: int | None = Field(
        None,
        description="Operator's user id, if available. Stored on the event "
                    "and on alarm_state.last_ack_user_id. Auth wiring "
                    "comes in a later phase.",
    )
    comment: str | None = Field(None, max_length=500)


class ShelveRequest(BaseModel):
    """Mute a rule for a fixed duration. The evaluator auto-unshelves
    when `shelved_until` expires; the operator can unshelve early."""
    duration_minutes: int = Field(
        ..., ge=1, le=43_200,
        description="How long to mute the rule, in minutes. Max 30 days.",
    )
    user_id: int | None = None
    comment: str | None = Field(None, max_length=500)


class UnshelveRequest(BaseModel):
    user_id: int | None = None
    comment: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# Rules CRUD
# ---------------------------------------------------------------------------

@router.post("/rules", response_model=AlarmRuleResponse, status_code=201)
def create_rule(
    body: AlarmRuleCreate,
    db: Annotated[Session, Depends(get_session)],
):
    """Create an alarm rule. The DB trigger auto-creates the matching
    alarm_state row in `normal` state; no explicit insert needed here."""
    try:
        row = db.execute(text("""
            INSERT INTO alarm_rules (
                tag_id, rule_type, severity, threshold, deadband,
                on_delay_sec, off_delay_sec, latched, enabled,
                message_template
            )
            VALUES (
                :tag_id, :rule_type, :severity, :threshold, :deadband,
                :on_delay_sec, :off_delay_sec, :latched, :enabled,
                :message_template
            )
            RETURNING id, tag_id, rule_type, severity, threshold, deadband,
                      on_delay_sec, off_delay_sec, latched, enabled,
                      message_template, created_at, updated_at
        """), body.model_dump()).mappings().first()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower()
        if "alarm_rules_unique_basic" in msg:
            raise HTTPException(
                409,
                f"A '{body.rule_type}' rule already exists for tag "
                f"{body.tag_id}. The four level types (hi_hi, hi, lo, lo_lo) "
                f"are mutually exclusive per tag.",
            )
        if "foreign key" in msg or "tags" in msg:
            raise HTTPException(
                422, f"tag_id {body.tag_id} does not exist",
            )
        raise HTTPException(400, f"Constraint violation: {e.orig}")

    return _attach_tag_name(db, dict(row))


@router.get("/rules", response_model=list[AlarmRuleResponse])
def list_rules(
    db: Annotated[Session, Depends(get_session)],
    tag_id:   int | None = Query(None),
    severity: Severity | None = Query(None),
    enabled:  bool | None = Query(None),
):
    """List rules, sorted by tag name then rule type. Filters are
    independent — pass any combination."""
    sql = """
        SELECT r.id, r.tag_id, t.name AS tag_name, r.rule_type, r.severity,
               r.threshold, r.deadband, r.on_delay_sec, r.off_delay_sec,
               r.latched, r.enabled, r.message_template,
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
               r.latched, r.enabled, r.message_template,
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
    db: Annotated[Session, Depends(get_session)],
):
    """Partial update. Anything you don't include stays as it was.
    `updated_at` is bumped by the DB trigger, not by the API."""
    exists = db.execute(
        text("SELECT id FROM alarm_rules WHERE id = :id"),
        {"id": rule_id},
    ).scalar()
    if exists is None:
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided")

    set_clauses = [f"{k} = :{k}" for k in updates]
    sql = (
        f"UPDATE alarm_rules SET {', '.join(set_clauses)} "
        f"WHERE id = :id "
        f"RETURNING id, tag_id, rule_type, severity, threshold, deadband, "
        f"          on_delay_sec, off_delay_sec, latched, enabled, "
        f"          message_template, created_at, updated_at"
    )

    try:
        row = db.execute(text(sql), {**updates, "id": rule_id}).mappings().first()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig).lower()
        if "alarm_rules_unique_basic" in msg:
            raise HTTPException(
                409,
                "Updating rule_type would collide with an existing rule of "
                "the same type on this tag.",
            )
        raise HTTPException(400, f"Constraint violation: {e.orig}")

    return _attach_tag_name(db, dict(row))


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Delete a rule. The matching alarm_state row cascades automatically;
    the event history in alarm_events is preserved (no FK to alarm_rules
    by design)."""
    result = db.execute(
        text("DELETE FROM alarm_rules WHERE id = :id"),
        {"id": rule_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, f"Alarm rule {rule_id} not found")
    db.commit()


# ---------------------------------------------------------------------------
# Active alarms view
# ---------------------------------------------------------------------------

@router.get("/active", response_model=list[AlarmActive])
def list_active(
    db: Annotated[Session, Depends(get_session)],
    severity: Severity | None = Query(None),
):
    """Currently active alarms. State must be one of:
    active_unack, active_ack, inactive_unack.

    Sorted with the loudest first: severity desc, then most-recent change.
    """
    # Severity ordering for the sort key — critical loudest, info quietest.
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

    # Severity sort: map to integer so ORDER BY does the right thing
    # without needing a CASE expression on every row.
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
# Event history
# ---------------------------------------------------------------------------

@router.get("/history", response_model=list[AlarmEventResponse])
def list_history(
    db: Annotated[Session, Depends(get_session)],
    rule_id:    int | None = Query(None),
    tag_id:     int | None = Query(None),
    event_type: EventType | None = Query(None),
    start:      datetime | None = Query(None, description="ISO timestamp (inclusive)"),
    end:        datetime | None = Query(None, description="ISO timestamp (exclusive)"),
    limit:      int = Query(100, ge=1, le=1000),
):
    """Event log, newest first. All filters combine with AND. Returns up
    to `limit` rows (default 100, hard cap 1000).

    Pagination strategy: pass `end` = previous batch's oldest event_time
    to walk backwards. id is unique within a chunk so ties on event_time
    are stable.
    """
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
# Acknowledge action
# ---------------------------------------------------------------------------

@router.post("/rules/{rule_id}/ack", response_model=AlarmEventResponse, status_code=201)
def ack_rule(
    rule_id: int,
    body: AckRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Acknowledge an active alarm.

    State machine transitions:
      active_unack   -> active_ack        (alarm still on, now acked)
      inactive_unack -> normal            (alarm cleared and now acked)
      anything else  -> 409 Conflict      (nothing to ack)

    Writes an `acked` event with the value/quality at ack time so the
    history shows what the operator saw when they pressed the button.
    """
    state_row = db.execute(text("""
        SELECT s.state, s.current_value, s.current_quality, r.tag_id
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        WHERE s.rule_id = :rule_id
    """), {"rule_id": rule_id}).mappings().first()

    if state_row is None:
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    current = state_row["state"]
    if current == "active_unack":
        new_state = "active_ack"
    elif current == "inactive_unack":
        new_state = "normal"
    else:
        raise HTTPException(
            409,
            f"Rule {rule_id} is in state '{current}'; nothing to acknowledge.",
        )

    # Update state + insert event + commit in one transaction. If either
    # fails, neither side persists.
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
    except Exception:
        db.rollback()
        raise

    # Attach tag_name for the response.
    return _attach_tag_name(db, dict(event), tag_id_field="tag_id")


# ---------------------------------------------------------------------------
# Shelve / unshelve actions  (Phase 14.4)
# ---------------------------------------------------------------------------

@router.get("/shelved", response_model=list[AlarmActive])
def list_shelved(db: Annotated[Session, Depends(get_session)]):
    """Currently-shelved rules. Same response shape as /active so the
    frontend can render them with shared code, but filtered to
    state='shelved'. Sorted by earliest shelve expiry (the operator
    cares which mute is about to lift)."""
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
    db: Annotated[Session, Depends(get_session)],
):
    """Mute a rule for `duration_minutes`. Allowed from any state EXCEPT
    `disabled` (re-enable the rule first). Re-shelving an already-shelved
    rule resets the expiry to the new duration (operator extends the
    mute without having to unshelve first)."""
    state_row = db.execute(text("""
        SELECT s.state, s.current_value, s.current_quality, r.tag_id
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        WHERE s.rule_id = :rule_id
    """), {"rule_id": rule_id}).mappings().first()

    if state_row is None:
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    if state_row["state"] == "disabled":
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
    except Exception:
        db.rollback()
        raise

    return _attach_tag_name(db, dict(event), tag_id_field="tag_id")


@router.post("/rules/{rule_id}/unshelve",
             response_model=AlarmEventResponse, status_code=201)
def unshelve_rule(
    rule_id: int,
    body: UnshelveRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """End a shelve early. Transitions state back to `normal`; the
    evaluator's next tick will re-evaluate the current value and
    re-alarm if appropriate (subject to on_delay)."""
    state_row = db.execute(text("""
        SELECT s.state, s.current_value, s.current_quality, r.tag_id
        FROM alarm_state s
        JOIN alarm_rules r ON r.id = s.rule_id
        WHERE s.rule_id = :rule_id
    """), {"rule_id": rule_id}).mappings().first()

    if state_row is None:
        raise HTTPException(404, f"Alarm rule {rule_id} not found")

    if state_row["state"] != "shelved":
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
    except Exception:
        db.rollback()
        raise

    return _attach_tag_name(db, dict(event), tag_id_field="tag_id")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _attach_tag_name(
    db: Session,
    row: dict,
    tag_id_field: str = "tag_id",
) -> dict:
    """Populate `tag_name` on a row dict from the tags table. Cheap
    follow-up query keeps the main INSERT/UPDATE SQL simple."""
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
