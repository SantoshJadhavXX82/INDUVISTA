"""Phase 16.0a - Calc block schemas API.

Single endpoint exposing the CONFIG_SCHEMA of every registered block.
The Phase 16.0b admin UI's create/edit form fetches this once, caches
it, and renders each block's form from the schema alone - no hardcoded
per-block UI.

Schema shape is documented in app/workers/calc_blocks/calc_block_schemas.py.
"""

from fastapi import APIRouter

from app.workers.calc_blocks import BLOCK_REGISTRY


router = APIRouter(tags=["calc"])


@router.get("/api/calc/block-schemas")
def list_block_schemas() -> dict:
    """Returns {block_code: schema_dict} for every registered block.

    Blocks without a schema (none right now after Phase 16.0a) get an
    empty dict. The frontend can still create calc_definitions for
    those by posting raw JSON, but no form will render.

    Schemas live alongside the block classes as CONFIG_SCHEMA class
    attributes, populated by calc_block_schemas.install_schemas() at
    package import time. This endpoint just surfaces them.
    """
    return {
        code: getattr(cls, "CONFIG_SCHEMA", {})
        for code, cls in BLOCK_REGISTRY.items()
    }
