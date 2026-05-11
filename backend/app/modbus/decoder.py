"""Modbus register decoder — inverse of simulator.encode_value.

Takes raw register values from the wire and returns a typed Python value.
The caller is responsible for applying scale + offset (engineering units)
after this returns. Decoding the raw bytes is intentionally one step;
scaling is a separate step so failed scaling and failed decoding produce
different ST reasons.
"""
from __future__ import annotations

import struct
from typing import Sequence, Union


def _byte_swap_reg(reg: int) -> int:
    """Swap the two bytes of a 16-bit register value."""
    return ((reg & 0xFF) << 8) | ((reg >> 8) & 0xFF)


def decode_value(
    registers: Sequence,
    data_type: str,
    byte_order: str = "ABCD",
) -> Union[int, float, bool]:
    """Decode raw register values into a typed Python value.

    For HR (FC 3) and IR (FC 4): `registers` is a list of 16-bit ints.
    For CO (FC 1) and DI (FC 2): `registers` is a list of bools.

    byte_order semantics for multi-register types:
      ABCD = canonical big-endian, registers in natural order
      CDAB = word swap (registers reversed)
      BADC = byte swap within each register
      DCBA = both
    """
    if data_type == "bool":
        return bool(registers[0])

    type_to_struct = {
        "int16": ">h", "uint16": ">H",
        "int32": ">i", "uint32": ">I",
        "int64": ">q", "uint64": ">Q",
        "float32": ">f", "float64": ">d",
    }
    if data_type not in type_to_struct:
        raise ValueError(f"Unsupported data_type: {data_type}")

    fmt = type_to_struct[data_type]
    n_regs = struct.calcsize(fmt) // 2

    if len(registers) < n_regs:
        raise ValueError(
            f"Not enough registers for {data_type}: need {n_regs}, got {len(registers)}"
        )

    regs = list(registers[:n_regs])

    # Reverse the byte_order transformation that the device applied
    if n_regs == 1:
        if byte_order in ("BADC", "DCBA"):
            regs = [_byte_swap_reg(regs[0])]
    else:
        if byte_order == "CDAB":
            regs = list(reversed(regs))
        elif byte_order == "BADC":
            regs = [_byte_swap_reg(r) for r in regs]
        elif byte_order == "DCBA":
            regs = [_byte_swap_reg(r) for r in reversed(regs)]
        # ABCD: no transformation

    packed = struct.pack(f">{n_regs}H", *regs)
    return struct.unpack(fmt, packed)[0]
