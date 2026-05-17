"""Phase 15.1 - SUM_OF block.

Sums the latest GOOD values of the input tags. Used as the
proof-of-concept block for the calc library architecture.

Config shape:
    {
        "inputs": [42, 17, 9]    # list of tag_ids to sum
    }

Quality policy:
    - If ALL inputs are GOOD (quality >= 128), output is GOOD_NON_SPECIFIC
      and value is the sum.
    - If ANY input is below GOOD, output quality drops to the worst
      input quality, and value is the sum of available values
      (treating None as 0).
    - If `inputs` is empty, raise at validate; never at evaluate.

Future blocks in this family (AVG_OF, MIN_OF, MAX_OF, WEIGHTED_AVG)
will follow the same shape with different aggregation logic.
"""

from __future__ import annotations

from typing import Any

from app.workers.calc_blocks.base import (
    BaseBlock, BlockResult, InputSample,
    register_block, GOOD_QUALITY, GOOD_NON_SPECIFIC,
)


class SumOf(BaseBlock):
    CODE = "SUM_OF"

    @classmethod
    def inputs(cls, block_config: dict[str, Any]) -> list[int]:
        raw = block_config.get("inputs", [])
        return [int(x) for x in raw]

    @classmethod
    def validate_config(cls, block_config: dict[str, Any]) -> None:
        if "inputs" not in block_config:
            raise ValueError(
                "SUM_OF requires 'inputs' in block_config — a list of tag IDs"
            )
        inputs = block_config["inputs"]
        if not isinstance(inputs, list) or len(inputs) < 1:
            raise ValueError(
                "SUM_OF 'inputs' must be a non-empty list of tag IDs"
            )
        if len(inputs) > 100:
            raise ValueError(
                "SUM_OF supports at most 100 inputs; "
                "split into nested SUM_OF blocks for larger sets."
            )
        for x in inputs:
            if not isinstance(x, int) or x <= 0:
                raise ValueError(
                    f"SUM_OF input {x!r} is not a positive integer tag ID"
                )
        # Reject duplicates — they're almost always a config mistake and
        # the operator can use a constant block instead if they really
        # want to weight a value 2x.
        if len(set(inputs)) != len(inputs):
            raise ValueError(
                "SUM_OF inputs must be unique; duplicates are not allowed"
            )

    @classmethod
    def evaluate(
        cls,
        block_config: dict[str, Any],
        inputs: list[InputSample],
    ) -> BlockResult:
        if not inputs:
            # Validation should prevent this, defensive fall-through
            return BlockResult(value=None, quality=0)

        # Sum available values. Missing values count as 0 — common
        # industrial convention for partial-data sums. The quality
        # output communicates whether the result is trustworthy.
        total = 0.0
        for s in inputs:
            if s.value is not None:
                total += s.value

        worst = cls.worst_input_quality(inputs)
        out_quality = GOOD_NON_SPECIFIC if worst >= GOOD_QUALITY else worst
        return BlockResult(value=total, quality=out_quality)


register_block(SumOf)
