"""Per-report diagnostics — Phase RM.6 (v4).

Each linked trigger is its OWN group with a CALENDAR-aligned completeness grid:

    Hourly   -> 00:00 .. 23:00          (today)
    Every N  -> 00:00 .. 23:45          (today)
    Daily    -> day 1 .. end-of-month   (this month)
    Weekly   -> the weekday's dates      (this month)
    Monthly  -> Jan .. Dec              (this year)
    Yearly   -> last 6 years

Every slot is a fixed calendar position; status is overlaid:
    generated | failed | missing | upcoming (not due yet)
A slot matches a run via report_jobs.snapshot_at (the due instant). Failed
slots carry the job's recorded error so the UI can explain them on click;
missing slots have no record by definition (nothing ran), so the UI shows a
best-effort cause.

Tag health replicates the canonical rule used by /diagnostics/summary EXACTLY
(st < 128 OR age > device.stale_after_sec; writable / disabled-device /
computed-device tags are excluded), so this never disagrees with the Tags page.

Per-trigger next-due is returned so each row gets its own live counter.
Read-only; no migration.
"""
from __future__ import annotations

import calendar
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.workers.report_schedule import next_instant

router = APIRouter(prefix="/api/report-config", tags=["report-diagnostics"])
_MAX_SLOTS = 400


def _app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _minute_key(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).replace(second=0, microsecond=0).timestamp() // 60)


def _hhmm(total_min: Optional[int]) -> tuple[int, int]:
    m = 6 * 60 if total_min is None else int(total_min)
    return m // 60, m % 60


def _hhmm_str(total_min: Optional[int]) -> str:
    h, m = _hhmm(total_min)
    return f"{h:02d}:{m:02d}"


def _kind(t: dict[str, Any]) -> str:
    if t.get("interval_minutes"):
        return "interval"
    if t.get("month_of_year"):
        return "yearly"
    if t.get("day_of_month"):
        return "monthly"
    if t.get("day_of_week") is not None or t.get("days_of_week"):
        return "weekly"
    period = (t.get("period") or "").lower()
    if period == "hourly" or (t.get("at_minute") is not None and period != "daily"):
        return "hourly"
    if t.get("at_time_min") is not None or period == "daily":
        return "daily"
    return "hourly"


def _cadence_label(t: dict[str, Any]) -> str:
    k = _kind(t)
    if k == "interval":
        n = int(t["interval_minutes"])
        return "Every minute" if n == 1 else f"Every {n} min"
    if k == "yearly":
        return f"Yearly @ {_hhmm_str(t.get('at_time_min'))}"
    if k == "monthly":
        return f"Monthly (day {int(t['day_of_month'])}) @ {_hhmm_str(t.get('at_time_min'))}"
    if k == "weekly":
        return f"Weekly @ {_hhmm_str(t.get('at_time_min'))}"
    if k == "hourly":
        return f"Hourly @ :{int(t.get('at_minute') or 0):02d}"
    return f"Daily @ {_hhmm_str(t.get('at_time_min'))}"


_SPAN_LABEL = {"hourly": "today", "interval": "today", "daily": "this month",
               "weekly": "this month", "monthly": "this year", "yearly": "last 6 years"}


def _first_dow(t: dict[str, Any]) -> int:
    dw = t.get("days_of_week")
    if dw:
        for part in str(dw).replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                return int(part) % 7
    if t.get("day_of_week") is not None:
        return int(t["day_of_week"]) % 7
    return 0  # Monday


def _calendar_slots(t: dict[str, Any], kind: str, now: datetime) -> list[tuple[datetime, str]]:
    tzi = now.tzinfo
    y, mo, dy = now.year, now.month, now.day
    hh, mm = _hhmm(t.get("at_time_min"))
    out: list[tuple[datetime, str]] = []
    if kind == "hourly":
        am = int(t.get("at_minute") or 0)
        for h in range(24):
            out.append((datetime(y, mo, dy, h, am, tzinfo=tzi), f"{h:02d}–{(h + 1) % 24:02d}"))
    elif kind == "interval":
        n = max(1, int(t["interval_minutes"]))
        cur = datetime(y, mo, dy, 0, 0, tzinfo=tzi)
        end = cur + timedelta(days=1)
        while cur < end and len(out) < _MAX_SLOTS:
            nxt = cur + timedelta(minutes=n)
            out.append((cur, f"{cur.strftime('%H:%M')}–{nxt.strftime('%H:%M')}"))
            cur = nxt
    elif kind == "daily":
        dim = calendar.monthrange(y, mo)[1]
        for d in range(1, dim + 1):
            out.append((datetime(y, mo, d, hh, mm, tzinfo=tzi), str(d)))
    elif kind == "weekly":
        dow = _first_dow(t)
        dim = calendar.monthrange(y, mo)[1]
        for d in range(1, dim + 1):
            dt = datetime(y, mo, d, hh, mm, tzinfo=tzi)
            if dt.weekday() == dow:
                out.append((dt, dt.strftime("%d %b")))
    elif kind == "monthly":
        D = int(t.get("day_of_month") or 1)
        for m in range(1, 13):
            dd = min(D, calendar.monthrange(y, m)[1])
            out.append((datetime(y, m, dd, hh, mm, tzinfo=tzi), calendar.month_abbr[m]))
    elif kind == "yearly":
        M = int(t.get("month_of_year") or 1)
        D = int(t.get("day_of_month") or 1)
        for yr in range(y - 5, y + 1):
            dd = min(D, calendar.monthrange(yr, M)[1])
            out.append((datetime(yr, M, dd, hh, mm, tzinfo=tzi), str(yr)))
    return out


