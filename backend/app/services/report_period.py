"""Report period — compute a report's DATA WINDOW, decoupled from its trigger.

The spec's core idea (Period Tab, §7): the *period* is the time window the report
covers, independent of *when* the job runs. A report generated at 10:05 for the
09:00–10:00 hour must compute that 09:00–10:00 window regardless of the slightly-
late fire time.

This module is intentionally pure: it imports only the stdlib (datetime,
zoneinfo) so it is unit-testable in isolation and has no DB or app coupling.

  compute_window(rule, ref, tz_name) -> (start_utc, end_utc)

`rule` is a mapping mirroring the report_period_rule row:
  period_type            hourly | daily | weekly | monthly | shift | batch | custom
  period_rule            previous_completed | current | custom
  boundary_offset_min    minutes after midnight the day/week/month boundary starts
                         (e.g. 360 => 06:00 day boundary). default 0.
  custom_start_offset_min / custom_end_offset_min   minutes from the hour anchor,
                         used ONLY when period_type == 'custom'.

All math is done in the PLANT timezone (so day/month boundaries and DST follow
plant rules) and the result is returned as timezone-aware UTC, matching the
project rule that timestamps are stored in UTC and periods use plant time.

shift/batch are not implemented here — they need shift schedules / batch records
and will arrive in a later phase; compute_window raises a clear ValueError.

NOTE: the value sets below MUST stay in sync with the CHECK constraints in
migration 0068_report_period_rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Mapping

PERIOD_TYPES = ("hourly", "daily", "weekly", "monthly", "shift", "batch", "custom")
PERIOD_RULES = ("previous_completed", "current", "custom")
MISSING_PERIOD_HANDLING = ("warn", "hold", "fail")

# Implemented in this phase; shift/batch are deferred.
_IMPLEMENTED = {"hourly", "daily", "weekly", "monthly", "custom"}

_UTC = ZoneInfo("UTC")


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _floor_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _floor_week(dt: datetime) -> datetime:
    # ISO week: Monday is day 0.
    d = _floor_day(dt)
    return d - timedelta(days=d.weekday())


def _floor_month(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_floor(mfloor: datetime) -> datetime:
    # mfloor is day-1 00:00 of some month; jump safely into the next month.
    return _floor_month(mfloor + timedelta(days=32))


def compute_window(rule: Mapping, ref: datetime, tz_name: str) -> tuple[datetime, datetime]:
    """Return (start_utc, end_utc) for the report's data window.

    `ref` is the reference instant (typically the trigger fire time). If it is
    naive it is assumed to be UTC. The half-open window [start, end) is returned
    as timezone-aware UTC datetimes.
    """
    period_type = (rule.get("period_type") or "").lower()
    period_rule = (rule.get("period_rule") or "previous_completed").lower()

    if period_type not in PERIOD_TYPES:
        raise ValueError(f"unknown period_type '{period_type}'")
    if period_rule not in PERIOD_RULES:
        raise ValueError(f"unknown period_rule '{period_rule}'")
    if period_type not in _IMPLEMENTED:
        raise ValueError(
            f"period_type '{period_type}' is not implemented yet "
            f"(needs shift schedule / batch records)"
        )

    boundary_off = int(rule.get("boundary_offset_min") or 0)
    tz = ZoneInfo(tz_name)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_UTC)
    local = ref.astimezone(tz)

    # ---- custom: explicit offsets from the current hour anchor, no prev/cur.
    if period_type == "custom":
        cs = rule.get("custom_start_offset_min")
        ce = rule.get("custom_end_offset_min")
        if cs is None or ce is None:
            raise ValueError(
                "custom period requires custom_start_offset_min and custom_end_offset_min"
            )
        anchor = _floor_hour(local)
        start = anchor + timedelta(minutes=int(cs))
        end = anchor + timedelta(minutes=int(ce))
        if end <= start:
            raise ValueError("custom period end offset must be after start offset")
        return start.astimezone(_UTC), end.astimezone(_UTC)

    # ---- current period [cur_start, cur_end) — the in-progress window.
    if period_type == "hourly":
        cur_start = _floor_hour(local)
        cur_end = cur_start + timedelta(hours=1)
    elif period_type == "daily":
        base = _floor_day(local) + timedelta(minutes=boundary_off)
        if local < base:
            base -= timedelta(days=1)
        cur_start, cur_end = base, base + timedelta(days=1)
    elif period_type == "weekly":
        base = _floor_week(local) + timedelta(minutes=boundary_off)
        if local < base:
            base -= timedelta(days=7)
        cur_start, cur_end = base, base + timedelta(days=7)
    else:  # monthly
        mfloor = _floor_month(local)
        base = mfloor + timedelta(minutes=boundary_off)
        if local < base:
            mfloor = _floor_month(mfloor - timedelta(days=1))
            base = mfloor + timedelta(minutes=boundary_off)
        cur_start = base
        cur_end = _next_month_floor(mfloor) + timedelta(minutes=boundary_off)

    # ---- apply the rule.
    if period_rule == "current":
        start, end = cur_start, cur_end
    else:  # previous_completed (and 'custom' already returned above)
        end = cur_start
        if period_type == "monthly":
            prev_floor = _floor_month(_floor_month(cur_start) - timedelta(days=1))
            start = prev_floor + timedelta(minutes=boundary_off)
        else:
            start = cur_start - (cur_end - cur_start)

    return start.astimezone(_UTC), end.astimezone(_UTC)
