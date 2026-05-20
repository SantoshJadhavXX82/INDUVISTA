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
    CONFIG_SCHEMA: dict = {}   # ← add this line; populated by install_schemas()
    
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

class StatefulBlock(BaseBlock):
    """Block that persists state across evaluation cycles.

    The worker fetches state from calc_block_state before each
    evaluation, passes it to evaluate() along with wall-clock time,
    and persists the new_state returned by the block.

    Subclass evaluate signature:
        evaluate(cls, cfg, samples, state, now_wall)
            -> (BlockResult, new_state_dict)

    new_state_dict must be JSON-serializable.
    """
    STATEFUL = True

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        raise NotImplementedError("StatefulBlock subclass must implement evaluate")

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


# ---------------------------------------------------------------------------
# Operand-spec resolver (Phase 17.0c)
# ---------------------------------------------------------------------------
#
# Every block input may be either a tag reference or a constant. Three
# config shapes are accepted, in priority order:
#
#   1. Bare positive int        — legacy tag-id form (still written by older
#      e.g.  5                    callers and existing DB rows)
#
#   2. Object with 'tag' key    — explicit tag form (frontend writes this)
#      e.g.  {"tag": 5}
#
#   3. Object with 'value' key  — constant form
#      e.g.  {"value": 3.14}
#
# The resolver below normalizes any of these to (tag_id, constant_value),
# with exactly one non-None. Blocks call it from validate_config() and
# from evaluate(), and the worker uses operand_tag_id() to populate the
# inputs() list (constants don't need fetching).
#
# Lists: every aggregation/selection/logical block that takes an `inputs`
# list of operands uses resolve_operand_spec() per item, so mixed
# `[5, {"value": 2.5}, {"tag": 8}]` lists work the same way.


def resolve_operand_spec(spec: Any) -> tuple[int | None, float | None]:
    """Decode an operand spec into (tag_id, const_value).

    Exactly one of the returned values is non-None on success.
    Raises ValueError on malformed input.
    """
    # Booleans are ints in Python — guard explicitly so we don't accept
    # True/False as tag ids.
    if isinstance(spec, bool):
        raise ValueError(
            "operand must not be a boolean; "
            "use {value: 0} or {value: 1} for boolean constants"
        )
    if isinstance(spec, int):
        if spec <= 0:
            raise ValueError(f"tag id {spec!r} must be a positive integer")
        return spec, None
    if isinstance(spec, float):
        # Some legacy DB rows stored numeric specs as floats. We
        # accept them defensively:
        #   positive integer-valued (e.g. 1334.0)  → tag id 1334
        #   anything else (1.5, -1.0, 0.0, NaN)    → constant
        # Note: WEIGHTED_AVG's weights need ALL bare numbers treated
        # as constants — that block uses its own helper that overrides
        # this heuristic. For aggregation/selection/etc. input lists,
        # legacy float-encoded tag ids round-trip cleanly here.
        import math
        if math.isfinite(spec) and spec > 0 and spec.is_integer():
            return int(spec), None
        if not math.isfinite(spec):
            raise ValueError(f"operand float must be finite, got {spec!r}")
        return None, float(spec)
    if isinstance(spec, dict):
        if "tag" in spec:
            tag = spec["tag"]
            if (not isinstance(tag, int)) or isinstance(tag, bool) or tag <= 0:
                raise ValueError(
                    f"'tag' must be a positive int, got {tag!r}"
                )
            return tag, None
        if "value" in spec:
            val = spec["value"]
            if (not isinstance(val, (int, float))) or isinstance(val, bool):
                raise ValueError(
                    f"'value' must be a number, got {type(val).__name__}"
                )
            return None, float(val)
        raise ValueError(
            f"operand object needs 'tag' or 'value' key; "
            f"got keys {list(spec.keys())}"
        )
    raise ValueError(
        f"operand must be int (tag id), {{'tag': id}}, or {{'value': number}}; "
        f"got {type(spec).__name__}"
    )


def validate_operand_spec(name: str, spec: Any) -> None:
    """Raise ValueError if spec is malformed, prefixing the message
    with the field name for easier debugging."""
    try:
        resolve_operand_spec(spec)
    except ValueError as e:
        raise ValueError(f"{name}: {e}")


def operand_tag_id(spec: Any) -> int | None:
    """If spec resolves to a tag, return its id; if a constant, None.
    Used by inputs() to populate the worker's fetch list."""
    tag, _ = resolve_operand_spec(spec)
    return tag


def resolve_operand_value(
    spec: Any, sample: InputSample | None,
) -> tuple[float | None, int]:
    """Resolve an operand to (value, quality).

    For tag operands, sample must be the InputSample corresponding to
    the tag id from operand_tag_id(spec). For constant operands, sample
    is ignored (pass None).

    Returns (None, sample.quality) when a tag sample is BAD. Always
    returns a tuple — never raises for runtime BAD-quality conditions.
    """
    tag, const = resolve_operand_spec(spec)
    if tag is not None:
        if sample is None:
            raise RuntimeError(
                f"tag operand id={tag} has no sample provided "
                "(worker bug: inputs() and evaluate() disagree)"
            )
        if sample.quality < GOOD_QUALITY or sample.value is None:
            return None, sample.quality
        return float(sample.value), sample.quality
    return float(const), GOOD_NON_SPECIFIC


def collect_list_tag_ids(specs: list[Any]) -> list[int]:
    """Helper for `inputs()` of list-mode blocks. Returns the tag ids
    among a list of operand specs, in order, skipping constants. The
    block's evaluate() then matches samples to spec positions using
    iter_list_operand_values."""
    out: list[int] = []
    for s in specs:
        tag = operand_tag_id(s)
        if tag is not None:
            out.append(tag)
    return out


def iter_list_operand_values(
    specs: list[Any], samples: list[InputSample],
) -> tuple[list[float] | None, int]:
    """Resolve a list of operand specs against a list of InputSamples
    (which only covers the tag-typed operands, in order). Returns
    (values, worst_quality) — values is None if any tag is BAD.

    Use this in aggregation/selection/logical block evaluators."""
    sample_idx = 0
    out: list[float] = []
    worst_q = GOOD_NON_SPECIFIC
    for spec in specs:
        tag, const = resolve_operand_spec(spec)
        if tag is not None:
            if sample_idx >= len(samples):
                raise RuntimeError(
                    "tag operand has no sample (worker bug: inputs() shorter than tag count)"
                )
            s = samples[sample_idx]
            sample_idx += 1
            if s.quality < GOOD_QUALITY or s.value is None:
                return None, s.quality
            out.append(float(s.value))
            worst_q = min(worst_q, s.quality)
        else:
            out.append(float(const))
    return out, worst_q
