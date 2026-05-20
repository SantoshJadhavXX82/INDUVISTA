"""Phase 15.4b - Arithmetic / numerical-function blocks (Tier E).
Phase 16.0b - Add N-ary mode to ADD and MUL via discriminated config.

20 stateless blocks across 3 categories that complete the IEC 61131-3
sect 6.6.2 numerical-functions library.

  arithmetic (8):
    ADD          binary: left + right    OR   N-ary: sum(inputs)
    SUB          left - right
    MUL          binary: left * right    OR   N-ary: product(inputs)
    DIV          left / right         (BAD if right == 0)
    MOD          fmod(left, right)    (BAD if right == 0)
    POW          left ** right        (BAD if complex or non-finite)
    MIN_OF_TWO   min(left, right)
    MAX_OF_TWO   max(left, right)

  unary_math (6):
    ABS          |x|
    NEG          -x
    SQRT         sqrt(x)              (BAD if x < 0)
    FLOOR        floor(x)
    CEIL         ceil(x)
    ROUND        round(x)             (banker's rounding, Python default)

  transcendental (6):
    EXP          e^x                  (BAD on overflow)
    LN           log_e(x)             (BAD if x <= 0)
    LOG10        log_10(x)            (BAD if x <= 0)
    SIN          sin(x)               (x in radians)
    COS          cos(x)
    TAN          tan(x)               (BAD near asymptote, |result| > 1e15)

Binary blocks accept either two tag inputs or one tag + one constant,
matching the comparison-block convention from Phase 15.4a:

    {"left": <tag_id>, "right": <tag_id>}     # tag vs tag
    {"left": <tag_id>, "value": <number>}      # tag vs constant

All blocks propagate BAD-quality inputs to BAD-quality outputs. Math
domain errors produce BlockResult(value=None, quality=0), never raise
exceptions. The worker's exception handler stays available for real
bugs only.

Trig is radians. For degrees, compose with MUL: degrees * (pi/180).
"""

from __future__ import annotations

import math
from typing import Any

from app.workers.calc_blocks.base import (
    BaseBlock, BlockResult, InputSample, register_block,
    GOOD_QUALITY, GOOD_NON_SPECIFIC,
)


# ---------------------------------------------------------------------------
# Validation + operand-fetch helpers
# ---------------------------------------------------------------------------

def _validate_tag_id(name: str, x: Any) -> None:
    if not isinstance(x, int) or x <= 0:
        raise ValueError(f"{name}: tag ID {x!r} is not a positive integer")


def _validate_binary_config(cls_name: str, cfg: dict) -> None:
    """Binary blocks need 'left' (tag id) and exactly one of
    'right' (tag id) or 'value' (constant)."""
    if "left" not in cfg:
        raise ValueError(f"{cls_name} requires 'left' tag ID")
    _validate_tag_id(f"{cls_name}.left", cfg["left"])
    has_right = "right" in cfg
    has_value = "value" in cfg
    if has_right == has_value:
        raise ValueError(
            f"{cls_name} requires exactly one of 'right' (tag) or "
            f"'value' (constant); got both or neither"
        )
    if has_right:
        _validate_tag_id(f"{cls_name}.right", cfg["right"])
    else:
        if not isinstance(cfg["value"], (int, float)) or isinstance(cfg["value"], bool):
            raise ValueError(
                f"{cls_name}.value must be a number, "
                f"got {type(cfg['value']).__name__}"
            )


def _validate_unary_config(cls_name: str, cfg: dict) -> None:
    if "input" not in cfg:
        raise ValueError(f"{cls_name} requires 'input' tag ID")
    _validate_tag_id(f"{cls_name}.input", cfg["input"])


def _binary_inputs(cfg: dict) -> list[int]:
    if "right" in cfg:
        return [int(cfg["left"]), int(cfg["right"])]
    return [int(cfg["left"])]


def _unary_inputs(cfg: dict) -> list[int]:
    return [int(cfg["input"])]


