"""Phase 15.2 - Tier A aggregation blocks.

Bundles 14 aggregation blocks that share the same shape: take a list
of input tag IDs, evaluate to (value, quality). Validation is shared
via _validate_input_list_config().

Standards:
  - NIST/SEMATECH e-Handbook sect 1.3.5 (descriptive statistics)
  - ASME PTC 19.1-2018 sect 4.3 (sample uncertainty, n-1 divisor
    for sample standard deviation)
  - IEC 61131-3 sect 2.5.1.4 (ADD, MUL extended to N inputs)

Quality propagation (per OPC UA Part 8 sect 5.2.2.7):
  - Default policy: worst input quality propagates to output
  - Output quality = GOOD_NON_SPECIFIC if all inputs are GOOD
  - Output quality = worst_input_quality if any input is below GOOD
  - COUNT_GOOD and COUNT_NONZERO override this policy because their
    semantics intrinsically tolerate bad inputs
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from app.workers.calc_blocks.base import (
    BaseBlock, BlockResult, InputSample, register_block,
    GOOD_QUALITY, GOOD_NON_SPECIFIC,
    resolve_operand_spec, validate_operand_spec, operand_tag_id,
    collect_list_tag_ids,
)


# ---------------------------------------------------------------------------
# Shared validation
# ---------------------------------------------------------------------------

def _validate_input_list_config(
    block_name: str,
    block_config: dict[str, Any],
    min_inputs: int = 1,
    max_inputs: int = 100,
) -> None:
    """Common validation for blocks that take an 'inputs' list of
    operand specs (tag id, {tag: id}, or {value: number}).

    Backward compatible: existing configs with bare-int lists continue
    to work because resolve_operand_spec() accepts bare positive ints.
    """
    if "inputs" not in block_config:
        raise ValueError(
            f"{block_name} requires 'inputs' in block_config - a list of "
            f"tag ids or {{tag: id}} / {{value: number}} operand specs"
        )
    inputs = block_config["inputs"]
    if not isinstance(inputs, list) or len(inputs) < min_inputs:
        raise ValueError(
            f"{block_name} 'inputs' must be a list of at least {min_inputs} items"
        )
    if len(inputs) > max_inputs:
        raise ValueError(
            f"{block_name} supports at most {max_inputs} inputs; "
            f"split into nested blocks for larger sets."
        )
    for i, spec in enumerate(inputs):
        validate_operand_spec(f"{block_name} inputs[{i}]", spec)
    # Tag ids must be unique among tag-typed operands; constants can repeat
    tag_ids = collect_list_tag_ids(inputs)
    if len(set(tag_ids)) != len(tag_ids):
        raise ValueError(f"{block_name} tag inputs must be unique")


def _good_values(cfg: dict, samples: list[InputSample]) -> list[float]:
    """Extract numeric values from cfg['inputs'] + matching samples.

    Tag operands contribute their sample.value (None values dropped).
    Constant operands ({value: n}) contribute their constant.

    Note this does NOT filter by quality - quality-aware filtering is
    handled per-block via _output_quality. Constants always count.
    """
    inputs = cfg.get("inputs", []) or []
    out: list[float] = []
    sample_idx = 0
    for spec in inputs:
        tag, const = resolve_operand_spec(spec)
        if tag is not None:
            if sample_idx >= len(samples):
                # Worker bug: inputs() vs evaluate() out of sync.
                # Defensive — skip rather than crash.
                continue
            s = samples[sample_idx]
            sample_idx += 1
            if s.value is not None:
                out.append(float(s.value))
        else:
            out.append(float(const))
    return out


def _resolve_weight_spec(spec) -> tuple[int | None, float | None]:
    """Resolve a WEIGHTED_AVG weight spec.

    Unlike resolve_operand_spec, bare numbers (int OR float) are
    interpreted as CONSTANTS, not tag ids. This preserves legacy
    weights:[1.0, 0.5, 1.0] and [1, 2, 1] semantics — those numbers
    were always meant to be multiplicative weights, never tag refs.

    To use a tag as a weight, wrap it explicitly as {'tag': N}.
    """
    import math
    if isinstance(spec, bool):
        raise ValueError(
            "weight must not be boolean; use {value: 0/1} for boolean weights"
        )
    if isinstance(spec, (int, float)):
        if isinstance(spec, float) and not math.isfinite(spec):
            raise ValueError(f"weight float must be finite, got {spec!r}")
        return None, float(spec)
    if isinstance(spec, dict):
        if "tag" in spec:
            t = spec["tag"]
            if (not isinstance(t, int)) or isinstance(t, bool) or t <= 0:
                raise ValueError(
                    f"weight 'tag' must be a positive int, got {t!r}"
                )
            return t, None
        if "value" in spec:
            v = spec["value"]
            if (not isinstance(v, (int, float))) or isinstance(v, bool):
                raise ValueError(
                    f"weight 'value' must be a number, got {type(v).__name__}"
                )
            return None, float(v)
        raise ValueError(
            f"weight object needs 'tag' or 'value' key; "
            f"got keys {list(spec.keys())}"
        )
    raise ValueError(
        f"weight must be a number, {{'tag': id}}, or {{'value': number}}; "
        f"got {type(spec).__name__}"
    )


def _output_quality(cfg: dict, samples: list[InputSample]) -> int:
    """Default propagation: worst quality across TAG operands; constants
    don't drag quality down (they're inline GOOD). If there are no
    inputs at all → BAD. If only constants → GOOD_NON_SPECIFIC."""
    inputs = cfg.get("inputs", []) or []
    if not inputs:
        return 0
    # Only tag operands have a quality; constants are inherently GOOD.
    tag_qualities = [s.quality for s in samples] if samples else []
    if not tag_qualities:
        # Pure-constant config → all known-good
        return GOOD_NON_SPECIFIC
    worst = min(tag_qualities)
    return GOOD_NON_SPECIFIC if worst >= GOOD_QUALITY else worst


# ===========================================================================
# Simple aggregations
# ===========================================================================

class AvgOf(BaseBlock):
    """Arithmetic mean of N inputs. NIST eHB sect 1.3.5.1."""
    CODE = "AVG_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("AVG_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        return BlockResult(value=sum(vals) / len(vals),
                           quality=_output_quality(cfg, samples))


class MinOf(BaseBlock):
    """Minimum value of N inputs."""
    CODE = "MIN_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MIN_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        return BlockResult(value=min(vals), quality=_output_quality(cfg, samples))


class MaxOf(BaseBlock):
    """Maximum value of N inputs."""
    CODE = "MAX_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MAX_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        return BlockResult(value=max(vals), quality=_output_quality(cfg, samples))


class MedianOf(BaseBlock):
    """Median (50th percentile). NIST eHB sect 1.3.5.4.
    For even N, returns the mean of the two middle values."""
    CODE = "MEDIAN_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MEDIAN_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        return BlockResult(value=statistics.median(vals),
                           quality=_output_quality(cfg, samples))


class ModeOf(BaseBlock):
    """Most-frequent value among inputs. Returns smallest value on ties
    (matches statistics.multimode()[0] behavior)."""
    CODE = "MODE_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MODE_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        modes = statistics.multimode(vals)
        return BlockResult(value=min(modes), quality=_output_quality(cfg, samples))


class RangeOf(BaseBlock):
    """Max minus min. NIST eHB sect 1.3.5.3."""
    CODE = "RANGE_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("RANGE_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        return BlockResult(value=max(vals) - min(vals),
                           quality=_output_quality(cfg, samples))


# ===========================================================================
# Statistical (n-1 divisor per ASME PTC 19.1)
# ===========================================================================

class StddevOf(BaseBlock):
    """Sample standard deviation with (n-1) Bessel correction.
    Per ASME PTC 19.1-2018 sect 4.3 and NIST eHB sect 1.3.5.6."""
    CODE = "STDDEV_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        # Sample stddev requires at least 2 inputs for the (n-1) divisor.
        _validate_input_list_config("STDDEV_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if len(vals) < 2:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        return BlockResult(value=statistics.stdev(vals),
                           quality=_output_quality(cfg, samples))


class VarianceOf(BaseBlock):
    """Sample variance with (n-1) Bessel correction.
    Per ASME PTC 19.1-2018 sect 4.3."""
    CODE = "VARIANCE_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("VARIANCE_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if len(vals) < 2:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        return BlockResult(value=statistics.variance(vals),
                           quality=_output_quality(cfg, samples))


class RmsOf(BaseBlock):
    """Root mean square: sqrt(mean of squares).
    Used in power, vibration, signal processing."""
    CODE = "RMS_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("RMS_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        mean_sq = sum(v * v for v in vals) / len(vals)
        return BlockResult(value=math.sqrt(mean_sq),
                           quality=_output_quality(cfg, samples))


# ===========================================================================
# Products and weighted aggregations
# ===========================================================================

class ProductOf(BaseBlock):
    """Product of all inputs. IEC 61131-3 MUL extended to N inputs."""
    CODE = "PRODUCT_OF"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("PRODUCT_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        prod = 1.0
        for v in vals:
            prod *= v
        return BlockResult(value=prod, quality=_output_quality(cfg, samples))


class GeometricMean(BaseBlock):
    """Geometric mean: Nth root of product. Requires all inputs positive."""
    CODE = "GEOMETRIC_MEAN"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("GEOMETRIC_MEAN", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        # Any non-positive value invalidates the geometric mean.
        # Mark quality as BAD rather than producing a complex/NaN result.
        if any(v <= 0 for v in vals):
            return BlockResult(value=None, quality=0)
        # Use log-sum-exp pattern for numerical stability on large products.
        return BlockResult(
            value=math.exp(sum(math.log(v) for v in vals) / len(vals)),
            quality=_output_quality(cfg, samples),
        )


class HarmonicMean(BaseBlock):
    """Harmonic mean: N / sum(1/x_i). All inputs must be non-zero."""
    CODE = "HARMONIC_MEAN"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("HARMONIC_MEAN", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(cfg, samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(cfg, samples))
        if any(v == 0 for v in vals):
            # Division by zero would occur. Return BAD quality.
            return BlockResult(value=None, quality=0)
        return BlockResult(
            value=len(vals) / sum(1.0 / v for v in vals),
            quality=_output_quality(cfg, samples),
        )


class WeightedAvg(BaseBlock):
    """Weighted arithmetic mean: sum(w_i * x_i) / sum(w_i).

    Configuration:
        inputs:  [tag_id_1, tag_id_2, ...]
        weights: [w_1, w_2, ...]  (same length as inputs)

    Weights must be positive. Weights are applied in the same order
    as inputs in block_config.
    """
    CODE = "WEIGHTED_AVG"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("WEIGHTED_AVG", cfg, min_inputs=1)
        weights = cfg.get("weights")
        if weights is None:
            raise ValueError(
                "WEIGHTED_AVG requires 'weights' list in block_config"
            )
        if not isinstance(weights, list):
            raise ValueError("WEIGHTED_AVG 'weights' must be a list")
        if len(weights) != len(cfg["inputs"]):
            raise ValueError(
                f"WEIGHTED_AVG: weights length {len(weights)} != "
                f"inputs length {len(cfg['inputs'])}"
            )
        # Weights use the weight-aware resolver: bare numbers are
        # CONSTANTS (legacy weights:[1.0, 0.5, ...] semantics), not
        # tag ids. To use a tag as a weight, wrap as {tag: N}.
        for i, w in enumerate(weights):
            tag, const = _resolve_weight_spec(w)
            if tag is None and const <= 0:
                raise ValueError(
                    f"WEIGHTED_AVG weights[{i}]={const!r} must be positive"
                )

    @classmethod
    def inputs(cls, cfg):
        # Tag ids come from inputs FIRST, then from weights — matches the
        # sample order the worker delivers them in evaluate().
        # For weights, only the explicit {tag: N} form fetches; bare
        # numbers are constants and need no sample.
        weight_tags = [
            _resolve_weight_spec(w)[0]
            for w in (cfg.get("weights", []) or [])
        ]
        weight_tag_ids = [t for t in weight_tags if t is not None]
        return collect_list_tag_ids(cfg.get("inputs", [])) + weight_tag_ids

    @classmethod
    def evaluate(cls, cfg, samples):
        inputs_specs = cfg.get("inputs", []) or []
        weights_specs = cfg.get("weights", []) or []
        n_input_tags = sum(1 for s in inputs_specs if operand_tag_id(s) is not None)
        input_samples = samples[:n_input_tags]
        weight_samples = samples[n_input_tags:]

        # Resolve each (value, weight) pair, skipping items where the
        # tag sample is BAD (matches legacy behavior).
        in_idx = 0
        w_idx = 0
        weighted_sum = 0.0
        weight_total = 0.0
        worst_q = GOOD_NON_SPECIFIC
        for v_spec, w_spec in zip(inputs_specs, weights_specs):
            v_tag, v_const = resolve_operand_spec(v_spec)
            if v_tag is not None:
                s = input_samples[in_idx]
                in_idx += 1
                worst_q = min(worst_q, s.quality)
                if s.value is None:
                    continue
                value = float(s.value)
            else:
                value = float(v_const)

            # Weights use the weight-aware resolver (bare num = const)
            w_tag, w_const = _resolve_weight_spec(w_spec)
            if w_tag is not None:
                s = weight_samples[w_idx]
                w_idx += 1
                worst_q = min(worst_q, s.quality)
                if s.value is None or s.value <= 0:
                    continue
                weight = float(s.value)
            else:
                weight = float(w_const)

            weighted_sum += value * weight
            weight_total += weight

        out_q = GOOD_NON_SPECIFIC if worst_q >= GOOD_QUALITY else worst_q
        if weight_total <= 0:
            return BlockResult(value=None, quality=out_q)
        return BlockResult(
            value=weighted_sum / weight_total,
            quality=out_q,
        )


# ===========================================================================
# Counts (quality-aware; intrinsically tolerate bad inputs)
# ===========================================================================

class CountGood(BaseBlock):
    """Number of inputs whose quality byte is >= 128 (GOOD or better).
    Output quality is always GOOD because COUNT is well-defined even
    when many inputs are BAD."""
    CODE = "COUNT_GOOD"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("COUNT_GOOD", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        # Count tag operands with GOOD quality + every constant operand
        # (constants are by definition inline-good).
        count = sum(1 for s in samples if s.quality >= GOOD_QUALITY)
        const_count = sum(
            1 for spec in (cfg.get("inputs", []) or [])
            if operand_tag_id(spec) is None
        )
        return BlockResult(value=float(count + const_count),
                           quality=GOOD_NON_SPECIFIC)


class CountNonzero(BaseBlock):
    """Number of inputs with value != 0 regardless of quality.
    Useful for counting active alarms, running motors, etc."""
    CODE = "COUNT_NONZERO"

    @classmethod
    def inputs(cls, cfg):
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("COUNT_NONZERO", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        # Count tag samples with a non-zero numeric value + constants
        # whose embedded value is non-zero.
        count = sum(
            1 for s in samples
            if s.value is not None and s.value != 0
        )
        for spec in (cfg.get("inputs", []) or []):
            tag, const = resolve_operand_spec(spec)
            if tag is None and const != 0:
                count += 1
        return BlockResult(value=float(count), quality=GOOD_NON_SPECIFIC)


# ===========================================================================
# Registration
# ===========================================================================

for cls in (AvgOf, MinOf, MaxOf, MedianOf, ModeOf, RangeOf,
            StddevOf, VarianceOf, RmsOf,
            ProductOf, GeometricMean, HarmonicMean, WeightedAvg,
            CountGood, CountNonzero):
    register_block(cls)
