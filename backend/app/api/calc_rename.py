"""Rename a computed tag.

POST /api/computed-tags/{id}/rename   body: {"name": "NewName"}

Renames the computed tag's underlying tags row. This is safe by
construction: every reference to a tag - block_config operands
({tag: id}), calc input lists, output routing, report bindings, the
flow/input-status endpoints - is by TAG ID, never by name. Renaming
therefore propagates everywhere automatically; nothing dangles.

Collision handling: tag names are unique per device
(uq_tags_device_name) and globally unique in some deployments
(tags.name UNIQUE in the baseline). Any unique violation returns 409
with a readable message instead of a 500.

Mounted in main.py:  app.include_router(calc_rename.router)
"""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import SessionLocal


router = APIRouter(tags=["calc"])


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)


@router.post("/api/computed-tags/{def_id}/rename")
def rename_computed_tag(def_id: int, body: RenameBody) -> dict:
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(400, "name must not be blank")

    with SessionLocal() as db:
        row = db.execute(text(
            "SELECT t.id, t.name, t.device_id"
            "  FROM computed_tags ct JOIN tags t ON t.id = ct.id"
            " WHERE ct.id = :id"), {"id": def_id}).mappings().first()
        if row is None:
            raise HTTPException(404, f"Computed tag {def_id} not found")

        old_name = row["name"]
        if new_name == old_name:
            return {"id": def_id, "old_name": old_name, "name": new_name,
                    "changed": False}

        try:
            db.execute(text(
                "UPDATE tags SET name = :name WHERE id = :id"),
                {"name": new_name, "id": def_id})
            db.commit()
        except Exception as e:
            db.rollback()
            msg = str(e)
            if re.search(r"unique|duplicate", msg, re.IGNORECASE):
                raise HTTPException(
                    409,
                    f"A tag named '{new_name}' already exists "
                    f"(names are unique per device). Pick another name.",
                )
            raise HTTPException(500, f"Rename failed: {type(e).__name__}: {e}")

        return {
            "id": def_id,
            "old_name": old_name,
            "name": new_name,
            "changed": True,
            "_note": ("References (block configs, outputs, reports) are by "
                      "tag ID and follow the new name automatically."),
        }
