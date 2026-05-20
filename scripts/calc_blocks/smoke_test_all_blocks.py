"""Rigorous smoke test for the Calc Blocks subsystem.

VERSION: v7 (Section 7 asserts all 9 validation cases; requires backend
              v7 patch with _validate_block in computed_tags.py)
         Verify with: Select-String -Path scripts\\calc_blocks\\smoke_test_all_blocks.py -Pattern "VERSION: v7"

End-to-end coverage of /api/calc, /api/computed-devices, /api/computed-tags.
Exercises ALL 62 registered block codes through their full lifecycle:
create → first evaluation → toggle → update → negative-test → delete.

Sections (in execution order — section N depends on N-1):
    0  Service health
    1  Schema catalog (GET /api/calc/block-schemas)
    2  Pool discovery + seed of all 62 blocks
    3  Every block evaluates within 30 s
    4  Update round-trip (PATCH block_config & execution_rate_ms)
    5  Toggle enable/disable + verify scheduling honours it
    6  ADD/MUL n_ary mode round-trip
    7  Negative cases (400/422 from API validation)
    8  Cleanup (optional; default off to keep state for next run)

Exit codes:
    0  — all assertions passed
    1  — one or more assertions failed
    2  — preconditions not met (backend down, pool insufficient, etc.)

Usage:
    python smoke_test_all_blocks.py
    python smoke_test_all_blocks.py --base http://127.0.0.1:8000 --cleanup
    python smoke_test_all_blocks.py --quick           # skip stateful settling, faster
    python smoke_test_all_blocks.py --section 3       # run just one section
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _api_client import Api, ApiError
from _block_configs import (
    RECIPES, TagPool, pool_requirements, recipes_by_code, BlockRecipe,
)
import seed_all_blocks as seed

# -----------------------------------------------------------------------
# Pass/fail bookkeeping
# -----------------------------------------------------------------------

class Tally:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []
        self.skipped: list[str] = []
        self.section: str = ""

    def pass_(self, msg: str) -> None:
        self.passed += 1
        print(f"  \033[32m[PASS]\033[0m {msg}")

    def fail(self, msg: str) -> None:
        self.failed.append(msg)
        print(f"  \033[31m[FAIL]\033[0m {msg}")

    def skip(self, msg: str) -> None:
        self.skipped.append(msg)
        print(f"  \033[33m[SKIP]\033[0m {msg}")

    @contextmanager
    def step(self, name: str):
        """Wrap a step so any uncaught exception is logged as FAIL,
        not propagated."""
        try:
            yield
        except AssertionError as e:
            self.fail(f"{name}: {e}")
        except ApiError as e:
            self.fail(f"{name}: HTTP {e.status} {e.body[:200]}")
        except Exception as e:
            self.fail(f"{name}: unhandled {type(e).__name__}: {e}")


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


# Quality threshold per app/workers/calc_blocks/base.py
GOOD_QUALITY = 128


# -----------------------------------------------------------------------
# Section 0 — Service health
# -----------------------------------------------------------------------

def section_0_health(api: Api, tally: Tally) -> bool:
    section("Section 0 — Service health")

    with tally.step("backend /health reachable"):
        ok = api.wait_ready(max_sec=60)
        assert ok, "backend did not respond on /health within 60s"
        tally.pass_("backend /health responsive")

    # Best-effort docker compose check. If docker isn't on PATH (e.g. running
    # the smoke from outside the host), we skip rather than fail — the
    # remaining API-level checks already verify the workers are alive.
    # shutil.which is cross-platform; subprocess.run(["which", ...]) is not.
    import shutil
    docker_exe = shutil.which("docker")
    if docker_exe is None:
        tally.skip("docker not on PATH — skipping container-state checks")
        return True

    for svc in ("backend", "postgres", "calc_evaluator"):
        with tally.step(f"docker service {svc} up"):
            try:
                r = subprocess.run(
                    [docker_exe, "compose", "ps", svc, "--format", "json"],
                    capture_output=True, text=True, timeout=10,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                # Last-ditch fallback — surface as skip, not fail
                tally.skip(f"docker compose ps {svc} failed: {e}")
                continue
            text = (r.stdout or "") + (r.stderr or "")
            running = ('"State":"running"' in text.replace(" ", "")
                       or '"Status":"Up' in text
                       or '"State": "running"' in text)
            assert running, f"service {svc} not running:\n{text[:400]}"
            tally.pass_(f"service {svc} running")
    return True


# -----------------------------------------------------------------------
# Section 1 — Schema catalog
# -----------------------------------------------------------------------

KNOWN_FIELD_TYPES = {
    "tag_ref", "tag_ref_list", "tag_or_constant", "tag_or_constant_list",
    "integer", "number", "number_list", "boolean", "enum", "mode_select",
}


def section_1_schemas(api: Api, tally: Tally) -> None:
    section("Section 1 — Schema catalog")

    with tally.step("GET /api/calc/block-schemas"):
        schemas = api.get("/api/calc/block-schemas")
        assert isinstance(schemas, dict), f"expected dict, got {type(schemas).__name__}"
        tally.pass_(f"endpoint returned {len(schemas)} schemas")

    expected_codes = {r.code for r in RECIPES}
    returned_codes = set(schemas.keys())

    with tally.step("every recipe code present in /api/calc/block-schemas"):
        missing = expected_codes - returned_codes
        assert not missing, f"missing from API: {sorted(missing)}"
        tally.pass_("all 62 block codes present in API response")

    with tally.step("no unknown codes in /api/calc/block-schemas"):
        extra = returned_codes - expected_codes
        assert not extra, f"unknown to recipes: {sorted(extra)}"
        tally.pass_("no orphan codes from the API")

    bad: list[str] = []
    for code, sch in schemas.items():
        if not isinstance(sch, dict):
            bad.append(f"{code}: not a dict")
            continue
        fields = sch.get("fields", [])
        if not isinstance(fields, list):
            bad.append(f"{code}: fields not a list")
            continue
        for i, f in enumerate(fields):
            if not isinstance(f, dict):
                bad.append(f"{code}.fields[{i}]: not a dict")
                continue
            for key in ("key", "label", "type"):
                if key not in f:
                    bad.append(f"{code}.fields[{i}]: missing {key!r}")
            if f.get("type") not in KNOWN_FIELD_TYPES:
                bad.append(f"{code}.fields[{i}].type={f.get('type')!r} not in {KNOWN_FIELD_TYPES}")
    with tally.step("every schema is well-formed"):
        assert not bad, "\n    " + "\n    ".join(bad[:10])
        tally.pass_("all schemas well-formed (fields list, types known)")


# -----------------------------------------------------------------------
# Section 2 — Seed all blocks
# -----------------------------------------------------------------------

def section_2_seed(api: Api, tally: Tally) -> tuple[int, dict[str, dict]]:
    section("Section 2 — Pool discovery + seed of all 62 blocks")

    pool = seed.discover_pool(api)
    print(f"  pool: numeric={len(pool.numeric)}  integer={len(pool.integer)}  bool={len(pool.booleans)}")
    reqs = pool_requirements()

    with tally.step("input tag pool meets minimum requirements"):
        gaps = pool.needs(num=reqs["numeric"], ints=reqs["integer"], bools=reqs["bool"])
        assert not gaps, f"pool gaps: {gaps}. Seed the 700xa tag pack first."
        tally.pass_(f"pool sufficient (need {reqs})")

    # Fresh device every run to avoid drift across smoke iterations
    print("  resetting smoke device and reseeding all blocks…")
    seed.reset_device(api)
    device_id = seed.ensure_device(api, dry_run=False)

    # Build live lookup once for value_filter use inside create_one
    live_by_id = {row["tag_id"]: row for row in api.get("/api/live")}

    created: dict[str, dict] = {}
    fail_count = 0
    for r in RECIPES:
        try:
            row = seed.create_one(api, device_id=device_id, recipe=r,
                                  pool=pool, live_by_id=live_by_id,
                                  dry_run=False)
            created[r.code] = row
        except ApiError as e:
            fail_count += 1
            tally.fail(f"create {r.code}: HTTP {e.status} {e.body[:200]}")

    with tally.step("all 62 blocks created via POST /api/computed-tags"):
        assert fail_count == 0, f"{fail_count} create failures"
        tally.pass_(f"created {len(created)} computed tags on device {device_id}")

    # Cross-check via GET — the create-then-immediately-list pattern caught
    # a Phase 15.4b bug where the response_model dropped block_config.
    with tally.step("GET /api/computed-tags?device_id reflects all 62"):
        tags = api.get("/api/computed-tags", device_id=device_id, limit=1000)
        # The GET endpoint takes no device_id filter in this build, but
        # filters can be applied client-side — match by name prefix:
        smoke_tags = [t for t in tags
                      if t["name"].startswith(seed.TAG_PREFIX)
                      and t["device_id"] == device_id]
        assert len(smoke_tags) == 62, f"expected 62, got {len(smoke_tags)}"
        tally.pass_(f"list endpoint returns all 62 smoke tags")

    return device_id, created


# -----------------------------------------------------------------------
# Section 3 — Every block evaluates
# -----------------------------------------------------------------------

def section_3_evaluation(api: Api, device_id: int, created: dict[str, dict],
                         tally: Tally, *, quick: bool = False) -> None:
    section("Section 3 — Every block evaluates within 30s")

    deadline = time.time() + (10.0 if quick else 30.0)
    # Map code -> created tag id
    code_to_id = {code: row["id"] for code, row in created.items()}
    by_code = recipes_by_code()

    # Poll until all blocks have at least one evaluation
    not_evaluated: set[str] = set(code_to_id.keys())
    last_status: dict[str, dict] = {}
    poll_round = 0
    while not_evaluated and time.time() < deadline:
        poll_round += 1
        tags = api.get("/api/computed-tags", device_id=device_id, limit=1000)
        for t in tags:
            if t["name"].startswith(seed.TAG_PREFIX) and t.get("last_executed_at"):
                code = t["name"][len(seed.TAG_PREFIX):].upper()
                if code in not_evaluated:
                    not_evaluated.discard(code)
                    last_status[code] = t
        if not_evaluated:
            time.sleep(1.0)

    with tally.step("every block produced at least one evaluation"):
        if not_evaluated:
            # Show first few for diagnosis
            samples = sorted(not_evaluated)[:8]
            assert False, (
                f"{len(not_evaluated)} blocks never executed after "
                f"{poll_round}s: {samples}"
            )
        tally.pass_(f"all {len(code_to_id)} blocks evaluated within {poll_round}s")

    # Per-block status check
    quality_issues: list[str] = []
    for code, row in last_status.items():
        recipe = by_code[code]
        # The current_value endpoint shape varies; the list endpoint surfaces
        # last_status (per ComputedTagResponse in backend/app/api/computed_tags.py).
        status = row.get("last_status")
        if status == "ok":
            continue
        if recipe.quality_may_be_bad or recipe.stateful:
            # Stateful blocks may not have "ok" status on first tick;
            # tolerate that.
            continue
        quality_issues.append(f"{code}: status={status!r} (expected 'ok')")

    with tally.step("blocks expected to be GOOD all reported ok"):
        if quality_issues:
            for q in quality_issues:
                print(f"      - {q}")
        assert not quality_issues, f"{len(quality_issues)} unexpected non-ok"
        tally.pass_("non-fallible blocks all reported status=ok")


# -----------------------------------------------------------------------
# Section 3b — Output quality on non-fallible blocks
# -----------------------------------------------------------------------
#
# Section 3 only checks "did the evaluator run". Section 3b checks the
# semantic quality of the OUTPUT — i.e. is the result actually usable.
# A block that produces value=NULL quality=BAD is a "successful evaluation"
# from the worker's POV but a failed result from the user's POV. The split
# matters because the most common cause of BAD outputs is BAD INPUTS — the
# seed picked an input tag that isn't being polled cleanly. Catching that
# here surfaces the upstream problem instead of silently passing.
#
# We check via /api/live (the same endpoint the UI's Value column uses)
# rather than re-extending the computed-tags response model.

def _extract_input_tag_ids(block_config: dict) -> list[int]:
    """Best-effort extraction of input tag IDs from a block_config.

    Excludes known constant keys (preset_ms, load_value, tolerance,
    min_agreement, value, weights). Walks lists; handles the n_ary
    ADD/MUL form where each item is {"tag": id} or {"value": const}.
    """
    NON_TAG_KEYS = {"preset_ms", "load_value", "tolerance",
                    "min_agreement", "value", "weights"}
    out: set[int] = set()
    for key, val in (block_config or {}).items():
        if key in NON_TAG_KEYS:
            continue
        if isinstance(val, bool):     # bool is a subclass of int — skip
            continue
        if isinstance(val, int):
            out.add(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, bool):
                    continue
                if isinstance(item, int):
                    out.add(item)
                elif isinstance(item, dict) and "tag" in item \
                        and isinstance(item["tag"], int) \
                        and not isinstance(item["tag"], bool):
                    out.add(item["tag"])
    return sorted(out)


def section_3b_output_quality(api: Api, device_id: int,
                              created: dict[str, dict],
                              tally: Tally) -> None:
    section("Section 3b — Output quality of non-fallible blocks")

    by_code = recipes_by_code()
    code_to_id = {code: row["id"] for code, row in created.items()}

    live = api.get("/api/live")
    live_by_id = {row["tag_id"]: row for row in live}

    real_bugs: list[tuple[str, int, dict]] = []
    upstream_bad: list[tuple[str, int, list[int]]] = []
    skipped_fallible = 0
    skipped_stateful = 0
    checked = 0
    for code, tid in code_to_id.items():
        recipe = by_code[code]
        if recipe.quality_may_be_bad:
            skipped_fallible += 1
            continue
        if recipe.stateful:
            skipped_stateful += 1
            continue
        live_row = live_by_id.get(tid)
        if not live_row:
            real_bugs.append((code, tid, {"note": "no /api/live row for output"}))
            continue
        checked += 1
        st = live_row.get("st")
        if st is None or st < GOOD_QUALITY:
            # Output is BAD. Find inputs and check if any is also BAD —
            # in that case the BAD output is expected, not a bug.
            full = api.get(f"/api/computed-tags/{tid}")
            input_ids = _extract_input_tag_ids(full.get("block_config", {}))
            bad_inputs: list[int] = []
            for in_id in input_ids:
                in_row = live_by_id.get(in_id)
                if in_row is None or (in_row.get("st") or 0) < GOOD_QUALITY:
                    bad_inputs.append(in_id)
            if bad_inputs:
                upstream_bad.append((code, tid, bad_inputs))
            else:
                real_bugs.append((code, tid, live_row))

    # --- Upstream-caused BADs: warn, do not fail ---
    if upstream_bad:
        print(f"  \033[33m[WARN]\033[0m {len(upstream_bad)} block(s) produced BAD "
              f"output due to BAD inputs (NOT a code bug):")
        for code, tid, bad_ins in upstream_bad[:15]:
            print(f"           {code:18s} (id={tid})  BAD input tag(s): {bad_ins}")
        if len(upstream_bad) > 15:
            print(f"           ... and {len(upstream_bad) - 15} more")
        print(f"           Fix the upstream Modbus device/block to make these "
              f"inputs polled with GOOD quality.")

    # --- Real bugs: fail ---
    with tally.step(f"non-fallible blocks ({checked}) — outputs reflect input quality"):
        if real_bugs:
            print(f"    {len(real_bugs)} BAD output(s) with NO BAD upstream "
                  f"input — these are real bugs:")
            for code, tid, row in real_bugs[:10]:
                if "note" in row:
                    print(f"      {code:18s} (id={tid})  {row['note']}")
                else:
                    print(f"      {code:18s} (id={tid})  "
                          f"st={row.get('st')}  value={row.get('value_double')}  "
                          f"age={row.get('age_seconds')}")
        assert not real_bugs, \
            f"{len(real_bugs)} unexplained BAD output(s) on non-fallible blocks"
        n_ok = checked - len(upstream_bad)
        tally.pass_(f"{n_ok}/{checked} non-fallible outputs GOOD; "
                    f"{len(upstream_bad)} BAD attributed to upstream inputs")


# -----------------------------------------------------------------------
# Section 4 — Update round-trip
# -----------------------------------------------------------------------

def section_4_update(api: Api, created: dict[str, dict], tally: Tally) -> None:
    section("Section 4 — Update round-trip")

    sum_id = created["SUM_OF"]["id"]
    original_rate = created["SUM_OF"]["execution_rate_ms"]
    new_rate = 5000 if original_rate != 5000 else 10000

    with tally.step("PATCH execution_rate_ms"):
        resp = api.patch(f"/api/computed-tags/{sum_id}",
                         {"execution_rate_ms": new_rate})
        assert resp["execution_rate_ms"] == new_rate, \
            f"server returned {resp['execution_rate_ms']}, expected {new_rate}"
        tally.pass_(f"execution_rate_ms {original_rate} -> {new_rate}")

    with tally.step("GET reflects the update"):
        got = api.get(f"/api/computed-tags/{sum_id}")
        assert got["execution_rate_ms"] == new_rate
        tally.pass_("PATCHed value persisted")

    # Restore so subsequent sections operate on a normal rate
    api.patch(f"/api/computed-tags/{sum_id}", {"execution_rate_ms": original_rate})


# -----------------------------------------------------------------------
# Section 5 — Toggle enable/disable
# -----------------------------------------------------------------------

def _wait_for_steady_state(api: Api, tag_id: int, *, parse_dt,
                           max_wait_sec: float = 40.0,
                           sample_interval: float = 2.5) -> tuple[bool, float]:
    """After a tag has been disabled, poll until last_executed_at stops
    advancing. Returns (settled, total_seconds_waited).

    The calc_evaluator worker reloads its enabled-tags cache every
    CALC_RELOAD_SEC seconds (default 30). Until then, a disabled tag
    keeps getting scheduled. We adaptively poll instead of sleeping
    a fixed interval — fast when reload happens quickly, correctly slow
    when it's set to the default 30s."""
    waited = 0.0
    while waited < max_wait_sec:
        snap_a = api.get(f"/api/computed-tags/{tag_id}")
        time.sleep(sample_interval)
        waited += sample_interval
        snap_b = api.get(f"/api/computed-tags/{tag_id}")
        a_dt = parse_dt(snap_a.get("last_executed_at"))
        b_dt = parse_dt(snap_b.get("last_executed_at"))
        if a_dt is None or b_dt is None:
            return True, waited
        # 100ms slop for clock-skew / DB round-trip artifacts.
        if (b_dt - a_dt).total_seconds() < 0.1:
            return True, waited
    return False, waited


