"""Phase 14.3 — Alarm evaluator worker.

A long-lived sync loop that:

  1. Loads enabled alarm rules from the DB (refreshed every 30 s, so
     operator edits via the API land within half a minute).
  2. Fetches the most-recent value per tag from tag_values (one batch
     query — no per-rule round trips).
  3. For each rule, runs the evaluation state machine:
       * applies deadband (hysteresis around the threshold so a
         flapping value doesn't generate event spam)
       * applies on_delay_sec / off_delay_sec (the operator's chatter
         filter — a condition must persist for that many seconds
         before the state actually transitions)
       * follows ISA-18.2 transitions for the four states the
         evaluator owns: normal, active_unack, active_ack, shelved
       * leaves ack-driven transitions (inactive_unack -> normal,
         active_unack -> active_ack) to the API layer
  4. Writes back to alarm_state + appends to alarm_events in a single
     transaction per rule, so partial failures never leave the state
     row out of sync with the event log.

What this version does NOT handle (defer):

  * rule_type IN ('deviation', 'rate_of_change') — needs separate
    design for setpoint sourcing and ROC window. Evaluator skips them
    with a debug log; the API still accepts them so the schema is
    forward-compatible.
  * shelved -> normal when shelved_until expires is handled HERE; the
    initial transition to 'shelved' is operator-driven and lives in
    the API (Phase 14.4 along with the shelve endpoint).
  * disabled rules — skipped entirely; the rule's state row is left as
    whatever it was. If you want to "clear on disable" we'll add that
    in 14.4.

Spec mapping:
  - §6.x Alarms state machine (ISA-18.2)
  - §6.y Deadband + delay handling
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How often we tick the evaluator. 1 s gives sub-second responsiveness
# for delays measured in seconds; lower would just burn CPU.
EVAL_TICK_SEC = float(os.getenv("ALARM_EVAL_TICK_SEC", "1.0"))

# How often we reload the rules cache. Trade-off between picking up
# operator edits quickly and avoiding constant DB round-trips.
RULE_RELOAD_SEC = float(os.getenv("ALARM_RULE_RELOAD_SEC", "30.0"))

# Hard upper bound on how old a value can be before we refuse to
# evaluate against it. Per-rule we tighten this further to 3x the
# tag's scan_interval if available. Keeps a long-dead device from
# perma-triggering a `hi` rule on whatever its last value happened
# to be.
MAX_VALUE_AGE_SEC = float(os.getenv("ALARM_MAX_VALUE_AGE_SEC", "300.0"))

# Quality threshold — same as the rest of the codebase. GOOD only.
GOOD_QUALITY = 128

# Rule types the evaluator currently handles. Everything else is
# silently skipped (no events written, state untouched).
# Rule types the evaluator currently handles. Everything else is
# silently skipped (no events written, state untouched).
EVALUABLE_TYPES = {
    "hi_hi", "hi", "lo", "lo_lo",
    # Phase 14.9 — Boolean comparisons. No deadband, no threshold —
    # the rule type itself encodes the comparison (see evaluate_condition).
    "bool_true", "bool_false",
    # Phase 14.7 — Rolling-window numeric analytics.
    #   deviation:      |value - rolling_mean| > threshold
    #   rate_of_change: |least-squares slope| > threshold (units/sec)
    "deviation", "rate_of_change",
}

# Fallback when alarm_rules.window_seconds is NULL. Matches typical
# industrial deviation/RoC alarms (1 minute).
DEFAULT_WINDOW_SECONDS = 60

# Minimum GOOD samples in the window before deviation/rate_of_change
# may produce a transition. Below this, the evaluator returns
# (False, False) and state is unchanged. Prevents flapping on startup
# or after a data gap.
MIN_WINDOW_SAMPLES = 3


log = logging.getLogger("alarm_evaluator")


# ---------------------------------------------------------------------------
# Rule + state dataclasses (sized for in-memory iteration; not SQLA models)
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    id: int
    tag_id: int
    rule_type: str
    severity: str
    threshold: float
    deadband: float
    on_delay_sec: int
    off_delay_sec: int
    latched: bool
    message_template: str | None
    scan_interval_ms: int | None
    # Phase 14.7 - rolling window for deviation and rate_of_change.
    # NULL for hi/lo/bool rules (they don't use it). NULL also
    # acceptable for dev/RoC rules created before this phase landed;
    # the evaluator falls back to DEFAULT_WINDOW_SECONDS.
    window_seconds: int | None


@dataclass
class StateSnapshot:
    state: str
    pending_active_since: datetime | None
    pending_clear_since: datetime | None
    shelved_until: datetime | None
    current_value: float | None
    current_quality: int | None


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

def load_rules(db) -> list[Rule]:
    """Return all enabled rules of evaluable types. Joins the tag's
    register_block to grab scan_interval_ms for the staleness check."""
    rows = db.execute(text("""
        SELECT r.id, r.tag_id, r.rule_type, r.severity, r.threshold,
               r.deadband, r.on_delay_sec, r.off_delay_sec, r.latched,
               r.message_template, r.window_seconds,
               rb.scan_interval_ms
        FROM alarm_rules r
        JOIN tags t              ON t.id = r.tag_id
        LEFT JOIN register_blocks rb ON rb.id = t.register_block_id
        WHERE r.enabled = true
          AND r.rule_type = ANY(:types)
    """), {"types": list(EVALUABLE_TYPES)}).mappings().all()
    return [Rule(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Batch query: latest value per tag, with age + quality
# ---------------------------------------------------------------------------

def latest_values_by_tag(db, tag_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Return {tag_id: {time, value, st}} for the most recent row per tag
    in the last MAX_VALUE_AGE_SEC seconds.

    DISTINCT ON exploits the (tag_id, time DESC) index pattern that the
    tag_values hypertable carries. Single query, all rules covered.
    """
    if not tag_ids:
        return {}
    rows = db.execute(text("""
        SELECT DISTINCT ON (tag_id)
               tag_id, time, value_double, st
        FROM tag_values
        WHERE tag_id = ANY(:ids)
          AND time >= NOW() - make_interval(secs => :max_age)
        ORDER BY tag_id, time DESC
    """), {
        "ids": tag_ids,
        "max_age": MAX_VALUE_AGE_SEC,
    }).mappings().all()
    return {
        r["tag_id"]: {
            "time": r["time"],
            "value": r["value_double"],
            "st": r["st"],
        }
        for r in rows
    }


