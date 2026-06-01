"""Report block model + compiler — the no-code builder foundation.

A report can be authored as an ordered list of typed BLOCKS (stored as JSON in
report_definitions.template_blocks). A layman drags/configures blocks; this
module compiles that JSON into the Jinja2 HTML that the existing render
pipeline (report_render.py) turns into a PDF. The user never writes Jinja2.

Two-tier model:
  * Layman blocks: header, text, tag_table (with calc columns + conditional
    formatting), kpi_row, chart, columns (multi-up), page_break, spacer.
  * Advanced escape hatch: raw (verbatim HTML/Jinja2) — hidden by default.

CALC COLUMNS (tag_table) use a SAFE mini-expression evaluated with simpleeval
(never Python eval). Formula scope:
  {col}              -> another column's value in the SAME row
  {sum:col} {avg:col} {min:col} {max:col} {count:col} -> table-wide aggregates
Resolution is two-pass: row formulas first (iterated to settle inter-refs),
then aggregates, then aggregate-referencing formulas.

This module has TWO entry points:
  compile_blocks(blocks) -> jinja2 HTML string   (structure/layout only)
  prepare_table_data(...) helpers used at RENDER time to compute calc columns
    against live/aggregated values (the compiler emits calls to these via the
    context, so the heavy data work happens with real numbers, not at compile).

To keep the compiled template simple and safe, calc columns are computed in
PYTHON during context-building (compute_tag_table) and the template just
iterates the finished rows. The compiler therefore emits a placeholder that
binds to a precomputed table in the context: context["tables"][<block_id>].
"""
from __future__ import annotations

import html
import re
from typing import Any

# simpleeval is the sandboxed evaluator (added to requirements with the render deps)
try:
    from simpleeval import SimpleEval
except Exception:  # pragma: no cover - import guard; real error surfaces on use
    SimpleEval = None  # type: ignore


# ---------------------------------------------------------------------------
# Safe formula evaluation (row + aggregate scope)
# ---------------------------------------------------------------------------
AGG_TOKEN = re.compile(r"\{(sum|avg|min|max|count):([^}]+)\}")
ROW_TOKEN = re.compile(r"\{([^}:]+)\}")

_AGG_FUNCS = {
    "sum": sum,
    "avg": lambda xs: (sum(xs) / len(xs)) if xs else 0.0,
    "min": min,
    "max": max,
    "count": len,
}


def _evaluator() -> "SimpleEval":
    if SimpleEval is None:
        raise RuntimeError("simpleeval is not installed; calc columns unavailable.")
    se = SimpleEval()
    # Curated, safe function whitelist (no eval/import/etc).
    se.functions = {"abs": abs, "round": round, "min": min, "max": max, "sum": sum}
    return se


def _has_agg(expr: str) -> bool:
    return bool(AGG_TOKEN.search(expr))


def _eval_row_formula(expr: str, row: dict[str, Any], se: "SimpleEval"):
    names: dict[str, Any] = {}

    def sub(m):
        col = m.group(1)
        if col not in row or row[col] is None:
            raise KeyError(col)
        safe = "c_" + re.sub(r"\W", "_", col)
        names[safe] = row[col]
        return safe

    try:
        py = ROW_TOKEN.sub(sub, expr)
    except KeyError:
        return None  # a referenced column isn't ready/!exists yet
    se.names = names
    try:
        return se.eval(py)
    except Exception:
        return None


def _eval_agg_formula(expr, aggregates, row, se):
    names: dict[str, Any] = {}

    def sub_agg(m):
        fn, col = m.group(1), m.group(2)
        key = f"a_{fn}_{re.sub(r'\\W', '_', col)}"
        names[key] = aggregates.get((fn, col), 0)
        return key

    e2 = AGG_TOKEN.sub(sub_agg, expr)

    def sub_row(m):
        col = m.group(1)
        safe = "c_" + re.sub(r"\W", "_", col)
        names[safe] = row.get(col)
        return safe

    e2 = ROW_TOKEN.sub(sub_row, e2)
    se.names = names
    try:
        return se.eval(e2)
    except Exception:
        return None


