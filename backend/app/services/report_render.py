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
                   lv.value_double, lv.value_text, lv.st,
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
                    age_seconds=r["age_seconds"],
                    named_set_id=r["named_set_id"],
                    states=states_by_set.get(r["named_set_id"]),
                )
            ordered.append(ctx)
            tags_by_key[ctx.name] = ctx
            if ctx.id is not None:
                tags_by_key[f"id:{ctx.id}"] = ctx

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
