#!/usr/bin/env python3
"""Seed one computed tag per block as browseable fixtures.

Creates two computed devices:
  - BLOCKTEST_INPUTS: 15 helper constant tags (VAL_2=2.0, VAL_4=4.0, etc.)
    Each is an ADD N-ary block with one {"value": X} item. No real channel
    needed - these are pure constants.
  - BLOCKTEST_BLOCKS: 56 computed tags, one per stateless block (including
    ADD's binary/N-ary/mixed-input variants and MUL's N-ary variant).

Idempotent: existing names by tag are skipped. Use --wipe to delete and
recreate cleanly.

Usage:
    python -m pip install requests
    python seed_block_tags.py            # create what's missing
    python seed_block_tags.py --wipe     # delete BLOCKTEST_* and recreate
"""
from __future__ import annotations

import argparse
import math
import sys

import requests

BASE = "http://localhost:8000/api"
INPUT_DEVICE = "BLOCKTEST_INPUTS"
TEST_DEVICE = "BLOCKTEST_BLOCKS"


# ---------------------------------------------------------------------------
# Helper input values (each becomes a computed tag named VAL_*)
# ---------------------------------------------------------------------------

INPUT_VALUES: dict[str, float] = {
    "VAL_2": 2.0, "VAL_3": 3.0, "VAL_4": 4.0, "VAL_5": 5.0,
    "VAL_6": 6.0, "VAL_8": 8.0, "VAL_10": 10.0,
    "VAL_0": 0.0, "VAL_NEG3": -3.0, "VAL_100": 100.0,
    "VAL_2P5": 2.5, "VAL_2P3": 2.3, "VAL_2P7": 2.7,
    "VAL_E": math.e, "VAL_PI4": math.pi / 4,
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def api_post(path: str, body: dict):
    r = requests.post(f"{BASE}{path}", json=body, timeout=10)
    if not r.ok:
        raise RuntimeError(f"POST {path}: HTTP {r.status_code}\n{r.text[:300]}")
    return r.json()

def api_get(path: str):
    r = requests.get(f"{BASE}{path}", timeout=10)
    r.raise_for_status()
    return r.json()

def api_delete(path: str):
    r = requests.delete(f"{BASE}{path}", timeout=10)
    if not r.ok and r.status_code != 404:
        raise RuntimeError(f"DELETE {path}: HTTP {r.status_code}")


# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------

def head(msg: str): print(f"\n=== {msg} ===")
def ok(msg: str):   print(f"  \033[32m+\033[0m {msg}")
def skip(msg: str): print(f"  \033[36m=\033[0m {msg}")
def warn(msg: str): print(f"  \033[33m!\033[0m {msg}")
def info(msg: str): print(f"  {msg}")


# ---------------------------------------------------------------------------
# Idempotency helpers
# ---------------------------------------------------------------------------

def find_device(name: str) -> int | None:
    for d in api_get("/computed-devices"):
        if d.get("name") == name:
            return d["id"]
    return None

def get_or_create_device(name: str, description: str) -> int:
    existing = find_device(name)
    if existing is not None:
        skip(f"device exists: {name} (id={existing})")
        return existing
    result = api_post("/computed-devices", {"name": name, "description": description})
    ok(f"created device {name} (id={result['id']})")
    return result["id"]

def tags_for_device(device_id: int) -> dict[str, int]:
    """Return {name: tag_id} for tags belonging to the given computed device."""
    out: dict[str, int] = {}
    for t in api_get("/computed-tags"):
        dev = t.get("computed_device_id", t.get("device_id"))
        if dev == device_id:
            out[t["name"]] = t["id"]
    return out


# ---------------------------------------------------------------------------
# Wipe
# ---------------------------------------------------------------------------

def wipe():
    head("Wiping prior BLOCKTEST_*")
    devices = api_get("/computed-devices")
    target_ids = [d["id"] for d in devices if d.get("name") in (INPUT_DEVICE, TEST_DEVICE)]
    if not target_ids:
        info("nothing to wipe")
        return
    tags = api_get("/computed-tags")
    for t in tags:
        dev = t.get("computed_device_id", t.get("device_id"))
        if dev in target_ids:
            try:
                api_delete(f"/computed-tags/{t['id']}")
            except Exception as e:
                warn(f"delete tag {t.get('name')}: {e}")
    for did in target_ids:
        try:
            api_delete(f"/computed-devices/{did}")
            ok(f"deleted device id={did}")
        except Exception as e:
            warn(f"delete device {did}: {e}")


# ---------------------------------------------------------------------------
# Phase 1: input helper tags
# ---------------------------------------------------------------------------

def seed_inputs(input_dev_id: int) -> dict[str, int]:
    """Create one ADD N-ary tag per INPUT_VALUES item, output = the constant.
    Returns {name: tag_id}, including pre-existing ones."""
    head("Helper input tags")
    existing = tags_for_device(input_dev_id)
    result: dict[str, int] = {}
    for name, val in INPUT_VALUES.items():
        if name in existing:
            result[name] = existing[name]
            skip(f"{name} (id={existing[name]})")
            continue
        body = {
            "device_id": input_dev_id,
            "name": name,
            "block_type": "ADD",
            "block_config": {"inputs": [{"value": val}]},
            "data_type": "float",
            "execution_priority": 1,  # run before downstream
        }
        try:
            r = api_post("/computed-tags", body)
            result[name] = r["id"]
            ok(f"{name} = {val} (id={r['id']})")
        except Exception as e:
            warn(f"create {name}: {e}")
    return result


# ---------------------------------------------------------------------------
# Phase 2: block fixture tags
# ---------------------------------------------------------------------------

def block_specs(ids: dict[str, int]) -> list[tuple[str, str, dict]]:
    """Return (suffix, block_code, block_config) for every block.
    Names will be BT_<suffix>."""
    v = lambda k: ids[k]
    inputs5 = [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]
    return [
        # === Aggregation Tier A (15) ===
        ("AVG_OF",         "AVG_OF",         {"inputs": inputs5}),
        ("MIN_OF",         "MIN_OF",         {"inputs": inputs5}),
        ("MAX_OF",         "MAX_OF",         {"inputs": inputs5}),
        ("MEDIAN_OF",      "MEDIAN_OF",      {"inputs": inputs5}),
        ("MODE_OF",        "MODE_OF",        {"inputs": inputs5}),
        ("RANGE_OF",       "RANGE_OF",       {"inputs": inputs5}),
        ("STDDEV_OF",      "STDDEV_OF",      {"inputs": inputs5}),
        ("VARIANCE_OF",    "VARIANCE_OF",    {"inputs": inputs5}),
        ("RMS_OF",         "RMS_OF",         {"inputs": inputs5}),
        ("PRODUCT_OF",     "PRODUCT_OF",     {"inputs": inputs5}),
        ("GEOMETRIC_MEAN", "GEOMETRIC_MEAN", {"inputs": inputs5}),
        ("HARMONIC_MEAN",  "HARMONIC_MEAN",  {"inputs": inputs5}),
        ("WEIGHTED_AVG",   "WEIGHTED_AVG",   {"inputs": inputs5, "weights": [1, 1, 1, 1, 1]}),
        ("COUNT_GOOD",     "COUNT_GOOD",     {"inputs": inputs5}),
        ("COUNT_NONZERO",  "COUNT_NONZERO",  {"inputs": inputs5}),

        # === Arithmetic Tier E - Binary (8) + variants ===
        ("ADD",        "ADD",        {"left": v("VAL_2"),  "right": v("VAL_4")}),
        ("ADD_NARY",   "ADD",        {"inputs": [{"value": 2}, {"value": 4}, {"value": 6}]}),
        ("ADD_MIXED",  "ADD",        {"inputs": [{"tag": v("VAL_4")}, {"value": 10}]}),
        ("SUB",        "SUB",        {"left": v("VAL_10"), "right": v("VAL_2")}),
        ("MUL",        "MUL",        {"left": v("VAL_4"),  "right": v("VAL_6")}),
        ("MUL_NARY",   "MUL",        {"inputs": [{"value": 2}, {"value": 3}, {"value": 4}]}),
        ("DIV",        "DIV",        {"left": v("VAL_10"), "right": v("VAL_4")}),
        ("MOD",        "MOD",        {"left": v("VAL_10"), "right": v("VAL_4")}),
        ("POW",        "POW",        {"left": v("VAL_2"),  "value": 3}),
        ("MIN_OF_TWO", "MIN_OF_TWO", {"left": v("VAL_4"),  "right": v("VAL_2")}),
        ("MAX_OF_TWO", "MAX_OF_TWO", {"left": v("VAL_4"),  "right": v("VAL_2")}),

        # === Arithmetic Tier E - Unary (6) ===
        ("ABS",   "ABS",   {"input": v("VAL_NEG3")}),
        ("NEG",   "NEG",   {"input": v("VAL_2")}),
        ("SQRT",  "SQRT",  {"input": v("VAL_4")}),
        ("FLOOR", "FLOOR", {"input": v("VAL_2P7")}),
        ("CEIL",  "CEIL",  {"input": v("VAL_2P3")}),
        ("ROUND", "ROUND", {"input": v("VAL_2P5")}),

        # === Arithmetic Tier E - Transcendental (6) ===
        ("EXP",   "EXP",   {"input": v("VAL_2")}),
        ("LN",    "LN",    {"input": v("VAL_E")}),
        ("LOG10", "LOG10", {"input": v("VAL_100")}),
        ("SIN",   "SIN",   {"input": v("VAL_PI4")}),
        ("COS",   "COS",   {"input": v("VAL_PI4")}),
        ("TAN",   "TAN",   {"input": v("VAL_PI4")}),

        # === Selection Tier B (6) ===
        ("FIRST_GOOD",      "FIRST_GOOD",      {"inputs": [v("VAL_2"), v("VAL_4")]}),
        ("LAST_GOOD",       "LAST_GOOD",       {"inputs": [v("VAL_2"), v("VAL_4")]}),
        ("HIGHEST_QUALITY", "HIGHEST_QUALITY", {"inputs": [v("VAL_2"), v("VAL_4")]}),
        ("HOT_STANDBY",     "HOT_STANDBY",     {"primary": v("VAL_2"), "standby": v("VAL_4")}),
        ("VOTING_M_OF_N",   "VOTING_M_OF_N",   {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6")], "tolerance": 10}),
        ("MUX_INDEX",       "MUX_INDEX",       {"index": v("VAL_2"), "values": [v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}),

        # === Conditional / Comparison / Logic Tier C (11) ===
        ("IF_THEN_ELSE", "IF_THEN_ELSE", {"condition": v("VAL_2"), "then_value": v("VAL_4"), "else_value": v("VAL_6")}),
        ("GT",     "GT",     {"left": v("VAL_4"), "value": 2}),
        ("LT",     "LT",     {"left": v("VAL_2"), "value": 4}),
        ("GTE",    "GTE",    {"left": v("VAL_2"), "value": 2}),
        ("LTE",    "LTE",    {"left": v("VAL_2"), "value": 2}),
        ("EQ",     "EQ",     {"left": v("VAL_2"), "value": 2}),
        ("NE",     "NE",     {"left": v("VAL_2"), "value": 3}),
        ("AND_OF", "AND_OF", {"inputs": [v("VAL_2"), v("VAL_4")]}),
        ("OR_OF",  "OR_OF",  {"inputs": [v("VAL_0"), v("VAL_4")]}),
        ("XOR_OF", "XOR_OF", {"inputs": [v("VAL_2"), v("VAL_0")]}),
        ("NOT",    "NOT",    {"input": v("VAL_0")}),

        # === SUM_OF (1) ===
        ("SUM_OF", "SUM_OF", {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6")]}),
    ]


def seed_blocks(test_dev_id: int, input_ids: dict[str, int]) -> tuple[int, int, int]:
    """Create one computed tag per block spec. Returns (created, skipped, failed)."""
    head("Block fixture tags")
    existing = tags_for_device(test_dev_id)
    created = skipped = failed = 0
    for suffix, code, cfg in block_specs(input_ids):
        name = f"BT_{suffix}"
        if name in existing:
            skipped += 1
            continue
        body = {
            "device_id": test_dev_id,
            "name": name,
            "block_type": code,
            "block_config": cfg,
            "data_type": "float",
            "execution_priority": 100,
        }
        try:
            api_post("/computed-tags", body)
            ok(f"{name} ({code})")
            created += 1
        except Exception as e:
            warn(f"{name}: {str(e)[:200]}")
            failed += 1
    return created, skipped, failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true",
                    help="Delete prior BLOCKTEST_* devices and tags before creating")
    args = ap.parse_args()

    if args.wipe:
        wipe()

    head("Devices")
    input_dev_id = get_or_create_device(INPUT_DEVICE, "Block fixtures - helper constant inputs")
    test_dev_id  = get_or_create_device(TEST_DEVICE,  "Block fixtures - one tag per block")

    input_ids = seed_inputs(input_dev_id)
    if len(input_ids) < len(INPUT_VALUES):
        warn(f"only {len(input_ids)}/{len(INPUT_VALUES)} input tags exist - downstream blocks "
             f"that need missing inputs will not be created")
        # Continue anyway with whatever exists.

    created, skipped, failed = seed_blocks(test_dev_id, input_ids)

    head("Summary")
    print(f"  Created : {created}")
    print(f"  Skipped : {skipped} (already existed)")
    print(f"  Failed  : {failed}")
    print()
    print(f"Browse the tags in Claude Computed Tags admin, filter device='{TEST_DEVICE}'.")
    print()
    print("Or inspect via psql once the evaluator has run a tick:")
    print('  docker exec svj_postgres psql -U induvista_admin -d induvista -c \\')
    print('    "SELECT t.name, ct.block_type, ltv.value_double, ltv.st, ltv.st_class')
    print('     FROM computed_tags ct JOIN tags t ON t.id=ct.id')
    print('     LEFT JOIN latest_tag_values ltv ON ltv.tag_id=ct.id')
    print("     WHERE t.name LIKE 'BT_%' ORDER BY t.name;\"")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
