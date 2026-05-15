"""
InduVista — Smoke test for Phase 12 duty/standby pairing.

Exercises the configuration layer of duty/standby (NOT failover behavior
in the worker — that's a separate Phase 12.2 deliverable).

Tests:
   1. Constraint enforcement: duty_role=duty without partner → 400
   2. POST /devices/{id}/pair → both rows updated atomically, symmetric
   3. Constraint enforcement: can't pair device with itself → 400
   4. POST /devices/{id}/swap-duty → roles flip, history row recorded
   5. GET /devices/{id}/duty-history → swap appears with correct reason
   6. POST /devices/{id}/unpair → both rows revert to duty_role='none'

Run from inside the backend container:
    docker compose cp smoke_tests/smoke_test_duty_standby.py backend:/tmp/sds.py
    docker compose exec backend python /tmp/sds.py
    docker compose exec backend rm /tmp/sds.py

Or directly:
    python smoke_test_duty_standby.py --base-url http://localhost:8000/api

Idempotent — creates two test devices named SMOKE_DUTY_A / SMOKE_DUTY_B
on the first available channel, cleans them up on entry. Re-run freely.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

BASE_URL = "http://backend:8000/api"
DEVICE_A_NAME = "SMOKE_DUTY_A"
DEVICE_B_NAME = "SMOKE_DUTY_B"
HEAD_W = 70


@dataclass
class Scenario:
    name: str
    results: list[tuple[str, str, str]] = field(default_factory=list)

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
    if data is not None:
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


def cleanup() -> None:
    """Remove any prior SMOKE_DUTY_* devices."""
    status, body = http("GET", "/devices")
    if status != 200:
        return
    for d in body:
        if d.get("name", "") in (DEVICE_A_NAME, DEVICE_B_NAME):
            # Unpair first so DELETE doesn't violate FK constraints from
            # device_duty_history (CASCADE on FK handles the history rows).
            if d.get("redundant_device_id") is not None:
                http("POST", f"/devices/{d['id']}/unpair", {})
            http("DELETE", f"/devices/{d['id']}")


def first_channel() -> int | None:
    status, body = http("GET", "/channels")
    if status != 200 or not body:
        return None
    return body[0]["id"]


def create_test_device(channel_id: int, name: str, host: str, port: int) -> int | None:
    status, body = http("POST", "/devices", {
        "channel_id": channel_id,
        "name": name,
        "host": host,
        "port": port,
        "unit_id": 1,
        # Important: create unpaired so the form's create flow is mirrored
        "duty_role": "none",
    })
    if status != 201:
        print(f"FATAL: create {name} returned {status}: {body}")
        return None
    return body["id"]


# ===========================================================================
# Scenarios
# ===========================================================================
def scen_constraint_blocks_unpaired_duty(channel_id: int) -> Scenario:
    """Phase 12 — duty_role='duty' without partner must be rejected."""
    s = Scenario("Phase 12 — DB constraint rejects duty_role without partner")
    status, body = http("POST", "/devices", {
        "channel_id": channel_id,
        "name": "SMOKE_DUTY_BAD",
        "host": "127.0.0.1",
        "port": 5099,
        "unit_id": 1,
        "duty_role": "duty",
        # NO redundant_device_id — should violate ck_devices_duty_role_consistency
    })
    if status in (400, 409, 422):
        detail = str(body.get("detail", "")).lower()
        if "duty_role" in detail or "consistency" in detail or "constraint" in detail:
            s.pass_(f"unpaired duty_role rejected with HTTP {status}")
        else:
            s.pass_(f"unpaired duty_role rejected with HTTP {status} (message: {body.get('detail', '?')})")
    elif status == 201:
        # If somehow accepted, clean up so we don't leak it
        if isinstance(body, dict) and "id" in body:
            http("DELETE", f"/devices/{body['id']}")
        s.fail("constraint not enforced", "POST returned 201 with no partner")
    else:
        s.fail("unexpected status", f"got {status} body={body}")
    return s


def scen_pair_endpoint(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12 — POST /pair sets both rows atomically and symmetrically."""
    s = Scenario("Phase 12 — pair endpoint creates symmetric duty/standby pair")
    status, body = http("POST", f"/devices/{dev_a}/pair", {
        "partner_device_id": dev_b,
        "this_role": "duty",
    })
    if status != 200:
        s.fail("pair POST", f"status={status} body={body}")
        return s
    s.pass_("pair POST returned 200")

    if body.get("duty_role") == "duty" and body.get("redundant_device_id") == dev_b:
        s.pass_("device A: duty_role=duty, redundant_device_id=B")
    else:
        s.fail("device A state",
               f"role={body.get('duty_role')} partner={body.get('redundant_device_id')}")

    # Check the partner via a separate fetch
    status_b, body_b = http("GET", f"/devices/{dev_b}")
    if status_b == 200:
        if body_b.get("duty_role") == "standby" and body_b.get("redundant_device_id") == dev_a:
            s.pass_("device B: duty_role=standby, redundant_device_id=A (auto-set by pair)")
        else:
            s.fail("device B state",
                   f"role={body_b.get('duty_role')} partner={body_b.get('redundant_device_id')}")
    else:
        s.fail("device B fetch", f"status={status_b}")

    return s


