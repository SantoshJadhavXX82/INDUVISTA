"""Pure-Python SVG chart rendering for report 'chart' blocks (Phase TC-3c).

Renders line / area / bar / pie charts as inline SVG so they embed directly in
the WeasyPrint PDF/HTML pipeline - no JavaScript, no external chart dependency,
and no fragile SVG filters/gradients (kept to primitives WeasyPrint renders
reliably: rect, line, path, polyline, circle, text).

Data sources:
  * bar / pie   -> each bound tag's aggregated period value (from tags_list)
  * line / area -> each bound tag's down-sampled time-series over the report
                   window (queried from tag_values; needs db). When the report
                   has no fixed period, falls back to the last `window_minutes`.

Per-chart options read from the block dict:
  chart_type      "line" | "area" | "bar" | "pie"
  title           str
  tag_ids         list[int] | None      (None/absent -> all bound tags)
  window_minutes  int                   (line/area fallback when no period)
  time_format     "system" | "utc"      (x-axis time zone; system = app tz)
  show_legend     bool
  show_values     bool
  show_grid       bool
  show_border     bool

render_chart() is defensive: any failure yields a small inline note rather than
breaking the whole report render.
"""
from __future__ import annotations

import datetime as _dt
import html
import math
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

try:
    from app.config import settings
    _APP_TZ = settings.app_timezone
except Exception:  # pragma: no cover - keep importable in any context
    _APP_TZ = "UTC"

# Modern, print-safe palette (cycled for many series).
_PAL = ["#3b82f6", "#ec4899", "#22c55e", "#f59e0b", "#8b5cf6",
        "#06b6d4", "#ef4444", "#14b8a6", "#eab308", "#64748b"]

_W, _H = 700, 330                       # SVG viewBox; CSS scales to container

# Theme tokens
_C_BORDER = "#e3e8ef"
_C_CARD = "#ffffff"
_C_PANEL = "#f8fafc"
_C_GRID = "#eef2f7"
_C_AXIS = "#cbd5e1"
_C_TITLE = "#1f2937"
_C_LABEL = "#94a3b8"
_C_VALUE = "#475569"
_FONT = "Segoe UI,Helvetica,Arial,sans-serif"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _fmt(v: float | None) -> str:
    if v is None:
        return ""
    a = abs(v)
    if a != 0 and (a < 0.01 or a >= 100000):
        return f"{v:.2e}"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


def _shell(body: str, show_border: bool = True, h: int = _H) -> str:
    frame = ""
    if show_border:
        frame = (f'<rect x="0.75" y="0.75" width="{_W - 1.5}" height="{h - 1.5}" '
                 f'rx="12" ry="12" fill="{_C_CARD}" stroke="{_C_BORDER}" '
                 f'stroke-width="1.5"/>')
    return (f'<svg viewBox="0 0 {_W} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="{_FONT}">{frame}{body}</svg>')


def _note(msg: str, show_border: bool = True) -> str:
    h = 90
    body = (f'<text x="{_W // 2}" y="{h // 2 + 4}" text-anchor="middle" '
            f'font-size="12.5" fill="#8a93a0">{_esc(msg)}</text>')
    return _shell(body, show_border, h)


def _title(title: str) -> str:
    if not title:
        return ""
    return (f'<text x="{_W / 2:.0f}" y="26" text-anchor="middle" font-size="14.5" '
            f'font-weight="600" fill="{_C_TITLE}">{_esc(title)}</text>'
            f'<line x1="20" y1="38" x2="{_W - 20}" y2="38" stroke="{_C_GRID}" '
            f'stroke-width="1"/>')


def _bounds(lo: float, hi: float):
    if lo == hi:
        if lo == 0:
            return -1.0, 1.0
        pad = abs(lo) * 0.1
        lo, hi = lo - pad, hi + pad
    if lo > 0:
        lo = 0.0
    if hi < 0:
        hi = 0.0
    span = hi - lo
    return lo, hi + span * 0.08          # headroom for value labels


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        return [lo]
    step = (hi - lo) / n
    return [lo + step * i for i in range(n + 1)]


