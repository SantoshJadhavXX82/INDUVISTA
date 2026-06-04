"""Report revision model (Phase B3a) — additive, runtime-dormant.

Captures immutable configuration snapshots of a report and an activation
lifecycle. Render and the scheduler still read the LIVE config; making the
active revision authoritative is Phase B3b. So nothing here changes runtime
behavior — it records history and gates activation on validation.

Lifecycle:
    draft ──activate──▶ active   (prior active ──▶ superseded)
    draft/superseded ──▶ archived (future; B3c UI)

Public API:
    snapshot_config(db, report_id)            -> dict | None
    create_draft(db, report_id, by, notes, tz)-> dict | None        (new draft revision)
    list_revisions(db, report_id)             -> list[dict]         (summaries, newest first)
    get_revision(db, rev_id)                  -> dict | None        (full, with config+validation)
    activate_revision(db, report_id, rev, by, tz) -> (dict|None, err|None)
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.report_validation import validate_report

DRAFT = "draft"
ACTIVE = "active"
SUPERSEDED = "superseded"
ARCHIVED = "archived"
REVISION_STATUSES = (DRAFT, ACTIVE, SUPERSEDED, ARCHIVED)


def _first(db: Session, sql: str, **p):
    return db.execute(text(sql), p).mappings().first()


def _all(db: Session, sql: str, **p):
    return db.execute(text(sql), p).mappings().all()


def _json(obj: Any) -> str:
    # default=str makes datetimes / Decimals JSON-safe.
    return json.dumps(obj, default=str)


def snapshot_config(db: Session, report_id: int) -> dict[str, Any] | None:
    """A self-contained, column-agnostic snapshot of a report's current config."""
    defn = _first(db, "SELECT * FROM report_definitions WHERE id = :id", id=report_id)
    if defn is None:
        return None
    d = dict(defn)
    d.pop("active_revision_id", None)  # a pointer, not part of the config

    bindings = [dict(r) for r in _all(
        db, "SELECT * FROM report_tags WHERE report_id = :r ORDER BY position", r=report_id)]
    period = _first(db, "SELECT * FROM report_period_rule WHERE report_id = :r", r=report_id)
    trig = [row[0] for row in db.execute(text(
        "SELECT trigger_id FROM report_trigger_links WHERE report_id = :r"),
        {"r": report_id}).all()]
    dests = [dict(r) for r in _all(
        db, "SELECT destination_id, fmt FROM report_destination_links WHERE report_id = :r",
        r=report_id)]

    return {
        "definition": d,
        "bindings": bindings,
        "period_rule": dict(period) if period else None,
        "trigger_ids": trig,
        "destinations": dests,
    }


def _coerce_json_fields(d: dict[str, Any]) -> dict[str, Any]:
    # psycopg2 normally returns JSONB as Python objects; be defensive anyway.
    for k in ("config", "validation"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except Exception:  # noqa: BLE001
                pass
    return d


def get_revision(db: Session, rev_id: int) -> dict[str, Any] | None:
    r = _first(db, "SELECT * FROM report_revisions WHERE id = :id", id=rev_id)
    return _coerce_json_fields(dict(r)) if r else None


def list_revisions(db: Session, report_id: int) -> list[dict[str, Any]]:
    rows = _all(db, """
        SELECT id, report_id, revision_no, status, notes,
               created_by, created_at, activated_by, activated_at,
               (validation ->> 'overall') AS validation_overall
        FROM report_revisions
        WHERE report_id = :r
        ORDER BY revision_no DESC
    """, r=report_id)
    return [dict(r) for r in rows]


def create_draft(db: Session, report_id: int, created_by: str | None,
                 notes: str | None, tz_name: str) -> dict[str, Any] | None:
    config = snapshot_config(db, report_id)
    if config is None:
        return None
    validation = validate_report(db, report_id, tz_name)
    next_no = db.execute(text(
        "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM report_revisions WHERE report_id = :r"
    ), {"r": report_id}).scalar()
    rev_id = db.execute(text("""
        INSERT INTO report_revisions
            (report_id, revision_no, status, config, validation, notes, created_by, created_at)
        VALUES (:r, :n, 'draft', :cfg ::jsonb, :val ::jsonb, :notes, :by, NOW())
        RETURNING id
    """), {"r": report_id, "n": next_no, "cfg": _json(config),
           "val": _json(validation), "notes": notes, "by": created_by}).scalar_one()
    db.commit()
    return get_revision(db, rev_id)


def activate_revision(db: Session, report_id: int, rev_id: int, user: str | None,
                      tz_name: str) -> tuple[dict[str, Any] | None, str | None]:
    """Activate a draft revision. Returns (revision, error). error is one of:
    'not_found', 'validation_failed', or a human message; None on success."""
    rev = _first(db, "SELECT id, report_id, status FROM report_revisions "
                     "WHERE id = :id AND report_id = :r", id=rev_id, r=report_id)
    if rev is None:
        return None, "not_found"
    if rev["status"] == ACTIVE:
        return get_revision(db, rev_id), None  # idempotent
    if rev["status"] in (SUPERSEDED, ARCHIVED):
        return None, "Cannot activate a superseded or archived revision."

    # Validation gate — block only hard failures (warnings are advisory).
    # NOTE (B3b): once the active revision is authoritative, validate the
    # snapshot rather than the live config.
    v = validate_report(db, report_id, tz_name)
    if v is None:
        return None, "not_found"
    if v["overall"] == "failed":
        return None, "validation_failed"

    db.execute(text("UPDATE report_revisions SET status = 'superseded' "
                    "WHERE report_id = :r AND status = 'active'"), {"r": report_id})
    db.execute(text("""
        UPDATE report_revisions
        SET status = 'active', activated_by = :by, activated_at = NOW(),
            validation = :val ::jsonb
        WHERE id = :id
    """), {"id": rev_id, "by": user, "val": _json(v)})
    db.execute(text("UPDATE report_definitions SET active_revision_id = :rev WHERE id = :r"),
               {"rev": rev_id, "r": report_id})
    db.commit()
    return get_revision(db, rev_id), None


# ---------------------------------------------------------------------------
# Authoritative-config accessors (Phase B3b).
# When a report has an ACTIVE revision, render + the scheduler source config
# from its immutable snapshot; otherwise they fall back to the live tables
# (so every report that was never activated behaves exactly as before).
# ---------------------------------------------------------------------------
def active_config(db: Session, report_id: int) -> dict[str, Any] | None:
    """The active revision's config snapshot, or None if the report has none."""
    rid = db.execute(text(
        "SELECT active_revision_id FROM report_definitions WHERE id = :id"
    ), {"id": report_id}).scalar()
    if not rid:
        return None
    rev = get_revision(db, rid)
    if rev is None or rev.get("status") != ACTIVE:
        return None
    cfg = rev.get("config")
    return cfg if isinstance(cfg, dict) else None


def effective_definition(db: Session, report_id: int) -> dict[str, Any] | None:
    """Definition fields used for rendering: snapshot if active, else live.

    Returns None only if the report does not exist. Keys mirror
    report_definitions (name, category, report_type, template_html,
    template_blocks, template_mode, page_size, orientation, ...).
    """
    snap = active_config(db, report_id)
    if snap is not None:
        defn = snap.get("definition")
        if isinstance(defn, dict) and defn:
            return dict(defn)
    row = db.execute(text(
        "SELECT name, category, report_type, template_html, template_blocks, "
        "template_mode, page_size, orientation FROM report_definitions WHERE id = :id"
    ), {"id": report_id}).mappings().first()
    return dict(row) if row else None
