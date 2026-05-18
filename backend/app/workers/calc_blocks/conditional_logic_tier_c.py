"""Phase 15.4a - Tier C conditional, comparison, and logical blocks.

11 blocks across three IEC 61131-3 sections:

  Conditional (sect 6.6.1):
    IF_THEN_ELSE              3-way selector driven by a boolean tag

  Comparison (sect 6.6.4):
    GT, LT, GTE, LTE          ordered comparisons
    EQ, NE                    equality with optional tolerance

  Logical (sect 6.6.3):
    AND_OF, OR_OF, XOR_OF     N-input logical operations
    NOT                       single-input inverter

Comparison blocks support two config shapes via one block code:

    {"left": <tag_id>, "right": <tag_id>}      # tag-vs-tag
    {"left": <tag_id>, "value": <number>}      # tag-vs-constant

This avoids doubling the block-type catalog with `GT` and `GT_CONST`
twins while keeping each instance's intent explicit in its config.

Boolean encoding follows the OPC UA / Modbus convention used elsewhere:
output 1.0 for TRUE, 0.0 for FALSE, stored in value_double like any
other numeric tag. Logical inputs treat >0 as TRUE, otherwise FALSE -
this matches how `bool_true` alarms read tag values today.

Quality propagation:
  IF_THEN_ELSE      condition BAD -> BAD; otherwise mirrors the chosen
                    branch's quality but caps at GOOD_NON_SPECIFIC
  Comparisons       any input BAD -> BAD; otherwise GOOD_NON_SPECIFIC
  Logical           any input BAD -> worst input quality; otherwise
                    GOOD_NON_SPECIFIC
"""

from __future__ import annotations

from typing import Any

from app.workers.calc_blocks.base import (
    BaseBlock, BlockResult, InputSample, register_block,
    GOOD_QUALITY, GOOD_NON_SPECIFIC,
)


# ---------------------------------------------------------------------------
# Shared validation helpers
# ---------------------------------------------------------------------------

def _validate_tag_id(block_name: str, x: Any) -> None:
    if not isinstance(x, int) or x <= 0:
        raise ValueError(
            f"{block_name}: tag ID {x!r} is not a positive integer"
        )


def _validate_inputs_list(
    block_name: str,
    cfg: dict[str, Any],
    min_inputs: int = 2,
    max_inputs: int = 20,
) -> None:
    if "inputs" not in cfg:
        raise ValueError(f"{block_name} requires 'inputs' in block_config")
    inputs = cfg["inputs"]
    if not isinstance(inputs, list) or len(inputs) < min_inputs:
        raise ValueError(
            f"{block_name} 'inputs' must be a list of at least "
            f"{min_inputs} tag IDs"
        )
    if len(inputs) > max_inputs:
        raise ValueError(f"{block_name} supports at most {max_inputs} inputs")
    for x in inputs:
        _validate_tag_id(block_name, x)
    if len(set(inputs)) != len(inputs):
        raise ValueError(f"{block_name} inputs must be unique")


def _all_good(samples: list[InputSample]) -> bool:
    return all(
        s.quality >= GOOD_QUALITY and s.value is not None
        for s in samples
    )


def _worst_quality(samples: list[InputSample]) -> int:
    return min((s.quality for s in samples), default=0)


# ===========================================================================
# Conditional (sect 6.6.1)
# ===========================================================================

class IfThenElse(BaseBlock):
    """3-way selector: output then_value if condition truthy, else else_value.

    Configuration:
        block_config = {
            "condition":  <tag_id>,    # >0 = TRUE
            "then_value": <tag_id>,
            "else_value": <tag_id>,
        }

    Quality: condition BAD -> output BAD. Otherwise output mirrors the
    chosen branch's quality, capped at GOOD_NON_SPECIFIC.

    Per IEC 61131-3 sect 6.6.1 SEL (extended to use tag-driven branches).
    """
    CODE = "IF_THEN_ELSE"

    @classmethod
    def inputs(cls, cfg):
        return [
            int(cfg["condition"]),
            int(cfg["then_value"]),
            int(cfg["else_value"]),
        ]

    @classmethod
    def validate_config(cls, cfg):
        for key in ("condition", "then_value", "else_value"):
            if key not in cfg:
                raise ValueError(
                    f"IF_THEN_ELSE requires '{key}' tag ID in block_config"
                )
            _validate_tag_id(f"IF_THEN_ELSE.{key}", cfg[key])

    @classmethod
    def evaluate(cls, cfg, samples):
        condition, then_s, else_s = samples

        if condition.quality < GOOD_QUALITY or condition.value is None:
            return BlockResult(value=None, quality=condition.quality)

        chosen = then_s if condition.value > 0 else else_s

        if chosen.value is None:
            return BlockResult(value=None, quality=chosen.quality)

        return BlockResult(
            value=chosen.value,
            quality=min(chosen.quality, GOOD_NON_SPECIFIC),
        )


