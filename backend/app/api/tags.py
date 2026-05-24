"""CRUD endpoints for tags."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import handle_integrity_error, sql_col
from app.api._validation import check_tag_fits_block, find_tag_overlaps
from app.db import get_session
from app.modbus.datatypes import canonical_register_count

router = APIRouter(prefix="/api", tags=["tags"])


DataType = Literal[
    "int16", "uint16", "int32", "uint32", "int64", "uint64",
    "float32", "float64", "bool",
]
ByteOrder = Literal["ABCD", "CDAB", "BADC", "DCBA"]


class TagCreate(BaseModel):
    device_id: int
    register_block_id: int | None = Field(
        None, description="Optional — writables may not be polled"
    )
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    data_type: DataType
    byte_order: ByteOrder = "ABCD"
    function_code: int = Field(..., ge=1, le=4)
    address: int = Field(..., ge=0, le=65535)
    register_count: int | None = Field(
        None, ge=1, le=4,
        description=(
            "Optional — when omitted, the server derives it from data_type "
            "(bool/int16/uint16 → 1, int32/uint32/float32 → 2, "
            "int64/uint64/float64 → 4). In Enron blocks this represents the "
            "on-wire width of one logical address; the API rejects values "
            "that disagree with data_type."
        ),
    )
    # Phase 8.1: engineering unit has two paths — pick from master (preferred)
    # or override with free text. Mutual exclusion is enforced by a CHECK
    # constraint in the DB and a validator below.
    engineering_unit_id: int | None = Field(
        None, description="FK to engineering_units master (preferred)"
    )
    engineering_unit: str | None = Field(
        None, max_length=64,
        description="Free-text override — use only when master doesn't have what you need",
    )
    scale: float = 1.0
    offset: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    # Phase 23.8 — display precision (digits after decimal point) in
    # the UI. NULL = auto (magnitude-based heuristic). 0..15 valid.
    # Storage precision is controlled by data_type — this is purely
    # for rendering.
    decimal_places: int | None = Field(None, ge=0, le=15)
    is_heartbeat: bool = False
    heartbeat_max_stale_sec: int | None = Field(None, gt=0)
    # Phase 8.3 — optional reference to a named_set for value→label translation.
    # Only meaningful for integer/boolean data types; the API doesn't enforce
    # this (data_type can change) but the UI does.
    named_set_id: int | None = None
    # Phase 8.5.1 — explicit write opt-in. Defaults FALSE — every tag is
    # read-only until the engineer says otherwise. DB CHECK forbids
    # writable=true on FC 2 (DI) and FC 4 (IR).
    writable: bool = Field(False, description="Allow writes to this tag")

    def model_post_init(self, _ctx):
        _validate_unit_mutual_exclusion(self.engineering_unit_id, self.engineering_unit)


class TagUpdate(BaseModel):
    # Phase 11 — name editable. Tag identity is the integer id (FK from
    # tag_values), not the name. Renaming preserves all historical data;
    # the unique constraint (device_id, name) is enforced at the DB level
    # and surfaces as a 409 if the new name collides with a sibling tag.
    name: str | None = Field(None, min_length=1, max_length=100)
    register_block_id: int | None = None
    description: str | None = None
    data_type: DataType | None = None
    byte_order: ByteOrder | None = None
    function_code: int | None = Field(None, ge=1, le=4)
    address: int | None = Field(None, ge=0, le=65535)
    register_count: int | None = Field(None, ge=1, le=4)
    engineering_unit_id: int | None = None
    engineering_unit: str | None = Field(None, max_length=64)
    scale: float | None = None
    offset: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    decimal_places: int | None = Field(None, ge=0, le=15)
    enabled: bool | None = None
    is_heartbeat: bool | None = None
    heartbeat_max_stale_sec: int | None = Field(None, gt=0)
    named_set_id: int | None = None
    # Phase 8.5.1 — writability toggle
    writable: bool | None = None
    # Note: no model_post_init validator here — PATCH may pass only ONE of the
    # two fields (e.g. switching from FK to override clears one), so the
    # DB CHECK constraint is the right gate, not the Pydantic model.


class TagResponse(BaseModel):
    id: int
    device_id: int
    device_name: str
    register_block_id: int | None
    register_block_name: str | None
    name: str
    description: str | None
    data_type: str
    byte_order: str
    function_code: int
    address: int
    register_count: int
    # Both unit fields — exactly one is non-null (or both null for unitless tags)
    engineering_unit_id: int | None
    engineering_unit: str | None
    # Resolved fields from the master, included for display — saves the
    # frontend from doing a separate lookup. NULL when the tag uses an
    # override (or has no unit at all).
    unit_code: str | None
    unit_label: str | None
    unit_quantity_kind: str | None
    scale: float
    offset: float
    min_value: float | None
    max_value: float | None
    decimal_places: int | None   # Phase 23.8 — see TagBase docstring
    enabled: bool
    is_heartbeat: bool
    heartbeat_max_stale_sec: int | None
    # Phase 8.3 — named_set FK + resolved name (NULL when no set assigned)
    named_set_id: int | None
    named_set_name: str | None
    # Phase 8.5.1 — write opt-in (false for read-only tags)
    writable: bool


def _validate_unit_mutual_exclusion(unit_id: int | None, unit_text: str | None) -> None:
    """Enforce: at most one of (engineering_unit_id, engineering_unit) set.

    Empty string in the text field counts as not-set, matching the DB CHECK.
    """
    if unit_id is not None and unit_text is not None and unit_text.strip():
        raise ValueError(
            "Cannot set both engineering_unit_id (master FK) and "
            "engineering_unit (override text). Pick one — leave the other null."
        )


_TAG_SELECT = """
    SELECT t.id, t.device_id, d.name AS device_name,
           t.register_block_id, b.name AS register_block_name,
           t.name, t.description, t.data_type, t.byte_order,
           t.function_code, t.address, t.register_count,
           t.engineering_unit_id, t.engineering_unit,
           eu.code  AS unit_code,
           eu.label AS unit_label,
           eu.quantity_kind AS unit_quantity_kind,
           t.scale, t."offset",
           t.min_value, t.max_value, t.decimal_places, t.enabled,
           t.is_heartbeat, t.heartbeat_max_stale_sec,
           t.named_set_id, ns.name AS named_set_name,
           t.writable
    FROM tags t
    JOIN devices d ON d.id = t.device_id
    LEFT JOIN register_blocks b ON b.id = t.register_block_id
    LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
    LEFT JOIN named_sets ns ON ns.id = t.named_set_id