def _percentages(values: list[float]) -> list[int]:
    """Integer percentages that always sum to exactly 100 (largest-remainder
    method) - avoids the naive per-slice rounding that can total 99 or 101."""
    total = sum(values)
    if total <= 0:
        return [0] * len(values)
    raw = [v / total * 100 for v in values]
    floors = [int(math.floor(x)) for x in raw]
    rem = 100 - sum(floors)
    order = sorted(range(len(values)), key=lambda i: raw[i] - floors[i], reverse=True)
    for k in range(max(rem, 0)):
        if order:
            floors[order[k % len(order)]] += 1
    return floors


def _panel(px0, py0, px1, py1) -> str:
    return (f'<rect x="{px0}" y="{py0}" width="{px1 - px0}" height="{py1 - py0}" '
            f'rx="7" fill="{_C_PANEL}"/>')


def _axes(lo, hi, px0, py0, px1, py1, show_grid=True) -> str:
    out = []
    for tk in _ticks(lo, hi):
        y = py1 - (tk - lo) / (hi - lo) * (py1 - py0) if hi > lo else py1
        if show_grid:
            out.append(f'<line x1="{px0}" y1="{y:.1f}" x2="{px1}" y2="{y:.1f}" '
                       f'stroke="{_C_GRID}" stroke-width="1" stroke-dasharray="3 4"/>')
        out.append(f'<text x="{px0 - 8}" y="{y + 3:.1f}" text-anchor="end" '
                   f'font-size="9.5" fill="{_C_LABEL}">{_esc(_fmt(tk))}</text>')
    out.append(f'<line x1="{px0}" y1="{py0}" x2="{px0}" y2="{py1}" '
               f'stroke="{_C_AXIS}" stroke-width="1"/>')
    out.append(f'<line x1="{px0}" y1="{py1}" x2="{px1}" y2="{py1}" '
               f'stroke="{_C_AXIS}" stroke-width="1"/>')
    return "".join(out)


def _legend_row(names, y) -> str:
    if not names:
        return ""
    measured = [(nm, 11 + min(len(nm), 20) * 6.0 + 16) for nm in names]
    total = sum(w for _, w in measured)
    x = max(16, (_W - total) / 2)
    out = []
    for i, (nm, w) in enumerate(measured):
        c = _PAL[i % len(_PAL)]
        label = nm if len(nm) <= 20 else nm[:19] + "\u2026"
        out.append(
            f'<rect x="{x:.0f}" y="{y - 9}" width="11" height="11" rx="3" fill="{c}"/>'
            f'<text x="{x + 16:.0f}" y="{y}" font-size="10.5" fill="{_C_VALUE}">'
            f'{_esc(label)}</text>')
        x += w
    return "".join(out)


