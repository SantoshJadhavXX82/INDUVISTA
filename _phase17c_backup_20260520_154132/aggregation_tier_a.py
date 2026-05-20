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
    """Common validation for blocks that take an 'inputs' list of tag IDs."""
    if "inputs" not in block_config:
        raise ValueError(
            f"{block_name} requires 'inputs' in block_config - a list of tag IDs"
        )
    inputs = block_config["inputs"]
    if not isinstance(inputs, list) or len(inputs) < min_inputs:
        raise ValueError(
            f"{block_name} 'inputs' must be a list of at least {min_inputs} tag IDs"
        )
    if len(inputs) > max_inputs:
        raise ValueError(
            f"{block_name} supports at most {max_inputs} inputs; "
            f"split into nested blocks for larger sets."
        )
    for x in inputs:
        if not isinstance(x, int) or x <= 0:
            raise ValueError(
                f"{block_name} input {x!r} is not a positive integer tag ID"
            )
    if len(set(inputs)) != len(inputs):
        raise ValueError(f"{block_name} inputs must be unique")


def _good_values(samples: list[InputSample]) -> list[float]:
    """Extract value from samples whose value is not None.

    Note this does NOT filter by quality - quality-aware filtering is
    handled per-block. This helper just keeps numeric/non-numeric out.
    """
    return [s.value for s in samples if s.value is not None]


def _output_quality(samples: list[InputSample]) -> int:
    """Default propagation: worst input quality, or GOOD_NON_SPECIFIC
    if all inputs are GOOD (>= 128)."""
    if not samples:
        return 0
    worst = min(s.quality for s in samples)
    return GOOD_NON_SPECIFIC if worst >= GOOD_QUALITY else worst


# ===========================================================================
# Simple aggregations
# ===========================================================================

class AvgOf(BaseBlock):
    """Arithmetic mean of N inputs. NIST eHB sect 1.3.5.1."""
    CODE = "AVG_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("AVG_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        return BlockResult(value=sum(vals) / len(vals),
                           quality=_output_quality(samples))


class MinOf(BaseBlock):
    """Minimum value of N inputs."""
    CODE = "MIN_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MIN_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        return BlockResult(value=min(vals), quality=_output_quality(samples))


class MaxOf(BaseBlock):
    """Maximum value of N inputs."""
    CODE = "MAX_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MAX_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        return BlockResult(value=max(vals), quality=_output_quality(samples))


class MedianOf(BaseBlock):
    """Median (50th percentile). NIST eHB sect 1.3.5.4.
    For even N, returns the mean of the two middle values."""
    CODE = "MEDIAN_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MEDIAN_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        return BlockResult(value=statistics.median(vals),
                           quality=_output_quality(samples))


class ModeOf(BaseBlock):
    """Most-frequent value among inputs. Returns smallest value on ties
    (matches statistics.multimode()[0] behavior)."""
    CODE = "MODE_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("MODE_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        modes = statistics.multimode(vals)
        return BlockResult(value=min(modes), quality=_output_quality(samples))


class RangeOf(BaseBlock):
    """Max minus min. NIST eHB sect 1.3.5.3."""
    CODE = "RANGE_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("RANGE_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        return BlockResult(value=max(vals) - min(vals),
                           quality=_output_quality(samples))


# ===========================================================================
# Statistical (n-1 divisor per ASME PTC 19.1)
# ===========================================================================