def _binary_operands(
    cfg: dict, samples: list[InputSample]
) -> tuple[float | None, float | None, int]:
    """Resolves left/right operands from samples + optional constant.
    Returns (left, right, quality_for_output). If either tag operand
    is BAD, (None, None, that_sample.quality) is returned."""
    left_sample = samples[0]
    if left_sample.quality < GOOD_QUALITY or left_sample.value is None:
        return None, None, left_sample.quality

    if "right" in cfg:
        right_sample = samples[1]
        if right_sample.quality < GOOD_QUALITY or right_sample.value is None:
            return None, None, right_sample.quality
        return left_sample.value, right_sample.value, GOOD_NON_SPECIFIC

    return left_sample.value, float(cfg["value"]), GOOD_NON_SPECIFIC


def _unary_operand(samples: list[InputSample]) -> tuple[float | None, int]:
    s = samples[0]
    if s.quality < GOOD_QUALITY or s.value is None:
        return None, s.quality
    return s.value, GOOD_NON_SPECIFIC


# ---------------------------------------------------------------------------
# N-ary mode helpers (Phase 16.0b - used by ADD and MUL)
# ---------------------------------------------------------------------------

def _is_nary_mode(cfg: dict) -> bool:
    """A binary block is in N-ary mode when 'inputs' is present in cfg.
    The two modes are mutually exclusive: validate_config rejects configs
    that mix them."""
    return "inputs" in cfg


def _validate_nary_config(cls_name: str, cfg: dict,
                          min_inputs: int = 2,
                          max_inputs: int = 100) -> None:
    """Validate N-ary inputs shape (Phase 16.0b update for mixed items).

    inputs is a list of items where each item is either:
        {"tag": <positive int tag id>}
        {"value": <number>}

    The two are mutually exclusive within an item. Tag IDs must be
    unique across the list; constants can repeat freely.
    """
    inputs = cfg["inputs"]
    if not isinstance(inputs, list) or len(inputs) < min_inputs:
        raise ValueError(
            f"{cls_name} (N-ary mode) 'inputs' must be a list of "
            f">= {min_inputs} items"
        )
    if len(inputs) > max_inputs:
        raise ValueError(
            f"{cls_name} (N-ary mode) supports at most {max_inputs} inputs"
        )
    for i, item in enumerate(inputs):
        if not isinstance(item, dict):
            raise ValueError(
                f"{cls_name} inputs[{i}] must be an object "
                f"{{tag: <id>}} or {{value: <number>}}; got {type(item).__name__}"
            )
        has_tag = "tag" in item
        has_value = "value" in item
        if has_tag == has_value:
            raise ValueError(
                f"{cls_name} inputs[{i}] must have exactly one of 'tag' or 'value'"
            )
        if has_tag:
            t = item["tag"]
            if isinstance(t, bool) or not isinstance(t, int) or t <= 0:
                raise ValueError(
                    f"{cls_name} inputs[{i}].tag must be a positive integer tag ID, "
                    f"got {t!r}"
                )
        else:
            v = item["value"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(
                    f"{cls_name} inputs[{i}].value must be a number, got {v!r}"
                )
    # Tag IDs must be unique; constants can repeat.
    tag_ids = [item["tag"] for item in inputs if "tag" in item]
    if len(set(tag_ids)) != len(tag_ids):
        raise ValueError(f"{cls_name} (N-ary) tag inputs must be unique")
    # Defensive: cannot mix N-ary with top-level binary keys.
    for binary_key in ("left", "right", "value"):
        if binary_key in cfg:
            raise ValueError(
                f"{cls_name}: cannot mix N-ary ('inputs') with binary "
                f"key '{binary_key}'"
            )


def _resolve_nary_operands(
    items: list[dict], samples: list[InputSample]
) -> tuple[list[float] | None, int]:
    """Resolve each item to a numeric operand. Tag items consume from
    `samples` in order (the worker passes samples in the same order
    as inputs() returns tag IDs). Constant items contribute their
    'value' directly.

    Returns (operands, output_quality). If any tag operand is BAD,
    returns (None, that_sample.quality) so the block emits a
    BAD-quality output.
    """
    sample_iter = iter(samples)
    operands: list[float] = []
    for item in items:
        if "value" in item:
            operands.append(float(item["value"]))
        else:
            s = next(sample_iter)
            if s.quality < GOOD_QUALITY or s.value is None:
                return None, s.quality
            operands.append(float(s.value))
    return operands, GOOD_NON_SPECIFIC


def _nary_good_values(samples: list[InputSample]) -> tuple[list[float], int]:
    """LEGACY (Phase 15.4b) - kept for any callers expecting the
    homogeneous-tag-list shape. ADD and MUL now use _resolve_nary_operands.
    Returns (good_values, output_quality) for N-ary aggregation.
    Output quality is GOOD_NON_SPECIFIC if every input is GOOD;
    otherwise the worst input quality propagates (matches the
    Tier A aggregation policy)."""
    if not samples:
        return [], 0
    worst = min(s.quality for s in samples)
    if worst < GOOD_QUALITY:
        # At least one BAD/UNCERTAIN input — propagate that quality and
        # don't compute (any aggregation over partial good values would
        # be misleading for the operator).
        return [], worst
    vals = [s.value for s in samples if s.value is not None]
    return vals, GOOD_NON_SPECIFIC


# ===========================================================================
# Arithmetic (8 binary)
# ===========================================================================

class _BinaryArithBase(BaseBlock):
    """Common shell for binary arithmetic. Subclass overrides
    _compute(left, right) -> float (or raises domain-error tuple)."""
    @classmethod
    def inputs(cls, cfg):
        return _binary_inputs(cfg)

    @classmethod
    def validate_config(cls, cfg):
        _validate_binary_config(cls.CODE, cfg)


class Add(BaseBlock):
    """ADD: binary (left + right or left + value) or N-ary (sum of mixed
    tags + constants).

    Mode is determined by config shape:
      {"left": ..., "right": ...} or {"left": ..., "value": ...}  -> binary
      {"inputs": [{"tag": id} | {"value": num}, ...]}              -> N-ary

    The two are mutually exclusive; validate_config rejects mixes.
    """
    CODE = "ADD"

    @classmethod
    def inputs(cls, cfg):
        if _is_nary_mode(cfg):
            # Only tag items need fetching; constants are inline.
            return [int(item["tag"]) for item in cfg["inputs"] if "tag" in item]
        return _binary_inputs(cfg)

    @classmethod
    def validate_config(cls, cfg):
        if _is_nary_mode(cfg):
            _validate_nary_config("ADD", cfg)
        else:
            _validate_binary_config("ADD", cfg)

    @classmethod
    def evaluate(cls, cfg, samples):
        if _is_nary_mode(cfg):
            operands, q = _resolve_nary_operands(cfg["inputs"], samples)
            if operands is None:
                return BlockResult(value=None, quality=q)
            return BlockResult(value=sum(operands), quality=q)
        # Binary mode
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=l + r, quality=q)