# --------------------------------------------------------------------------
# data fetch (line / area)
# --------------------------------------------------------------------------
def _fetch_series(db, tag_ids, start, end, buckets: int = 140) -> dict[int, list]:
    out: dict[int, list] = {}
    try:
        span = (end - start).total_seconds()
    except Exception:
        return out
    width = max(span / max(buckets, 1), 1.0) if span > 0 else 1.0
    for tid in tag_ids:
        try:
            rows = db.execute(text("""
                SELECT min(time) AS t, avg(value_double) AS v
                FROM tag_values
                WHERE tag_id = :t AND time >= :s AND time < :e
                  AND value_double IS NOT NULL
                GROUP BY floor(extract(epoch from (time - :s)) / :w)
                ORDER BY 1
            """), {"t": tid, "s": start, "e": end, "w": width}).fetchall()
            out[tid] = [(r[0], float(r[1])) for r in rows if r[1] is not None]
        except Exception:
            out[tid] = []
    return out


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------
def _svg_bar(pairs, title, show_values, show_grid, show_border) -> str:
    px0, px1 = 58, _W - 22
    py0 = 52 if title else 24
    py1 = _H - 46
    vals = [v for _, v in pairs]
    lo, hi = _bounds(min(vals + [0.0]), max(vals + [0.0]))
    zero_y = py1 - (0 - lo) / (hi - lo) * (py1 - py0) if hi > lo else py1
    n = len(pairs)
    slot = (px1 - px0) / max(n, 1)
    bw = min(slot * 0.6, 64)
    body = [_title(title), _panel(px0, py0, px1, py1),
            _axes(lo, hi, px0, py0, px1, py1, show_grid)]
    for i, (label, v) in enumerate(pairs):
        cx = px0 + slot * i + slot / 2
        y = py1 - (v - lo) / (hi - lo) * (py1 - py0) if hi > lo else py1
        top, h = (min(y, zero_y), abs(y - zero_y))
        c = _PAL[i % len(_PAL)]
        body.append(f'<rect x="{cx - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                    f'height="{max(h, 0.8):.1f}" rx="4" fill="{c}" fill-opacity="0.92"/>')
        lab = label if len(label) <= 12 else label[:11] + "\u2026"
        body.append(f'<text x="{cx:.1f}" y="{py1 + 15:.1f}" text-anchor="middle" '
                    f'font-size="9.5" fill="{_C_VALUE}">{_esc(lab)}</text>')
        if show_values:
            body.append(f'<text x="{cx:.1f}" y="{top - 4:.1f}" text-anchor="middle" '
                        f'font-size="9.5" font-weight="600" fill="{_C_TITLE}">'
                        f'{_esc(_fmt(v))}</text>')
    return _shell("".join(body), show_border)


def _lighten(hexc: str, amt: float) -> str:
    hexc = hexc.lstrip("#")
    r, g, b = int(hexc[0:2], 16), int(hexc[2:4], 16), int(hexc[4:6], 16)
    r = int(r + (255 - r) * amt)
    g = int(g + (255 - g) * amt)
    b = int(b + (255 - b) * amt)
    return f"#{r:02x}{g:02x}{b:02x}"


def _annular(cx, cy, r1, r2, a1, a2, fill, op=0.92) -> str:
    """A ring segment between radii r1 (inner) and r2 (outer)."""
    large = 1 if (a2 - a1) > math.pi else 0
    x1o, y1o = cx + r2 * math.cos(a1), cy + r2 * math.sin(a1)
    x2o, y2o = cx + r2 * math.cos(a2), cy + r2 * math.sin(a2)
    x1i, y1i = cx + r1 * math.cos(a2), cy + r1 * math.sin(a2)
    x2i, y2i = cx + r1 * math.cos(a1), cy + r1 * math.sin(a1)
    d = (f'M {x1o:.1f} {y1o:.1f} A {r2:.0f} {r2:.0f} 0 {large} 1 {x2o:.1f} {y2o:.1f} '
         f'L {x1i:.1f} {y1i:.1f} A {r1:.0f} {r1:.0f} 0 {large} 0 {x2i:.1f} {y2i:.1f} Z')
    return (f'<path d="{d}" fill="{fill}" fill-opacity="{op}" '
            f'stroke="#ffffff" stroke-width="1.5"/>')


def _svg_pie(pairs, title, show_legend, show_values, show_border,
             variant="pie") -> str:
    pos = [(n, v) for n, v in pairs if isinstance(v, (int, float)) and v > 0]
    if not pos:
        return _note("Pie/Doughnut needs positive values to chart.", show_border)
    total = sum(v for _, v in pos)
    pcts = _percentages([v for _, v in pos])
    cx, cy, r = 190, (_H // 2) + (10 if title else 0), 104
    inner = r * 0.55 if variant == "doughnut" else 0.0
    explode = variant == "exploded"
    body = [_title(title)]
    body.append(f'<circle cx="{cx}" cy="{cy}" r="{r + 4}" fill="{_C_PANEL}"/>')
    ang = -math.pi / 2
    for i, (name, v) in enumerate(pos):
        frac = v / total
        a2 = ang + frac * 2 * math.pi
        mid = (ang + a2) / 2
        ccx, ccy = cx, cy
        if explode:
            ccx, ccy = cx + 13 * math.cos(mid), cy + 13 * math.sin(mid)
        c = _PAL[i % len(_PAL)]
        large = 1 if frac > 0.5 else 0
        if inner > 0:
            body.append(_annular(ccx, ccy, inner, r, ang, a2, c, 0.92))
        else:
            x1, y1 = ccx + r * math.cos(ang), ccy + r * math.sin(ang)
            x2, y2 = ccx + r * math.cos(a2), ccy + r * math.sin(a2)
            body.append(f'<path d="M {ccx:.1f} {ccy:.1f} L {x1:.1f} {y1:.1f} '
                        f'A {r} {r} 0 {large} 1 {x2:.1f} {y2:.1f} Z" '
                        f'fill="{c}" fill-opacity="0.92" stroke="#ffffff" stroke-width="2"/>')
        if show_values and frac > 0.04:
            lr = (inner + r) / 2 if inner > 0 else r * 0.62
            lx, ly = ccx + lr * math.cos(mid), ccy + lr * math.sin(mid)
            body.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                        f'font-size="10" font-weight="600" fill="#ffffff">'
                        f'{pcts[i]}%</text>')
        ang = a2
    if variant == "doughnut":
        body.append(f'<text x="{cx}" y="{cy - 1}" text-anchor="middle" '
                    f'font-size="16" font-weight="700" fill="{_C_TITLE}">'
                    f'{_esc(_fmt(total))}</text>')
        body.append(f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" '
                    f'font-size="9.5" fill="{_C_LABEL}">Total</text>')
    if show_legend:
        ly = 64 if title else 48
        for i, (name, v) in enumerate(pos):
            c = _PAL[i % len(_PAL)]
            label = name if len(name) <= 22 else name[:21] + "\u2026"
            body.append(
                f'<rect x="360" y="{ly - 10}" width="12" height="12" rx="3" fill="{c}"/>'
                f'<text x="378" y="{ly}" font-size="11.5" fill="{_C_TITLE}">'
                f'{_esc(label)}</text>'
                f'<text x="378" y="{ly + 15}" font-size="10" fill="{_C_LABEL}">'
                f'{_esc(_fmt(v))} ({pcts[i]}%)</text>')
            ly += 36
    return _shell("".join(body), show_border)


def _svg_hbar(pairs, title, show_values, show_grid, show_border) -> str:
    px0, px1 = 124, _W - 30
    py0 = 52 if title else 24
    py1 = _H - 28
    vals = [v for _, v in pairs]
    lo, hi = _bounds(min(vals + [0.0]), max(vals + [0.0]))
    zero_x = px0 + (0 - lo) / (hi - lo) * (px1 - px0) if hi > lo else px0
    n = len(pairs)
    slot = (py1 - py0) / max(n, 1)
    bh = min(slot * 0.6, 38)
    body = [_title(title), _panel(px0, py0, px1, py1)]
    for tk in _ticks(lo, hi):
        x = px0 + (tk - lo) / (hi - lo) * (px1 - px0) if hi > lo else px0
        if show_grid:
            body.append(f'<line x1="{x:.1f}" y1="{py0}" x2="{x:.1f}" y2="{py1}" '
                        f'stroke="{_C_GRID}" stroke-width="1" stroke-dasharray="3 4"/>')
        body.append(f'<text x="{x:.1f}" y="{py1 + 14:.1f}" text-anchor="middle" '
                    f'font-size="9" fill="{_C_LABEL}">{_esc(_fmt(tk))}</text>')
    body.append(f'<line x1="{px0}" y1="{py0}" x2="{px0}" y2="{py1}" '
                f'stroke="{_C_AXIS}" stroke-width="1"/>')
    body.append(f'<line x1="{px0}" y1="{py1}" x2="{px1}" y2="{py1}" '
                f'stroke="{_C_AXIS}" stroke-width="1"/>')
    for i, (label, v) in enumerate(pairs):
        cy = py0 + slot * i + slot / 2
        x = px0 + (v - lo) / (hi - lo) * (px1 - px0) if hi > lo else px0
        left, w = (min(x, zero_x), abs(x - zero_x))
        c = _PAL[i % len(_PAL)]
        body.append(f'<rect x="{left:.1f}" y="{cy - bh / 2:.1f}" width="{max(w, 0.8):.1f}" '
                    f'height="{bh:.1f}" rx="4" fill="{c}" fill-opacity="0.92"/>')
        lab = label if len(label) <= 18 else label[:17] + "\u2026"
        body.append(f'<text x="{px0 - 8}" y="{cy + 3:.1f}" text-anchor="end" '
                    f'font-size="9.5" fill="{_C_VALUE}">{_esc(lab)}</text>')
        if show_values:
            body.append(f'<text x="{left + w + 5:.1f}" y="{cy + 3:.1f}" text-anchor="start" '
                        f'font-size="9.5" font-weight="600" fill="{_C_TITLE}">'
                        f'{_esc(_fmt(v))}</text>')
    return _shell("".join(body), show_border)


def _svg_sunburst(groups, title, show_legend, show_border) -> str:
    """groups: list of (group_name, [(tag_name, value), ...]); 2-ring sunburst."""
    flat = [(gn, tn, v) for gn, items in groups for tn, v in items
            if isinstance(v, (int, float)) and v > 0]
    if not flat:
        return _note("Sunburst needs positive values to chart.", show_border)
    gtot: dict = {}
    gorder: list = []
    for gn, tn, v in flat:
        if gn not in gtot:
            gtot[gn] = 0.0
            gorder.append(gn)
        gtot[gn] += v
    total = sum(gtot.values())
    cx, cy = 200, (_H // 2) + (10 if title else 0)
    r_in, r_mid, r_out = 34, 80, 118
    body = [_title(title)]
    body.append(f'<circle cx="{cx}" cy="{cy}" r="{r_out + 4}" fill="{_C_PANEL}"/>')
    ang = -math.pi / 2
    for gi, gn in enumerate(gorder):
        gc = _PAL[gi % len(_PAL)]
        ga2 = ang + (gtot[gn] / total) * 2 * math.pi
        body.append(_annular(cx, cy, r_in, r_mid, ang, ga2, gc, 0.95))
        gmid = (ang + ga2) / 2
        if (gtot[gn] / total) > 0.06:
            lx, ly = cx + ((r_in + r_mid) / 2) * math.cos(gmid), cy + ((r_in + r_mid) / 2) * math.sin(gmid)
            body.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                        f'font-size="8.5" fill="#ffffff">{_esc(gn[:10])}</text>')
        sub = ang
        items = [(tn, v) for g2, tn, v in flat if g2 == gn]
        for j, (tn, v) in enumerate(items):
            tfrac = v / total
            sa2 = sub + tfrac * 2 * math.pi
            tc = _lighten(gc, min(0.18 + 0.16 * j, 0.62))
            body.append(_annular(cx, cy, r_mid, r_out, sub, sa2, tc, 0.95))
            tmid = (sub + sa2) / 2
            if tfrac > 0.05:
                lx, ly = cx + ((r_mid + r_out) / 2) * math.cos(tmid), cy + ((r_mid + r_out) / 2) * math.sin(tmid)
                body.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                            f'font-size="8" fill="{_C_TITLE}">{_esc(tn[:8])}</text>')
            sub = sa2
        ang = ga2
    if show_legend:
        ly = 64 if title else 48
        for gi, gn in enumerate(gorder):
            gc = _PAL[gi % len(_PAL)]
            body.append(
                f'<rect x="372" y="{ly - 10}" width="12" height="12" rx="3" fill="{gc}"/>'
                f'<text x="390" y="{ly}" font-size="11" fill="{_C_TITLE}">'
                f'{_esc(gn[:24])}</text>')
            ly += 22
    return _shell("".join(body), show_border)


