"""Phase 17.0b - Computed Tag preview endpoint.

POST /api/computed-tags/preview

Computes the predicted output of a block given its config and (optional)
input value overrides. Used by the frontend for live preview while
configuring a computed tag.

Same block registry as the worker - the predicted value is guaranteed
to match what the worker would produce given the same inputs. No JS/Python
drift possible.

Supports both stateless and stateful blocks:
  - Stateless: pass block_type + block_config + input_values. Returns
    the result.
  - Stateful: also pass `state` and optional `now`. Returns the result
    AND `new_state` so the frontend can step a timeline simulation by
    feeding new_state back into the next request.

Default behavior: if input_values is empty / missing tags, each required
input is treated as value=1.0 quality=GOOD. Lets the user preview blocks
without setting up real tag values.

Validation errors and execution errors return HTTP 200 with status
"validation_error" or "execution_error" in the body. The endpoint
itself only 4xx's for malformed requests.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.workers.calc_blocks import get_block
from app.workers.calc_blocks.base import (
    InputSample, GOOD_NON_SPECIFIC,
)


router = APIRouter(prefix="/api/computed-tags", tags=["computed-tags"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PreviewInputValue(BaseModel):
    """One supplied input value for preview. Both fields default
    to a 'good 1.0' so partial overrides are easy."""
    tag_id: int
    value: float | None = 1.0
    quality: int = Field(GOOD_NON_SPECIFIC, ge=0, le=255)


class PreviewRequest(BaseModel):
    block_type: str = Field(..., max_length=64, description="e.g. 'ADD', 'HARMONIC_MEAN'")
    block_config: dict = Field(default_factory=dict)
    input_values: list[PreviewInputValue] = Field(
        default_factory=list,
        description="Optional explicit values for the block's tag inputs. "
                    "Tags not listed default to value=1.0 quality=192.",
    )
    # Stateful-only fields. Ignored for stateless blocks.
    state: dict | None = Field(
        None,
        description="Previous state for stateful blocks. None = fresh start.",
    )
    now: float | None = Field(
        None,
        description="Wall-clock seconds for stateful blocks (sim time). "
                    "Defaults to server's time.time() if not provided.",
    )


class PreviewResponse(BaseModel):
    # Result
    value: float | None
    quality: int
    # Metadata
    is_stateful: bool
    new_state: dict | None = None
    # Status. The endpoint always returns HTTP 200 - check this field.
    status: str = Field(..., description="ok | validation_error | execution_error | unknown_block")
    error: str | None = None
    # What inputs were actually used (after override resolution + defaults).
    # Useful for the UI to show "we substituted 1.0 for tag #42 since you
    # didn't override it."
    inputs_used: list[PreviewInputValue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/preview", response_model=PreviewResponse)
def preview_computed_tag(body: PreviewRequest) -> PreviewResponse:
    # 1. Resolve the block class
    block_cls = get_block(body.block_type)
    if block_cls is None:
        return PreviewResponse(
            value=None, quality=0, is_stateful=False,
            status="unknown_block",
            error=f"unknown block_type '{body.block_type}'",
        )

    is_stateful = bool(getattr(block_cls, "STATEFUL", False))

    # 2. Validate config (catches user mistakes with friendly messages)
    try:
        block_cls.validate_config(body.block_config)
    except Exception as e:
        return PreviewResponse(
            value=None, quality=0, is_stateful=is_stateful,
            status="validation_error",
            error=str(e),
        )

    # 3. Extract the tag IDs the block wants
    try:
        wanted_tag_ids = block_cls.inputs(body.block_config)
    except Exception as e:
        return PreviewResponse(
            value=None, quality=0, is_stateful=is_stateful,
            status="validation_error",
            error=f"failed to extract inputs from config: {e}",
        )

    # 4. Build samples - overrides first, then defaults for missing
    override_map: dict[int, PreviewInputValue] = {
        iv.tag_id: iv for iv in body.input_values
    }
    samples: list[InputSample] = []
    inputs_used: list[PreviewInputValue] = []
    for tid in wanted_tag_ids:
        if tid in override_map:
            iv = override_map[tid]
            samples.append(InputSample(
                tag_id=tid, value=iv.value, quality=iv.quality,
            ))
            inputs_used.append(iv)
        else:
            default = PreviewInputValue(
                tag_id=tid, value=1.0, quality=GOOD_NON_SPECIFIC,
            )
            samples.append(InputSample(
                tag_id=tid, value=1.0, quality=GOOD_NON_SPECIFIC,
            ))
            inputs_used.append(default)

    # 5. Run the block
    try:
        if is_stateful:
            now_wall = body.now if body.now is not None else time.time()
            state = body.state or {}
            result, new_state = block_cls.evaluate(
                body.block_config, samples, state, now_wall,
            )
            return PreviewResponse(
                value=result.value,
                quality=result.quality,
                is_stateful=True,
                new_state=_clean_state(new_state),
                status="ok",
                inputs_used=inputs_used,
            )
        else:
            result = block_cls.evaluate(body.block_config, samples)
            return PreviewResponse(
                value=result.value,
                quality=result.quality,
                is_stateful=False,
                new_state=None,
                status="ok",
                inputs_used=inputs_used,
            )
    except Exception as e:
        return PreviewResponse(
            value=None, quality=0,
            is_stateful=is_stateful,
            new_state=None,
            status="execution_error",
            error=f"{type(e).__name__}: {e}",
            inputs_used=inputs_used,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_state(state: Any) -> dict | None:
    """Make sure state is JSON-serializable. The worker normally
    json.dumps it before storing, so this just confirms shape."""
    if state is None:
        return None
    if isinstance(state, dict):
        return state
    # Defensive: stateful blocks should always return dicts, but if one
    # returns something else we surface the type rather than 500'ing.
    return {"_invalid_state_type": type(state).__name__}
