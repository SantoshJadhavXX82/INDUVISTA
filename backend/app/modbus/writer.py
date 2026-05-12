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
import os
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


async def write_tag(
    tag_name: str,
    raw_value_str: str,
    *,
    verify: bool = True,
    source: str = "cli",
    user_label: str | None = None,
) -> dict:
    """Write the given value to the named tag and optionally read it back.

    Phase 8.5: this function now logs every write to write_journal, regardless
    of success/failure. The journal entry includes the tag name snapshot, the
    raw requested value, the FC used, the latency, and the verify-read value
    if applicable. Result returned as a dict so the REST endpoint can serialize it.

    Returns:
        {
            "success": bool,
            "error": str | None,
            "latency_ms": float | None,
            "verify_value": Any | None,
            "function_code": int | None,  # actual write FC (5/6/15/16)
            "journal_id": int | None,
        }
    """
    import time as _time
    result: dict = {
        "success": False,
        "error": None,
        "latency_ms": None,
        "verify_value": None,
        "function_code": None,
        "journal_id": None,
    }

    with engine.connect() as conn:
        tag = conn.execute(text("""
            SELECT t.id, t.device_id, t.name, t.data_type, t.byte_order,
                   t.function_code, t.address, t.register_count,
                   t.scale, t."offset",
                   t.register_block_id, t.writable AS tag_writable,
                   rb.writable AS block_writable,
                   d.name AS device_name, d.host, d.port, d.unit_id,
                   d.request_timeout_ms,
                   lv.value_double AS lv_value_double,
                   lv.value_text   AS lv_value_text
            FROM tags t
            JOIN devices d ON d.id = t.device_id
            LEFT JOIN register_blocks rb ON rb.id = t.register_block_id
            LEFT JOIN latest_tag_values lv ON lv.tag_id = t.id
            WHERE t.name = :name AND t.enabled = TRUE AND d.enabled = TRUE
        """), {"name": tag_name}).mappings().first()

    if not tag:
        result["error"] = f"Tag {tag_name!r} not found (or tag/device disabled)"
        # Log to journal with success=false. tag_id null because we can't resolve it.
        result["journal_id"] = _journal_write(
            tag_id=None, tag_name=tag_name, source=source,
            user_label=user_label, function_code=0, address=0,
            requested_value=raw_value_str, success=False,
            error=result["error"], verify_value=None, latency_ms=None,
            value_before=None,
        )
        return result

    # Phase 8.5.1 — capture pre-write value from latest_tag_values for audit.
    # This is "what the system most recently believed was there" — up to one
    # scan_interval_ms stale, which matches industrial fiscal-computer practice.
    value_before = _format_audit_value(
        tag["lv_value_double"], tag["lv_value_text"], tag["data_type"],
    )

    log.info(
        "Tag %r → %s (%s:%d unit=%d) FC%d addr=%d type=%s byte_order=%s "
        "value_before=%s",
        tag_name, tag["device_name"], tag["host"], tag["port"],
        tag["unit_id"], tag["function_code"], tag["address"],
        tag["data_type"], tag["byte_order"], value_before,
    )

    # Phase 8.5.1 — writability enforcement.
    # Tags must be explicitly opted in. The reasoning is policy, not protocol —
    # the Modbus spec already prevents writes to FC 2/4 (DI/IR), but the
    # writable flag adds engineering judgment: "yes, this Coil/HR is a
    # setpoint, not a measurement that happens to share an address space".
    fc_read = tag["function_code"]
    if fc_read in (2, 4):
        result["error"] = (
            f"Tag {tag_name} is on a read-only Modbus area (FC{fc_read}); "
            "discrete inputs (FC2) and input registers (FC4) cannot be written"
        )
        result["journal_id"] = _journal_write(
            tag_id=tag["id"], tag_name=tag_name, source=source,
            user_label=user_label, function_code=fc_read, address=tag["address"],
            requested_value=raw_value_str, success=False,
            error=result["error"], verify_value=None, latency_ms=None,
            value_before=value_before,
        )
        return result

    if not tag["tag_writable"]:
        result["error"] = (
            f"Tag {tag_name} is not configured writable. Mark it as Writable "
            "in the Tag editor before issuing writes."
        )
        result["journal_id"] = _journal_write(
            tag_id=tag["id"], tag_name=tag_name, source=source,
            user_label=user_label, function_code=fc_read, address=tag["address"],
            requested_value=raw_value_str, success=False,
            error=result["error"], verify_value=None, latency_ms=None,
            value_before=value_before,
        )
        return result

    if tag["register_block_id"] is not None and not tag["block_writable"]:
        result["error"] = (
            f"Tag {tag_name}'s block is not configured writable. "
            "Mark the parent Register Block as Read+Write before issuing writes."
        )
        result["journal_id"] = _journal_write(
            tag_id=tag["id"], tag_name=tag_name, source=source,
            user_label=user_label, function_code=fc_read, address=tag["address"],
            requested_value=raw_value_str, success=False,
            error=result["error"], verify_value=None, latency_ms=None,
            value_before=value_before,
        )
        return result

    try:
        eng_value = parse_value(raw_value_str, tag["data_type"])
    except ValueError as e:
        result["error"] = str(e)
        result["journal_id"] = _journal_write(
            tag_id=tag["id"], tag_name=tag_name, source=source,
            user_label=user_label, function_code=fc_read, address=tag["address"],
            requested_value=raw_value_str, success=False,
            error=result["error"], verify_value=None, latency_ms=None,
            value_before=value_before,
        )
        return result

    log.info("Engineering value parsed: %r (%s)", eng_value, type(eng_value).__name__)

    # Reverse engineering scale + offset
    if tag["data_type"] == "bool":
        raw_value = eng_value
    else:
        raw_value = (float(eng_value) - tag["offset"]) / tag["scale"]
        if tag["data_type"].startswith(("int", "uint")):
            raw_value = int(round(raw_value))
    log.info("Raw value (post-scale/offset): %r", raw_value)

    regs = encode_value(raw_value, tag["data_type"], tag["byte_order"])
    log.info("Encoded: %d register(s) → %s", len(regs), regs)

    timeout_sec = (tag.get("request_timeout_ms") or 3000) / 1000.0
    client = AsyncModbusTcpClient(
        host=tag["host"], port=tag["port"], timeout=timeout_sec,
    )
    write_fc: int | None = None
    verify_value = None
    t0 = _time.monotonic()
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
                write_fc = 5
            else:
                wr = await client.write_coils(
                    address=tag["address"], values=[bool(r) for r in regs], slave=unit_id,
                )
                op = "write_coils (FC 15)"
                write_fc = 15
        elif fc_read == 3:
            if len(regs) == 1:
                wr = await client.write_register(
                    address=tag["address"], value=regs[0], slave=unit_id,
                )
                op = "write_register (FC 6)"
                write_fc = 6
            else:
                wr = await client.write_registers(
                    address=tag["address"], values=regs, slave=unit_id,
                )
                op = "write_registers (FC 16)"
                write_fc = 16
        else:
            raise ValueError(f"Unknown function_code {fc_read}")

        if wr.isError():
            raise RuntimeError(f"Modbus {op} returned an error: {wr}")
        log.info("✓ %s OK", op)
        result["function_code"] = write_fc

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
                verify_value = decoded
                ok = (
                    (tag["data_type"] == "bool" and bool(decoded) == bool(eng_value))
                    or (tag["data_type"] != "bool"
                        and abs(float(decoded) - float(eng_value)) < 1e-5)
                )
                marker = "✓" if ok else "✗"
                log.info("Read-back: %s %s (wrote %r)", decoded, marker, eng_value)

        result["latency_ms"] = (_time.monotonic() - t0) * 1000
        result["success"] = True
        result["verify_value"] = verify_value

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["latency_ms"] = (_time.monotonic() - t0) * 1000
        log.error("Write failed: %s", result["error"])
    finally:
        try:
            client.close()
        except Exception:
            pass

    # Always log to journal, success or failure
    result["journal_id"] = _journal_write(
        tag_id=tag["id"], tag_name=tag_name, source=source,
        user_label=user_label, function_code=write_fc or fc_read,
        address=tag["address"], requested_value=raw_value_str,
        success=result["success"], error=result["error"],
        verify_value=str(verify_value) if verify_value is not None else None,
        latency_ms=result["latency_ms"],
        value_before=value_before,
    )
    return result