def _svg_line(series, title, area, show_legend, show_grid, show_border,
              tz, tz_abbr) -> str:
    series = [(nm, un, pts) for nm, un, pts in series if pts]
    if not series:
        return _note("No time-series data in this period.", show_border)
    px0, px1 = 58, _W - 22
    py0 = 52 if title else 24
    py1 = _H - (52 if show_legend else 40)
    all_v = [v for _, _, pts in series for _, v in pts]
    all_t = [t.timestamp() for _, _, pts in series for t, _ in pts]
    lo, hi = _bounds(min(all_v), max(all_v))
    t0, t1 = min(all_t), max(all_t)
    span = (t1 - t0) or 1.0

    def X(ts):
        return px0 + (ts - t0) / span * (px1 - px0)

    def Y(v):
        return py1 - (v - lo) / (hi - lo) * (py1 - py0) if hi > lo else py1

    body = [_title(title), _panel(px0, py0, px1, py1),
            _axes(lo, hi, px0, py0, px1, py1, show_grid)]
    for i, (name, unit, pts) in enumerate(series):
        c = _PAL[i % len(_PAL)]
        coords = [(X(t.timestamp()), Y(v)) for t, v in pts]
        if area and coords:
            d = (f'M {coords[0][0]:.1f} {py1:.1f} '
                 + " ".join(f'L {x:.1f} {y:.1f}' for x, y in coords)
                 + f' L {coords[-1][0]:.1f} {py1:.1f} Z')
            body.append(f'<path d="{d}" fill="{c}" fill-opacity="0.15" stroke="none"/>')
        poly = " ".join(f'{x:.1f},{y:.1f}' for x, y in coords)
        body.append(f'<polyline points="{poly}" fill="none" stroke="{c}" '
                    f'stroke-width="2.1" stroke-linejoin="round" stroke-linecap="round"/>')
        if len(coords) == 1:
            body.append(f'<circle cx="{coords[0][0]:.1f}" cy="{coords[0][1]:.1f}" '
                        f'r="2.8" fill="{c}"/>')
    span_days = span / 86400.0
    fmt = "%H:%M" if span_days < 1 else "%m-%d %H:%M"
    for frac in (0.0, 0.5, 1.0):
        ts = t0 + span * frac
        lbl = _dt.datetime.fromtimestamp(ts, tz=tz).strftime(fmt)
        anchor = "start" if frac == 0 else "end" if frac == 1 else "middle"
        body.append(f'<text x="{X(ts):.1f}" y="{py1 + 15:.1f}" text-anchor="{anchor}" '
                    f'font-size="9.5" fill="{_C_LABEL}">{_esc(lbl)}</text>')
    if tz_abbr:
        body.append(f'<text x="{px0}" y="{_H - 8}" font-size="9" fill="{_C_LABEL}">'
                    f'Times in {_esc(tz_abbr)}</text>')
    if show_legend:
        body.append(_legend_row([nm for nm, _, _ in series], _H - 22))
    return _shell("".join(body), show_border)


