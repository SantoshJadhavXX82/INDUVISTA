"""Report aggregation — turn raw historian samples into report values.

Phase A2. Given a report's data bindings and a computed period window, this
layer aggregates each tag over [start, end) from the `tag_values` hypertable and
produces a render context with the SAME shape as report_render.build_live_context
(so existing templates render unchanged).

  aggregate_value(db, tag_id, func, start, end, quality_rule) -> dict
  build_period_context(db, bindings, window, tz_name)         -> context dict

QUALITY (project standard — docs/modbus_quality_reference.md)
------------------------------------------------------------
  st >= 128            VALID / VALID_EXTENDED   -> Good
  64 <= st < 128       SUSPECT (st==64 = Stale) -> Uncertain
  st < 64              INVALID                  -> Bad
  no samples in window                          -> Missing
Display quality for a single-sample function (latest/first/last) is that
sample's own st; for window aggregates it is derived from the window's quality
distribution (all good -> Good, some good -> Uncertain, none good -> Bad).

NOTE: value-set constants below MUST match the CHECK constraints in migration
0069_report_binding_columns.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.report_render import TagCtx, GOOD_ST, build_live_context, load_named_set_states  # GOOD_ST == 128
from app.services.report_period import compute_window, resolve_window
from app.services.report_revisions import active_config

SUSPECT_ST = 64

DATA_FUNCTIONS = (
    "latest", "first", "last", "average", "min", "max",
    "sum", "count", "delta", "availability", "missing_pct",
)
QUALITY_RULES = ("all", "good_only", "good_uncertain")

# Held / Substituted are device-fabricated fill values (Phase 2c), never
# measured data, so they are excluded from period aggregates AND from the
# window-quality counts under every quality_rule. st_reason is written by the
# acquisition workers (HOLD_LAST / SUBSTITUTED); compared case-insensitively.
_EXCLUDE_FABRICATED = (
    " AND (st_reason IS NULL OR upper(st_reason) NOT IN "
    "('HOLD_LAST', 'HELD', 'SUBSTITUTED', 'SUBSTITUTE'))"
)
MISSING_ACTIONS = ("blank", "warning", "fail", "estimate")
BAD_ACTIONS = ("blank", "warning", "fail", "last_good")
VALUE_FORMATS = ("number", "text", "date", "percent", "scientific")

_SINGLE_SAMPLE = {"latest", "first", "last"}
_SCALAR_AGG = {"average": "avg", "min": "min", "max": "max",
               "sum": "sum", "count": "count"}


def _st_filter(quality_rule: str) -> str:
    """SQL fragment restricting which samples feed a value aggregate.
    Constants are inlined (module constants, never user input).

    Held / Substituted readings (Phase 2c device fault policy) are device-
    fabricated fill values, not measured data, so they are EXCLUDED from every
    aggregate regardless of quality_rule — a custody total must never silently
    include a held or substituted value. Genuine good/uncertain/stale/bad stay
    governed by the rule."""
    if quality_rule == "good_only":
        return _EXCLUDE_FABRICATED + f" AND st >= {GOOD_ST}"
    if quality_rule == "good_uncertain":
        return _EXCLUDE_FABRICATED + f" AND st >= {SUSPECT_ST}"
    return _EXCLUDE_FABRICATED  # 'all' — still excludes fabricated fill values


def derive_display_st(n_total: int, n_good: int) -> int | None:
    """Window-distribution -> a representative st for an aggregate value."""
    if not n_total:
        return None              # Missing
    if n_good == n_total:
        return GOOD_ST           # Good
    if n_good > 0:
        return SUSPECT_ST        # Uncertain
    return 0                     # Bad


def aggregate_value(db: Session, tag_id: int, func: str,
                    start: datetime, end: datetime,
                    quality_rule: str = "all") -> dict[str, Any]:
    """Aggregate one tag over [start, end). Returns value + quality + counts."""
    func = (func or "latest").lower()
    if func not in DATA_FUNCTIONS:
        raise ValueError(f"unknown data_function '{func}'")
    qrule = (quality_rule or "all").lower()
    if qrule not in QUALITY_RULES:
        raise ValueError(f"unknown quality_rule '{qrule}'")
    stf = _st_filter(qrule)
    p = {"t": tag_id, "s": start, "e": end, "good": GOOD_ST, "susp": SUSPECT_ST}

    # Window quality distribution over all numeric samples (any quality).
    dist = db.execute(text(f"""
        SELECT count(*) AS n_total,
               count(*) FILTER (WHERE st >= :good) AS n_good,
               count(*) FILTER (WHERE st >= :susp AND st < :good) AS n_unc,
               count(*) FILTER (WHERE st < :susp) AS n_bad
        FROM tag_values
        WHERE tag_id = :t AND time >= :s AND time < :e AND value_double IS NOT NULL{_EXCLUDE_FABRICATED}
    """), p).mappings().first()
    n_total = (dist["n_total"] if dist else 0) or 0
    n_good = (dist["n_good"] if dist else 0) or 0
    n_unc = (dist["n_unc"] if dist else 0) or 0
    n_bad = (dist["n_bad"] if dist else 0) or 0

    value: float | None = None
    text_value: str | None = None
    sample_st: int | None = None

    if func in _SINGLE_SAMPLE:
        order = "ASC" if func == "first" else "DESC"
        row = db.execute(text(f"""
            SELECT value_double, value_text, st FROM tag_values
            WHERE tag_id = :t AND time >= :s AND time < :e
              AND value_double IS NOT NULL{stf}
            ORDER BY time {order} LIMIT 1
        """), p).first()
        if row is not None:
            value, text_value, sample_st = row[0], row[1], row[2]
        elif func == "latest":
            # Carry-forward (sample-and-hold): if the tag didn't log inside the
            # window, report the value in effect at the window end — the most
            # recent sample at/before `end`. This matches the live-snapshot
            # 'latest' and the historian convention for on-change / slow-updating
            # tags (e.g. lab/calorific values that change every few minutes),
            # so a short period doesn't show Missing just because no new sample
            # happened to land inside it. first/last stay window-bound.
            cf = db.execute(text(f"""
                SELECT value_double, value_text, st FROM tag_values
                WHERE tag_id = :t AND time < :e
                  AND value_double IS NOT NULL{stf}
                ORDER BY time DESC LIMIT 1
            """), p).first()
            if cf is not None:
                value, text_value, sample_st = cf[0], cf[1], cf[2]
    elif func == "delta":
        rows = db.execute(text(f"""
            (SELECT value_double FROM tag_values
               WHERE tag_id = :t AND time >= :s AND time < :e
                 AND value_double IS NOT NULL{stf} ORDER BY time ASC LIMIT 1)
            UNION ALL
            (SELECT value_double FROM tag_values
               WHERE tag_id = :t AND time >= :s AND time < :e
                 AND value_double IS NOT NULL{stf} ORDER BY time DESC LIMIT 1)
        """), p).fetchall()
        if len(rows) == 2 and rows[0][0] is not None and rows[1][0] is not None:
            value = float(rows[1][0]) - float(rows[0][0])
    elif func == "availability":
        value = (100.0 * n_good / n_total) if n_total else None
    elif func == "missing_pct":
        # Data-completeness as the exact complement of availability: the
        # percentage of the window's samples that are NOT Good (uncertain or
        # bad). A window with no samples at all is treated as 100% missing.
        value = (100.0 * (n_total - n_good) / n_total) if n_total else 100.0
    else:  # average / min / max / sum / count
        agg = _SCALAR_AGG[func]
        col = "value_double" if func != "count" else "value_double"
        row = db.execute(text(f"""
            SELECT {agg}({col}) FROM tag_values
            WHERE tag_id = :t AND time >= :s AND time < :e
              AND value_double IS NOT NULL{stf}
        """), p).first()
        if row is not None and row[0] is not None:
            value = float(row[0])

    display_st = sample_st if func in _SINGLE_SAMPLE else derive_display_st(n_total, n_good)

    return {
        "value": value, "text": text_value, "st": display_st,
        "n_total": n_total, "n_good": n_good,
        "n_uncertain": n_unc, "n_bad": n_bad,
    }


def build_period_context(db: Session, bindings: Sequence[Mapping[str, Any]],
                         window: tuple[datetime, datetime],
                         tz_name: str) -> dict[str, Any]:
    """Build a render context (live-context shape) from period aggregates.

    `bindings` is a sequence of mappings mirroring report_tags rows:
      tag_id, position, alias, display_name, unit_id, data_function, quality_rule
    Tags are emitted in the given order. Keys: tag name, "id:<n>", and alias.
    """
    start, end = window
    tag_ids = [b["tag_id"] for b in bindings if b.get("tag_id")]

    meta: dict[int, Mapping[str, Any]] = {}
    if tag_ids:
        rows = db.execute(text("""
            SELECT t.id, t.name, t.description, t.named_set_id,
                   COALESCE(eu.code, t.engineering_unit) AS unit
            FROM tags t
            LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
            WHERE t.id = ANY(:ids) AND t.deleted_at IS NULL
        """), {"ids": tag_ids}).mappings().all()
        meta = {r["id"]: r for r in rows}
    states_by_set = load_named_set_states(db, [m["named_set_id"] for m in meta.values()])

    unit_ids = [b["unit_id"] for b in bindings if b.get("unit_id")]
    unit_codes: dict[int, str] = {}
    if unit_ids:
        urows = db.execute(
            text("SELECT id, code FROM engineering_units WHERE id = ANY(:ids)"),
            {"ids": unit_ids}).fetchall()
        unit_codes = {r[0]: r[1] for r in urows}

    tags_by_key: dict[str, TagCtx] = {}
    ordered: list[TagCtx] = []

    for b in bindings:
        tid = b.get("tag_id")
        m = meta.get(tid) if tid is not None else None
        if tid is not None:
            agg = aggregate_value(db, tid, b.get("data_function") or "latest",
                                  start, end, b.get("quality_rule") or "all")
        else:
            agg = {"value": None, "text": None, "st": None}
        st = agg["st"]
        name = (b.get("display_name") or (m["name"] if m else None)
                or b.get("alias") or f"tag_{tid}")
        unit = unit_codes.get(b.get("unit_id")) or (m["unit"] if m else None)
        ctx = TagCtx(
            id=tid, name=name, value=agg["value"], text=agg["text"], unit=unit,
            quality=st, quality_good=(st is not None and st >= GOOD_ST),
            age_seconds=None, description=(m["description"] if m else None),
            named_set_id=(m["named_set_id"] if m else None),
            states=(states_by_set.get(m["named_set_id"]) if m else None),
        )
        ordered.append(ctx)
        tags_by_key[ctx.name] = ctx
        if tid is not None:
            tags_by_key[f"id:{tid}"] = ctx
        if b.get("alias"):
            tags_by_key[b["alias"]] = ctx

    def tag_lookup(key: Any) -> TagCtx:
        if isinstance(key, int):
            return tags_by_key.get(f"id:{key}", TagCtx(id=key, name=f"tag_{key}"))
        return tags_by_key.get(str(key), TagCtx(name=str(key)))

    now = datetime.now(ZoneInfo("UTC"))
    return {
        "tags": tags_by_key,
        "tags_list": ordered,
        "tag": tag_lookup,
        "now": now,
        "report": {
            "generated_at": now.isoformat(timespec="seconds"),
            "timezone": tz_name,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        },
    }


# --------------------------------------------------------------------------- #
# Shared resolver — the single place that decides snapshot vs period.          #
# --------------------------------------------------------------------------- #
def resolve_report_context(db: Session, report_id: int, tz_name: str,
                           ref: datetime,
                           live_tag_ids: Sequence[int],
                           force_live: bool = False) -> tuple[dict[str, Any], tuple[datetime, datetime] | None]:
    """Build the render context for a report, choosing the path by config.

    OPT-IN: if the report has an *enabled* report_period_rule, compute the data
    window from `ref` and aggregate its bindings over that window. Otherwise
    fall back to the legacy live snapshot over `live_tag_ids` — byte-identical
    to the previous behavior.

    Returns (context, window) where window is None for the snapshot path.

    Phase B3b: if the report has an ACTIVE revision, its immutable snapshot is
    authoritative — the period rule and bindings come from the snapshot, not the
    live tables. Reports without an active revision take the live path below,
    byte-identical to before.

    force_live=True ignores the active snapshot and always uses the LIVE period
    rule + bindings. Used by the template preview so editors see their unsaved /
    not-yet-activated changes rendered against current data.
    """
    snap = None if force_live else active_config(db, report_id)
    if snap is not None:
        srule = snap.get("period_rule")
        sbind = snap.get("bindings") or []
        if srule and srule.get("enabled"):
            window = resolve_window(db, dict(srule), ref, tz_name)
            ctx = build_period_context(db, [dict(b) for b in sbind], window, tz_name)
            return ctx, window
        tag_ids = [b["tag_id"] for b in sbind if b.get("tag_id")]
        ctx = build_live_context(db, tag_ids, tz_name)
        return ctx, None

    rule = db.execute(text(
        "SELECT * FROM report_period_rule WHERE report_id = :r AND enabled = true"
    ), {"r": report_id}).mappings().first()

    if rule:
        window = resolve_window(db, dict(rule), ref, tz_name)
        bindings = db.execute(text("""
            SELECT tag_id, position, alias, display_name, unit_id,
                   data_function, quality_rule
            FROM report_tags WHERE report_id = :r ORDER BY position, tag_id
        """), {"r": report_id}).mappings().all()
        ctx = build_period_context(db, [dict(b) for b in bindings], window, tz_name)
        return ctx, window

    ctx = build_live_context(db, list(live_tag_ids), tz_name)
    return ctx, None