def _format_audit_value(
    value_double, value_text, data_type: str,
) -> str | None:
    """Format latest_tag_values content for the audit journal.

    Mirrors the UI's value display so the 'before' string in the journal
    matches what someone would have seen on the Live page when the write
    was issued.
    """
    if value_text is not None and value_text != "":
        return str(value_text)[:64]
    if value_double is None:
        return None
    if data_type == "bool":
        return "true" if value_double else "false"
    if data_type.startswith("float"):
        # Strip trailing zeros for readability, but keep at least 1 decimal
        return f"{value_double:.6g}"
    # int types — stringify as int
    try:
        return str(int(value_double))
    except (ValueError, OverflowError):
        return str(value_double)


def _journal_write(
    *, tag_id: int | None, tag_name: str, source: str,
    user_label: str | None, function_code: int, address: int,
    requested_value: str, success: bool, error: str | None,
    verify_value: str | None, latency_ms: float | None,
    value_before: str | None = None,
) -> int | None:
    """Best-effort write to write_journal. Returns the new id or None on failure.

    NEVER raises — auditing failures must not break the write API.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO write_journal (
                    tag_id, tag_name_snapshot, source, user_label,
                    function_code, address, requested_value,
                    success, error, verify_value, latency_ms, value_before
                ) VALUES (
                    :tag_id, :tag_name, :source, :user_label,
                    :fc, :addr, :req, :success, :error, :verify, :latency,
                    :vbefore
                ) RETURNING id
            """), {
                "tag_id": tag_id, "tag_name": tag_name[:255],
                "source": source, "user_label": (user_label or "")[:128] or None,
                "fc": function_code or 0, "addr": address,
                "req": requested_value, "success": success,
                "error": error, "verify": verify_value,
                "latency": latency_ms,
                "vbefore": value_before,
            }).first()
            return int(row[0]) if row else None
    except Exception:
        log.exception("Failed to write to write_journal — audit lost")
        return None


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
    parser.add_argument(
        "--user", default=os.environ.get("USER", "unknown"),
        help="Label written to the audit journal (default: $USER)",
    )
    args = parser.parse_args()

    result = asyncio.run(write_tag(
        args.tag_name, args.value,
        verify=not args.no_verify,
        source="cli", user_label=args.user,
    ))
    if not result["success"]:
        log.error("Write failed: %s", result["error"])
        sys.exit(1)
    log.info(
        "✓ write OK (FC %s, %.1fms, journal id=%s)",
        result["function_code"], result["latency_ms"] or 0.0,
        result["journal_id"],
    )


if __name__ == "__main__":
    main()
