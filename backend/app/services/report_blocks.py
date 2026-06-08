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
from app.services.report_charts import render_chart  # TC-3 chart rendering

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

    # Guarantee every column key exists on every row. A formula that could not
    # be evaluated (e.g. the tag has no value) leaves its key unset above; the
    # compiled template references row["<key>"] under StrictUndefined, so a
    # missing key would crash the whole render. Default such cells to None,
    # which the cell template renders as "—".
    for r in rows:
        for c in columns:
            r.setdefault(c["key"], None)

    return {"rows": rows, "aggregates": {f"{k[0]}:{k[1]}": v for k, v in aggregates.items()}}


def collect_block_tag_ids(blocks) -> list[int]:
    """Return every tag id referenced anywhere in a template's blocks.

    Walks the block tree and picks up tag ids wherever they live: stream-table
    `cells`, KPI `items[].tag_id`, tag_table / chart `tag_id`/`tag`, and nested
    `columns` panels. Order-preserving and de-duplicated. Used to auto-bind
    referenced tags into the report's Data tab (report_tags) so the renderer's
    context includes them. Tag *names* (string refs in text blocks) are ignored
    here — only integer ids, which is what bindings key on. Booleans (e.g.
    quality.enabled) are explicitly excluded since bool is an int subclass.
    """
    out: list[int] = []
    seen: set[int] = set()

    def add(v):
        if isinstance(v, int) and not isinstance(v, bool) and v not in seen:
            seen.add(v)
            out.append(v)

    def walk(node):
        if isinstance(node, dict):
            add(node.get("tag_id"))
            add(node.get("tag"))
            cells = node.get("cells")
            if isinstance(cells, list):
                for c in cells:
                    add(c)
            for k, v in node.items():
                if k in ("tag_id", "tag", "cells"):
                    continue
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(blocks or [])
    return out


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


