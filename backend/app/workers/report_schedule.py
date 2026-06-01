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
    at_time_min = g("at_time_min")
    if at_time_min is None:
        at_time_min = 6 * 60  # sensible default contract hour 06:00

    # 1. interval-based (every N minutes) — highest specificity
    iv = g("interval_minutes")
    if iv:
        if last_fired_at is None:
            # first run: due as of the most recent interval boundary <= now
            return now.replace(second=0, microsecond=0)
        nxt = last_fired_at + timedelta(minutes=int(iv))
        return nxt if nxt <= now else None

    # 2. yearly (month_of_year set)
    if g("month_of_year"):
        return _last_yearly(now, int(g("month_of_year")),
                            int(g("day_of_month") or 1), at_time_min)

    # 3. monthly (day_of_month set, no month)
    if g("day_of_month"):
        return _last_monthly(now, int(g("day_of_month")), at_time_min)

    # 4. weekly (day_of_week set)
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
