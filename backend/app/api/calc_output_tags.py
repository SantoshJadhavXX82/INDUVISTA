"""Phase 16.0b - Calc output tag creation API.

Purpose-built endpoint for creating tags on the Calculations device.
The existing /api/tags POST is Modbus-specific - it requires
function_code, address, and similar fields that don't apply to
calc-output tags. This endpoint:

  1. Auto-detects the Calculations device (protocol='manual')
  2. Dynamically discovers the tags table's NOT NULL columns
     and supplies type-appropriate zero defaults so the INSERT
     succeeds regardless of what Modbus-specific NOT NULL fields
     the table has
  3. Returns the new tag id + minimal info needed by the modal

Frontend uses this from CreateCalcModal when the operator picks
"Calculated tag (new, internal on Calculations device)" mode.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import SessionLocal


router = APIRouter(tags=["calc"])


# Match the existing /api/tags POST data type validation. Anything
# outside this set should be rejected with a clean 422 here too.
DataType = Literal[
    "int16", "uint16", "int32", "uint32", "int64", "uint64",
    "float32", "float64", "bool",
]


class CalcOutputTagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    data_type: DataType
    description: str | None = None


class CalcOutputTagResponse(BaseModel):
    id: int
    name: str
    data_type: str
    device_id: int


# Type-appropriate zero values for filling required NOT NULL columns
# we don't otherwise set. Maps Postgres data_type (from
# information_schema) to a literal default.
_ZERO_DEFAULTS = {
    "integer": 0,
    "bigint": 0,
    "smallint": 0,
    "numeric": 0,
    "real": 0.0,
    "double precision": 0.0,
    "text": "",
    "character varying": "",
    "character": "",
    "boolean": False,
}


# Per-column overrides that beat _ZERO_DEFAULTS. Used for Modbus
# columns with CHECK constraints rejecting zero - the schema has
# `ck_tags_function_code` enforcing valid Modbus function codes
# (typically 1..6, 15, 16). FC 3 = Read Holding Registers, universally
# valid. address=0 is left untouched since no CHECK rejects it.
_COLUMN_OVERRIDES = {
    "function_code": 3,
}


def _discover_required_columns(db, table_name: str = "tags") -> list[dict]:
    """Returns rows describing each column of the table:
       {column_name, data_type, is_nullable ('YES'|'NO'), column_default}.
    Used to figure out which NOT NULL columns we need to satisfy with
    explicit values in the INSERT."""
    return db.execute(text("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = :t AND table_schema = 'public'
        ORDER BY ordinal_position
    """), {"t": table_name}).mappings().all()


def _build_insert(explicit: dict, db) -> tuple[str, dict]:
    """Given explicit values for known columns, fill in defaults for
    any other NOT NULL no-default column. _COLUMN_OVERRIDES wins over
    _ZERO_DEFAULTS so columns with CHECK constraints (like
    function_code) get a valid value instead of zero.

    Columns we never touch:
      - 'id' (auto-generated)
      - any nullable column
      - any column with a column_default
    """
    cols = _discover_required_columns(db)
    out = dict(explicit)
    for c in cols:
        col = c["column_name"]
        if col in out or col == "id":
            continue
        if c["is_nullable"] == "YES" or c["column_default"] is not None:
            continue
        # NOT NULL with no default - need a value.
        if col in _COLUMN_OVERRIDES:
            out[col] = _COLUMN_OVERRIDES[col]
        else:
            dt = c["data_type"]
            if dt in _ZERO_DEFAULTS:
                out[col] = _ZERO_DEFAULTS[dt]
            # else: unknown type, leave it - the INSERT will fail loudly
            # and we'll know to add it to _ZERO_DEFAULTS or _COLUMN_OVERRIDES.

    columns_sql = ", ".join(out.keys())
    placeholders = ", ".join(f":{k}" for k in out.keys())
    sql = (
        f"INSERT INTO tags ({columns_sql}) "
        f"VALUES ({placeholders}) "
        f"RETURNING id, name, data_type, device_id"
    )
    return sql, out


@router.post("/api/calc/output-tags", response_model=CalcOutputTagResponse)
def create_calc_output_tag(body: CalcOutputTagCreate):
    """Create a new tag on the Calculations device for use as a calc
    block output. Auto-discovers the device by querying for
    protocol='manual'. Returns the created tag's basics."""
    with SessionLocal() as db:
        # 1. Find the Calculations pseudo-device.
        dev = db.execute(text("""
            SELECT id, name FROM devices
            WHERE protocol = 'manual' AND name ILIKE '%calc%'
            ORDER BY id LIMIT 1
        """)).mappings().first()
        if dev is None:
            # Fallback: any manual-protocol device.
            dev = db.execute(text("""
                SELECT id, name FROM devices WHERE protocol = 'manual'
                ORDER BY id LIMIT 1
            """)).mappings().first()
        if dev is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No manual-protocol device found. The Calculations "
                    "pseudo-device should have been created by an earlier "
                    "migration. Create it first or POST to /api/devices "
                    "with protocol='manual'."
                ),
            )

        # 2. Reject duplicate names on this device.
        existing = db.execute(
            text("SELECT id FROM tags WHERE name = :n AND device_id = :d"),
            {"n": body.name, "d": dev["id"]},
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A tag named '{body.name}' already exists on "
                    f"device '{dev['name']}' (#{dev['id']})."
                ),
            )

        # 3. Build a schema-aware INSERT.
        explicit = {
            "name": body.name,
            "data_type": body.data_type,
            "device_id": dev["id"],
            "description": body.description or f"Calc output ({body.name})",
        }
        sql, params = _build_insert(explicit, db)

        try:
            row = db.execute(text(sql), params).mappings().first()
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"INSERT into tags failed: {type(e).__name__}: {e}. "
                    f"If this is a NOT NULL constraint on a column we "
                    f"didn't auto-fill, paste the column name."
                ),
            )

        return CalcOutputTagResponse(**row)
