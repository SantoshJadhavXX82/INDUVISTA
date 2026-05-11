"""Modbus TCP write helper — CLI tool for writing values to RW tags.

Rounds out the InduVista Modbus master. Phase 1 covered reads (FC 1, 2, 3, 4);
this module covers FC 5 (write single coil), FC 6 (write single register),
FC 15 (write multiple coils), and FC 16 (write multiple registers).

Usage:
  docker compose run --rm backend python -m app.modbus.writer <tag_name> <value>

Examples:
  # Trigger a batch start (CO 1, bool)
  docker compose run --rm backend python -m app.modbus.writer StartBatchCmd 1

  # Set a 16-bit setpoint (HR 400, uint16)
  docker compose run --rm backend python -m app.modbus.writer Write_UINT16 4321

  # Write a 32-bit float (HR 420, float32, 2 regs via FC 16)
  docker compose run --rm backend python -m app.modbus.writer Write_FLOAT32 17.25

  # Skip the read-back verification
  docker compose run --rm backend python -m app.modbus.writer Write_INT16 -100 --no-verify

Discrete inputs (FC 2) and input registers (FC 4) are read-only per the Modbus
spec; the writer rejects them before any network call.

Phase 1 scope: one-shot writes from the command line. Future phases will add
the same capability via a REST endpoint (Phase 3) and an auditable write
journal (Phase 12).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import struct
import sys

from pymodbus.client import AsyncModbusTcpClient
from sqlalchemy import text

from app.db import engine
from app.modbus.decoder import decode_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
)
log = logging.getLogger("write")


def _byte_swap_reg(reg: int) -> int:
    return ((reg & 0xFF) << 8) | ((reg >> 8) & 0xFF)


def encode_value(value, data_type: str, byte_order: str) -> list[int]:
    """Encode a typed value into 16-bit registers.

    Mirrors the simulator's encode_value and is the inverse of
    app.modbus.decoder.decode_value. Duplicated here intentionally so the
    backend has zero dependency on the simulator package — the simulator
    is a dev tool, not a production component.
    """
    if data_type == "bool":
        return [1 if value else 0]

    type_to_struct = {
        "int16": ">h", "uint16": ">H",
        "int32": ">i", "uint32": ">I",
        "int64": ">q", "uint64": ">Q",
        "float32": ">f", "float64": ">d",
    }
    if data_type.startswith(("int", "uint")):
        value = int(value)
    else:
        value = float(value)

    fmt = type_to_struct[data_type]
    packed = struct.pack(fmt, value)
    n_regs = len(packed) // 2
    regs = list(struct.unpack(f">{n_regs}H", packed))

    if n_regs == 1:
        if byte_order in ("BADC", "DCBA"):
            regs = [_byte_swap_reg(regs[0])]
        return regs

    if byte_order == "CDAB":
        regs = list(reversed(regs))
    elif byte_order == "BADC":
        regs = [_byte_swap_reg(r) for r in regs]
    elif byte_order == "DCBA":
        regs = [_byte_swap_reg(r) for r in reversed(regs)]

    return regs


def parse_value(raw: str, data_type: str):
    """Parse a CLI string into the right Python type for this tag's data_type."""
    if data_type == "bool":
        s = raw.strip().lower()
        if s in ("1", "true", "on", "yes", "t"):
            return True
        if s in ("0", "false", "off", "no", "f"):
            return False
        raise ValueError(f"Cannot parse {raw!r} as bool")
    if data_type.startswith(("int", "uint")):
        return int(raw)
    if data_type.startswith("float"):
        return float(raw)
    raise ValueError(f"Unsupported data_type for writes: {data_type}")