# --------------------------------------------------------------------------
# tag resolution for explicitly-selected tags (may be outside report bindings)
# --------------------------------------------------------------------------
def _tag_names(db, ids) -> dict:
    """{tag_id: name} for tags not present in the report bindings."""
    if not ids or db is None:
        return {}
    try:
        rows = db.execute(text("SELECT id, name FROM tags WHERE id = ANY(:ids)"),
                          {"ids": list(ids)}).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _tag_values(db, ids, window) -> dict:
    """One numeric value per tag for bar/pie when it isn't a report binding:
    average over the report window, or the latest sample if there is no window.
    """
    out: dict = {}
    if not ids or db is None:
        return out
    for tid in ids:
        try:
            if window:
                v = db.execute(text(
                    "SELECT avg(value_double) FROM tag_values "
                    "WHERE tag_id = :t AND time >= :s AND time < :e "
                    "AND value_double IS NOT NULL"),
                    {"t": tid, "s": window[0], "e": window[1]}).scalar()
            else:
                v = db.execute(text(
                    "SELECT value_double FROM tag_values "
                    "WHERE tag_id = :t AND value_double IS NOT NULL "
                    "ORDER BY time DESC LIMIT 1"),
                    {"t": tid}).scalar()
            if v is not None:
                out[tid] = float(v)
        except Exception:
            pass
    return out