"""


def _resolve_register_count(
    data_type: str,
    register_count: int | None,
    *,
    is_enron_block: bool,
) -> int:
    """Reconcile the supplied register_count with the canonical value for
    data_type. The result is what will be persisted.

    Rules:
      * register_count omitted → derive from data_type (auto).
      * register_count supplied and equals canonical → accept.
      * register_count supplied and differs from canonical, in an Enron
        block → reject (wire-width inference would break).
      * register_count supplied and differs from canonical, STANDARD mode
        → accept (legacy / power-user override).
    """
    canonical = canonical_register_count(data_type)
    if canonical is None:
        raise HTTPException(
            400, f"unsupported data_type {data_type!r}",
        )
    if register_count is None:
        return canonical
    if register_count == canonical:
        return register_count
    if is_enron_block:
        raise HTTPException(
            400,
            f"register_count={register_count} disagrees with data_type "
            f"{data_type!r} (canonical = {canonical}). In Enron blocks the "
            f"register_count is locked to the data_type's natural width "
            f"because the wire-level reader uses it to slice the response. "
            f"Either change data_type to one that needs {register_count} "
            f"registers, or let the API derive register_count automatically "
            f"by omitting it.",
        )
    # STANDARD mode tolerates non-canonical pairings (rare power-user case).
    return register_count


def _block_addressing_mode(db: Session, register_block_id: int) -> str:
    """Look up a register_block's addressing_mode. Returns 'STANDARD' if the
    block doesn't exist (FK error will surface at INSERT time)."""
    row = db.execute(
        text("SELECT addressing_mode FROM register_blocks WHERE id = :id"),
        {"id": register_block_id},
    ).first()
    if not row:
        return "STANDARD"
    return row[0]