class Sub(_BinaryArithBase):
    CODE = "SUB"
    @classmethod
    def evaluate(cls, cfg, samples):
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=l - r, quality=q)


class Mul(BaseBlock):
    """MUL: binary (left * right or left * value) or N-ary (product of
    mixed tags + constants).

    Mode determined by config shape (see Add for details)."""
    CODE = "MUL"

    @classmethod
    def inputs(cls, cfg):
        if _is_nary_mode(cfg):
            return [int(item["tag"]) for item in cfg["inputs"] if "tag" in item]
        return _binary_inputs(cfg)

    @classmethod
    def validate_config(cls, cfg):
        if _is_nary_mode(cfg):
            _validate_nary_config("MUL", cfg)
        else:
            _validate_binary_config("MUL", cfg)

    @classmethod
    def evaluate(cls, cfg, samples):
        if _is_nary_mode(cfg):
            operands, q = _resolve_nary_operands(cfg["inputs"], samples)
            if operands is None:
                return BlockResult(value=None, quality=q)
            prod = 1.0
            for v in operands:
                prod *= v
            return BlockResult(value=prod, quality=q)
        # Binary mode
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=l * r, quality=q)


class Div(_BinaryArithBase):
    CODE = "DIV"
    @classmethod
    def evaluate(cls, cfg, samples):
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        if r == 0:
            return BlockResult(value=None, quality=0)
        return BlockResult(value=l / r, quality=q)


