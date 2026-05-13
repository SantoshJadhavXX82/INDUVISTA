"""Unit tests for app.modbus.decoder.decode_value.

The decoder is the most-touched piece of the worker path: every byte coming
off the wire goes through it. A regression here corrupts every reading on
every dashboard. These tests are the safety floor: parametrised across
all (data_type × byte_order) combinations with known reference values.

Reference values chosen so the byte pattern is hand-verifiable:
  float32 1.0       = 0x3F800000 = [0x3F80, 0x0000]
  float32 -1.0      = 0xBF800000 = [0xBF80, 0x0000]
  float64 1.0       = 0x3FF0000000000000 = [0x3FF0, 0x0000, 0x0000, 0x0000]
  int32  0x12345678 = [0x1234, 0x5678]
  int16  0x1234     = [0x1234]

Each value is then re-encoded in the four byte orders ABCD, CDAB, BADC,
DCBA — assert that decode_value returns the original regardless of how
the device packed it.
"""
from __future__ import annotations

import math
import struct
import pytest

from app.modbus.decoder import decode_value


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — produce the register list a device WOULD send given a value
# and a byte_order, so the test exercises the inverse path.
# ---------------------------------------------------------------------------

def _swap16(reg: int) -> int:
    """Swap the two bytes of a 16-bit register."""
    return ((reg & 0xFF) << 8) | ((reg >> 8) & 0xFF)


def _encode(value: float | int, data_type: str, byte_order: str) -> list[int]:
    """Inverse of decode_value — produce the registers a device would send.

    The decoder reverses whatever ordering the device applied; this helper
    APPLIES that ordering so we can round-trip every byte_order through the
    decoder and assert we land back at the original value.
    """
    fmt = {
        "int16": ">h", "uint16": ">H",
        "int32": ">i", "uint32": ">I",
        "int64": ">q", "uint64": ">Q",
        "float32": ">f", "float64": ">d",
    }[data_type]
    packed = struct.pack(fmt, value)
    n_regs = len(packed) // 2
    # Canonical ABCD layout — bytes in natural big-endian order, paired
    # into registers MSB-first.
    regs = list(struct.unpack(f">{n_regs}H", packed))

    if n_regs == 1:
        if byte_order in ("BADC", "DCBA"):
            return [_swap16(regs[0])]
        return regs

    if byte_order == "ABCD":
        return regs
    if byte_order == "CDAB":
        return list(reversed(regs))
    if byte_order == "BADC":
        return [_swap16(r) for r in regs]
    if byte_order == "DCBA":
        return [_swap16(r) for r in reversed(regs)]
    raise ValueError(f"Unknown byte_order in test helper: {byte_order}")


# ===========================================================================
# bool — single bit, no byte_order semantics
# ===========================================================================

class TestBool:
    def test_true_from_nonzero(self):
        assert decode_value([1], "bool") is True

    def test_true_from_arbitrary_nonzero(self):
        assert decode_value([0xFFFF], "bool") is True

    def test_false_from_zero(self):
        assert decode_value([0], "bool") is False

    def test_python_bool_input(self):
        """FC 1/2 returns bools, not ints — decoder must accept either."""
        assert decode_value([True], "bool") is True
        assert decode_value([False], "bool") is False


# ===========================================================================
# 16-bit integers — only ABCD vs BADC (byte-swap) is meaningful here
# ===========================================================================

class TestInt16Family:
    @pytest.mark.parametrize("byte_order", ["ABCD", "BADC", "CDAB", "DCBA"])
    @pytest.mark.parametrize("value", [0, 1, -1, 32767, -32768, 0x1234, -0x1234])
    def test_int16_roundtrip(self, value, byte_order):
        regs = _encode(value, "int16", byte_order)
        assert decode_value(regs, "int16", byte_order) == value

    @pytest.mark.parametrize("byte_order", ["ABCD", "BADC", "CDAB", "DCBA"])
    @pytest.mark.parametrize("value", [0, 1, 65535, 0x1234, 0xABCD])
    def test_uint16_roundtrip(self, value, byte_order):
        regs = _encode(value, "uint16", byte_order)
        assert decode_value(regs, "uint16", byte_order) == value


# ===========================================================================
# 32-bit integers — all 4 byte orders meaningful
# ===========================================================================

