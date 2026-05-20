"""Calc-block diagnostics — turn raw exceptions and quality-propagation
events into user-facing messages.

The calc_evaluator stores a single string per computed tag in
computed_tag_execution_stats.last_error_message. This module exists so
that string is *useful to an operator* rather than a stack-trace dump.

Two main entry points:

  classify_error(exc, block_type, samples_by_tag, block_config) -> str
      Called from the except handler. Inspects the exception type and
      the block's input context, returns a sentence the operator can
      act on.

  diagnose_bad_quality(samples, output_quality) -> str | None
      Called when evaluation succeeded (no exception) but the output
      quality is below the GOOD threshold. Identifies which input(s)
      caused the propagation and returns a sentence — or None if the
      output is actually GOOD.

Returned strings follow a consistent grammar:

    "<title> — <specific cause>. <action>."

so the UI can split on " — " for a 2-line display if it wants
(headline + detail), or render the whole string in a banner.
"""

from __future__ import annotations

from typing import Any

# Re-export the threshold so callers don't have to reach into base.py
from app.workers.calc_blocks.base import (
    GOOD_QUALITY, InputSample,
)


def classify_error(
    exc: BaseException,
    block_type: str,
    samples_by_tag: dict[int, InputSample] | None = None,
    block_config: dict[str, Any] | None = None,
) -> str:
    """Turn an exception raised during block evaluation into an operator-
    friendly message. Never raises — falls back to a generic
    "unexpected error" message if classification fails.

    The function is intentionally rule-based (not ML, not parsing
    English) so the output is predictable and testable.
    """
    try:
        return _classify(exc, block_type, samples_by_tag or {}, block_config or {})
    except Exception:
        # Diagnostics module must NEVER hide the original problem behind
        # a diagnostics bug. Fall back to the raw exception text.
        return f"Unclassified error — {type(exc).__name__}: {exc}"


def _classify(
    exc: BaseException,
    block_type: str,
    samples: dict[int, InputSample],
    config: dict[str, Any],
) -> str:
    name = type(exc).__name__
    msg = str(exc)

    # ----- Database / transaction errors -----
    if "InFailedSqlTransaction" in name or "InFailedSqlTransaction" in msg:
        return (
            "Database transaction error — a previous tag in this "
            "evaluation cycle failed, leaving the database session in an "
            "aborted state. This tag is downstream collateral, not the "
            "root cause. Check earlier 'Last error' entries from this "
            "cycle to find the actual failing tag."
        )
    if name in ("OperationalError", "InterfaceError", "DatabaseError"):
        return (
            "Database connectivity issue — could not read input samples "
            "or write output. Check that Postgres is healthy and "
            "reachable. Should self-recover on the next cycle."
        )

    # ----- Block-config errors -----
    if name == "KeyError":
        key = msg.strip("'\"")
        return (
            f"Configuration error — block_config is missing required key "
            f"'{key}' for {block_type}. Open this calc in admin and "
            f"re-save it so the field defaults populate."
        )
    if name == "ValueError" and (
        "must be" in msg or "invalid" in msg.lower() or "expected" in msg.lower()
    ):
        return (
            f"Configuration error — {msg.strip('. ')}. "
            f"Edit this calc block to fix the input or value."
        )
    if name == "TypeError" and (
        "not 'dict'" in msg
        or "argument must be" in msg
        or "got" in msg.lower()
    ):
        return (
            f"Configuration shape mismatch — {block_type} block_config has "
            f"a value of unexpected type ({msg.strip('. ')}). This usually "
            f"means the calc was created with an older schema. Open the "
            f"calc in admin, re-pick the inputs, and save."
        )

    # ----- Math errors -----
    if name == "ZeroDivisionError":
        return (
            f"Division by zero — the divisor input is currently 0. Add "
            f"a guard (e.g. IF_THEN_ELSE to skip this case) or constrain "
            f"the divisor with min_value > 0."
        )
    if name == "OverflowError":
        return (
            f"Numeric overflow — the calculation produced a value larger "
            f"than float64 can represent. Check input magnitudes; consider "
            f"a scaling factor or different block."
        )
    if name == "ValueError" and ("domain" in msg.lower() or "math" in msg.lower()):
        return (
            f"Math domain error — {block_type} received an input outside "
            f"its valid range (e.g. sqrt of a negative number, log of "
            f"zero). Add an IF_THEN_ELSE guard upstream."
        )

    # ----- Tag-existence errors -----
    if name == "IndexError" and samples is not None:
        return (
            f"Missing input tag — one of the configured inputs returned "
            f"no sample. The tag may have been deleted, or never received "
            f"any data yet. Verify the input tag still exists in Tag "
            f"Explorer and is being polled."
        )

    # ----- Deadline / timeout -----
    if "Deadline" in name or "deadline" in msg.lower():
        return (
            f"Evaluation deadline exceeded — this block took longer than "
            f"its allowed budget. Consider raising the execution rate "
            f"(longer interval) or simplifying the block configuration."
        )

    # ----- Default: keep the raw text but make it look intentional -----
    return (
        f"Unexpected {name} during {block_type} evaluation — {msg}. "
        f"Check the worker logs (docker compose logs calc_evaluator) "
        f"for the full traceback."
    )


