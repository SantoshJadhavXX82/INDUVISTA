"""Phase 15.3 - Tier B selection blocks.

6 blocks that select 1 of N inputs based on quality / vote / index
rather than reducing them via arithmetic. Standard in DCS selector
libraries (Yokogawa CENTUM SELECTOR, Honeywell Experion CL
SelectInput, ABB 800xA Function Designer).

Standards:
  - IEC 61131-3 sect 6.6.1 (selection function blocks: SEL, MUX,
    MAX, MIN, LIMIT)
  - IEC 61511 Part 1 sect 9.4 (voting architecture for safety
    instrumented systems)
  - OPC UA Part 8 sect 5.2.2 (quality byte semantics; GOOD >= 128)

Quality handling per block:
  FIRST_GOOD / LAST_GOOD     scan inputs for first/last GOOD; if any
                              GOOD found, output is GOOD_NON_SPECIFIC
  HIGHEST_QUALITY            argmax over quality byte; output quality
                              mirrors the chosen input's quality so
                              "best of all bad" is still BAD downstream
  HOT_STANDBY                primary if GOOD, else standby; output
                              GOOD_NON_SPECIFIC if either fires
  VOTING_M_OF_N              median of largest cluster within tolerance,
                              BAD if no cluster meets min_agreement
  MUX_INDEX                  output quality mirrors the selected input;
                              BAD if index itself is BAD or out-of-range
"""

from __future__ import annotations

import statistics
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
    """Common validation for blocks taking an 'inputs' list of tag IDs.

    Selection blocks generally need >= 2 inputs (a 1-input "selector"
    is just a passthrough and not worth the block overhead).
    """
    if "inputs" not in cfg:
        raise ValueError(f"{block_name} requires 'inputs' in block_config")
    inputs = cfg["inputs"]
    if not isinstance(inputs, list) or len(inputs) < min_inputs:
        raise ValueError(
            f"{block_name} 'inputs' must be a list of at least "
            f"{min_inputs} tag IDs"
        )
    if len(inputs) > max_inputs:
        raise ValueError(
            f"{block_name} supports at most {max_inputs} inputs"
        )
    for x in inputs:
        _validate_tag_id(block_name, x)
    if len(set(inputs)) != len(inputs):
        raise ValueError(f"{block_name} inputs must be unique")


def _worst_quality(samples: list[InputSample]) -> int:
    """Surface the worst input quality. Used when no block-specific
    output is producible (no GOOD inputs / no cluster / out-of-range)."""
    return min((s.quality for s in samples), default=0)


# ===========================================================================
# Order-based selectors
# ===========================================================================

