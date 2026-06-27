"""Pure schedule logic for the report trigger engine.

Decides, for a timed trigger, the most recent scheduled instant at or before
'now' (the "due instant"). The worker fires a report when the due instant is
newer than what it last fired (last_fired_at). This is driven entirely by the
concrete schedule fields — never a hardcoded period-name ladder — so new period
names work without code changes:

    interval_minutes  -> every N minutes
    day_of_week       -> weekly (0=Mon..6=Sun) at at_time_min
    day_of_month      -> monthly on that day at at_time_min
    month_of_year     -> yearly in that month (day_of_month) at at_time_min
    at_time_min       -> daily at that minute-of-day
    at_minute         -> hourly at that minute
    (cron_expr)       -> delegated to a cron lib if present; else skipped safely

All times are timezone-aware. The worker passes a tz-aware 'now' in the report
timezone; due instants are returned tz-aware in the same tz.

This module has NO side effects and NO DB access, so it is unit-testable.
"""
from __future__ import annotations
from datetime import datetime, timedelta, time as dtime
from typing import Any, Optional
import calendar


def _last_daily(now: datetime, minute_of_day: int) -> datetime:
    """Most recent occurrence of minute-of-day at-or-before now."""
    today = now.replace(hour=minute_of_day // 60, minute=minute_of_day % 60,
                        second=0, microsecond=0)
    return today if today <= now else today - timedelta(days=1)


def _last_hourly(now: datetime, at_minute: int) -> datetime:
    this_hour = now.replace(minute=at_minute, second=0, microsecond=0)
    return this_hour if this_hour <= now else this_hour - timedelta(hours=1)


def _last_weekly(now: datetime, day_of_week: int, minute_of_day: int) -> datetime:
    """day_of_week: 0=Mon..6=Sun. Most recent that weekday at minute-of-day."""
    target = now.replace(hour=minute_of_day // 60, minute=minute_of_day % 60,
                         second=0, microsecond=0)
    delta_days = (now.weekday() - day_of_week) % 7
    candidate = target - timedelta(days=delta_days)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


def _last_weekly_multi(now: datetime, days: list[int], minute_of_day: int) -> datetime:
    """Most recent occurrence among several weekdays at minute-of-day.
    days: list of 0=Mon..6=Sun. Returns the latest matching instant <= now."""
    best = None
    for d in days:
        c = _last_weekly(now, d, minute_of_day)
        if best is None or c > best:
            best = c
    return best


