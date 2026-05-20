"""Seed one Computed Tag per block code, idempotently.

VERSION: v6 (value_filter support for input-value-sensitive blocks)
         Verify with: Select-String -Path scripts\\calc_blocks\\seed_all_blocks.py -Pattern "VERSION: v6"

Creates (or rebuilds, when --reset is passed) a dedicated Computed
Device named SMOKE_CALC_DEVICE and populates it with 62 computed tags —
one per block code in BLOCK_REGISTRY. Each tag's block_config references
real Modbus tags discovered on the running system, so the calc_evaluator
worker can actually exercise the block.

Usage:
    python seed_all_blocks.py                # idempotent: skips existing
    python seed_all_blocks.py --reset        # deletes the device and recreates
    python seed_all_blocks.py --dry-run      # print the plan, change nothing
    python seed_all_blocks.py --base http://10.0.0.5:8000   # remote backend

Exit codes:
    0  — all 62 created or already present
    2  — pool requirements unmet (run the 700xa tag pack seed first)
    3  — API failure during creation

The script is deliberately verbose so a fresh operator can read the
log and see which tag the script picked for each block's inputs. That
makes failures debuggable without grepping audit logs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _api_client import Api, ApiError
from _block_configs import RECIPES, TagPool, pool_requirements, BlockRecipe


# Names used by the seed. Stable so reruns are idempotent.
DEVICE_NAME = "SMOKE_CALC_DEVICE"
TAG_PREFIX = "smoke_blk_"     # full name = f"{TAG_PREFIX}{block_code.lower()}"


# ===========================================================================
# Pool discovery
# ===========================================================================

# Data-type buckets, mirrored from backend/app/api/tags.py DataType Literal
INT_TYPES = {"int16", "uint16", "int32", "uint32", "int64", "uint64"}
NUM_TYPES = INT_TYPES | {"float32", "float64"}
BOOL_TYPES = {"bool"}

# Quality / freshness thresholds. Matches the backend's GOOD_QUALITY = 128
# threshold from app/workers/calc_blocks/base.py. age_seconds threshold of
# 10 covers reasonable poll cadences (every block we exercise polls at
# 1-second rate; 10s of grace handles transient backlogs).
GOOD_QUALITY = 128
MAX_AGE_SECONDS = 10.0


def _eligible(live_row: dict) -> tuple[bool, str]:
    """Decide whether a tag (live snapshot) is eligible to be used as an
    input by the seed. Returns (ok, reason_if_not)."""
    if not live_row.get("enabled"):
        return False, "tag disabled"
    st = live_row.get("st")
    if st is None:
        return False, "never read (st is NULL)"
    if st < GOOD_QUALITY:
        return False, f"BAD quality (st={st})"
    age = live_row.get("age_seconds")
    if age is None:
        return False, "no recent value (age is NULL)"
    if age > MAX_AGE_SECONDS:
        return False, f"stale (age={age:.1f}s)"
    return True, ""


def discover_pool(api: Api) -> TagPool:
    """Walk /api/tags + /api/live and bucket eligible tags by data type.

    Strategy is quality-first with a documented fallback:
      1. Eligible = enabled AND st >= GOOD_QUALITY AND age_seconds <= 10
         (sorted by freshness, lowest age first).
      2. If pool_requirements() can't be met with eligible tags alone,
         the missing slots are filled from /api/tags directly (regardless
         of live quality) and a loud warning is printed. This lets the
         seed still create all 62 blocks; the smoke recognises BAD output
         on bool-input blocks as upstream-caused, not a code bug.

    Tags on SMOKE_CALC_DEVICE are always excluded."""
    return _discover_pool_augmented(api)[0]


def _discover_pool_augmented(api: Api) -> tuple[TagPool, dict]:
    """discover_pool() but also returns the report. The seed uses both
    when --diagnose is set."""
    pool, report = _discover_pool_with_report(api)
    reqs = pool_requirements()
    gaps = pool.needs(num=reqs["numeric"], ints=reqs["integer"], bools=reqs["bool"])
    report["augmented"] = {"numeric": 0, "integer": 0, "bool": 0}
    if not gaps:
        return pool, report

    # Need to fall back. Re-walk /api/tags WITHOUT the live-quality filter
    # to find degraded candidates we can use.
    tags = api.get("/api/tags", limit=1000)
    smoke_device_id = report["smoke_device_id"]
    have_numeric = set(pool.numeric)
    have_integer = set(pool.integer)
    have_bool    = set(pool.booleans)
    for t in tags:
        tid = t["id"]
        if smoke_device_id is not None and t.get("device_id") == smoke_device_id:
            continue
        dtype = t.get("data_type")
        if dtype in BOOL_TYPES and tid not in have_bool \
                and len(pool.booleans) < reqs["bool"]:
            pool.booleans.append(tid)
            have_bool.add(tid)
            report["augmented"]["bool"] += 1
        elif dtype in INT_TYPES and tid not in have_integer \
                and len(pool.integer) < reqs["integer"]:
            pool.integer.append(tid)
            have_integer.add(tid)
            report["augmented"]["integer"] += 1
            # integers also count as numeric
            if tid not in have_numeric and len(pool.numeric) < reqs["numeric"]:
                pool.numeric.append(tid)
                have_numeric.add(tid)
        elif dtype in NUM_TYPES and tid not in have_numeric \
                and len(pool.numeric) < reqs["numeric"]:
            pool.numeric.append(tid)
            have_numeric.add(tid)
            report["augmented"]["numeric"] += 1
    return pool, report


def _discover_pool_with_report(api: Api) -> tuple[TagPool, dict]:
    """Like discover_pool() but also returns a report of WHY each tag
    was included or excluded. The seed uses this for --diagnose output."""
    tags = api.get("/api/tags", limit=1000)
    live = api.get("/api/live")
    live_by_id = {row["tag_id"]: row for row in live}

    # Resolve smoke device id so we can skip its tags
    smoke_device_id: int | None = None
    for d in api.get("/api/computed-devices"):
        if d["name"] == DEVICE_NAME:
            smoke_device_id = d["id"]
            break

    pool = TagPool()
    # Per-tag candidate rows; we sort then truncate at the end
    cand_numeric: list[tuple[float, int, dict]] = []
    cand_integer: list[tuple[float, int, dict]] = []
    cand_bool:    list[tuple[float, int, dict]] = []
    rejected: list[tuple[int, str, str, str]] = []  # (id, name, dtype, reason)

    for t in tags:
        tid = t["id"]
        if smoke_device_id is not None and t.get("device_id") == smoke_device_id:
            continue
        dtype = t.get("data_type")
        name = t.get("name", "<unnamed>")
        live_row = live_by_id.get(tid)
        if live_row is None:
            rejected.append((tid, name, dtype, "not in /api/live (no read schedule)"))
            continue
        ok, reason = _eligible(live_row)
        if not ok:
            rejected.append((tid, name, dtype, reason))
            continue
        age = live_row["age_seconds"]
        # Bucket. Integer tags also count as numeric.
        if dtype in BOOL_TYPES:
            cand_bool.append((age, tid, live_row))
        elif dtype in INT_TYPES:
            cand_integer.append((age, tid, live_row))
            cand_numeric.append((age, tid, live_row))
        elif dtype in NUM_TYPES:
            cand_numeric.append((age, tid, live_row))

    cand_numeric.sort(key=lambda r: r[0])
    cand_integer.sort(key=lambda r: r[0])
    cand_bool.sort(key=lambda r: r[0])

    pool.numeric  = [r[1] for r in cand_numeric]
    pool.integer  = [r[1] for r in cand_integer]
    pool.booleans = [r[1] for r in cand_bool]

    # Build report
    report = {
        "smoke_device_id": smoke_device_id,
        "eligible": {
            "numeric": [(r[1], live_by_id[r[1]]["tag_name"], r[0]) for r in cand_numeric[:8]],
            "integer": [(r[1], live_by_id[r[1]]["tag_name"], r[0]) for r in cand_integer[:8]],
            "bool":    [(r[1], live_by_id[r[1]]["tag_name"], r[0]) for r in cand_bool[:8]],
        },
        "rejected_count": len(rejected),
        "rejected_sample": rejected[:20],
        "rejection_reasons": {},
    }
    # Bucket rejections by reason
    for tid, name, dtype, reason in rejected:
        key = reason.split(" (")[0]   # e.g. "BAD quality" → "BAD quality"
        report["rejection_reasons"].setdefault(key, []).append((tid, name, dtype))
    return pool, report


def print_pool_report(report: dict) -> None:
    print("    --- eligible inputs (best-first by freshness) ---")
    for slot in ("numeric", "integer", "bool"):
        rows = report["eligible"][slot]
        if not rows:
            print(f"      {slot:8s} : (none eligible)")
            continue
        first = rows[0]
        rest = f", +{len(rows)-1} more" if len(rows) > 1 else ""
        print(f"      {slot:8s} : top pick id={first[0]} name={first[1]!r} "
              f"age={first[2]:.1f}s{rest}")
    if report["rejected_count"]:
        print(f"    --- {report['rejected_count']} tag(s) rejected ---")
        for reason, items in sorted(report["rejection_reasons"].items()):
            print(f"      [{reason}] x{len(items)}")
            for tid, name, dtype in items[:3]:
                print(f"          id={tid} ({dtype}) name={name!r}")
            if len(items) > 3:
                print(f"          ... and {len(items) - 3} more")


# ===========================================================================
# Device management
# ===========================================================================

def ensure_device(api: Api, *, dry_run: bool = False) -> int:
    """Return the id of SMOKE_CALC_DEVICE, creating it if necessary."""
    devices = api.get("/api/computed-devices")
    for d in devices:
        if d["name"] == DEVICE_NAME:
            return d["id"]
    print(f"  device {DEVICE_NAME!r} not found — will create")
    if dry_run:
        return -1
    created = api.post("/api/computed-devices", {
        "name": DEVICE_NAME,
        "description": "Auto-managed by scripts/calc_blocks/seed_all_blocks.py. "
                       "Holds one computed tag per registered block code "
                       "for smoke-test purposes. Safe to delete.",
        "enabled": True,
        "scan_interval_ms": 1000,
    })
    print(f"  created device id={created['id']}")
    return created["id"]


def reset_device(api: Api) -> None:
    """Delete SMOKE_CALC_DEVICE if it exists. Cascades to its tags."""
    devices = api.get("/api/computed-devices")
    for d in devices:
        if d["name"] == DEVICE_NAME:
            print(f"  reset: deleting device id={d['id']} (cascades to its computed tags)")
            api.delete(f"/api/computed-devices/{d['id']}")
            return
    print(f"  reset: device {DEVICE_NAME!r} not found, nothing to delete")


# ===========================================================================
# Tag management
# ===========================================================================

def existing_tags_on_device(api: Api, device_id: int) -> dict[str, dict]:
    """Map name -> tag row for every tag on the smoke device."""
    all_tags = api.get("/api/tags", device_id=device_id, limit=1000)
    return {t["name"]: t for t in all_tags}


def tag_name_for(recipe: BlockRecipe) -> str:
    return f"{TAG_PREFIX}{recipe.code.lower()}"


def create_one(api: Api, *, device_id: int, recipe: BlockRecipe,
               pool: TagPool, live_by_id: dict[int, dict],
               dry_run: bool) -> dict:
    """POST /api/computed-tags for a single recipe. Returns the created
    row (or a stub when dry_run).

    If the recipe has a value_filter, build a per-recipe pool view that
    puts value-filter-passing tags first, so unary blocks see a tag
    whose current value is in the function's safe domain. Avoids e.g.
    EXP(2705) → inf showing as BAD in the UI."""
    effective_pool = pool
    filter_note = ""
    if recipe.value_filter is not None:
        def _filter_list(ids: list[int]) -> list[int]:
            accepting: list[int] = []
            rejecting: list[int] = []
            for tid in ids:
                row = live_by_id.get(tid)
                if row is None:
                    rejecting.append(tid)
                    continue
                val = row.get("value_double")
                try:
                    if val is not None and recipe.value_filter(float(val)):
                        accepting.append(tid)
                    else:
                        rejecting.append(tid)
                except (TypeError, ValueError):
                    rejecting.append(tid)
            return accepting + rejecting  # passers first, others as fallback

        # Apply to every type slot since the recipe might pick from any
        from dataclasses import replace as _replace
        effective_pool = _replace(
            pool,
            numeric=_filter_list(pool.numeric),
            integer=_filter_list(pool.integer),
            booleans=_filter_list(pool.booleans),
        )
        # Quick diagnostic note for the log
        n_pass_num = sum(
            1 for t in pool.numeric
            if (lambda v: v is not None and recipe.value_filter(float(v)))
               ((live_by_id.get(t) or {}).get("value_double"))
        )
        filter_note = f"  [value_filter: {recipe.value_filter_desc}; " \
                      f"{n_pass_num}/{len(pool.numeric)} numeric tags pass]"

    block_config = recipe.build_config(effective_pool)
    name = tag_name_for(recipe)
    body = {
        "device_id": device_id,
        "name": name,
        "data_type": recipe.output_dtype,
        "description": f"Smoke fixture for block {recipe.code} ({recipe.category})",
        "block_type": recipe.code,
        "block_config": block_config,
        "execution_rate_ms": 1000,
        "enabled": True,
    }
    if dry_run:
        return {"id": -1, "name": name, **body, "dry_run": True,
                "_filter_note": filter_note}
    try:
        result = api.post("/api/computed-tags", body)
        if filter_note:
            result["_filter_note"] = filter_note
        return result
    except ApiError as e:
        # Re-raise with the block_config attached for easier debugging
        raise ApiError(
            status=e.status,
            body=f"creating {recipe.code}: {e.body}\nsubmitted config: {json.dumps(block_config)}",
            url=e.url,
        ) from e


# ===========================================================================
# Main flow
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000",
                        help="Backend base URL (default: %(default)s)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete the smoke device first, then recreate from scratch")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; make no API changes")
    parser.add_argument("--diagnose", action="store_true",
                        help="Show pool report (what would be picked + what's rejected and why), then exit")
    parser.add_argument("--only", action="append", default=[],
                        metavar="CODE",
                        help="Seed only the given block code(s). Repeatable.")
    args = parser.parse_args()

    api = Api(args.base)
    print(f"==> waiting for backend at {args.base}")
    if not api.wait_ready(max_sec=60):
        print(f"!! backend at {args.base} did not become ready", file=sys.stderr)
        return 3
    print("    backend ready")

    # --- pool discovery (with live-quality filtering + degraded fallback) ---
    print("==> discovering input tags from /api/tags + /api/live")
    pool, report = _discover_pool_augmented(api)
    aug = report.get("augmented", {})
    print(f"    eligible (GOOD quality + fresh): {len(pool.numeric) - aug.get('numeric',0)} numeric, "
          f"{len(pool.integer) - aug.get('integer',0)} integer, "
          f"{len(pool.booleans) - aug.get('bool',0)} bool")
    print_pool_report(report)
    if any(aug.values()):
        print()
        print("    \033[33m" + "!" * 60 + "\033[0m")
        print("    \033[33m! WARNING: pool short on GOOD-quality tags — augmenting\033[0m")
        for slot in ("numeric", "integer", "bool"):
            if aug.get(slot, 0) > 0:
                print(f"    \033[33m!   added {aug[slot]} degraded {slot} tag(s)\033[0m")
        print("    \033[33m! Blocks reading these inputs will produce BAD-quality\033[0m")
        print("    \033[33m! output. Fix the upstream Modbus device/block to poll them.\033[0m")
        print("    \033[33m" + "!" * 60 + "\033[0m")

    if args.diagnose:
        print()
        print("--diagnose set; exiting without seeding")
        return 0

    reqs = pool_requirements()
    gaps = pool.needs(num=reqs["numeric"], ints=reqs["integer"], bools=reqs["bool"])
    if gaps:
        print()
        print("!! input pool is not sufficient to seed every block:", file=sys.stderr)
        for g in gaps:
            print(f"     {g}", file=sys.stderr)
        print(file=sys.stderr)
        print("   Hint: seed the 700xa tag pack first, or add a Modbus device "
              "with at least 4 numeric tags + 1 integer tag + 2 bool tags.",
              file=sys.stderr)
        return 2

    # --- device management ---
    print(f"==> ensuring device {DEVICE_NAME!r}")
    if args.reset and not args.dry_run:
        reset_device(api)
    device_id = ensure_device(api, dry_run=args.dry_run)
    print(f"    device_id={device_id}")

    # --- recipe selection ---
    recipes = RECIPES
    if args.only:
        wanted = {c.upper() for c in args.only}
        recipes = [r for r in RECIPES if r.code in wanted]
        unknown = wanted - {r.code for r in recipes}
        if unknown:
            print(f"!! unknown block codes: {sorted(unknown)}", file=sys.stderr)
            return 2
    print(f"==> seeding {len(recipes)} block(s)")

    # --- discover what's already there ---
    if device_id > 0:
        existing = existing_tags_on_device(api, device_id)
    else:
        existing = {}

    # Build live lookup once for value_filter use inside create_one
    live_by_id = {row["tag_id"]: row for row in api.get("/api/live")}

    created = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    for r in recipes:
        name = tag_name_for(r)
        if name in existing and not args.reset:
            print(f"  [skip] {r.code:18s} (already exists as tag id={existing[name]['id']})")
            skipped += 1
            continue
        try:
            row = create_one(api, device_id=device_id, recipe=r, pool=pool,
                             live_by_id=live_by_id, dry_run=args.dry_run)
            status = "DRY-RUN" if args.dry_run else f"id={row['id']}"
            note = row.get("_filter_note", "")
            print(f"  [ok]   {r.code:18s} ({r.category:14s}) → {status}{note}")
            created += 1
        except ApiError as e:
            print(f"  [FAIL] {r.code:18s} HTTP {e.status}", file=sys.stderr)
            print(f"         {e.body[:300]}", file=sys.stderr)
            failed.append((r.code, str(e)))

    # --- summary ---
    print()
    print("=" * 60)
    print(f"created: {created}   skipped: {skipped}   failed: {len(failed)}")
    if failed:
        print()
        print("FAILED block codes:")
        for code, msg in failed:
            print(f"  - {code}: {msg.splitlines()[0]}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