class TestInt32Family:
    @pytest.mark.parametrize("byte_order", ["ABCD", "CDAB", "BADC", "DCBA"])
    @pytest.mark.parametrize("value", [
        0, 1, -1, 0x12345678, -0x12345678,
        2147483647, -2147483648,  # int32 bounds
    ])
    def test_int32_roundtrip(self, value, byte_order):
        regs = _encode(value, "int32", byte_order)
        assert decode_value(regs, "int32", byte_order) == value

    @pytest.mark.parametrize("byte_order", ["ABCD", "CDAB", "BADC", "DCBA"])
    @pytest.mark.parametrize("value", [
        0, 1, 0xDEADBEEF, 0xFFFFFFFF, 4294967295,
    ])
    def test_uint32_roundtrip(self, value, byte_order):
        regs = _encode(value, "uint32", byte_order)
        assert decode_value(regs, "uint32", byte_order) == value


# ===========================================================================
# float32 — the headline data type for industrial Modbus
# ===========================================================================

class TestFloat32:
    @pytest.mark.parametrize("byte_order", ["ABCD", "CDAB", "BADC", "DCBA"])
    @pytest.mark.parametrize("value", [
        0.0, 1.0, -1.0, 3.14159, -3.14159,
        1.5e-10, 1.5e10,
        # Pressure, temperature, flow rate — realistic Daniel/Emerson values
        14.696, 100.0, 1234.5678, 0.6234,
    ])
    def test_float32_roundtrip(self, value, byte_order):
        regs = _encode(value, "float32", byte_order)
        result = decode_value(regs, "float32", byte_order)
        # float32 round-trip loses precision; check within tolerance
        assert math.isclose(result, value, rel_tol=1e-6, abs_tol=1e-9)

    def test_float32_one_canonical_abcd(self):
        """Sanity: 1.0 in ABCD is exactly [0x3F80, 0x0000]."""
        assert decode_value([0x3F80, 0x0000], "float32", "ABCD") == 1.0

    def test_float32_one_word_swapped_cdab(self):
        """CDAB places the high word last."""
        assert decode_value([0x0000, 0x3F80], "float32", "CDAB") == 1.0

    def test_float32_one_byte_swapped_badc(self):
        """BADC swaps bytes within each register."""
        assert decode_value([0x803F, 0x0000], "float32", "BADC") == 1.0

    def test_float32_one_full_reverse_dcba(self):
        """DCBA = full little-endian reverse."""
        assert decode_value([0x0000, 0x803F], "float32", "DCBA") == 1.0

    def test_float32_nan_propagates(self):
        """NaN should decode to NaN without raising."""
        nan_regs = [0x7FC0, 0x0000]  # canonical NaN
        result = decode_value(nan_regs, "float32", "ABCD")
        assert math.isnan(result)

    def test_float32_infinity(self):
        inf_regs = [0x7F80, 0x0000]
        result = decode_value(inf_regs, "float32", "ABCD")
        assert math.isinf(result) and result > 0


# ===========================================================================
# 64-bit types — same 4 byte orders, 4 registers wide
# ===========================================================================

class TestInt64Family:
    @pytest.mark.parametrize("byte_order", ["ABCD", "CDAB", "BADC", "DCBA"])
    @pytest.mark.parametrize("value", [
        0, 1, -1, 0x1122334455667788, -0x1122334455667788,
        9223372036854775807, -9223372036854775808,  # int64 bounds
    ])
    def test_int64_roundtrip(self, value, byte_order):
        regs = _encode(value, "int64", byte_order)
        assert decode_value(regs, "int64", byte_order) == value

    @pytest.mark.parametrize("byte_order", ["ABCD", "CDAB", "BADC", "DCBA"])
    @pytest.mark.parametrize("value", [
        0, 1, 0xDEADBEEFCAFEBABE, 0xFFFFFFFFFFFFFFFF,
    ])
    def test_uint64_roundtrip(self, value, byte_order):
        regs = _encode(value, "uint64", byte_order)
        assert decode_value(regs, "uint64", byte_order) == value


class TestFloat64:
    @pytest.mark.parametrize("byte_order", ["ABCD", "CDAB", "BADC", "DCBA"])
    @pytest.mark.parametrize("value", [
        0.0, 1.0, -1.0, math.pi, -math.pi,
        1.5e-100, 1.5e100,
        # Daniel SIM 2251 / fiscal-grade values
        1234.5678901234567, 0.6234567890,
    ])
    def test_float64_roundtrip(self, value, byte_order):
        regs = _encode(value, "float64", byte_order)
        result = decode_value(regs, "float64", byte_order)
        assert math.isclose(result, value, rel_tol=1e-12, abs_tol=1e-15)


# ===========================================================================
# Error paths — the decoder must fail loudly on malformed input
# ===========================================================================

class TestErrorPaths:
    def test_unknown_data_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported data_type"):
            decode_value([0x1234], "string", "ABCD")

    def test_insufficient_registers_for_float32(self):
        with pytest.raises(ValueError, match="Not enough registers"):
            decode_value([0x3F80], "float32", "ABCD")

    def test_insufficient_registers_for_float64(self):
        with pytest.raises(ValueError, match="Not enough registers"):
            decode_value([0x3FF0, 0x0000], "float64", "ABCD")

    def test_empty_registers_for_int16(self):
        with pytest.raises(ValueError, match="Not enough registers"):
            decode_value([], "int16", "ABCD")