class FirstGood(BaseBlock):
    """Return value of first input with quality >= GOOD.

    Scans inputs in declared order. Useful for prioritized redundancy
    where inputs[0] is preferred, then [1], etc. For an industrial
    two-input failover with named primary/standby keys see HOT_STANDBY.
    """
    CODE = "FIRST_GOOD"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("FIRST_GOOD", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        for s in samples:
            if s.quality >= GOOD_QUALITY and s.value is not None:
                return BlockResult(value=s.value, quality=GOOD_NON_SPECIFIC)
        return BlockResult(value=None, quality=_worst_quality(samples))


class LastGood(BaseBlock):
    """Return value of last input with quality >= GOOD (scan in reverse).

    Useful when inputs are ordered chronologically (oldest -> newest)
    and the most recent GOOD reading should win, e.g. when picking the
    freshest of a set of staggered sensor reads.
    """
    CODE = "LAST_GOOD"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("LAST_GOOD", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        for s in reversed(samples):
            if s.quality >= GOOD_QUALITY and s.value is not None:
                return BlockResult(value=s.value, quality=GOOD_NON_SPECIFIC)
        return BlockResult(value=None, quality=_worst_quality(samples))


# ===========================================================================
# Quality-based selector
# ===========================================================================

class HighestQuality(BaseBlock):
    """Return value of input with the highest quality byte.

    On ties (multiple inputs at the same max quality), returns the
    first encountered. Output quality is the chosen input's actual
    quality - if even the best input is BAD, output is BAD, preserving
    downstream visibility into the failure mode.
    """
    CODE = "HIGHEST_QUALITY"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("HIGHEST_QUALITY", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        valid = [s for s in samples if s.value is not None]
        if not valid:
            return BlockResult(value=None, quality=_worst_quality(samples))
        # max() picks first occurrence on ties, which is what we want.
        best = max(valid, key=lambda s: s.quality)
        return BlockResult(value=best.value, quality=best.quality)


# ===========================================================================
# Named-pair failover
# ===========================================================================

class HotStandby(BaseBlock):
    """Two-input failover with named 'primary' and 'standby' keys.

    Stateless: returns primary if GOOD, else standby. Bumpless transfer
    with failback hysteresis (don't switch back from standby to primary
    until primary has been GOOD for N seconds) requires per-block state
    persistence and ships in a future stateful tier.

    The semantic difference from FIRST_GOOD with 2 inputs is config
    shape - primary/standby are named, not ordered, making the intent
    explicit in operator-facing config and in any future SCADA mapping.

    Configuration:
        block_config = {
            "primary": <tag_id>,
            "standby": <tag_id>,
        }
    """
    CODE = "HOT_STANDBY"

    @classmethod
    def inputs(cls, cfg):
        return [int(cfg["primary"]), int(cfg["standby"])]

    @classmethod
    def validate_config(cls, cfg):
        if "primary" not in cfg or "standby" not in cfg:
            raise ValueError(
                "HOT_STANDBY requires 'primary' and 'standby' tag IDs "
                "in block_config"
            )
        _validate_tag_id("HOT_STANDBY", cfg["primary"])
        _validate_tag_id("HOT_STANDBY", cfg["standby"])
        if cfg["primary"] == cfg["standby"]:
            raise ValueError(
                "HOT_STANDBY 'primary' and 'standby' must be different tags"
            )

    @classmethod
    def evaluate(cls, cfg, samples):
        primary, standby = samples
        if primary.quality >= GOOD_QUALITY and primary.value is not None:
            return BlockResult(value=primary.value, quality=GOOD_NON_SPECIFIC)
        if standby.quality >= GOOD_QUALITY and standby.value is not None:
            return BlockResult(value=standby.value, quality=GOOD_NON_SPECIFIC)
        return BlockResult(
            value=None,
            quality=min(primary.quality, standby.quality),
        )


# ===========================================================================
# Voting (TMR pattern)
# ===========================================================================

class VotingMofN(BaseBlock):
    """Median of largest cluster of inputs that agree within tolerance.

    Triple-modular-redundancy / safety-instrumented-system pattern.
    Per IEC 61511 Part 1 sect 9.4.

    Algorithm:
      1. Collect GOOD inputs whose value is not None.
      2. Sort. Find the largest contiguous sorted cluster where
         max(cluster) - min(cluster) <= tolerance.
      3. If cluster size >= min_agreement, output median of the cluster
         with quality GOOD_NON_SPECIFIC.
      4. Otherwise output BAD.

    Default min_agreement is strict majority: floor(N/2) + 1.
    For 2-of-3 TMR (N=3) this gives M=2 as expected.

    Configuration:
        block_config = {
            "inputs":         [tag_id, tag_id, ...],   # >= 2; typically 3+
            "tolerance":      <float>,                 # max - min across cluster
            "min_agreement":  <int>                    # optional; default = floor(N/2)+1
        }
    """
    CODE = "VOTING_M_OF_N"

    @classmethod
    def inputs(cls, cfg):
        return [int(x) for x in cfg.get("inputs", [])]

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("VOTING_M_OF_N", cfg, min_inputs=2)
        if "tolerance" not in cfg:
            raise ValueError(
                "VOTING_M_OF_N requires 'tolerance' in block_config"
            )
        tol = cfg["tolerance"]
        if not isinstance(tol, (int, float)) or tol < 0:
            raise ValueError(
                "VOTING_M_OF_N 'tolerance' must be a non-negative number"
            )
        n = len(cfg["inputs"])
        m = cfg.get("min_agreement")
        if m is not None:
            if not isinstance(m, int) or m < 2 or m > n:
                raise ValueError(
                    f"VOTING_M_OF_N 'min_agreement' must be an integer "
                    f"in [2, {n}]"
                )

    @classmethod
    def evaluate(cls, cfg, samples):
        tol = float(cfg["tolerance"])
        n = len(samples)
        m = cfg.get("min_agreement") or (n // 2 + 1)  # strict majority

        good = sorted(
            s.value for s in samples
            if s.quality >= GOOD_QUALITY and s.value is not None
        )
        if len(good) < m:
            return BlockResult(value=None, quality=_worst_quality(samples))

        # Sliding window: largest contiguous range with span <= tol.
        # O(N^2) worst case but N is small (validated to <= 20).
        best_cluster: list[float] = []
        for i in range(len(good)):
            j = i
            while j < len(good) and good[j] - good[i] <= tol:
                j += 1
            cluster = good[i:j]
            if len(cluster) > len(best_cluster):
                best_cluster = cluster

        if len(best_cluster) < m:
            return BlockResult(value=None, quality=_worst_quality(samples))

        return BlockResult(
            value=statistics.median(best_cluster),
            quality=GOOD_NON_SPECIFIC,
        )


# ===========================================================================
# Index-controlled multiplexer
# ===========================================================================

class MuxIndex(BaseBlock):
    """Index-controlled selector. First input is the index control,
    remaining inputs are the value tags; output = values[index].

    Per IEC 61131-3 sect 6.6.1 MUX function block.

    Configuration:
        block_config = {
            "index":  <tag_id>,                  # control tag (0-based int value)
            "values": [tag_id, tag_id, ...],     # 1..64 value tags
        }

    Quality:
      - index BAD or value None -> output BAD
      - index value not integer -> output BAD
      - index out of [0, len(values)-1] -> output BAD
      - otherwise output mirrors the selected value's quality

    NB: index value is read from the index TAG, not the block config.
    Block config only declares which tag carries the index. This lets
    the index be driven by another calc block or PLC output.
    """
    CODE = "MUX_INDEX"

    @classmethod
    def inputs(cls, cfg):
        return [int(cfg["index"])] + [int(x) for x in cfg.get("values", [])]

    @classmethod
    def validate_config(cls, cfg):
        if "index" not in cfg:
            raise ValueError(
                "MUX_INDEX requires 'index' tag ID in block_config"
            )
        _validate_tag_id("MUX_INDEX", cfg["index"])
        values = cfg.get("values")
        if not isinstance(values, list) or len(values) < 1:
            raise ValueError(
                "MUX_INDEX requires 'values' list with at least 1 tag ID"
            )
        if len(values) > 64:
            raise ValueError("MUX_INDEX supports at most 64 value inputs")
        for x in values:
            _validate_tag_id("MUX_INDEX", x)
        if len(set(values)) != len(values):
            raise ValueError("MUX_INDEX 'values' must be unique")
        if cfg["index"] in values:
            raise ValueError(
                "MUX_INDEX 'index' tag must not also appear in 'values'"
            )

    @classmethod
    def evaluate(cls, cfg, samples):
        index_sample = samples[0]
        value_samples = samples[1:]

        if index_sample.quality < GOOD_QUALITY or index_sample.value is None:
            return BlockResult(value=None, quality=index_sample.quality)

        idx_float = float(index_sample.value)
        if not idx_float.is_integer():
            # Non-integer index - configuration error at runtime.
            return BlockResult(value=None, quality=0)
        idx = int(idx_float)
        if idx < 0 or idx >= len(value_samples):
            return BlockResult(value=None, quality=0)

        selected = value_samples[idx]
        return BlockResult(value=selected.value, quality=selected.quality)


# ===========================================================================
# Registration
# ===========================================================================

for cls in (FirstGood, LastGood, HighestQuality,
            HotStandby, VotingMofN, MuxIndex):
    register_block(cls)