def compute_tag_table(columns: list[dict], rows_data: list[dict]) -> dict[str, Any]:
    """Resolve a tag_table's calc columns (row + aggregate) against data rows.

    columns: [{key, kind: 'data'|'formula', formula?, label, fmt?, conditional?}]
    rows_data: [{<data col key>: value, ...}, ...]
    Returns {"rows": [...resolved...], "aggregates": {(fn,col): value}}.
    """
    se = _evaluator()
    row_formula_cols = [c for c in columns if c.get("kind") == "formula" and not _has_agg(c.get("formula", ""))]
    agg_formula_cols = [c for c in columns if c.get("kind") == "formula" and _has_agg(c.get("formula", ""))]

    rows = [dict(r) for r in rows_data]

    # Pass 1: row formulas, iterated so inter-formula references settle.
    for _ in range(len(row_formula_cols) + 1):
        changed = False
        for r in rows:
            for c in row_formula_cols:
                if r.get(c["key"]) is None:
                    v = _eval_row_formula(c["formula"], r, se)
                    if v is not None:
                        r[c["key"]] = v
                        changed = True
        if not changed:
            break

    # Pass 2a: aggregates needed by aggregate formulas.
    needed = set()
    for c in agg_formula_cols:
        for m in AGG_TOKEN.finditer(c["formula"]):
            needed.add((m.group(1), m.group(2)))
    aggregates: dict[tuple, Any] = {}
    for (fn, col) in needed:
        vals = [r[col] for r in rows if r.get(col) is not None and isinstance(r[col], (int, float))]
        aggregates[(fn, col)] = _AGG_FUNCS[fn](vals) if vals else 0

    # Pass 2b: aggregate-referencing formulas, per row.
    for r in rows:
        for c in agg_formula_cols:
            r[c["key"]] = _eval_agg_formula(c["formula"], aggregates, r, se)

    return {"rows": rows, "aggregates": {f"{k[0]}:{k[1]}": v for k, v in aggregates.items()}}


# ---------------------------------------------------------------------------
# Block -> Jinja2 HTML compiler
# ---------------------------------------------------------------------------
def _esc(s: Any) -> str:
    return html.escape(str(s)) if s is not None else ""


def _fmt_expr(value_expr: str, fmt: str | None) -> str:
    """Emit a Jinja2 value expression with optional numeric format."""
    if fmt:
        # fmt like '0.00' -> places=2
        places = 0
        if "." in fmt:
            places = len(fmt.split(".", 1)[1])
        return f"{{{{ {value_expr} | fmt({places}) }}}}"
    return f"{{{{ {value_expr} }}}}"


