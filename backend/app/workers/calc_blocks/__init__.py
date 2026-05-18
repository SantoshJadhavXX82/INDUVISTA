"""Calc block registry init.

Importing this package side-effects the BLOCK_REGISTRY: each block
module calls register_block(...) at import time. After all block
modules have loaded, install_schemas() attaches a CONFIG_SCHEMA class
attribute on every registered block. The calc_evaluator worker
imports this package on startup and then has every available block
ready to dispatch, complete with schema.

Add new blocks by:
  1. Creating a new file (or class in an existing file) that defines
     a BaseBlock subclass and calls register_block(SubclassName).
  2. Adding an import here so the module loads.
  3. Adding a row in calc_block_types via a migration with the
     desired is_evaluable flag.
  4. Adding an entry to BLOCK_SCHEMAS in calc_block_schemas.py so
     the UI v2 create form can render it.
"""

from app.workers.calc_blocks import base  # noqa: F401
from app.workers.calc_blocks import sum_of  # noqa: F401
from app.workers.calc_blocks import aggregation_tier_a  # noqa: F401  (Phase 15.2)
from app.workers.calc_blocks import selection_tier_b   # noqa: F401  (Phase 15.3)
from app.workers.calc_blocks import conditional_logic_tier_c  # noqa: F401  (Phase 15.4a)
from app.workers.calc_blocks import stateful_tier_d   # noqa: F401  (Phase 15.5)
from app.workers.calc_blocks import arithmetic_tier_e  # noqa: F401  (Phase 15.4b)

from app.workers.calc_blocks import calc_block_schemas  # noqa: F401  (Phase 16.0a)

from app.workers.calc_blocks.base import (
    BLOCK_REGISTRY, get_block, known_block_codes,
    BaseBlock, StatefulBlock, BlockResult, InputSample,
)

# Attach CONFIG_SCHEMA to each registered block. Must happen AFTER
# every block module has imported.
calc_block_schemas.install_schemas()


__all__ = [
    "BLOCK_REGISTRY", "get_block", "known_block_codes",
    "BaseBlock", "StatefulBlock", "BlockResult", "InputSample",
]
