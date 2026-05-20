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
    """Validation for blocks taking an 'inputs' list of operand specs
    (tag id, {tag: id}, or {value: number})."""
    if "inputs" not in cfg:
        raise ValueError(f"{block_name} requires 'inputs' in block_config")
    inputs = cfg["inputs"]
    if not isinstance(inputs, list) or len(inputs) < min_inputs:
        raise ValueError(
            f"{block_name} 'inputs' must be a list of at least "
            f"{min_inputs} items"
        )
    if len(inputs) > max_inputs:
        raise ValueError(
            f"{block_name} supports at most {max_inputs} inputs"
        )
    for i, spec in enumerate(inputs):
        validate_operand_spec(f"{block_name} inputs[{i}]", spec)
    tag_ids = collect_list_tag_ids(inputs)
    if len(set(tag_ids)) != len(tag_ids):
        raise ValueError(f"{block_name} tag inputs must be unique")


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
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("FIRST_GOOD", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        # Iterate specs in declared order; constants count as GOOD.
        sample_idx = 0
        for spec in (cfg.get("inputs", []) or []):
            tag, const = resolve_operand_spec(spec)
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                if s.quality >= GOOD_QUALITY and s.value is not None:
                    return BlockResult(value=float(s.value),
                                       quality=GOOD_NON_SPECIFIC)
            else:
                # Constants are inherently GOOD
                return BlockResult(value=float(const),
                                   quality=GOOD_NON_SPECIFIC)
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
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("LAST_GOOD", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        # Iterate specs in REVERSE declared order; constants are GOOD.
        specs = list(cfg.get("inputs", []) or [])
        # First map each spec to its sample index (constants get None)
        sample_idx = 0
        per_spec_sample: list[InputSample | None] = []
        for spec in specs:
            if operand_tag_id(spec) is not None:
                per_spec_sample.append(samples[sample_idx])
                sample_idx += 1
            else:
                per_spec_sample.append(None)
        # Walk in reverse
        for spec, s in reversed(list(zip(specs, per_spec_sample))):
            tag, const = resolve_operand_spec(spec)
            if tag is not None:
                if s is not None and s.quality >= GOOD_QUALITY and s.value is not None:
                    return BlockResult(value=float(s.value),
                                       quality=GOOD_NON_SPECIFIC)
            else:
                return BlockResult(value=float(const),
                                   quality=GOOD_NON_SPECIFIC)
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
        return collect_list_tag_ids(cfg.get("inputs", []))

    @classmethod
    def validate_config(cls, cfg):
        _validate_inputs_list("HIGHEST_QUALITY", cfg, min_inputs=2)

    @classmethod
    def evaluate(cls, cfg, samples):
        # Build (value, quality) pairs from cfg+samples. Constants have
        # GOOD_NON_SPECIFIC quality by definition.
        specs = cfg.get("inputs", []) or []
        candidates: list[tuple[float, int]] = []  # (value, quality)
        sample_idx = 0
        for spec in specs:
            tag, const = resolve_operand_spec(spec)
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                if s.value is not None:
                    candidates.append((float(s.value), s.quality))
            else:
                candidates.append((float(const), GOOD_NON_SPECIFIC))
        if not candidates:
            return BlockResult(value=None, quality=_worst_quality(samples))
        # max() picks first occurrence on ties — preserve order semantics
        best_val, best_q = max(candidates, key=lambda vq: vq[1])
        return BlockResult(value=best_val, quality=best_q)


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
        ids: list[int] = []
        for key in ("primary", "standby"):
            tag = operand_tag_id(cfg[key])
            if tag is not None:
                ids.append(tag)
        return ids

    @classmethod
    def validate_config(cls, cfg):
        if "primary" not in cfg or "standby" not in cfg:
            raise ValueError(
                "HOT_STANDBY requires 'primary' and 'standby' operands "
                "(tag id or {value: n}) in block_config"
            )
        validate_operand_spec("HOT_STANDBY.primary", cfg["primary"])
        validate_operand_spec("HOT_STANDBY.standby", cfg["standby"])
        # When both are tags, they must be distinct
        p_tag = operand_tag_id(cfg["primary"])
        s_tag = operand_tag_id(cfg["standby"])
        if p_tag is not None and s_tag is not None and p_tag == s_tag:
            raise ValueError(
                "HOT_STANDBY 'primary' and 'standby' must be different tags"
            )

    @classmethod
    def evaluate(cls, cfg, samples):
        # Build (value, quality) for each slot from cfg+samples
        sample_idx = 0
        def _resolve(key: str):
            nonlocal sample_idx
            tag, const = resolve_operand_spec(cfg[key])
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                return s.value, s.quality
            return float(const), GOOD_NON_SPECIFIC
        p_val, p_q = _resolve("primary")
        s_val, s_q = _resolve("standby")
        if p_q >= GOOD_QUALITY and p_val is not None:
            return BlockResult(value=float(p_val), quality=GOOD_NON_SPECIFIC)
        if s_q >= GOOD_QUALITY and s_val is not None:
            return BlockResult(value=float(s_val), quality=GOOD_NON_SPECIFIC)
        return BlockResult(value=None, quality=min(p_q, s_q))


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
        return collect_list_tag_ids(cfg.get("inputs", []))

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
        specs = cfg.get("inputs", []) or []
        n = len(specs)
        m = cfg.get("min_agreement") or (n // 2 + 1)  # strict majority

        # Collect numeric values from GOOD tag samples + every constant.
        good_values: list[float] = []
        sample_idx = 0
        for spec in specs:
            tag, const = resolve_operand_spec(spec)
            if tag is not None:
                s = samples[sample_idx]
                sample_idx += 1
                if s.quality >= GOOD_QUALITY and s.value is not None:
                    good_values.append(float(s.value))
            else:
                good_values.append(float(const))
        good = sorted(good_values)
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
        ids: list[int] = []
        idx_tag = operand_tag_id(cfg["index"])
        if idx_tag is not None:
            ids.append(idx_tag)
        ids.extend(collect_list_tag_ids(cfg.get("values", [])))
        return ids

    @classmethod
    def validate_config(cls, cfg):
        if "index" not in cfg:
            raise ValueError(
                "MUX_INDEX requires 'index' operand in block_config"
            )
        validate_operand_spec("MUX_INDEX.index", cfg["index"])
        values = cfg.get("values")
        if not isinstance(values, list) or len(values) < 1:
            raise ValueError(
                "MUX_INDEX requires 'values' list with at least 1 item"
            )
        if len(values) > 64:
            raise ValueError("MUX_INDEX supports at most 64 value inputs")
        for i, v in enumerate(values):
            validate_operand_spec(f"MUX_INDEX.values[{i}]", v)
        # Tag uniqueness across values
        value_tag_ids = collect_list_tag_ids(values)
        if len(set(value_tag_ids)) != len(value_tag_ids):
            raise ValueError("MUX_INDEX 'values' tag entries must be unique")
        # Index tag must not appear in values' tag list
        idx_tag = operand_tag_id(cfg["index"])
        if idx_tag is not None and idx_tag in value_tag_ids:
            raise ValueError(
                "MUX_INDEX 'index' tag must not also appear in 'values'"
            )

    @classmethod
    def evaluate(cls, cfg, samples):
        sample_idx = 0
        # Resolve index
        idx_tag, idx_const = resolve_operand_spec(cfg["index"])
        if idx_tag is not None:
            index_sample = samples[sample_idx]
            sample_idx += 1
            if index_sample.quality < GOOD_QUALITY or index_sample.value is None:
                return BlockResult(value=None, quality=index_sample.quality)
            idx_float = float(index_sample.value)
        else:
            idx_float = float(idx_const)

        if not idx_float.is_integer():
            return BlockResult(value=None, quality=0)
        idx = int(idx_float)
        values_specs = cfg.get("values", []) or []
        if idx < 0 or idx >= len(values_specs):
            return BlockResult(value=None, quality=0)

        # Resolve only the selected value
        selected_spec = values_specs[idx]
        # We need the sample for it (if it's a tag). To find it, walk
        # through specs[0..idx-1] and count tags to skip the right number
        # of consumed samples.
        for prior_spec in values_specs[:idx]:
            if operand_tag_id(prior_spec) is not None:
                sample_idx += 1
        sel_tag, sel_const = resolve_operand_spec(selected_spec)
        if sel_tag is not None:
            sel_sample = samples[sample_idx]
            return BlockResult(value=sel_sample.value,
                               quality=sel_sample.quality)
        return BlockResult(value=float(sel_const),
                           quality=GOOD_NON_SPECIFIC)


# ===========================================================================
# Registration
# ===========================================================================

for cls in (FirstGood, LastGood, HighestQuality,
            HotStandby, VotingMofN, MuxIndex):
    register_block(cls)