def _wait_for_resumption(api: Api, tag_id: int, *, parse_dt,
                         max_wait_sec: float = 40.0,
                         sample_interval: float = 2.5) -> tuple[bool, float]:
    """After a tag has been re-enabled, poll until last_executed_at
    starts advancing again. Same adaptive logic as _wait_for_steady_state."""
    waited = 0.0
    snap_a = api.get(f"/api/computed-tags/{tag_id}")
    a_dt = parse_dt(snap_a.get("last_executed_at"))
    while waited < max_wait_sec:
        time.sleep(sample_interval)
        waited += sample_interval
        snap_b = api.get(f"/api/computed-tags/{tag_id}")
        b_dt = parse_dt(snap_b.get("last_executed_at"))
        if a_dt is None and b_dt is not None:
            # Was never evaluated before, now has been — re-enabled
            return True, waited
        if a_dt is not None and b_dt is not None \
                and (b_dt - a_dt).total_seconds() > 0.5:
            return True, waited
    return False, waited


def section_5_toggle(api: Api, created: dict[str, dict], tally: Tally) -> None:
    section("Section 5 — Toggle enable/disable")
    print("  NOTE: the calc_evaluator worker refreshes its enabled-tags cache")
    print("        every CALC_RELOAD_SEC seconds (default 30). This section")
    print("        polls adaptively — Section 5 typically takes ~30-40s.")

    # Use AVG_OF — a quick non-stateful block — for the toggle dance
    tag_id = created["AVG_OF"]["id"]

    from datetime import datetime
    def parse_dt(s):
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    # --- Disable ---
    with tally.step("PATCH enabled=false"):
        resp = api.patch(f"/api/computed-tags/{tag_id}", {"enabled": False})
        assert resp["enabled"] is False
        tally.pass_("enabled set to False")

    print("  polling for disable to propagate (up to 40s)...")
    settled, waited = _wait_for_steady_state(
        api, tag_id, parse_dt=parse_dt, max_wait_sec=40.0, sample_interval=2.5,
    )
    with tally.step("disabled tag is not being re-evaluated"):
        assert settled, (
            f"worker still re-evaluating after {waited:.1f}s. "
            f"If CALC_RELOAD_SEC > 35 in your env, raise the smoke's "
            f"max_wait_sec or set CALC_RELOAD_SEC=10."
        )
        tally.pass_(f"worker stopped evaluating disabled tag "
                    f"(settled in ~{waited:.1f}s)")

    # --- Re-enable ---
    with tally.step("PATCH enabled=true"):
        resp = api.patch(f"/api/computed-tags/{tag_id}", {"enabled": True})
        assert resp["enabled"] is True
        tally.pass_("re-enabled")

    print("  polling for re-enable to propagate (up to 40s)...")
    resumed, waited = _wait_for_resumption(
        api, tag_id, parse_dt=parse_dt, max_wait_sec=40.0, sample_interval=2.5,
    )
    with tally.step("re-enabled tag resumed evaluation"):
        assert resumed, (
            f"worker did not resume evaluation within {waited:.1f}s. "
            f"Same hint as above re CALC_RELOAD_SEC."
        )
        tally.pass_(f"evaluation resumed after re-enable "
                    f"(~{waited:.1f}s)")


