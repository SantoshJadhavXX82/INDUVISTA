"""Data-type metadata — canonical sizes and width inference.

`register_count` was traditionally interpreted two ways depending on
addressing mode:

  * STANDARD mode → number of 16-bit registers the tag's bytes span.
    A float32 spans registers N and N+1, so register_count = 2.

  * ENRON mode → wire-width-divided-by-2. Each Enron logical address
    holds one value, but the wire returns N×wire_width bytes for N
    addresses. The Enron channel needs to know that width to slice the
    response correctly.

Either way, the canonical value of `register_count` is fully determined
by the data_type:

    bool   / int16  / uint16   →  1   (2 bytes)
    int32  / uint32 / float32  →  2   (4 bytes)
    int64  / uint64 / float64  →  4   (8 bytes)

Storing it as a separate column gives the database flexibility for
non-canonical edge cases (e.g., reading the first 2 bytes of a 4-byte
field), but in practice every well-formed tag matches the canonical
value. The API auto-derives it when the client doesn't supply one, and
rejects mismatches in Enron mode (where they would break wire-width
inference).
"""
from __future__ import annotations


# Canonical register_count per data_type. Keep in sync with
# app/modbus/decoder.py's type_to_struct sizes.
CANONICAL_REGISTER_COUNT: dict[str, int] = {
    "bool":    1,
    "int16":   1,
    "uint16":  1,
    "int32":   2,
    "uint32":  2,
    "float32": 2,
    "int64":   4,
    "uint64":  4,
    "float64": 4,
}


KNOWN_DATA_TYPES: frozenset[str] = frozenset(CANONICAL_REGISTER_COUNT.keys())


def canonical_register_count(data_type: str) -> int | None:
    """Return the natural register_count for a data_type, or None if unknown."""
    return CANONICAL_REGISTER_COUNT.get(data_type)


def width_bytes(data_type: str) -> int | None:
    """Return the on-wire byte width of a value, or None if unknown."""
    rc = CANONICAL_REGISTER_COUNT.get(data_type)
    return rc * 2 if rc is not None else None