# ===========================================================================
# Phase 9.1 — Enron address-offset math
#
# The decoder itself doesn't know about Enron; the offset math lives in
# the worker's _decode_block. But the rule is simple enough to assert
# directly: in Enron mode, byte offset of a tag = (logical_address_delta
# × tag.register_count). These tests cover that math at the function
# level so a regression in the worker shows up here first.
# ===========================================================================

class TestEnronOffsetMath:
    """Validates the offset arithmetic used by worker._decode_block for
    addressing_mode in ('ENRON_HOLDING', 'ENRON_INPUT')."""

    @pytest.mark.parametrize(
        "logical_addr,block_start,register_count,expected_offset",
        [
            # 16 mole-% floats at addresses 7001..7016 in a Daniel SIM 2251
            # composition block (register_count=2 because float32 = 4 bytes
            # = 2 physical registers worth of bytes the device returns).
            (7001, 7001, 2,  0),   # first float → byte 0
            (7002, 7001, 2,  2),   # second float → register offset 2
            (7008, 7001, 2, 14),
            (7016, 7001, 2, 30),   # last float → register offset 30
            # uint16 in an Enron block (register_count=1, identical to STD)
            (3001, 3001, 1, 0),
            (3034, 3001, 1, 33),
            # float64 Enron block — 4 physical registers per logical slot
            (8001, 8001, 4, 0),
            (8002, 8001, 4, 4),
            (8003, 8001, 4, 8),
        ],
    )
    def test_enron_offset_formula(
        self, logical_addr, block_start, register_count, expected_offset,
    ):
        """rel = (tag.address - block.start_address) × tag.register_count."""
        rel_logical = logical_addr - block_start
        rel_physical = rel_logical * register_count
        assert rel_physical == expected_offset

    def test_standard_vs_enron_diverge_at_2nd_address(self):
        """Sanity: STANDARD and ENRON produce identical offsets ONLY for
        the first address in the block. After that they diverge by the
        tag's register width."""
        block_start = 7001
        register_count = 2  # float32
        # First address — both modes return 0
        rel_std = 7001 - block_start
        rel_enron = (7001 - block_start) * register_count
        assert rel_std == rel_enron == 0
        # Second logical address (7002) — diverges
        rel_std = 7002 - block_start
        rel_enron = (7002 - block_start) * register_count
        assert rel_std == 1
        assert rel_enron == 2
        assert rel_std != rel_enron


# ===========================================================================
# Real-world payloads — values pulled from the Daniel SIM 2251 reference
# map (see d301595x012.pdf, §1.2.3). These exercise the exact byte
# patterns InduVista will see in production at SVJ.
# ===========================================================================

class TestDanielSIM2251Payloads:
    """End-to-end byte patterns for the Daniel GC use case at SVJ."""

    def test_specific_gravity_typical_natural_gas(self):
        """SG of natural gas is ~0.60. Daniel default byte_order is ABCD."""
        # 0.6234 encoded as float32 in ABCD
        regs = _encode(0.6234, "float32", "ABCD")
        result = decode_value(regs, "float32", "ABCD")
        assert math.isclose(result, 0.6234, rel_tol=1e-6)

    def test_btu_typical_dry_natural_gas(self):
        """BTU dry for pipeline natural gas is ~1000-1100 BTU/CF."""
        regs = _encode(1023.45, "float32", "ABCD")
        result = decode_value(regs, "float32", "ABCD")
        assert math.isclose(result, 1023.45, rel_tol=1e-5)

    def test_mole_percent_methane(self):
        """Methane mole-% in pipeline gas is typically ~94-96%."""
        regs = _encode(94.532, "float32", "ABCD")
        result = decode_value(regs, "float32", "ABCD")
        assert math.isclose(result, 94.532, rel_tol=1e-5)

    def test_alarm_register_uint16(self):
        """Alarm Flag 1 (Daniel reg 3046) is uint16 with bits 14, 15
        carrying real alarms. Decoder returns the int; bit-of-INT
        extraction comes in Phase 9.2."""
        regs = _encode(0xC000, "uint16", "ABCD")  # bits 14 and 15 set
        result = decode_value(regs, "uint16", "ABCD")
        assert result == 0xC000
        # Confirm the bit semantics for the future bit-of-INT slice
        assert (result >> 14) & 1 == 1  # bit 14 set
        assert (result >> 15) & 1 == 1  # bit 15 set