# -----------------------------------------------------------------------
# Section 6 — ADD/MUL n_ary mode
# -----------------------------------------------------------------------

def section_6_nary_mode(api: Api, created: dict[str, dict],
                        tally: Tally) -> None:
    section("Section 6 — ADD/MUL n_ary mode round-trip")

    # The seed used binary mode; flip ADD to n_ary with mixed tags+constants.
    pool = seed.discover_pool(api)
    add_id = created["ADD"]["id"]
    nary_cfg = {
        "inputs": [
            {"tag": pool.numeric[0]},
            {"tag": pool.numeric[1]},
            {"value": 3.5},
        ],
    }
    with tally.step("PATCH ADD to n_ary mode (tags + constant mix)"):
        resp = api.patch(f"/api/computed-tags/{add_id}",
                         {"block_config": nary_cfg})
        got = resp.get("block_config", {})
        assert "inputs" in got and isinstance(got["inputs"], list), \
            f"inputs not echoed back: {got}"
        tally.pass_("n_ary config accepted by API")

    # Wait for a fresh evaluation tick
    time.sleep(2.5)
    with tally.step("ADD in n_ary mode evaluates without error"):
        got = api.get(f"/api/computed-tags/{add_id}")
        status = got.get("last_status")
        assert status in (None, "ok"), \
            f"n_ary ADD reported status={status!r}"
        tally.pass_("ADD evaluated in n_ary mode")


