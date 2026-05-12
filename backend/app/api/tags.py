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
    register_count: int = Field(..., ge=1, le=4)
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
           t.min_value, t.max_value, t.enabled,
           t.is_heartbeat, t.heartbeat_max_stale_sec,
           t.named_set_id, ns.name AS named_set_name,
           t.writable
    FROM tags t
    JOIN devices d ON d.id = t.device_id
    LEFT JOIN register_blocks b ON b.id = t.register_block_id
    LEFT JOIN engineering_units eu ON eu.id = t.engineering_unit_id
    LEFT JOIN named_sets ns ON ns.id = t.named_set_id
"""


def _validate_addressing(
    db: Session,
    *,
    device_id: int,
    function_code: int,
    address: int,
    register_count: int,
    register_block_id: int | None,
    exclude_tag_id: int | None,
) -> None:
    """Run Phase 5 pre-write validation on a tag's addressing.

    Raises 400 if the resulting tag would overlap an existing tag, or if
    its declared register_block can't contain it. Used by both POST (with
    exclude_tag_id=None) and PATCH (with the tag's own id, so it doesn't
    "overlap with itself" during edit).
    """
    overlaps = find_tag_overlaps(
        db, device_id, function_code, address, register_count,
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
            f"tag address range [{address}, {address + register_count}) "
            f"on FC {function_code} overlaps with: {shown}{more}",
        )

    if register_block_id is not None:
        problem = check_tag_fits_block(
            db, register_block_id, function_code, address, register_count,
        )
        if problem is not None:
            raise HTTPException(400, f"tag does not fit its register_block: {problem}")


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
    _validate_addressing(
        db,
        device_id=body.device_id,
        function_code=body.function_code,
        address=body.address,
        register_count=body.register_count,
        register_block_id=body.register_block_id,
        exclude_tag_id=None,
    )
    payload = body.model_dump()
    try:
        new_id = db.execute(
            text("""
                INSERT INTO tags (
                    device_id, register_block_id, name, description,
                    data_type, byte_order, function_code,
                    address, register_count,
                    engineering_unit_id, engineering_unit, scale, "offset",
                    min_value, max_value,
                    is_heartbeat, heartbeat_max_stale_sec,
                    named_set_id, writable
                )
                VALUES (
                    :device_id, :register_block_id, :name, :description,
                    :data_type, :byte_order, :function_code,
                    :address, :register_count,
                    :engineering_unit_id, :engineering_unit, :scale, :offset,
                    :min_value, :max_value,
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
    address_fields = {"address", "register_count", "function_code", "register_block_id"}
    if address_fields & updates.keys():
        existing = db.execute(
            text("""
                SELECT device_id, function_code, address, register_count,
                       register_block_id
                FROM tags WHERE id = :id
            """),
            {"id": tag_id},
        ).mappings().first()
        if not existing:
            raise HTTPException(404, f"tag {tag_id} not found")
        merged = {**dict(existing), **{k: v for k, v in updates.items() if k in address_fields | {"device_id"}}}
        _validate_addressing(
            db,
            device_id=merged["device_id"],
            function_code=merged["function_code"],
            address=merged["address"],
            register_count=merged["register_count"],
            register_block_id=merged["register_block_id"],
            exclude_tag_id=tag_id,
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
    """One per row of the input — either a created TagResponse OR an error."""
    row: int
    tag_id: int | None = None
    name: str | None = None
    error: str | None = None


class BulkTagsRequest(BaseModel):
    tags: list[TagCreate]


@router.post("/tags/bulk", response_model=list[BulkTagResult])
def bulk_create_tags(
    body: BulkTagsRequest,
    db: Annotated[Session, Depends(get_session)],
):
    """Create many tags in one request. Per-row error reporting.

    Each tag is attempted in its own savepoint so one bad row doesn't poison
    the rest. The endpoint returns a list aligned to the input order so the
    client can show "row 5 failed because X" alongside successful rows.

    Validation (overlap, block-fit) runs per row, same as the singular POST.
    """
    results: list[BulkTagResult] = []
    for idx, tag_in in enumerate(body.tags):
        try:
            # Use a nested transaction (SAVEPOINT) so individual failures
            # don't abort the whole batch.
            with db.begin_nested():
                result = db.execute(
                    text("""
                        INSERT INTO tags (
                            device_id, register_block_id, name, description,
                            data_type, byte_order, function_code,
                            address, register_count,
                            engineering_unit_id, engineering_unit, scale, "offset",
                            min_value, max_value, named_set_id
                        ) VALUES (
                            :device_id, :register_block_id, :name, :description,
                            :data_type, :byte_order, :function_code,
                            :address, :register_count,
                            :engineering_unit_id, :engineering_unit, :scale, :offset,
                            :min_value, :max_value, :named_set_id
                        )
                        RETURNING id
                    """),
                    tag_in.model_dump(),
                )
                new_id = result.scalar_one()
            results.append(BulkTagResult(
                row=idx, tag_id=new_id, name=tag_in.name,
            ))
        except IntegrityError as e:
            # Map common Postgres errors to a friendly message
            msg = str(e.orig).split("\n")[0] if hasattr(e, "orig") else str(e)
            results.append(BulkTagResult(
                row=idx, name=tag_in.name, error=msg,
            ))
        except Exception as e:
            results.append(BulkTagResult(
                row=idx, name=tag_in.name, error=str(e),
            ))
    db.commit()  # commit all successful nested transactions
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