def diagnose_bad_quality(
    output_quality: int,
    samples: list[InputSample],
    samples_by_tag: dict[int, InputSample] | None = None,
    block_type: str = "",
    value_is_none: bool = False,
    block_config: dict[str, Any] | None = None,
) -> str | None:
    """If the block's output is unusable, identify why.

    Two distinct failure modes are diagnosed here:

      A) Quality propagation: an upstream tag is BAD, so the block
         output inherits that. output_quality < GOOD_QUALITY.

      B) Block-internal "no result": all inputs were GOOD but the block
         itself decided it couldn't produce a value. Quality stays
         GOOD, output_value is None. Examples:
            * VOTING_M_OF_N — no cluster met the agreement threshold
            * MEDIAN_OF / AVG_OF — zero GOOD inputs after filtering
            * HOT_STANDBY    — no source meets selection criteria
            * Conditional comparisons — undefined for some input shapes

    Returns a single-sentence diagnostic, or None when the output is
    actually fine.
    """
    # Output is genuinely good — nothing to diagnose.
    if not value_is_none and output_quality >= GOOD_QUALITY:
        return None

    # ----- Case A: quality propagation (output quality is BAD) -----
    if output_quality < GOOD_QUALITY:
        bad = [s for s in samples if s.quality < GOOD_QUALITY]
        if not bad:
            return (
                "Output quality BAD — block evaluated without input "
                "failures but reported BAD quality. This is unusual; "
                "check the worker logs."
            )
        if len(bad) == 1:
            b = bad[0]
            return (
                f"Output quality BAD — caused by input tag #{b.tag_id} "
                f"(quality byte {b.quality}, last value "
                f"{'null' if b.value is None else b.value}). "
                f"Investigate that source tag in Tag Explorer."
            )
        ids = ", ".join(f"#{b.tag_id}" for b in bad[:5])
        more = f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""
        return (
            f"Output quality BAD — {len(bad)} input tags are reporting "
            f"BAD quality: {ids}{more}. Investigate the source tags in "
            f"Tag Explorer to find a common cause (network, gateway, "
            f"sensor)."
        )

    # ----- Case B: block-internal "no result" (value None, quality GOOD) -----
    # Block-specific diagnostics where we can be precise about why.
    block_diag = _block_specific_no_result(block_type, samples, block_config or {})
    if block_diag:
        return block_diag

    # Generic fallback when the block doesn't have a specific diagnostic.
    return (
        f"No value produced — {block_type} evaluated all inputs "
        f"successfully (quality GOOD) but couldn't compute a result. "
        f"Common causes: VOTING_M_OF_N found no cluster within tolerance, "
        f"WEIGHTED_AVG total weight = 0, FIRST_GOOD found no GOOD inputs. "
        f"Check the block's config thresholds against the input values."
    )


