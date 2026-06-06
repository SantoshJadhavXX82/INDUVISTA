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
# Reusable document bands (Phase C). These render the report's identity header #
# and the sign-off provenance as self-contained HTML (inline styles, so they   #
# look right even when a template brings no CSS). They are exposed to Jinja2   #
# templates as the globals header_block() / signoff_block() — opt-in, so       #
# existing templates are unaffected. Values are escaped; structure is safe.    #
# --------------------------------------------------------------------------- #
def _band_value(v: Any):
    from markupsafe import escape
    return escape("\u2014" if v is None or v == "" else v)


def render_header_band(report: dict[str, Any]) -> str:
    name = _band_value((report or {}).get("name") or "Report")
    ps, pe = (report or {}).get("period_start"), (report or {}).get("period_end")
    period = f"{ps} \u2013 {pe}" if ps else None
    rows = [
        ("Report code", (report or {}).get("report_code")),
        ("Area", (report or {}).get("area")),
        ("Equipment", (report or {}).get("equipment")),
        ("Department", (report or {}).get("owner_dept")),
        ("Period", period),
        ("Generated", (report or {}).get("generated_at")),
    ]
    cells = "".join(
        f"<tr><th style=\"text-align:left;padding:2px 10px 2px 0;color:#5a6b7b;"
        f"font-weight:600;white-space:nowrap;vertical-align:top\">{_band_value(k)}</th>"
        f"<td style=\"padding:2px 0;vertical-align:top\">{_band_value(v)}</td></tr>"
        for k, v in rows
    )
    return (
        "<header class=\"iv-report-header\" style=\"border-bottom:2px solid #0040A0;"
        "margin:0 0 12px;padding:0 0 8px\">"
        f"<h1 style=\"margin:0 0 6px;font-size:18px;color:#0040A0\">{name}</h1>"
        f"<table style=\"border-collapse:collapse;font-size:11px;color:#1c2530\">{cells}</table>"
        "</header>"
    )


def render_signoff_band(signoff: dict[str, Any] | None) -> str:
    base = ("padding:6px 10px;margin:12px 0 0;border-top:1px solid #ccc;"
            "font-size:11px;color:#1c2530")
    if not signoff:
        return (f"<footer class=\"iv-signoff\" style=\"{base};color:#8a6d00;"
                "background:#fff8e1\">Draft \u2014 not yet approved.</footer>")
    rev = _band_value(signoff.get("revision_no"))
    status = _band_value(signoff.get("status"))
    prep_by, prep_at = _band_value(signoff.get("prepared_by")), _band_value(signoff.get("prepared_at"))
    appr_by, appr_at = _band_value(signoff.get("approved_by")), _band_value(signoff.get("approved_at"))
    return (
        f"<footer class=\"iv-signoff\" style=\"{base}\">"
        f"<strong>Revision {rev}</strong> ({status})"
        f" &middot; Prepared by {prep_by} on {prep_at}"
        f" &middot; Approved by {appr_by} on {appr_at}"
        "</footer>"
    )


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

    # Phase C: opt-in document bands. Templates can place {{ header_block() }}
    # and {{ signoff_block() }} to render the standard identity header and
    # sign-off footer; Markup keeps them from being re-escaped.
    from markupsafe import Markup
    _rep = context.get("report") or {}
    _so = context.get("signoff")
    env.globals["header_block"] = lambda: Markup(render_header_band(_rep))
    env.globals["signoff_block"] = lambda: Markup(render_signoff_band(_so))

    # Fmt Phase 2 — quality markers (color + text marker).
    from app.services.report_render import qwrap
    env.globals["qwrap"] = qwrap

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
            "period_start": report.get("period_start"),
            "period_end": report.get("period_end"),
            "report_code": report.get("report_code"),
            "area": report.get("area"),
            "equipment": report.get("equipment"),
            "owner_dept": report.get("owner_dept"),
        },
        "signoff": context.get("signoff"),
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
    for k in ("name", "category", "report_type", "generated_at", "timezone",
              "period_start", "period_end", "report_code", "area", "equipment", "owner_dept"):
        lines.append(f"    <{k}>{esc(r.get(k))}</{k}>")
    lines.append("  </metadata>")
    so = data.get("signoff")
    if so:
        lines.append(f'  <signoff revision_no="{esc(so.get("revision_no"))}"'
                     f' status="{esc(so.get("status"))}">')
        for k in ("prepared_by", "prepared_at", "approved_by", "approved_at"):
            lines.append(f"    <{k}>{esc(so.get(k))}</{k}>")
        lines.append("  </signoff>")
    else:
        lines.append('  <signoff status="draft"/>')
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
