"""Report job log (Phase B4a) — trigger-level run records.

A `report_job` is one render trigger: it captures the report, the revision that
was active, the formats requested, status, timing, and any error. This is the
higher-level companion to `report_records` (which stays as the per-format output
detail). Additive — nothing else changes behavior.

B4a records on-demand renders in one shot via `record_job`. The scheduler
integration (start_job/finish_job for long, multi-format runs) is B4b.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
PARTIAL = "partial"
MISSED = "missed"
JOB_STATUSES = (QUEUED, RUNNING, SUCCEEDED, FAILED, PARTIAL, MISSED)
TRIGGER_KINDS = ("on_demand", "timed", "tag", "manual")

_COLS = ("id, report_id, report_name, trigger_kind, revision_id, formats, status, "
         "snapshot_at, period_start, period_end, error, created_at, started_at, "
         "finished_at, duration_ms")


def record_job(db: Session, *, report_id: int, report_name: str | None,
               trigger_kind: str, revision_id: int | None, formats: str | None,
               status: str, snapshot_at: datetime | None,
               period_start: datetime | None, period_end: datetime | None,
               error: str | None, started_at: datetime | None,
               finished_at: datetime | None) -> int:
    """Insert a completed job in one shot. Does NOT commit (caller commits)."""
    duration_ms = None
    if started_at and finished_at:
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    return db.execute(text("""
        INSERT INTO report_jobs
            (report_id, report_name, trigger_kind, revision_id, formats, status,
             snapshot_at, period_start, period_end, error, created_at,
             started_at, finished_at, duration_ms)
        VALUES (:rid, :name, :tk, :rev, :fmts, :st, :snap, :ps, :pe, :err, NOW(),
                :sa, :fa, :dur)
        RETURNING id
    """), {
        "rid": report_id, "name": report_name, "tk": trigger_kind, "rev": revision_id,
        "fmts": formats, "st": status, "snap": snapshot_at, "ps": period_start,
        "pe": period_end, "err": error, "sa": started_at, "fa": finished_at,
        "dur": duration_ms,
    }).scalar_one()


def list_jobs(db: Session, report_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where = "WHERE report_id = :rid" if report_id is not None else ""
    params: dict[str, Any] = {"lim": max(1, min(limit, 500))}
    if report_id is not None:
        params["rid"] = report_id
    rows = db.execute(text(
        f"SELECT {_COLS} FROM report_jobs {where} ORDER BY id DESC LIMIT :lim"
    ), params).mappings().all()
    return [dict(r) for r in rows]


# --- Per-stage generation telemetry (resolve -> data -> render -> deliver) --
STAGE_NAMES = ("resolve", "data", "render", "deliver")


def record_stages(db: Session, job_id: int, stages: list[dict[str, Any]]) -> int:
    """Insert per-stage telemetry rows for a job. Does NOT commit (caller commits)."""
    if not job_id or not stages:
        return 0
    for s in stages:
        db.execute(text("""
            INSERT INTO report_job_stages
                (job_id, seq, stage, status, n, bytes, ms, detail, created_at)
            VALUES (:jid, :seq, :stage, :status, :n, :bytes, :ms, :detail, NOW())
        """), {
            "jid": job_id, "seq": s.get("seq"), "stage": s.get("stage"),
            "status": s.get("status"), "n": s.get("n"), "bytes": s.get("bytes"),
            "ms": s.get("ms"), "detail": s.get("detail"),
        })
    return len(stages)


def list_stages(db: Session, job_id: int) -> list[dict[str, Any]]:
    """Per-stage telemetry for one job, in stage order."""
    rows = db.execute(text("""
        SELECT id, job_id, seq, stage, status, n, bytes, ms, detail, created_at
        FROM report_job_stages WHERE job_id = :jid ORDER BY seq, id
    """), {"jid": job_id}).mappings().all()
    return [dict(r) for r in rows]
