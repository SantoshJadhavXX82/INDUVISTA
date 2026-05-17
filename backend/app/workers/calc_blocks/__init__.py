"""Calc block registry init.

Importing this package side-effects the BLOCK_REGISTRY: each block
module calls register_block(...) at import time. The calc_evaluator
worker imports this package on startup and then has every available
block ready to dispatch.

Add new blocks by:
  1. Creating a new file (e.g. avg_of.py) that defines a BaseBlock
     subclass and calls register_block(SubclassName).
  2. Adding an import here so it loads when the package loads.
  3. Adding a row in calc_block_types with is_evaluable=true via
     a migration.
"""

from app.workers.calc_blocks import base  # noqa: F401
from app.workers.calc_blocks import sum_of  # noqa: F401

from app.workers.calc_blocks.base import (
    BLOCK_REGISTRY, get_block, known_block_codes,
    BaseBlock, BlockResult, InputSample,
)

__all__ = [
    "BLOCK_REGISTRY", "get_block", "known_block_codes",
    "BaseBlock", "BlockResult", "InputSample",
]