def _validate_addressing(
    db: Session,
    *,
    device_id: int,
    function_code: int,
    address: int,
    register_count: int,
    register_block_id: int | None,
    exclude_tag_id: int | None,
    is_enron_block: bool,
) -> None:
    """Run pre-write validation on a tag's addressing.

    Raises 400 if the tag would overlap an existing tag, or if its declared
    register_block can't contain it.

    For Enron blocks the address span used for overlap/fit checks is 1
    (each logical address is one independent value), regardless of
    register_count. For STANDARD blocks it's register_count (the tag's
    bytes really do span that many physical registers).
    """
    address_span = 1 if is_enron_block else register_count

    if is_enron_block:
        # Enron overlap = another tag at the SAME address in the same block.
        # The find_tag_overlaps byte-range arithmetic over-flags here, because
        # neighbouring float32 tags at addresses N and N+1 both have rc=2.
        sql = """
            SELECT id, name, address, register_count
            FROM tags
            WHERE register_block_id = :rb_id
              AND address = :addr
        """
        params: dict = {"rb_id": register_block_id, "addr": address}
        if exclude_tag_id is not None:
            sql += " AND id <> :exclude_id"
            params["exclude_id"] = exclude_tag_id
        same_addr = db.execute(text(sql), params).mappings().all()
        if same_addr:
            other = same_addr[0]
            raise HTTPException(
                400,
                f"address {address} is already used by tag {other['name']!r} "
                f"(id={other['id']}) in this Enron block. Each Enron address "
                f"holds one logical value; pick a different address.",
            )
    else:
        overlaps = find_tag_overlaps(
            db, device_id, function_code, address, address_span,
            exclude_tag_id=exclude_tag_id,
        )
        if overlaps:
            shown = ", ".join(
                f"{o['name']!r} (id={o['id']}, address={o['address']}+{o['register_count']})"
                for o in overlaps[:3]
            )
            more = f" and {len(overlaps) - 3} more" if len(overlaps) > 3 else ""
            raise HTTPException(
                400,
                f"tag address range [{address}, {address + address_span}) "
                f"on FC {function_code} overlaps with: {shown}{more}",
            )

    if register_block_id is not None:
        problem = check_tag_fits_block(
            db, register_block_id, function_code, address, address_span,
        )
        if problem is not None:
            raise HTTPException(400, f"tag does not fit its register_block: {problem}")

        if is_enron_block:
            # Uniform-width invariant across an Enron block — guards the
            # Enron channel reader, which infers value width from the first
            # tag's register_count.
            block_info = db.execute(text("""
                SELECT (SELECT MIN(t.register_count)
                          FROM tags t
                         WHERE t.register_block_id = :rb_id
                           AND (:exclude IS NULL OR t.id != :exclude)
                       ) AS min_existing_rc,
                       (SELECT MAX(t.register_count)
                          FROM tags t
                         WHERE t.register_block_id = :rb_id
                           AND (:exclude IS NULL OR t.id != :exclude)
                       ) AS max_existing_rc
            """), {"rb_id": register_block_id, "exclude": exclude_tag_id}).mappings().first()
            existing_min = block_info["min_existing_rc"] if block_info else None
            existing_max = block_info["max_existing_rc"] if block_info else None
            if existing_min is not None and existing_min != existing_max:
                raise HTTPException(
                    400,
                    f"Enron block {register_block_id} already contains "
                    f"tags with mixed register_count "
                    f"({existing_min}..{existing_max}). Resolve the existing "
                    f"mismatch before adding more tags.",
                )
            if existing_min is not None and existing_min != register_count:
                raise HTTPException(
                    400,
                    f"Enron blocks require uniform tag width. Existing tags "
                    f"use register_count={existing_min} (width={existing_min*2} "
                    f"bytes / data_type wider than 16-bit needs ≥2). This tag "
                    f"is register_count={register_count}. Either change this "
                    f"tag's data_type to match, or put it in a different block.",
                )