# ===========================================================================
# Comparison (sect 6.6.4)
# ===========================================================================

class _BinaryCompareBase(BaseBlock):
    """Shared base for binary comparison blocks.

    Accepts two config shapes:
      {"left": <tag_id>, "right": <tag_id>}    -> tag-vs-tag
      {"left": <tag_id>, "value": <number>}    -> tag-vs-constant

    Exactly one of `right` or `value` must be present. inputs() returns
    just [left] for the constant form, [left, right] for tag-vs-tag.
    """

    @classmethod
    def inputs(cls, cfg):
        ids = [int(cfg["left"])]
        if "right" in cfg:
            ids.append(int(cfg["right"]))
        return ids

    @classmethod
    def validate_config(cls, cfg):
        if "left" not in cfg:
            raise ValueError(
                f"{cls.CODE} requires 'left' tag ID in block_config"
            )
        _validate_tag_id(f"{cls.CODE}.left", cfg["left"])

        has_right = "right" in cfg
        has_value = "value" in cfg
        if has_right == has_value:
            raise ValueError(
                f"{cls.CODE} requires exactly one of 'right' (tag ID) "
                f"or 'value' (numeric constant) in block_config"
            )

        if has_right:
            _validate_tag_id(f"{cls.CODE}.right", cfg["right"])
            if cfg["left"] == cfg["right"]:
                raise ValueError(
                    f"{cls.CODE}: 'left' and 'right' must be different tags"
                )
        else:
            v = cfg["value"]
            if not isinstance(v, (int, float)):
                raise ValueError(f"{cls.CODE}: 'value' must be numeric")

    @classmethod
    def _do_compare(cls, left: float, right: float) -> bool:
        raise NotImplementedError("subclass must override _do_compare")

    @classmethod
    def evaluate(cls, cfg, samples):
        left = samples[0]
        if left.quality < GOOD_QUALITY or left.value is None:
            return BlockResult(value=None, quality=left.quality)

        if len(samples) > 1:
            right = samples[1]
            if right.quality < GOOD_QUALITY or right.value is None:
                return BlockResult(value=None, quality=right.quality)
            right_val = right.value
        else:
            right_val = float(cfg["value"])

        result = cls._do_compare(left.value, right_val)
        return BlockResult(
            value=1.0 if result else 0.0,
            quality=GOOD_NON_SPECIFIC,
        )


class GreaterThan(_BinaryCompareBase):
    """left > right (or left > value)."""
    CODE = "GT"

    @classmethod
    def _do_compare(cls, left, right):
        return left > right


class LessThan(_BinaryCompareBase):
    """left < right."""
    CODE = "LT"

    @classmethod
    def _do_compare(cls, left, right):
        return left < right


class GreaterThanOrEqual(_BinaryCompareBase):
    """left >= right."""
    CODE = "GTE"

    @classmethod
    def _do_compare(cls, left, right):
        return left >= right


class LessThanOrEqual(_BinaryCompareBase):
    """left <= right."""
    CODE = "LTE"

    @classmethod
    def _do_compare(cls, left, right):
        return left <= right


# EQ and NE extend with an optional tolerance for float-safe equality.