def _tag_devices(db, ids) -> dict:
    """{tag_id: device_name} for the sunburst hierarchy (device -> tag)."""
    if not ids or db is None:
        return {}
    try:
        rows = db.execute(text(
            "SELECT t.id, COALESCE(d.name, 'Ungrouped') "
            "FROM tags t LEFT JOIN devices d ON d.id = t.device_id "
            "WHERE t.id = ANY(:ids)"), {"ids": list(ids)}).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def render_chart(block: dict, tags_list, db=None, window=None) -> str:
    """Render one 'chart' block to an inline SVG string. Never raises.

    Plots exactly the tags chosen in the editor (block['tag_ids']); these may be
    any tags, not only the report's bound tags. When no tags are chosen, falls
    back to all of the report's bound tags.
    """
    show_border = block.get("show_border", True) is not False
    try:
        ctype = (block.get("chart_type") or "line").lower()
        title = (block.get("title") or "").strip()
        show_legend = block.get("show_legend", True) is not False
        show_values = bool(block.get("show_values", False))
        show_grid = block.get("show_grid", True) is not False

        # report bindings, indexed by tag id (carry name/value/unit)
        bound: dict = {}
        bound_order: list = []
        for t in (tags_list or []):
            tid = getattr(t, "id", None)
            if tid is None or tid in bound:
                continue
            bound[tid] = {"id": tid, "name": getattr(t, "name", None) or "",
                          "value": getattr(t, "value", None),
                          "unit": getattr(t, "unit", None) or ""}
            bound_order.append(tid)

        # which tags to plot: explicit selection, else all bound tags
        sel = block.get("tag_ids")
        plot_ids = [i for i in sel if i is not None] if isinstance(sel, list) \
            else list(bound_order)
        if not plot_ids:
            return _note("No tags selected for this chart.", show_border)

        # names for selected tags that aren't report bindings
        extra = _tag_names(db, [i for i in plot_ids if i not in bound])
        meta = [bound.get(i, {"id": i, "name": extra.get(i, f"tag_{i}"),
                              "value": None, "unit": ""}) for i in plot_ids]

        if ctype in ("line", "area"):
            if (block.get("time_format") or "system").lower() == "utc":
                tz, tz_abbr = _dt.timezone.utc, "UTC"
            else:
                try:
                    tz = ZoneInfo(_APP_TZ)
                    tz_abbr = _dt.datetime.now(tz).strftime("%Z") or _APP_TZ
                except Exception:
                    tz, tz_abbr = _dt.timezone.utc, "UTC"

            if window:
                start_, end_ = window[0], window[1]
            else:
                end_ = _dt.datetime.now(_dt.timezone.utc)
                mins = int(block.get("window_minutes") or 60)
                start_ = end_ - _dt.timedelta(minutes=max(mins, 1))
            ids = [m["id"] for m in meta]
            series_map = _fetch_series(db, ids, start_, end_) if db is not None else {}
            named = [(m["name"], m["unit"], series_map.get(m["id"], [])) for m in meta]
            return _svg_line(named, title, area=(ctype == "area"),
                             show_legend=show_legend, show_grid=show_grid,
                             show_border=show_border, tz=tz, tz_abbr=tz_abbr)

        # value charts: bar / hbar / pie / doughnut / exploded / sunburst
        need = [m["id"] for m in meta if not isinstance(m["value"], (int, float))]
        qvals = _tag_values(db, need, window) if need else {}
        valued = []  # (id, name, value)
        for m in meta:
            v = m["value"] if isinstance(m["value"], (int, float)) else qvals.get(m["id"])
            if isinstance(v, (int, float)):
                valued.append((m["id"], m["name"], float(v)))
        if not valued:
            return _note("No numeric tag values to chart for this period.", show_border)
        pairs = [(n, v) for _, n, v in valued]
        if ctype == "hbar":
            return _svg_hbar(pairs, title, show_values=show_values,
                             show_grid=show_grid, show_border=show_border)
        if ctype in ("pie", "doughnut", "exploded"):
            return _svg_pie(pairs, title, show_legend=show_legend,
                            show_values=show_values, show_border=show_border,
                            variant=ctype)
        if ctype == "sunburst":
            devmap = _tag_devices(db, [i for i, _, _ in valued])
            order, gd = [], {}
            for i, n, v in valued:
                dn = devmap.get(i, "Ungrouped")
                if dn not in gd:
                    gd[dn] = []
                    order.append(dn)
                gd[dn].append((n, v))
            return _svg_sunburst([(dn, gd[dn]) for dn in order], title,
                                 show_legend=show_legend, show_border=show_border)
        return _svg_bar(pairs, title, show_values=show_values,
                        show_grid=show_grid, show_border=show_border)
    except Exception as e:  # never break a report render over a chart
        return _note(f"Chart could not be rendered ({type(e).__name__}).", show_border)
