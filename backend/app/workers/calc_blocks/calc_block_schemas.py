"""Phase 16.0a - Calc block configuration schemas.

Each block in BLOCK_REGISTRY has a corresponding entry here describing
its block_config shape. Schemas are attached as `CONFIG_SCHEMA` class
attributes by install_schemas(), which runs once from
calc_blocks/__init__.py after all block modules have imported.

Schema is INTENTIONALLY simpler than full JSON Schema: it targets
form-rendering, not validation. Authoritative validation stays in each
block's validate_config() classmethod; the schema is hints for UI.

Shape:

    {
        "description": str,                  # optional summary for UI
        "fields": [
            {
                "key":      str,             # block_config key
                "label":    str,             # display label
                "type":     str,             # see FIELD_TYPES below
                "required": bool,            # default False
                "default":  Any,             # optional, seeds new forms
                "help":     str,             # optional tooltip text
                # Type-specific extras:
                "min":      number,          # integer / number
                "max":      number,
                "minItems": int,             # tag_ref_list / number_list
                "maxItems": int,
                "filter":   {                # tag_ref / tag_ref_list hint
                    "data_type": [...]       # restrict to these dtypes
                }
            }
        ]
    }

Field types the frontend (Phase 16.0b) will support:

  tag_ref          Single tag picker.
  tag_ref_list     Ordered list of tag picks; key is "inputs" by convention.
  tag_or_constant  Either a tag pick (posts as block_config[key]=<tag_id>)
                   or a numeric constant (posts as block_config["value"]
                   ALONGSIDE block_config[key] being absent). The two keys
                   are mutually exclusive - this is the convention used by
                   comparison / binary-arithmetic blocks since Phase 15.4a.
  integer          Whole-number input.
  number           Decimal-number input.
  number_list      Ordered list of numbers (used by WEIGHTED_AVG).
  boolean          Checkbox.
  enum             Dropdown chosen from options[].

Backend exposes schemas via GET /api/calc/block-schemas which returns
{block_code: schema}. Frontend fetches once and caches.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Filter constants (UI hints for tag pickers)
# ---------------------------------------------------------------------------

BOOL_FILTER = {"data_type": ["bool"]}
INTEGER_FILTER = {"data_type": ["int16", "uint16", "int32", "uint32"]}
NUMERIC_FILTER = {"data_type": ["int16", "uint16", "int32", "uint32",
                                "float", "double"]}


# ---------------------------------------------------------------------------
# Field-construction helpers (DRY)
# ---------------------------------------------------------------------------

def _tag_ref(key: str, label: str, *,
             required: bool = True,
             filter_: dict | None = None,
             help_: str | None = None) -> dict:
    f: dict[str, Any] = {"key": key, "label": label, "type": "tag_ref"}
    if required:
        f["required"] = True
    if filter_:
        f["filter"] = filter_
    if help_:
        f["help"] = help_
    return f


def _tag_ref_list(label: str = "Input tags", *,
                  min_items: int = 1,
                  max_items: int = 100,
                  filter_: dict | None = None,
                  help_: str | None = None) -> dict:
    f: dict[str, Any] = {
        "key": "inputs", "label": label, "type": "tag_ref_list",
        "required": True, "minItems": min_items, "maxItems": max_items,
    }
    if filter_:
        f["filter"] = filter_
    if help_:
        f["help"] = help_
    return f


def _integer(key: str, label: str, *,
             required: bool = True,
             default: int | None = None,
             min_: int | None = None,
             max_: int | None = None,
             help_: str | None = None) -> dict:
    f: dict[str, Any] = {"key": key, "label": label, "type": "integer"}
    if required:
        f["required"] = True
    if default is not None:
        f["default"] = default
    if min_ is not None:
        f["min"] = min_
    if max_ is not None:
        f["max"] = max_
    if help_:
        f["help"] = help_
    return f


def _number(key: str, label: str, *,
            required: bool = False,
            default: float | None = None,
            min_: float | None = None,
            max_: float | None = None,
            help_: str | None = None) -> dict:
    f: dict[str, Any] = {"key": key, "label": label, "type": "number"}
    if required:
        f["required"] = True
    if default is not None:
        f["default"] = default
    if min_ is not None:
        f["min"] = min_
    if max_ is not None:
        f["max"] = max_
    if help_:
        f["help"] = help_
    return f


def _tag_or_constant(key: str = "right", label: str = "Right operand", *,
                     help_: str | None = None) -> dict:
    """Picks either a tag (posts as block_config[key]) OR a constant
    (posts as block_config['value']). Both keys are MUTUALLY EXCLUSIVE
    in the saved config - the validate_config functions enforce that."""
    f: dict[str, Any] = {
        "key": key, "label": label, "type": "tag_or_constant",
        "required": True,
    }
    if help_:
        f["help"] = help_
    return f


def _mode_select(key: str, label: str, default_mode: str,
                 modes: list[tuple[str, str, list[dict]]],
                 *, help_: str | None = None) -> dict:
    """Discriminated field: presents a radio of modes, renders sub-fields
    for the selected mode. Used by ADD and MUL to expose both binary
    (left + right) and N-ary (sum/product of inputs) shapes in one form.

    modes is a list of (value, label, fields) tuples - one per mode.

    `key` is a virtual key that's NOT stored in block_config; it just
    drives form state. The actual block_config keys come from the
    sub-fields of the active mode.
    """
    return {
        "key": key,
        "label": label,
        "type": "mode_select",
        "default": default_mode,
        "options": [
            {"value": v, "label": lab, "fields": fs}
            for (v, lab, fs) in modes
        ],
        **({"help": help_} if help_ else {}),
    }


# ---------------------------------------------------------------------------
# Reusable schema templates
# ---------------------------------------------------------------------------

def _inputs_list_schema(*,
                        min_items: int = 1,
                        max_items: int = 100,
                        filter_: dict | None = None,
                        help_: str | None = None) -> dict:
    return {"fields": [_tag_ref_list(min_items=min_items,
                                     max_items=max_items,
                                     filter_=filter_, help_=help_)]}


def _unary_input_schema(*, filter_: dict | None = None) -> dict:
    return {"fields": [_tag_ref("input", "Input tag", filter_=filter_)]}


def _binary_tag_const_schema(*, with_tolerance: bool = False) -> dict:
    fields = [
        _tag_ref("left", "Left operand"),
        _tag_or_constant(help_="Either another tag or a numeric constant."),
    ]
    if with_tolerance:
        fields.append(_number("tolerance", "Tolerance", default=0, min_=0,
                              help_="Absolute tolerance for the comparison."))
    return {"fields": fields}


def _timer_schema() -> dict:
    return {"fields": [
        _tag_ref("input", "Input tag", filter_=BOOL_FILTER),
        _integer("preset_ms", "Preset (ms)", default=1000, min_=1,
                 help_="Time threshold in milliseconds."),
    ]}


def _latch_schema() -> dict:
    return {"fields": [
        _tag_ref("set", "Set tag (S)", filter_=BOOL_FILTER),
        _tag_ref("reset", "Reset tag (R)", filter_=BOOL_FILTER),
    ]}


# ---------------------------------------------------------------------------
# Schema map - one entry per registered block
# ---------------------------------------------------------------------------

BLOCK_SCHEMAS: dict[str, dict] = {
    # ===== Aggregation (Tier A) =================================
    "SUM_OF":         _inputs_list_schema(),
    "AVG_OF":         _inputs_list_schema(),
    "MIN_OF":         _inputs_list_schema(),
    "MAX_OF":         _inputs_list_schema(),
    "MEDIAN_OF":      _inputs_list_schema(),
    "MODE_OF":        _inputs_list_schema(),
    "RANGE_OF":       _inputs_list_schema(),
    "RMS_OF":         _inputs_list_schema(),
    "PRODUCT_OF":     _inputs_list_schema(),
    "GEOMETRIC_MEAN": _inputs_list_schema(
        help_="All inputs must be positive."),
    "HARMONIC_MEAN":  _inputs_list_schema(
        help_="All inputs must be non-zero."),
    "STDDEV_OF":      _inputs_list_schema(min_items=2,
        help_="Sample stddev (n-1 divisor); requires >=2 inputs."),
    "VARIANCE_OF":    _inputs_list_schema(min_items=2,
        help_="Sample variance (n-1 divisor); requires >=2 inputs."),
    "COUNT_GOOD":     _inputs_list_schema(),
    "COUNT_NONZERO":  _inputs_list_schema(),
    "WEIGHTED_AVG": {"fields": [
        _tag_ref_list(min_items=1),
        {
            "key": "weights",
            "label": "Weights",
            "type": "number_list",
            "required": True,
            "help": "One positive number per input, in the same order.",
        },
    ]},

    # ===== Selection (Tier B) ===================================
    "FIRST_GOOD":      _inputs_list_schema(min_items=2, max_items=20,
        help_="Scans in declared order; first GOOD wins."),
    "LAST_GOOD":       _inputs_list_schema(min_items=2, max_items=20,
        help_="Scans in reverse; last GOOD wins."),
    "HIGHEST_QUALITY": _inputs_list_schema(min_items=2, max_items=20,
        help_="Returns the input with the highest quality byte."),
    "HOT_STANDBY": {"fields": [
        _tag_ref("primary", "Primary tag"),
        _tag_ref("standby", "Standby tag"),
    ]},
    "VOTING_M_OF_N": {"fields": [
        _tag_ref_list("Voting tags", min_items=2, max_items=20),
        _number("tolerance", "Tolerance", required=True, min_=0,
                help_="Maximum spread across the agreeing cluster."),
        _integer("min_agreement", "Min agreement", required=False, min_=2,
                 help_="Defaults to strict majority floor(N/2)+1."),
    ]},
    "MUX_INDEX": {"fields": [
        _tag_ref("index", "Index tag", filter_=INTEGER_FILTER,
                 help_="Tag whose value selects the output (0-based)."),
        _tag_ref_list("Value tags", min_items=1, max_items=64),
    ]},

    # ===== Conditional (Tier C) =================================
    "IF_THEN_ELSE": {"fields": [
        _tag_ref("condition", "Condition tag", filter_=BOOL_FILTER),
        _tag_ref("then_value", "Value when TRUE"),
        _tag_ref("else_value", "Value when FALSE"),
    ]},

    # ===== Comparison (Tier C) ==================================
    "GT":  _binary_tag_const_schema(),
    "LT":  _binary_tag_const_schema(),
    "GTE": _binary_tag_const_schema(),
    "LTE": _binary_tag_const_schema(),
    "EQ":  _binary_tag_const_schema(with_tolerance=True),
    "NE":  _binary_tag_const_schema(with_tolerance=True),

    # ===== Logical (Tier C) =====================================
    "AND_OF": _inputs_list_schema(min_items=2, max_items=20,
                                  filter_=BOOL_FILTER,
        help_="Boolean inputs (value > 0 is TRUE)."),
    "OR_OF":  _inputs_list_schema(min_items=2, max_items=20,
                                  filter_=BOOL_FILTER),
    "XOR_OF": _inputs_list_schema(min_items=2, max_items=20,
                                  filter_=BOOL_FILTER,
        help_="N-input XOR: TRUE if odd number of inputs are TRUE."),
    "NOT":    _unary_input_schema(filter_=BOOL_FILTER),

    # ===== Stateful (Tier D) ====================================
    "TON":    _timer_schema(),
    "TOF":    _timer_schema(),
    "TP":     _timer_schema(),
    "R_TRIG": _unary_input_schema(filter_=BOOL_FILTER),
    "F_TRIG": _unary_input_schema(filter_=BOOL_FILTER),
    "SR":     _latch_schema(),
    "RS":     _latch_schema(),
    "CTU": {"fields": [
        _tag_ref("count_up", "Count-up trigger (CU)", filter_=BOOL_FILTER),
        _tag_ref("reset", "Reset tag (R)", filter_=BOOL_FILTER),
    ]},
    "CTD": {"fields": [
        _tag_ref("count_down", "Count-down trigger (CD)", filter_=BOOL_FILTER),
        _tag_ref("load", "Load tag (LD)", filter_=BOOL_FILTER),
        _integer("load_value", "Load value", required=False, default=0,
                 help_="CV is reset to this value when LD goes TRUE."),
    ]},

    # ===== Arithmetic binary (Tier E) ===========================
    # ADD and MUL support BOTH binary and N-ary modes via mode_select.
    # N-ary inputs can MIX tags and constants - each item is either
    # {tag: <id>} or {value: <number>} (Phase 16.0b polish).
    # The other binary blocks (SUB, DIV, MOD, POW, MIN_OF_TWO, MAX_OF_TWO)
    # stay binary-only since N-ary semantics are ambiguous for them.
    "ADD": {"fields": [_mode_select(
        "_add_mode", "Addition mode", "binary",
        [
            ("binary", "Two operands (a + b, or a + constant)", [
                _tag_ref("left", "Left operand"),
                _tag_or_constant(help_="Either another tag or a numeric constant."),
            ]),
            ("n_ary", "Sum of N inputs (mix tags and constants, 2 to 100)", [
                {
                    "key": "inputs",
                    "label": "Inputs (2 to 100)",
                    "type": "tag_or_constant_list",
                    "required": True,
                    "minItems": 2,
                    "maxItems": 100,
                    "help": "Each entry is either a tag or a numeric constant.",
                },
            ]),
        ],
    )]},
    "MUL": {"fields": [_mode_select(
        "_mul_mode", "Multiplication mode", "binary",
        [
            ("binary", "Two operands (a × b, or a × constant)", [
                _tag_ref("left", "Left operand"),
                _tag_or_constant(help_="Either another tag or a numeric constant."),
            ]),
            ("n_ary", "Product of N inputs (mix tags and constants, 2 to 100)", [
                {
                    "key": "inputs",
                    "label": "Inputs (2 to 100)",
                    "type": "tag_or_constant_list",
                    "required": True,
                    "minItems": 2,
                    "maxItems": 100,
                    "help": "Each entry is either a tag or a numeric constant.",
                },
            ]),
        ],
    )]},
    "SUB":         _binary_tag_const_schema(),
    "DIV":         _binary_tag_const_schema(),
    "MOD":         _binary_tag_const_schema(),
    "POW":         _binary_tag_const_schema(),
    "MIN_OF_TWO":  _binary_tag_const_schema(),
    "MAX_OF_TWO":  _binary_tag_const_schema(),

    # ===== Unary math (Tier E) ==================================
    "ABS":   _unary_input_schema(),
    "NEG":   _unary_input_schema(),
    "SQRT":  _unary_input_schema(),
    "FLOOR": _unary_input_schema(),
    "CEIL":  _unary_input_schema(),
    "ROUND": _unary_input_schema(),

    # ===== Transcendental (Tier E) ==============================
    "EXP":   _unary_input_schema(),
    "LN":    _unary_input_schema(),
    "LOG10": _unary_input_schema(),
    "SIN":   _unary_input_schema(),
    "COS":   _unary_input_schema(),
    "TAN":   _unary_input_schema(),
}


def install_schemas() -> tuple[int, list[str]]:
    """Attach CONFIG_SCHEMA to each registered block class.

    Called from calc_blocks/__init__.py after all block modules have
    imported (so BLOCK_REGISTRY is fully populated). Idempotent - safe
    to call multiple times. Blocks that declared CONFIG_SCHEMA inline
    in their class body get OVERWRITTEN by the entry here. That's by
    design until decentralized declarations win out (eventually we'll
    flip this and have the inline declaration win).

    Returns (installed_count, missing_codes) where missing_codes are
    blocks in BLOCK_REGISTRY that lack a schema entry. Production
    deployment is fine when missing_codes is non-empty (those blocks
    just don't render in the UI v2 create form) but we log a warning.
    """
    import logging
    from app.workers.calc_blocks.base import BLOCK_REGISTRY

    log = logging.getLogger("calc_blocks")
    n = 0
    for code, schema in BLOCK_SCHEMAS.items():
        cls = BLOCK_REGISTRY.get(code)
        if cls is None:
            log.warning(
                "calc_block_schemas: schema for %r references an "
                "unregistered block; skipping", code,
            )
            continue
        cls.CONFIG_SCHEMA = schema
        n += 1

    missing = [code for code in BLOCK_REGISTRY if code not in BLOCK_SCHEMAS]
    if missing:
        log.warning(
            "calc_block_schemas: %d registered blocks have no schema: %s",
            len(missing), sorted(missing),
        )

    log.info("calc_block_schemas: installed %d schemas", n)
    return n, sorted(missing)
