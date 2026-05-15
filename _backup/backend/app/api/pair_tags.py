"""Pair tag endpoints — Phase 12.3.

A pair tag is a virtual tag spanning the two halves of a duty/standby
device pair. Its live value resolves dynamically to whichever side is
currently the duty (per devices.duty_role).

Auto-generation:
  POST /api/devices/{id}/pair  → after pair commit, auto_create_pair_tags()
                                  inserts pair_tags for every (name, data_type)
                                  match across the two devices.
  POST /api/devices/{id}/unpair → before unpair commit, auto_delete_pair_tags()
                                   removes pair_tags for the pair.

Manual control:
  GET  /api/pair-tags                — list all pair tags
  GET  /api/pair-tags/live           — live values resolved to current duty
  POST /api/pair-tags/regenerate     — refresh all pair tags (e.g. after
                                       adding tags to a paired device)
  POST /api/pair-tags/regenerate/{pair_id}  — refresh one pair
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["pair-tags"])


# ---------------------------------------------------------------------------
# Helpers — used both by these endpoints AND by the /pair, /unpair endpoints
# in devices.py. Defined as standalone functions so devices.py can call them
# inside the same DB transaction as the pairing change itself.
# ---------------------------------------------------------------------------

def _canonical_pair(a: int, b: int) -> tuple[int, int]:
    """Return (a, b) with a < b. pair_tags stores rows in canonical order
    so the unique constraint catches duplicate-attempt inserts from either
    direction."""
    return (a, b) if a < b else (b, a)


def auto_create_pair_tags(db: Session, device_a: int, device_b: int) -> int:
    """Create one pair_tag row per (name, data_type)-matching tag pair across
    the two devices. Returns the number of rows inserted.

    Called by POST /devices/{id}/pair AFTER the devices.duty_role and
    redundant_device_id columns have been updated, but BEFORE the commit.
    The atomicity matters: if pair_tag creation fails, the whole pairing
    operation rolls back.

    ON CONFLICT DO NOTHING is defensive — if someone calls /pair twice in
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
    """One row per pair tag, with the value resolved to whichever side is
    currently duty.

    `kind` is always 'pair' — this lets the frontend merge pair tags into
    the same list as LiveTag rows and differentiate them by inspecting
    one field rather than two endpoints' worth of types."""
    kind: str = "pair"
    pair_tag_id: int
    tag_name: str
    data_type: str
    function_code: int
    address: int
    engineering_unit: str | None
    # The "active" side is whichever is currently duty.
    active_device_id: int | None
    active_device_name: str | None
    active_tag_id: int | None
    # Both sides of the pair for context — UI shows "duty: X, standby: Y".
    primary_device_id: int
    primary_device_name: str
    primary_device_duty_role: str
    partner_device_id: int
    partner_device_name: str
    partner_device_duty_role: str
    # Live value from the active side (NULL if neither side is duty,
    # which only happens if the schema invariant is broken).
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
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/pair-tags", response_model=list[PairTagResponse])
def list_pair_tags(db: Annotated[Session, Depends(get_session)]):
    """List every pair tag with the current duty_role of both sides.

    Useful for the pair-tag management view; for live values use
    /pair-tags/live which joins against latest_tag_values."""
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
    """Live values for every pair tag, resolved to whichever side is duty.

    The CASE expression in the SELECT decides which side is active:
      - primary is duty   → use primary
      - partner is duty   → use partner
      - neither is duty   → use primary as a fallback (shouldn't happen
                            given the ck_devices_duty_role_consistency
                            constraint, but we don't NULL the row out
                            because that would hide the pair tag entirely)
    """
    rows = db.execute(
        text("""
            SELECT
                pt.id AS pair_tag_id,
                pt.name AS tag_name,
                ta.data_type,
                ta.function_code,
                ta.address,
                COALESCE(eu.code, ta.engineering_unit_override) AS engineering_unit,
                pt.primary_device_id,
                dp.name AS primary_device_name,
                dp.duty_role AS primary_device_duty_role,
                pt.partner_device_id,
                dq.name AS partner_device_name,
                dq.duty_role AS partner_device_duty_role,
                -- Active side resolution
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
            -- Resolve the active tag's latest value (LEFT JOIN — pair tags
            -- whose active side has never been polled still show up with
            -- NULL value, rather than disappearing from the list).
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


@router.post("/pair-tags/regenerate", response_model=RegenerateResponse)
def regenerate_all_pair_tags(db: Annotated[Session, Depends(get_session)]):
    """Recompute pair_tags for every paired device.

    Use when tags have been added or renamed on a paired device after the
    initial pairing. The function:
      1. For each pair, runs the auto_create logic (idempotent: new matches
         are added, existing rows are left alone via ON CONFLICT).
      2. Removes orphan pair_tags whose underlying primary_tag_id or
         partner_tag_id no longer match by name (e.g. someone renamed
         one side and the pair is no longer canonical).
    """
    # Find every paired-device couple, in canonical order (lower id first).
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

    db.commit()
    return {
        "pairs_processed": len(pairs),
        "created": created_total,
        "deleted_orphans": deleted.rowcount or 0,
    }