def _block_specific_no_result(
    block_type: str,
    samples: list[InputSample],
    config: dict[str, Any],
) -> str | None:
    """Generate a block-aware diagnostic for the value-null case.

    Each block-type gets a message that names its specific config
    parameters and shows the actual input values, so the operator can
    see why the block decided not to produce a value.
    """
    good_vals = [
        s.value for s in samples
        if s.quality >= GOOD_QUALITY and s.value is not None
    ]

    if block_type == "VOTING_M_OF_N":
        tol = config.get("tolerance", "?")
        n = len(samples)
        m = config.get("min_agreement") or (n // 2 + 1)
        if not good_vals:
            return (
                f"VOTING_M_OF_N: no GOOD inputs to vote on. Block needs "
                f"at least {m} agreeing inputs but received 0 usable values."
            )
        spread = max(good_vals) - min(good_vals) if len(good_vals) >= 2 else 0
        vals_str = ", ".join(f"{v:g}" for v in good_vals)
        return (
            f"VOTING_M_OF_N could not form quorum — received values "
            f"[{vals_str}] (spread {spread:g}) but tolerance is {tol} and "
            f"min_agreement is {m}. No cluster of {m}+ values has spread "
            f"≤ {tol}. Either raise tolerance, lower min_agreement, or "
            f"verify these are redundant signals that should actually agree."
        )

    if block_type == "WEIGHTED_AVG":
        return (
            f"WEIGHTED_AVG: total weight is zero — all weights are 0 or "
            f"all input tags are BAD. Check the weights config and the "
            f"input tag quality."
        )

    if block_type == "FIRST_GOOD" or block_type == "LAST_GOOD":
        return (
            f"{block_type}: no inputs are GOOD. The block scans inputs in "
            f"order looking for one with quality ≥ GOOD; received zero. "
            f"Check input tag quality."
        )

    if block_type == "HIGHEST_QUALITY":
        return (
            f"HIGHEST_QUALITY: no inputs are GOOD. The block picks the "
            f"input with the highest quality byte ≥ GOOD; received zero. "
            f"Check input tag quality."
        )

    if block_type == "HOT_STANDBY":
        return (
            f"HOT_STANDBY: neither primary nor backup is GOOD. The block "
            f"falls back from primary → backup → standby; all three are "
            f"BAD or have null values. Check the source tags."
        )

    if block_type == "MUX_INDEX":
        return (
            f"MUX_INDEX: the index input is out of range. Configure "
            f"the index to be 0..N-1 where N is the number of values."
        )

    if block_type in {"MEDIAN_OF", "AVG_OF", "MAX_OF", "MIN_OF", "SUM_OF",
                       "RANGE_OF", "MODE_OF", "STDDEV_OF", "VARIANCE_OF",
                       "RMS_OF", "GEOMETRIC_MEAN", "HARMONIC_MEAN",
                       "PRODUCT_OF"}:
        return (
            f"{block_type}: no GOOD numeric inputs to aggregate. The "
            f"block filters out BAD-quality and null inputs first; "
            f"after filtering, no values remain. Check input tag quality."
        )

    if block_type in {"DIV", "MOD"}:
        return (
            f"{block_type}: divisor is 0 or undefined. Add a guard "
            f"upstream (IF_THEN_ELSE) or constrain the divisor."
        )

    if block_type in {"SQRT", "LN", "LOG10"}:
        return (
            f"{block_type}: input out of domain. {block_type} requires "
            f"input > 0 (sqrt: ≥0). Add an IF_THEN_ELSE guard upstream."
        )

    # No block-specific diagnostic available
    return None

