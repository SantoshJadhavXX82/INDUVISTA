"""Report validation engine (Phase B1).

Read-only configuration checks that answer one question: is this report safe to
run / activate? Returns a structured result the UI renders and the (future)
activation gate consults. No writes, no schema dependency beyond what already
exists — so it is additive and safe.

Result levels mirror the spec (§15):
  passed   no issue
  warning  can run, but something is probably wrong (e.g. no destinations)
  failed   cannot run as configured (e.g. template missing for PDF output)
  info     advisory only

  validate_report(db, report_id, tz_name) -> {"overall": <level>, "results": [...]}
  (returns None if the report does not exist, so the caller can 404)
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.report_period import compute_window

FAILED = "failed"
WARNING = "warning"
PASSED = "passed"
INFO = "info"
_RANK = {PASSED: 0, INFO: 0, WARNING: 1, FAILED: 2}


def validate_report(db: Session, report_id: int, tz_name: str) -> dict[str, Any] | None:
    results: list[dict[str, Any]] = []

    def add(category: str, level: str, message: str, field: str | None = None) -> None:
        results.append({"category": category, "level": level,
                        "message": message, "field": field})

    defn = db.execute(text(
        "SELECT name, category, report_type, template_html "
        "FROM report_definitions WHERE id = :id"
    ), {"id": report_id}).mappings().first()
    if defn is None:
        return None

    # --- Identity ---
    if not (defn["name"] or "").strip():
        add("identity", FAILED, "Report name is required.", "name")

    # --- Data bindings ---
    binds = db.execute(text(
        "SELECT tag_id, alias FROM report_tags WHERE report_id = :r ORDER BY position"
    ), {"r": report_id}).mappings().all()
    if not binds:
        add("data", WARNING, "No tags are bound; the report will have no data rows.")
    aliases = [b["alias"] for b in binds if b["alias"]]
    dupes = sorted({a for a in aliases if aliases.count(a) > 1})
    if dupes:
        add("data", FAILED, f"Duplicate alias(es): {', '.join(dupes)}.")
    tag_ids = [b["tag_id"] for b in binds]
    if tag_ids:
        present = {r[0] for r in db.execute(text(
            "SELECT id FROM tags WHERE id = ANY(:ids) AND deleted_at IS NULL"
        ), {"ids": tag_ids}).fetchall()}
        missing = [t for t in tag_ids if t not in present]
        if missing:
            add("data", FAILED,
                f"{len(missing)} bound tag(s) no longer exist or were deleted "
                f"(e.g. {missing[:10]}).")

    # --- Period rule (resolvable?) ---
    rule = db.execute(text(
        "SELECT * FROM report_period_rule WHERE report_id = :r AND enabled = true"
    ), {"r": report_id}).mappings().first()
    if rule:
        try:
            compute_window(dict(rule), datetime.now(ZoneInfo("UTC")), tz_name)
            add("period", PASSED, "Period rule resolves to a valid window.")
        except Exception as e:  # noqa: BLE001 — surface the reason to the user
            add("period", FAILED, f"Period rule cannot be resolved: {e}")
    else:
        add("period", INFO, "No period rule — the report renders a live snapshot.")

    # --- Delivery + the template requirement it implies ---
    dests = db.execute(text("""
        SELECT dl.fmt AS override, d.default_fmts, d.name, d.enabled
        FROM report_destination_links dl
        JOIN report_destinations d ON d.id = dl.destination_id
        WHERE dl.report_id = :r
    """), {"r": report_id}).mappings().all()
    if not dests:
        add("delivery", WARNING,
            "No destinations linked; scheduled runs have nowhere to deliver.")
    fmts: set[str] = set()
    for d in dests:
        eff = (d["override"] or "").strip() or (d["default_fmts"] or "pdf")
        for f in eff.split(","):
            f = f.strip().lower()
            if f:
                fmts.add(f)
    if (fmts & {"pdf", "html"}) and not (defn["template_html"] or "").strip():
        add("layout", FAILED,
            "A template is required because a linked destination outputs PDF or HTML.",
            "template_html")

    # --- Triggers ---
    ntrig = db.execute(text(
        "SELECT count(*) FROM report_trigger_links WHERE report_id = :r"
    ), {"r": report_id}).scalar() or 0
    if ntrig == 0 and defn["category"] != "on_demand":
        add("trigger", WARNING,
            "No triggers linked; this report will not run on a schedule.")

    overall = PASSED
    for r in results:
        if _RANK[r["level"]] > _RANK[overall]:
            overall = r["level"]
    # collapse INFO-only into PASSED for the headline
    if overall == INFO:
        overall = PASSED
    return {"overall": overall, "results": results}
