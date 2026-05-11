#!/usr/bin/env python3
"""InduVista Modbus TCP simulator — gas flow computer.

Reads tag definitions from a JSON config (default /app/config/gas_flow_computer.json),
exposes them on a Modbus TCP server (default 0.0.0.0:5020), and updates simulated
values in the background according to each tag's `sim` block.

Phase 1 dev tool. Not part of the production system.
"""

import asyncio
import json
import logging
import math
import os
import random
import struct
import time
from pathlib import Path

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sim")

CONFIG_ROOT = Path(os.environ.get("CONFIG_ROOT", "/app/config"))
SIM_DEVICE = os.environ.get("SIM_DEVICE", "FLOWCOMP_001")

# Datastore sizes — generous enough for the gas-flow-computer map (max addr 2543).
HR_SIZE = 4096
IR_SIZE = 1024
CO_SIZE = 256
DI_SIZE = 256

UPDATE_INTERVAL = 1.0          # seconds between dynamic value refreshes
SAMPLE_LOG_INTERVAL = 10.0     # seconds between visible activity logs


def _byte_swap_reg(reg: int) -> int:
    """Swap the two bytes of a 16-bit register value."""
    return ((reg & 0xFF) << 8) | ((reg >> 8) & 0xFF)


def encode_value(value, data_type: str, byte_order: str) -> list[int]:
    """Encode a typed value into a list of 16-bit register values.

    byte_order semantics (for multi-register types):
      ABCD = canonical big-endian, registers in natural order
      CDAB = word swap (registers reversed)
      BADC = byte swap within each register
      DCBA = both
    """
    if data_type == "bool":
        return [1 if value else 0]

    type_to_struct = {
        "int16": ">h", "uint16": ">H",
        "int32": ">i", "uint32": ">I",
        "int64": ">q", "uint64": ">Q",
        "float32": ">f", "float64": ">d",
    }
    # Coerce — JSON loads every number as float, and ramp/step modes return
    # floats too, but struct's integer formats require int.
    if data_type.startswith(("int", "uint")):
        value = int(value)
    else:
        value = float(value)

    fmt = type_to_struct[data_type]
    packed = struct.pack(fmt, value)
    n_regs = len(packed) // 2
    regs = list(struct.unpack(f">{n_regs}H", packed))

    if n_regs == 1:
        # Single-register types — only byte swap matters
        if byte_order in ("BADC", "DCBA"):
            regs = [_byte_swap_reg(regs[0])]
        return regs

    if byte_order == "CDAB":
        regs = list(reversed(regs))
    elif byte_order == "BADC":
        regs = [_byte_swap_reg(r) for r in regs]
    elif byte_order == "DCBA":
        regs = [_byte_swap_reg(r) for r in reversed(regs)]
    # ABCD: no transformation

    return regs


def compute_value(tag: dict, t: float, prev_value):
    """Compute a tag's current simulated value given elapsed time t (seconds)."""
    sim = tag["sim"]
    mode = sim.get("mode", "static")
    initial = sim.get("initial")
    period = sim.get("period_s") or 60.0

    if mode == "static":
        return initial if initial is not None else 0

    if tag["data_type"] == "bool":
        if mode == "toggle":
            return (int(t / period) % 2) == 1
        if mode == "random":
            return random.random() < 0.5
        return bool(initial) if initial is not None else False

    smin = sim.get("min")
    smax = sim.get("max")
    if smin is None:
        smin = initial if initial is not None else 0
    if smax is None:
        smax = smin

    if mode == "sine":
        center = (smin + smax) / 2.0
        amplitude = (smax - smin) / 2.0
        return center + amplitude * math.sin(2 * math.pi * t / period)

    if mode == "random":
        return random.uniform(smin, smax)

    if mode == "ramp":
        frac = (t % period) / period
        return smin + (smax - smin) * frac

    if mode == "step":
        base = prev_value if prev_value is not None else (
            initial if initial is not None else 0
        )
        return base + 1

    return initial if initial is not None else 0


def build_context(config: dict) -> ModbusServerContext:
    """Build the pymodbus server context and seed initial values."""
    hr = ModbusSequentialDataBlock(0, [0] * HR_SIZE)
    ir = ModbusSequentialDataBlock(0, [0] * IR_SIZE)
    co = ModbusSequentialDataBlock(0, [False] * CO_SIZE)
    di = ModbusSequentialDataBlock(0, [False] * DI_SIZE)

    # zero_mode=True disables ModbusSlaveContext's legacy "address + 1" remap.
    # Without it, reads through the slave context request internal position N+1
    # while our direct `slave.store["h"].setValues(addr, ...)` writes to position
    # N, causing an off-by-one across every multi-register tag. The mismatch is
    # silent — pymodbus has no diagnostic for it — and produces chaotic float
    # values that span the float32 range. With zero_mode=True, PDU address N
    # maps to internal position N on both paths.
    slave = ModbusSlaveContext(di=di, co=co, hr=hr, ir=ir, zero_mode=True)

    seeded = 0
    for tag in config["tags"]:
        initial = tag["sim"].get("initial")
        if initial is None:
            continue
        try:
            regs = encode_value(initial, tag["data_type"], tag["byte_order"])
        except (struct.error, KeyError, ValueError) as e:
            log.warning("encode initial failed for %s: %s", tag["name"], e)
            continue

        fc, addr = tag["function_code"], tag["address"]
        try:
            if fc == 3:
                hr.setValues(addr, regs)
            elif fc == 4:
                ir.setValues(addr, regs)
            elif fc == 2:
                di.setValues(addr, [bool(regs[0])])
            elif fc == 1:
                co.setValues(addr, [bool(regs[0])])
            seeded += 1
        except Exception as e:
            log.warning("seed setValues failed for %s @ %d: %s", tag["name"], addr, e)

    log.info("Seeded %d/%d tags with initial values", seeded, len(config["tags"]))

    return ModbusServerContext(
        slaves={config["device"]["unit_id"]: slave},
        single=False,
    )