def current_state(db, rule_id: int) -> StateSnapshot | None:
    row = db.execute(text("""
        SELECT state, pending_active_since, pending_clear_since,
               shelved_until, current_value, current_quality
        FROM alarm_state WHERE rule_id = :id
    """), {"id": rule_id}).mappings().first()
    if row is None:
        return None
    return StateSnapshot(**dict(row))


# ---------------------------------------------------------------------------
# Window analytics — Phase 14.7
# ---------------------------------------------------------------------------

def compute_rolling_mean(db, tag_id: int, window_seconds: int) -> float | None:
    """GOOD-only rolling mean over the last window_seconds for one tag.

    Returns None when fewer than MIN_WINDOW_SAMPLES are available — the
    caller should treat that as "insufficient data, don't transition".
    """
    row = db.execute(text("""
        SELECT COUNT(*)::int AS n, AVG(value_double) AS mean
        FROM tag_values
        WHERE tag_id = :tid
          AND time >= NOW() - make_interval(secs => :window)
          AND st >= 128
    """), {"tid": tag_id, "window": window_seconds}).mappings().first()
    if row is None or (row["n"] or 0) < MIN_WINDOW_SAMPLES:
        return None
    return float(row["mean"]) if row["mean"] is not None else None


def compute_rolling_slope(db, tag_id: int, window_seconds: int) -> float | None:
    """Least-squares slope (units per second) of GOOD samples over the
    last window_seconds for one tag.

    slope = sum((x - x̄)(y - ȳ)) / sum((x - x̄)²)
    where x is epoch-seconds and y is the tag value.

    Returns None when fewer than MIN_WINDOW_SAMPLES are available OR
    when all samples share the same timestamp (degenerate denominator).
    """
    rows = db.execute(text("""
        SELECT EXTRACT(EPOCH FROM time)::double precision AS t,
               value_double AS v
        FROM tag_values
        WHERE tag_id = :tid
          AND time >= NOW() - make_interval(secs => :window)
          AND st >= 128
        ORDER BY time
    """), {"tid": tag_id, "window": window_seconds}).mappings().all()

    if len(rows) < MIN_WINDOW_SAMPLES:
        return None

    xs = [float(r["t"]) for r in rows]
    ys = [float(r["v"]) for r in rows]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


# ---------------------------------------------------------------------------
# Condition test — deadband-aware
# ---------------------------------------------------------------------------

