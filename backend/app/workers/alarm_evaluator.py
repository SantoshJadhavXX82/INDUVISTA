"""Phase 14.3 / 14.7 / 14.9 / 14.10 - Alarm evaluator worker.

A long-lived sync loop that:

  1. Loads enabled alarm rules from the DB (refreshed every 30 s).
  2. Fetches the most-recent value per tag from tag_values.
  3. For each rule, runs the evaluation state machine with deadband,
     on_delay/off_delay, and ISA-18.2 transitions.
  4. Writes state + events atomically per rule.

Phase 14.10 additions:
  - frozen: max(value) - min(value) <= threshold over window
            (inverted condition direction - small delta means stuck)
  - spike:  |latest - prior| > threshold between consecutive GOOD samples
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

EVAL_TICK_SEC = float(os.getenv("ALARM_EVAL_TICK_SEC", "1.0"))
RULE_RELOAD_SEC = float(os.getenv("ALARM_RULE_RELOAD_SEC", "30.0"))
MAX_VALUE_AGE_SEC = float(os.getenv("ALARM_MAX_VALUE_AGE_SEC", "300.0"))

GOOD_QUALITY = 128

EVALUABLE_TYPES = {
    "hi_hi", "hi", "lo", "lo_lo",
    # Phase 14.9 - Boolean comparisons. No deadband, no threshold;
    # the rule type itself encodes the comparison.
    "bool_true", "bool_false",
    # Phase 14.7 - Rolling-window numeric analytics.
    "deviation", "rate_of_change",
    # Phase 14.10 - frozen detects stuck values over a window;
    # spike detects sample-to-sample jumps. Both ship as system types.
    "frozen", "spike",
}

DEFAULT_WINDOW_SECONDS = 60
MIN_WINDOW_SAMPLES = 3


log = logging.getLogger("alarm_evaluator")


# ---------------------------------------------------------------------------
# Rule + state dataclasses
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


def latest_values_by_tag(db, tag_ids: list[int]) -> dict[int, dict[str, Any]]:
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
# Window analytics - Phase 14.7
# ---------------------------------------------------------------------------

def compute_rolling_mean(db, tag_id: int, window_seconds: int) -> float | None:
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
# Phase 14.10 - Frozen + spike metric helpers
# ---------------------------------------------------------------------------

def compute_window_delta(db, tag_id: int, window_seconds: int) -> float | None:
    """For frozen detection: (max - min) of GOOD values over the window.
    Returns None when fewer than 2 GOOD samples exist - one sample can't
    establish whether the value is moving."""
    row = db.execute(text("""
        SELECT COUNT(*)::int AS n,
               MAX(value_double) - MIN(value_double) AS delta
        FROM tag_values
        WHERE tag_id = :tid
          AND time >= NOW() - make_interval(secs => :window)
          AND value_double IS NOT NULL
          AND st >= 128
    """), {"tid": tag_id, "window": window_seconds}).mappings().first()
    if row is None or (row["n"] or 0) < 2:
        return None
    if row["delta"] is None:
        return None
    return float(row["delta"])


def compute_sample_delta(db, tag_id: int) -> float | None:
    """For spike detection: |latest - prior| absolute delta of the most
    recent two GOOD samples. None when fewer than 2 exist."""
    rows = db.execute(text("""
        SELECT value_double
        FROM tag_values
        WHERE tag_id = :tid
          AND value_double IS NOT NULL
          AND st >= 128
        ORDER BY time DESC
        LIMIT 2
    """), {"tid": tag_id}).fetchall()
    if len(rows) < 2:
        return None
    latest = rows[0][0]
    prior = rows[1][0]
    if latest is None or prior is None:
        return None
    return abs(float(latest) - float(prior))


# ---------------------------------------------------------------------------
# Condition test - deadband-aware
# ---------------------------------------------------------------------------

def evaluate_condition(
    rule: Rule,
    value: float,
    *,
    deviation_metric: float | None = None,
    slope_metric: float | None = None,
    frozen_metric: float | None = None,
    spike_metric: float | None = None,
) -> tuple[bool, bool]:
    """Return (condition_active, condition_clear).

    For HIGH (hi, hi_hi):
        active when value > threshold
        clear  when value < threshold - deadband
    For LOW (lo, lo_lo):
        active when value < threshold
        clear  when value > threshold + deadband
    For BOOLEAN-TRUE / BOOLEAN-FALSE: rule_type encodes the comparison.
    For DEVIATION / RATE_OF_CHANGE: windowed metric vs threshold.

    For FROZEN (Phase 14.10):
        active when delta <= threshold        (small delta = stuck)
        clear  when delta >  threshold + deadband
                    (deadband is ADDED for clear because the comparison
                     direction is inverted - more change means less frozen)
    For SPIKE (Phase 14.10):
        active when |latest - prior| >  threshold
        clear  when |latest - prior| <= threshold - deadband
    """
    if rule.rule_type in ("hi_hi", "hi"):
        return (value > rule.threshold,
                value < rule.threshold - rule.deadband)
    if rule.rule_type in ("lo_lo", "lo"):
        return (value < rule.threshold,
                value > rule.threshold + rule.deadband)
    if rule.rule_type == "bool_true":
        return (value != 0, value == 0)
    if rule.rule_type == "bool_false":
        return (value == 0, value != 0)
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
    # Phase 14.10 - frozen. Inverted: stuck = LOW delta.
    if rule.rule_type == "frozen":
        if frozen_metric is None:
            return (False, False)
        return (frozen_metric <= rule.threshold,
                frozen_metric > rule.threshold + rule.deadband)
    # Phase 14.10 - spike. Standard "above threshold" on sample-to-sample delta.
    if rule.rule_type == "spike":
        if spike_metric is None:
            return (False, False)
        return (spike_metric > rule.threshold,
                spike_metric <= rule.threshold - rule.deadband)
    # Unreachable for EVALUABLE_TYPES, defensive fall-through.
    return (False, False)


# ---------------------------------------------------------------------------
# Per-rule evaluation - the state machine
# ---------------------------------------------------------------------------

def evaluate_rule(db, rule: Rule, snap: StateSnapshot, value_row: dict | None) -> None:
    now = datetime.now(timezone.utc)

    if snap.state == "shelved":
        if snap.shelved_until is not None and now >= snap.shelved_until:
            _transition(db, rule, snap, "normal", "unshelved", now,
                        value=None, quality=None,
                        comment="shelve_expired")
        return

    if snap.state == "disabled":
        return

    if value_row is None:
        return

    age_sec = (now - value_row["time"]).total_seconds()
    if rule.scan_interval_ms:
        per_rule_max = max(3.0 * rule.scan_interval_ms / 1000.0, 5.0)
        if age_sec > per_rule_max:
            return

    st = value_row["st"]
    value = value_row["value"]

    _update_current_reading(db, rule.id, value, st)

    if st is None or st < GOOD_QUALITY or value is None:
        return

    # Phase 14.7 / 14.10 - compute windowed/sample metrics here so
    # evaluate_condition stays pure.
    deviation_metric: float | None = None
    slope_metric: float | None = None
    frozen_metric: float | None = None
    spike_metric: float | None = None
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
    elif rule.rule_type == "frozen":
        window = rule.window_seconds or DEFAULT_WINDOW_SECONDS
        frozen_metric = compute_window_delta(db, rule.tag_id, window)
    elif rule.rule_type == "spike":
        spike_metric = compute_sample_delta(db, rule.tag_id)

    condition_active, condition_clear = evaluate_condition(
        rule, value,
        deviation_metric=deviation_metric,
        slope_metric=slope_metric,
        frozen_metric=frozen_metric,
        spike_metric=spike_metric,
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
            _set_pending(db, rule.id, pending_active_since=None, pending_clear_since=None)

    elif state in ("active_unack", "active_ack"):
        if condition_clear:
            if pc is None:
                pc = now
                _set_pending(db, rule.id, pending_active_since=None, pending_clear_since=pc)
            elif (now - pc).total_seconds() >= rule.off_delay_sec:
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
        if condition_active:
            _transition(db, rule, snap, "active_unack", "activated", now,
                        value=value, quality=st,
                        comment=_render_message(rule, value))


# ---------------------------------------------------------------------------
# DB helpers
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
    # Phase 14.10
    if rule.rule_type == "frozen":
        window = rule.window_seconds or DEFAULT_WINDOW_SECONDS
        return (f"Value frozen: range <= {rule.threshold} "
                f"over {window}s window (current value={value})")
    if rule.rule_type == "spike":
        return (f"Value spike: sample-to-sample jump exceeded "
                f"{rule.threshold} (value={value})")
    return f"{rule.rule_type} threshold {rule.threshold} crossed (value={value})"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Lifecycle — startup/shutdown predictability (Phase 17 hardening).
#
# Pattern matches calc_evaluator.py — same guarantees:
#   • SIGTERM/SIGINT sets a flag, never raises from the handler
#   • main loop is `while not _should_stop` with sub-second poll
#   • optional warm-up delay before the first cycle
#   • clear log markers at start, warm-up complete, stop
#   • cycle/rule counters in the exit log for forensic clarity
# ---------------------------------------------------------------------------

_should_stop = False

# Warm-up grace before first cycle. Lets the Modbus side populate
# tag_values so alarm rules don't all fire MISSING_INPUT on first tick.
# Set CALC_WARMUP_SEC=0 to disable.
WARMUP_SEC = float(os.getenv("ALARM_WARMUP_SEC", "3.0"))

# Sleep tick — even when waiting between cycles, we never block longer
# than this so SIGTERM is detected promptly (default 100ms).
_SLEEP_CHUNK_SEC = 0.1


def _install_sig_handlers() -> None:
    def stop(signum, _frame):
        global _should_stop
        if not _should_stop:
            name = signal.Signals(signum).name
            log.info(
                "alarm_evaluator: %s received; finishing current tick and exiting cleanly",
                name,
            )
            _should_stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def _responsive_sleep(seconds: float) -> None:
    """Sleep up to `seconds`, but wake every _SLEEP_CHUNK_SEC to check
    the shutdown flag. Returns early if _should_stop is set."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not _should_stop:
        remaining = deadline - time.monotonic()
        time.sleep(min(_SLEEP_CHUNK_SEC, remaining))


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_sig_handlers()
    log.info(
        "alarm_evaluator starting (tick=%.2fs, reload=%.2fs, max_age=%.0fs, warmup=%.1fs)",
        EVAL_TICK_SEC, RULE_RELOAD_SEC, MAX_VALUE_AGE_SEC, WARMUP_SEC,
    )

    # Warm-up — same logic as calc_evaluator. Respects shutdown so a
    # docker stop right after start exits in <100ms rather than the
    # full warm-up window.
    if WARMUP_SEC > 0:
        _responsive_sleep(WARMUP_SEC)
        if _should_stop:
            log.info("alarm_evaluator: shutdown during warm-up; never ran a tick")
            return
        log.info("alarm_evaluator: warm-up complete, beginning evaluation")

    rules: list[Rule] = []
    last_reload = 0.0
    cycles_run = 0
    rules_evaluated = 0

    while not _should_stop:
        cycle_start = time.monotonic()
        try:
            with SessionLocal() as db:
                if cycle_start - last_reload >= RULE_RELOAD_SEC:
                    rules = load_rules(db)
                    last_reload = cycle_start

                if rules:
                    tag_ids = list({r.tag_id for r in rules})
                    values = latest_values_by_tag(db, tag_ids)

                    for rule in rules:
                        # Honor shutdown mid-cycle so a slow cycle doesn't
                        # delay exit. Current rule's transaction state is
                        # bounded by its own try/except.
                        if _should_stop:
                            log.info(
                                "alarm_evaluator: shutdown requested mid-cycle; "
                                "stopping after %d of %d rules", rules_evaluated, len(rules),
                            )
                            break
                        try:
                            snap = current_state(db, rule.id)
                            if snap is None:
                                log.warning("no alarm_state row for rule %d", rule.id)
                                continue
                            evaluate_rule(db, rule, snap, values.get(rule.tag_id))
                            rules_evaluated += 1
                        except Exception:
                            log.exception("evaluation failed for rule %d", rule.id)
                            db.rollback()
            cycles_run += 1
        except Exception:
            log.exception("evaluator cycle failed")

        elapsed = time.monotonic() - cycle_start
        if elapsed < EVAL_TICK_SEC:
            _responsive_sleep(EVAL_TICK_SEC - elapsed)
        elif elapsed > 2 * EVAL_TICK_SEC:
            log.warning("evaluator cycle slow: %.2fs (target %.2fs)",
                        elapsed, EVAL_TICK_SEC)

    log.info(
        "alarm_evaluator: stopped cleanly after %d cycles, %d rule evaluations",
        cycles_run, rules_evaluated,
    )


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log.exception("evaluator crashed")
        sys.exit(1)
