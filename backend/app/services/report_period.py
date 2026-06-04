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

`shift` is implemented: callers pass the plant shift start times (sorted "HH:MM"
strings) via the `shifts` argument and the window snaps to shift boundaries (the
last shift wraps past midnight). `batch` is implemented too: resolve_window reads
the report_batches table and passes the selected run's (start, end) via `batch`.
compute_window itself stays pure — the DB reads live in load_shift_starts /
load_batch_window / resolve_window below.

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

# Implemented here. batch resolves from batch-run records via resolve_window
# (compute_window itself receives the resolved batch window as a parameter).
_IMPLEMENTED = {"hourly", "daily", "weekly", "monthly", "shift", "batch", "custom"}

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


def _shift_boundaries(shifts: list[str], local: datetime, tz: ZoneInfo) -> list[datetime]:
    """Sorted tz-aware shift-start datetimes spanning the day before/of/after `local`.

    `shifts` are plant-local "HH:MM" start times. Three days of boundaries guarantee
    that any `local` in the middle day has a defined current shift, a next boundary
    (shift end) and a previous boundary (previous shift), including the night shift
    that wraps past midnight.
    """
    starts = sorted({s for s in shifts if s})
    bounds: list[datetime] = []
    for off in (-1, 0, 1):
        d = (local + timedelta(days=off)).date()
        for hhmm in starts:
            h, m = (int(x) for x in hhmm.split(":"))
            bounds.append(datetime(d.year, d.month, d.day, h, m, tzinfo=tz))
    bounds.sort()
    return bounds


def compute_window(
    rule: Mapping, ref: datetime, tz_name: str,
    shifts: list[str] | None = None,
    batch: tuple[datetime, datetime] | None = None,
) -> tuple[datetime, datetime]:
    """Return (start_utc, end_utc) for the report's data window.

    `ref` is the reference instant (typically the trigger fire time). If it is
    naive it is assumed to be UTC. The half-open window [start, end) is returned
    as timezone-aware UTC datetimes.

    `shifts` (for period_type 'shift') and `batch` (for 'batch') are resolved by
    the DB-aware resolve_window below and passed in here, keeping this function
    pure. `batch` is the (started_at, ended_at) of the selected batch run.
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

    # ---- batch: window comes straight from the selected batch run.
    if period_type == "batch":
        if not batch:
            if period_rule == "current":
                raise ValueError("no batch is currently open")
            raise ValueError("no completed batch run found")
        bs, be = batch
        if bs.tzinfo is None:
            bs = bs.replace(tzinfo=_UTC)
        if be.tzinfo is None:
            be = be.replace(tzinfo=_UTC)
        if be <= bs:
            raise ValueError("batch end must be after batch start")
        return bs.astimezone(_UTC), be.astimezone(_UTC)

    # ---- shift: snap to configured shift boundaries (last shift wraps midnight).
    if period_type == "shift":
        if not shifts:
            raise ValueError("shift period requires a configured shift schedule")
        bounds = _shift_boundaries(list(shifts), local, tz)
        cur_i = None
        for i, b in enumerate(bounds):
            if b <= local:
                cur_i = i
            else:
                break
        if cur_i is None or cur_i + 1 >= len(bounds):
            raise ValueError("could not resolve current shift window around ref")
        cur_start, cur_end = bounds[cur_i], bounds[cur_i + 1]
        if period_rule == "current":
            start, end = cur_start, cur_end
        else:  # previous_completed
            if cur_i - 1 < 0:
                raise ValueError("could not resolve previous shift window around ref")
            start, end = bounds[cur_i - 1], cur_start
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


# ---------------------------------------------------------------------------
# DB-aware helpers. compute_window stays pure; these read the shift schedule
# from system_settings (mirrors api/settings.py 'shifts.config') so callers
# can resolve a 'shift' period without knowing the storage details. Imports
# are lazy so the pure window math above stays stdlib-only / offline-testable.
# ---------------------------------------------------------------------------
_DEFAULT_SHIFT_STARTS = ["06:00", "14:00", "22:00"]


def load_shift_starts(db) -> list[str] | None:
    """Return sorted plant shift start times ("HH:MM"), or None if shifts are
    disabled / unconfigured. Falls back to the 3-shift default when the setting
    is absent, matching api/settings.py's GET /settings/shifts behaviour."""
    import json
    from sqlalchemy import text

    row = db.execute(
        text("SELECT value FROM system_settings WHERE key = 'shifts.config'")
    ).first()
    if not row or not row[0]:
        cfg = {"enabled": True, "shifts": [{"start": s} for s in _DEFAULT_SHIFT_STARTS]}
    else:
        try:
            cfg = json.loads(row[0])
        except Exception:
            cfg = {"enabled": True, "shifts": [{"start": s} for s in _DEFAULT_SHIFT_STARTS]}
    if not cfg.get("enabled"):
        return None
    starts = sorted(s["start"] for s in cfg.get("shifts", []) if s.get("start"))
    return starts or None


def load_batch_window(db, period_rule: str, ref: datetime):
    """Resolve the (started_at, ended_at) of the batch run a 'batch' period
    should cover, or None if there is no matching run.

      previous_completed (default) -> the most recently ENDED batch run
      current                      -> the currently OPEN batch run, ending at `ref`

    Batch runs come from the report_batches table (manual override today; a
    tag-threshold worker can insert 'tag'-sourced runs later)."""
    from sqlalchemy import text

    pr = (period_rule or "previous_completed").lower()
    if pr == "current":
        row = db.execute(text(
            "SELECT started_at, ended_at FROM report_batches "
            "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        )).first()
        if not row:
            return None
        return (row[0], row[1] or ref)
    row = db.execute(text(
        "SELECT started_at, ended_at FROM report_batches "
        "WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1"
    )).first()
    if not row:
        return None
    return (row[0], row[1])


def resolve_window(db, rule: Mapping, ref: datetime, tz_name: str) -> tuple[datetime, datetime]:
    """compute_window, but loads DB-backed inputs: the shift schedule for
    'shift' periods and the batch run for 'batch' periods. Use this anywhere a
    window is resolved against live config; the pure compute_window is for
    offline/unit use where shifts/batch are passed in."""
    period_type = (rule.get("period_type") or "").lower()
    shifts = None
    batch = None
    if period_type == "shift":
        shifts = load_shift_starts(db)
    elif period_type == "batch":
        batch = load_batch_window(db, rule.get("period_rule") or "previous_completed", ref)
    return compute_window(rule, ref, tz_name, shifts=shifts, batch=batch)
