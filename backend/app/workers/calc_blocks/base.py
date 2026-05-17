"""Phase 15.1 - Calc block base contract.

Every calc block subclasses `BaseBlock` and registers itself via
`register_block(...)`. The registry is consulted by the
`calc_evaluator` worker each tick to evaluate every enabled calc tag.

A block declares:
  - `CODE`: stable identifier matching calc_block_types.code
  - `inputs()`: which tag_ids this block reads (extracted from
    block_config so the worker can build the dependency graph)
  - `evaluate()`: pure function (input_values, block_config) ->
    (value, quality)
  - `validate_config()`: raised at save time to reject bad configs

The contract is intentionally narrow so blocks are easy to write,
test, and combine. State (rolling buffers, edge detection memory,
etc.) is NOT carried inside the block — stateful blocks fetch their
history from tag_values at evaluate time.

Quality follows the same convention as Modbus reads:
  - 192 (GOOD_NON_SPECIFIC): clean value
  - 0..127: BAD — output should not be used
  - 64..127: UNCERTAIN — value present but caller should treat with care
The convention: if ANY input is below GOOD threshold, the output
quality drops to the worst input quality. Blocks override this if
they have block-specific semantics (e.g. voting blocks tolerate
some bad inputs).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


GOOD_QUALITY = 128
GOOD_NON_SPECIFIC = 192
BAD_QUALITY = 0


@dataclass
class InputSample:
    """One input value supplied to a block's evaluate(). Mirrors the
    shape of a tag_values row but only carries the fields blocks need."""
    tag_id: int
    value: float | None       # None when no recent good value exists
    quality: int              # 0..255 OPC-style quality byte


@dataclass
class BlockResult:
    value: float | None
    quality: int


class BaseBlock(ABC):
    """Abstract base class for all calc blocks."""

    CODE: str  # subclasses set this; matches calc_block_types.code

    @classmethod
    @abstractmethod
    def inputs(cls, block_config: dict[str, Any]) -> list[int]:
        """Return the tag_ids this block reads. Used by the worker
        to fetch the right input samples and to detect dependency
        cycles at save time."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def validate_config(cls, block_config: dict[str, Any]) -> None:
        """Raise ValueError with a human-readable message if the
        config is malformed. Called at API save time so operators
        get immediate feedback."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def evaluate(
        cls,
        block_config: dict[str, Any],
        inputs: list[InputSample],
    ) -> BlockResult:
        """Compute the block's output given the latest input samples.
        Pure function — no I/O, no side effects, no state."""
        raise NotImplementedError

    @staticmethod
    def worst_input_quality(inputs: list[InputSample]) -> int:
        """Helper: lowest quality across all inputs. Default policy
        for blocks that have no quality-specific semantics."""
        if not inputs:
            return BAD_QUALITY
        return min(s.quality for s in inputs)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BLOCK_REGISTRY: dict[str, type[BaseBlock]] = {}


def register_block(cls: type[BaseBlock]) -> type[BaseBlock]:
    """Decorator or direct call to register a block class. Used at
    module import time by the per-block files in calc_blocks/."""
    if not getattr(cls, "CODE", None):
        raise ValueError(f"Block class {cls.__name__} has no CODE attribute")
    if cls.CODE in BLOCK_REGISTRY:
        # Re-registration is fine (idempotent during dev with hot reload)
        # but emit a warning so accidental name collisions are visible.
        import logging
        logging.getLogger("calc_evaluator").warning(
            "Block %s re-registered (was %s)", cls.CODE,
            BLOCK_REGISTRY[cls.CODE].__name__,
        )
    BLOCK_REGISTRY[cls.CODE] = cls
    return cls


def get_block(code: str) -> type[BaseBlock] | None:
    """Look up a block class by its CODE. Returns None for unknown
    block types so the worker can skip them gracefully."""
    return BLOCK_REGISTRY.get(code)


def known_block_codes() -> list[str]:
    """Codes currently registered. Used by the API to report which
    block types are evaluable in this build."""
    return sorted(BLOCK_REGISTRY.keys())
