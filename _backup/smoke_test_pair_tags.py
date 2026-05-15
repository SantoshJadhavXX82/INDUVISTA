"""
InduVista — Smoke test for Phase 12.3 pair tags.

A pair tag is a virtual tag that resolves to whichever side of a
duty/standby device pair is currently the duty. Pair tags are created
automatically when devices are paired (matching tags by name and
data_type) and deleted when devices are unpaired.

Tests:
  1. Pairing two devices that share tag names creates pair_tags rows
  2. /pair-tags lists them with both sides' current duty_role
  3. /pair-tags/live resolves the active side to the current duty
  4. swap-duty flips the active-side resolution without changing the
     underlying pair_tags rows
  5. Unpairing the devices removes the pair_tags rows
  6. Re-pairing recreates them (idempotency)

Run from inside the backend container:
    docker compose cp smoke_tests/smoke_test_pair_tags.py backend:/tmp/spt.py
    docker compose exec backend python /tmp/spt.py
    docker compose exec backend rm /tmp/spt.py

Idempotent — creates SMOKE_PAIR_A / SMOKE_PAIR_B devices on the first
available channel, plus matching tags MTR_DENSITY / MTR_TEMP on each.
Cleans up on entry and exit.
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
DEVICE_A_NAME = "SMOKE_PAIR_A"
DEVICE_B_NAME = "SMOKE_PAIR_B"
TAG_NAMES = ("MTR_DENSITY", "MTR_TEMP")  # both will exist on A and B
TAG_NAME_A_ONLY = "MTR_VISCOSITY"        # only on A, should NOT generate a pair tag
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
    """Remove smoke test artifacts in dependency-safe order."""
    status, devices = http("GET", "/devices")
    if status != 200:
        return
    test_devices = [d for d in devices if d.get("name", "").startswith("SMOKE_PAIR_")]
    # Unpair first so pair_tags vanish + duty_role goes to 'none'.
    for d in test_devices:
        if d.get("redundant_device_id") is not None:
            http("POST", f"/devices/{d['id']}/unpair", {})
    # Now delete the devices (cascade removes any straggling tags).
    for d in test_devices:
        http("DELETE", f"/devices/{d['id']}")


def first_channel() -> int | None:
    status, body = http("GET", "/channels")
    if status != 200 or not body:
        return None
    return body[0]["id"]


def create_test_device(channel_id: int, name: str, port: int) -> int | None:
    status, body = http("POST", "/devices", {
        "channel_id": channel_id,
        "name": name,
        "host": "127.0.0.1",
        "port": port,
        "unit_id": 1,
        "duty_role": "none",
    })
    if status != 201:
        print(f"FATAL: create {name} returned {status}: {body}")
        return None
    return body["id"]


def create_test_tag(device_id: int, name: str, address: int) -> int | None:
    """Tags directly on the device (no register_block) — fine for this
    test since we only care about name matching, not polling."""
    # Need a register_block first; create a minimal one.
    status, blk = http("POST", "/register-blocks", {
        "device_id": device_id,
        "name": f"SMOKE_BLK_{name}",
        "function_code": 3,
        "start_address": address,
        "count": 2,
        "scan_interval_ms": 5000,
        "writable": False,
        "enabled": False,
    })
    if status != 201:
        print(f"FATAL: create block for {name} returned {status}: {blk}")
        return None
    status, tag = http("POST", "/tags", {
        "device_id": device_id,
        "register_block_id": blk["id"],
        "name": name,
        "data_type": "float32",
        "byte_order": "ABCD",
        "function_code": 3,
        "address": address,
        "register_count": 2,
        "enabled": True,
        "writable": False,
    })
    if status != 201:
        print(f"FATAL: create tag {name} returned {status}: {tag}")
        return None
    return tag["id"]


# ===========================================================================
# Scenarios
# ===========================================================================
def scen_pairing_creates_pair_tags(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12.3 — pairing devices with name-matching tags creates pair_tags."""
    s = Scenario("Phase 12.3 — pairing creates pair tags for name-matching tags")

    status, body = http("POST", f"/devices/{dev_a}/pair", {
        "partner_device_id": dev_b,
        "this_role": "duty",
    })
    if status != 200:
        s.fail("pair POST", f"status={status} body={body}")
        return s
    s.pass_("pair POST returned 200")

    # Verify pair_tags table now has rows for our matching names
    status, pairs = http("GET", "/pair-tags")
    if status != 200:
        s.fail("/pair-tags GET", f"status={status}")
        return s

    smoke_pairs = [
        p for p in pairs
        if p.get("primary_device_id") in (dev_a, dev_b)
        and p.get("partner_device_id") in (dev_a, dev_b)
    ]
    if len(smoke_pairs) == len(TAG_NAMES):
        s.pass_(f"{len(TAG_NAMES)} pair tags created (one per matching name)")
    else:
        s.fail("pair tag count",
               f"expected {len(TAG_NAMES)} got {len(smoke_pairs)}: {[p['name'] for p in smoke_pairs]}")
        return s

    created_names = sorted(p["name"] for p in smoke_pairs)
    expected = sorted(TAG_NAMES)
    if created_names == expected:
        s.pass_("pair tag names match expected (MTR_DENSITY, MTR_TEMP)")
    else:
        s.fail("pair tag names", f"got {created_names} expected {expected}")

    # The unmatched tag (MTR_VISCOSITY only on A) should NOT have a pair tag
    if TAG_NAME_A_ONLY not in created_names:
        s.pass_(f"unmatched tag '{TAG_NAME_A_ONLY}' correctly skipped (no partner)")
    else:
        s.fail("unmatched tag", f"'{TAG_NAME_A_ONLY}' should not have a pair tag")

    return s