def _has_content(d: dict[str, Any]) -> bool:
    if (d.get("template_mode") or "html") == "blocks":
        return bool(d.get("template_blocks"))
    return bool((d.get("template_html") or "").strip())


@router.get("/diagnostics")
def report_diagnostics(db: Session = Depends(get_session)):
    tz = _app_tz()
    now = datetime.now(tz)

    defs = db.execute(text("""
        SELECT id, name, category, report_type, enabled, template_mode, template_html, template_blocks
        FROM report_definitions ORDER BY name
    """)).mappings().all()

    reports: list[dict[str, Any]] = []
    for d in defs:
        rid = d["id"]
        trigs = [dict(t) for t in db.execute(text("""
            SELECT t.* FROM report_trigger_links l
            JOIN report_triggers t ON t.id = l.trigger_id
            WHERE l.report_id = :rid AND t.enabled = true
        """), {"rid": rid}).mappings().all()]
        timed = [t for t in trigs if t.get("trigger_type") == "timed"]

        # earliest calendar instant across triggers (for the jobs fetch range)
        plans = {t["id"]: (_kind(t)) for t in timed}
        all_slots = {t["id"]: _calendar_slots(t, plans[t["id"]], now) for t in timed}
        earliest = now
        for slots in all_slots.values():
            if slots:
                earliest = min(earliest, slots[0][0])

        rows = db.execute(text("""
            SELECT status, snapshot_at, duration_ms, error
            FROM report_jobs
            WHERE report_id = :rid AND snapshot_at >= :start
        """), {"rid": rid, "start": earliest}).mappings().all()
        ok_keys: set[int] = set()
        fail_err: dict[int, str] = {}
        durs: list[int] = []
        for j in rows:
            if j["snapshot_at"] is not None:
                k = _minute_key(j["snapshot_at"])
                if j["status"] == "succeeded":
                    ok_keys.add(k)
                elif j["status"] in ("failed", "missed"):
                    fail_err[k] = j["error"] or "Run failed (no detail recorded)."
            if j["status"] == "succeeded" and j["duration_ms"] is not None:
                durs.append(j["duration_ms"])

        trigger_views: list[dict[str, Any]] = []
        roll_exp = roll_gen = roll_miss = roll_fail = 0
        for t in trigs:
            if t.get("trigger_type") != "timed":
                trigger_views.append({
                    "trigger_id": t["id"], "name": t.get("name"), "type": "tag",
                    "cadence": "On tag change", "span_label": "event-driven",
                    "kind": "tag", "next_due_at": None,
                    "coverage": {"expected": 0, "generated": 0, "missing": 0, "failed": 0, "upcoming": 0},
                    "slots": [],
                })
                continue
            kind = plans[t["id"]]
            slots_out = []
            g = m = f = up = 0
            for seq, (dt, label) in enumerate(all_slots[t["id"]], start=1):
                k = _minute_key(dt)
                detail = None
                if dt > now:
                    st = "upcoming"; up += 1
                    detail = "Scheduled — not due yet."
                elif k in ok_keys:
                    st = "generated"; g += 1
                elif k in fail_err:
                    st = "failed"; f += 1
                    detail = fail_err[k]
                else:
                    st = "missing"; m += 1
                    detail = ("No report was produced for this scheduled time. "
                              "Likely the scheduler was not running then, the trigger "
                              "was linked later, or it predates a config/timezone change.")
                slots_out.append({"seq": seq, "at": dt.isoformat(), "label": label,
                                  "status": st, "detail": detail})
            lfa = db.execute(text(
                "SELECT last_fired_at FROM report_trigger_state WHERE report_id=:rid AND trigger_id=:tid"),
                {"rid": rid, "tid": t["id"]}).scalar()
            try:
                nd = next_instant(t, now, lfa)
            except Exception:
                nd = None
            exp = g + m + f
            trigger_views.append({
                "trigger_id": t["id"], "name": t.get("name"), "type": "timed",
                "cadence": _cadence_label(t), "span_label": _SPAN_LABEL.get(kind, "today"),
                "kind": kind, "next_due_at": nd.isoformat() if nd else None,
                "coverage": {"expected": exp, "generated": g, "missing": m, "failed": f, "upcoming": up},
                "slots": slots_out,
            })
            roll_exp += exp; roll_gen += g; roll_miss += m; roll_fail += f

        timing = {
            "last_ms": durs[0] if durs else None,
            "avg_ms": round(sum(durs) / len(durs)) if durs else None,
            "max_ms": max(durs) if durs else None,
            "samples": len(durs),
        }

        last_success = db.execute(text(
            "SELECT max(created_at) FROM report_jobs WHERE report_id=:rid AND status='succeeded'"), {"rid": rid}).scalar()
        last_fail = db.execute(text(
            "SELECT created_at, error FROM report_jobs WHERE report_id=:rid AND status='failed' "
            "ORDER BY created_at DESC LIMIT 1"), {"rid": rid}).mappings().first()
        last_any = db.execute(text(
            "SELECT max(created_at) FROM report_jobs WHERE report_id=:rid"), {"rid": rid}).scalar()

        # ---- tag health: EXACT canonical rule (matches /diagnostics/summary) --
        tag_rows = db.execute(text("""
            SELECT tg.id, tg.name, tg.writable, lv.st, lv.time,
                   d.protocol, d.enabled AS dev_enabled, d.stale_after_sec,
                   EXTRACT(EPOCH FROM (NOW() - lv.time)) AS age_sec
            FROM report_tags rt
            JOIN tags tg ON tg.id = rt.tag_id
            LEFT JOIN devices d ON d.id = tg.device_id
            LEFT JOIN latest_tag_values lv ON lv.tag_id = tg.id
            WHERE rt.report_id = :rid
            ORDER BY rt.position, tg.name
        """), {"rid": rid}).mappings().all()
        tag_list = []
        for r in tag_rows:
            st = r["st"]
            excluded = bool(r["writable"]) or (r["protocol"] == "computed") or (r["dev_enabled"] is False)
            quality = "no_data" if st is None else ("good" if st >= 128 else "bad")
            stale = False
            if not excluded:
                if r["time"] is None:
                    stale = True
                elif r["stale_after_sec"] is not None and r["age_sec"] is not None \
                        and r["age_sec"] > r["stale_after_sec"]:
                    stale = True
            healthy = excluded or ((st is not None and st >= 128) and not stale)
            tag_list.append({
                "tag_id": r["id"], "name": r["name"], "st": st,
                "quality": quality, "stale": stale, "excluded": excluded,
                "healthy": healthy,
                "age_sec": round(r["age_sec"]) if r["age_sec"] is not None else None,
            })
        tags_total = len(tag_list)
        tags_healthy = sum(1 for t in tag_list if t["healthy"])

        stor = db.execute(text("""
            SELECT count(*) AS n, COALESCE(sum(byte_size),0) AS b, max(generated_at) AS last_at
            FROM report_records WHERE report_id=:rid AND status='ok'
        """), {"rid": rid}).mappings().first()

        # level: error if failing or no content; warning if missing or unhealthy tags
        hard = (roll_fail > 0 and roll_gen == 0) or (trigs and not _has_content(d))
        soft = bool(roll_miss) or (tags_total and tags_healthy < tags_total)
        level = "error" if hard else ("warning" if soft else "ok")

        reports.append({
            "report_id": rid, "report_name": d["name"], "category": d["category"],
            "report_type": d["report_type"],
            "enabled": d["enabled"], "level": level,
            "triggers": trigger_views,
            "coverage": {"expected": roll_exp, "generated": roll_gen,
                         "missing": roll_miss, "failed": roll_fail},
            "timing": timing,
            "timeline": {
                "last_generated_at": last_any.isoformat() if last_any else None,
                "last_success_at": last_success.isoformat() if last_success else None,
                "last_failed_at": last_fail["created_at"].isoformat() if last_fail else None,
                "last_error": last_fail["error"] if last_fail else None,
            },
            "tags": {"total": tags_total, "healthy": tags_healthy, "list": tag_list},
            "storage": {"files": stor["n"] if stor else 0,
                        "bytes": int(stor["b"]) if stor and stor["b"] is not None else 0,
                        "last_file_at": stor["last_at"].isoformat() if stor and stor["last_at"] else None},
        })

    summary = {
        "reports": len(reports),
        "ok": sum(1 for r in reports if r["level"] == "ok"),
        "warning": sum(1 for r in reports if r["level"] == "warning"),
        "error": sum(1 for r in reports if r["level"] == "error"),
        "total_missing": sum(r["coverage"]["missing"] for r in reports),
    }
    return {"timezone": settings.app_timezone, "summary": summary, "reports": reports}