def _parse_days(raw) -> list[int]:
    """Parse a days_of_week value: comma-separated string, list, or None."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        vals = raw
    else:
        vals = str(raw).split(",")
    out = []
    for v in vals:
        try:
            i = int(str(v).strip())
            if 0 <= i <= 6:
                out.append(i)
        except (ValueError, TypeError):
            continue
    return sorted(set(out))


def _last_monthly(now: datetime, day_of_month: int, minute_of_day: int) -> datetime:
    def at(year: int, month: int) -> datetime:
        dom = min(day_of_month, calendar.monthrange(year, month)[1])
        return now.replace(year=year, month=month, day=dom,
                           hour=minute_of_day // 60, minute=minute_of_day % 60,
                           second=0, microsecond=0)
    candidate = at(now.year, now.month)
    if candidate > now:
        y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        candidate = at(y, m)
    return candidate


def _last_yearly(now: datetime, month_of_year: int, day_of_month: int,
                 minute_of_day: int) -> datetime:
    def at(year: int) -> datetime:
        dom = min(day_of_month, calendar.monthrange(year, month_of_year)[1])
        return now.replace(year=year, month=month_of_year, day=dom,
                           hour=minute_of_day // 60, minute=minute_of_day % 60,
                           second=0, microsecond=0)
    candidate = at(now.year)
    if candidate > now:
        candidate = at(now.year - 1)
    return candidate


def due_instant(trigger: dict[str, Any], now: datetime,
                last_fired_at: Optional[datetime]) -> Optional[datetime]:
    """Return the most recent scheduled instant at-or-before `now` for a timed
    trigger, or None if it cannot be determined. The worker fires when this is
    strictly newer than last_fired_at.

    Resolution order is by the most specific concrete field present, so the
    `period` name is only a hint and never required.
    """
    g = trigger.get
    # 0. one-shot (run_at set): fire exactly once at run_at, then never
    #    again. Returns run_at once it is at-or-before now; the worker
    #    records last_fired_at = run_at after firing, so due (== run_at)
    #    is no longer strictly newer than last_fired_at next tick.
    run_at = g("run_at")
    if run_at is not None:
        return run_at if run_at <= now else None
    at_time_min = g("at_time_min")
    if at_time_min is None:
        at_time_min = 6 * 60  # sensible default contract hour 06:00

    # 1. interval-based (every N minutes) — highest specificity
    iv = g("interval_minutes")
    if iv:
        step_min = max(1, int(iv))
        if last_fired_at is None:
            # first run: due as of the current minute boundary
            return now.replace(second=0, microsecond=0)
        # Skip straight to the LATEST due boundary instead of replaying every
        # missed step one-per-tick. Without this, a stale last_fired_at (e.g.
        # after the scheduler was down/rebuilt) caused a catch-up storm that
        # back-filled ~one report per tick. Stale intermediate boundaries are
        # intentionally NOT back-filled — same philosophy as MAX_CATCHUP_MIN.
        step = timedelta(minutes=step_min)
        n = int((now - last_fired_at) // step)
        if n < 1:
            return None  # not due yet
        return last_fired_at + n * step

    # 2. yearly (month_of_year set)
    if g("month_of_year"):
        return _last_yearly(now, int(g("month_of_year")),
                            int(g("day_of_month") or 1), at_time_min)

    # 3. monthly (day_of_month set, no month)
    if g("day_of_month"):
        return _last_monthly(now, int(g("day_of_month")), at_time_min)

    # 4. weekly — prefer multi-day (days_of_week), fall back to single day_of_week
    days = _parse_days(g("days_of_week"))
    if days:
        return _last_weekly_multi(now, days, at_time_min)
    dow = g("day_of_week")
    if dow is not None:
        return _last_weekly(now, int(dow), at_time_min)

    # 5. daily (at_time_min present and period not hourly)
    period = (g("period") or "").lower()
    if period == "hourly" or (g("at_minute") is not None and period != "daily"):
        return _last_hourly(now, int(g("at_minute") or 0))

    if g("at_time_min") is not None or period == "daily":
        return _last_daily(now, at_time_min)

    # 6. cron — only if a cron field is set; delegated to croniter if available
    cron = g("cron_expr")
    if cron:
        try:
            from croniter import croniter  # optional dependency
            base = last_fired_at or (now - timedelta(minutes=1))
            it = croniter(cron, base)
            nxt = it.get_next(datetime)
            return nxt if nxt <= now else None
        except Exception:
            return None  # no croniter / bad expr -> skip safely

    return None


def tag_edge_fires(edge: str, last_value: Optional[float],
                   current_value: Optional[float]) -> bool:
    """Decide if a tag trigger should fire given its edge semantics.
    Mirrors DanPac 'to_nonzero' plus 'rising' and 'any_change'.
    """
    if current_value is None:
        return False
    e = (edge or "to_nonzero").lower()
    if e == "to_nonzero":
        return (last_value is None or last_value == 0) and current_value != 0
    if e == "rising":
        return last_value is not None and current_value > last_value
    if e == "any_change":
        return last_value is None or current_value != last_value
    return False


# --------------------------------------------------------------------------- #
# Tag condition evaluation (edge | comparison | formula), becomes-true firing. #
# --------------------------------------------------------------------------- #
def _cond_true(trigger: dict, value: Optional[float]) -> Optional[bool]:
    """Is the trigger's tag condition TRUE for `value`? None if undeterminable.
    Mode precedence: formula (tag_expr) > comparison (tag_op+tag_value) > edge.
    Edge conditions are transition-based and handled by tag_condition_fires, so
    here edge returns None (defer to the edge path)."""
    if value is None:
        return None
    g = trigger.get
    expr = g("tag_expr")
    if expr:
        try:
            from simpleeval import simple_eval
            return bool(simple_eval(expr, names={"x": value, "value": value, "v": value}))
        except Exception:
            return None  # bad expression -> never fires (safe)
    op = g("tag_op")
    tv = g("tag_value")
    if op and tv is not None:
        try:
            tv = float(tv)
        except (TypeError, ValueError):
            return None
        if op == "=" or op == "==":
            return value == tv
        if op == "!=":
            return value != tv
        if op == ">":
            return value > tv
        if op == ">=":
            return value >= tv
        if op == "<":
            return value < tv
        if op == "<=":
            return value <= tv
        return None
    return None  # no comparison/formula -> caller uses edge logic


def _has_condition(trigger: dict) -> bool:
    """True if this trigger uses comparison/formula mode (vs edge mode)."""
    g = trigger.get
    if g("tag_expr"):
        return True
    if g("tag_op") and g("tag_value") is not None:
        return True
    return False


def tag_condition_fires(trigger: dict, last_value: Optional[float],
                        current_value: Optional[float]) -> bool:
    """Unified tag-trigger decision.

    - If a comparison (tag_op+tag_value) or formula (tag_expr) is configured,
      fire on the FALSE->TRUE transition of that condition (one fire per crossing).
      A condition that cannot be evaluated (bad expression, missing value) fails
      CLOSED -> never fires (it does NOT fall back to edge mode).
    - Otherwise (no comparison/formula configured) use edge semantics
      (tag_edge: to_nonzero | rising | any_change).
    """
    if _has_condition(trigger):
        cur_cond = _cond_true(trigger, current_value)
        if cur_cond is not True:
            return False  # current not true (or unevaluable) -> no fire
        prev_cond = _cond_true(trigger, last_value)
        return prev_cond is not True  # becomes-true crossing only
    # edge mode
    return tag_edge_fires(trigger.get("tag_edge") or "to_nonzero",
                          last_value, current_value)


# --------------------------------------------------------------------------- #
# Forward schedule — next due instant strictly AFTER now. Mirror of the        #
# _last_* helpers; used by the /next-due endpoint to drive the countdown.      #
# --------------------------------------------------------------------------- #
def _next_daily(now: datetime, minute_of_day: int) -> datetime:
    today = now.replace(hour=minute_of_day // 60, minute=minute_of_day % 60,
                        second=0, microsecond=0)
    return today if today > now else today + timedelta(days=1)


def _next_hourly(now: datetime, at_minute: int) -> datetime:
    this_hour = now.replace(minute=at_minute, second=0, microsecond=0)
    return this_hour if this_hour > now else this_hour + timedelta(hours=1)


def _next_weekly(now: datetime, day_of_week: int, minute_of_day: int) -> datetime:
    target = now.replace(hour=minute_of_day // 60, minute=minute_of_day % 60,
                         second=0, microsecond=0)
    delta_days = (day_of_week - now.weekday()) % 7
    candidate = target + timedelta(days=delta_days)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _next_weekly_multi(now: datetime, days: list, minute_of_day: int) -> datetime:
    best = None
    for d in days:
        c = _next_weekly(now, d, minute_of_day)
        if best is None or c < best:
            best = c
    return best


def _next_monthly(now: datetime, day_of_month: int, minute_of_day: int) -> datetime:
    def at(year: int, month: int) -> datetime:
        dom = min(day_of_month, calendar.monthrange(year, month)[1])
        return now.replace(year=year, month=month, day=dom,
                           hour=minute_of_day // 60, minute=minute_of_day % 60,
                           second=0, microsecond=0)
    candidate = at(now.year, now.month)
    if candidate > now:
        return candidate
    ny, nm = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return at(ny, nm)


def _next_yearly(now: datetime, month_of_year: int, day_of_month: int,
                 minute_of_day: int) -> datetime:
    def at(year: int) -> datetime:
        dom = min(day_of_month, calendar.monthrange(year, month_of_year)[1])
        return now.replace(year=year, month=month_of_year, day=dom,
                           hour=minute_of_day // 60, minute=minute_of_day % 60,
                           second=0, microsecond=0)
    candidate = at(now.year)
    return candidate if candidate > now else at(now.year + 1)


def next_instant(trigger: dict, now: datetime,
                 last_fired_at: "Optional[datetime]" = None) -> "Optional[datetime]":
    """Next due instant strictly AFTER `now`. Forward mirror of due_instant();
    same field precedence. Returns a tz-aware datetime, or None (one-shot in the
    past, or cron without croniter)."""
    g = trigger.get
    run_at = g("run_at")
    if run_at is not None:
        return run_at if run_at > now else None
    at_time_min = g("at_time_min")
    if at_time_min is None:
        at_time_min = 6 * 60
    iv = g("interval_minutes")
    if iv:
        iv = int(iv)
        base = last_fired_at or now
        nxt = base + timedelta(minutes=iv)
        if nxt <= now:
            steps = int((now - nxt).total_seconds() // (iv * 60)) + 1
            nxt = nxt + timedelta(minutes=iv * steps)
        return nxt
    if g("month_of_year"):
        return _next_yearly(now, int(g("month_of_year")),
                            int(g("day_of_month") or 1), at_time_min)
    if g("day_of_month"):
        return _next_monthly(now, int(g("day_of_month")), at_time_min)
    days = _parse_days(g("days_of_week"))
    if days:
        return _next_weekly_multi(now, days, at_time_min)
    dow = g("day_of_week")
    if dow is not None:
        return _next_weekly(now, int(dow), at_time_min)
    period = (g("period") or "").lower()
    if period == "hourly" or (g("at_minute") is not None and period != "daily"):
        return _next_hourly(now, int(g("at_minute") or 0))
    if g("at_time_min") is not None or period == "daily":
        return _next_daily(now, at_time_min)
    cron = g("cron_expr")
    if cron:
        try:
            from croniter import croniter  # optional dependency
            it = croniter(cron, now)
            return it.get_next(datetime)
        except Exception:
            return None
    return None
