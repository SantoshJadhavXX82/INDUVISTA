"""Report trigger engine (worker).

Makes configured report triggers actually fire:
  1. loads enabled report definitions + their linked triggers,
  2. for TIMED triggers, computes the due instant (report_schedule.due_instant)
     and fires if it is newer than the last-fired instant we recorded,
  3. for TAG triggers, reads the latest tag value and fires on the configured
     edge (to_nonzero / rising / any_change),
  4. renders the report (services.report_render) for its tag set,
  5. delivers the PDF to every linked destination (folder today; printer/share
     are stubbed with a clear log line),
  6. writes a report_records row (ok or error) and updates trigger state.

Mirrors the alarm_evaluator worker shape: SessionLocal, responsive sleep,
signal handlers, per-cycle reload, per-item try/except with rollback.

Disabled unless ENABLE_REPORT_SCHEDULER is true (default true). Tick interval
from REPORT_SCHEDULER_INTERVAL_SEC (default 30s).
"""
from __future__ import annotations
import os
import sys
import signal
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Optional

from sqlalchemy import text
from app.db import SessionLocal
from app.services.report_render import render_report
from app.services.report_formats import render_html, build_report_data, to_json, to_xml
from app.services.report_aggregate import resolve_report_context
from app.services.report_revisions import effective_definition
from app.workers.report_schedule import due_instant, tag_condition_fires

log = logging.getLogger("report_scheduler")

ENABLED = os.getenv("ENABLE_REPORT_SCHEDULER", "true").lower() in ("1", "true", "yes")
TICK_SEC = float(os.getenv("REPORT_SCHEDULER_INTERVAL_SEC", "30"))
APP_TZ = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
WARMUP_SEC = float(os.getenv("REPORT_SCHEDULER_WARMUP_SEC", "5.0"))
# Skip a timed catch-up older than this many minutes (avoid a flood of stale
# reports after a long downtime; one catch-up is enough). 0 = always catch up.
MAX_CATCHUP_MIN = float(os.getenv("REPORT_SCHEDULER_MAX_CATCHUP_MIN", "1440"))

_should_stop = False
_SLEEP_CHUNK_SEC = 0.2