async def write_tag(tag_name: str, raw_value_str: str, *, verify: bool = True):
    """Write the given value to the named tag and optionally read it back."""
    with engine.connect() as conn:
        tag = conn.execute(text("""
            SELECT t.id, t.device_id, t.name, t.data_type, t.byte_order,
                   t.function_code, t.address, t.register_count,
                   t.scale, t."offset",
                   d.name AS device_name, d.host, d.port, d.unit_id
            FROM tags t
            JOIN devices d ON d.id = t.device_id
            WHERE t.name = :name AND t.enabled = TRUE AND d.enabled = TRUE
        """), {"name": tag_name}).mappings().first()

    if not tag:
        raise ValueError(f"Tag {tag_name!r} not found (or tag/device disabled)")

    log.info(
        "Tag %r → %s (%s:%d unit=%d) FC%d addr=%d type=%s byte_order=%s",
        tag_name, tag["device_name"], tag["host"], tag["port"],
        tag["unit_id"], tag["function_code"], tag["address"],
        tag["data_type"], tag["byte_order"],
    )

    fc_read = tag["function_code"]
    if fc_read in (2, 4):
        raise ValueError(
            f"Tag {tag_name} sits on a read-only Modbus area (FC{fc_read}); "
            "discrete inputs (FC2) and input registers (FC4) cannot be written"
        )

    eng_value = parse_value(raw_value_str, tag["data_type"])
    log.info("Engineering value parsed: %r (%s)", eng_value, type(eng_value).__name__)

    # Reverse engineering scale + offset to get the raw on-wire value.
    if tag["data_type"] == "bool":
        raw_value = eng_value
    else:
        raw_value = (float(eng_value) - tag["offset"]) / tag["scale"]
        if tag["data_type"].startswith(("int", "uint")):
            raw_value = int(round(raw_value))
    log.info("Raw value (post-scale/offset): %r", raw_value)

    regs = encode_value(raw_value, tag["data_type"], tag["byte_order"])
    log.info("Encoded: %d register(s) → %s", len(regs), regs)

    client = AsyncModbusTcpClient(host=tag["host"], port=tag["port"])
    try:
        await client.connect()
        if not client.connected:
            raise ConnectionError(f"Could not connect to {tag['host']}:{tag['port']}")

        unit_id = tag["unit_id"]
        if fc_read == 1:
            if len(regs) == 1:
                wr = await client.write_coil(
                    address=tag["address"], value=bool(regs[0]), slave=unit_id,
                )
                op = "write_coil (FC 5)"
            else:
                wr = await client.write_coils(
                    address=tag["address"], values=[bool(r) for r in regs], slave=unit_id,
                )
                op = "write_coils (FC 15)"
        elif fc_read == 3:
            if len(regs) == 1:
                wr = await client.write_register(
                    address=tag["address"], value=regs[0], slave=unit_id,
                )
                op = "write_register (FC 6)"
            else:
                wr = await client.write_registers(
                    address=tag["address"], values=regs, slave=unit_id,
                )
                op = "write_registers (FC 16)"
        else:
            raise ValueError(f"Unknown function_code {fc_read}")

        if wr.isError():
            raise RuntimeError(f"Modbus {op} returned an error: {wr}")
        log.info("✓ %s OK", op)

        if verify:
            if fc_read == 1:
                rr = await client.read_coils(
                    address=tag["address"], count=tag["register_count"], slave=unit_id,
                )
                raw = rr.bits[: tag["register_count"]]
            else:
                rr = await client.read_holding_registers(
                    address=tag["address"], count=tag["register_count"], slave=unit_id,
                )
                raw = rr.registers

            if rr.isError():
                log.warning("Read-back failed: %s", rr)
            else:
                decoded = decode_value(raw, tag["data_type"], tag["byte_order"])
                if tag["data_type"] != "bool":
                    decoded = float(decoded) * tag["scale"] + tag["offset"]
                ok = (
                    (tag["data_type"] == "bool" and bool(decoded) == bool(eng_value))
                    or (tag["data_type"] != "bool"
                        and abs(float(decoded) - float(eng_value)) < 1e-5)
                )
                marker = "✓" if ok else "✗"
                log.info("Read-back: %s %s (wrote %r)", decoded, marker, eng_value)
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Write a value to a Modbus tag via its configured device.",
    )
    parser.add_argument("tag_name", help="Tag name from the database (e.g. StartBatchCmd)")
    parser.add_argument("value", help="Value to write; parsed per the tag's data_type")
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip read-back verification (write blind)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(write_tag(args.tag_name, args.value, verify=not args.no_verify))
    except (ValueError, ConnectionError, RuntimeError) as e:
        log.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