def evaluate_condition(
    rule: Rule,
    value: float,
    *,
    deviation_metric: float | None = None,
    slope_metric: float | None = None,
) -> tuple[bool, bool]:
    """Return (condition_active, condition_clear).

    For a HIGH rule (hi, hi_hi):
        active   when  value > threshold
        clear    when  value < threshold - deadband
        in-band  when  threshold - deadband <= value <= threshold
                       -> neither flag set; current state stays

    For a LOW rule (lo, lo_lo):
        active   when  value < threshold
        clear    when  value > threshold + deadband
        in-band  -> neither flag set

    For a BOOLEAN-TRUE rule (bool_true):
        active   when  value != 0   (the discrete signal is asserted)
        clear    when  value == 0
        No deadband — digital signals don't oscillate around a value;
        debouncing is handled by on_delay_sec / off_delay_sec instead.

    For a BOOLEAN-FALSE rule (bool_false):
        active   when  value == 0   (the discrete signal is absent)
        clear    when  value != 0
        Mirrors bool_true.

    For a DEVIATION rule:
        deviation_metric = |value - rolling_mean(window)|
        active   when  deviation_metric > threshold
        clear    when  deviation_metric < threshold - deadband
        If deviation_metric is None (insufficient window data), no
        transition is signalled.

    For a RATE_OF_CHANGE rule:
        slope_metric = |least-squares slope over window| (units/sec)
        active   when  slope_metric > threshold
        clear    when  slope_metric < threshold - deadband
        If slope_metric is None, no transition is signalled.

    The in-band region (for numeric types) is the deadband. Inside it,
    no transition is considered. Prevents a noisy signal hovering at
    threshold from generating constant activate/clear pairs.
    """
    if rule.rule_type in ("hi_hi", "hi"):
        return (value > rule.threshold,
                value < rule.threshold - rule.deadband)
    if rule.rule_type in ("lo", "lo_lo"):
        return (value < rule.threshold,
                value > rule.threshold + rule.deadband)
    # Phase 14.9 — Boolean comparisons. threshold and deadband are
    # ignored for these rule types; the form layer hides those fields.
    if rule.rule_type == "bool_true":
        return (value != 0, value == 0)
    if rule.rule_type == "bool_false":
        return (value == 0, value != 0)
    # Phase 14.7 — Window analytics. threshold is in deviation units
    # for `deviation`, and in units-per-second for `rate_of_change`.
    # When the rolling metric is None (insufficient samples), neither
    # flag is set — state stays put until enough data accumulates.
    if rule.rule_type == "deviation":
        if deviation_metric is None:
            return (False, False)
        return (deviation_metric > rule.threshold,
                deviation_metric < rule.threshold - rule.deadband)
    if rule.rule_type == "rate_of_change":
        if slope_metric is None:
            return (False, False)
        return (slope_metric > rule.threshold,
                slope_metric < rule.threshold - rule.deadband)
    # Unreachable for EVALUABLE_TYPES, defensive fall-through.
    return (False, False)


# ---------------------------------------------------------------------------
# Per-rule evaluation — the state machine
# ---------------------------------------------------------------------------

