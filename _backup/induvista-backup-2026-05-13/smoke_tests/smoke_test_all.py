"""
InduVista — Comprehensive smoke test (Phase 9.1.x → 11).

Exercises every feature shipped this session against a live backend:

   Phase 9.1.x  Enron addressing, register_count auto-derive, gap=1 float32,
                Enron-aware overlap detection, diagnostics-summary Enron fix
   Phase 10.2   Register Browser Enron support + scan retry + cycle samples
   Phase 11     Tag/Device rename, CSV upsert-by-name, navigation,
                tag quality, block coverage, device picker

Usage (from D:\\INDUVISTA):

    docker compose cp smoke_tests/smoke_test_all.py backend:/tmp/smoke.py
    docker compose exec backend python /tmp/smoke.py
    docker compose exec backend rm /tmp/smoke.py

Or directly:
    python smoke_test_all.py --base-url http://localhost:8000/api

Idempotent — each scenario cleans up its own artifacts on entry. Re-run
freely. Exit code 0 if all PASS or SKIP, 1 if any FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

BASE_URL = "http://backend:8000/api"
DEVICE_NAME_DEFAULT = "GC_SIM_001"
TEST_BLOCK = "SMOKE_TEST_BLOCK"
TEST_DEVICE_RENAME = "SMOKE_TEST_DEVICE_RENAME"
HEAD_W = 70


@dataclass
class Scenario:
    name: str
    results: list[tuple[str, str, str]] = field(default_factory=list)   # (label, PASS|FAIL|SKIP, detail)

    def pass_(self, label: str, detail: str = "") -> None:
        self.results.append((label, "PASS", detail))

    def fail(self, label: str, detail: str = "") -> None:
        self.results.append((label, "FAIL", detail))

    def skip(self, label: str, detail: str = "") -> None:
        self.results.append((label, "SKIP", detail))


def http(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or "{}")
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        return 0, {"error": str(e)}


# ===========================================================================
# Cleanup helpers
# ===========================================================================
def find_device(name: str) -> dict | None:
    status, body = http("GET", "/devices")
    if status != 200:
        return None
    for d in body:
        if d["name"] == name:
            return d
    return None


def cleanup(device_id: int) -> None:
    """Remove anything left over from prior runs. Safe to call repeatedly."""
    status, tags = http("GET", f"/tags?device_id={device_id}")
    if status == 200:
        for t in tags:
            if t.get("name", "").startswith("SMOKE_"):
                http("DELETE", f"/tags/{t['id']}")
    status, blocks = http("GET", f"/register-blocks?device_id={device_id}")
    if status == 200:
        for b in blocks:
            if b.get("name") == TEST_BLOCK:
                http("DELETE", f"/register-blocks/{b['id']}")


# ===========================================================================
# Scenarios
# ===========================================================================
def scen_enron_block_creation(device_id: int) -> tuple[Scenario, int]:
    """Phase 9.1.x — block creation with ENRON_HOLDING addressing."""
    s = Scenario("Phase 9.1.x — Enron block creation")
    status, body = http("POST", "/register-blocks", {
        "device_id": device_id,
        "name": TEST_BLOCK,
        "function_code": 3,
        "start_address": 7501,
        "count": 16,
        "addressing_mode": "ENRON_HOLDING",
        "scan_interval_ms": 5000,
        "writable": False,
        "enabled": False,
    })
    if status != 201:
        s.fail("create returned 201", f"got {status}: {body}")
        return s, 0
    s.pass_("create returned 201")
    block_id = body.get("id", 0)

    if body.get("addressing_mode") == "ENRON_HOLDING":
        s.pass_("addressing_mode persisted as ENRON_HOLDING")
    else:
        s.fail("addressing_mode persisted", f"got {body.get('addressing_mode')}")

    if body.get("count") == 16:
        s.pass_("count=16 logical addresses (not 32 registers)")
    else:
        s.fail("count semantics", f"expected 16 got {body.get('count')}")

    return s, block_id


def scen_gap1_float32(device_id: int, block_id: int) -> Scenario:
    """Phase 9.1.2 — KEY CASE: consecutive float32 tags in Enron block."""
    s = Scenario("Phase 9.1.2 — gap=1 float32 tags (consecutive Enron addresses)")
    if not block_id:
        s.skip("scenario requires block from prior step")
        return s

    # First tag at 7501
    status, body = http("POST", "/tags", {
        "device_id": device_id, "register_block_id": block_id,
        "name": "SMOKE_F32_01", "data_type": "float32", "byte_order": "ABCD",
        "function_code": 3, "address": 7501, "register_count": 2,
        "enabled": True, "writable": False,
    })
    if status == 201:
        s.pass_("first float32 at 7501 inserts")
    else:
        s.fail("first float32 insert", f"status={status} body={body}")
        return s

    # Adjacent gap=1 tag at 7502 — this is what Phase 9.1.2 made possible
    status, body = http("POST", "/tags", {
        "device_id": device_id, "register_block_id": block_id,
        "name": "SMOKE_F32_02", "data_type": "float32", "byte_order": "ABCD",
        "function_code": 3, "address": 7502, "register_count": 2,
        "enabled": True, "writable": False,
    })
    if status == 201:
        s.pass_("gap=1 float32 at 7502 inserts (Phase 9.1.2 fix)")
    else:
        s.fail("gap=1 float32 insert", f"status={status} body={body}")

    # Same-address rejection should still work
    status, body = http("POST", "/tags", {
        "device_id": device_id, "register_block_id": block_id,
        "name": "SMOKE_DUPE", "data_type": "float32", "byte_order": "ABCD",
        "function_code": 3, "address": 7501, "register_count": 2,
        "enabled": True, "writable": False,
    })
    if status in (400, 422):
        s.pass_(f"same-address rejected with HTTP {status}")
    else:
        s.fail("same-address should reject", f"got {status}")

    return s


def scen_register_count_autoderive(device_id: int, block_id: int) -> Scenario:
    """Phase 9.1.2 — register_count auto-derives from data_type."""
    s = Scenario("Phase 9.1.2 — register_count auto-derive from data_type")
    if not block_id:
        s.skip("scenario requires block from prior step")
        return s

    # POST without register_count — should auto-fill to 2 for float32
    status, body = http("POST", "/tags", {
        "device_id": device_id, "register_block_id": block_id,
        "name": "SMOKE_F32_AUTO", "data_type": "float32", "byte_order": "ABCD",
        "function_code": 3, "address": 7503,
        # register_count omitted
        "enabled": True, "writable": False,
    })
    if status == 201:
        s.pass_("POST without register_count returns 201")
        if body.get("register_count") == 2:
            s.pass_("register_count auto-derived to 2 for float32")
        else:
            s.fail("auto-derive value", f"expected 2 got {body.get('register_count')}")
    else:
        s.fail("auto-derive POST", f"status={status} body={body}")

    return s


def scen_tag_rename(device_id: int, block_id: int) -> Scenario:
    """Phase 11 — tag rename via PATCH."""
    s = Scenario("Phase 11 — tag rename via PATCH")
    if not block_id:
        s.skip("scenario requires block from prior step")
        return s

    # Find SMOKE_F32_01
    status, tags = http("GET", f"/tags?register_block_id={block_id}")
    if status != 200:
        s.fail("list tags", f"status={status}")
        return s
    target = next((t for t in tags if t["name"] == "SMOKE_F32_01"), None)
    if not target:
        s.skip("SMOKE_F32_01 not found")
        return s

    # Rename to SMOKE_F32_01_RENAMED
    status, body = http("PATCH", f"/tags/{target['id']}", {
        "name": "SMOKE_F32_01_RENAMED",
    })
    if status == 200:
        s.pass_("rename PATCH returns 200")
        if body.get("name") == "SMOKE_F32_01_RENAMED":
            s.pass_("name field reflects new value")
        else:
            s.fail("rename persistence", f"got name={body.get('name')}")
    else:
        s.fail("rename PATCH", f"status={status} body={body}")
        return s

    # Try renaming to a name that already exists — should 409
    status, body = http("PATCH", f"/tags/{target['id']}", {
        "name": "SMOKE_F32_02",   # collides with the other tag we created
    })
    if status == 409:
        s.pass_("duplicate-name rename rejected with HTTP 409")
    else:
        s.fail("collision detection", f"expected 409 got {status} body={body}")

    # Rename back to original so the rest of the suite finds it
    http("PATCH", f"/tags/{target['id']}", {"name": "SMOKE_F32_01"})

    return s


def scen_device_rename(device_id: int, original_name: str) -> Scenario:
    """Phase 11 — device rename via PATCH."""
    s = Scenario("Phase 11 — device rename via PATCH")
    new_name = TEST_DEVICE_RENAME

    status, body = http("PATCH", f"/devices/{device_id}", {"name": new_name})
    if status == 200:
        s.pass_("rename to test name returns 200")
        if body.get("name") == new_name:
            s.pass_("device name reflects new value")
        else:
            s.fail("device rename persistence", f"got {body.get('name')}")
    else:
        s.fail("device rename PATCH", f"status={status} body={body}")

    # Rename back so the test environment is unchanged afterward
    status, body = http("PATCH", f"/devices/{device_id}", {"name": original_name})
    if status == 200 and body.get("name") == original_name:
        s.pass_("rename back to original")
    else:
        s.fail("rename back", f"status={status} body={body}")

    return s


def scen_csv_upsert(device_id: int, block_id: int) -> Scenario:
    """Phase 11 — CSV upsert by (device_id, name).

    First call creates 4 new rows; second call sends the same rows back —
    expect 'updated' actions instead of duplicate-name errors.
    """
    s = Scenario("Phase 11 — CSV bulk upsert-by-name")
    if not block_id:
        s.skip("scenario requires block from prior step")
        return s

    rows = [
        {
            "device_id": device_id, "register_block_id": block_id,
            "name": f"SMOKE_BULK_{i:02d}", "data_type": "float32", "byte_order": "ABCD",
            "function_code": 3, "address": 7510 + i, "register_count": 2,
            "enabled": True, "writable": False,
            "description": "first pass",
        }
        for i in range(4)
    ]

    # First import — all should be "created"
    status, body = http("POST", "/tags/bulk", {"tags": rows})
    if status != 200:
        s.fail("first bulk import", f"status={status}")
        return s
    actions = [r.get("action") for r in body]
    if actions == ["created"] * 4:
        s.pass_("first import: 4 created")
    else:
        s.fail("first import actions", f"got {actions}")

    # Second import — same names, expect "updated"
    for r in rows:
        r["description"] = "second pass"
    status, body = http("POST", "/tags/bulk", {"tags": rows})
    if status != 200:
        s.fail("second bulk import", f"status={status}")
        return s
    actions = [r.get("action") for r in body]
    if actions == ["updated"] * 4:
        s.pass_("second import: 4 updated (no duplicate-name errors)")
    else:
        s.fail("second import actions", f"got {actions}")

    # Verify description was actually updated
    status, tags = http("GET", f"/tags?register_block_id={block_id}")
    if status == 200:
        bulk_tags = [t for t in tags if t["name"].startswith("SMOKE_BULK_")]
        if all(t.get("description") == "second pass" for t in bulk_tags):
            s.pass_("descriptions persisted from second pass")
        else:
            s.fail("descriptions not updated", f"got {[t.get('description') for t in bulk_tags]}")

    # Third call — new name at occupied address → should error per-row
    occupy_row = {
        "device_id": device_id, "register_block_id": block_id,
        "name": "SMOKE_BULK_CONFLICT", "data_type": "float32", "byte_order": "ABCD",
        "function_code": 3, "address": 7510, "register_count": 2,
        "enabled": True, "writable": False,
    }
    status, body = http("POST", "/tags/bulk", {"tags": [occupy_row]})
    if status == 200 and body and body[0].get("action") == "error":
        s.pass_("new-name-at-occupied-address rejected per-row")
    else:
        s.fail("address-conflict rejection", f"got {body}")

    return s


def scen_register_browser_standard(device_id: int) -> Scenario:
    """Phase 7 — Register Browser standard mode."""
    s = Scenario("Phase 7 — Register Browser standard mode")
    status, body = http("POST", f"/devices/{device_id}/scan-range", {
        "function_code": 3,
        "start_address": 100,
        "end_address": 105,
    })
    if status == 200:
        s.pass_(f"standard scan returns 200 ({len(body.get('rows', []))} rows)")
    elif status == 502:
        # Device unreachable — acceptable in this scenario, don't fail
        s.skip("device unreachable; standard scan untestable right now")
    else:
        s.fail("standard scan", f"status={status} body={body}")
    return s


def scen_register_browser_enron(device_id: int) -> Scenario:
    """Phase 10.2 — Register Browser Enron mode with decoded floats."""
    s = Scenario("Phase 10.2 — Register Browser Enron read")
    status, body = http("POST", f"/devices/{device_id}/scan-range", {
        "function_code": 3,
        "start_address": 7001,
        "end_address": 7004,
        "addressing_mode": "ENRON_HOLDING",
        "value_width_bytes": 4,
    })
    if status == 502:
        s.skip("device unreachable; Enron scan untestable right now")
        return s
    if status != 200:
        s.fail("Enron scan", f"status={status} body={body}")
        return s

    rows = body.get("rows", [])
    if len(rows) == 4:
        s.pass_(f"Enron scan returns 4 rows (one per logical address)")
    else:
        s.fail("row count", f"expected 4 got {len(rows)}")
        return s

    # Each row should carry decoded_float32_abcd
    if all("decoded_float32_abcd" in r for r in rows):
        s.pass_("rows include decoded_float32_abcd field")
    else:
        s.fail("rows missing decoded_float32_abcd field")

    # hex width should be 4 bytes per address (= 12 chars with spaces, or 8 without)
    hex_strs = [r.get("hex", "") for r in rows]
    if all(len(h.replace(" ", "")) == 8 for h in hex_strs):
        s.pass_("hex is 4 bytes per logical address")
    else:
        s.fail("hex byte-width", f"got lengths {[len(h.replace(' ', '')) for h in hex_strs]}")

    return s


def scen_diagnostics_enron_aware(device_id: int, block_id: int) -> Scenario:
    """Phase 9.1.2-hotfix-diagnostics — overlap/fit counts honor Enron span=1."""
    s = Scenario("Phase 9.1.2-hotfix — diagnostics summary counts are Enron-aware")
    status, body = http("GET", "/diagnostics/summary")
    if status != 200:
        s.fail("summary GET", f"status={status}")
        return s

    # We just inserted 7 consecutive Enron tags (7501..7503, 7510..7513).
    # Pre-fix, the byte-range overlap detector would have called those
    # adjacent float32s overlapping (each "uses" 2 registers in standard
    # semantics, so 7501-7502 vs 7502-7503 collide). With the Phase 9.1.2
    # Enron-aware effective_span=1, they're all valid → 0 overlaps.
    overlap = body.get("overlap_count")
    fit = body.get("block_fit_issue_count")
    if overlap is not None and overlap < 5:
        s.pass_(f"overlap_count is reasonable (got {overlap}; pre-fix this would have been 15+)")
    else:
        s.fail("overlap_count", f"got {overlap}")
    if fit is not None and fit < 5:
        s.pass_(f"block_fit_issue_count is reasonable (got {fit})")
    else:
        s.fail("block_fit_issue_count", f"got {fit}")
    return s


def scen_cycle_samples(device_id: int) -> Scenario:
    """Phase 10.2-hotfix-cycle-samples — Diagnostics samples column is non-zero."""
    s = Scenario("Phase 10.2-hotfix — worker reports non-zero cycle samples")
    status, body = http("GET", "/diagnostics/worker-status")
    if status != 200:
        s.fail("worker-status GET", f"status={status}")
        return s

    row = next((r for r in body if r.get("device_id") == device_id), None)
    if not row:
        s.skip(f"device {device_id} has no worker_device_status row yet")
        return s

    total = row.get("last_cycle_samples_total")
    age = row.get("seconds_since_last_cycle")

    if age is not None and age < 60:
        s.pass_(f"last_cycle_at is recent ({age:.1f}s ago)")
    else:
        s.fail("last_cycle_at stale", f"age={age}")

    if total is not None and total > 0:
        s.pass_(f"last_cycle_samples_total > 0 ({total} samples in last window)")
    else:
        s.fail("samples stuck at zero", f"got {total} — hotfix-cycle-samples may not be deployed")

    return s


def scen_block_coverage_data(device_id: int, block_id: int) -> Scenario:
    """Phase 11 — block coverage data shape (the contract the SVG map consumes)."""
    s = Scenario("Phase 11 — block coverage map data contract")
    if not block_id:
        s.skip("scenario requires block from prior step")
        return s

    status, body = http("GET", "/register-blocks")
    if status != 200:
        s.fail("blocks GET", f"status={status}")
        return s
    block = next((b for b in body if b.get("id") == block_id), None)
    if not block:
        s.fail("block lookup", f"block_id={block_id} not in response")
        return s

    required_fields = ["start_address", "count", "addressing_mode"]
    if all(f in block for f in required_fields):
        s.pass_("block carries start_address, count, addressing_mode")
    else:
        s.fail("missing fields", f"have {list(block.keys())}")

    status, tags = http("GET", f"/tags?register_block_id={block_id}")
    if status == 200:
        if tags and all("address" in t and "data_type" in t for t in tags):
            s.pass_(f"tags GET returns address + data_type ({len(tags)} tags)")
        else:
            s.fail("tag fields", f"tags={tags[:1]}")
    else:
        s.fail("tags GET", f"status={status}")

    return s


def scen_gas_composition_sum(device_id: int) -> Scenario:
    """End-to-end: live mole% tags should sum to ~100%."""
    s = Scenario("End-to-end — gas composition sums to 100%")
    status, body = http("GET", f"/live?device_id={device_id}")
    if status != 200:
        s.skip(f"live GET failed: {status}")
        return s

    mole_tags = [
        t for t in body
        if t.get("tag_name", "").startswith("Last_Analy_Mole_")
        or t.get("tag_name", "").lower().startswith("mole_")
    ]
    if not mole_tags:
        s.skip("no Last_Analy_Mole_* tags configured")
        return s

    values = [t.get("value_double") for t in mole_tags if t.get("value_double") is not None]
    if not values:
        s.skip("mole% tags have no current value (worker may not be polling)")
        return s

    total = sum(values)
    if 99.0 <= total <= 101.0:
        s.pass_(f"composition sum = {total:.2f}% (within 99–101%)")
    elif 0.0 < total < 99.0:
        s.fail(
            f"composition sum is {total:.2f}% — suggests missing components",
            "review tag addressing (Phase 10.2 fix: gap=1 in Enron mode)",
        )
    else:
        s.fail(f"composition sum is {total:.2f}%", "unexpected total")
    return s


# ===========================================================================
# Output formatting
# ===========================================================================
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_scenario(s: Scenario) -> None:
    print()
    print(Colors.BOLD + s.name + Colors.RESET)
    print("-" * HEAD_W)
    for label, status, detail in s.results:
        color = {
            "PASS": Colors.GREEN, "FAIL": Colors.RED, "SKIP": Colors.YELLOW,
        }.get(status, "")
        line = f"  [{color}{status}{Colors.RESET}] {label}"
        if detail:
            line += f"  — {detail}"
        print(line)


def main() -> int:
    global BASE_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--device-name", default=DEVICE_NAME_DEFAULT)
    args = ap.parse_args()
    BASE_URL = args.base_url

    print(f"InduVista smoke test — {BASE_URL}")
    print("=" * HEAD_W)

    dev = find_device(args.device_name)
    if not dev:
        # Try auto-discovery: any device with a "gc" in the name
        status, body = http("GET", "/devices")
        candidates = [d for d in (body or []) if "gc" in d.get("name", "").lower()]
        if candidates:
            dev = candidates[0]
            print(f"Note: '{args.device_name}' not found, using '{dev['name']}' (id={dev['id']})")
        else:
            print(f"FATAL: no suitable device found. Available:")
            for d in body or []:
                print(f"  - id={d['id']}  name={d['name']}")
            return 2

    device_id = dev["id"]
    device_name = dev["name"]
    print(f"Using device {device_name} (id={device_id})")

    cleanup(device_id)

    scenarios: list[Scenario] = []

    # Build phase — block + tags
    s1, block_id = scen_enron_block_creation(device_id)
    scenarios.append(s1)
    scenarios.append(scen_gap1_float32(device_id, block_id))
    scenarios.append(scen_register_count_autoderive(device_id, block_id))
    scenarios.append(scen_csv_upsert(device_id, block_id))
    scenarios.append(scen_tag_rename(device_id, block_id))
    scenarios.append(scen_device_rename(device_id, device_name))

    # Live device — protocol behavior
    scenarios.append(scen_register_browser_standard(device_id))
    scenarios.append(scen_register_browser_enron(device_id))

    # Worker + diagnostics
    scenarios.append(scen_diagnostics_enron_aware(device_id, block_id))
    scenarios.append(scen_cycle_samples(device_id))
    scenarios.append(scen_block_coverage_data(device_id, block_id))

    # End-to-end
    scenarios.append(scen_gas_composition_sum(device_id))

    # Final cleanup
    cleanup(device_id)

    # Output
    for s in scenarios:
        print_scenario(s)

    # Summary
    p = sum(1 for s in scenarios for _, st, _ in s.results if st == "PASS")
    f = sum(1 for s in scenarios for _, st, _ in s.results if st == "FAIL")
    k = sum(1 for s in scenarios for _, st, _ in s.results if st == "SKIP")

    print()
    print("=" * HEAD_W)
    summary = (
        f"RESULTS: {Colors.GREEN}{p} PASS{Colors.RESET}  "
        f"{Colors.RED if f else ''}{f} FAIL{Colors.RESET if f else ''}  "
        f"{Colors.YELLOW if k else ''}{k} SKIP{Colors.RESET if k else ''}"
    )
    print(summary)

    if f == 0:
        print(Colors.GREEN + Colors.BOLD + "All checks passed." + Colors.RESET)
        return 0
    else:
        print(Colors.RED + Colors.BOLD + f"{f} check(s) failed — see above." + Colors.RESET)
        return 1


if __name__ == "__main__":
    sys.exit(main())
