"""Block configuration recipes for every registered calc block.

VERSION: v6 (added value_filter field for input-value-sensitive blocks)

Single source of truth for "what does a valid block_config look like for
block code X". Used by both seed_all_blocks.py (to create one computed
tag per block) and smoke_test_all_blocks.py (to verify they all evaluate).

For each block code we record:
    code          : the canonical CODE matching BLOCK_REGISTRY
    category      : which tier/family it belongs to
    output_dtype  : data_type to set on the resulting computed tag
    needs         : minimum input-tag requirements
    build_config  : (pool) -> block_config dict
    expect_value  : optional (samples_dict) -> expected output for
                    correctness checks; None when value depends on
                    timing/state and is not deterministically checkable

The build_config callable receives a `TagPool` (see TagPool below) and
returns a config dict consumed verbatim by POST /api/computed-tags.

Why each recipe is its own function instead of a dict literal:
  * Several blocks need to enforce "tag A != tag B" relationships that
    depend on what the pool actually offers
  * MUX_INDEX wants an integer-valued tag for index and at least one
    distinct value tag
  * WEIGHTED_AVG needs weights matching inputs length
  * SR/RS/CTU/CTD need two DIFFERENT bool tags
A pure-data table would force these constraints into a separate layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# TagPool — what the seed/smoke scripts hand to each recipe
# ---------------------------------------------------------------------------

@dataclass
class TagPool:
    """Available input tags discovered on the running system.

    Recipes pick from these slots. The seed/smoke probes (GET /api/tags)
    populate them and refuse to proceed if any required slot is empty.
    """
    numeric: list[int] = field(default_factory=list)   # any float/int tags
    integer: list[int] = field(default_factory=list)   # int16/int32/uint16/uint32
    booleans: list[int] = field(default_factory=list)  # data_type='bool'

    def needs(self, *, num: int = 0, ints: int = 0, bools: int = 0) -> list[str]:
        """Return list of unmet requirements (empty list = pool is sufficient).
        Used by seed/smoke to fail fast with a clear message."""
        gaps: list[str] = []
        if len(self.numeric) < num:
            gaps.append(f"need {num} numeric tags, have {len(self.numeric)}")
        if len(self.integer) < ints:
            gaps.append(f"need {ints} integer tags, have {len(self.integer)}")
        if len(self.booleans) < bools:
            gaps.append(f"need {bools} bool tags, have {len(self.booleans)}")
        return gaps


# ---------------------------------------------------------------------------
# Recipe dataclass
# ---------------------------------------------------------------------------

@dataclass
class BlockRecipe:
    code: str
    category: str
    output_dtype: str           # data_type to set on the computed tag
    build_config: Callable[[TagPool], dict]
    # Minimum pool requirements — checked before invoking build_config
    needs_numeric: int = 0
    needs_integer: int = 0
    needs_bool: int = 0
    # When True, this block is stateful: first tick may not produce a value;
    # the smoke test allows a longer settling window before asserting status=ok
    stateful: bool = False
    # When True, the block can legitimately return BAD quality for the
    # smoke-test inputs we picked (e.g. LN(negative_input)). The smoke
    # treats "evaluated at least once" as sufficient, not "status=ok".
    quality_may_be_bad: bool = False
    # Optional value-range constraint for input tags. When set, the seed
    # picks input tags whose CURRENT live value satisfies the predicate.
    # Avoids EXP(2705) → inf, LN(negative) → BAD, etc. — making more
    # blocks show GOOD output in the UI even with arbitrary sensor data.
    # The filter is applied universally to pool.numeric / pool.integer
    # / pool.booleans before build_config is called.
    value_filter: Callable[[float], bool] | None = None
    value_filter_desc: str = ""           # human-readable for log output
    # Optional expected-value predicate (currently unused but reserved
    # for the rigorous correctness section of the smoke).
    expect_value: Callable[[dict], float | None] | None = None


# ---------------------------------------------------------------------------
# Recipe builders — kept as plain functions so reading the recipes below
# is one screen of mostly-table.
# ---------------------------------------------------------------------------

def _inputs_list(n: int = 3):
    def build(pool: TagPool) -> dict:
        return {"inputs": pool.numeric[:n]}
    return build


def _bool_inputs_list(n: int = 3):
    def build(pool: TagPool) -> dict:
        return {"inputs": pool.booleans[:n]}
    return build


def _weighted_avg():
    def build(pool: TagPool) -> dict:
        inputs = pool.numeric[:3]
        return {"inputs": inputs, "weights": [1.0, 2.0, 1.0][:len(inputs)]}
    return build


def _hot_standby():
    def build(pool: TagPool) -> dict:
        return {"primary": pool.numeric[0], "standby": pool.numeric[1]}
    return build


def _voting():
    def build(pool: TagPool) -> dict:
        # min_agreement is optional; let block compute strict majority
        return {"inputs": pool.numeric[:3], "tolerance": 5.0}
    return build


def _mux_index():
    def build(pool: TagPool) -> dict:
        # index uses an integer tag; values are different numeric tags
        # (index must not appear in values per validate_config)
        idx_tag = pool.integer[0]
        values = [t for t in pool.numeric if t != idx_tag][:3]
        return {"index": idx_tag, "values": values}
    return build


def _if_then_else():
    def build(pool: TagPool) -> dict:
        return {
            "condition": pool.booleans[0],
            "then_value": pool.numeric[0],
            "else_value": pool.numeric[1],
        }
    return build


def _binary_tag_tag():
    """left+right, both tags. Default form for SUB/DIV/MOD/POW/etc."""
    def build(pool: TagPool) -> dict:
        return {"left": pool.numeric[0], "right": pool.numeric[1]}
    return build


def _binary_tag_const(value: float = 2.0):
    """left tag + constant value. Used as alternative form for ADD/MUL."""
    def build(pool: TagPool) -> dict:
        return {"left": pool.numeric[0], "value": value}
    return build


def _binary_tag_const_with_tol(value: float = 0.0, tol: float = 0.1):
    """For EQ/NE which support a tolerance field."""
    def build(pool: TagPool) -> dict:
        return {"left": pool.numeric[0], "value": value, "tolerance": tol}
    return build


def _unary_input(slot: str = "numeric"):
    def build(pool: TagPool) -> dict:
        tag = (pool.booleans if slot == "bool" else pool.numeric)[0]
        return {"input": tag}
    return build


def _timer():
    """TON/TOF/TP — bool input + preset_ms."""
    def build(pool: TagPool) -> dict:
        return {"input": pool.booleans[0], "preset_ms": 500}
    return build


def _sr_rs():
    """SR/RS — two distinct bool tags as set/reset."""
    def build(pool: TagPool) -> dict:
        return {"set": pool.booleans[0], "reset": pool.booleans[1]}
    return build


def _ctu():
    def build(pool: TagPool) -> dict:
        return {"count_up": pool.booleans[0], "reset": pool.booleans[1]}
    return build


def _ctd():
    def build(pool: TagPool) -> dict:
        return {
            "count_down": pool.booleans[0],
            "load": pool.booleans[1],
            "load_value": 10,
        }
    return build


# ---------------------------------------------------------------------------
# The full registry — order shown matches the BLOCK_SCHEMAS layout
# ---------------------------------------------------------------------------

RECIPES: list[BlockRecipe] = [
    # ===== Aggregation (Tier A) =====================================
    BlockRecipe("SUM_OF",         "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("AVG_OF",         "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("MIN_OF",         "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("MAX_OF",         "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("MEDIAN_OF",      "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("MODE_OF",        "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("RANGE_OF",       "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("RMS_OF",         "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("PRODUCT_OF",     "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("GEOMETRIC_MEAN", "aggregation",  "float64", _inputs_list(3), needs_numeric=3,
                quality_may_be_bad=True,  # BAD if any input <= 0
                value_filter=lambda v: v > 0,
                value_filter_desc="value > 0"),
    BlockRecipe("HARMONIC_MEAN",  "aggregation",  "float64", _inputs_list(3), needs_numeric=3,
                quality_may_be_bad=True,  # BAD if any input == 0
                value_filter=lambda v: v != 0,
                value_filter_desc="value != 0"),
    BlockRecipe("STDDEV_OF",      "aggregation",  "float64", _inputs_list(3), needs_numeric=3),  # min 2
    BlockRecipe("VARIANCE_OF",    "aggregation",  "float64", _inputs_list(3), needs_numeric=3),  # min 2
    BlockRecipe("COUNT_GOOD",     "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("COUNT_NONZERO",  "aggregation",  "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("WEIGHTED_AVG",   "aggregation",  "float64", _weighted_avg(),  needs_numeric=3),

    # ===== Selection (Tier B) =======================================
    BlockRecipe("FIRST_GOOD",      "selection",   "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("LAST_GOOD",       "selection",   "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("HIGHEST_QUALITY", "selection",   "float64", _inputs_list(3), needs_numeric=3),
    BlockRecipe("HOT_STANDBY",     "selection",   "float64", _hot_standby(),  needs_numeric=2),
    BlockRecipe("VOTING_M_OF_N",   "selection",   "float64", _voting(),       needs_numeric=3,
                quality_may_be_bad=True),  # may be BAD if inputs disagree beyond tolerance
    BlockRecipe("MUX_INDEX",       "selection",   "float64", _mux_index(),
                needs_numeric=4,  # 3 values + 1 to ensure index!=any-value
                needs_integer=1,
                quality_may_be_bad=True,  # may be BAD if index out of range
                value_filter=lambda v: 0 <= v < 3,
                value_filter_desc="0 <= value < 3 (index range for 3-value mux)"),

    # ===== Conditional (Tier C) =====================================
    BlockRecipe("IF_THEN_ELSE", "conditional",   "float64", _if_then_else(),
                needs_numeric=2, needs_bool=1),

    # ===== Comparison (Tier C) ======================================
    BlockRecipe("GT",  "comparison", "float64", _binary_tag_const(0.0), needs_numeric=1),
    BlockRecipe("LT",  "comparison", "float64", _binary_tag_const(0.0), needs_numeric=1),
    BlockRecipe("GTE", "comparison", "float64", _binary_tag_const(0.0), needs_numeric=1),
    BlockRecipe("LTE", "comparison", "float64", _binary_tag_const(0.0), needs_numeric=1),
    BlockRecipe("EQ",  "comparison", "float64", _binary_tag_const_with_tol(0.0, 0.5), needs_numeric=1),
    BlockRecipe("NE",  "comparison", "float64", _binary_tag_const_with_tol(0.0, 0.5), needs_numeric=1),

    # ===== Logical (Tier C) =========================================
    BlockRecipe("AND_OF", "logical",   "float64", _bool_inputs_list(2), needs_bool=2),
    BlockRecipe("OR_OF",  "logical",   "float64", _bool_inputs_list(2), needs_bool=2),
    BlockRecipe("XOR_OF", "logical",   "float64", _bool_inputs_list(2), needs_bool=2),
    BlockRecipe("NOT",    "logical",   "float64", _unary_input("bool"), needs_bool=1),

    # ===== Stateful (Tier D) ========================================
    BlockRecipe("TON",    "stateful", "float64", _timer(), needs_bool=1, stateful=True),
    BlockRecipe("TOF",    "stateful", "float64", _timer(), needs_bool=1, stateful=True),
    BlockRecipe("TP",     "stateful", "float64", _timer(), needs_bool=1, stateful=True),
    BlockRecipe("R_TRIG", "stateful", "float64", _unary_input("bool"), needs_bool=1, stateful=True),
    BlockRecipe("F_TRIG", "stateful", "float64", _unary_input("bool"), needs_bool=1, stateful=True),
    BlockRecipe("SR",     "stateful", "float64", _sr_rs(), needs_bool=2, stateful=True),
    BlockRecipe("RS",     "stateful", "float64", _sr_rs(), needs_bool=2, stateful=True),
    BlockRecipe("CTU",    "stateful", "float64", _ctu(),   needs_bool=2, stateful=True),
    BlockRecipe("CTD",    "stateful", "float64", _ctd(),   needs_bool=2, stateful=True),

    # ===== Arithmetic binary (Tier E) ===============================
    # ADD/MUL also support an n_ary mode, but the binary form is what
    # every other binary block uses, so we exercise that here. The n_ary
    # mode is covered separately in smoke_test_all_blocks Section 7.
    BlockRecipe("ADD",        "arithmetic", "float64", _binary_tag_tag(),    needs_numeric=2),
    BlockRecipe("SUB",        "arithmetic", "float64", _binary_tag_tag(),    needs_numeric=2),
    BlockRecipe("MUL",        "arithmetic", "float64", _binary_tag_tag(),    needs_numeric=2),
    BlockRecipe("DIV",        "arithmetic", "float64", _binary_tag_const(2), needs_numeric=1,
                quality_may_be_bad=True),  # BAD if denominator == 0
    BlockRecipe("MOD",        "arithmetic", "float64", _binary_tag_const(2), needs_numeric=1,
                quality_may_be_bad=True),
    BlockRecipe("POW",        "arithmetic", "float64", _binary_tag_const(2), needs_numeric=1,
                quality_may_be_bad=True),  # BAD on overflow / complex result
    BlockRecipe("MIN_OF_TWO", "arithmetic", "float64", _binary_tag_tag(),    needs_numeric=2),
    BlockRecipe("MAX_OF_TWO", "arithmetic", "float64", _binary_tag_tag(),    needs_numeric=2),

    # ===== Unary math (Tier E) ======================================
    BlockRecipe("ABS",   "unary_math", "float64", _unary_input(), needs_numeric=1),
    BlockRecipe("NEG",   "unary_math", "float64", _unary_input(), needs_numeric=1),
    BlockRecipe("SQRT",  "unary_math", "float64", _unary_input(), needs_numeric=1,
                quality_may_be_bad=True,  # BAD if input < 0
                value_filter=lambda v: v >= 0,
                value_filter_desc="value >= 0"),
    BlockRecipe("FLOOR", "unary_math", "float64", _unary_input(), needs_numeric=1),
    BlockRecipe("CEIL",  "unary_math", "float64", _unary_input(), needs_numeric=1),
    BlockRecipe("ROUND", "unary_math", "float64", _unary_input(), needs_numeric=1),

    # ===== Transcendental (Tier E) ==================================
    BlockRecipe("EXP",   "transcendental", "float64", _unary_input(), needs_numeric=1,
                quality_may_be_bad=True,  # overflow on large inputs
                value_filter=lambda v: -700 < v < 700,
                value_filter_desc="-700 < value < 700 (avoid math.exp overflow)"),
    BlockRecipe("LN",    "transcendental", "float64", _unary_input(), needs_numeric=1,
                quality_may_be_bad=True,  # BAD if input <= 0
                value_filter=lambda v: v > 0,
                value_filter_desc="value > 0"),
    BlockRecipe("LOG10", "transcendental", "float64", _unary_input(), needs_numeric=1,
                quality_may_be_bad=True,
                value_filter=lambda v: v > 0,
                value_filter_desc="value > 0"),
    BlockRecipe("SIN",   "transcendental", "float64", _unary_input(), needs_numeric=1),
    BlockRecipe("COS",   "transcendental", "float64", _unary_input(), needs_numeric=1),
    BlockRecipe("TAN",   "transcendental", "float64", _unary_input(), needs_numeric=1,
                quality_may_be_bad=True),  # BAD near asymptote
]


# Quick sanity at import — fail loud if duplicates sneak in
_codes_seen: set[str] = set()
for r in RECIPES:
    if r.code in _codes_seen:
        raise RuntimeError(f"Duplicate recipe for {r.code}")
    _codes_seen.add(r.code)


def recipes_by_code() -> dict[str, BlockRecipe]:
    return {r.code: r for r in RECIPES}


def pool_requirements() -> dict[str, int]:
    """Compute the worst-case pool requirements across all recipes.
    Used by the smoke test to fail fast if the system can't seed the
    full set."""
    return {
        "numeric": max((r.needs_numeric for r in RECIPES), default=0),
        "integer": max((r.needs_integer for r in RECIPES), default=0),
        "bool":    max((r.needs_bool    for r in RECIPES), default=0),
    }