def evaluate_rule(db, rule: Rule, snap: StateSnapshot, value_row: dict | None) -> None:
    """One pass for one rule. Writes alarm_state + (optionally) one event
    in a single transaction. Caller owns the outer loop and commit cadence
    decisions; this function commits its own per-rule transaction so a
    panic on rule N doesn't roll back rule N-1's progress.
    """
    now = datetime.now(timezone.utc)

    # ---- shelved: only transition if the shelve expired ----
    if snap.state == "shelved":
        if snap.shelved_until is not None and now >= snap.shelved_until:
            _transition(db, rule, snap, "normal", "unshelved", now,
                        value=None, quality=None,
                        comment="shelve_expired")
        return

    if snap.state == "disabled":
        return  # operator paused this rule; ignore until enabled again

    # ---- need a usable value to evaluate ----
    if value_row is None:
        # No recent value at all — record nothing; state stays where it is.
        return

    # Per-rule recency: tighten MAX_VALUE_AGE_SEC down to 3x scan_interval
    # if known. A 1 s scan should not be evaluated against a 4-minute-old
    # sample; a 60 s scan reasonably can be.
    age_sec = (now - value_row["time"]).total_seconds()
    if rule.scan_interval_ms:
        per_rule_max = max(3.0 * rule.scan_interval_ms / 1000.0, 5.0)
        if age_sec > per_rule_max:
            return

    st = value_row["st"]
    value = value_row["value"]

    # Always refresh current_value / current_quality on the state row so
    # the UI shows recency even when no transition fires.
    _update_current_reading(db, rule.id, value, st)

    # If the latest value isn't GOOD, we don't trigger transitions on it
    # (could be a sensor fault). We still updated the reading above so the
    # operator sees "uncertain" or "bad" in the active list.
    if st is None or st < GOOD_QUALITY or value is None:
        return

    # Phase 14.7 — For window analytics, compute the metric here so
    # evaluate_condition stays pure. For other rule types, leave the
    # kwargs as None and evaluate_condition ignores them.
    deviation_metric: float | None = None
    slope_metric: float | None = None
    if rule.rule_type == "deviation":
        window = rule.window_seconds or DEFAULT_WINDOW_SECONDS
        mean = compute_rolling_mean(db, rule.tag_id, window)
        if mean is not None:
            deviation_metric = abs(value - mean)
    elif rule.rule_type == "rate_of_change":
        window = rule.window_seconds or DEFAULT_WINDOW_SECONDS
        slope = compute_rolling_slope(db, rule.tag_id, window)
        if slope is not None:
            slope_metric = abs(slope)

    condition_active, condition_clear = evaluate_condition(
        rule, value,
        deviation_metric=deviation_metric,
        slope_metric=slope_metric,
    )

    state = snap.state
    pa = snap.pending_active_since
    pc = snap.pending_clear_since

    if state == "normal":
        if condition_active:
            if pa is None:
                pa = now
                _set_pending(db, rule.id, pending_active_since=pa, pending_clear_since=None)
            elif (now - pa).total_seconds() >= rule.on_delay_sec:
                _transition(db, rule, snap, "active_unack", "activated", now,
                            value=value, quality=st,
                            comment=_render_message(rule, value))
        elif pa is not None:
            # condition lost before delay expired — reset
            _set_pending(db, rule.id, pending_active_since=None, pending_clear_since=None)

    elif state in ("active_unack", "active_ack"):
        if condition_clear:
            if pc is None:
                pc = now
                _set_pending(db, rule.id, pending_active_since=None, pending_clear_since=pc)
            elif (now - pc).total_seconds() >= rule.off_delay_sec:
                # latched + currently unacked -> inactive_unack (operator
                # must ack to clear). Otherwise straight to normal.
                if state == "active_unack" and rule.latched:
                    target = "inactive_unack"
                else:
                    target = "normal"
                _transition(db, rule, snap, target, "cleared", now,
                            value=value, quality=st,
                            comment=None)
        elif pc is not None:
            _set_pending(db, rule.id, pending_active_since=None, pending_clear_since=None)

    elif state == "inactive_unack":
        # Latched-and-cleared alarm waiting for operator ack. If the
        # condition becomes active again, jump straight back to
        # active_unack (no on_delay — it's a re-entry).
        if condition_active:
            _transition(db, rule, snap, "active_unack", "activated", now,
                        value=value, quality=st,
                        comment=_render_message(rule, value))


# ---------------------------------------------------------------------------
# DB helpers — each runs its own commit for atomicity per call
# ---------------------------------------------------------------------------

def _update_current_reading(db, rule_id: int, value: float | None, st: int | None) -> None:
    db.execute(text("""
        UPDATE alarm_state
        SET current_value = :v, current_quality = :q
        WHERE rule_id = :id
    """), {"id": rule_id, "v": value, "q": st})
    db.commit()


def _set_pending(db, rule_id: int, *, pending_active_since, pending_clear_since) -> None:
    db.execute(text("""
        UPDATE alarm_state
        SET pending_active_since = :pa,
            pending_clear_since  = :pc
        WHERE rule_id = :id
    """), {"id": rule_id, "pa": pending_active_since, "pc": pending_clear_since})
    db.commit()


def _transition(
    db, rule: Rule, snap: StateSnapshot,
    new_state: str, event_type: str, now: datetime,
    *, value: float | None, quality: int | None, comment: str | None,
) -> None:
    """Atomic: update state row + insert event row + commit."""
    db.execute(text("""
        UPDATE alarm_state
        SET state                = :new_state,
            last_change_time     = :now,
            pending_active_since = NULL,
            pending_clear_since  = NULL,
            current_value        = COALESCE(:v, current_value),
            current_quality      = COALESCE(:q, current_quality)
        WHERE rule_id = :id
    """), {
        "id": rule.id, "new_state": new_state, "now": now,
        "v": value, "q": quality,
    })
    db.execute(text("""
        INSERT INTO alarm_events (
            rule_id, tag_id, event_time, event_type,
            value, quality, comment
        )
        VALUES (
            :rule_id, :tag_id, :event_time, :event_type,
            :value, :quality, :comment
        )
    """), {
        "rule_id":    rule.id,
        "tag_id":     rule.tag_id,
        "event_time": now,
        "event_type": event_type,
        "value":      value,
        "quality":    quality,
        "comment":    comment,
    })
    db.commit()
    log.info(
        "rule=%d tag=%d %s -> %s (%s, value=%s)",
        rule.id, rule.tag_id, snap.state, new_state, event_type, value,
    )