def scen_pair_self_rejected(dev_a: int) -> Scenario:
    """Phase 12 — pairing a device with itself must be rejected."""
    s = Scenario("Phase 12 — pair endpoint rejects self-pairing")
    status, body = http("POST", f"/devices/{dev_a}/pair", {
        "partner_device_id": dev_a,
        "this_role": "duty",
    })
    if status == 400:
        s.pass_(f"self-pairing rejected with HTTP 400")
    else:
        s.fail("self-pairing should reject", f"got {status} body={body}")
    return s


def scen_swap_duty(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12 — swap-duty flips roles atomically and inserts history row."""
    s = Scenario("Phase 12 — swap-duty flips roles + records history")

    # Read pre-swap state
    _, pre_a = http("GET", f"/devices/{dev_a}")
    _, pre_b = http("GET", f"/devices/{dev_b}")

    status, body = http("POST", f"/devices/{dev_a}/swap-duty", {
        "reason": "manual",
        "notes": "smoke test swap",
    })
    if status != 200:
        s.fail("swap-duty POST", f"status={status} body={body}")
        return s
    s.pass_("swap-duty POST returned 200")

    # Verify both roles flipped (whoever was duty is now standby and vice versa)
    if body.get("duty_role") != pre_a.get("duty_role"):
        s.pass_(f"device A flipped: {pre_a.get('duty_role')} → {body.get('duty_role')}")
    else:
        s.fail("device A role didn't flip", f"still {body.get('duty_role')}")

    _, post_b = http("GET", f"/devices/{dev_b}")
    if post_b.get("duty_role") != pre_b.get("duty_role"):
        s.pass_(f"device B flipped: {pre_b.get('duty_role')} → {post_b.get('duty_role')}")
    else:
        s.fail("device B role didn't flip", f"still {post_b.get('duty_role')}")

    # Pair pointers must be intact
    if body.get("redundant_device_id") == dev_b and post_b.get("redundant_device_id") == dev_a:
        s.pass_("pair pointers preserved through swap")
    else:
        s.fail("pair pointers broken",
               f"A.partner={body.get('redundant_device_id')} B.partner={post_b.get('redundant_device_id')}")

    return s


def scen_duty_history(dev_a: int) -> Scenario:
    """Phase 12 — duty-history endpoint returns the swap we just made."""
    s = Scenario("Phase 12 — duty-history surfaces recent swaps")
    status, body = http("GET", f"/devices/{dev_a}/duty-history")
    if status != 200:
        s.fail("duty-history GET", f"status={status}")
        return s
    if not isinstance(body, list):
        s.fail("duty-history shape", f"expected list got {type(body).__name__}")
        return s
    if len(body) < 2:
        # We expect at least 2 rows: the 'startup' pair-creation + the 'manual' swap
        s.fail("history row count",
               f"expected ≥2 rows (pair + swap), got {len(body)}: {body}")
        return s
    s.pass_(f"history has {len(body)} rows")

    # Newest-first ordering
    reasons = [r["reason"] for r in body]
    if reasons[0] == "manual":
        s.pass_("newest row is the 'manual' swap")
    else:
        s.fail("ordering", f"expected newest='manual', got {reasons[0]}")

    # Notes round-trip
    if any(r.get("notes") == "smoke test swap" for r in body):
        s.pass_("swap notes persisted")
    else:
        s.fail("notes missing", f"got {[r.get('notes') for r in body]}")

    # Device names included (the join in /duty-history)
    if all(r.get("device_name") and r.get("paired_device_name") for r in body):
        s.pass_("history rows include both device names")
    else:
        s.fail("missing device names", str(body[:1]))

    return s


def scen_unpair(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12 — unpair reverts both rows to standalone."""
    s = Scenario("Phase 12 — unpair reverts both rows to duty_role='none'")
    status, body = http("POST", f"/devices/{dev_a}/unpair", {})
    if status != 200:
        s.fail("unpair POST", f"status={status} body={body}")
        return s
    s.pass_("unpair POST returned 200")

    if body.get("duty_role") == "none" and body.get("redundant_device_id") is None:
        s.pass_("device A: duty_role=none, redundant_device_id=NULL")
    else:
        s.fail("device A state after unpair",
               f"role={body.get('duty_role')} partner={body.get('redundant_device_id')}")

    _, body_b = http("GET", f"/devices/{dev_b}")
    if body_b.get("duty_role") == "none" and body_b.get("redundant_device_id") is None:
        s.pass_("device B: duty_role=none, redundant_device_id=NULL")
    else:
        s.fail("device B state after unpair",
               f"role={body_b.get('duty_role')} partner={body_b.get('redundant_device_id')}")
    return s


def scen_swap_without_pair_rejected(dev_a: int) -> Scenario:
    """Phase 12 — swap on an unpaired device is rejected."""
    s = Scenario("Phase 12 — swap-duty rejected on unpaired device")
    status, body = http("POST", f"/devices/{dev_a}/swap-duty", {"reason": "manual"})
    if status == 400:
        s.pass_("swap on unpaired device rejected with HTTP 400")
    else:
        s.fail("expected 400", f"got {status} body={body}")
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
    args = ap.parse_args()
    BASE_URL = args.base_url

    print(f"InduVista duty/standby smoke test — {BASE_URL}")
    print("=" * HEAD_W)

    # Find a channel to put the test devices on.
    channel_id = first_channel()
    if not channel_id:
        print("FATAL: no channels configured. Create at least one in Configure → Networks first.")
        return 2
    print(f"Using channel_id={channel_id}")

    cleanup()

    # Create two test devices
    dev_a = create_test_device(channel_id, DEVICE_A_NAME, "127.0.0.1", 5098)
    dev_b = create_test_device(channel_id, DEVICE_B_NAME, "127.0.0.1", 5099)
    if not dev_a or not dev_b:
        return 2
    print(f"Created test devices: A=id{dev_a}, B=id{dev_b}")

    scenarios: list[Scenario] = []
    scenarios.append(scen_constraint_blocks_unpaired_duty(channel_id))
    scenarios.append(scen_pair_self_rejected(dev_a))
    scenarios.append(scen_pair_endpoint(dev_a, dev_b))
    scenarios.append(scen_swap_duty(dev_a, dev_b))
    scenarios.append(scen_duty_history(dev_a))
    scenarios.append(scen_unpair(dev_a, dev_b))
    scenarios.append(scen_swap_without_pair_rejected(dev_a))

    # Cleanup
    cleanup()

    for s in scenarios:
        print_scenario(s)

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
        print(Colors.RED + Colors.BOLD + f"{f} check(s) failed." + Colors.RESET)
        return 1


if __name__ == "__main__":
    sys.exit(main())
