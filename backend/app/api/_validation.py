"""Shared validation helpers — pure functions that return data, no exceptions.

Used by:
  * app/api/diagnostics.py — lists all currently-problematic rows
  * app/api/tags.py        — rejects new/modified tags that would create problems

Keeping these here means the rules of what "valid" means live in one file.
Both consumers agree on the definition.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def find_tag_overlaps(
    db: Session,
    device_id: int,
    function_code: int,
    address: int,
    register_count: int,
    exclude_tag_id: int | None = None,
) -> list[dict]:
    """Return existing tags whose address range overlaps the given range.

    Two tags overlap if they're on the same device, same Modbus function code,
    and their `[address, address + register_count)` ranges intersect. Different
    function codes (HR vs IR vs coils vs DI) live in independent address spaces
    so they never overlap.

    `exclude_tag_id` lets PATCH check overlap against everything *except* the
    row being updated — otherwise updating any tag would "overlap with itself".
    """
    sql = """
        SELECT id, name, address, register_count, function_code
        FROM tags
        WHERE device_id = :device_id
          AND function_code = :fc
          AND address < :end_addr
          AND address + register_count > :start_addr
    """
    params: dict = {
        "device_id": device_id,
        "fc": function_code,
        "start_addr": address,
        "end_addr": address + register_count,
    }
    if exclude_tag_id is not None:
        sql += " AND id <> :exclude_id"
        params["exclude_id"] = exclude_tag_id
    sql += " ORDER BY address"
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


def check_tag_fits_block(
    db: Session,
    register_block_id: int,
    function_code: int,
    address: int,
    register_count: int,
) -> str | None:
    """Return None if the tag fits its declared block, else a human-readable problem.

    The block defines `[start_address, start_address + count)` on a specific
    function_code. The tag must:
      1. Use the same function_code as the block.
      2. Start at or after block.start_address.
      3. End at or before block.start_address + block.count.

    If the block doesn't exist, this returns None — the FK constraint will
    surface that error at INSERT time with a clearer message than we could
    produce here.
    """
    row = db.execute(
        text("""
            SELECT function_code, start_address, count, name
            FROM register_blocks
            WHERE id = :id
        """),
        {"id": register_block_id},
    ).mappings().first()
    if not row:
        return None

    if function_code != row["function_code"]:
        return (
            f"tag function_code={function_code} doesn't match "
            f"block {row['name']!r} function_code={row['function_code']}"
        )
    if address < row["start_address"]:
        return (
            f"tag address={address} is below block {row['name']!r} "
            f"start_address={row['start_address']}"
        )
    tag_end = address + register_count
    block_end = row["start_address"] + row["count"]
    if tag_end > block_end:
        return (
            f"tag spans past block {row['name']!r} end "
            f"(tag end={tag_end}, block end={block_end})"
        )
    return None