class _ToleranceCompareBase(_BinaryCompareBase):
    """Base for EQ / NE - adds an optional `tolerance` parameter."""

    @classmethod
    def validate_config(cls, cfg):
        super().validate_config(cfg)
        tol = cfg.get("tolerance", 0)
        if not isinstance(tol, (int, float)) or tol < 0:
            raise ValueError(
                f"{cls.CODE} 'tolerance' must be a non-negative number"
            )

    @classmethod
    def _do_compare(cls, left, right):
        raise NotImplementedError

    @classmethod
    def _do_compare_with_tol(
        cls, left: float, right: float, tol: float,
    ) -> bool:
        raise NotImplementedError

    @classmethod
    def evaluate(cls, cfg, samples):
        left = samples[0]
        if left.quality < GOOD_QUALITY or left.value is None:
            return BlockResult(value=None, quality=left.quality)

        if len(samples) > 1:
            right = samples[1]
            if right.quality < GOOD_QUALITY or right.value is None:
                return BlockResult(value=None, quality=right.quality)
            right_val = right.value
        else:
            right_val = float(cfg["value"])

        tol = float(cfg.get("tolerance", 0))
        result = cls._do_compare_with_tol(left.value, right_val, tol)
        return BlockResult(
            value=1.0 if result else 0.0,
            quality=GOOD_NON_SPECIFIC,
        )


class Equal(_ToleranceCompareBase):
    """|left - right| <= tolerance (default 0 = strict equality).

    Tolerance is recommended for any floating-point comparison; only
    use the default of 0 when comparing integer-valued tags.
    """
    CODE = "EQ"

    @classmethod
    def _do_compare_with_tol(cls, left, right, tol):
        return abs(left - right) <= tol


class NotEqual(_ToleranceCompareBase):
    """|left - right| > tolerance."""
    CODE = "NE"

    @classmethod
    def _do_compare_with_tol(cls, left, right, tol):
        return abs(left - right) > tol


# ===========================================================================
# Logical (sect 6.6.3)
# ===========================================================================

class AndOf(BaseBlock):
    """Logical AND of N inputs. Any input BAD -> output BAD.

    Inputs are coerced to boolean by `value > 0`. Matches how
    bool_true alarm rules read tag values.
    """
    CODE = "AND_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("AND_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        if not _all_good(samples):
            return BlockResult(value=None, quality=_worst_quality(samples))
        result = all(s.value > 0 for s in samples)
        return BlockResult(
            value=1.0 if result else 0.0, quality=GOOD_NON_SPECIFIC,
        )


class OrOf(BaseBlock):
    """Logical OR of N inputs. Any input BAD -> output BAD."""
    CODE = "OR_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("OR_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        if not _all_good(samples):
            return BlockResult(value=None, quality=_worst_quality(samples))
        result = any(s.value > 0 for s in samples)
        return BlockResult(
            value=1.0 if result else 0.0, quality=GOOD_NON_SPECIFIC,
        )


class XorOf(BaseBlock):
    """Logical XOR of N inputs - TRUE if an odd number are TRUE.

    Per IEC 61131-3 sect 6.6.3, XOR is generalized to N inputs as the
    parity (odd-count) operation.
    """
    CODE = "XOR_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("XOR_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        if not _all_good(samples):
            return BlockResult(value=None, quality=_worst_quality(samples))
        true_count = sum(1 for s in samples if s.value > 0)
        return BlockResult(
            value=1.0 if true_count % 2 == 1 else 0.0,
            quality=GOOD_NON_SPECIFIC,
        )


class Not(BaseBlock):
    """Single-input logical NOT. >0 inverts to 0; <=0 inverts to 1."""
    CODE = "NOT"

    @classmethod
    def inputs(cls, cfg):
        return [int(cfg["input"])]

    @classmethod
    def validate_config(cls, cfg):
        if "input" not in cfg:
            raise ValueError("NOT requires 'input' tag ID in block_config")
        _validate_tag_id("NOT", cfg["input"])

    @classmethod
    def evaluate(cls, cfg, samples):
        s = samples[0]
        if s.quality < GOOD_QUALITY or s.value is None:
            return BlockResult(value=None, quality=s.quality)
        result = s.value <= 0
        return BlockResult(
            value=1.0 if result else 0.0, quality=GOOD_NON_SPECIFIC,
        )


# ===========================================================================
# Registration
# ===========================================================================

for cls in (
    IfThenElse,
    GreaterThan, LessThan, GreaterThanOrEqual, LessThanOrEqual,
    Equal, NotEqual,
    AndOf, OrOf, XorOf, Not,
):
    register_block(cls)