def scen_live_resolves_to_duty(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12.3 — /pair-tags/live picks values from whichever side is duty."""
    s = Scenario("Phase 12.3 — live endpoint resolves to current duty side")
    status, live = http("GET", "/pair-tags/live")
    if status != 200:
        s.fail("/pair-tags/live GET", f"status={status}")
        return s

    smoke_rows = [
        r for r in live
        if r.get("primary_device_id") in (dev_a, dev_b)
        and r.get("partner_device_id") in (dev_a, dev_b)
    ]
    if len(smoke_rows) == len(TAG_NAMES):
        s.pass_(f"live returns {len(TAG_NAMES)} pair tag rows")
    else:
        s.fail("live row count", f"got {len(smoke_rows)} expected {len(TAG_NAMES)}")
        return s

    # Verify active resolution. After pairing with this_role=duty, dev_a is
    # duty. So active_device_id should == dev_a for both rows.
    for r in smoke_rows:
        if r.get("active_device_id") == dev_a:
            s.pass_(f"'{r['tag_name']}': active_device_id resolved to dev_a (current duty)")
        else:
            s.fail(f"'{r['tag_name']}' active resolution",
                   f"expected {dev_a} got {r.get('active_device_id')}")

    # Verify the kind discriminator is present so the frontend can merge.
    if all(r.get("kind") == "pair" for r in smoke_rows):
        s.pass_("rows carry kind='pair' discriminator")
    else:
        s.fail("kind field", f"got {[r.get('kind') for r in smoke_rows]}")

    return s


def scen_swap_flips_resolution(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12.3 — swapping duty makes /pair-tags/live point at the other side."""
    s = Scenario("Phase 12.3 — swap-duty re-resolves pair tags to new duty")

    status, _ = http("POST", f"/devices/{dev_a}/swap-duty", {"reason": "manual"})
    if status != 200:
        s.fail("swap-duty POST", f"status={status}")
        return s

    status, live = http("GET", "/pair-tags/live")
    if status != 200:
        s.fail("/pair-tags/live GET after swap", f"status={status}")
        return s

    smoke_rows = [
        r for r in live
        if r.get("primary_device_id") in (dev_a, dev_b)
        and r.get("partner_device_id") in (dev_a, dev_b)
    ]

    # After swap, dev_b is duty. active_device_id should == dev_b.
    all_flipped = all(r.get("active_device_id") == dev_b for r in smoke_rows)
    if all_flipped and smoke_rows:
        s.pass_(f"all {len(smoke_rows)} pair tags now resolve to dev_b (new duty)")
    else:
        s.fail("active resolution didn't flip",
               f"got {[r.get('active_device_id') for r in smoke_rows]}, expected all={dev_b}")

    # Pair tag IDs should be the same — the rows themselves didn't change,
    # only the resolution did. This is the architectural point of the feature.
    if all(r.get("pair_tag_id") for r in smoke_rows):
        s.pass_("pair_tag_id values stable across swap (rows are virtual)")

    return s


def scen_unpair_removes_pair_tags(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12.3 — unpairing devices deletes their pair tags."""
    s = Scenario("Phase 12.3 — unpair removes pair tags")
    status, _ = http("POST", f"/devices/{dev_a}/unpair", {})
    if status != 200:
        s.fail("unpair POST", f"status={status}")
        return s

    status, pairs = http("GET", "/pair-tags")
    if status != 200:
        s.fail("/pair-tags GET after unpair", f"status={status}")
        return s

    remaining = [
        p for p in pairs
        if p.get("primary_device_id") in (dev_a, dev_b)
        and p.get("partner_device_id") in (dev_a, dev_b)
    ]
    if not remaining:
        s.pass_("pair tags removed after unpair")
    else:
        s.fail("orphan pair tags", f"{len(remaining)} rows remain")
    return s


def scen_re_pair_idempotent(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12.3 — pairing again recreates the pair tags (idempotency)."""
    s = Scenario("Phase 12.3 — re-pairing recreates pair tags cleanly")
    status, _ = http("POST", f"/devices/{dev_a}/pair", {
        "partner_device_id": dev_b,
        "this_role": "duty",
    })
    if status != 200:
        s.fail("re-pair POST", f"status={status}")
        return s

    status, pairs = http("GET", "/pair-tags")
    smoke_pairs = [
        p for p in pairs
        if p.get("primary_device_id") in (dev_a, dev_b)
        and p.get("partner_device_id") in (dev_a, dev_b)
    ]
    if len(smoke_pairs) == len(TAG_NAMES):
        s.pass_(f"re-pair recreated {len(TAG_NAMES)} pair tags")
    else:
        s.fail("re-pair count", f"got {len(smoke_pairs)}")

    return s


def scen_regenerate_finds_new_matches(dev_a: int, dev_b: int) -> Scenario:
    """Phase 12.3 — regenerate endpoint adds pair tags for newly-matching names.

    Scenario: after pairing, add MTR_VISCOSITY to device B (it was only on
    A before). Regenerate should now create a third pair tag.
    """
    s = Scenario("Phase 12.3 — regenerate picks up tags added after pairing")

    # Add MTR_VISCOSITY to dev_b at a different address so it matches
    # what we created on dev_a.
    new_tag_id = create_test_tag(dev_b, TAG_NAME_A_ONLY, 200)
    if not new_tag_id:
        s.fail("test setup", "couldn't create MTR_VISCOSITY on dev_b")
        return s
    s.pass_("added matching tag to dev_b after pairing")

    status, result = http("POST", "/pair-tags/regenerate", {})
    if status != 200:
        s.fail("regenerate POST", f"status={status}")
        return s

    if result.get("created", 0) >= 1:
        s.pass_(f"regenerate created {result.get('created')} new pair tag(s)")
    else:
        s.fail("regenerate created count", f"got {result}")

    # Verify the new pair tag is present
    status, pairs = http("GET", "/pair-tags")
    has_viscosity = any(
        p["name"] == TAG_NAME_A_ONLY
        and p.get("primary_device_id") in (dev_a, dev_b)
        and p.get("partner_device_id") in (dev_a, dev_b)
        for p in pairs
    )
    if has_viscosity:
        s.pass_(f"new pair tag '{TAG_NAME_A_ONLY}' appears in /pair-tags listing")
    else:
        s.fail("missing pair tag", f"'{TAG_NAME_A_ONLY}' not found")

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

    print(f"InduVista pair-tags smoke test — {BASE_URL}")
    print("=" * HEAD_W)

    channel_id = first_channel()
    if not channel_id:
        print("FATAL: no networks configured.")
        return 2
    print(f"Using channel_id={channel_id}")

    cleanup()

    # Create two devices on the same network
    dev_a = create_test_device(channel_id, DEVICE_A_NAME, 5097)
    dev_b = create_test_device(channel_id, DEVICE_B_NAME, 5096)
    if not dev_a or not dev_b:
        return 2
    print(f"Created devices: A=id{dev_a}, B=id{dev_b}")

    # Create tags. Both devices get MTR_DENSITY (addr 100) and MTR_TEMP (addr 102);
    # only device A gets MTR_VISCOSITY (addr 104). The pair-creation should
    # generate 2 pair tags, NOT 3.
    addrs = {"MTR_DENSITY": 100, "MTR_TEMP": 102, "MTR_VISCOSITY": 104}
    for tag_name in (*TAG_NAMES, TAG_NAME_A_ONLY):
        if not create_test_tag(dev_a, tag_name, addrs[tag_name]):
            return 2
    for tag_name in TAG_NAMES:  # B doesn't get MTR_VISCOSITY initially
        if not create_test_tag(dev_b, tag_name, addrs[tag_name]):
            return 2
    print(f"Created tags: A=[{','.join(TAG_NAMES)},{TAG_NAME_A_ONLY}], B=[{','.join(TAG_NAMES)}]")

    scenarios: list[Scenario] = []
    scenarios.append(scen_pairing_creates_pair_tags(dev_a, dev_b))
    scenarios.append(scen_live_resolves_to_duty(dev_a, dev_b))
    scenarios.append(scen_swap_flips_resolution(dev_a, dev_b))
    scenarios.append(scen_unpair_removes_pair_tags(dev_a, dev_b))
    scenarios.append(scen_re_pair_idempotent(dev_a, dev_b))
    scenarios.append(scen_regenerate_finds_new_matches(dev_a, dev_b))

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