def _install_sig_handlers() -> None:
    def _handler(signum, frame):
        global _should_stop
        if not _should_stop:
            log.info("report_scheduler: signal %s received, shutting down", signum)
            _should_stop = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _responsive_sleep(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not _should_stop:
        remaining = deadline - time.monotonic()
        time.sleep(min(_SLEEP_CHUNK_SEC, remaining))


# --------------------------------------------------------------------------- DB
def _load_jobs(db) -> list[dict[str, Any]]:
    """One row per (enabled report x linked enabled trigger), with the trigger
    fields and any saved state. Tag set is resolved per report from its
    template if present, else all tags referenced (kept simple: pull tag_ids
    from report_trigger link's report; here we read an optional tag_ids column
    if your definitions store one, else empty -> caller may supply)."""
    sql = text("""
        SELECT d.id AS report_id, d.name AS report_name, d.category,
               t.id AS trigger_id, t.trigger_type, t.period,
               t.at_minute, t.at_time_min, t.day_of_month, t.month_of_year,
               t.day_of_week, t.interval_minutes, t.cron_expr,
               t.tag_id, t.tag_edge, t.tag_op, t.tag_value, t.tag_expr,
               s.last_fired_at, s.last_seen_value
        FROM report_definitions d
        JOIN report_trigger_links l ON l.report_id = d.id
        JOIN report_triggers t      ON t.id = l.trigger_id
        LEFT JOIN report_trigger_state s
               ON s.report_id = d.id AND s.trigger_id = t.id
        WHERE d.enabled = true AND t.enabled = true
    """)
    return [dict(r._mapping) for r in db.execute(sql)]


def _report_tag_ids(db, report_id: int) -> list[int]:
    """Tag ids to render for a report. Strategy: use the report's destination
    /render convention — here we read distinct tag ids the report references via
    a (future) report_tags table if present; otherwise return [] and let the
    template use whatever live context is built. Kept minimal + safe."""
    try:
        rows = db.execute(text(
            "SELECT tag_id FROM report_tags WHERE report_id = :rid ORDER BY position"
        ), {"rid": report_id}).fetchall()
        return [r[0] for r in rows]
    except Exception:
        db.rollback()
        return []  # no report_tags table -> template-driven / empty context


def _latest_value(db, tag_id: int) -> Optional[float]:
    row = db.execute(text(
        "SELECT value_double FROM latest_tag_values WHERE tag_id = :tid"
    ), {"tid": tag_id}).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _destinations(db, report_id: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT dl.fmt, dest.default_fmts, dest.dest_type, dest.target, dest.name
        FROM report_destination_links dl
        JOIN report_destinations dest ON dest.id = dl.destination_id
        WHERE dl.report_id = :rid AND dest.enabled = true
    """), {"rid": report_id})
    return [dict(r._mapping) for r in rows]


def _upsert_state(db, report_id: int, trigger_id: int,
                  last_fired_at: Optional[datetime] = None,
                  last_seen_value: Optional[float] = None) -> None:
    db.execute(text("""
        INSERT INTO report_trigger_state
            (report_id, trigger_id, last_fired_at, last_seen_value, updated_at)
        VALUES (:rid, :tid, :lfa, :lsv, now())
        ON CONFLICT (report_id, trigger_id) DO UPDATE SET
            last_fired_at = COALESCE(EXCLUDED.last_fired_at,
                                     report_trigger_state.last_fired_at),
            last_seen_value = COALESCE(EXCLUDED.last_seen_value,
                                       report_trigger_state.last_seen_value),
            updated_at = now()
    """), {"rid": report_id, "tid": trigger_id,
           "lfa": last_fired_at, "lsv": last_seen_value})


class _UnsupportedFormat(Exception):
    """Raised for a format that has no serializer yet (e.g. csv)."""


def _render_one(fmt: str, dm, ctx: dict[str, Any]) -> "tuple[bytes, str]":
    """Render the report context to ONE output format → (bytes, ext).

    Mirrors api/reports_config.render_definition's format dispatch so scheduled
    and on-demand renders are byte-for-byte consistent. Raises _UnsupportedFormat
    for formats with no serializer; raises a normal Exception for genuine render
    failures (e.g. pdf/html requested with no template).
    """
    page = dm.get("page_size") or "A4"
    orient = dm.get("orientation") or "portrait"
    tmpl = (dm.get("template_html") or "")
    if fmt == "pdf":
        if not tmpl.strip():
            raise ValueError("pdf requested but report has no template_html")
        return render_report(tmpl, ctx, page, orient), "pdf"
    if fmt == "html":
        if not tmpl.strip():
            raise ValueError("html requested but report has no template_html")
        return render_html(tmpl, ctx, page, orient).encode("utf-8"), "html"
    if fmt == "json":
        return to_json(build_report_data(ctx)), "json"
    if fmt == "xml":
        return to_xml(build_report_data(ctx)), "xml"
    raise _UnsupportedFormat(f"format '{fmt}' is not supported for scheduled delivery yet")


def _record(db, job: dict[str, Any], trigger_kind: str, snapshot_at: datetime,
            *, fmt: str, status: str,
            file_path: Optional[str] = None, byte_size: Optional[int] = None,
            error: Optional[str] = None) -> None:
    """Insert one report_records row — one per delivered/attempted format."""
    db.execute(text("""
        INSERT INTO report_records
            (report_id, report_name, category, trigger_id, trigger_kind,
             snapshot_at, period_start, period_end, fmt, file_path, byte_size,
             status, error)
        VALUES (:rid, :rn, :cat, :tid, :tk, :snap, :ps, :pe, :fmt, :fp, :sz,
                :st, :err)
    """), {"rid": job["report_id"], "rn": job["report_name"],
           "cat": job.get("category"), "tid": job.get("trigger_id"),
           "tk": trigger_kind, "snap": snapshot_at,
           "ps": job.get("_period_start"), "pe": job.get("_period_end"),
           "fmt": fmt, "fp": file_path, "sz": byte_size, "st": status,
           "err": error})


# --------------------------------------------------------------- render+deliver
def _fire(db, job: dict[str, Any], tz: ZoneInfo, snapshot_at: datetime,
          trigger_kind: str) -> None:
    """Render the report and deliver to all destinations; write report_records."""
    report_id = job["report_id"]
    report_name = job["report_name"]
    tag_ids = _report_tag_ids(db, report_id)
    dests = _destinations(db, report_id)
    if not dests:
        # Still render+record once to the default archive so misconfig is visible.
        dests = [{"fmt": None, "default_fmts": "pdf", "dest_type": "folder",
                  "target": "/var/lib/induvista/reports", "name": "Local Archive (default)"}]

    try:
        # build the definition + context, render once, deliver to each dest.
        # Phase B3b: snapshot if the report has an active revision, else live.
        dm = effective_definition(db, report_id)
        if dm is None:
            raise RuntimeError(f"report {report_id} not found")
        ctx, window = resolve_report_context(db, report_id, APP_TZ, snapshot_at, tag_ids)
        # Carry the computed window so _record persists it on every row.
        job["_period_start"] = window[0] if window else None
        job["_period_end"] = window[1] if window else None
        # Templates reference the report's own metadata; the context builders
        # do not set these, so inject them (mirrors the on-demand endpoint).
        ctx["report"]["name"] = dm.get("name") or report_name
        ctx["report"]["category"] = dm.get("category")
        ctx["report"]["report_type"] = dm.get("report_type")

        stamp = snapshot_at.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in report_name)
        rendered: dict[str, tuple[bytes, str]] = {}   # fmt -> (bytes, ext), rendered once

        for d in dests:
            # Effective format set for THIS destination: the per-report override
            # (the link's fmt) wins; else the destination's own default set;
            # else pdf. Tokens normalized (lowercase, no blanks).
            override = (d.get("fmt") or "").strip()
            default = (d.get("default_fmts") or "pdf").strip()
            fmt_set = [f.strip().lower() for f in (override or default).split(",") if f.strip()] or ["pdf"]

            for fmt in fmt_set:
                # Render each format at most once across all destinations.
                try:
                    if fmt not in rendered:
                        rendered[fmt] = _render_one(fmt, dm, ctx)
                    payload, ext = rendered[fmt]
                except _UnsupportedFormat as exc:
                    # e.g. csv — no serializer yet. Log + skip without a record,
                    # matching the on-demand endpoint (which also can't emit it).
                    log.info("report '%s': %s; skipping for dest '%s'",
                             report_name, exc, d.get("name"))
                    continue
                except Exception as exc:
                    log.exception("report '%s': render %s failed", report_name, fmt)
                    _record(db, job, trigger_kind, snapshot_at, fmt=fmt,
                            status="error", error=str(exc)[:500])
                    continue

                # Deliver with the format-correct file extension.
                try:
                    out_dir = Path(d["target"]); out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / f"{safe}_{stamp}.{ext}"
                    dtype = d.get("dest_type")
                    if dtype in ("folder", "network_drive"):
                        out_path.write_bytes(payload)
                    elif dtype == "printer":
                        out_path.write_bytes(payload)  # spool stub: write then (future) lp/IPP
                        log.info("report '%s': printer delivery stubbed (wrote %s)",
                                 report_name, out_path)
                    else:
                        log.warning("report '%s': unknown dest_type '%s'", report_name, dtype)
                        _record(db, job, trigger_kind, snapshot_at, fmt=fmt,
                                status="error", error=f"unknown dest_type '{dtype}'")
                        continue
                except Exception as exc:
                    log.exception("report '%s': deliver %s to '%s' failed",
                                  report_name, fmt, d.get("name"))
                    _record(db, job, trigger_kind, snapshot_at, fmt=fmt,
                            status="error", error=str(exc)[:500])
                    continue

                _record(db, job, trigger_kind, snapshot_at, fmt=fmt, status="ok",
                        file_path=str(out_path), byte_size=len(payload))
                log.info("report '%s' fired (%s) -> %s [%s] (%d bytes)",
                         report_name, trigger_kind, out_path, fmt, len(payload))

    except Exception as e:
        log.exception("report '%s' render/deliver failed", report_name)
        db.execute(text("""
            INSERT INTO report_records
                (report_id, report_name, category, trigger_id, trigger_kind,
                 snapshot_at, period_start, period_end, fmt, status, error)
            VALUES (:rid, :rn, :cat, :tid, :tk, :snap, :ps, :pe, 'pdf', 'error', :err)
        """), {"rid": report_id, "rn": report_name, "cat": job.get("category"),
               "tid": job["trigger_id"], "tk": trigger_kind,
               "snap": snapshot_at, "ps": job.get("_period_start"),
               "pe": job.get("_period_end"), "err": str(e)[:500]})


# ------------------------------------------------------------------------- loop
def _tick(db, tz: ZoneInfo) -> int:
    now = datetime.now(tz)
    fired = 0
    for job in _load_jobs(db):
        try:
            if job["trigger_type"] == "timed":
                due = due_instant(job, now, job.get("last_fired_at"))
                if due is None:
                    continue
                lfa = job.get("last_fired_at")
                if lfa is not None and due <= lfa:
                    continue  # already fired this instant
                # optional stale-catchup guard
                if MAX_CATCHUP_MIN > 0:
                    age_min = (now - due).total_seconds() / 60.0
                    if age_min > MAX_CATCHUP_MIN:
                        log.info("report '%s': skipping stale due instant %s (%.0fm old)",
                                 job["report_name"], due, age_min)
                        _upsert_state(db, job["report_id"], job["trigger_id"],
                                      last_fired_at=due)
                        db.commit()
                        continue
                _fire(db, job, tz, snapshot_at=due, trigger_kind="timed")
                _upsert_state(db, job["report_id"], job["trigger_id"],
                              last_fired_at=due)
                db.commit()
                fired += 1

            elif job["trigger_type"] == "tag":
                tag_id = job.get("tag_id")
                if not tag_id:
                    continue
                cur = _latest_value(db, tag_id)
                last = job.get("last_seen_value")
                if tag_condition_fires(job, last, cur):
                    _fire(db, job, tz, snapshot_at=now, trigger_kind="tag")
                    fired += 1
                # always record the latest value so edges are detected next tick
                _upsert_state(db, job["report_id"], job["trigger_id"],
                              last_seen_value=cur)
                db.commit()
        except Exception:
            log.exception("trigger handling failed for report %s trigger %s",
                          job.get("report_id"), job.get("trigger_id"))
            db.rollback()
    return fired


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not ENABLED:
        log.info("report_scheduler disabled (ENABLE_REPORT_SCHEDULER=false); idling.")
        # stay alive so the container doesn't crash-loop, but do nothing
        _install_sig_handlers()
        while not _should_stop:
            _responsive_sleep(60)
        return

    _install_sig_handlers()
    try:
        tz = ZoneInfo(APP_TZ)
    except Exception:
        log.warning("bad APP_TIMEZONE '%s'; falling back to UTC", APP_TZ)
        tz = ZoneInfo("UTC")

    log.info("report_scheduler starting (tick=%.0fs, tz=%s, max_catchup=%.0fm)",
             TICK_SEC, APP_TZ, MAX_CATCHUP_MIN)

    if WARMUP_SEC > 0:
        _responsive_sleep(WARMUP_SEC)
        if _should_stop:
            return

    cycles = 0
    while not _should_stop:
        cycle_start = time.monotonic()
        try:
            with SessionLocal() as db:
                n = _tick(db, tz)
                if n:
                    log.info("tick fired %d report(s)", n)
        except Exception:
            log.exception("scheduler cycle failed")
        cycles += 1
        elapsed = time.monotonic() - cycle_start
        if elapsed < TICK_SEC:
            _responsive_sleep(TICK_SEC - elapsed)

    log.info("report_scheduler: stopped cleanly after %d cycles", cycles)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log.exception("report_scheduler crashed")
        sys.exit(1)
