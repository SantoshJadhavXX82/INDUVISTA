"""Pair tag endpoints - Phase 12.3.
Phase 16.0h - audit() call on POST /regenerate.

A pair tag is a virtual tag spanning the two halves of a duty/standby
device pair. Its live value resolves dynamically to whichever side is
currently the duty (per devices.duty_role).

Auto-generation:
  POST /api/devices/{id}/pair  -> after pair commit, auto_create_pair_tags()
                                  inserts pair_tags for every (name, data_type)
                                  match across the two devices.
  POST /api/devices/{id}/unpair -> before unpair commit, auto_delete_pair_tags()
                                   removes pair_tags for the pair.

Manual control:
  GET  /api/pair-tags                - list all pair tags (read-only)
  GET  /api/pair-tags/live           - live values resolved to current duty
  POST /api/pair-tags/regenerate     - refresh all pair tags [pair_tag.regenerate]

Helper functions auto_create_pair_tags() and auto_delete_pair_tags() are
called from devices.py during /pair and /unpair operations. They are not
audited here - devices.py audits them as part of device.pair / device.unpair
events with the pair_tag counts in the details.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.utils.audit import audit, AuditEvent


router = APIRouter(prefix="/api", tags=["pair-tags"])


# ---------------------------------------------------------------------------
# Helpers - called both from these endpoints AND from devices.py /pair, /unpair.
# Audit responsibility:
#   - When called from here (regenerate_all_pair_tags), the audit happens
#     in the endpoint with action='pair_tag.regenerate'.
#   - When called from devices.py /pair or /unpair, the audit happens in
#     devices.py with action='device.pair' / 'device.unpair' and the counts
#     embedded in details. Don't audit twice.
# ---------------------------------------------------------------------------


def _canonical_pair(a: int, b: int) -> tuple[int, int]:
    """Return (a, b) with a < b. pair_tags stores rows in canonical order
    so the unique constraint catches duplicate-attempt inserts from either
    direction."""
    return (a, b) if a < b else (b, a)


def auto_create_pair_tags(db: Session, device_a: int, device_b: int) -> int:
    """Create one pair_tag row per (name, data_type)-matching tag pair
    across the two devices. Returns the number of rows inserted.

    Called by POST /devices/{id}/pair AFTER the devices.duty_role and
    redundant_device_id columns have been updated, but BEFORE the commit.
    The atomicity matters: if pair_tag creation fails, the whole pairing
    operation rolls back.

    ON CONFLICT DO NOTHING is defensive - if someone calls /pair twice in
    a row, the second call is a no-op rather than an error.
    """
    primary, partner = _canonical_pair(device_a, device_b)
    result = db.execute(
        text("""
            INSERT INTO pair_tags
                (name, primary_tag_id, partner_tag_id,
                 primary_device_id, partner_device_id, auto_generated)
            SELECT
                ta.name,
                ta.id, tb.id,
                :primary, :partner,
                TRUE
            FROM tags ta
            JOIN tags tb
              ON  tb.device_id  = :partner
              AND tb.name       = ta.name
              AND tb.data_type  = ta.data_type
            WHERE ta.device_id = :primary
            ON CONFLICT (primary_device_id, partner_device_id, name) DO NOTHING
        """),
        {"primary": primary, "partner": partner},
    )
    return result.rowcount or 0


def auto_delete_pair_tags(db: Session, device_a: int, device_b: int) -> int:
    """Delete pair_tags for a pair. Called by /unpair BEFORE the unpair
    commit. Returns the number of rows removed."""
    primary, partner = _canonical_pair(device_a, device_b)
    result = db.execute(
        text("""
            DELETE FROM pair_tags
            WHERE primary_device_id = :primary AND partner_device_id = :partner
        """),
        {"primary": primary, "partner": partner},
    )
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PairTagResponse(BaseModel):
    id: int
    name: str
    primary_tag_id: int
    partner_tag_id: int
    primary_device_id: int
    partner_device_id: int
    primary_device_name: str
    partner_device_name: str
    primary_device_duty_role: str
    partner_device_duty_role: str
    auto_generated: bool
    created_at: str


class PairTagLive(BaseModel):
    kind: str = "pair"
    pair_tag_id: int
    tag_name: str
    data_type: str
    function_code: int
    address: int
    engineering_unit: str | None
    active_device_id: int | None
    active_device_name: str | None
    active_tag_id: int | None
    primary_device_id: int
    primary_device_name: str
    primary_device_duty_role: str
    partner_device_id: int
    partner_device_name: str
    partner_device_duty_role: str
    pair_manual_override: bool
    value_double: float | None
    value_text: str | None
    time: str | None
    st: int | None
    st_reason: str | None
    age_seconds: float | None


class RegenerateResponse(BaseModel):
    pairs_processed: int
    created: int
    deleted_orphans: int


# ---------------------------------------------------------------------------
# Read-only endpoints (not audited)
# ---------------------------------------------------------------------------


@router.get("/pair-tags", response_model=list[PairTagResponse])
def list_pair_tags(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(
        text("""
            SELECT
                pt.id, pt.name,
                pt.primary_tag_id, pt.partner_tag_id,
                pt.primary_device_id, pt.partner_device_id,
                dp.name AS primary_device_name,
                dq.name AS partner_device_name,
                dp.duty_role AS primary_device_duty_role,
                dq.duty_role AS partner_device_duty_role,
                pt.auto_generated, pt.created_at
            FROM pair_tags pt
            JOIN devices dp ON dp.id = pt.primary_device_id
            JOIN devices dq ON dq.id = pt.partner_device_id
            ORDER BY pt.primary_device_id, pt.partner_device_id, pt.name
        """)
    ).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]


@router.get("/pair-tags/live", response_model=list[PairTagLive])
def list_pair_tags_live(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(
        text("""
            SELECT
                pt.id AS pair_tag_id,
                pt.name AS tag_name,
                ta.data_type,
                ta.function_code,
                ta.address,
                COALESCE(eu.code, ta.engineering_unit) AS engineering_unit,
                pt.primary_device_id,
                dp.name AS primary_device_name,
                dp.duty_role AS primary_device_duty_role,
                pt.partner_device_id,
                dq.name AS partner_device_name,
                dq.duty_role AS partner_device_duty_role,
                (dp.manual_override OR dq.manual_override) AS pair_manual_override,
                CASE
                    WHEN dp.duty_role = 'duty' THEN pt.primary_device_id
                    WHEN dq.duty_role = 'duty' THEN pt.partner_device_id
                    ELSE pt.primary_device_id
                END AS active_device_id,
                CASE
                    WHEN dp.duty_role = 'duty' THEN dp.name
                    WHEN dq.duty_role = 'duty' THEN dq.name
                    ELSE dp.name
                END AS active_device_name,
                CASE
                    WHEN dp.duty_role = 'duty' THEN pt.primary_tag_id
                    WHEN dq.duty_role = 'duty' THEN pt.partner_tag_id
                    ELSE pt.primary_tag_id
                END AS active_tag_id,
                lv.value_double,
                lv.value_text,
                lv.time,
                lv.st,
                lv.st_reason,
                EXTRACT(EPOCH FROM (NOW() - lv.time)) AS age_seconds
            FROM pair_tags pt
            JOIN tags ta ON ta.id = pt.primary_tag_id
            JOIN devices dp ON dp.id = pt.primary_device_id
            JOIN devices dq ON dq.id = pt.partner_device_id
            LEFT JOIN engineering_units eu ON eu.id = ta.engineering_unit_id
            LEFT JOIN latest_tag_values lv
                ON lv.tag_id = CASE
                    WHEN dp.duty_role = 'duty' THEN pt.primary_tag_id
                    WHEN dq.duty_role = 'duty' THEN pt.partner_tag_id
                    ELSE pt.primary_tag_id
                END
            ORDER BY pt.primary_device_id, pt.partner_device_id, pt.name
        """)
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["kind"] = "pair"
        if d.get("time") is not None:
            d["time"] = d["time"].isoformat()
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Mutating endpoint (audited)
# ---------------------------------------------------------------------------


@router.post("/pair-tags/regenerate", response_model=RegenerateResponse)
def regenerate_all_pair_tags(
    request: Request,
    db: Annotated[Session, Depends(get_session)],
):
    """Recompute pair_tags for every paired device.

    Audited as 'pair_tag.regenerate' - a system-wide maintenance op.
    Summary captures (pairs_processed, created, deleted_orphans) so a
    future "when did regeneration last actually do something?" question
    can be answered from the audit log alone.
    """
    pairs = db.execute(
        text("""
            SELECT DISTINCT
                LEAST(id, redundant_device_id) AS primary_device_id,
                GREATEST(id, redundant_device_id) AS partner_device_id
            FROM devices
            WHERE redundant_device_id IS NOT NULL
              AND duty_role IN ('duty', 'standby')
        """)
    ).mappings().all()

    pair_list = [
        {"primary": p["primary_device_id"], "partner": p["partner_device_id"]}
        for p in pairs
    ]

    try:
        created_total = 0
        for p in pairs:
            created_total += auto_create_pair_tags(
                db, p["primary_device_id"], p["partner_device_id"]
            )

        # Orphan cleanup: pair_tags whose primary_tag and partner_tag no
        # longer agree on (name, data_type). This catches renames.
        deleted = db.execute(
            text("""
                DELETE FROM pair_tags pt
                USING tags ta, tags tb
                WHERE ta.id = pt.primary_tag_id
                  AND tb.id = pt.partner_tag_id
                  AND (ta.name <> pt.name
                       OR tb.name <> pt.name
                       OR ta.data_type <> tb.data_type)
            """)
        )
        deleted_orphans = deleted.rowcount or 0

        db.commit()
    except Exception as e:
        db.rollback()
        audit(AuditEvent(
            action="pair_tag.regenerate",
            target_type="pair_tag",
            summary="Regenerate failed",
            status="error",
            error_message=f"{type(e).__name__}: {e}",
            details={"pairs_attempted": pair_list},
        ), request)
        raise

    audit(AuditEvent(
        action="pair_tag.regenerate",
        target_type="pair_tag",
        summary=f"Regenerated pair_tags: {len(pairs)} pair(s) processed, "
                f"{created_total} created, {deleted_orphans} orphan(s) removed",
        details={
            "pairs_processed": len(pairs),
            "pairs": pair_list,
            "created": created_total,
            "deleted_orphans": deleted_orphans,
        },
    ), request)

    return {
        "pairs_processed": len(pairs),
        "created": created_total,
        "deleted_orphans": deleted_orphans,
    }
