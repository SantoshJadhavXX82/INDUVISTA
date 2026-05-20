"""Phase 15.5 - Tier D stateful blocks.

9 blocks across 4 categories. Each persists state across evaluation
cycles via the calc_block_state table (added in migration 0039).

  timer (sect 6.6.5):
    TON         On-delay: Q goes TRUE after IN has been TRUE for preset
    TOF         Off-delay: Q stays TRUE for preset after IN goes FALSE
    TP          Pulse: rising edge of IN gives Q high for exactly preset

  edge_detector (sect 6.6.6):
    R_TRIG      Q high for one evaluation when IN rises
    F_TRIG      Q high for one evaluation when IN falls

  latch (sect 6.6.7):
    SR          Set-dominant: Q latches TRUE on S, FALSE on R; S wins ties
    RS          Reset-dominant: opposite tie-break

  counter (sect 6.6.8):
    CTU         Up counter, output is current count (CV)
    CTD         Down counter

Stateful interface (vs the pure BaseBlock.evaluate):

    evaluate(cfg, samples, state, now_wall) -> (BlockResult, new_state)

  state         dict from previous tick; first call gets {} and the
                block applies defaults via state.get(key, default)
  now_wall      float, epoch seconds from time.time(); the worker
                passes this so blocks can compute elapsed time using
                wall-clock that survives worker restarts
  return        2-tuple: the standard BlockResult plus the new state
                dict to persist for the next tick. The new_state must
                be JSON-serializable (no datetimes - use floats).

Per IEC 61131-3 sect 6.6.5-6.6.8 (timers/edges/latches/counters).
"""

from __future__ import annotations

from typing import Any