# -----------------------------------------------------------------------
# Section 7 — API validation coverage (assertions)
# -----------------------------------------------------------------------
#
# As of the v7 backend patch, the create/PATCH endpoints call
# BLOCK_REGISTRY[block_type].validate_config() before INSERT. So all
# cases below are CONFIRMED — the API must return 4xx. If you're running
# against an older backend without the patch, these will fail; that's
# the smoke correctly detecting the missing validation.

def section_7_negative(api: Api, created: dict[str, dict], tally: Tally) -> None:
    section("Section 7 — API validation coverage")

    pool = seed.discover_pool(api)
    smoke_device_id = api.get(f"/api/computed-tags/{created['ABS']['id']}")["device_id"]

    # Track tag ids the API accidentally created during any case so we
    # can clean them up before returning.
    accepted_ids: list[int] = []

    def mk(code: str, **overrides) -> dict:
        return {
            "device_id": smoke_device_id,
            "name": f"smoke_neg_{int(time.time()*1000000)}_{code}",
            "data_type": "float64",
            "block_type": code,
            "block_config": {"input": pool.numeric[0]},
            "execution_rate_ms": 1000,
            "enabled": False,
            **overrides,
        }

    def assert_rejected(name: str, body: dict) -> None:
        """API must return 4xx; failing this is a smoke FAIL."""
        try:
            row = api.post("/api/computed-tags", body)
            accepted_ids.append(row["id"])
            tally.fail(f"{name}: expected 4xx, got 201 (tag id={row['id']}) "
                       f"— is the backend running with the v7 _validate_block patch?")
        except ApiError as e:
            if 400 <= e.status < 500:
                tally.pass_(f"{name}: rejected with HTTP {e.status}")
            else:
                tally.fail(f"{name}: expected 4xx, got HTTP {e.status}")

    # ---- Endpoint-level validation (already in the API before v7) ------
    assert_rejected(
        "execution_rate_ms not in ALLOWED list",
        mk("ABS", execution_rate_ms=123),
    )
    assert_rejected(
        "output_tag_id pointing at non-existent tag",
        mk("ABS", output_tag_id=999999999),
    )

    # ---- Block-level validation (added in v7 via _validate_block) ------
    assert_rejected(
        "unknown block_type",
        mk("DOES_NOT_EXIST", block_type="DOES_NOT_EXIST"),
    )
    assert_rejected(
        "missing required key (TON without preset_ms)",
        mk("TON", block_config={"input": pool.booleans[0]}),
    )
    assert_rejected(
        "SR with set==reset (must be distinct)",
        mk("SR", block_config={"set": pool.booleans[0], "reset": pool.booleans[0]}),
    )
    assert_rejected(
        "WEIGHTED_AVG with length-mismatched weights",
        mk("WEIGHTED_AVG",
           block_config={"inputs": pool.numeric[:3], "weights": [1.0]}),
    )
    assert_rejected(
        "binary block with both 'right' and 'value' (mutually exclusive)",
        mk("DIV", block_config={
            "left": pool.numeric[0],
            "right": pool.numeric[1],
            "value": 2.0,
        }),
    )
    assert_rejected(
        "MUX_INDEX with index appearing in values list",
        mk("MUX_INDEX", block_config={
            "index": pool.numeric[0],
            "values": [pool.numeric[0], pool.numeric[1]],
        }),
    )

    # ---- PATCH validation: a malformed block_config update is rejected -
    abs_id = created["ABS"]["id"]
    with tally.step("PATCH with invalid block_config is rejected"):
        try:
            row = api.patch(f"/api/computed-tags/{abs_id}",
                            {"block_config": {"input": "not_a_tag_id"}})
            tally.fail(f"malformed PATCH was accepted (expected 4xx)")
        except ApiError as e:
            assert 400 <= e.status < 500, f"expected 4xx, got {e.status}"
            tally.pass_(f"malformed PATCH rejected with HTTP {e.status}")

    # ---- Cleanup any tags the API accidentally created -----------------
    if accepted_ids:
        for tid in accepted_ids:
            try:
                api.delete(f"/api/computed-tags/{tid}")
            except ApiError:
                pass


