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

    # Convenience for templates: formatted value or text or em-dash.
    @property
    def display(self) -> str:
        if self.value is not None:
            return f"{self.value:g}"
        if self.text is not None:
            return self.text
        return "—"


def build_live_context(db: Session, tag_ids: list[int], tz_name: str) -> dict[str, Any]:
    """Build a render context from the current live values of the given tags."""
    tags_by_key: dict[str, TagCtx] = {}
    ordered: list[TagCtx] = []

    if tag_ids:
        rows = db.execute(text("""
            SELECT t.id, t.name, t.description,
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