from app.workers.calc_blocks.base import (
    StatefulBlock, BlockResult, InputSample, register_block,
    GOOD_QUALITY, GOOD_NON_SPECIFIC,
    resolve_operand_spec, validate_operand_spec, operand_tag_id,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _validate_tag_id(name: str, x: Any) -> None:
    if not isinstance(x, int) or x <= 0:
        raise ValueError(
            f"{name}: tag ID {x!r} is not a positive integer"
        )


def _is_high(sample: InputSample) -> bool:
    """Treat a sample as TRUE if quality is GOOD and value > 0."""
    return (
        sample.quality >= GOOD_QUALITY
        and sample.value is not None
        and sample.value > 0
    )


def _resolve_bool_operand(
    spec: object, sample: InputSample | None,
) -> tuple[bool | None, int]:
    """Decode a tag-or-constant operand spec into (bool_value, quality).
    bool_value is None when the underlying tag sample is BAD; the worker
    caller emits a BAD output in that case (preserving stateful state)."""
    tag, const = resolve_operand_spec(spec)
    if tag is not None:
        if sample is None:
            return None, 0  # defensive — worker should never hit this
        if sample.quality < GOOD_QUALITY or sample.value is None:
            return None, sample.quality
        return sample.value > 0, GOOD_NON_SPECIFIC
    return (const > 0), GOOD_NON_SPECIFIC


def _bad_result(sample: InputSample, state: dict) -> tuple[BlockResult, dict]:
    """When primary input is BAD, output BAD but preserve state so the
    timer/counter/latch resumes correctly when input recovers."""
    return BlockResult(value=None, quality=sample.quality), state


# ===========================================================================
# Timers (sect 6.6.5)
# ===========================================================================

class OnDelayTimer(StatefulBlock):
    """On-Delay Timer: Q goes TRUE when IN has been TRUE for preset_ms.

    When IN goes FALSE, Q resets to FALSE immediately and the timer
    rearms. Per IEC 61131-3 sect 6.6.5.

    Configuration:
        block_config = {
            "input":     <tag_id>,
            "preset_ms": <int>,     # delay in milliseconds
        }

    State:
        "in_was_high"     bool, the IN value at the previous tick
        "high_started_ts" float | None, wall time when IN went high
    """
    CODE = "TON"

    @classmethod
    def inputs(cls, cfg):
        t = operand_tag_id(cfg["input"])
        return [t] if t is not None else []

    @classmethod
    def validate_config(cls, cfg):
        if "input" not in cfg:
            raise ValueError("TON requires 'input' operand")
        validate_operand_spec("TON.input", cfg["input"])
        if "preset_ms" not in cfg:
            raise ValueError("TON requires 'preset_ms' in block_config")
        p = cfg["preset_ms"]
        if not isinstance(p, int) or p < 1:
            raise ValueError("TON 'preset_ms' must be a positive integer")

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        # Tag-or-constant input. Constant is "always at its value".
        sample = samples[0] if (operand_tag_id(cfg["input"]) is not None) else None
        is_high_opt, q = _resolve_bool_operand(cfg["input"], sample)
        if is_high_opt is None:
            # BAD upstream; preserve state for clean resume
            return BlockResult(value=None, quality=q), state

        is_high = is_high_opt
        preset_sec = cfg["preset_ms"] / 1000.0
        was_high = state.get("in_was_high", False)
        started_ts = state.get("high_started_ts")

        if is_high and not was_high:
            new_started = now_wall  # rising edge
        elif is_high and was_high:
            new_started = started_ts  # continuing
        else:
            new_started = None  # IN is low

        if is_high and new_started is not None:
            elapsed = now_wall - new_started
            q = 1.0 if elapsed >= preset_sec else 0.0
        else:
            q = 0.0

        new_state = {"in_was_high": is_high, "high_started_ts": new_started}
        return BlockResult(value=q, quality=GOOD_NON_SPECIFIC), new_state


class OffDelayTimer(StatefulBlock):
    """Off-Delay Timer: Q stays TRUE for preset_ms after IN goes FALSE.

    While IN is TRUE, Q is TRUE. When IN goes FALSE, Q remains TRUE for
    preset_ms, then drops to FALSE. Per IEC 61131-3 sect 6.6.5.

    Configuration:
        block_config = {
            "input":     <tag_id>,
            "preset_ms": <int>,
        }
    """
    CODE = "TOF"

    @classmethod
    def inputs(cls, cfg):
        t = operand_tag_id(cfg["input"])
        return [t] if t is not None else []

    @classmethod
    def validate_config(cls, cfg):
        if "input" not in cfg:
            raise ValueError("TOF requires 'input' operand")
        validate_operand_spec("TOF.input", cfg["input"])
        if "preset_ms" not in cfg:
            raise ValueError("TOF requires 'preset_ms' in block_config")
        p = cfg["preset_ms"]
        if not isinstance(p, int) or p < 1:
            raise ValueError("TOF 'preset_ms' must be a positive integer")

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        # Tag-or-constant input. Constant is "always at its value".
        sample = samples[0] if (operand_tag_id(cfg["input"]) is not None) else None
        is_high_opt, q = _resolve_bool_operand(cfg["input"], sample)
        if is_high_opt is None:
            # BAD upstream; preserve state for clean resume
            return BlockResult(value=None, quality=q), state

        is_high = is_high_opt
        preset_sec = cfg["preset_ms"] / 1000.0
        was_high = state.get("in_was_high", False)
        low_started = state.get("low_started_ts")

        if not is_high and was_high:
            new_low_started = now_wall  # falling edge
        elif not is_high and not was_high:
            new_low_started = low_started  # still low
        else:
            new_low_started = None  # IN is high

        if is_high:
            q = 1.0
        elif new_low_started is not None:
            elapsed = now_wall - new_low_started
            q = 1.0 if elapsed < preset_sec else 0.0
        else:
            q = 0.0

        new_state = {"in_was_high": is_high, "low_started_ts": new_low_started}
        return BlockResult(value=q, quality=GOOD_NON_SPECIFIC), new_state


class PulseTimer(StatefulBlock):
    """Pulse Timer: rising edge of IN produces Q high for exactly preset_ms.

    Subsequent rising edges during an active pulse are IGNORED (this
    matches IEC 61131-3 sect 6.6.5 TP semantics: TP is non-retriggerable).
    The pulse runs to completion regardless of what IN does afterwards.

    Configuration:
        block_config = {
            "input":     <tag_id>,
            "preset_ms": <int>,
        }
    """
    CODE = "TP"

    @classmethod
    def inputs(cls, cfg):
        t = operand_tag_id(cfg["input"])
        return [t] if t is not None else []

    @classmethod
    def validate_config(cls, cfg):
        if "input" not in cfg:
            raise ValueError("TP requires 'input' operand")
        validate_operand_spec("TP.input", cfg["input"])
        if "preset_ms" not in cfg:
            raise ValueError("TP requires 'preset_ms' in block_config")
        p = cfg["preset_ms"]
        if not isinstance(p, int) or p < 1:
            raise ValueError("TP 'preset_ms' must be a positive integer")

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        # Tag-or-constant input. Constant is "always at its value".
        sample = samples[0] if (operand_tag_id(cfg["input"]) is not None) else None
        is_high_opt, q = _resolve_bool_operand(cfg["input"], sample)
        if is_high_opt is None:
            # BAD upstream; preserve state for clean resume
            return BlockResult(value=None, quality=q), state

        is_high = is_high_opt
        preset_sec = cfg["preset_ms"] / 1000.0
        was_high = state.get("in_was_high", False)
        pulse_started = state.get("pulse_started_ts")

        # Rising edge starts a new pulse - only if no pulse currently active.
        if is_high and not was_high and pulse_started is None:
            new_pulse_started = now_wall
        else:
            new_pulse_started = pulse_started

        if new_pulse_started is not None:
            elapsed = now_wall - new_pulse_started
            if elapsed < preset_sec:
                q = 1.0
            else:
                q = 0.0
                new_pulse_started = None  # pulse complete
        else:
            q = 0.0

        new_state = {
            "in_was_high": is_high,
            "pulse_started_ts": new_pulse_started,
        }
        return BlockResult(value=q, quality=GOOD_NON_SPECIFIC), new_state


# ===========================================================================
# Edge detectors (sect 6.6.6)
# ===========================================================================

class RisingEdge(StatefulBlock):
    """Rising-edge trigger: Q is TRUE for one evaluation cycle when IN
    transitions FALSE -> TRUE. Per IEC 61131-3 sect 6.6.6.

    Configuration:
        block_config = {"input": <tag_id>}
    """
    CODE = "R_TRIG"

    @classmethod
    def inputs(cls, cfg):
        t = operand_tag_id(cfg["input"])
        return [t] if t is not None else []

    @classmethod
    def validate_config(cls, cfg):
        if "input" not in cfg:
            raise ValueError("R_TRIG requires 'input' operand")
        validate_operand_spec("R_TRIG.input", cfg["input"])

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        sample = samples[0] if (operand_tag_id(cfg["input"]) is not None) else None
        is_high_opt, q = _resolve_bool_operand(cfg["input"], sample)
        if is_high_opt is None:
            return BlockResult(value=None, quality=q), state
        is_high = is_high_opt
        was_high = state.get("prev_high", False)
        q = 1.0 if (is_high and not was_high) else 0.0
        new_state = {"prev_high": is_high}
        return BlockResult(value=q, quality=GOOD_NON_SPECIFIC), new_state


class FallingEdge(StatefulBlock):
    """Falling-edge trigger: Q is TRUE for one evaluation cycle when IN
    transitions TRUE -> FALSE. Per IEC 61131-3 sect 6.6.6.

    Configuration:
        block_config = {"input": <tag_id>}
    """
    CODE = "F_TRIG"

    @classmethod
    def inputs(cls, cfg):
        t = operand_tag_id(cfg["input"])
        return [t] if t is not None else []

    @classmethod
    def validate_config(cls, cfg):
        if "input" not in cfg:
            raise ValueError("F_TRIG requires 'input' operand")
        validate_operand_spec("F_TRIG.input", cfg["input"])

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        sample = samples[0] if (operand_tag_id(cfg["input"]) is not None) else None
        is_high_opt, q = _resolve_bool_operand(cfg["input"], sample)
        if is_high_opt is None:
            return BlockResult(value=None, quality=q), state
        is_high = is_high_opt
        was_high = state.get("prev_high", False)
        q = 1.0 if (not is_high and was_high) else 0.0
        new_state = {"prev_high": is_high}
        return BlockResult(value=q, quality=GOOD_NON_SPECIFIC), new_state


# ===========================================================================
# Latches (sect 6.6.7)
# ===========================================================================

class _LatchBase(StatefulBlock):
    """Shared logic for SR / RS latches.

    Both inputs need exactly two tag IDs: one for set, one for reset.
    Initial Q is 0. When both inputs are simultaneously TRUE, the
    dominant input wins; SR has S dominant, RS has R dominant.

    Configuration (both SR and RS):
        block_config = {
            "set":   <tag_id>,
            "reset": <tag_id>,
        }
    """

    SET_DOMINANT: bool = True  # override in RS subclass

    @classmethod
    def inputs(cls, cfg):
        ids: list[int] = []
        for key in ("set", "reset"):
            t = operand_tag_id(cfg[key])
            if t is not None:
                ids.append(t)
        return ids

    @classmethod
    def validate_config(cls, cfg):
        for key in ("set", "reset"):
            if key not in cfg:
                raise ValueError(f"{cls.CODE} requires '{key}' operand")
            validate_operand_spec(f"{cls.CODE}.{key}", cfg[key])
        # Disallow only when BOTH are tags AND identical
        s_tag = operand_tag_id(cfg["set"])
        r_tag = operand_tag_id(cfg["reset"])
        if s_tag is not None and r_tag is not None and s_tag == r_tag:
            raise ValueError(
                f"{cls.CODE}: 'set' and 'reset' must be different tags"
            )

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        # Resolve set + reset; consume samples in spec-order
        sample_idx = 0
        def _consume(key):
            nonlocal sample_idx
            t = operand_tag_id(cfg[key])
            if t is not None:
                sample = samples[sample_idx]
                sample_idx += 1
            else:
                sample = None
            val, q = _resolve_bool_operand(cfg[key], sample)
            return val, q
        s_val, s_q = _consume("set")
        if s_val is None:
            return BlockResult(value=None, quality=s_q), state
        r_val, r_q = _consume("reset")
        if r_val is None:
            return BlockResult(value=None, quality=r_q), state

        s = s_val
        r = r_val
        q = state.get("q", 0.0)

        if cls.SET_DOMINANT:
            if s:
                q = 1.0
            elif r:
                q = 0.0
        else:
            if r:
                q = 0.0
            elif s:
                q = 1.0

        return BlockResult(value=q, quality=GOOD_NON_SPECIFIC), {"q": q}


class SetReset(_LatchBase):
    """Set-dominant latch: S=1 sets Q regardless of R (S wins ties).
    Per IEC 61131-3 sect 6.6.7."""
    CODE = "SR"
    SET_DOMINANT = True


class ResetSet(_LatchBase):
    """Reset-dominant latch: R=1 clears Q regardless of S (R wins ties).
    Per IEC 61131-3 sect 6.6.7."""
    CODE = "RS"
    SET_DOMINANT = False


# ===========================================================================
# Counters (sect 6.6.8)
# ===========================================================================

class CountUp(StatefulBlock):
    """Up counter (CTU). On rising edge of count_up, CV increments by 1.
    When reset is TRUE, CV is forced to 0. Output is the current CV
    value (as float, since the framework's tag values are doubles).

    Per IEC 61131-3 sect 6.6.8.

    Configuration:
        block_config = {
            "count_up": <tag_id>,
            "reset":    <tag_id>,
        }
    """
    CODE = "CTU"

    @classmethod
    def inputs(cls, cfg):
        ids: list[int] = []
        for key in ("count_up", "reset"):
            t = operand_tag_id(cfg[key])
            if t is not None:
                ids.append(t)
        return ids

    @classmethod
    def validate_config(cls, cfg):
        for key in ("count_up", "reset"):
            if key not in cfg:
                raise ValueError(f"CTU requires '{key}' operand")
            validate_operand_spec(f"CTU.{key}", cfg[key])
        cu_tag = operand_tag_id(cfg["count_up"])
        r_tag = operand_tag_id(cfg["reset"])
        if cu_tag is not None and r_tag is not None and cu_tag == r_tag:
            raise ValueError("CTU: 'count_up' and 'reset' must differ")

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        sample_idx = 0
        def _consume(key):
            nonlocal sample_idx
            t = operand_tag_id(cfg[key])
            if t is not None:
                s = samples[sample_idx]
                sample_idx += 1
            else:
                s = None
            return _resolve_bool_operand(cfg[key], s)
        cu_opt, cu_q = _consume("count_up")
        if cu_opt is None:
            return BlockResult(value=None, quality=cu_q), state
        r_opt, r_q = _consume("reset")
        if r_opt is None:
            return BlockResult(value=None, quality=r_q), state

        cu = cu_opt
        r = r_opt
        prev_cu = state.get("prev_cu", False)
        cv = state.get("cv", 0)

        if r:
            cv = 0
        elif cu and not prev_cu:
            cv += 1

        new_state = {"prev_cu": cu, "cv": cv}
        return BlockResult(value=float(cv), quality=GOOD_NON_SPECIFIC), new_state


class CountDown(StatefulBlock):
    """Down counter (CTD). On rising edge of count_down, CV decrements
    by 1. When load is TRUE, CV is reloaded to load_value (default 0).
    Output is current CV.

    Per IEC 61131-3 sect 6.6.8.

    Configuration:
        block_config = {
            "count_down": <tag_id>,
            "load":       <tag_id>,
            "load_value": <int>,     # optional, default 0
        }
    """
    CODE = "CTD"

    @classmethod
    def inputs(cls, cfg):
        ids: list[int] = []
        for key in ("count_down", "load"):
            t = operand_tag_id(cfg[key])
            if t is not None:
                ids.append(t)
        return ids

    @classmethod
    def validate_config(cls, cfg):
        for key in ("count_down", "load"):
            if key not in cfg:
                raise ValueError(f"CTD requires '{key}' operand")
            validate_operand_spec(f"CTD.{key}", cfg[key])
        cd_tag = operand_tag_id(cfg["count_down"])
        l_tag = operand_tag_id(cfg["load"])
        if cd_tag is not None and l_tag is not None and cd_tag == l_tag:
            raise ValueError("CTD: 'count_down' and 'load' must differ")
        lv = cfg.get("load_value", 0)
        if not isinstance(lv, int):
            raise ValueError("CTD 'load_value' must be an integer")

    @classmethod
    def evaluate(cls, cfg, samples, state, now_wall):
        sample_idx = 0
        def _consume(key):
            nonlocal sample_idx
            t = operand_tag_id(cfg[key])
            if t is not None:
                s = samples[sample_idx]
                sample_idx += 1
            else:
                s = None
            return _resolve_bool_operand(cfg[key], s)
        cd_opt, cd_q = _consume("count_down")
        if cd_opt is None:
            return BlockResult(value=None, quality=cd_q), state
        ld_opt, ld_q = _consume("load")
        if ld_opt is None:
            return BlockResult(value=None, quality=ld_q), state

        cd = cd_opt
        ld = ld_opt
        load_value = cfg.get("load_value", 0)
        prev_cd = state.get("prev_cd", False)
        cv = state.get("cv", load_value)

        if ld:
            cv = load_value
        elif cd and not prev_cd:
            cv -= 1

        new_state = {"prev_cd": cd, "cv": cv}
        return BlockResult(value=float(cv), quality=GOOD_NON_SPECIFIC), new_state


# ===========================================================================
# Registration
# ===========================================================================

for cls in (
    OnDelayTimer, OffDelayTimer, PulseTimer,
    RisingEdge, FallingEdge,
    SetReset, ResetSet,
    CountUp, CountDown,
):
    register_block(cls)
