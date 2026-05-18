"""Phase 15.2 - Calc definitions CRUD API.

Endpoints under /api/calc:

  GET    /api/calc/block-types               - list registered block types
  GET    /api/calc/definitions               - list calc definitions (with stats)
  GET    /api/calc/definitions/{id}          - one definition
  GET    /api/calc/definitions/{id}/stats    - full execution stats row
  POST   /api/calc/definitions               - create
  PATCH  /api/calc/definitions/{id}          - update (rate, type, config, enabled)
  DELETE /api/calc/definitions/{id}          - delete

15.2 additions vs 15.1:
  - execution_rate_ms field on create/update/response, validated
    against the allowed industrial rate set
  - execution stats joined into list/detail responses
  - dedicated /stats endpoint for ops/admin views
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.workers.calc_blocks import get_block, BLOCK_REGISTRY


router = APIRouter(prefix="/api/calc", tags=["calc"])


# Mirror of the CHECK constraint in migration 0035. Keep these in sync.
ALLOWED_EXECUTION_RATES_MS = (
    100, 250, 500, 1000, 5000, 10000, 30000,
    60000, 300000, 900000, 3600000,
)


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
    has_registry_entry: bool
    created_at: datetime
    updated_at: datetime


class ExecutionStatsResponse(BaseModel):
    calc_def_id: int
    last_executed_at: datetime | None
    last_duration_ms: float | None
    last_status: str
    last_error_message: str | None
    next_scheduled_at: datetime | None
    consecutive_overruns: int
    consecutive_errors: int
    total_executions: int
    total_overruns: int
    total_errors: int
    total_skips: int


class CalcDefinitionResponse(BaseModel):
    id: int
    tag_id: int
    tag_name: str | None
    block_type: str
    block_config: dict[str, Any]
    enabled: bool
    execution_rate_ms: int
    created_at: datetime
    updated_at: datetime
    # Joined stats (may be None for never-executed defs)
    last_executed_at: datetime | None = None
    last_duration_ms: float | None = None
    last_status: str | None = None
    total_executions: int = 0
    total_overruns: int = 0
    total_errors: int = 0


class CalcDefinitionCreate(BaseModel):
    tag_id: int
    block_type: str = Field(..., min_length=1, max_length=64)
    block_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    execution_rate_ms: int = 1000

    @field_validator("execution_rate_ms")
    @classmethod
    def _check_rate(cls, v: int) -> int:
        if v not in ALLOWED_EXECUTION_RATES_MS:
            raise ValueError(
                f"execution_rate_ms must be one of {ALLOWED_EXECUTION_RATES_MS}, "
                f"got {v}"
            )
        return v


class CalcDefinitionUpdate(BaseModel):
    block_type: str | None = Field(None, min_length=1, max_length=64)
    block_config: dict[str, Any] | None = None
    enabled: bool | None = None
    execution_rate_ms: int | None = None

    @field_validator("execution_rate_ms")
    @classmethod
    def _check_rate(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v not in ALLOWED_EXECUTION_RATES_MS:
            raise ValueError(
                f"execution_rate_ms must be one of {ALLOWED_EXECUTION_RATES_MS}, "
                f"got {v}"
            )
        return v


# ---------------------------------------------------------------------------
# Validation helpers (carried over from Phase 15.1)
# ---------------------------------------------------------------------------

def _validate_block(
    db: Session,
    block_type: str,
    block_config: dict[str, Any],
    self_tag_id: int,
    self_calc_id: int | None,
) -> None:
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
    rows = db.execute(text("""
        SELECT id, tag_id, block_type, block_config
        FROM calc_definitions
        WHERE enabled = true
    """)).mappings().all()

    adj: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        if self_calc_id is not None and r["id"] == self_calc_id:
            continue
        cls = get_block(r["block_type"])
        if cls is None:
            continue
        cfg = r["block_config"] or {}
        try:
            for inp in cls.inputs(cfg):
                adj[inp].append(r["tag_id"])
        except Exception:
            continue

    for inp in new_inputs:
        adj[inp].append(new_tag_id)

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
# Calc definition endpoints (with joined stats)
# ---------------------------------------------------------------------------

# Joined SELECT used by both list and detail. LEFT JOIN to stats so
# never-executed defs return null stats rather than disappearing.
_LIST_SQL = """
    SELECT cd.id, cd.tag_id, t.name AS tag_name, cd.block_type,
           cd.block_config, cd.enabled, cd.execution_rate_ms,
           cd.created_at, cd.updated_at,
           ces.last_executed_at, ces.last_duration_ms,
           COALESCE(ces.last_status, 'pending') AS last_status,
           COALESCE(ces.total_executions, 0) AS total_executions,
           COALESCE(ces.total_overruns, 0)   AS total_overruns,
           COALESCE(ces.total_errors, 0)     AS total_errors
    FROM calc_definitions cd
    LEFT JOIN tags t ON t.id = cd.tag_id
    LEFT JOIN calc_execution_stats ces ON ces.calc_def_id = cd.id
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


@router.get(
    "/definitions/{def_id}/stats",
    response_model=ExecutionStatsResponse,
)
def get_stats(
    def_id: int,
    db: Annotated[Session, Depends(get_session)],
):
    """Full execution-stats row. Returns 404 if the def has never run
    (and therefore no stats row exists yet)."""
    exists = db.execute(
        text("SELECT id FROM calc_definitions WHERE id = :id"),
        {"id": def_id},
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, f"Calc definition {def_id} not found")

    row = db.execute(
        text("""
            SELECT calc_def_id, last_executed_at, last_duration_ms,
                   last_status, last_error_message, next_scheduled_at,
                   consecutive_overruns, consecutive_errors,
                   total_executions, total_overruns, total_errors,
                   total_skips
            FROM calc_execution_stats WHERE calc_def_id = :id
        """),
        {"id": def_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(
            404,
            f"Calc definition {def_id} has not executed yet; no stats row exists."
        )
    return dict(row)


@router.post(
    "/definitions",
    response_model=CalcDefinitionResponse,
    status_code=201,
)
def create_definition(
    body: CalcDefinitionCreate,
    db: Annotated[Session, Depends(get_session)],
):
    exists = db.execute(
        text("SELECT id FROM tags WHERE id = :id"),
        {"id": body.tag_id},
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(400, f"Tag {body.tag_id} does not exist")

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
            INSERT INTO calc_definitions
                (tag_id, block_type, block_config, enabled, execution_rate_ms)
            VALUES
                (:tag_id, :block_type, CAST(:block_config AS jsonb),
                 :enabled, :rate)
        """), {
            "tag_id": body.tag_id,
            "block_type": body.block_type,
            "block_config": _to_jsonb(body.block_config),
            "enabled": body.enabled,
            "rate": body.execution_rate_ms,
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


@router.patch(
    "/definitions/{def_id}",
    response_model=CalcDefinitionResponse,
)
def update_definition(
    def_id: int,
    body: CalcDefinitionUpdate,
    db: Annotated[Session, Depends(get_session)],
):
    existing = db.execute(
        text("""
            SELECT id, tag_id, block_type, block_config, execution_rate_ms
            FROM calc_definitions WHERE id = :id
        """),
        {"id": def_id},
    ).mappings().first()
    if existing is None:
        raise HTTPException(404, f"Calc definition {def_id} not found")

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_definition(def_id, db)

    # Re-validate the block whenever type or config change.
    if "block_type" in fields or "block_config" in fields:
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


def _to_jsonb(d: dict[str, Any]) -> str:
    import json
    return json.dumps(d)