def _render_message(rule: Rule, value: float) -> str:
    """Render the rule's message_template with simple {field} substitutions.
    Free-text by design; the evaluator never trusts the template to be
    well-formed and falls back to a generic line on KeyError or ValueError.
    """
    tmpl = rule.message_template
    if not tmpl:
        return _default_message(rule, value)
    try:
        return tmpl.format(
            value=value,
            threshold=rule.threshold,
            rule_type=rule.rule_type,
            severity=rule.severity,
        )
    except (KeyError, ValueError, IndexError):
        return _default_message(rule, value)


def _default_message(rule: Rule, value: float) -> str:
    """Type-aware default message used when message_template is empty or
    fails to render. Avoids "threshold X crossed" wording for boolean
    types where threshold has no meaning."""
    if rule.rule_type == "bool_true":
        return f"Boolean signal asserted (value={value})"
    if rule.rule_type == "bool_false":
        return f"Boolean signal absent (value={value})"
    if rule.rule_type == "deviation":
        window = rule.window_seconds or DEFAULT_WINDOW_SECONDS
        return (f"Deviation from rolling mean exceeded threshold "
                f"{rule.threshold} over {window}s window (value={value})")
    if rule.rule_type == "rate_of_change":
        window = rule.window_seconds or DEFAULT_WINDOW_SECONDS
        return (f"Rate of change exceeded threshold {rule.threshold} "
                f"units/sec over {window}s window (value={value})")
    return f"{rule.rule_type} threshold {rule.threshold} crossed (value={value})"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_should_stop = False


def _install_sig_handlers() -> None:
    def stop(signum, _frame):
        global _should_stop
        log.info("got signal %d, shutting down after current tick", signum)
        _should_stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_sig_handlers()
    log.info(
        "alarm_evaluator starting (tick=%.2fs, reload=%.2fs, max_age=%.0fs)",
        EVAL_TICK_SEC, RULE_RELOAD_SEC, MAX_VALUE_AGE_SEC,
    )

    rules: list[Rule] = []
    # Initialise far enough in the past that the first iteration always
    # reloads. Without this we'd need a flag, or worse, the previous
    # `or not rules` hack which reloaded every tick whenever 0 rules
    # were enabled (cheap query but log spam).
    last_reload = 0.0

    while not _should_stop:
        cycle_start = time.monotonic()
        try:
            with SessionLocal() as db:
                # Reload rule cache periodically
                if cycle_start - last_reload >= RULE_RELOAD_SEC:
                    rules = load_rules(db)
                    last_reload = cycle_start
                    log.info("loaded %d enabled rule(s)", len(rules))

                if rules:
                    tag_ids = list({r.tag_id for r in rules})
                    values = latest_values_by_tag(db, tag_ids)

                    for rule in rules:
                        try:
                            snap = current_state(db, rule.id)
                            if snap is None:
                                # State row should exist (trigger creates
                                # it on rule insert). If not, the rule is
                                # malformed — skip and log.
                                log.warning("no alarm_state row for rule %d", rule.id)
                                continue
                            evaluate_rule(db, rule, snap, values.get(rule.tag_id))
                        except Exception:
                            log.exception("evaluation failed for rule %d", rule.id)
                            db.rollback()
        except Exception:
            log.exception("evaluator cycle failed")

        # Tick pacing — sleep the remainder of the period
        elapsed = time.monotonic() - cycle_start
        if elapsed < EVAL_TICK_SEC:
            time.sleep(EVAL_TICK_SEC - elapsed)
        elif elapsed > 2 * EVAL_TICK_SEC:
            log.warning("evaluator cycle slow: %.2fs (target %.2fs)",
                        elapsed, EVAL_TICK_SEC)

    log.info("alarm_evaluator stopped")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log.exception("evaluator crashed")
        sys.exit(1)
