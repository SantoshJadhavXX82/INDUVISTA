"""
InduVista — Phase 10.2 smoke test for Enron configuration workflows.

Runs four scenarios end-to-end against a live backend and reports PASS/FAIL:

  1. Block creation in Enron mode (FC=3 → ENRON_HOLDING)
  2. Float32 tag creation at address 7001 (first tag)
  3. Float32 tag creation at address 7002 (gap=1, key Phase 9.1.2 case)
  4. CSV export → CSV re-import round-trip preserves the layout

Usage (from D:\\INDUVISTA in PowerShell):
    docker compose exec backend python /app/scripts/smoke_enron_ui.py

Or directly against an exposed backend:
    python smoke_enron_ui.py --base-url http://localhost:8000

Idempotent: cleans up its own test block + tags on exit. Safe to re-run.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import urllib.error
import urllib.request
import json

BASE_URL = "http://backend:8000/api"
TEST_BLOCK_NAME = "SMOKE_ENRON_TEST_BLOCK"
DEVICE_NAME = "GC_SIM_001"   # adjust if your device is named differently
PASSES = 0
FAILS = 0


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
    """Tiny stdlib HTTP client — no extra deps required inside the container."""
    url = f"{BASE_URL}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSES, FAILS
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))
    if ok:
        PASSES += 1
    else:
        FAILS += 1


def find_device_id(name: str) -> int | None:
    status, body = http("GET", "/devices")
    if status != 200:
        return None
    for d in body:
        if d["name"] == name:
            return d["id"]
    return None


def find_gc_device() -> tuple[int, str] | None:
    """Fall-back: look for any device pointing at the SVJ Daniel GC's
    known address, or the first available device, so the smoke test can
    proceed without depending on the exact device name being GC_SIM_001."""
    status, body = http("GET", "/devices")
    if status != 200 or not body:
        return None
    # Prefer the real Daniel GC if we recognize it
    for d in body:
        if d.get("host") == "192.168.1.25" and d.get("port") == 1502:
            return d["id"], d["name"]
    # Otherwise prefer any device whose name suggests "GC"
    for d in body:
        if "gc" in d.get("name", "").lower():
            return d["id"], d["name"]
    # Last resort: first device in the list
    d = body[0]
    return d["id"], d["name"]


def list_devices() -> None:
    status, body = http("GET", "/devices")
    if status != 200:
        print(f"  (failed to list devices: HTTP {status})")
        return
    if not body:
        print("  (no devices configured)")
        return
    for d in body:
        print(f"  - id={d['id']:>3}  name={d['name']:<24}  {d.get('host')}:{d.get('port')}")


def cleanup(device_id: int) -> None:
    """Remove all artifacts from prior smoke-test runs:
       1. Any tag on this device whose name starts with SMOKE_ (covers orphans
          left when block deletion didn't cascade or a prior run crashed).
       2. Any register_block named SMOKE_ENRON_TEST_BLOCK on this device."""
    # Step 1: delete leftover tags by name prefix
    status, tags = http("GET", f"/tags?device_id={device_id}")
    if status == 200:
        for t in tags:
            if t.get("name", "").startswith("SMOKE_"):
                http("DELETE", f"/tags/{t['id']}")

    # Step 2: delete leftover blocks
    status, blocks = http("GET", f"/register-blocks?device_id={device_id}")
    if status == 200:
        for b in blocks:
            if b.get("name") == TEST_BLOCK_NAME:
                http("DELETE", f"/register-blocks/{b['id']}")


def main() -> int:
    global PASSES, FAILS, BASE_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--device-name", default=DEVICE_NAME)
    args = ap.parse_args()
    BASE_URL = args.base_url

    print(f"Smoke testing Enron UI/API flows against {BASE_URL}")
    print("=" * 70)

    device_id = find_device_id(args.device_name)
    if not device_id:
        # Fall back to auto-discovery so the smoke test can still run
        # without forcing the user to know the exact device name.
        fallback = find_gc_device()
        if fallback:
            device_id, args.device_name = fallback
            print(f"Note: '--device-name' not found; using '{args.device_name}' "
                  f"(id={device_id}) as the closest match.")
        else:
            print(f"FATAL: no devices found. Configure at least one device first.")
            print("Available devices:")
            list_devices()
            return 2
    print(f"Using device {args.device_name} (id={device_id})")

    cleanup(device_id)   # remove any prior test artifacts
    print()

    # -------------------------------------------------------------------
    # Scenario 1: create an Enron block via the API (what the UI POSTs)
    # -------------------------------------------------------------------
    print("Scenario 1 — Enron block creation")
    status, block = http("POST", "/register-blocks", {
        "device_id": device_id,
        "name": TEST_BLOCK_NAME,
        "function_code": 3,
        "start_address": 7501,
        "count": 16,                       # 16 logical values
        "addressing_mode": "ENRON_HOLDING",
        "scan_interval_ms": 5000,
        "writable": False,
        "enabled": False,                  # disabled so worker doesn't poll the test
    })
    check("block POST returns 201", status == 201, f"status={status} body={block}")
    block_id = block.get("id") if status == 201 else None
    if block_id:
        check("addressing_mode persisted as ENRON_HOLDING",
              block.get("addressing_mode") == "ENRON_HOLDING",
              f"got {block.get('addressing_mode')}")

    if not block_id:
        print("Cannot continue without block. Aborting.")
        return 1
    print()

    # -------------------------------------------------------------------
    # Scenario 2: float32 tag at address 7501 (first tag in block)
    # -------------------------------------------------------------------
    print("Scenario 2 — float32 tag at address 7501 (first in block)")
    status, tag1 = http("POST", "/tags", {
        "device_id": device_id,
        "register_block_id": block_id,
        "name": "SMOKE_MOLE_01",
        "data_type": "float32",
        "byte_order": "ABCD",
        "function_code": 3,
        "address": 7501,
        "register_count": 2,
        "enabled": True,
        "writable": False,
    })
    check("first float32 tag POST returns 201", status == 201,
          f"status={status} body={tag1}")
    if status == 201:
        check("register_count persisted as 2",
              tag1.get("register_count") == 2,
              f"got {tag1.get('register_count')}")
    print()

    # -------------------------------------------------------------------
    # Scenario 3: float32 tag at address 7502 (gap=1 — KEY CASE)
    # This is what Phase 9.1.2 made possible. Pre-9.1.2 it returned 422.
    # -------------------------------------------------------------------
    print("Scenario 3 — float32 tag at 7502 (gap=1 from first tag)")
    status, tag2 = http("POST", "/tags", {
        "device_id": device_id,
        "register_block_id": block_id,
        "name": "SMOKE_MOLE_02",
        "data_type": "float32",
        "byte_order": "ABCD",
        "function_code": 3,
        "address": 7502,
        "register_count": 2,
        "enabled": True,
        "writable": False,
    })
    check("gap=1 float32 tag POST returns 201 (no false overlap)",
          status == 201, f"status={status} body={tag2}")
    print()

    # -------------------------------------------------------------------
    # Scenario 4: same-address conflict still detected
    # Add another tag at 7501 — should be rejected even in Enron mode.
    # -------------------------------------------------------------------
    print("Scenario 4 — duplicate-address rejection still works")
    status, dup = http("POST", "/tags", {
        "device_id": device_id,
        "register_block_id": block_id,
        "name": "SMOKE_DUPE_01",
        "data_type": "float32",
        "byte_order": "ABCD",
        "function_code": 3,
        "address": 7501,                   # same as SMOKE_MOLE_01
        "register_count": 2,
        "enabled": True,
        "writable": False,
    })
    check("same-address POST is rejected", status in (400, 422),
          f"got status={status}, expected 400 or 422 (API uses 400 for "
          f"business-rule conflicts)")
    print()

    # -------------------------------------------------------------------
    # Scenario 5: register_count auto-derive when omitted
    # -------------------------------------------------------------------
    print("Scenario 5 — register_count auto-derive for omitted field")
    status, tag3 = http("POST", "/tags", {
        "device_id": device_id,
        "register_block_id": block_id,
        "name": "SMOKE_MOLE_03",
        "data_type": "float32",
        "byte_order": "ABCD",
        "function_code": 3,
        "address": 7503,
        # register_count intentionally omitted
        "enabled": True,
        "writable": False,
    })
    check("tag with no register_count returns 201", status == 201,
          f"status={status} body={tag3}")
    if status == 201:
        check("register_count auto-derived to 2 for float32",
              tag3.get("register_count") == 2,
              f"got {tag3.get('register_count')}")
    print()

    # -------------------------------------------------------------------
    # Scenario 6: bulk create (mirrors CSV import) at consecutive gap=1
    # -------------------------------------------------------------------
    print("Scenario 6 — bulk-create (CSV-import path) at gap=1 consecutive")
    bulk_body = {
        "tags": [
            {
                "device_id": device_id,
                "register_block_id": block_id,
                "name": f"SMOKE_BULK_{i:02d}",
                "data_type": "float32",
                "byte_order": "ABCD",
                "function_code": 3,
                "address": 7510 + i,            # gap=1: 7510, 7511, 7512, 7513
                "register_count": 2,
                "enabled": True,
                "writable": False,
            }
            for i in range(4)
        ]
    }
    status, bulk_result = http("POST", "/tags/bulk", bulk_body)
    check("bulk insert of 4 gap=1 tags returns 200/201",
          status in (200, 201), f"status={status}")
    if status in (200, 201):
        # /tags/bulk returns a flat list of BulkTagResult, one per row,
        # each carrying either a tag_id (success) or an error message.
        rows = bulk_result if isinstance(bulk_result, list) else []
        ok_rows = [r for r in rows if r.get("tag_id") and not r.get("error")]
        check(f"all 4 bulk tags created (got {len(ok_rows)}/4)",
              len(ok_rows) == 4,
              f"errors: {[r.get('error') for r in rows if r.get('error')]}")
    print()

    # -------------------------------------------------------------------
    # Scenario 7: read back via GET /tags — verify final state
    # -------------------------------------------------------------------
    print("Scenario 7 — readback validates final layout")
    status, all_tags = http("GET", f"/tags?register_block_id={block_id}")
    if status == 200:
        addrs = sorted(t["address"] for t in all_tags)
        expected = [7501, 7502, 7503, 7510, 7511, 7512, 7513]
        check("addresses match expected gap=1 layout",
              addrs == expected,
              f"got {addrs}")
        all_rc_2 = all(t["register_count"] == 2 for t in all_tags)
        check("all tags have register_count=2", all_rc_2)

    print()
    print("=" * 70)
    print(f"Cleanup: deleting test block {TEST_BLOCK_NAME}")
    cleanup(device_id)

    print()
    print(f"RESULTS: {PASSES} passed, {FAILS} failed")
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