def _compile_block(b: dict, idx: int) -> str:
    t = b.get("type")
    bid = b.get("id", f"b{idx}")

    if t == "header":
        title = "{{ report.name }}" if b.get("title_source", "report_name") == "report_name" \
            else _esc(b.get("custom_title", ""))
        parts = [f'<div class="rpt-header">']
        if b.get("show_logo", True):
            parts.append('{% if report.logo %}<img class="rpt-logo" src="{{ report.logo }}"/>{% endif %}')
        parts.append(f"<h1>{title}</h1>")
        if b.get("show_generated", True):
            parts.append('<div class="rpt-gen">Generated {{ report.generated_at }} · {{ report.timezone }}</div>')
        parts.append("</div>")
        return "\n".join(parts)

    if t == "text":
        # content may contain {tag:Name} -> compile to a Jinja expression.
        content = b.get("content", "")
        def tagsub(m):
            return f'{{{{ tag("{m.group(1)}").display }}}}'
        content = re.sub(r"\{tag:([^}]+)\}", tagsub, _esc(content))
        return f'<p class="rpt-text">{content}</p>'

    if t == "tag_table":
        # The table's resolved rows are precomputed into context["tables"][bid].
        cols = b.get("columns", [])
        head = "".join(f"<th>{_esc(c.get('label', c.get('key','')))}</th>" for c in cols)
        cells = []
        for c in cols:
            key = c.get("key", "")
            align = "right" if c.get("kind") == "formula" or c.get("numeric") else "left"
            # conditional formatting: {op, value, class} e.g. {">":7,"class":"bad"}
            cond = c.get("conditional")
            cls = ""
            if cond:
                op = cond.get("op", ">"); val = cond.get("value"); klass = cond.get("class", "bad")
                cls = (f' class="{{{{ \'{klass}\' if (row["{key}"] is not none and '
                       f'row["{key}"] {op} {val}) else \'\' }}}}"')
            val_expr = f'row["{key}"]'
            disp = _fmt_expr(val_expr, c.get("fmt")) if c.get("kind") == "formula" or c.get("fmt") \
                else f'{{{{ row["{key}"] if row["{key}"] is not none else "—" }}}}'
            cells.append(f'<td style="text-align:{align}"{cls}>{disp}</td>')
        rowtpl = "".join(cells)
        return (
            f'<table class="rpt-table">'
            f"<thead><tr>{head}</tr></thead><tbody>"
            f'{{% for row in tables["{bid}"].rows %}}<tr>{rowtpl}</tr>{{% endfor %}}'
            f"</tbody></table>"
        )

    if t == "kpi_row":
        items = b.get("items", [])
        cells = []
        for it in items:
            tid = it.get("tag_id")
            label = _esc(it.get("label", ""))
            cells.append(
                f'<div class="rpt-kpi"><div class="kpi-val">'
                f'{{{{ tag(id={tid}).display }}}}</div>'
                f'<div class="kpi-lbl">{label}</div></div>'
            )
        return f'<div class="rpt-kpis">{"".join(cells)}</div>'

    if t == "chart":
        # The SVG is rendered at context-build time into context["charts"][bid].
        return f'<div class="rpt-chart">{{{{ charts["{bid}"] | safe }}}}</div>'

    if t == "columns":
        n = int(b.get("count", 2))
        panels = b.get("panels", [])
        inner = []
        for panel in panels:
            sub = "\n".join(_compile_block(pb, j) for j, pb in enumerate(panel))
            inner.append(f'<div class="rpt-col">{sub}</div>')
        return f'<div class="rpt-cols" style="grid-template-columns:repeat({n},1fr)">{"".join(inner)}</div>'

    if t == "page_break":
        return '<div style="page-break-after:always"></div>'

    if t == "spacer":
        h = int(b.get("height_mm", 6))
        return f'<div style="height:{h}mm"></div>'

    if t == "raw":  # advanced escape hatch — verbatim (trusted: engineer-authored)
        return b.get("html", "")

    return f"<!-- unknown block type: {_esc(t)} -->"


# Base CSS injected once so blocks render consistently.
_BASE_CSS = """
<style>
  body{font-family:sans-serif;font-size:11px;color:#1c2530}
  .rpt-header{display:flex;align-items:center;gap:14px;border-bottom:2px solid #0040A0;padding-bottom:6px;margin-bottom:10px}
  .rpt-logo{height:34px}
  .rpt-header h1{color:#b00;font-size:16px;margin:0}
  .rpt-gen{color:#888;font-size:10px;margin-left:auto}
  .rpt-text{margin:6px 0}
  .rpt-table{width:100%;border-collapse:collapse;margin:8px 0}
  .rpt-table th{background:#0040A0;color:#fff;text-align:left;padding:4px 6px;font-size:10px}
  .rpt-table td{border-bottom:.5px solid #ddd;padding:4px 6px;font-variant-numeric:tabular-nums}
  .rpt-table td.bad{color:#c00;font-weight:700}
  .rpt-table td.warn{color:#b8730a;font-weight:700}
  .rpt-kpis{display:flex;gap:12px;margin:10px 0}
  .rpt-kpi{flex:1;border:1px solid #e2e6ea;border-radius:8px;padding:10px;text-align:center}
  .kpi-val{font-size:20px;font-weight:700;color:#0040A0}
  .kpi-lbl{font-size:10px;color:#6b7785;margin-top:2px}
  .rpt-cols{display:grid;gap:12px;margin:8px 0}
  .rpt-col{border:1px solid #e2e6ea;border-radius:6px;padding:10px}
  .rpt-chart{margin:10px 0;max-width:100%}
  .rpt-chart svg{max-width:100%;height:auto}
</style>
"""


def compile_blocks(blocks: list[dict], page_size: str = "A4", orientation: str = "portrait") -> str:
    """Compile an ordered block list into a Jinja2 HTML template string."""
    page = (f"<style>@page {{ size: {page_size} {orientation}; margin: 14mm; "
            f'@bottom-center {{ content:"Page " counter(page) " of " counter(pages); '
            f"font-size:9px; color:#999 }} }}</style>")
    body = "\n".join(_compile_block(b, i) for i, b in enumerate(blocks or []))
    return page + _BASE_CSS + body