@router.get("/tags", response_model=list[TagResponse])
def list_tags(
    db: Annotated[Session, Depends(get_session)],
    device_id: Annotated[int | None, Query()] = None,
    register_block_id: Annotated[int | None, Query()] = None,
    name: Annotated[str | None, Query(description="Partial match, case-insensitive")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    where: list[str] = []
    params: dict = {}
    if device_id is not None:
        where.append("t.device_id = :device_id")
        params["device_id"] = device_id
    if register_block_id is not None:
        where.append("t.register_block_id = :rb_id")
        params["rb_id"] = register_block_id
    if name:
        where.append("t.name ILIKE :name_pat")
        params["name_pat"] = f"%{name}%"

    sql = _TAG_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.device_id, t.function_code, t.address LIMIT :limit"
    params["limit"] = limit

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get(
    "/devices/{device_id}/tags",
    response_model=list[TagResponse],
    tags=["devices"],
)
def list_device_tags(
    device_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    return list_tags(db, device_id=device_id, register_block_id=None, name=None)


@router.get("/tags/{tag_id}", response_model=TagResponse)
def get_tag(tag_id: int, db: Annotated[Session, Depends(get_session)]):
    row = db.execute(
        text(_TAG_SELECT + " WHERE t.id = :id"),
        {"id": tag_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"tag {tag_id} not found")
    return dict(row)


@router.post("/tags", response_model=TagResponse, status_code=201)
def create_tag(body: TagCreate, db: Annotated[Session, Depends(get_session)]):
    # Phase 9.1.1+ — auto-derive register_count from data_type when the
    # client omitted it. This lets the UI hide the field for Enron blocks
    # (where it's not user-meaningful), and gives an explicit error when
    # someone sends an inconsistent (data_type, register_count) pair.
    is_enron = False
    if body.register_block_id is not None:
        is_enron = _block_addressing_mode(db, body.register_block_id) in (
            "ENRON_HOLDING", "ENRON_INPUT",
        )
    resolved_rc = _resolve_register_count(
        body.data_type, body.register_count, is_enron_block=is_enron,
    )

    _validate_addressing(
        db,
        device_id=body.device_id,
        function_code=body.function_code,
        address=body.address,
        register_count=resolved_rc,
        register_block_id=body.register_block_id,
        exclude_tag_id=None,
        is_enron_block=is_enron,
    )
    payload = body.model_dump()
    payload["register_count"] = resolved_rc
    try:
        new_id = db.execute(
            text("""
                INSERT INTO tags (
                    device_id, register_block_id, name, description,
                    data_type, byte_order, function_code,
                    address, register_count,
                    engineering_unit_id, engineering_unit, scale, "offset",
                    min_value, max_value, decimal_places,
                    is_heartbeat, heartbeat_max_stale_sec,
                    named_set_id, writable
                )
                VALUES (
                    :device_id, :register_block_id, :name, :description,
                    :data_type, :byte_order, :function_code,
                    :address, :register_count,
                    :engineering_unit_id, :engineering_unit, :scale, :offset,
                    :min_value, :max_value, :decimal_places,
                    :is_heartbeat, :heartbeat_max_stale_sec,
                    :named_set_id, :writable
                )
                RETURNING id
            """),
            payload,
        ).scalar_one()
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "tag")

    return get_tag(new_id, db)


@router.patch("/tags/{tag_id}", response_model=TagResponse)
def update_tag(
    tag_id: int,
    body: TagUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "no fields to update")

    # If any address-relevant field is being touched, re-validate the
    # resulting tag against the rest. Cosmetic edits (description, scale,
    # engineering_unit, min/max, enabled) skip this — they can't introduce
    # overlap or block-fit issues.
    address_fields = {
        "address", "register_count", "function_code",
        "register_block_id", "data_type",
    }
    if address_fields & updates.keys():
        existing = db.execute(
            text("""
                SELECT device_id, function_code, address, register_count,
                       register_block_id, data_type
                FROM tags WHERE id = :id
            """),
            {"id": tag_id},
        ).mappings().first()
        if not existing:
            raise HTTPException(404, f"tag {tag_id} not found")
        merged = {
            **dict(existing),
            **{
                k: v for k, v in updates.items()
                if k in address_fields | {"device_id"}
            },
        }
        is_enron = False
        if merged["register_block_id"] is not None:
            is_enron = _block_addressing_mode(db, merged["register_block_id"]) in (
                "ENRON_HOLDING", "ENRON_INPUT",
            )
        # If the client changed data_type or sent register_count=None, run
        # the resolver. Otherwise honour what they sent.
        if "data_type" in updates or "register_count" in updates:
            resolved_rc = _resolve_register_count(
                merged["data_type"],
                merged.get("register_count"),
                is_enron_block=is_enron,
            )
            merged["register_count"] = resolved_rc
            updates["register_count"] = resolved_rc
        _validate_addressing(
            db,
            device_id=merged["device_id"],
            function_code=merged["function_code"],
            address=merged["address"],
            register_count=merged["register_count"],
            register_block_id=merged["register_block_id"],
            exclude_tag_id=tag_id,
            is_enron_block=is_enron,
        )

    set_clauses = ", ".join(f"{sql_col(k)} = :{k}" for k in updates)
    updates["id"] = tag_id

    try:
        result = db.execute(
            text(f"UPDATE tags SET {set_clauses} WHERE id = :id"),
            updates,
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"tag {tag_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        handle_integrity_error(e, "tag")

    return get_tag(tag_id, db)


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: int, db: Annotated[Session, Depends(get_session)]):
    try:
        result = db.execute(
            text("DELETE FROM tags WHERE id = :id"),
            {"id": tag_id},
        )
        if result.rowcount == 0:
            raise HTTPException(404, f"tag {tag_id} not found")
        db.commit()
    except IntegrityError as e:
        db.rollback()
        # tag_values is a hypertable referencing tags; can't easily cascade.
        raise HTTPException(
            409,
            f"tag {tag_id} cannot be deleted because historical values reference it. "
            "Disable it instead with PATCH enabled=false.",
        )


# ---------------------------------------------------------------------------
# Bulk create (Phase 6 enhancement — CSV import)
# ---------------------------------------------------------------------------

class BulkTagResult(BaseModel):
    """One per row of the input — what happened.

    Phase 11 — upsert semantics. `action` describes the outcome:
       created — INSERTed a new tag (no name match found)
       updated — UPDATEed an existing tag (matched by device_id + name)
       error   — neither, see `error` for the reason
    """
    row: int
    tag_id: int | None = None
    name: str | None = None
    action: str | None = None        # "created" | "updated" | "error"
    error: str | None = None


class BulkTagsRequest(BaseModel):
    tags: list[TagCreate]


@router.post("/tags/bulk", response_model=list[BulkTagResult])
def bulk_create_tags(
    body: BulkTagsRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Create OR update many tags in one request. Per-row error reporting.

    Phase 11 — upsert by (device_id, name). If a row's name already
    exists on the target device, the existing tag is UPDATED with the
    row's fields (instead of failing with "duplicate name"). New names
    are INSERTed as before. This makes CSV export → edit → re-import a
    safe round-trip workflow.

    Each row runs in its own savepoint so a single bad row doesn't poison
    the rest. The endpoint returns a list aligned to input order so the
    client can show "row 5 was updated, row 6 errored because X."

    Validation:
      - Overlap and block-fit checks run per row, same as singular POST.
      - For UPDATE rows, the existing tag is excluded from overlap checks
        (a tag never overlaps itself). For INSERT rows at a *new* name
        but an occupied address, the request is rejected.
    """
    results: list[BulkTagResult] = []
    for idx, tag_in in enumerate(body.tags):
        try:
            with db.begin_nested():
                # Phase 11 — look up existing tag by (device_id, name).
                # Drives the upsert decision.
                existing_id = db.execute(
                    text("""
                        SELECT id FROM tags
                        WHERE device_id = :device_id AND name = :name
                    """),
                    {"device_id": tag_in.device_id, "name": tag_in.name},
                ).scalar_one_or_none()

                # Resolve register_count (handles None + Enron auto-derive)
                is_enron = False
                if tag_in.register_block_id is not None:
                    is_enron = _block_addressing_mode(
                        db, tag_in.register_block_id,
                    ) in ("ENRON_HOLDING", "ENRON_INPUT")
                resolved_rc = _resolve_register_count(
                    tag_in.data_type, tag_in.register_count,
                    is_enron_block=is_enron,
                )

                _validate_addressing(
                    db,
                    device_id=tag_in.device_id,
                    function_code=tag_in.function_code,
                    address=tag_in.address,
                    register_count=resolved_rc,
                    register_block_id=tag_in.register_block_id,
                    # Exclude self when updating so we don't flag an
                    # unchanged tag as overlapping with its own address.
                    exclude_tag_id=existing_id,
                    is_enron_block=is_enron,
                )

                payload = tag_in.model_dump()
                payload["register_count"] = resolved_rc

                if existing_id is None:
                    # INSERT path — new name, new row
                    result = db.execute(
                        text("""
                            INSERT INTO tags (
                                device_id, register_block_id, name, description,
                                data_type, byte_order, function_code,
                                address, register_count,
                                engineering_unit_id, engineering_unit, scale, "offset",
                                min_value, max_value, decimal_places, named_set_id
                            ) VALUES (
                                :device_id, :register_block_id, :name, :description,
                                :data_type, :byte_order, :function_code,
                                :address, :register_count,
                                :engineering_unit_id, :engineering_unit, :scale, :offset,
                                :min_value, :max_value, :decimal_places, :named_set_id
                            )
                            RETURNING id
                        """),
                        payload,
                    )
                    new_id = result.scalar_one()
                    results.append(BulkTagResult(
                        row=idx, tag_id=new_id, name=tag_in.name,
                        action="created",
                    ))
                else:
                    # UPDATE path — same name on same device, refresh fields.
                    # We deliberately don't touch is_heartbeat, named_set_id,
                    # heartbeat_max_stale_sec — those are typically configured
                    # via UI and not present in a CSV round-trip. Same for
                    # writable.
                    payload["id"] = existing_id
                    db.execute(
                        text("""
                            UPDATE tags SET
                                register_block_id = :register_block_id,
                                description = :description,
                                data_type = :data_type,
                                byte_order = :byte_order,
                                function_code = :function_code,
                                address = :address,
                                register_count = :register_count,
                                engineering_unit_id = :engineering_unit_id,
                                engineering_unit = :engineering_unit,
                                scale = :scale,
                                "offset" = :offset,
                                min_value = :min_value,
                                max_value = :max_value,
                                decimal_places = :decimal_places,
                                named_set_id = :named_set_id
                            WHERE id = :id
                        """),
                        payload,
                    )
                    results.append(BulkTagResult(
                        row=idx, tag_id=existing_id, name=tag_in.name,
                        action="updated",
                    ))
        except HTTPException as he:
            # _validate_addressing raises HTTPException with .detail — surface
            # that message verbatim so users see "address 7002 overlaps with
            # SMOKE_MOLE_02" instead of a generic "addressing conflict."
            results.append(BulkTagResult(
                row=idx, name=tag_in.name, action="error",
                error=str(he.detail),
            ))
        except IntegrityError as e:
            msg = str(e.orig).split("\n")[0] if hasattr(e, "orig") else str(e)
            results.append(BulkTagResult(
                row=idx, name=tag_in.name, action="error", error=msg,
            ))
        except Exception as e:
            results.append(BulkTagResult(
                row=idx, name=tag_in.name, action="error", error=str(e),
            ))
    db.commit()
    return results


# ---------------------------------------------------------------------------
# Bulk delete (Phase 6 enhancement)
# ---------------------------------------------------------------------------

class BulkDeleteRequest(BaseModel):
    tag_ids: list[int]


class BulkDeleteResult(BaseModel):
    tag_id: int
    success: bool
    error: str | None = None


@router.post("/tags/bulk-delete", response_model=list[BulkDeleteResult])
def bulk_delete_tags(
    body: BulkDeleteRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Delete many tags in one request. Per-row error reporting.

    Same nested-savepoint pattern as /api/tags/bulk — tags with historical
    values in the tag_values hypertable get a clear "disable instead" error
    while other tags in the batch still succeed.
    """
    results: list[BulkDeleteResult] = []
    for tid in body.tag_ids:
        try:
            with db.begin_nested():
                result = db.execute(
                    text("DELETE FROM tags WHERE id = :id"),
                    {"id": tid},
                )
                if result.rowcount == 0:
                    results.append(BulkDeleteResult(
                        tag_id=tid, success=False, error="not found",
                    ))
                    continue
            results.append(BulkDeleteResult(tag_id=tid, success=True))
        except IntegrityError:
            # tag_values is a hypertable referencing tags; deletion is blocked
            # by FK if any samples exist. Tell the user to disable instead.
            results.append(BulkDeleteResult(
                tag_id=tid, success=False,
                error="historical values reference this tag; disable it instead",
            ))
        except Exception as e:
            results.append(BulkDeleteResult(
                tag_id=tid, success=False, error=str(e),
            ))
    db.commit()
    return results

# ---------------------------------------------------------------------------
# Phase 8.2 — per-tag group membership management
#
# Groups are orthogonal: a tag can belong to many groups. These endpoints
# manage the tag_group_memberships join table from the tag's side. The
# /api/groups endpoints handle group CRUD; this is the "which groups is
# this tag in?" side.
# ---------------------------------------------------------------------------


class TagGroupsRequest(BaseModel):
    """Replace the full set of groups for a tag.

    Idempotent: calling with the same group_ids twice is a no-op. Pass an
    empty list to remove all memberships.
    """
    group_ids: list[int]


@router.put("/tags/{tag_id}/groups", response_model=list[int])
def set_tag_groups(
    tag_id: int,
    body: TagGroupsRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Replace this tag's group memberships with the provided set.

    Returns the resulting list of group_ids. Validates that the tag and
    all referenced groups exist; any unknown group_id raises 400 with the
    list of missing ids.
    """
    # Verify the tag exists
    tag_exists = db.execute(
        text("SELECT 1 FROM tags WHERE id = :id"),
        {"id": tag_id},
    ).scalar()
    if not tag_exists:
        raise HTTPException(404, f"Tag {tag_id} not found")

    # Verify all requested groups exist (single query, returns the set actually present)
    if body.group_ids:
        found = set(db.execute(
            text("SELECT id FROM groups WHERE id = ANY(:ids)"),
            {"ids": body.group_ids},
        ).scalars().all())
        missing = [g for g in body.group_ids if g not in found]
        if missing:
            raise HTTPException(400, f"Unknown group_id(s): {missing}")

    # Replace memberships atomically — delete all, insert new set.
    db.execute(
        text("DELETE FROM tag_group_memberships WHERE tag_id = :tag_id"),
        {"tag_id": tag_id},
    )
    if body.group_ids:
        db.execute(
            text("""
                INSERT INTO tag_group_memberships (tag_id, group_id)
                SELECT :tag_id, unnest(:group_ids)
                ON CONFLICT DO NOTHING
            """),
            {"tag_id": tag_id, "group_ids": body.group_ids},
        )
    db.commit()

    # Return the resulting set
    return list(db.execute(
        text("SELECT group_id FROM tag_group_memberships WHERE tag_id = :tag_id "
             "ORDER BY group_id"),
        {"tag_id": tag_id},
    ).scalars().all())


@router.get("/tags/{tag_id}/groups", response_model=list[int])
def get_tag_groups(tag_id: int, db: Annotated[Session, Depends(get_session)]):
    """Return the list of group_ids this tag is a member of."""
    tag_exists = db.execute(
        text("SELECT 1 FROM tags WHERE id = :id"),
        {"id": tag_id},
    ).scalar()
    if not tag_exists:
        raise HTTPException(404, f"Tag {tag_id} not found")
    return list(db.execute(
        text("SELECT group_id FROM tag_group_memberships WHERE tag_id = :tag_id "
             "ORDER BY group_id"),
        {"tag_id": tag_id},
    ).scalars().all())
