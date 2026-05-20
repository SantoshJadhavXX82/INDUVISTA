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
    resolve_operand_spec, validate_operand_spec, operand_tag_id,
    collect_list_tag_ids,
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
    """Validation for blocks taking an 'inputs' list of operand specs."""
    if "inputs" not in cfg:
        raise ValueError(f"{block_name} requires 'inputs' in block_config")
    inputs = cfg["inputs"]
    if not isinstance(inputs, list) or len(inputs) < min_inputs:
        raise ValueError(
            f"{block_name} 'inputs' must be a list of at least "
            f"{min_inputs} items"
        )
    if len(inputs) > max_inputs:
        raise ValueError(f"{block_name} supports at most {max_inputs} inputs")
    for i, spec in enumerate(inputs):
        validate_operand_spec(f"{block_name} inputs[{i}]", spec)
    tag_ids = collect_list_tag_ids(inputs)
    if len(set(tag_ids)) != len(tag_ids):
        raise ValueError(f"{block_name} tag inputs must be unique")


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
        # Order: condition tag (if any), then_value tag (if any), else_value tag (if any)
        ids: list[int] = []
        for key in ("condition", "then_value", "else_value"):
            t = operand_tag_id(cfg[key])
            if t is not None:
                ids.append(t)
        return ids

    @classmethod
    def validate_config(cls, cfg):
        for key in ("condition", "then_value", "else_value"):
            if key not in cfg:
                raise ValueError(
                    f"IF_THEN_ELSE requires '{key}' operand in block_config "
                    f"(tag id or {{value: n}})"
                )
            validate_operand_spec(f"IF_THEN_ELSE.{key}", cfg[key])

    @classmethod
    def evaluate(cls, cfg, samples):
        # Decode each operand. Walk samples in order of tag-typed operands.
        sample_idx = 0
        def _resolve(key: str):
            nonlocal sample_idx
            tag, const = resolve_operand_spec(cfg[key])
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                return s.value, s.quality
            return float(const), GOOD_NON_SPECIFIC

        cond_val, cond_q = _resolve("condition")
        if cond_q < GOOD_QUALITY or cond_val is None:
            return BlockResult(value=None, quality=cond_q)

        # Resolve BOTH branches in declaration order so the sample_idx
        # walker consumes them in the order the worker delivered them.
        then_val, then_q = _resolve("then_value")
        else_val, else_q = _resolve("else_value")

        chosen_val, chosen_q = (then_val, then_q) if cond_val > 0 else (else_val, else_q)
        if chosen_val is None:
            return BlockResult(value=None, quality=chosen_q)
        return BlockResult(
            value=float(chosen_val),
            quality=min(chosen_q, GOOD_NON_SPECIFIC),
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
        ids: list[int] = []
        left_tag = operand_tag_id(cfg["left"])
        if left_tag is not None:
            ids.append(left_tag)
        if "right" in cfg:
            right_tag = operand_tag_id(cfg["right"])
            if right_tag is not None:
                ids.append(right_tag)
        return ids

    @classmethod
    def validate_config(cls, cfg):
        if "left" not in cfg:
            raise ValueError(
                f"{cls.CODE} requires 'left' operand in block_config"
            )
        validate_operand_spec(f"{cls.CODE}.left", cfg["left"])

        if "right" in cfg:
            validate_operand_spec(f"{cls.CODE}.right", cfg["right"])
            if "value" in cfg:
                raise ValueError(
                    f"{cls.CODE}: cannot set both 'right' and legacy 'value'"
                )
            # When both are tags, they must be distinct
            l_tag = operand_tag_id(cfg["left"])
            r_tag = operand_tag_id(cfg["right"])
            if l_tag is not None and r_tag is not None and l_tag == r_tag:
                raise ValueError(
                    f"{cls.CODE}: 'left' and 'right' must be different tags"
                )
        elif "value" in cfg:
            # Legacy constant form on RHS
            validate_operand_spec(
                f"{cls.CODE}.value (legacy)",
                {"value": cfg["value"]},
            )
        else:
            raise ValueError(f"{cls.CODE} requires 'right' operand")

    @classmethod
    def _do_compare(cls, left: float, right: float) -> bool:
        raise NotImplementedError("subclass must override _do_compare")

    @classmethod
    def evaluate(cls, cfg, samples):
        sample_idx = 0
        # left
        l_tag, l_const = resolve_operand_spec(cfg["left"])
        if l_tag is not None:
            ls = samples[sample_idx]
            sample_idx += 1
            if ls.quality < GOOD_QUALITY or ls.value is None:
                return BlockResult(value=None, quality=ls.quality)
            left_val = float(ls.value)
        else:
            left_val = float(l_const)
        # right (or legacy 'value')
        if "right" in cfg:
            r_tag, r_const = resolve_operand_spec(cfg["right"])
            if r_tag is not None:
                rs = samples[sample_idx]
                sample_idx += 1
                if rs.quality < GOOD_QUALITY or rs.value is None:
                    return BlockResult(value=None, quality=rs.quality)
                right_val = float(rs.value)
            else:
                right_val = float(r_const)
        else:
            right_val = float(cfg["value"])

        result = cls._do_compare(left_val, right_val)
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
        sample_idx = 0
        # left
        l_tag, l_const = resolve_operand_spec(cfg["left"])
        if l_tag is not None:
            ls = samples[sample_idx]
            sample_idx += 1
            if ls.quality < GOOD_QUALITY or ls.value is None:
                return BlockResult(value=None, quality=ls.quality)
            left_val = float(ls.value)
        else:
            left_val = float(l_const)
        # right (or legacy 'value')
        if "right" in cfg:
            r_tag, r_const = resolve_operand_spec(cfg["right"])
            if r_tag is not None:
                rs = samples[sample_idx]
                sample_idx += 1
                if rs.quality < GOOD_QUALITY or rs.value is None:
                    return BlockResult(value=None, quality=rs.quality)
                right_val = float(rs.value)
            else:
                right_val = float(r_const)
        else:
            right_val = float(cfg["value"])

        tol = float(cfg.get("tolerance", 0))
        result = cls._do_compare_with_tol(left_val, right_val, tol)
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
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("AND_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        # Each operand is bool-coerced via value > 0. Constants pass through
        # at GOOD quality; tag samples must also be GOOD or output is BAD.
        bool_vals: list[bool] = []
        sample_idx = 0
        for spec in (cfg.get("inputs", []) or []):
            tag, const = resolve_operand_spec(spec)
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                if s.quality < GOOD_QUALITY or s.value is None:
                    return BlockResult(value=None, quality=s.quality)
                bool_vals.append(s.value > 0)
            else:
                bool_vals.append(const > 0)
        result = all(bool_vals)
        return BlockResult(
            value=1.0 if result else 0.0, quality=GOOD_NON_SPECIFIC,
        )


class OrOf(BaseBlock):
    """Logical OR of N inputs. Any input BAD -> output BAD."""
    CODE = "OR_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("OR_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        bool_vals: list[bool] = []
        sample_idx = 0
        for spec in (cfg.get("inputs", []) or []):
            tag, const = resolve_operand_spec(spec)
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                if s.quality < GOOD_QUALITY or s.value is None:
                    return BlockResult(value=None, quality=s.quality)
                bool_vals.append(s.value > 0)
            else:
                bool_vals.append(const > 0)
        result = any(bool_vals)
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
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("XOR_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        bool_vals: list[bool] = []
        sample_idx = 0
        for spec in (cfg.get("inputs", []) or []):
            tag, const = resolve_operand_spec(spec)
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                if s.quality < GOOD_QUALITY or s.value is None:
                    return BlockResult(value=None, quality=s.quality)
                bool_vals.append(s.value > 0)
            else:
                bool_vals.append(const > 0)
        true_count = sum(1 for v in bool_vals if v)
        return BlockResult(
            value=1.0 if true_count % 2 == 1 else 0.0,
            quality=GOOD_NON_SPECIFIC,
        )


class Not(BaseBlock):
    """Single-input logical NOT. >0 inverts to 0; <=0 inverts to 1."""
    CODE = "NOT"

    @classmethod
    def inputs(cls, cfg):
        t = operand_tag_id(cfg["input"])
        return [t] if t is not None else []

    @classmethod
    def validate_config(cls, cfg):
        if "input" not in cfg:
            raise ValueError("NOT requires 'input' operand in block_config")
        validate_operand_spec("NOT.input", cfg["input"])

    @classmethod
    def evaluate(cls, cfg, samples):
        tag, const = resolve_operand_spec(cfg["input"])
        if tag is not None:
            s = samples[0]
            if s.quality < GOOD_QUALITY or s.value is None:
                return BlockResult(value=None, quality=s.quality)
            val = s.value
        else:
            val = float(const)
        result = val <= 0
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