def _compile_block(b: dict, idx: int, q: bool = False, l: bool = False) -> str:
    t = b.get("type")
    bid = b.get("id", f"b{idx}")

    if t == "header":
        title = "{{ report.name }}" if b.get("title_source", "report_name") == "report_name" \
            else _esc(b.get("custom_title", ""))
        parts = [f'<div class="rpt-header"{_inline_style(b)}>']
        if b.get("show_logo", True):
            parts.append('{% if report.logo is defined and report.logo %}<img class="rpt-logo" src="{{ report.logo }}"/>{% endif %}')
        parts.append(f"<h1>{title}</h1>")
        if b.get("show_generated", True):
            parts.append('<div class="rpt-gen">Generated '
                         '{{ report.generated_at | localtime(report.timezone, "%d-%b-%Y %H:%M", True) }}</div>')
        parts.append("</div>")
        return "\n".join(parts)

    if t == "text":
        # content may contain {tag:Name} -> compile to a Jinja expression.
        content = b.get("content", "")
        def tagsub(m):
            return f'{{{{ tag("{m.group(1)}").display }}}}'
        content = re.sub(r"\{tag:([^}]+)\}", tagsub, _esc(content))
        return f'<p class="rpt-text"{_inline_style(b)}>{content}</p>'

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
            f'{_band_css(bid, b, "table")}'
            f'<table id="{bid}" class="rpt-table">'
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
            unit = _esc(it.get("unit", ""))
            dec = it.get("decimals")
            if dec is None or dec == "":
                inner = "tag(%d).display" % tid
            else:
                inner = "(tag(%d).value | fmt(%d))" % (tid, int(dec))
            val = "{{ %s }}" % (("vwrap(tag(%d), %s, %s, %s)" % (tid, inner, l, q)) if (q or l) else inner)
            unit_html = f'<span class="kpi-unit">{unit}</span>' if unit else ""
            cells.append(
                f'<div class="rpt-kpi"><div class="kpi-val">{val}{unit_html}</div>'
                f'<div class="kpi-lbl">{label}</div></div>'
            )
        return f'<div class="rpt-kpis"{_inline_style(b)}>{"".join(cells)}</div>'

    if t == "chart":
        # The SVG is rendered at context-build time into context["charts"][bid].
        return f'<div class="rpt-chart">{{{{ charts["{bid}"] | safe }}}}</div>'

    if t == "columns":
        n = int(b.get("count", 2))
        panels = b.get("panels", [])
        inner = []
        for panel in panels:
            sub = "\n".join(_compile_block(pb, j, q, l) for j, pb in enumerate(panel))
            inner.append(f'<div class="rpt-col">{sub}</div>')
        return f'<div class="rpt-cols" style="grid-template-columns:repeat({n},1fr)">{"".join(inner)}</div>'

    if t == "stream_table":
        # No-code multi-stream comparison table: rows = measurements, columns =
        # devices/streams. Each cell carries a resolved tag_id (or null → em-dash);
        # decimals null → use the tag's own display. Built/edited visually in the UI.
        cols = b.get("columns", []) or []
        secs = b.get("sections", []) or []
        ncol = len(cols)
        span = 2 + ncol
        band_rows = bool(b.get("band_rows"))
        band_cols = bool(b.get("band_cols"))
        out = []
        if b.get("title"):
            out.append(f'<div class="rpt-text" style="font-weight:700">{_esc(b.get("title"))}</div>')
        out.append(_band_css(bid, b, "stream"))
        out.append('<table id="%s" class="rpt-stream"%s>' % (bid, _inline_style(b)))
        out.append('<tr class="rpt-stream-head"><td></td><td></td>'
                   + "".join(f'<td>{_esc(c.get("label", ""))}</td>' for c in cols)
                   + "</tr>")
        for s in secs:
            name = s.get("name", "")
            if name:
                out.append('<tr><td colspan="%d" class="rpt-stream-sect"%s>%s</td></tr>'
                           % (span, _inline_style(s), _esc(name)))
            dr = 0  # reset zebra parity per section: first data row unshaded
            for r in s.get("rows", []):
                dec = r.get("decimals")
                cells = r.get("cells", []) or []
                tds = []
                for i in range(ncol):
                    cid = cells[i] if i < len(cells) else None
                    numcls = "num cband" if (band_cols and i % 2 == 1) else "num"
                    if cid in (None, ""):
                        tds.append('<td class="%s">&mdash;</td>' % numcls)
                    elif dec is None or dec == "":
                        inner = "tag(%d).display" % int(cid)
                        expr = ("vwrap(tag(%d), %s, %s, %s)" % (int(cid), inner, l, q)) if (q or l) else inner
                        tds.append('<td class="%s">{{ %s }}</td>' % (numcls, expr))
                    else:
                        inner = "(tag(%d).value | fmt(%d))" % (int(cid), int(dec))
                        expr = ("vwrap(tag(%d), %s, %s, %s)" % (int(cid), inner, l, q)) if (q or l) else inner
                        tds.append('<td class="%s">{{ %s }}</td>' % (numcls, expr))
                rowcls = ' class="rpt-band"' if (band_rows and dr % 2 == 1) else ""
                out.append('<tr%s><td>%s</td><td class="rpt-unit">%s</td>%s</tr>'
                           % (rowcls, _esc(r.get("label", "")), _esc(r.get("unit", "")), "".join(tds)))
                dr += 1
        out.append("</table>")
        return "\n".join(out)

    if t in ("page_header", "page_footer", "report_style"):
        return ""  # handled in compile_blocks (margin boxes / theme / time basis)

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
  .rpt-table{width:100%;border-collapse:collapse;margin:8px 0;table-layout:fixed}
  .rpt-table th{background:#0040A0;color:#fff;text-align:left;padding:4px 6px;font-size:10px;word-break:break-word}
  .rpt-table td{border-bottom:.5px solid #ddd;padding:4px 6px;font-variant-numeric:tabular-nums;word-break:break-word}
  .rpt-table td.bad{color:#c00;font-weight:700}
  .rpt-table td.warn{color:#b8730a;font-weight:700}
  .rpt-kpis{display:flex;gap:12px;margin:10px 0}
  .rpt-kpi{flex:1;border:1px solid #e2e6ea;border-radius:8px;padding:10px;text-align:center}
  .kpi-val{font-size:20px;font-weight:700;color:#0040A0}
  .kpi-unit{font-size:11px;font-weight:600;color:#6b7785;margin-left:3px}
  .kpi-lbl{font-size:10px;color:#6b7785;margin-top:2px}
  .rpt-cols{display:grid;gap:12px;margin:8px 0}
  .rpt-col{border:1px solid #e2e6ea;border-radius:6px;padding:10px}
  .rpt-chart{margin:10px 0;max-width:100%}  .rpt-chart svg{max-width:100%;height:auto}
  .rpt-stream{width:100%;border-collapse:collapse;font-size:12px;margin:6px 0}
  .rpt-stream td{padding:2px 6px;vertical-align:top}
  .rpt-stream .rpt-stream-head td{text-decoration:underline;text-align:right}
  .rpt-stream .rpt-stream-sect{text-decoration:underline;padding-top:10px}
  .rpt-stream .rpt-unit{color:#555}
  /* Fmt Phase 2 — quality markers (color + text marker, never color alone) */
  .rpt-q-bad{color:#DC2626;font-weight:700}
  .rpt-q-uncertain{color:#D97706;font-weight:700}
  .rpt-q-stale{color:#6B7280;font-weight:600}
  .rpt-q-missing{color:#9CA3AF;font-weight:600}
  .rpt-q-held{color:#0891B2;font-weight:600}
  .rpt-q-substituted{color:#7C3AED;font-weight:700}
  .rpt-qm{font-size:.85em;font-weight:700;margin-left:1px;font-variant-numeric:normal}
  /* RS-Lineage — values carry a provenance tooltip (hover); subtle dotted hint */
  .rpt-prov{cursor:help;border-bottom:1px dotted rgba(100,116,139,.45)}
  .rpt-stream .num{text-align:right;font-variant-numeric:tabular-nums}
</style>
"""


# ---------------------------------------------------------------------------
# Page header / footer (Phase A).
# A `page_header` / `page_footer` block has three slot strings: left, center,
# right. Each slot is plain text that may contain tokens. These are lifted out
# of the body flow and compiled into WeasyPrint @page margin boxes so they
# repeat on every printed page. Dynamic per-page tokens ({page}/{pages}) become
# CSS counters; static-per-render tokens ({report_title}/{generated_at}/
# {timezone}) become Jinja expressions (rendered before WeasyPrint, escaped for
# CSS via the `cssq` filter). Unknown {tokens} are treated as literal text.
# ---------------------------------------------------------------------------
_SLOT_TOKENS = {
    "page": "counter(page)",
    "pages": "counter(pages)",
    "page_of": '"Page " counter(page) " of " counter(pages)',
    "report_title": '{{ (report.name if report.name is defined else "") | cssq }}',
    "generated_at": '{{ (report.generated_at if report.generated_at is defined else "") | cssq }}',
    "timezone": '{{ (report.timezone if report.timezone is defined else "") | cssq }}',
}
_SLOT_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")
_HDR_POS = {"left": "@top-left", "center": "@top-center", "right": "@top-right"}
_FTR_POS = {"left": "@bottom-left", "center": "@bottom-center", "right": "@bottom-right"}


def _css_literal(text: str) -> str:
    """Quote arbitrary text as a CSS string value (compile-time literals)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _gen_at_expr(time: dict | None) -> str:
    """Jinja expr for a localized, CSS-escaped generated timestamp per time basis."""
    time = time or {}
    basis = time.get("basis") or "system"
    fmt = time.get("format") or "%d-%b-%Y %H:%M"
    suffix = "True" if time.get("show_suffix") else "False"
    if basis == "utc":
        tzarg = '"UTC"'
    elif basis in ("system", ""):
        tzarg = "report.timezone"
    else:
        tzarg = '"%s"' % basis
    return ('{{ (report.generated_at if report.generated_at is defined else "") '
            '| localtime(%s, "%s", %s) | cssq }}' % (tzarg, fmt, suffix))


def _compile_slot(slot: str | None, time: dict | None = None) -> str | None:
    """Turn one slot string into a CSS `content` value, or None if empty."""
    slot = (slot or "").strip()
    if not slot:
        return None
    tokmap = dict(_SLOT_TOKENS)
    tokmap["generated_at"] = _gen_at_expr(time)
    pieces: list[str] = []
    pos = 0
    for m in _SLOT_TOKEN_RE.finditer(slot):
        if m.start() > pos:
            pieces.append(_css_literal(slot[pos:m.start()]))
        tok = m.group(1)
        pieces.append(tokmap.get(tok, _css_literal(m.group(0))))
        pos = m.end()
    if pos < len(slot):
        pieces.append(_css_literal(slot[pos:]))
    return " ".join(p for p in pieces if p) or None


def _margin_boxes(block: dict | None, posmap: dict[str, str], time: dict | None = None) -> list[str]:
    out: list[str] = []
    for slot, pos in posmap.items():
        content = _compile_slot((block or {}).get(slot), time) if block else None
        if content:
            out.append("%s { content: %s; font-size:9px; color:#666 }" % (pos, content))
    return out


def _page_style(page_size: str, orientation: str,
                header: dict | None, footer: dict | None, time: dict | None = None) -> str:
    boxes: list[str] = []
    boxes += _margin_boxes(header, _HDR_POS, time)
    if footer:
        boxes += _margin_boxes(footer, _FTR_POS, time)
    else:
        # Backward-compatible default: reports without a footer block still get
        # an automatic centred page counter.
        boxes.append('@bottom-center { content:"Page " counter(page) " of " '
                     'counter(pages); font-size:9px; color:#999 }')
    inner = " ".join(boxes)
    return ("<style>@page { size: %s %s; margin: 16mm 14mm; %s }</style>"
            % (page_size, orientation, inner))


# --- Per-object + theme styling (Font/Color spec §3-§7, §11, §15) ------------
# Font choices map to fallback stacks: the exact font renders when installed in
# the PDF (WeasyPrint) container, otherwise a close substitute is used so the
# report still reads correctly (serif/mono/sans intent preserved).
_FONT_STACKS = {
    "Segoe UI": "'Segoe UI','Noto Sans','DejaVu Sans',sans-serif",
    "Arial": "Arial,'Liberation Sans','DejaVu Sans',sans-serif",
    "Calibri": "Calibri,Carlito,'DejaVu Sans',sans-serif",
    "Verdana": "Verdana,'DejaVu Sans',sans-serif",
    "Tahoma": "Tahoma,'DejaVu Sans',sans-serif",
    "Roboto": "Roboto,'Noto Sans','DejaVu Sans',sans-serif",
    "Noto Sans": "'Noto Sans','DejaVu Sans',sans-serif",
    "Times New Roman": "'Times New Roman','Liberation Serif','DejaVu Serif',serif",
    "Courier New": "'Courier New','Liberation Mono','DejaVu Sans Mono',monospace",
}


def _font_stack(family: str) -> str:
    return _FONT_STACKS.get(family, family)


def _band_css(bid: str, b: dict, kind: str) -> str:
    """Scoped banding CSS for one table.

    kind="stream": flat <tr> table — band via the .rpt-band (data rows) and
    .cband (data cols) classes added at compile time. We must NOT use
    tbody/nth-child here: the HTML parser wraps the bare <tr>s in an implicit
    <tbody>, so nth-child would shade section headings and the header row too.

    kind="table": tag_table has an explicit thead/tbody, so nth-child(even)
    cleanly targets only the data rows/columns.
    """
    rules: list[str] = []
    rc = b.get("band_row_color") or "#F3F6FA"
    cc = b.get("band_col_color") or "#EEF4FB"
    if kind == "stream":
        if b.get("band_rows"):
            rules.append("#%s tr.rpt-band>td{background:%s}" % (bid, rc))
        if b.get("band_cols"):
            rules.append("#%s td.cband{background:%s}" % (bid, cc))
    else:  # tag_table
        if b.get("band_rows"):
            rules.append("#%s tbody tr:nth-child(even) td{background:%s}" % (bid, rc))
        if b.get("band_cols"):
            rules.append("#%s thead th:nth-child(even),#%s tbody td:nth-child(even){background:%s}" % (bid, bid, cc))
    return ("<style>%s</style>" % "".join(rules).replace('"', "")) if rules else ""


_FONT_WEIGHTS = {"light": "300", "regular": "400", "normal": "400",
                 "medium": "500", "semibold": "600", "bold": "700"}
_CASE_MAP = {"uppercase": "uppercase", "lowercase": "lowercase",
             "capitalize": "capitalize", "title case": "capitalize",
             "title_case": "capitalize", "sentence case": "none",
             "as_typed": "none", "none": "none"}


def _inline_style(b: dict) -> str:
    """Build an inline ` style="..."` from a block's §15 style object (subset)."""
    s = b.get("style")
    if not isinstance(s, dict) or not s:
        return ""
    css: list[str] = []
    f = s.get("font") or {}
    if f.get("family"):
        css.append("font-family:%s" % _font_stack(f["family"]))
    if f.get("size_pt"):
        css.append("font-size:%spt" % f["size_pt"])
    if f.get("weight"):
        css.append("font-weight:%s" % _FONT_WEIGHTS.get(str(f["weight"]).lower(), f["weight"]))
    if f.get("italic"):
        css.append("font-style:italic")
    if f.get("underline"):
        css.append("text-decoration:underline")
    if f.get("case") and _CASE_MAP.get(str(f["case"]), "none") != "none":
        css.append("text-transform:%s" % _CASE_MAP[str(f["case"])])
    tx = s.get("text") or {}
    if tx.get("color"):
        css.append("color:%s" % tx["color"])
    if tx.get("horizontal_align"):
        css.append("text-align:%s" % tx["horizontal_align"])
    if tx.get("line_height"):
        css.append("line-height:%s" % tx["line_height"])
    bg = s.get("background") or {}
    if bg.get("color") and bg.get("type", "solid") != "none":
        css.append("background:%s" % bg["color"])
    bd = s.get("border") or {}
    if bd.get("enabled") and bd.get("color"):
        css.append("border:%spt %s %s" % (bd.get("width_pt", 0.5), bd.get("style", "solid"), bd["color"]))
    pad = s.get("padding_mm") or {}
    if pad:
        css.append("padding:%smm %smm %smm %smm" % (
            pad.get("top", 0), pad.get("right", 0), pad.get("bottom", 0), pad.get("left", 0)))
    if not css:
        return ""
    return ' style="%s"' % ";".join(css).replace('"', "")


def _theme_css(rs: dict | None) -> str:
    """Report-level theme → CSS overriding the base styles."""
    th = (rs or {}).get("theme") or {}
    if not isinstance(th, dict) or not th:
        return ""
    rules: list[str] = []
    body: list[str] = []
    if th.get("font_family"):
        body.append("font-family:%s" % _font_stack(th["font_family"]))
    if th.get("font_size_pt"):
        body.append("font-size:%spt" % th["font_size_pt"])
    if th.get("text_color"):
        body.append("color:%s" % th["text_color"])
    if body:
        rules.append("body{%s}" % ";".join(body))
    if th.get("heading_color"):
        rules.append(".rpt-header h1{color:%s}" % th["heading_color"])
    # Section headings inside stream tables. Colour falls back to heading_color;
    # background/size/caps are optional section-specific controls.
    sect: list[str] = []
    sc = th.get("section_color") or th.get("heading_color")
    if sc:
        sect.append("color:%s" % sc)
    if th.get("section_bg"):
        sect.append("background:%s" % th["section_bg"])
        sect.append("padding:3px 6px")
    if th.get("section_size_pt"):
        sect.append("font-size:%spt" % th["section_size_pt"])
    if th.get("section_caps"):
        sect.append("text-transform:uppercase")
    if sect:
        rules.append(".rpt-stream .rpt-stream-sect{%s}" % ";".join(sect))
    thb: list[str] = []
    if th.get("table_header_bg"):
        thb.append("background:%s" % th["table_header_bg"])
    if th.get("table_header_fg"):
        thb.append("color:%s" % th["table_header_fg"])
    if thb:
        rules.append(".rpt-table th{%s}" % ";".join(thb))
    if th.get("alt_row"):
        rules.append(".rpt-table tbody tr:nth-child(even) td{background:%s}" % th["alt_row"])
    css = "".join(rules).replace('"', "")
    return ("<style>%s</style>" % css) if css else ""


def _merge_style(default_style: dict | None, block: dict | None) -> dict | None:
    """Cascade the global default style under a per-report report_style block.

    A per-report value overrides the global default *only when it is actually
    set* — empty strings / None are treated as "inherit", so clearing a field
    on a report falls back to the global default rather than blanking it.
    Booleans/0 are kept (so unticking e.g. UPPERCASE is a real override).
    theme/time/page each merge key-by-key.
    """
    ds = default_style or {}
    b = block or {}
    if not ds and not b:
        return None

    def _set(d):
        return {k: v for k, v in (d or {}).items() if v not in (None, "")}

    out: dict = {"type": "report_style"}
    for k in ("theme", "time", "page", "quality", "lineage"):
        merged = {**(ds.get(k) or {}), **_set(b.get(k))}
        if merged:
            out[k] = merged
    return out


# RS-Lineage printed appendix (opt-in). Renders a visible "Data Lineage" table
# from the resolved context (tags_list), present on both live and period paths.
# Uses prov_* globals registered in the render envs.
_LINEAGE_APPENDIX = (
    "<style>"
    ".rpt-lineage-appendix{margin-top:16px;page-break-inside:auto}"
    ".rpt-lin-h{font-size:11px;font-weight:700;margin:8px 0 4px;border-top:1px solid #94a3b8;padding-top:6px}"
    ".rpt-lin-cap{font-size:8.5px;color:#64748b;margin:0 0 4px}"
    ".rpt-lin-tbl{width:100%;border-collapse:collapse;font-size:9px}"
    ".rpt-lin-tbl th,.rpt-lin-tbl td{border:0.5px solid #cbd5e1;padding:2px 5px;text-align:left;vertical-align:top}"
    ".rpt-lin-tbl thead th{background:#f1f5f9;font-weight:600}"
    ".rpt-lin-tbl td.num{text-align:right;font-variant-numeric:tabular-nums}"
    "</style>"
    "{% if tags_list %}"
    '<div class="rpt-lineage-appendix">'
    '<div class="rpt-lin-h">Data Lineage</div>'
    '<p class="rpt-lin-cap">Provenance of every value used in this report \u2014 '
    "source tag, current value, quality, capture age, origin and derivation.</p>"
    '<table class="rpt-lin-tbl"><thead><tr>'
    "<th>Tag</th><th>Value</th><th>Quality</th><th>Captured</th><th>Origin</th><th>Derivation</th>"
    "</tr></thead><tbody>"
    "{% for t in tags_list %}<tr>"
    "<td>{{ t.name }}{% if t.id %} (#{{ t.id }}){% endif %}</td>"
    '<td class="num">{{ t.display }}{% if t.unit %} {{ t.unit }}{% endif %}</td>'
    "<td>{{ prov_quality(t) }}{% if t.quality is not none %} (st {{ t.quality }}){% endif %}</td>"
    "<td>{{ prov_age(t.age_seconds) }}</td>"
    "<td>{{ prov_origin(t.source) }}</td>"
    '<td>{{ t.derivation or "\u2014" }}</td>'
    "</tr>{% endfor %}"
    "</tbody></table></div>"
    "{% endif %}"
)


def compile_blocks(blocks: list[dict], page_size: str = "A4", orientation: str = "portrait",
                   default_style: dict | None = None) -> str:
    """Compile an ordered block list into a Jinja2 HTML template string.

    page_header / page_footer / report_style blocks are pulled out of the flow:
    header/footer become @page margin boxes; report_style sets the time basis
    (for {generated_at}) and the report theme CSS. All other blocks compile
    into the body in order, each honoring its own optional `style` object.
    """
    blocks = blocks or []
    header = next((b for b in blocks if b.get("type") == "page_header"), None)
    footer = next((b for b in blocks if b.get("type") == "page_footer"), None)
    style = next((b for b in blocks if b.get("type") == "report_style"), None)
    if default_style:
        style = _merge_style(default_style, style)
    flow = [b for b in blocks if b.get("type") not in ("page_header", "page_footer", "report_style")]
    # Page size/orientation come from the report definition (Settings tab), not
    # the theme cascade — keeps a single source of truth for page geometry.
    page = _page_style(page_size, orientation, header, footer, (style or {}).get("time"))
    theme = _theme_css(style)
    # Fmt Phase 2 — quality markers. Enabled by default; only NON-good values are
    # marked, so all-good reports render identically. A report (or the global
    # default) can disable via report_style.quality.enabled = false.
    q_cfg = (style or {}).get("quality") or {}
    q_enabled = q_cfg.get("enabled", True)
    # RS-Lineage — provenance hover tooltips on every value. Default on; disable
    # via report_style.lineage.enabled = false.
    l_cfg = (style or {}).get("lineage") or {}
    l_enabled = l_cfg.get("enabled", True)
    body = "\n".join(_compile_block(b, i, q_enabled, l_enabled) for i, b in enumerate(flow))
    # RS-Lineage printed appendix (opt-in) — a visible "Data Lineage" table so
    # provenance survives in the PDF, where hover/click do not exist.
    if l_cfg.get("appendix", False):
        body += _LINEAGE_APPENDIX
    return page + _BASE_CSS + theme + body


# ---------------------------------------------------------------------------
# Render-time context for compiled blocks.
# compile_blocks emits references to tables["<bid>"].rows (tag_table) and
# charts["<bid>"] (chart). This precomputes those from the report's already
# resolved tag context (tags_list), so the compiled Jinja template just
# iterates finished rows. tag_table rows default to one row per bound tag with
# the standard keys name/value/unit/quality/display; the block's columns pick
# from those (plus formula columns resolved by compute_tag_table). Charts are a
# placeholder until TC-3 wires real SVG. Block ids mirror the compiler's
# fallback (b.get("id", f"b{index}")) so the keys line up.
# ---------------------------------------------------------------------------
_CHART_PLACEHOLDER = ('<div style="color:#8a93a0;font-size:10px;border:1px dashed #cfd6dd;'
                      'padding:10px;text-align:center">[chart rendering pending]</div>')


def build_block_context(blocks: list[dict], tags_list: list, db=None, window=None) -> dict[str, Any]:
    """Return {'tables': {...}, 'charts': {...}} for a compiled block list."""
    def _q(t) -> str:
        return "GOOD" if getattr(t, "quality_good", False) else "BAD"

    rows_data = [{
        "name": getattr(t, "name", None),
        "value": getattr(t, "value", None),
        "unit": getattr(t, "unit", None),
        "display": (t.display if hasattr(t, "display") else getattr(t, "value", None)),
        "quality": _q(t),
    } for t in (tags_list or [])]

    tables: dict[str, Any] = {}
    charts: dict[str, str] = {}

    def walk(bs: list[dict]) -> None:
        for i, b in enumerate(bs or []):
            bid = b.get("id", f"b{i}")
            t = b.get("type")
            if t == "tag_table":
                cols = b.get("columns", [])
                try:
                    tables[bid] = compute_tag_table(cols, rows_data)
                except Exception:
                    safe_rows = []
                    for r in rows_data:
                        rr = dict(r)
                        for c in cols:
                            rr.setdefault(c.get("key"), None)
                        safe_rows.append(rr)
                    tables[bid] = {"rows": safe_rows, "aggregates": {}}
            elif t == "chart":
                charts[bid] = render_chart(b, tags_list, db, window)
            elif t == "columns":
                for panel in b.get("panels", []):
                    walk(panel)

    walk(blocks)
    return {"tables": tables, "charts": charts}
