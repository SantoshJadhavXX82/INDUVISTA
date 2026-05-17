"""Phase 15.1 - Calc definitions CRUD API.

Endpoints under /api/calc:

  GET    /api/calc/block-types         - list registered block types
                                         + their is_evaluable status
  GET    /api/calc/definitions         - list all calc definitions
  GET    /api/calc/definitions/{id}    - one definition
  POST   /api/calc/definitions         - create
  PATCH  /api/calc/definitions/{id}    - update block_type / config / enabled
  DELETE /api/calc/definitions/{id}    - delete

Validation done at save time:
  - block_type must exist in calc_block_types and be is_evaluable
  - block_config must satisfy the block class's validate_config()
  - inputs must reference existing tags
  - the resulting calc graph must remain acyclic

The actual calc evaluation happens in the calc_evaluator worker;
this API is purely for configuration.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.workers.calc_blocks import get_block, BLOCK_REGISTRY


router = APIRouter(prefix="/api/calc", tags=["calc"])


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------

class BlockTypeResponse(BaseModel):
    id: int
    code: str
    label: str
    category: str
    description: str | None
    rank: int
    is_evaluable: bool
    has_registry_entry: bool   # True if the Python registry has this code
    created_at: datetime
    updated_at: datetime


class CalcDefinitionResponse(BaseModel):
    id: int
    tag_id: int
    tag_name: str | None
    block_type: str
    block_config: dict[str, Any]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class CalcDefinitionCreate(BaseModel):
    tag_id: int
    block_type: str = Field(..., min_length=1, max_length=64)
    block_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class CalcDefinitionUpdate(BaseModel):
    # tag_id is immutable - delete + recreate if needed
    block_type: str | None = Field(None, min_length=1, max_length=64)
    block_config: dict[str, Any] | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_block(
    db: Session,
    block_type: str,
    block_config: dict[str, Any],
    self_tag_id: int,
    self_calc_id: int | None,
) -> None:
    """Raise HTTPException if the block_type / config combo can't be saved.

    Checks:
      1. block_type is registered (Python has a class) AND is_evaluable
         in the catalog (the latter mirrors alarm_rule_types pattern).
      2. block class accepts the config (validate_config didn't raise).
      3. All input tag IDs exist in the tags table.
      4. self_tag_id is not in its own inputs (trivial cycle).
      5. The full calc graph remains acyclic after this save.
    """
    block_cls = get_block(block_type)
    if block_cls is None:
        raise HTTPException(
            400,
            f"Unknown block type '{block_type}'. Registered types: "
            f"{sorted(BLOCK_REGISTRY.keys())}"
        )

    catalog_row = db.execute(
        text("SELECT is_evaluable FROM calc_block_types WHERE code = :code"),
        {"code": block_type},
    ).mappings().first()
    if catalog_row is None:
        raise HTTPException(
            400,
            f"Block type '{block_type}' is in the registry but missing "
            f"from calc_block_types catalog. Run pending migrations."
        )
    if not catalog_row["is_evaluable"]:
        raise HTTPException(
            400,
            f"Block type '{block_type}' is taxonomy-only (is_evaluable=false). "
            f"Wait for the evaluator support to ship."
        )

    try:
        block_cls.validate_config(block_config)
    except ValueError as e:
        raise HTTPException(400, f"Invalid block_config: {e}")

    inputs = block_cls.inputs(block_config)
    if self_tag_id in inputs:
        raise HTTPException(
            400,
            f"Calc cannot reference its own output tag (id={self_tag_id})."
        )

    # All inputs must exist
    if inputs:
        existing = db.execute(
            text("SELECT id FROM tags WHERE id = ANY(:ids)"),
            {"ids": inputs},
        ).scalars().all()
        missing = set(inputs) - set(existing)
        if missing:
            raise HTTPException(
                400,
                f"Input tag IDs do not exist: {sorted(missing)}"
            )

    # Cycle detection across the full graph
    if _would_introduce_cycle(db, self_tag_id, self_calc_id, inputs):
        raise HTTPException(
            409,
            f"Saving this calc would introduce a dependency cycle in the "
            f"calc graph. Choose different inputs."
        )


def _would_introduce_cycle(
    db: Session,
    new_tag_id: int,
    self_calc_id: int | None,
    new_inputs: list[int],
) -> bool:
    """Simulate the post-save graph and check for cycles.

    Builds the edge set: for every calc def, edges from each input
    tag_id to the def's output tag_id. Then DFS from each node to
    detect back-edges.
    """
    rows = db.execute(text("""
        SELECT id, tag_id, block_type, block_config
        FROM calc_definitions
        WHERE enabled = true
    """)).mappings().all()

    # adjacency: input_tag_id -> [output_tag_id, ...]
    adj: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        if self_calc_id is not None and r["id"] == self_calc_id:
            continue  # excluding the row we're replacing
        cls = get_block(r["block_type"])
        if cls is None:
            continue
        cfg = r["block_config"] or {}
        try:
            for inp in cls.inputs(cfg):
                adj[inp].append(r["tag_id"])
        except Exception:
            continue

    # Add our prospective edges
    for inp in new_inputs:
        adj[inp].append(new_tag_id)

    # DFS for cycles starting from new_tag_id
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = defaultdict(lambda: WHITE)

    def visit(node: int) -> bool:
        color[node] = GRAY
        for nxt in adj.get(node, []):
            if color[nxt] == GRAY:
                return True
            if color[nxt] == WHITE and visit(nxt):
                return True
        color[node] = BLACK
        return False

    # We only need to check whether new_tag_id participates in a cycle.
    # If reaching any GRAY ancestor is possible from new_tag_id, that's
    # a cycle through it.
    return visit(new_tag_id)


# ---------------------------------------------------------------------------
# Block types endpoint
# ---------------------------------------------------------------------------

@router.get("/block-types", response_model=list[BlockTypeResponse])
def list_block_types(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text("""
        SELECT id, code, label, category, description, rank,
               is_evaluable, created_at, updated_at
        FROM calc_block_types ORDER BY rank ASC
    """)).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["has_registry_entry"] = r["code"] in BLOCK_REGISTRY
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Calc definition endpoints
# ---------------------------------------------------------------------------

_LIST_SQL = """
    SELECT cd.id, cd.tag_id, t.name AS tag_name, cd.block_type,
           cd.block_config, cd.enabled, cd.created_at, cd.updated_at
    FROM calc_definitions cd
    LEFT JOIN tags t ON t.id = cd.tag_id
"""


@router.get("/definitions", response_model=list[CalcDefinitionResponse])
def list_definitions(db: Annotated[Session, Depends(get_session)]):
    rows = db.execute(text(_LIST_SQL + " ORDER BY cd.id")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/definitions/{def_id}", response_model=CalcDefinitionResponse)
def get_definition(
    def_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    row = db.execute(
        text(_LIST_SQL + " WHERE cd.id = :id"),
        {"id": def_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, f"Calc definition {def_id} not found")
    return dict(row)


@router.post("/definitions", response_model=CalcDefinitionResponse, status_code=201)
def create_definition(
    body: CalcDefinitionCreate,
    db: Annotated[Session, Depends(get_session)],
):
    # Tag must exist
    exists = db.execute(
        text("SELECT id FROM tags WHERE id = :id"),
        {"id": body.tag_id},
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(400, f"Tag {body.tag_id} does not exist")

    # Tag must not already have a calc definition
    has_calc = db.execute(
        text("SELECT id FROM calc_definitions WHERE tag_id = :id"),
        {"id": body.tag_id},
    ).scalar_one_or_none()
    if has_calc is not None:
        raise HTTPException(
            409,
            f"Tag {body.tag_id} already has a calc definition (id={has_calc})"
        )

    _validate_block(db, body.block_type, body.block_config,
                    self_tag_id=body.tag_id, self_calc_id=None)

    try:
        db.execute(text("""
            INSERT INTO calc_definitions (tag_id, block_type, block_config, enabled)
            VALUES (:tag_id, :block_type, CAST(:block_config AS jsonb), :enabled)
        """), {
            "tag_id": body.tag_id,
            "block_type": body.block_type,
            "block_config": _to_jsonb(body.block_config),
            "enabled": body.enabled,
        })
        db.commit()
    except Exception:
        db.rollback()
        raise

    new_id = db.execute(
        text("SELECT id FROM calc_definitions WHERE tag_id = :id"),
        {"id": body.tag_id},
    ).scalar_one()
    return get_definition(def_id=new_id, db=db)


@router.patch("/definitions/{def_id}", response_model=CalcDefinitionResponse)
def update_definition(
    def_id: int,
    body: CalcDefinitionUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text("SELECT id, tag_id, block_type, block_config FROM calc_definitions WHERE id = :id"),
        {"id": def_id},
    ).mappings().first()
    if existing is None:
        raise HTTPException(404, f"Calc definition {def_id} not found")

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_definition(def_id, db)

    # Re-validate with the merged config
    final_block_type = fields.get("block_type", existing["block_type"])
    final_config = fields.get("block_config", existing["block_config"] or {})
    _validate_block(db, final_block_type, final_config,
                    self_tag_id=existing["tag_id"], self_calc_id=def_id)

    set_clauses = []
    params: dict[str, Any] = {"id": def_id}
    for k, v in fields.items():
        if k == "block_config":
            params[k] = _to_jsonb(v)
            set_clauses.append(f"{k} = CAST(:{k} AS jsonb)")
        else:
            params[k] = v
            set_clauses.append(f"{k} = :{k}")

    try:
        db.execute(
            text(f"UPDATE calc_definitions SET {', '.join(set_clauses)} WHERE id = :id"),
            params,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_definition(def_id, db)


@router.delete("/definitions/{def_id}", status_code=204)
def delete_definition(
    def_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    exists = db.execute(
        text("SELECT id FROM calc_definitions WHERE id = :id"),
        {"id": def_id},
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, f"Calc definition {def_id} not found")

    try:
        db.execute(
            text("DELETE FROM calc_definitions WHERE id = :id"),
            {"id": def_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_jsonb(d: dict[str, Any]) -> str:
    """Serialise a Python dict for the JSONB column. SQLAlchemy/psycopg2
    accepts a str cast to ::jsonb when needed; we keep it simple by
    using json.dumps so the SQL stays portable across drivers."""
    import json
    return json.dumps(d)