async def update_loop(context: ModbusServerContext, config: dict, start_t: float):
    """Refresh all dynamic tag values once per UPDATE_INTERVAL."""
    last_values: dict[str, float] = {}
    unit_id = config["device"]["unit_id"]
    slave = context[unit_id]

    while True:
        await asyncio.sleep(UPDATE_INTERVAL)
        t = time.monotonic() - start_t
        for tag in config["tags"]:
            if tag["sim"].get("mode", "static") == "static":
                continue

            try:
                val = compute_value(tag, t, last_values.get(tag["name"]))
            except Exception as e:
                log.warning("compute_value failed for %s: %s", tag["name"], e)
                continue

            if isinstance(val, (int, float)):
                last_values[tag["name"]] = float(val)

            try:
                regs = encode_value(val, tag["data_type"], tag["byte_order"])
            except Exception as e:
                log.warning("encode_value failed for %s: %s", tag["name"], e)
                continue

            fc, addr = tag["function_code"], tag["address"]
            try:
                if fc == 3:
                    slave.store["h"].setValues(addr, regs)
                elif fc == 4:
                    slave.store["i"].setValues(addr, regs)
                elif fc == 2:
                    slave.store["d"].setValues(addr, [bool(regs[0])])
                elif fc == 1:
                    slave.store["c"].setValues(addr, [bool(regs[0])])
            except Exception as e:
                log.warning("setValues failed for %s @ %d: %s", tag["name"], addr, e)


async def sample_log_loop(config: dict, start_t: float):
    """Log a few sample tag values every 10s so docker compose logs shows activity."""
    sample_names = (
        "S1_PressureTx_mA", "S1_TurbineFreq",
        "CUR_GSVOL", "CUR_LINE_PRESSURE", "CUR_LINE_TEMPERATURE",
        "S1_PulseCount",
    )
    by_name = {t["name"]: t for t in config["tags"]}
    samples = [by_name[n] for n in sample_names if n in by_name]

    last_values: dict[str, float] = {}
    while True:
        await asyncio.sleep(SAMPLE_LOG_INTERVAL)
        t = time.monotonic() - start_t
        parts = []
        for tag in samples:
            try:
                val = compute_value(tag, t, last_values.get(tag["name"]))
                if isinstance(val, (int, float)):
                    last_values[tag["name"]] = float(val)
                eu = tag.get("engineering_unit") or ""
                if isinstance(val, float):
                    parts.append(f"{tag['name']}={val:g}{eu}")
                else:
                    parts.append(f"{tag['name']}={val}{eu}")
            except Exception:
                pass
        log.info("sample → " + " | ".join(parts))


async def main():
    device_path = CONFIG_ROOT / "devices" / f"{SIM_DEVICE}.json"
    log.info("Loading device config from %s", device_path)
    if not device_path.exists():
        log.error(
            "Device config not found at %s — check CONFIG_ROOT/SIM_DEVICE env",
            device_path,
        )
        raise SystemExit(1)

    with open(device_path, "r", encoding="utf-8") as f:
        device = json.load(f)

    template_path = CONFIG_ROOT / "device_templates" / f"{device['template']}.json"
    log.info("Loading template from %s", template_path)
    if not template_path.exists():
        log.error("Template not found at %s", template_path)
        raise SystemExit(1)

    with open(template_path, "r", encoding="utf-8") as f:
        template = json.load(f)

    # Combine device instance + template into the shape the rest of this
    # module expects (unchanged since Phase 1).
    config = {
        "device": {
            "name": device["name"],
            "description": device.get("description"),
            "unit_id": device["unit_id"],
            # The simulator binds to 0.0.0.0 regardless of the device's
            # configured host (which is what *clients* should connect to).
            "modbus_host": "0.0.0.0",
            "modbus_port": device["port"],
        },
        "register_blocks": template["register_blocks"],
        "tags": template["tags"],
        "bit_labels": template.get("bit_labels", {}),
    }

    log.info(
        "Pretending to be device %s using template %s — %d tags across %d blocks",
        device["name"], device["template"],
        len(config["tags"]),
        len(config["register_blocks"]),
    )

    context = build_context(config)
    start_t = time.monotonic()

    # Background tasks — must hold references or they get garbage collected
    asyncio.create_task(update_loop(context, config, start_t))
    asyncio.create_task(sample_log_loop(config, start_t))

    host = config["device"]["modbus_host"]
    port = config["device"]["modbus_port"]
    unit = config["device"]["unit_id"]
    log.info("Modbus TCP server starting on %s:%d (unit %d)", host, port, unit)

    await StartAsyncTcpServer(context=context, address=(host, port))


if __name__ == "__main__":
    asyncio.run(main())