class StddevOf(BaseBlock):
    """Sample standard deviation with (n-1) Bessel correction.
    Per ASME PTC 19.1-2018 sect 4.3 and NIST eHB sect 1.3.5.6."""
    CODE = "STDDEV_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        # Sample stddev requires at least 2 inputs for the (n-1) divisor.
        _validate_input_list_config("STDDEV_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if len(vals) < 2:
            return BlockResult(value=None, quality=_output_quality(samples))
        return BlockResult(value=statistics.stdev(vals),
                           quality=_output_quality(samples))


class VarianceOf(BaseBlock):
    """Sample variance with (n-1) Bessel correction.
    Per ASME PTC 19.1-2018 sect 4.3."""
    CODE = "VARIANCE_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("VARIANCE_OF", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if len(vals) < 2:
            return BlockResult(value=None, quality=_output_quality(samples))
        return BlockResult(value=statistics.variance(vals),
                           quality=_output_quality(samples))


class RmsOf(BaseBlock):
    """Root mean square: sqrt(mean of squares).
    Used in power, vibration, signal processing."""
    CODE = "RMS_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("RMS_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        mean_sq = sum(v * v for v in vals) / len(vals)
        return BlockResult(value=math.sqrt(mean_sq),
                           quality=_output_quality(samples))


# ===========================================================================
# Products and weighted aggregations
# ===========================================================================

class ProductOf(BaseBlock):
    """Product of all inputs. IEC 61131-3 MUL extended to N inputs."""
    CODE = "PRODUCT_OF"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("PRODUCT_OF", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        prod = 1.0
        for v in vals:
            prod *= v
        return BlockResult(value=prod, quality=_output_quality(samples))


class GeometricMean(BaseBlock):
    """Geometric mean: Nth root of product. Requires all inputs positive."""
    CODE = "GEOMETRIC_MEAN"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("GEOMETRIC_MEAN", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        # Any non-positive value invalidates the geometric mean.
        # Mark quality as BAD rather than producing a complex/NaN result.
        if any(v <= 0 for v in vals):
            return BlockResult(value=None, quality=0)
        # Use log-sum-exp pattern for numerical stability on large products.
        return BlockResult(
            value=math.exp(sum(math.log(v) for v in vals) / len(vals)),
            quality=_output_quality(samples),
        )


class HarmonicMean(BaseBlock):
    """Harmonic mean: N / sum(1/x_i). All inputs must be non-zero."""
    CODE = "HARMONIC_MEAN"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("HARMONIC_MEAN", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        vals = _good_values(samples)
        if not vals:
            return BlockResult(value=None, quality=_output_quality(samples))
        if any(v == 0 for v in vals):
            # Division by zero would occur. Return BAD quality.
            return BlockResult(value=None, quality=0)
        return BlockResult(
            value=len(vals) / sum(1.0 / v for v in vals),
            quality=_output_quality(samples),
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
        return [int(x) for x in cfg.get("inputs", [])]

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
        for w in weights:
            if not isinstance(w, (int, float)) or w <= 0:
                raise ValueError(
                    f"WEIGHTED_AVG weight {w!r} must be a positive number"
                )

    @classmethod
    def evaluate(cls, cfg, samples):
        weights = cfg["weights"]
        # Align weights with sample order, dropping samples with no value.
        paired = [(s.value, w) for s, w in zip(samples, weights)
                  if s.value is not None]
        if not paired:
            return BlockResult(value=None, quality=_output_quality(samples))
        weighted_sum = sum(v * w for v, w in paired)
        weight_total = sum(w for _, w in paired)
        return BlockResult(
            value=weighted_sum / weight_total,
            quality=_output_quality(samples),
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
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("COUNT_GOOD", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        count = sum(1 for s in samples if s.quality >= GOOD_QUALITY)
        return BlockResult(value=float(count), quality=GOOD_NON_SPECIFIC)


class CountNonzero(BaseBlock):
    """Number of inputs with value != 0 regardless of quality.
    Useful for counting active alarms, running motors, etc."""
    CODE = "COUNT_NONZERO"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_input_list_config("COUNT_NONZERO", cfg, min_inputs=1)

    @classmethod
    def evaluate(cls, cfg, samples):
        count = sum(1 for s in samples
                    if s.value is not None and s.value != 0)
        return BlockResult(value=float(count), quality=GOOD_NON_SPECIFIC)


# ===========================================================================
# Registration
# ===========================================================================

for cls in (AvgOf, MinOf, MaxOf, MedianOf, ModeOf, RangeOf,
            StddevOf, VarianceOf, RmsOf,
            ProductOf, GeometricMean, HarmonicMean, WeightedAvg,
            CountGood, CountNonzero):
    register_block(cls)
