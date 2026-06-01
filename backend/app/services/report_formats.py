"""Report output-format serializers — HTML, JSON, XML (+ PDF stays in report_render).

Architecture (per the Reporting Spec §6.3): compute the report-data structure
ONCE, then serialize to each format. PDF and HTML share the Jinja2 render (HTML
stops before WeasyPrint); JSON and XML serialize the canonical data structure.

  render_html(template_html, context, page_size, orientation) -> str (standalone HTML)
  build_report_data(context)                                  -> canonical dict (§6.3.1)
  to_json(data)                                               -> bytes (application/json)
  to_xml(data)                                                -> bytes (application/xml)

The canonical structure is intentionally generic (urn:induvista:report:1). A
vendor/industry schema can be layered later without touching callers.
"""
from __future__ import annotations
from typing import Any
import json
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape


# --------------------------------------------------------------------------- #
# HTML — same Jinja2 render as PDF, but return the HTML string (no WeasyPrint). #
# --------------------------------------------------------------------------- #
def render_html(template_html: str, context: dict[str, Any],
                page_size: str = "A4", orientation: str = "portrait") -> str:
    from jinja2.sandbox import SandboxedEnvironment
    from jinja2 import StrictUndefined

    env = SandboxedEnvironment(autoescape=True, undefined=StrictUndefined)

    def fmt(value, places=2):
        try:
            return f"{float(value):.{int(places)}f}"
        except (TypeError, ValueError):
            return "\u2014"
    env.filters["fmt"] = fmt

    body = env.from_string(template_html).render(**context)

    # Standalone HTML: ensure a default style block exists if the template
    # didn't bring one, so the file looks right opened directly / emailed.
    if "@page" not in body and "<style" not in body:
        default = (
            f"<style>@page {{ size: {page_size} {orientation}; margin: 14mm; }}"
            f" body {{ font-family: sans-serif; font-size: 12px; color:#1c2530; }}"
            f" table {{ border-collapse: collapse; width: 100%; }}"
            f" th {{ background:#0040A0; color:#fff; padding:5px; text-align:left; }}"
            f" td {{ border-bottom:.5px solid #ddd; padding:5px; }}</style>"
        )
        body = default + body
    # Wrap as a complete document if the template produced only a fragment.
    if "<html" not in body.lower():
        body = (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{_xml_escape(str((context.get('report') or {}).get('name','Report')))}</title>"
                f"</head><body>{body}</body></html>")
    return body


# --------------------------------------------------------------------------- #
# Canonical report-data structure (data only — no visual layout).             #
# --------------------------------------------------------------------------- #
def build_report_data(context: dict[str, Any]) -> dict[str, Any]:
    """Turn the render context into the canonical, serializable report-data dict.
    Pulls report metadata + the resolved tag list (value/unit/quality)."""
    report = dict(context.get("report") or {})
    tags_list = context.get("tags_list") or []

    def tag_row(t: Any) -> dict[str, Any]:
        # t is a TagCtx (dataclass-like) or a dict; read defensively.
        get = (lambda k, d=None: getattr(t, k, d)) if not isinstance(t, dict) else (lambda k, d=None: t.get(k, d))
        return {
            "id": get("id"),
            "name": get("name"),
            "value": get("value"),
            "text": get("text"),
            "unit": get("unit"),
            "quality": get("quality"),
            "quality_good": bool(get("quality_good", False)),
            "age_seconds": get("age_seconds"),
            "description": get("description"),
        }

    return {
        "schema": "urn:induvista:report:1",
        "report": {
            "name": report.get("name"),
            "category": report.get("category"),
            "report_type": report.get("report_type"),
            "generated_at": report.get("generated_at"),
            "timezone": report.get("timezone"),
        },
        "tags": [tag_row(t) for t in tags_list],
        "tag_count": len(tags_list),
    }


def to_json(data: dict[str, Any]) -> bytes:
    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)
    return json.dumps(data, indent=2, default=default).encode("utf-8")


def to_xml(data: dict[str, Any]) -> bytes:
    """Serialize the canonical structure to the generic InduVista XML schema."""
    def esc(v: Any) -> str:
        return _xml_escape("" if v is None else str(v))

    r = data.get("report", {})
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(f'<report schema="{esc(data.get("schema"))}">')
    lines.append("  <metadata>")
    for k in ("name", "category", "report_type", "generated_at", "timezone"):
        lines.append(f"    <{k}>{esc(r.get(k))}</{k}>")
    lines.append("  </metadata>")
    lines.append(f'  <tags count="{esc(data.get("tag_count"))}">')
    for t in data.get("tags", []):
        lines.append('    <tag'
                     f' id="{esc(t.get("id"))}"'
                     f' quality_good="{str(t.get("quality_good", False)).lower()}">')
        for k in ("name", "value", "text", "unit", "quality", "age_seconds", "description"):
            lines.append(f"      <{k}>{esc(t.get(k))}</{k}>")
        lines.append("    </tag>")
    lines.append("  </tags>")
    lines.append("</report>")
    return ("\n".join(lines)).encode("utf-8")
