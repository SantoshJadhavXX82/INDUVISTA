#!/usr/bin/env python3
"""Phase 17.0b - rigorous two-phase block smoke test.

Phase 1: /api/computed-tags/preview verification (block-math correctness)
Phase 2: end-to-end DB pipeline verification (create -> evaluate -> read)
Phase 3: stateful blocks via /preview multi-step sequences

If Phase 1 passes but Phase 2 fails for a block, the calc_evaluator has a
bug in the value/quality write path (the block itself is correct).

Requires:
    pip install requests
    docker exec svj_postgres available
    backend at http://localhost:8000

Usage:
    python block_smoke.py                # full run
    python block_smoke.py --cleanup-only # just delete prior BLOCKTEST_* artifacts
    python block_smoke.py --preview-only # skip phase 2 (fast math check)
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE = "http://localhost:8000/api"
INPUT_DEVICE_NAME = "BLOCKTEST_INPUTS"
TEST_DEVICE_NAME = "BLOCKTEST_BLOCKS"

PG_USER = "induvista_admin"
PG_DB = "induvista"
PG_CONTAINER = "svj_postgres"

GOOD_QUALITY = 128
GOOD_NON_SPECIFIC = 192

# Wait for evaluator to populate values after creation.
# The calc_evaluator runs every 1s by default; allow generous margin.
EVALUATOR_WAIT_SEC = 6.0

# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"  {msg}")

def head(msg: str) -> None:
    print(f"\n=== {msg} ===")

def ok(msg: str) -> None:
    print(f"  \033[32mPASS\033[0m  {msg}")

def fail(msg: str) -> None:
    print(f"  \033[31mFAIL\033[0m  {msg}")

def warn(msg: str) -> None:
    print(f"  \033[33mWARN\033[0m  {msg}")

# ---------------------------------------------------------------------------
# HTTP + psql helpers
# ---------------------------------------------------------------------------

def api_post(path: str, body: dict) -> Any:
    r = requests.post(f"{BASE}{path}", json=body, timeout=10)
    if not r.ok:
        raise RuntimeError(f"POST {path}: HTTP {r.status_code}\n{r.text[:500]}")
    return r.json()

def api_get(path: str) -> Any:
    r = requests.get(f"{BASE}{path}", timeout=10)
    if not r.ok:
        raise RuntimeError(f"GET {path}: HTTP {r.status_code}")
    return r.json()

def api_delete(path: str) -> None:
    r = requests.delete(f"{BASE}{path}", timeout=10)
    if not r.ok and r.status_code != 404:
        raise RuntimeError(f"DELETE {path}: HTTP {r.status_code}\n{r.text[:200]}")

def psql(sql: str) -> str:
    """Execute SQL inside the postgres container; return stdout."""
    proc = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql",
         "-U", PG_USER, "-d", PG_DB, "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()

def latest_value(tag_id: int) -> tuple[float | None, int | None]:
    """Read (value, quality) from latest_tag_values for tag_id."""
    out = psql(f"SELECT value_double, st FROM latest_tag_values WHERE tag_id = {tag_id};")
    if not out:
        return None, None
    parts = out.split("|")
    val = float(parts[0]) if parts[0] not in ("", "\\N") else None
    qual = int(parts[1]) if parts[1] else None
    return val, qual

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

# Helper input values - each gets a computed tag that outputs this constant
# using ADD in N-ary mode with a single {value: X} item. No real channel needed.
INPUT_VALUES = {
    "VAL_2": 2.0,
    "VAL_3": 3.0,
    "VAL_4": 4.0,
    "VAL_5": 5.0,
    "VAL_6": 6.0,
    "VAL_8": 8.0,
    "VAL_10": 10.0,
    "VAL_0": 0.0,
    "VAL_NEG3": -3.0,
    "VAL_100": 100.0,
    "VAL_2P5": 2.5,
    "VAL_2P3": 2.3,
    "VAL_2P7": 2.7,
    "VAL_E": math.e,
    "VAL_PI4": math.pi / 4,
}


@dataclass
class StatelessTest:
    name: str
    code: str
    cfg: dict
    expected: float
    tol: float = 1e-6


def build_stateless_tests(ids: dict[str, int]) -> list[StatelessTest]:
    """All stateless block tests. `ids` maps VAL_* name -> tag_id."""
    v = lambda k: ids[k]
    T = StatelessTest
    return [
        # === Aggregation Tier A (15) ===
        # Input set [2, 4, 6, 8, 10], n=5, mean=6
        T("AVG_OF",         "AVG_OF",         {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 6.0),
        T("MIN_OF",         "MIN_OF",         {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 2.0),
        T("MAX_OF",         "MAX_OF",         {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 10.0),
        T("MEDIAN_OF",      "MEDIAN_OF",      {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 6.0),
        T("MODE_OF",        "MODE_OF",        {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 2.0),  # all unique -> min wins
        T("RANGE_OF",       "RANGE_OF",       {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 8.0),
        # sample variance (n-1) for [2,4,6,8,10]: ((4+16+0+4+16)+(no, with mean 6))=(16+4+0+4+16)/4 = 10
        T("STDDEV_OF",      "STDDEV_OF",      {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, math.sqrt(10), 1e-4),
        T("VARIANCE_OF",    "VARIANCE_OF",    {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 10.0, 1e-4),
        T("RMS_OF",         "RMS_OF",         {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, math.sqrt(44), 1e-4),
        T("PRODUCT_OF",     "PRODUCT_OF",     {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 3840.0),
        T("GEOMETRIC_MEAN", "GEOMETRIC_MEAN", {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 3840 ** 0.2, 1e-4),
        T("HARMONIC_MEAN",  "HARMONIC_MEAN",  {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 5.0 / sum(1/x for x in (2,4,6,8,10)), 1e-4),
        T("WEIGHTED_AVG",   "WEIGHTED_AVG",   {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")], "weights": [1, 1, 1, 1, 1]}, 6.0),
        T("COUNT_GOOD",     "COUNT_GOOD",     {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 5.0),
        T("COUNT_NONZERO",  "COUNT_NONZERO",  {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 5.0),

        # === Arithmetic Tier E - Binary (8) + N-ary variants ===
        T("ADD",            "ADD",       {"left": v("VAL_2"), "right": v("VAL_4")}, 6.0),
        T("ADD_NARY",       "ADD",       {"inputs": [{"value": 2}, {"value": 4}, {"value": 6}]}, 12.0),
        T("ADD_MIXED",      "ADD",       {"inputs": [{"tag": v("VAL_4")}, {"value": 10}]}, 14.0),
        T("SUB",            "SUB",       {"left": v("VAL_10"), "right": v("VAL_2")}, 8.0),
        T("MUL",            "MUL",       {"left": v("VAL_4"), "right": v("VAL_6")}, 24.0),
        T("MUL_NARY",       "MUL",       {"inputs": [{"value": 2}, {"value": 3}, {"value": 4}]}, 24.0),
        T("DIV",            "DIV",       {"left": v("VAL_10"), "right": v("VAL_4")}, 2.5),
        T("MOD",            "MOD",       {"left": v("VAL_10"), "right": v("VAL_4")}, 2.0),
        T("POW",            "POW",       {"left": v("VAL_2"), "value": 3}, 8.0),
        T("MIN_OF_TWO",     "MIN_OF_TWO",{"left": v("VAL_4"), "right": v("VAL_2")}, 2.0),
        T("MAX_OF_TWO",     "MAX_OF_TWO",{"left": v("VAL_4"), "right": v("VAL_2")}, 4.0),

        # === Arithmetic Tier E - Unary (6) ===
        T("ABS",   "ABS",   {"input": v("VAL_NEG3")}, 3.0),
        T("NEG",   "NEG",   {"input": v("VAL_2")},    -2.0),
        T("SQRT",  "SQRT",  {"input": v("VAL_4")},    2.0),
        T("FLOOR", "FLOOR", {"input": v("VAL_2P7")},  2.0),
        T("CEIL",  "CEIL",  {"input": v("VAL_2P3")},  3.0),
        # banker's rounding: 2.5 -> 2 (since 2 is even)
        T("ROUND", "ROUND", {"input": v("VAL_2P5")},  2.0),

        # === Arithmetic Tier E - Transcendental (6) ===
        T("EXP",   "EXP",   {"input": v("VAL_2")},     math.exp(2),       1e-4),
        T("LN",    "LN",    {"input": v("VAL_E")},     1.0,               1e-6),
        T("LOG10", "LOG10", {"input": v("VAL_100")},   2.0,               1e-6),
        T("SIN",   "SIN",   {"input": v("VAL_PI4")},   math.sin(math.pi / 4), 1e-6),
        T("COS",   "COS",   {"input": v("VAL_PI4")},   math.cos(math.pi / 4), 1e-6),
        T("TAN",   "TAN",   {"input": v("VAL_PI4")},   1.0,               1e-6),

        # === Selection Tier B (6) ===
        T("FIRST_GOOD",      "FIRST_GOOD",      {"inputs": [v("VAL_2"), v("VAL_4")]}, 2.0),
        T("LAST_GOOD",       "LAST_GOOD",       {"inputs": [v("VAL_2"), v("VAL_4")]}, 4.0),
        T("HIGHEST_QUALITY", "HIGHEST_QUALITY", {"inputs": [v("VAL_2"), v("VAL_4")]}, 2.0),  # tie - first wins
        T("HOT_STANDBY",     "HOT_STANDBY",     {"primary": v("VAL_2"), "standby": v("VAL_4")}, 2.0),
        T("VOTING_M_OF_N",   "VOTING_M_OF_N",   {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6")], "tolerance": 10}, 4.0),  # all within tol; median = 4
        # MUX: index=VAL_2 (value 2.0, integer), values=[VAL_4, VAL_6, VAL_8, VAL_10] -> index 2 picks VAL_8 (value 8.0)
        T("MUX_INDEX",       "MUX_INDEX",       {"index": v("VAL_2"), "values": [v("VAL_4"), v("VAL_6"), v("VAL_8"), v("VAL_10")]}, 8.0),

        # === Conditional / Comparison / Logic Tier C (11) ===
        T("IF_THEN_ELSE", "IF_THEN_ELSE", {"condition": v("VAL_2"), "then_value": v("VAL_4"), "else_value": v("VAL_6")}, 4.0),
        T("GT",     "GT",     {"left": v("VAL_4"), "value": 2}, 1.0),
        T("LT",     "LT",     {"left": v("VAL_2"), "value": 4}, 1.0),
        T("GTE",    "GTE",    {"left": v("VAL_2"), "value": 2}, 1.0),
        T("LTE",    "LTE",    {"left": v("VAL_2"), "value": 2}, 1.0),
        T("EQ",     "EQ",     {"left": v("VAL_2"), "value": 2}, 1.0),
        T("NE",     "NE",     {"left": v("VAL_2"), "value": 3}, 1.0),
        T("AND_OF", "AND_OF", {"inputs": [v("VAL_2"), v("VAL_4")]}, 1.0),
        T("OR_OF",  "OR_OF",  {"inputs": [v("VAL_0"), v("VAL_4")]}, 1.0),
        T("XOR_OF", "XOR_OF", {"inputs": [v("VAL_2"), v("VAL_0")]}, 1.0),
        T("NOT",    "NOT",    {"input": v("VAL_0")}, 1.0),

        # === SUM_OF (1) ===
        T("SUM_OF", "SUM_OF", {"inputs": [v("VAL_2"), v("VAL_4"), v("VAL_6")]}, 12.0),
    ]


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup():
    info("Looking for prior BLOCKTEST artifacts...")
    try:
        devices = api_get("/computed-devices")
    except Exception as e:
        warn(f"could not list devices: {e}")
        return
    target_ids = [d["id"] for d in devices if d.get("name") in (INPUT_DEVICE_NAME, TEST_DEVICE_NAME)]
    if not target_ids:
        info("nothing to clean")
        return
    # Delete tags first (in case CASCADE isn't enabled)
    try:
        tags = api_get("/computed-tags")
        for t in tags:
            if t.get("computed_device_id") in target_ids or t.get("device_id") in target_ids:
                try:
                    api_delete(f"/computed-tags/{t['id']}")
                except Exception as e:
                    warn(f"delete tag {t.get('name', t.get('id'))}: {e}")
    except Exception as e:
        warn(f"could not list tags: {e}")
    # Then devices
    for did in target_ids:
        try:
            api_delete(f"/computed-devices/{did}")
            info(f"deleted device id={did}")
        except Exception as e:
            warn(f"delete device {did}: {e}")


# ---------------------------------------------------------------------------
# Phase 1: preview-based math verification (no DB writes)
# ---------------------------------------------------------------------------

def phase1_preview() -> tuple[int, int, dict[str, dict]]:
    """Verify each block's math via /preview using synthetic input values.

    Returns (passed, failed, results_by_name).
    """
    head("Phase 1: /preview verification (block math)")
    # Synthetic tag IDs - we provide values via input_values overrides
    syn_ids = {name: 9000 + i for i, name in enumerate(INPUT_VALUES.keys())}
    tests = build_stateless_tests(syn_ids)

    # Build the input_values payload once - covers every helper tag
    input_values = [
        {"tag_id": syn_ids[name], "value": val, "quality": GOOD_NON_SPECIFIC}
        for name, val in INPUT_VALUES.items()
    ]

    passed = 0
    failed = 0
    results: dict[str, dict] = {}

    for t in tests:
        try:
            body = {
                "block_type": t.code,
                "block_config": t.cfg,
                "input_values": input_values,
            }
            resp = api_post("/computed-tags/preview", body)
            results[t.name] = resp

            if resp.get("status") != "ok":
                fail(f"{t.name:<18} status={resp.get('status')} error={resp.get('error')}")
                failed += 1
                continue

            actual = resp.get("value")
            quality = resp.get("quality", 0)

            if actual is None:
                fail(f"{t.name:<18} value=None expected={t.expected}")
                failed += 1
                continue

            diff = abs(actual - t.expected)
            if diff > t.tol:
                fail(f"{t.name:<18} value={actual} expected={t.expected} diff={diff:.6g}")
                failed += 1
                continue

            if quality < GOOD_QUALITY:
                fail(f"{t.name:<18} value={actual} (correct) but quality={quality} (BAD)")
                failed += 1
                continue

            ok(f"{t.name:<18} value={actual:<12g} quality={quality}")
            passed += 1
        except Exception as e:
            fail(f"{t.name:<18} exception: {e}")
            failed += 1

    print(f"\n  Phase 1 result: {passed} passed, {failed} failed (of {len(tests)})")
    return passed, failed, results


# ---------------------------------------------------------------------------
# Phase 2: end-to-end - real computed tags + DB read
# ---------------------------------------------------------------------------

def phase2_e2e(phase1_results: dict[str, dict]) -> tuple[int, int]:
    """Create input + test computed tags, wait, read back via psql."""
    head("Phase 2: end-to-end (create real tags, wait for evaluator, read DB)")

    # Create devices
    info("Creating BLOCKTEST devices...")
    input_dev = api_post("/computed-devices", {
        "name": INPUT_DEVICE_NAME,
        "description": "Block smoke test - synthetic input values",
    })
    test_dev = api_post("/computed-devices", {
        "name": TEST_DEVICE_NAME,
        "description": "Block smoke test - one tag per block",
    })
    info(f"  input device id={input_dev['id']}, test device id={test_dev['id']}")

    # Create helper input tags (each is ADD N-ary with one constant)
    info("Creating helper input tags...")
    input_ids: dict[str, int] = {}
    for name, val in INPUT_VALUES.items():
        body = {
            "device_id": input_dev["id"],
            "name": name,
            "block_type": "ADD",
            "block_config": {"inputs": [{"value": val}]},
            "data_type": "float",
            "execution_priority": 1,  # run first (lower = earlier)
        }
        result = api_post("/computed-tags", body)
        input_ids[name] = result["id"]

    info(f"  created {len(input_ids)} input tags")

    # Wait for inputs to populate
    info(f"Waiting {EVALUATOR_WAIT_SEC}s for input tags to evaluate...")
    time.sleep(EVALUATOR_WAIT_SEC)

    # Verify input tags actually populated. If they didn't, Phase 2 is doomed.
    bad_inputs = []
    for name, tid in input_ids.items():
        val, qual = latest_value(tid)
        if val is None:
            bad_inputs.append((name, tid, "no row"))
        elif qual is None or qual < GOOD_QUALITY:
            bad_inputs.append((name, tid, f"quality={qual}"))
        elif abs(val - INPUT_VALUES[name]) > 1e-6:
            bad_inputs.append((name, tid, f"value={val}"))

    if bad_inputs:
        warn(f"{len(bad_inputs)} helper input tags failed to populate cleanly:")
        for name, tid, why in bad_inputs[:5]:
            warn(f"  {name} (id={tid}): {why}")
        warn("Phase 2 results will be unreliable for blocks depending on these inputs")

    # Create test blocks
    info("Creating one computed tag per block...")
    tests = build_stateless_tests(input_ids)
    test_tag_ids: dict[str, int] = {}
    for t in tests:
        body = {
            "device_id": test_dev["id"],
            "name": f"BT_{t.name}",
            "block_type": t.code,
            "block_config": t.cfg,
            "data_type": "float",
            "execution_priority": 100,  # after inputs
        }
        try:
            result = api_post("/computed-tags", body)
            test_tag_ids[t.name] = result["id"]
        except Exception as e:
            warn(f"create BT_{t.name}: {e}")

    info(f"  created {len(test_tag_ids)}/{len(tests)} test tags")

    # Wait for evaluator
    info(f"Waiting {EVALUATOR_WAIT_SEC}s for test tags to evaluate...")
    time.sleep(EVALUATOR_WAIT_SEC)

    # Verify each
    passed = 0
    failed = 0
    pipeline_bugs = []  # (name, preview_quality, db_quality)

    for t in tests:
        if t.name not in test_tag_ids:
            failed += 1
            continue
        tid = test_tag_ids[t.name]
        val, qual = latest_value(tid)

        # Compare against Phase 1 result for the same block
        preview = phase1_results.get(t.name, {})
        preview_value = preview.get("value")
        preview_quality = preview.get("quality")

        if val is None:
            fail(f"{t.name:<18} no DB row (preview was value={preview_value} quality={preview_quality})")
            failed += 1
            continue

        # Math correctness
        diff = abs(val - t.expected)
        if diff > t.tol:
            fail(f"{t.name:<18} DB value={val} expected={t.expected} diff={diff:.6g}")
            failed += 1
            continue

        # Quality correctness
        if qual is None or qual < GOOD_QUALITY:
            # Distinguish: was preview also bad, or is this a pipeline bug?
            if preview_quality is not None and preview_quality >= GOOD_QUALITY:
                pipeline_bugs.append((t.name, preview_quality, qual))
                fail(f"{t.name:<18} PIPELINE BUG: value={val} (correct) DB quality={qual} but preview quality={preview_quality}")
            else:
                fail(f"{t.name:<18} value={val} quality={qual} (preview also bad: {preview_quality})")
            failed += 1
            continue

        ok(f"{t.name:<18} DB value={val:<12g} quality={qual}")
        passed += 1

    print(f"\n  Phase 2 result: {passed} passed, {failed} failed (of {len(tests)})")
    if pipeline_bugs:
        print(f"\n  \033[31mPIPELINE BUGS DETECTED ({len(pipeline_bugs)}):\033[0m")
        print(f"  These blocks produced correct values but BAD quality in the DB,")
        print(f"  while /preview gave GOOD quality for the same config:")
        for name, preview_q, db_q in pipeline_bugs:
            print(f"    - {name}: preview quality={preview_q}, DB quality={db_q}")
        print(f"  Likely cause: calc_evaluator's write_output is dropping the quality,")
        print(f"  or fetching input samples differently than the /preview endpoint.")

    return passed, failed


# ---------------------------------------------------------------------------
# Phase 3: stateful blocks via /preview multi-step
# ---------------------------------------------------------------------------

@dataclass
class StatefulStep:
    """One step in a stateful simulation. For 1-input blocks (TON/TOF/TP/R_TRIG/F_TRIG)
    use inputs=[v]. For 2-input blocks (SR/RS/CTU/CTD) use inputs=[v1, v2]."""
    inputs: list[float]
    now: float
    expected: float


@dataclass
class StatefulCase:
    name: str
    code: str
    cfg: dict
    input_tag_ids: list[int]
    steps: list[StatefulStep]


def stateful_cases() -> list[StatefulCase]:
    return [
        # TON preset 1000ms: input HIGH at t=0; output goes HIGH at t>=1.0s
        StatefulCase("TON_on_delay", "TON",
            cfg={"input": 1, "preset_ms": 1000},
            input_tag_ids=[1],
            steps=[
                StatefulStep([1.0], 0.0, 0.0),   # rising, elapsed=0 < 1.0
                StatefulStep([1.0], 0.5, 0.0),   # elapsed=0.5
                StatefulStep([1.0], 1.0, 1.0),   # elapsed=1.0 >= preset -> HIGH
                StatefulStep([1.0], 1.5, 1.0),   # still HIGH
                StatefulStep([0.0], 1.6, 0.0),   # input drops -> output drops
            ]),
        # TOF preset 1000ms: input HIGH then drops -> output stays HIGH for 1s after fall
        StatefulCase("TOF_off_delay", "TOF",
            cfg={"input": 1, "preset_ms": 1000},
            input_tag_ids=[1],
            steps=[
                StatefulStep([1.0], 0.0, 1.0),    # input high -> output high
                StatefulStep([1.0], 0.5, 1.0),
                StatefulStep([0.0], 0.6, 1.0),    # falling at t=0.6, elapsed=0
                StatefulStep([0.0], 1.0, 1.0),    # elapsed=0.4 < 1.0
                StatefulStep([0.0], 1.7, 0.0),    # elapsed=1.1 >= 1.0 -> LOW
            ]),
        # TP preset 500ms: rising edge -> 500ms pulse
        StatefulCase("TP_pulse", "TP",
            cfg={"input": 1, "preset_ms": 500},
            input_tag_ids=[1],
            steps=[
                StatefulStep([1.0], 0.0, 1.0),   # rising, pulse starts, elapsed=0
                StatefulStep([1.0], 0.2, 1.0),   # in pulse
                StatefulStep([0.0], 0.4, 1.0),   # pulse continues regardless of input
                StatefulStep([0.0], 0.6, 0.0),   # elapsed >= 0.5 -> end
            ]),
        # R_TRIG: low->high in one step
        StatefulCase("R_TRIG_rising", "R_TRIG",
            cfg={"input": 1},
            input_tag_ids=[1],
            steps=[
                StatefulStep([0.0], 0.0, 0.0),   # was 0, still 0
                StatefulStep([1.0], 0.1, 1.0),   # rising edge
                StatefulStep([1.0], 0.2, 0.0),   # still high but no new edge
            ]),
        # F_TRIG
        StatefulCase("F_TRIG_falling", "F_TRIG",
            cfg={"input": 1},
            input_tag_ids=[1],
            steps=[
                StatefulStep([1.0], 0.0, 0.0),   # was 0, now 1 -> no falling
                StatefulStep([1.0], 0.1, 0.0),   # still 1
                StatefulStep([0.0], 0.2, 1.0),   # falling edge
                StatefulStep([0.0], 0.3, 0.0),   # still low
            ]),
        # SR set-dominant
        StatefulCase("SR_latch", "SR",
            cfg={"set": 1, "reset": 2},
            input_tag_ids=[1, 2],
            steps=[
                StatefulStep([1.0, 0.0], 0.0, 1.0),   # set -> q=1
                StatefulStep([0.0, 0.0], 0.1, 1.0),   # latched
                StatefulStep([0.0, 1.0], 0.2, 0.0),   # reset -> q=0
                StatefulStep([1.0, 1.0], 0.3, 1.0),   # both -> SR set-dominant -> q=1
            ]),
        # RS reset-dominant
        StatefulCase("RS_latch", "RS",
            cfg={"set": 1, "reset": 2},
            input_tag_ids=[1, 2],
            steps=[
                StatefulStep([1.0, 0.0], 0.0, 1.0),   # set -> q=1
                StatefulStep([0.0, 0.0], 0.1, 1.0),   # latched
                StatefulStep([0.0, 1.0], 0.2, 0.0),   # reset -> q=0
                StatefulStep([1.0, 1.0], 0.3, 0.0),   # both -> RS reset-dominant -> q=0
            ]),
        # CTU
        StatefulCase("CTU_count_up", "CTU",
            cfg={"count_up": 1, "reset": 2},
            input_tag_ids=[1, 2],
            steps=[
                StatefulStep([0.0, 0.0], 0.0, 0.0),   # cv=0
                StatefulStep([1.0, 0.0], 0.1, 1.0),   # rising -> cv=1
                StatefulStep([1.0, 0.0], 0.2, 1.0),   # still high, no rising
                StatefulStep([0.0, 0.0], 0.3, 1.0),   # falling, no change
                StatefulStep([1.0, 0.0], 0.4, 2.0),   # next rising -> cv=2
                StatefulStep([0.0, 1.0], 0.5, 0.0),   # reset -> cv=0
            ]),
        # CTD
        StatefulCase("CTD_count_down", "CTD",
            cfg={"count_down": 1, "load": 2, "load_value": 5},
            input_tag_ids=[1, 2],
            steps=[
                StatefulStep([0.0, 0.0], 0.0, 5.0),   # first call: cv defaults to load_value=5
                StatefulStep([1.0, 0.0], 0.1, 4.0),   # rising -> cv=4
                StatefulStep([1.0, 0.0], 0.2, 4.0),   # still high, no change
                StatefulStep([0.0, 0.0], 0.3, 4.0),
                StatefulStep([1.0, 0.0], 0.4, 3.0),   # rising -> cv=3
                StatefulStep([0.0, 1.0], 0.5, 5.0),   # load -> 5
            ]),
    ]


def phase3_stateful() -> tuple[int, int]:
    head("Phase 3: stateful blocks via /preview multi-step")
    passed = 0
    failed = 0
    for case in stateful_cases():
        state = None
        case_failed = False
        for i, step in enumerate(case.steps):
            input_values = [
                {"tag_id": tid, "value": v, "quality": GOOD_NON_SPECIFIC}
                for tid, v in zip(case.input_tag_ids, step.inputs)
            ]
            body = {
                "block_type": case.code,
                "block_config": case.cfg,
                "input_values": input_values,
                "state": state,
                "now": step.now,
            }
            try:
                resp = api_post("/computed-tags/preview", body)
            except Exception as e:
                fail(f"{case.name:<20} step {i+1} HTTP error: {e}")
                case_failed = True
                break
            if resp.get("status") != "ok":
                fail(f"{case.name:<20} step {i+1} status={resp.get('status')} error={resp.get('error')}")
                case_failed = True
                break

            actual = resp.get("value")
            if actual is None or abs(actual - step.expected) > 1e-6:
                fail(f"{case.name:<20} step {i+1}: now={step.now}s inputs={step.inputs} got={actual} expected={step.expected}")
                case_failed = True
                break
            state = resp.get("new_state")

        if not case_failed:
            ok(f"{case.name:<20} ({len(case.steps)} steps OK, final state={state})")
            passed += 1
        else:
            failed += 1

    print(f"\n  Phase 3 result: {passed} passed, {failed} failed (of {len(stateful_cases())})")
    return passed, failed


# ---------------------------------------------------------------------------
# Diagnostic for "correct value, bad quality" bug
# ---------------------------------------------------------------------------

def diagnose_bad_quality():
    head("Diagnostic: computed tags with correct value but BAD quality")
    sql = """
        SELECT ct.id, t.name, ct.block_type, ltv.value_double, ltv.st
        FROM computed_tags ct
        JOIN tags t ON t.id = ct.id
        LEFT JOIN latest_tag_values ltv ON ltv.tag_id = ct.id
        WHERE ltv.value_double IS NOT NULL AND ltv.st < 128
        ORDER BY ct.id;
    """
    out = psql(sql)
    if not out:
        info("no computed tags with correct value + bad quality - clean.")
        return
    print(f"  Found {len(out.splitlines())} computed tag(s) with non-null value and quality < GOOD:")
    print(f"  {'id':>5}  {'name':<30}  {'block_type':<20}  {'value':<14}  quality")
    for line in out.splitlines():
        cid, name, bt, val, qual = line.split("|")
        print(f"  {cid:>5}  {name[:30]:<30}  {bt[:20]:<20}  {val[:14]:<14}  {qual}")
    info("For each, check input qualities via:")
    info("  docker exec svj_postgres psql -U induvista_admin -d induvista -c \\")
    info("    \"SELECT input_tag_id, t.name, ltv.value_double, ltv.st")
    info("       FROM (SELECT id AS computed_id, ")
    info("                jsonb_path_query(block_config, '$.** ? (@.type() == \\\"number\\\")')::text::int AS input_tag_id ")
    info("              FROM computed_tags WHERE id=<COMPUTED_ID>) src")
    info("       LEFT JOIN tags t ON t.id=src.input_tag_id")
    info("       LEFT JOIN latest_tag_values ltv ON ltv.tag_id=src.input_tag_id;\"")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup-only", action="store_true", help="Delete BLOCKTEST_* and exit")
    ap.add_argument("--preview-only", action="store_true", help="Skip Phase 2 (DB E2E)")
    ap.add_argument("--skip-cleanup", action="store_true", help="Don't clean before running")
    ap.add_argument("--keep", action="store_true", help="Don't clean up at end (leave tags for manual inspection)")
    args = ap.parse_args()

    head("Phase 0: cleanup")
    if not args.skip_cleanup:
        cleanup()

    if args.cleanup_only:
        print("\nDone.\n")
        return 0

    # Phase 1
    p1_pass, p1_fail, p1_results = phase1_preview()

    # Phase 2
    p2_pass = p2_fail = 0
    if not args.preview_only:
        try:
            p2_pass, p2_fail = phase2_e2e(p1_results)
        except Exception as e:
            fail(f"Phase 2 aborted: {e}")
            p2_fail = 999

    # Phase 3
    p3_pass, p3_fail = phase3_stateful()

    # Diagnostic for any pre-existing bad-quality computed tags
    try:
        diagnose_bad_quality()
    except Exception as e:
        warn(f"diagnostic skipped: {e}")

    # Summary
    head("Summary")
    print(f"  Phase 1 (preview math):     {p1_pass:>3} pass, {p1_fail:>3} fail")
    if not args.preview_only:
        print(f"  Phase 2 (DB pipeline E2E):  {p2_pass:>3} pass, {p2_fail:>3} fail")
    print(f"  Phase 3 (stateful timeline):{p3_pass:>3} pass, {p3_fail:>3} fail")
    total_pass = p1_pass + p2_pass + p3_pass
    total_fail = p1_fail + p2_fail + p3_fail
    print(f"  Total:                      {total_pass:>3} pass, {total_fail:>3} fail")

    if not args.keep:
        head("Cleaning up")
        try:
            cleanup()
        except Exception as e:
            warn(f"cleanup error: {e}")
    else:
        info("Skipping cleanup (--keep). BLOCKTEST_* devices/tags remain.")

    print()
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
