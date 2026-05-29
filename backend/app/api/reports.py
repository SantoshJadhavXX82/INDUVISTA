"""Phase 20 — Reporting: shift-summary API + PDF export.

  GET /api/reports/shift-summary       -> JSON (on-screen report)
  GET /api/reports/shift-summary.pdf   -> downloadable PDF (print/email/archive)

Both share _compute_shift_summary() so the aggregation, shift-window math, and
quality accounting live in one place. The PDF is rendered server-side with
reportlab (consistent across browsers, and ready for the scheduler later).
"""
from __future__ import annotations

import io
from datetime import date as date_cls, datetime, time as time_cls, timedelta
from zoneinfo import ZoneInfo
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.config import settings

router = APIRouter(prefix="/api/reports", tags=["reports"])

GOOD_ST = 128


class TagShiftStat(BaseModel):
    tag_id: int
    name: str
    engineering_unit: str | None = None
    data_type: str
    avg: float | None = None
    min: float | None = None
    max: float | None = None
    first: float | None = None
    last: float | None = None
    first_text: str | None = None
    last_text: str | None = None
    sample_count: int = 0
    good_count: int = 0
    bad_count: int = 0


class ShiftWindow(BaseModel):
    code: str
    label: str
    start_local: str
    end_local: str
    start_utc: str
    end_utc: str
    tags: list[TagShiftStat]


class ShiftSummaryResponse(BaseModel):
    report_date: str
    timezone: str
    generated_at: str
    tag_ids: list[int]
    shifts: list[ShiftWindow]


def _load_shifts(db: Session) -> list[dict]:
    import json
    row = db.execute(
        text("SELECT value FROM system_settings WHERE key = 'shifts.config'")
    ).first()
    if row and row[0]:
        cfg = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        shifts = cfg.get("shifts") or []
    else:
        shifts = [
            {"code": "A", "label": "Morning", "start": "06:00"},
            {"code": "B", "label": "Evening", "start": "14:00"},
            {"code": "C", "label": "Night", "start": "22:00"},
        ]
    shifts.sort(key=lambda s: s["start"])
    return shifts


def _shift_windows(shifts: list[dict], day: date_cls, tz: ZoneInfo) -> list[dict]:
    out = []
    n = len(shifts)
    for i, s in enumerate(shifts):
        hh, mm = map(int, s["start"].split(":"))
        start_local = datetime.combine(day, time_cls(hh, mm), tzinfo=tz)
        nxt = shifts[(i + 1) % n]
        nhh, nmm = map(int, nxt["start"].split(":"))
        if i + 1 < n:
            end_local = datetime.combine(day, time_cls(nhh, nmm), tzinfo=tz)
        else:
            end_local = datetime.combine(day + timedelta(days=1), time_cls(nhh, nmm), tzinfo=tz)
        out.append({
            "code": s["code"], "label": s["label"],
            "start_local": start_local, "end_local": end_local,
            "start_utc": start_local.astimezone(ZoneInfo("UTC")),
            "end_utc": end_local.astimezone(ZoneInfo("UTC")),
        })
    return out


def _compute_shift_summary(
    db: Session, tag_ids: list[int], date: str | None, shift_code: str | None,
) -> ShiftSummaryResponse:
    if not tag_ids:
        raise HTTPException(400, "At least one tag_id is required.")
    if len(tag_ids) > 200:
        raise HTTPException(400, "At most 200 tags per report.")

    tz_name = settings.app_timezone
    tz = ZoneInfo(tz_name)

    if date:
        try:
            day = date_cls.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, f"Invalid date {date!r}; expected YYYY-MM-DD.")
    else:
        day = datetime.now(tz).date()

    meta_rows = db.execute(text("""
        SELECT t.id, t.name, t.data_type,
               COALESCE(eu.label, t.engineering_unit) AS engineering_unit
        FROM tags t
        LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
        WHERE t.id = ANY(:ids) AND t.deleted_at IS NULL
    """), {"ids": tag_ids}).mappings().all()
    meta = {r["id"]: r for r in meta_rows}
    missing = [tid for tid in tag_ids if tid not in meta]
    if missing:
        raise HTTPException(404, f"Tag(s) not found or deleted: {missing}")

    windows = _shift_windows(_load_shifts(db), day, tz)
    if shift_code:
        windows = [w for w in windows if w["code"] == shift_code]
        if not windows:
            raise HTTPException(404, f"Shift code {shift_code!r} not found.")

    shift_results: list[ShiftWindow] = []
    for w in windows:
        rows = db.execute(text("""
            SELECT
                tag_id,
                avg(value_double) FILTER (WHERE st >= :good)          AS avg_v,
                min(value_double) FILTER (WHERE st >= :good)          AS min_v,
                max(value_double) FILTER (WHERE st >= :good)          AS max_v,
                first(value_double, time) FILTER (WHERE st >= :good)  AS first_v,
                last(value_double, time)  FILTER (WHERE st >= :good)  AS last_v,
                first(value_text, time)   FILTER (WHERE st >= :good)  AS first_t,
                last(value_text, time)    FILTER (WHERE st >= :good)  AS last_t,
                count(*)                                              AS n_all,
                count(*) FILTER (WHERE st >= :good)                   AS n_good,
                count(*) FILTER (WHERE st < :good)                    AS n_bad
            FROM tag_values
            WHERE tag_id = ANY(:ids) AND time >= :s AND time < :e
            GROUP BY tag_id
        """), {"ids": tag_ids, "s": w["start_utc"], "e": w["end_utc"], "good": GOOD_ST}).mappings().all()
        by_tag = {r["tag_id"]: r for r in rows}

        tag_stats: list[TagShiftStat] = []
        for tid in tag_ids:
            m = meta[tid]
            r = by_tag.get(tid)
            if r is None:
                tag_stats.append(TagShiftStat(
                    tag_id=tid, name=m["name"], engineering_unit=m["engineering_unit"],
                    data_type=m["data_type"], sample_count=0, good_count=0, bad_count=0,
                ))
                continue
            tag_stats.append(TagShiftStat(
                tag_id=tid, name=m["name"], engineering_unit=m["engineering_unit"],
                data_type=m["data_type"],
                avg=r["avg_v"], min=r["min_v"], max=r["max_v"],
                first=r["first_v"], last=r["last_v"],
                first_text=r["first_t"], last_text=r["last_t"],
                sample_count=r["n_all"] or 0, good_count=r["n_good"] or 0, bad_count=r["n_bad"] or 0,
            ))

        shift_results.append(ShiftWindow(
            code=w["code"], label=w["label"],
            start_local=w["start_local"].isoformat(), end_local=w["end_local"].isoformat(),
            start_utc=w["start_utc"].isoformat(), end_utc=w["end_utc"].isoformat(),
            tags=tag_stats,
        ))

    return ShiftSummaryResponse(
        report_date=day.isoformat(), timezone=tz_name,
        generated_at=datetime.now(ZoneInfo("UTC")).isoformat(),
        tag_ids=tag_ids, shifts=shift_results,
    )