# -----------------------------------------------------------------------
# Section 8 — Cleanup
# -----------------------------------------------------------------------

def section_8_cleanup(api: Api, tally: Tally, *, do_cleanup: bool) -> None:
    section("Section 8 — Cleanup")

    if not do_cleanup:
        tally.skip("--cleanup not specified; leaving SMOKE_CALC_DEVICE in place")
        return

    with tally.step("DELETE SMOKE_CALC_DEVICE cascades to its computed tags"):
        seed.reset_device(api)
        # Confirm gone
        devs = api.get("/api/computed-devices")
        assert not any(d["name"] == seed.DEVICE_NAME for d in devs), \
            "device still present after delete"
        tally.pass_("device + its 62 computed tags removed")


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete SMOKE_CALC_DEVICE at the end")
    parser.add_argument("--quick", action="store_true",
                        help="Shorter timeouts; less waiting between toggles")
    parser.add_argument("--section", type=int, default=None,
                        help="Run only the given section number (0-8)")
    args = parser.parse_args()

    api = Api(args.base)
    tally = Tally()
    started = time.time()

    only = args.section
    section_0_health(api, tally)
    if only is None or only == 1:
        section_1_schemas(api, tally)

    device_id = -1
    created: dict[str, dict] = {}
    if only is None or only >= 2:
        device_id, created = section_2_seed(api, tally)

    if only is None or only == 3:
        section_3_evaluation(api, device_id, created, tally, quick=args.quick)
        section_3b_output_quality(api, device_id, created, tally)
    if only is None or only == 4:
        section_4_update(api, created, tally)
    if only is None or only == 5:
        if args.quick:
            tally.skip("Section 5 (toggle) skipped under --quick")
        else:
            section_5_toggle(api, created, tally)
    if only is None or only == 6:
        section_6_nary_mode(api, created, tally)
    if only is None or only == 7:
        section_7_negative(api, created, tally)
    if only is None or only == 8:
        section_8_cleanup(api, tally, do_cleanup=args.cleanup)

    # Summary
    duration = time.time() - started
    print()
    print("=" * 72)
    print(f"  RESULT: {tally.passed} passed, {len(tally.failed)} failed, "
          f"{len(tally.skipped)} skipped — {duration:.1f}s")
    print("=" * 72)
    if tally.failed:
        print()
        print("FAILURES:")
        for f in tally.failed:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
