"""Report render pipeline — Jinja2 template + live data context → HTML → PDF.

Pure, reusable rendering layer used by the on-demand render endpoint now and
the trigger engine later. Two responsibilities:

  build_live_context(db, tag_ids)  → a context dict from current live values
  render_report(template_html, context, page_size, orientation) → PDF bytes

TEMPLATE CONTRACT
-----------------
Templates are Jinja2 HTML. The context provides:
  report.{name, generated_at, timezone, ...}   (filled by the caller)
  tags                  → dict keyed by BOTH tag name and "id:<n>", each a
                          TagCtx with: id, name, value, text, unit, quality,
                          quality_good (bool), age_seconds, description.
  tag(name_or_id)       → helper to fetch a TagCtx (returns a blank one if
                          missing, so a template never explodes on a typo).
  now                   → datetime (UTC) at render time.

Templates use autoescaping (HTML-safe) and a sandboxed environment (no access
to Python internals from template expressions).

WeasyPrint turns the rendered HTML into a PDF. @page CSS in the template
controls size/orientation/margins; we also inject a sensible default @page
from the definition's page_size/orientation if the template doesn't set one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

GOOD_ST = 128
SUSPECT_ST = 64

# Fmt Phase 2 — quality markers. Project standard (docs/modbus_quality_reference):
#   st >= 128 -> Good · st == 64 -> Stale · 64 < st < 128 -> Uncertain ·
#   st < 64 -> Bad · no value -> Missing. Color is ALWAYS paired with a text
#   marker (never color alone) so reports stay readable in B/W and for the
#   color-blind (spec FMT-004).
QUALITY_MARK = {
    "bad":       ("\u2717", "Bad / invalid reading"),   # ✗
    "uncertain": ("?",      "Uncertain / suspect reading"),
    "stale":     ("\u27f3", "Stale — not refreshed"),   # ⟳
    "missing":   ("\u2013", "Missing — no data"),        # –
}


def quality_state(ctx) -> str | None:
    """Classify a resolved tag's display quality, or None when good/unknown.

    Accepts a TagCtx (or anything exposing value/text/quality). Returns one of
    'bad' | 'uncertain' | 'stale' | 'missing', or None for good/unknown values
    (which render plain — status-by-exception keeps good reports clean).
    """
    val = getattr(ctx, "value", None)
    txt = getattr(ctx, "text", None)
    st = getattr(ctx, "quality", None)
    if val is None and (txt is None or txt == ""):
        return "missing"
    if st is None:
        return None
    try:
        st = int(st)
    except (TypeError, ValueError):
        return None
    if st < SUSPECT_ST:
        return "bad"
    if st == SUSPECT_ST:
        return "stale"
    if st < GOOD_ST:
        return "uncertain"
    return None  # good


def qwrap(ctx, formatted):
    """Wrap an already-formatted value with a quality color class + text marker.

    Good/unknown values pass through unchanged. Returns Markup so the
    autoescaping render env does not re-escape the wrapper; the inner value is
    escaped defensively (enum labels/text may contain special characters).
    """
    from markupsafe import Markup, escape
    safe = escape("" if formatted is None else str(formatted))
    state = quality_state(ctx)
    if not state:
        return Markup(safe)
    glyph, title = QUALITY_MARK[state]
    return Markup(
        f'<span class="rpt-q-{state}" title="{escape(title)}">{safe}'
        f'<sup class="rpt-qm">{glyph}</sup></span>'
    )


# RS-Lineage — per-value provenance. Origin is the latest_tag_values.source.
_ORIGIN_LABEL = {
    "modbus": "Modbus", "opc_ua": "OPC UA", "mqtt": "MQTT",
    "computed": "Computed", "calc": "Computed",
    "csv": "CSV import", "manual": "Manual entry",
    "estimated": "Estimated",
    "store_forward": "Store-and-forward",
}
_QUALITY_WORD = {None: "Good", "bad": "Bad", "uncertain": "Uncertain",
                 "stale": "Stale", "missing": "Missing"}


def _age_phrase(age: float | None) -> str:
    if age is None:
        return "unknown"
    a = int(age)
    if a < 90:
        return f"{a}s ago"
    if a < 5400:
        return f"{a // 60}m ago"
    if a < 172800:
        return f"{a // 3600}h ago"
    return f"{a // 86400}d ago"


def _input_ids(block_config: dict | None) -> list[int]:
    """Pull referenced input tag ids out of a calc block_config (best-effort).

    Aggregation blocks use inputs:[id,...]; some use [{"tag":id},...]; weighted
    blocks also carry weights:[{"tag":id},...]. Tolerate all shapes, ignore the
    rest so an unfamiliar block never breaks rendering.
    """
    out: list[int] = []
    bc = block_config or {}
    for key in ("inputs", "operands", "terms"):
        raw = bc.get(key)
        if isinstance(raw, list):
            for x in raw:
                if isinstance(x, int):
                    out.append(x)
                elif isinstance(x, dict):
                    t = x.get("tag") or x.get("tag_id") or x.get("id")
                    if isinstance(t, int):
                        out.append(t)
    return out


def _weight_ids(block_config: dict | None) -> list[int]:
    raw = (block_config or {}).get("weights")
    out: list[int] = []
    if isinstance(raw, list):
        for x in raw:
            if isinstance(x, dict):
                t = x.get("tag") or x.get("tag_id") or x.get("id")
                if isinstance(t, int):
                    out.append(t)
    return out


def format_derivation(block_type: str, block_config: dict | None, name_of) -> str:
    """One-line derivation summary, e.g. SUM_OF(A, B) or
    WEIGHTED_AVG(A, B · weighted by W). `name_of(id) -> str` resolves names."""
    names = [name_of(i) for i in _input_ids(block_config)]
    weights = sorted({name_of(w) for w in _weight_ids(block_config)})
    bt = block_type or "?"
    if names and weights:
        return f"{bt}({', '.join(names)} · weighted by {', '.join(weights)})"
    if names:
        return f"{bt}({', '.join(names)})"
    return bt


def provenance_title(ctx, deriv: str | None = None) -> str:
    """Build the human-readable audit string shown on hover (RS-Lineage).

    Lines: source tag (#id) · value+unit · quality (state + st) · captured age ·
    origin. `deriv` (optional) carries a computed/Station derivation summary.
    """
    name = getattr(ctx, "name", "") or "(unknown)"
    tid = getattr(ctx, "id", None)
    val = getattr(ctx, "value", None)
    txt = getattr(ctx, "text", None)
    unit = getattr(ctx, "unit", None)
    st = getattr(ctx, "quality", None)
    src = getattr(ctx, "source", None)
    state = quality_state(ctx)
    if val is not None:
        shown = f"{val:g}" + (f" {unit}" if unit else "")
    elif txt:
        shown = str(txt)
    else:
        shown = "—"
    qual = _QUALITY_WORD.get(state, "Good")
    lines = [
        f"Source: {name}" + (f" (#{tid})" if tid is not None else ""),
        f"Value: {shown}",
        f"Quality: {qual}" + (f" (st {st})" if st is not None else ""),
        f"Captured: {_age_phrase(getattr(ctx, 'age_seconds', None))}",
        f"Origin: {_ORIGIN_LABEL.get(src, src or 'unknown')}",
    ]
    if deriv is None:
        deriv = getattr(ctx, "derivation", None)
    if deriv:
        lines.append(f"Derivation: {deriv}")
    return "\n".join(lines)


def _prov_data_attrs(ctx, deriv=None) -> str:
    """Machine-readable provenance as data-p-* attributes for the click-through
    side panel. Mirrors provenance_title fields; empty values are omitted. The
    PDF/WeasyPrint ignores unknown data-* attributes, so this is render-safe."""
    from markupsafe import escape
    name = getattr(ctx, "name", "") or ""
    tid = getattr(ctx, "id", None)
    val = getattr(ctx, "value", None)
    txt = getattr(ctx, "text", None)
    unit = getattr(ctx, "unit", None)
    st = getattr(ctx, "quality", None)
    src = getattr(ctx, "source", None)
    state = quality_state(ctx)
    if deriv is None:
        deriv = getattr(ctx, "derivation", None)
    shown = (f"{val:g}" if val is not None else (str(txt) if txt else ""))
    pairs = {
        "name": name,
        "id": "" if tid is None else str(tid),
        "value": shown,
        "unit": unit or "",
        "quality": _QUALITY_WORD.get(state, "Good"),
        "st": "" if st is None else str(st),
        "age": _age_phrase(getattr(ctx, "age_seconds", None)),
        "origin": _ORIGIN_LABEL.get(src, src or "unknown"),
        "deriv": deriv or "",
    }
    return "".join(f' data-p-{k}="{escape(v)}"' for k, v in pairs.items() if v != "")


def prov_quality(ctx) -> str:
    """Quality word for the lineage appendix table."""
    return _QUALITY_WORD.get(quality_state(ctx), "Good")


def prov_age(secs) -> str:
    """Captured-age phrase for the lineage appendix table."""
    return _age_phrase(secs)


def prov_origin(src) -> str:
    """Origin label for the lineage appendix table."""
    return _ORIGIN_LABEL.get(src, src or "unknown")


def vwrap(ctx, formatted, lineage=False, quality=True, deriv=None):
    """Unified value wrapper for quality markers + lineage tooltips.

    - quality=True  : non-good values get a color class + text marker.
    - lineage=True  : every value gets a provenance hover tooltip (title).
    Good values with neither feature active pass through unchanged, so output
    is byte-identical when both are off. Returns Markup (autoescape-safe).
    """
    from markupsafe import Markup, escape
    safe = escape("" if formatted is None else str(formatted))
    state = quality_state(ctx) if quality else None
    if not lineage and not state:
        return Markup(safe)
    classes = []
    attrs = ""
    if lineage:
        classes.append("rpt-prov")
        attrs = _prov_data_attrs(ctx, deriv)
    if state:
        classes.append(f"rpt-q-{state}")
    title = provenance_title(ctx, deriv) if lineage else QUALITY_MARK[state][1]
    inner = safe
    if state:
        glyph, _ = QUALITY_MARK[state]
        inner = f'{safe}<sup class="rpt-qm">{glyph}</sup>'
    return Markup(f'<span class="{" ".join(classes)}"{attrs} title="{escape(title)}">{inner}</span>')


@dataclass
class TagCtx:
    id: int | None = None
    name: str = ""
    value: float | None = None          # value_double
    text: str | None = None             # value_text
    unit: str | None = None
    quality: int | None = None          # st byte
    quality_good: bool = False
    age_seconds: float | None = None
    description: str | None = None
    source: str | None = None           # origin: modbus / opc_ua / computed / manual / csv ...
    derivation: str | None = None       # RS-Lineage v1.1: computed/Station block + inputs
    named_set_id: int | None = None
    states: dict | None = None          # {raw_value:int -> display_text:str}

    # Convenience for templates: enum label, else formatted value or text or em-dash.
    @property
    def display(self) -> str:
        if self.states and self.value is not None:
            key = int(round(self.value))
            if key in self.states:
                return self.states[key]
        if self.value is not None:
            return f"{self.value:g}"
        if self.text is not None:
            return self.text
        return "—"


def load_named_set_states(db: Session, named_set_ids) -> dict[int, dict[int, str]]:
    """Return {named_set_id: {raw_value: display_text}} for the given sets.

    Used to translate enumerated tag values (e.g. 1 -> "ONLINE") to text in
    reports, mirroring how the tag list and dashboards display them.
    """
    out: dict[int, dict[int, str]] = {}
    ids = list({n for n in (named_set_ids or []) if n})
    if not ids:
        return out
    rows = db.execute(text(
        "SELECT named_set_id, raw_value, display_text FROM named_set_values "
        "WHERE named_set_id = ANY(:ids)"), {"ids": ids}).mappings().all()
    for r in rows:
        out.setdefault(r["named_set_id"], {})[int(r["raw_value"])] = r["display_text"]
    return out


def build_live_context(db: Session, tag_ids: list[int], tz_name: str) -> dict[str, Any]:
    """Build a render context from the current live values of the given tags."""
    tags_by_key: dict[str, TagCtx] = {}
    ordered: list[TagCtx] = []

    if tag_ids:
        rows = db.execute(text("""
            SELECT t.id, t.name, t.description, t.named_set_id,
                   COALESCE(eu.code, t.engineering_unit) AS unit,
                   lv.value_double, lv.value_text, lv.st, lv.source,
                   CASE WHEN lv.time IS NULL THEN NULL
                        ELSE EXTRACT(EPOCH FROM (NOW() - lv.time))::float END AS age_seconds
            FROM tags t
            LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
            LEFT JOIN latest_tag_values lv ON lv.tag_id = t.id
            WHERE t.id = ANY(:ids) AND t.deleted_at IS NULL
        """), {"ids": tag_ids}).mappings().all()

        by_id = {r["id"]: r for r in rows}
        states_by_set = load_named_set_states(db, [r["named_set_id"] for r in rows])
        # Preserve the caller's requested order.
        for tid in tag_ids:
            r = by_id.get(tid)
            if r is None:
                ctx = TagCtx(id=tid, name=f"tag_{tid}")
            else:
                st = r["st"]
                ctx = TagCtx(
                    id=r["id"], name=r["name"], description=r["description"],
                    value=r["value_double"], text=r["value_text"], unit=r["unit"],
                    quality=st, quality_good=(st is not None and st >= GOOD_ST),
                    age_seconds=r["age_seconds"], source=r["source"],
                    named_set_id=r["named_set_id"],
                    states=states_by_set.get(r["named_set_id"]),
                )
            ordered.append(ctx)
            tags_by_key[ctx.name] = ctx
            if ctx.id is not None:
                tags_by_key[f"id:{ctx.id}"] = ctx

        # RS-Lineage v1.1 — enrich computed/Station values with their derivation
        # (block + input names). A computed_tags row may drive its own tag
        # (ct.id) and/or a separate output tag (ct.output_tag_id); match both.
        present = [c.id for c in ordered if c.id is not None]
        if present:
            try:
                crows = db.execute(text("""
                    SELECT ct.id AS ct_id, ct.output_tag_id, ct.block_type, ct.block_config
                    FROM computed_tags ct
                    WHERE ct.id = ANY(:ids) OR ct.output_tag_id = ANY(:ids)
                """), {"ids": present}).mappings().all()
                if crows:
                    # Resolve all referenced input/weight names in one query.
                    need: set[int] = set()
                    for cr in crows:
                        bc = cr["block_config"]
                        need.update(_input_ids(bc))
                        need.update(_weight_ids(bc))
                    name_map: dict[int, str] = {}
                    if need:
                        nrows = db.execute(
                            text("SELECT id, name FROM tags WHERE id = ANY(:ids)"),
                            {"ids": list(need)},
                        ).mappings().all()
                        name_map = {r["id"]: r["name"] for r in nrows}

                    def _name_of(i: int) -> str:
                        return name_map.get(i, f"#{i}")

                    by_ctid = {c.id: c for c in ordered if c.id is not None}
                    for cr in crows:
                        deriv = format_derivation(cr["block_type"], cr["block_config"], _name_of)
                        for key in (cr["ct_id"], cr["output_tag_id"]):
                            c = by_ctid.get(key)
                            if c is not None and not c.derivation:
                                c.derivation = deriv
            except Exception:
                # Derivation is a nicety; never let it break a render.
                pass

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
        },
    }


def render_report(
    template_html: str,
    context: dict[str, Any],
    page_size: str = "A4",
    orientation: str = "portrait",
) -> bytes:
    """Render a Jinja2 HTML template with `context` and return PDF bytes."""
    # Lazy imports so the rest of the app loads even if WeasyPrint's system
    # libs are momentarily missing (the import error then surfaces only when
    # someone actually renders, with a clear message).
    from jinja2.sandbox import SandboxedEnvironment
    from jinja2 import StrictUndefined
    from weasyprint import HTML

    env = SandboxedEnvironment(autoescape=True, undefined=StrictUndefined)

    # A couple of safe formatting filters templates will want.
    def fmt(value, places=2):
        try:
            return f"{float(value):.{int(places)}f}"
        except (TypeError, ValueError):
            return "—"
    env.filters["fmt"] = fmt

    from markupsafe import Markup as _Markup
    def cssq(value):
        s = "" if value is None else str(value)
        return _Markup('"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"')
    env.filters["cssq"] = cssq
    def localtime(value, tz="UTC", fmt="%d-%b-%Y %H:%M", suffix=False):
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        if value in (None, ""):
            return ""
        try:
            d = _dt.fromisoformat(str(value))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_ZI("UTC"))
            loc = d.astimezone(_ZI(tz or "UTC"))
            out = loc.strftime(fmt)
            return out + (" " + (loc.tzname() or tz) if suffix else "")
        except Exception:
            return str(value)
    env.filters["localtime"] = localtime

    # Fmt Phase 2 — quality markers (color + text marker).
    env.globals["qwrap"] = qwrap
    # RS-Lineage — provenance tooltips + quality, unified.
    env.globals["vwrap"] = vwrap
    # RS-Lineage printed appendix helpers.
    env.globals["prov_quality"] = prov_quality
    env.globals["prov_age"] = prov_age
    env.globals["prov_origin"] = prov_origin

    template = env.from_string(template_html)
    body = template.render(**context)

    # If the template didn't declare an @page, prepend a sensible default so
    # the definition's page_size/orientation are honored.
    if "@page" not in body:
        default_page = (
            f"<style>@page {{ size: {page_size} {orientation}; margin: 14mm; }}"
            f" body {{ font-family: sans-serif; font-size: 11px; }}</style>"
        )
        body = default_page + body

    return HTML(string=body).write_pdf()