@router.get("/shift-summary", response_model=ShiftSummaryResponse)
def shift_summary(
    db: Annotated[Session, Depends(get_session)],
    tag_ids: Annotated[list[int], Query(description="Tag IDs to include")],
    date: str | None = Query(None, description="Local calendar date YYYY-MM-DD (default: today)"),
    shift_code: str | None = Query(None, description="Limit to a single shift code"),
):
    """Per-tag aggregates for each shift on the given local date."""
    return _compute_shift_summary(db, tag_ids, date, shift_code)


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.1f}"
    return f"{v:.4g}"


def _render_pdf(summary: ShiftSummaryResponse) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Shift Summary {summary.report_date}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#666666"))
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10)
    cell_r = ParagraphStyle("cellR", parent=cell, alignment=2)
    tag_name = ParagraphStyle("tn", parent=styles["Normal"], fontSize=9, leading=11)

    story = []
    story.append(Paragraph("Shift Summary Report", h1))
    gen = datetime.fromisoformat(summary.generated_at)
    story.append(Paragraph(
        f"Date: {summary.report_date} &nbsp;&middot;&nbsp; Timezone: {summary.timezone} "
        f"&nbsp;&middot;&nbsp; Tags: {len(summary.tag_ids)} &nbsp;&middot;&nbsp; Shifts: {len(summary.shifts)} "
        f"&nbsp;&middot;&nbsp; Generated: {gen.strftime('%Y-%m-%d %H:%M UTC')}",
        sub,
    ))
    story.append(Spacer(1, 8))

    header = [Paragraph("<b>Tag</b>", cell)]
    for s in summary.shifts:
        sl = datetime.fromisoformat(s.start_local).strftime("%H:%M")
        el = datetime.fromisoformat(s.end_local).strftime("%H:%M")
        header.append(Paragraph(
            f"<b>{s.code} &middot; {s.label}</b><br/><font size=6 color='#888888'>{sl}-{el}</font>", cell))

    data = [header]
    for tid in summary.tag_ids:
        per = [next((t for t in s.tags if t.tag_id == tid), None) for s in summary.shifts]
        meta = next((p for p in per if p), None)
        nm = meta.name if meta else f"Tag {tid}"
        unit = f"<br/><font size=6 color='#888888'>{meta.engineering_unit}</font>" if meta and meta.engineering_unit else ""
        row = [Paragraph(f"<b>{nm}</b>{unit}", tag_name)]
        for st in per:
            if not st or st.sample_count == 0:
                row.append(Paragraph("—", cell_r))
            elif st.avg is not None:
                txt = (
                    f"<b>{_fmt(st.avg)}</b><br/>"
                    f"<font size=6 color='#888888'>min {_fmt(st.min)} &middot; max {_fmt(st.max)}</font><br/>"
                    f"<font size=6 color='#888888'>first {_fmt(st.first)} &middot; last {_fmt(st.last)}</font>"
                )
                if st.bad_count > 0:
                    txt += f"<br/><font size=6 color='#cc7700'>{st.bad_count} bad / {st.sample_count}</font>"
                row.append(Paragraph(txt, cell_r))
            else:
                row.append(Paragraph((st.last_text or st.first_text or "—"), cell_r))
        data.append(row)

    tag_w = 55 * mm
    shift_w = (doc.width - tag_w) / max(1, len(summary.shifts))
    table = Table(data, colWidths=[tag_w] + [shift_w] * len(summary.shifts), repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0040A0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Each cell shows the shift average (bold) with min/max and first/last below. "
        "Aggregates use GOOD-quality samples only. Empty cells mean no samples were logged "
        "in that shift window. SVJ Industrial Reporting Tool.",
        sub,
    ))

    doc.build(story)
    return buf.getvalue()


@router.get("/shift-summary.pdf")
def shift_summary_pdf(
    db: Annotated[Session, Depends(get_session)],
    tag_ids: Annotated[list[int], Query(description="Tag IDs to include")],
    date: str | None = Query(None, description="Local calendar date YYYY-MM-DD (default: today)"),
    shift_code: str | None = Query(None, description="Limit to a single shift code"),
):
    """Downloadable PDF of the shift summary."""
    summary = _compute_shift_summary(db, tag_ids, date, shift_code)
    pdf_bytes = _render_pdf(summary)
    fname = f"shift_summary_{summary.report_date}.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