class Mod(_BinaryArithBase):
    CODE = "MOD"
    @classmethod
    def evaluate(cls, cfg, samples):
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        if r == 0:
            return BlockResult(value=None, quality=0)
        # math.fmod is C-style: sign follows dividend. Standard for IEC.
        return BlockResult(value=math.fmod(l, r), quality=q)


class Pow(_BinaryArithBase):
    CODE = "POW"
    @classmethod
    def evaluate(cls, cfg, samples):
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        try:
            v = l ** r
        except (ValueError, OverflowError, ZeroDivisionError):
            return BlockResult(value=None, quality=0)
        if isinstance(v, complex):
            return BlockResult(value=None, quality=0)
        if not math.isfinite(v):
            return BlockResult(value=None, quality=0)
        return BlockResult(value=float(v), quality=q)


class MinOfTwo(_BinaryArithBase):
    CODE = "MIN_OF_TWO"
    @classmethod
    def evaluate(cls, cfg, samples):
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=min(l, r), quality=q)


class MaxOfTwo(_BinaryArithBase):
    CODE = "MAX_OF_TWO"
    @classmethod
    def evaluate(cls, cfg, samples):
        l, r, q = _binary_operands(cfg, samples)
        if l is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=max(l, r), quality=q)


# ===========================================================================
# Unary math (6)
# ===========================================================================

class _UnaryMathBase(BaseBlock):
    @classmethod
    def inputs(cls, cfg):
        return _unary_inputs(cfg)

    @classmethod
    def validate_config(cls, cfg):
        _validate_unary_config(cls.CODE, cfg)


class Abs(_UnaryMathBase):
    CODE = "ABS"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=abs(v), quality=q)


class Neg(_UnaryMathBase):
    CODE = "NEG"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=-v, quality=q)


class Sqrt(_UnaryMathBase):
    CODE = "SQRT"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        if v < 0:
            return BlockResult(value=None, quality=0)
        return BlockResult(value=math.sqrt(v), quality=q)


class Floor(_UnaryMathBase):
    CODE = "FLOOR"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=float(math.floor(v)), quality=q)


class Ceil(_UnaryMathBase):
    CODE = "CEIL"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=float(math.ceil(v)), quality=q)


class Round(_UnaryMathBase):
    CODE = "ROUND"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        # Python's round() is banker's rounding (half to even).
        # Operators wanting half-up can compose: FLOOR(x + 0.5).
        return BlockResult(value=float(round(v)), quality=q)


# ===========================================================================
# Transcendental (6)
# ===========================================================================

class Exp(_UnaryMathBase):
    CODE = "EXP"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        try:
            r = math.exp(v)
        except OverflowError:
            return BlockResult(value=None, quality=0)
        if not math.isfinite(r):
            return BlockResult(value=None, quality=0)
        return BlockResult(value=r, quality=q)


class Ln(_UnaryMathBase):
    CODE = "LN"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        if v <= 0:
            return BlockResult(value=None, quality=0)
        return BlockResult(value=math.log(v), quality=q)


class Log10(_UnaryMathBase):
    CODE = "LOG10"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        if v <= 0:
            return BlockResult(value=None, quality=0)
        return BlockResult(value=math.log10(v), quality=q)


class Sin(_UnaryMathBase):
    CODE = "SIN"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=math.sin(v), quality=q)


class Cos(_UnaryMathBase):
    CODE = "COS"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        return BlockResult(value=math.cos(v), quality=q)


class Tan(_UnaryMathBase):
    CODE = "TAN"
    @classmethod
    def evaluate(cls, cfg, samples):
        v, q = _unary_operand(samples)
        if v is None:
            return BlockResult(value=None, quality=q)
        r = math.tan(v)
        # tan near pi/2 + n*pi is numerically unstable; treat huge
        # magnitudes as BAD so downstream alarms don't see garbage.
        if not math.isfinite(r) or abs(r) > 1e15:
            return BlockResult(value=None, quality=0)
        return BlockResult(value=r, quality=q)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

for cls in (
    Add, Sub, Mul, Div, Mod, Pow, MinOfTwo, MaxOfTwo,
    Abs, Neg, Sqrt, Floor, Ceil, Round,
    Exp, Ln, Log10, Sin, Cos, Tan,
):
    register_block(cls)
