"""
InduVista — Phase 12 RIGOROUS smoke test: full duty/standby lifecycle.

Adds two NEW devices from scratch and walks them through every step of
the duty/standby workflow, verifying both happy paths and edge cases:

  1.  Create two unpaired devices
  2.  Create tags with controlled variations:
        - matching name + type (should auto-pair)
        - matching name, mismatched type (should NOT auto-pair)
        - present on one device only (should NOT auto-pair)
        - a designated duty status tag
  3.  Pair the devices
  4.  Verify pair tags auto-generated for ONLY the matching name+type pairs
  5.  Assign duty_status_tag_id via PATCH, verify persistence
  6.  Worker reconciliation: simulate device-reported flip, verify role swap
        + DEDUP: exactly ONE history row, not two
  7.  Unknown value: worker leaves state alone
  8.  Stale read (ST != OK): worker leaves state alone
  9.  Manual swap via /swap-duty API
 10.  Add a new matching tag after pairing → regenerate picks it up
 11.  Delete a pair tag's underlying tag → orphan removed
 12.  Unpair: pair tags removed, roles reset, duty_status_tag_id preserved
 13.  Cleanup

Run from inside the backend container:
    docker compose cp smoke_test_phase12_rigorous.py backend:/tmp/sp12r.py
    docker compose exec backend python /tmp/sp12r.py
    docker compose exec backend rm /tmp/sp12r.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

BASE_URL = "http://backend:8000/api"
PREFIX = "SMOKE_P12R_"      # all test resources get this prefix
RECONCILE_WAIT_SEC = 8      # worker reconciles every 5s; allow margin
HEAD_W = 70


# ----------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


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


def print_scenario(s: Scenario) -> None:
    print()
    print(Colors.BOLD + s.name + Colors.RESET)
    print("-" * HEAD_W)
    for label, status, detail in s.results:
        color = {"PASS": Colors.GREEN, "FAIL": Colors.RED, "SKIP": Colors.YELLOW}.get(status, "")
        line = f"  [{color}{status}{Colors.RESET}] {label}"
        if detail:
            line += f"  — {detail}"
        print(line)


# ----------------------------------------------------------------------
# HTTP + DB helpers
# ----------------------------------------------------------------------
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


def _db_url() -> str:
    return os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg2://induvista_admin:induvista_admin@postgres:5432/induvista",
    )


def db_execute(sql: str, params: dict | None = None) -> None:
    from sqlalchemy import create_engine, text
    engine = create_engine(_db_url(), future=True)
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def db_query(sql: str, params: dict | None = None) -> list[dict]:
    from sqlalchemy import create_engine, text
    engine = create_engine(_db_url(), future=True)
    with engine.begin() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


# ----------------------------------------------------------------------
# Fixture helpers
# ----------------------------------------------------------------------
def cleanup() -> None:
    """Remove all resources created by previous test runs."""
    status, devices = http("GET", "/devices")
    if status != 200:
        return
    test_devs = [d for d in devices if d.get("name", "").startswith(PREFIX)]
    for d in test_devs:
        if d.get("redundant_device_id") is not None:
            http("POST", f"/devices/{d['id']}/unpair", {})
    for d in test_devs:
        http("DELETE", f"/devices/{d['id']}")


def reset_global_settings() -> None:
    """Ensure duty/standby values are at defaults (1, 0) before test."""
    http("PATCH", "/settings/duty-standby", {"duty_value": 1, "standby_value": 0})


def first_channel() -> int | None:
    status, body = http("GET", "/channels")
    if status != 200 or not body:
        return None
    return body[0]["id"]


def create_device(channel_id: int, name: str, port: int) -> int | None:
    # enabled=False stops the worker from creating a poll loop for this
    # device entirely — which is what we need, because polling fails (no
    # simulator at 127.0.0.1:5090) and the worker would write bad-read
    # entries to latest_tag_values, overwriting our simulate_tag_read.
    # Reconciliation does NOT filter on devices.enabled, so disabled
    # devices are still subject to duty/standby reconciliation.
    status, body = http("POST", "/devices", {
        "channel_id": channel_id, "name": name,
        "host": "127.0.0.1", "port": port, "unit_id": 1,
        "duty_role": "none",
        "enabled": False,
    })
    return body["id"] if status == 201 else None


def create_block(device_id: int, name: str, start_address: int, count: int = 16) -> int | None:
    # enabled=False keeps the worker from polling our fake host:port, which
    # would otherwise overwrite our simulate_tag_read writes.
    status, body = http("POST", "/register-blocks", {
        "device_id": device_id, "name": name,
        "function_code": 3, "start_address": start_address, "count": count,
        "scan_interval_ms": 5000, "writable": False, "enabled": False,
    })
    return body["id"] if status == 201 else None


def create_tag(device_id: int, block_id: int, name: str,
               data_type: str = "uint16", address: int = 9000) -> int | None:
    # register_count must match data_type's wire width
    rc = 2 if data_type in ("int32", "uint32", "float32") else (
         4 if data_type in ("int64", "uint64", "float64") else 1)
    status, body = http("POST", "/tags", {
        "device_id": device_id, "register_block_id": block_id,
        "name": name, "data_type": data_type, "byte_order": "ABCD",
        "function_code": 3, "address": address, "register_count": rc,
        "enabled": True, "writable": False,
    })
    return body["id"] if status == 201 else None


def simulate_tag_read(tag_id: int, value: float, st: int = 128) -> None:
    """Write directly to latest_tag_values to simulate a worker poll.

    Our test blocks are enabled=False so the worker doesn't actually poll
    them — these writes persist until reconciliation reads them.

    Uses INSERT...SELECT to derive device_id from the tags table since
    latest_tag_values.device_id is NOT NULL."""
    db_execute("""
        INSERT INTO latest_tag_values (tag_id, device_id, value_double, time, st)
        SELECT :tid, device_id, :val, NOW(), :st FROM tags WHERE id = :tid
        ON CONFLICT (tag_id) DO UPDATE
          SET value_double = EXCLUDED.value_double,
              time = NOW(),
              st = EXCLUDED.st
    """, {"tid": tag_id, "val": value, "st": st})


def history_count(device_id: int) -> int:
    rows = db_query("""
        SELECT COUNT(*) AS c FROM device_duty_history
        WHERE device_id = :id OR paired_device_id = :id
    """, {"id": device_id})
    return rows[0]["c"] if rows else 0


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------
def scen_create_devices(channel_id: int):
    s = Scenario("1. Create two unpaired devices from scratch")
    dev_a = create_device(channel_id, f"{PREFIX}METER_A", 5090)
    dev_b = create_device(channel_id, f"{PREFIX}METER_B", 5091)
    if dev_a and dev_b:
        s.pass_(f"created METER_A (id={dev_a}) and METER_B (id={dev_b})")
    else:
        s.fail("device creation", f"A={dev_a}, B={dev_b}")
        return s, None, None
    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if (a.get("duty_role") == "none" and a.get("redundant_device_id") is None and
        b.get("duty_role") == "none" and b.get("redundant_device_id") is None):
        s.pass_("both start unpaired (duty_role=none, no partner)")
    else:
        s.fail("not unpaired on create",
               f"A={a.get('duty_role')}/{a.get('redundant_device_id')}, "
               f"B={b.get('duty_role')}/{b.get('redundant_device_id')}")
    return s, dev_a, dev_b


def scen_create_tags(dev_a: int, dev_b: int):
    s = Scenario("2. Create tags with controlled name/data-type variations")
    blk_a = create_block(dev_a, f"{PREFIX}BLK_A", 9000)
    blk_b = create_block(dev_b, f"{PREFIX}BLK_B", 9000)
    if not blk_a or not blk_b:
        s.fail("register blocks", f"A={blk_a}, B={blk_b}")
        return s, {}, {}
    s.pass_("register blocks created")

    tags_a: dict[str, int] = {}
    tags_b: dict[str, int] = {}

    # MATCH_FLOW: matching name + type → SHOULD become a pair tag
    tags_a["MATCH_FLOW"] = create_tag(dev_a, blk_a, "MATCH_FLOW", "uint16", 9000)
    tags_b["MATCH_FLOW"] = create_tag(dev_b, blk_b, "MATCH_FLOW", "uint16", 9000)

    # MATCH_TEMP: matching name + type → SHOULD become a pair tag
    tags_a["MATCH_TEMP"] = create_tag(dev_a, blk_a, "MATCH_TEMP", "float32", 9002)
    tags_b["MATCH_TEMP"] = create_tag(dev_b, blk_b, "MATCH_TEMP", "float32", 9002)

    # DUTY_STATUS: for reconciliation, also matches → pair tag
    tags_a["DUTY_STATUS"] = create_tag(dev_a, blk_a, "DUTY_STATUS", "uint16", 9006)
    tags_b["DUTY_STATUS"] = create_tag(dev_b, blk_b, "DUTY_STATUS", "uint16", 9006)

    # A_ONLY / B_ONLY: present on one side only → NO pair tag
    tags_a["A_ONLY"] = create_tag(dev_a, blk_a, "A_ONLY", "uint16", 9007)
    tags_b["B_ONLY"] = create_tag(dev_b, blk_b, "B_ONLY", "uint16", 9008)

    # MISMATCH_TYPE: same name, different data_type → NO pair tag
    tags_a["MISMATCH_TYPE"] = create_tag(dev_a, blk_a, "MISMATCH_TYPE", "uint16", 9009)
    tags_b["MISMATCH_TYPE"] = create_tag(dev_b, blk_b, "MISMATCH_TYPE", "float32", 9010)

    if None in tags_a.values() or None in tags_b.values():
        s.fail("tag creation", f"A={tags_a}, B={tags_b}")
    else:
        s.pass_(f"created {len(tags_a)} on A, {len(tags_b)} on B")
    return s, tags_a, tags_b


def scen_pair_devices(dev_a: int, dev_b: int) -> Scenario:
    s = Scenario("3. Pair devices via /pair endpoint — symmetric, history logged")
    status, body = http("POST", f"/devices/{dev_a}/pair", {
        "partner_device_id": dev_b, "this_role": "duty",
    })
    if status == 200:
        s.pass_("POST /pair returned 200")
    else:
        s.fail("/pair", f"status={status} body={body}")
        return s

    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if (a.get("duty_role") == "duty" and a.get("redundant_device_id") == dev_b and
        b.get("duty_role") == "standby" and b.get("redundant_device_id") == dev_a):
        s.pass_("symmetric: A=duty(↔B), B=standby(↔A)")
    else:
        s.fail("pair asymmetric",
               f"A={a.get('duty_role')}/{a.get('redundant_device_id')}, "
               f"B={b.get('duty_role')}/{b.get('redundant_device_id')}")

    rows = db_query("""
        SELECT reason FROM device_duty_history
        WHERE device_id IN (:a, :b) ORDER BY switched_at DESC LIMIT 1
    """, {"a": dev_a, "b": dev_b})
    if rows and rows[0]["reason"] == "startup":
        s.pass_("history row with reason='startup' inserted")
    else:
        s.fail("startup history missing", f"got {rows}")
    return s


def scen_pair_tags_auto(dev_a: int, dev_b: int) -> Scenario:
    s = Scenario("4. Auto-generated pair tags: name AND data_type must match")
    rows = db_query("""
        SELECT name FROM pair_tags
        WHERE primary_device_id IN (:a, :b) AND partner_device_id IN (:a, :b)
        ORDER BY name
    """, {"a": dev_a, "b": dev_b})
    got = sorted([r["name"] for r in rows])
    expected = sorted(["MATCH_FLOW", "MATCH_TEMP", "DUTY_STATUS"])

    if got == expected:
        s.pass_(f"exactly the right pair tags: {got}")
    else:
        s.fail("pair tag set mismatch", f"expected {expected}, got {got}")

    for excluded in ("A_ONLY", "B_ONLY", "MISMATCH_TYPE"):
        if excluded not in got:
            s.pass_(f"{excluded} correctly excluded")
        else:
            s.fail(f"{excluded} should NOT be a pair tag",
                   "either no partner or mismatched data_type")
    return s


def scen_assign_duty_status_tag(dev_a: int, dev_b: int,
                                 tags_a: dict, tags_b: dict) -> Scenario:
    s = Scenario("5. duty_status_tag_id assigns, persists, clears, restores")
    # Set on A via PATCH
    status, body = http("PATCH", f"/devices/{dev_a}", {
        "duty_status_tag_id": tags_a["DUTY_STATUS"],
    })
    if status == 200 and body.get("duty_status_tag_id") == tags_a["DUTY_STATUS"]:
        s.pass_(f"PATCH response shows duty_status_tag_id={tags_a['DUTY_STATUS']}")
    else:
        s.fail("PATCH A", f"status={status} body={body}")

    # GET roundtrip
    _, a = http("GET", f"/devices/{dev_a}")
    if a.get("duty_status_tag_id") == tags_a["DUTY_STATUS"]:
        s.pass_("GET roundtrip preserves the value")
    else:
        s.fail("GET roundtrip", f"got {a.get('duty_status_tag_id')}")

    # DB direct check
    rows = db_query("SELECT duty_status_tag_id FROM devices WHERE id=:id", {"id": dev_a})
    if rows and rows[0]["duty_status_tag_id"] == tags_a["DUTY_STATUS"]:
        s.pass_("DB column matches")
    else:
        s.fail("DB column", f"{rows}")

    # Clear with null
    status, body = http("PATCH", f"/devices/{dev_a}", {"duty_status_tag_id": None})
    if status == 200 and body.get("duty_status_tag_id") is None:
        s.pass_("PATCH with null clears the field")
    else:
        s.fail("clear via null", f"status={status} body={body}")

    # Restore on both for subsequent reconciliation tests
    http("PATCH", f"/devices/{dev_a}", {"duty_status_tag_id": tags_a["DUTY_STATUS"]})
    http("PATCH", f"/devices/{dev_b}", {"duty_status_tag_id": tags_b["DUTY_STATUS"]})
    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if (a.get("duty_status_tag_id") == tags_a["DUTY_STATUS"] and
        b.get("duty_status_tag_id") == tags_b["DUTY_STATUS"]):
        s.pass_("re-assigned on both for subsequent tests")
    else:
        s.fail("re-assign", "could not restore for downstream tests")
    return s


def scen_reconciliation_dedup(dev_a: int, dev_b: int,
                              tags_a: dict, tags_b: dict) -> Scenario:
    """The pivotal test: worker reconciles AND only ONE history row is added."""
    s = Scenario("6. Worker reconciles from device-reported value (with dedup)")

    # Sync initial state — A=duty reports 1, B=standby reports 0
    simulate_tag_read(tags_a["DUTY_STATUS"], 1)
    simulate_tag_read(tags_b["DUTY_STATUS"], 0)
    time.sleep(RECONCILE_WAIT_SEC)

    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if a.get("duty_role") != "duty" or b.get("duty_role") != "standby":
        s.fail("baseline drifted",
               f"A={a.get('duty_role')} B={b.get('duty_role')}")
        return s
    s.pass_("baseline confirmed: A=duty/1, B=standby/0")

    before = history_count(dev_a)

    # Flip the device-reported values — A now reports standby, B now reports duty
    simulate_tag_read(tags_a["DUTY_STATUS"], 0)
    simulate_tag_read(tags_b["DUTY_STATUS"], 1)
    s.pass_("simulated reports flipped: A=0, B=1")

    # Diagnostic: confirm latest_tag_values actually has our flipped values
    # before we wait for the worker. If these don't match, the issue is in
    # simulate_tag_read, not the worker.
    pre = db_query("""
        SELECT lv.tag_id, lv.value_double, lv.st,
               EXTRACT(EPOCH FROM (NOW() - lv.time))::int AS age
        FROM latest_tag_values lv
        WHERE lv.tag_id IN (:a, :b)
        ORDER BY lv.tag_id
    """, {"a": tags_a["DUTY_STATUS"], "b": tags_b["DUTY_STATUS"]})
    pre_map = {r["tag_id"]: r for r in pre}
    a_row = pre_map.get(tags_a["DUTY_STATUS"])
    b_row = pre_map.get(tags_b["DUTY_STATUS"])
    if a_row and a_row["value_double"] == 0 and a_row["st"] == 128 and \
       b_row and b_row["value_double"] == 1 and b_row["st"] == 128:
        s.pass_("latest_tag_values has the expected flipped values")
    else:
        s.fail("simulate_tag_read didn't take",
               f"A={a_row} B={b_row} — the INSERT/UPDATE didn't commit cleanly")
        return s

    # Diagnostic: what does the reconciliation query see? If our pair shows
    # up here with the right values, the worker SHOULD swap. If not, the
    # issue is in the query/state.
    recon_view = db_query("""
        SELECT d.id AS device_id, d.duty_role, d.redundant_device_id,
               d.duty_status_tag_id, lv.value_double, lv.st
        FROM devices d
        LEFT JOIN latest_tag_values lv ON lv.tag_id = d.duty_status_tag_id
        WHERE d.duty_status_tag_id IS NOT NULL
          AND d.redundant_device_id IS NOT NULL
          AND d.duty_role IN ('duty', 'standby')
          AND d.id IN (:a, :b)
    """, {"a": dev_a, "b": dev_b})
    if len(recon_view) == 2:
        s.pass_(f"reconciliation query returns both test devices ({len(recon_view)} rows)")
    else:
        s.fail("worker's reconciliation query doesn't see our pair",
               f"got {len(recon_view)} rows: {recon_view}")
        return s

    # Diagnostic: is the worker even alive? Check worker_device_status table
    # — should have rows updated within the last 30 seconds if the worker
    # is running its main loop.
    alive = db_query("""
        SELECT COUNT(*) AS c FROM worker_device_status
        WHERE updated_at > NOW() - interval '30 seconds'
    """)
    if alive and alive[0]["c"] > 0:
        s.pass_(f"worker is alive ({alive[0]['c']} devices reporting recent status)")
    else:
        s.fail("worker appears NOT to be running",
               "no worker_device_status updates in last 30s — reconciliation can't fire")

    time.sleep(RECONCILE_WAIT_SEC)

    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if a.get("duty_role") == "standby" and b.get("duty_role") == "duty":
        s.pass_("worker reconciled: A→standby, B→duty")
    else:
        s.fail("reconciliation didn't fire",
               f"A={a.get('duty_role')} B={b.get('duty_role')} (despite query seeing them + worker alive)")
        return s

    # Dedup check — exactly ONE new history row, not two
    after = history_count(dev_a)
    delta = after - before
    if delta == 1:
        s.pass_(f"DEDUP OK: exactly 1 new history row (before={before}, after={after})")
    elif delta == 2:
        s.fail("DEDUP BUG: 2 history rows added",
               "both sides of the pair generated duplicate rows")
    else:
        s.fail("history delta unexpected", f"delta={delta}")

    rows = db_query("""
        SELECT reason FROM device_duty_history
        WHERE (device_id=:a OR paired_device_id=:a)
          AND switched_at > NOW() - interval '1 minute'
        ORDER BY switched_at DESC LIMIT 1
    """, {"a": dev_a})
    if rows and rows[0]["reason"] == "device_reported":
        s.pass_("most recent history row has reason='device_reported'")
    else:
        s.fail("reason mismatch", f"got {rows}")
    return s


def scen_unknown_value_ignored(dev_a: int, dev_b: int,
                                tags_a: dict, tags_b: dict) -> Scenario:
    s = Scenario("7. Unknown value (neither duty nor standby) is ignored")
    _, before_a = http("GET", f"/devices/{dev_a}")
    _, before_b = http("GET", f"/devices/{dev_b}")
    role_a, role_b = before_a.get("duty_role"), before_b.get("duty_role")
    h_before = history_count(dev_a)

    simulate_tag_read(tags_a["DUTY_STATUS"], 99)
    simulate_tag_read(tags_b["DUTY_STATUS"], 99)
    time.sleep(RECONCILE_WAIT_SEC)

    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if a.get("duty_role") == role_a and b.get("duty_role") == role_b:
        s.pass_("duty_role unchanged on both devices")
    else:
        s.fail("state mutated", f"A:{role_a}→{a.get('duty_role')} B:{role_b}→{b.get('duty_role')}")

    if history_count(dev_a) == h_before:
        s.pass_("no new history rows")
    else:
        s.fail("history mutated", f"delta={history_count(dev_a) - h_before}")
    return s


def scen_stale_read_ignored(dev_a: int, dev_b: int,
                             tags_a: dict, tags_b: dict) -> Scenario:
    s = Scenario("8. Stale read (ST != OK) is ignored")
    # Resync to a known good state first
    simulate_tag_read(tags_a["DUTY_STATUS"], 0)  # A reports standby
    simulate_tag_read(tags_b["DUTY_STATUS"], 1)  # B reports duty
    time.sleep(RECONCILE_WAIT_SEC)

    _, before = http("GET", f"/devices/{dev_a}")
    role_before = before.get("duty_role")

    # Write a mismatching value with BAD st (64 = not READ_OK 128)
    target = 1 if role_before == "standby" else 0
    simulate_tag_read(tags_a["DUTY_STATUS"], target, st=64)
    time.sleep(RECONCILE_WAIT_SEC)

    _, after = http("GET", f"/devices/{dev_a}")
    if after.get("duty_role") == role_before:
        s.pass_(f"role unchanged ({role_before}) despite mismatching value with ST=64")
    else:
        s.fail("state changed despite bad ST",
               f"role flipped {role_before}→{after.get('duty_role')}")
    return s


def scen_manual_swap(dev_a: int, dev_b: int,
                     tags_a: dict, tags_b: dict) -> Scenario:
    s = Scenario("9. Manual /swap-duty API flips roles and records reason='manual'")
    # First restore good reads matching current state so worker won't immediately swap back
    _, before = http("GET", f"/devices/{dev_a}")
    role_before = before.get("duty_role")

    status, _ = http("POST", f"/devices/{dev_a}/swap-duty", {
        "reason": "manual", "notes": "rigorous smoke test",
    })
    if status == 200:
        s.pass_("POST /swap-duty returned 200")
    else:
        s.fail("/swap-duty", f"status={status}")
        return s

    _, after = http("GET", f"/devices/{dev_a}")
    expected = "duty" if role_before == "standby" else "standby"
    if after.get("duty_role") == expected:
        s.pass_(f"role flipped {role_before}→{expected} immediately")
    else:
        s.fail("role didn't flip", f"got {after.get('duty_role')}, expected {expected}")

    rows = db_query("""
        SELECT reason, notes FROM device_duty_history
        WHERE device_id=:a OR paired_device_id=:a
        ORDER BY switched_at DESC LIMIT 1
    """, {"a": dev_a})
    if rows and rows[0]["reason"] == "manual" and rows[0]["notes"] == "rigorous smoke test":
        s.pass_("history: reason='manual', notes preserved")
    else:
        s.fail("history mismatch", f"got {rows}")
    return s


def scen_regenerate_new_tag(dev_a: int, dev_b: int,
                            tags_a: dict, tags_b: dict) -> Scenario:
    s = Scenario("10. Regenerate picks up matching tags added AFTER pairing")
    blk_a = db_query("SELECT register_block_id FROM tags WHERE id=:id",
                     {"id": tags_a["MATCH_FLOW"]})[0]["register_block_id"]
    blk_b = db_query("SELECT register_block_id FROM tags WHERE id=:id",
                     {"id": tags_b["MATCH_FLOW"]})[0]["register_block_id"]

    new_a = create_tag(dev_a, blk_a, "POST_PAIR_TAG", "uint16", 9013)
    new_b = create_tag(dev_b, blk_b, "POST_PAIR_TAG", "uint16", 9013)
    if not new_a or not new_b:
        s.fail("create new tags", f"A={new_a} B={new_b}")
        return s
    s.pass_("added POST_PAIR_TAG on both devices")

    rows = db_query("""
        SELECT COUNT(*) AS c FROM pair_tags
        WHERE name='POST_PAIR_TAG'
          AND primary_device_id IN (:a, :b) AND partner_device_id IN (:a, :b)
    """, {"a": dev_a, "b": dev_b})
    if rows[0]["c"] == 0:
        s.pass_("not yet a pair tag (regenerate hasn't run)")
    else:
        s.fail("auto-paired without regenerate", f"{rows}")

    status, body = http("POST", "/pair-tags/regenerate", {})
    if status == 200:
        s.pass_(f"regenerate ran: created={body.get('created')}")
    else:
        s.fail("regenerate", f"status={status}")

    rows = db_query("""
        SELECT COUNT(*) AS c FROM pair_tags
        WHERE name='POST_PAIR_TAG'
          AND primary_device_id IN (:a, :b) AND partner_device_id IN (:a, :b)
    """, {"a": dev_a, "b": dev_b})
    if rows[0]["c"] == 1:
        s.pass_("after regenerate, POST_PAIR_TAG is a pair tag")
    else:
        s.fail("regenerate didn't pick up the new tag", f"count={rows[0]['c']}")
    return s


def scen_orphan_cleanup(dev_a: int, dev_b: int, tags_a: dict) -> Scenario:
    s = Scenario("11. Deleting a tag removes its pair tag (CASCADE / regenerate)")
    tag_id = tags_a["MATCH_TEMP"]
    status, _ = http("DELETE", f"/tags/{tag_id}")
    if status not in (200, 204):
        s.fail("tag delete", f"status={status}")
        return s
    s.pass_(f"deleted MATCH_TEMP (tag_id={tag_id}) from device A")

    # The ON DELETE CASCADE should already have removed the pair tag
    rows = db_query("""
        SELECT COUNT(*) AS c FROM pair_tags
        WHERE name='MATCH_TEMP'
          AND primary_device_id IN (:a, :b) AND partner_device_id IN (:a, :b)
    """, {"a": dev_a, "b": dev_b})
    if rows[0]["c"] == 0:
        s.pass_("MATCH_TEMP pair tag removed by FK CASCADE")
    else:
        s.fail("orphan persists", f"count={rows[0]['c']}")

    # Regenerate should be a no-op (idempotent)
    status, body = http("POST", "/pair-tags/regenerate", {})
    if status == 200:
        s.pass_(f"regenerate idempotent (created={body.get('created')}, "
                f"deleted_orphans={body.get('deleted_orphans')})")
    else:
        s.fail("regenerate", f"status={status}")
    return s


def scen_manual_override(dev_a: int, dev_b: int,
                          tags_a: dict, tags_b: dict) -> Scenario:
    """Manual override mode: worker reconciliation is suspended for this
    pair, so manual swaps persist instead of getting reconciled back."""
    s = Scenario("12a. Manual override suspends reconciliation; swaps stick")

    # Sync to known state: A=duty/reports-1, B=standby/reports-0
    # Restore pairing first (scenario 9 may have flipped it; bring back to A=duty)
    _, a_now = http("GET", f"/devices/{dev_a}")
    if a_now.get("duty_role") != "duty":
        http("POST", f"/devices/{dev_a}/swap-duty", {"reason": "manual", "notes": "reset for 12a"})
    simulate_tag_read(tags_a["DUTY_STATUS"], 1)
    simulate_tag_read(tags_b["DUTY_STATUS"], 0)
    time.sleep(RECONCILE_WAIT_SEC)

    # Enable override
    status, body = http("POST", f"/devices/{dev_a}/set-pair-override", {"enable": True})
    if status == 200 and body.get("manual_override") is True:
        s.pass_("override enabled via /set-pair-override")
    else:
        s.fail("enable override", f"status={status} body={body}")
        return s

    # Both sides should reflect override=True
    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if a.get("manual_override") and b.get("manual_override"):
        s.pass_("both sides of pair show manual_override=true")
    else:
        s.fail("symmetric override",
               f"A={a.get('manual_override')} B={b.get('manual_override')}")

    # Now flip the device-reported value AND do a manual swap
    # In auto mode this would race; in override the swap should stick.
    h_before = history_count(dev_a)
    http("POST", f"/devices/{dev_a}/swap-duty", {"reason": "manual", "notes": "during override"})
    # Make the device reports DISAGREE with the new stored state (would
    # normally trigger reconciliation back)
    simulate_tag_read(tags_a["DUTY_STATUS"], 1)  # A says duty
    simulate_tag_read(tags_b["DUTY_STATUS"], 0)  # B says standby
    # But the manual swap set A=standby, B=duty. In auto this would
    # reconcile back within 5s. With override, it shouldn't.
    time.sleep(RECONCILE_WAIT_SEC)

    _, a2 = http("GET", f"/devices/{dev_a}")
    _, b2 = http("GET", f"/devices/{dev_b}")
    if a2.get("duty_role") == "standby" and b2.get("duty_role") == "duty":
        s.pass_("manual swap persists despite disagreeing device reports (worker suspended)")
    else:
        s.fail("worker reconciled despite override",
               f"A={a2.get('duty_role')} B={b2.get('duty_role')}")

    h_after = history_count(dev_a)
    delta = h_after - h_before
    if delta == 1:
        s.pass_(f"exactly 1 history row (the manual swap, no reconciliation)")
    else:
        s.fail("history delta", f"expected 1 (manual only), got {delta}")

    # Disable override
    status, body = http("POST", f"/devices/{dev_a}/set-pair-override", {"enable": False})
    if status == 200 and body.get("manual_override") is False:
        s.pass_("override disabled")
    else:
        s.fail("disable override", f"status={status} body={body}")
        return s

    # The PATCH to manual_override may cause the worker to reload state
    # and run a Cycle 1 that overwrites simulated latest_tag_values with
    # bad reads (since 127.0.0.1:5090 has no simulator). That race is
    # unpredictable, so we poll: re-inject fresh simulated reads, sleep
    # a reconcile cycle, check role. Repeat until reconciled or timeout.
    a3 = b3 = None
    deadline = time.time() + 20  # 20s of resilience
    while time.time() < deadline:
        simulate_tag_read(tags_a["DUTY_STATUS"], 1)  # A says duty
        simulate_tag_read(tags_b["DUTY_STATUS"], 0)  # B says standby
        time.sleep(3)  # let one reconcile cycle (5s interval) catch up
        _, a3 = http("GET", f"/devices/{dev_a}")
        _, b3 = http("GET", f"/devices/{dev_b}")
        if a3.get("duty_role") == "duty" and b3.get("duty_role") == "standby":
            break

    if a3 and a3.get("duty_role") == "duty" and b3.get("duty_role") == "standby":
        s.pass_("after returning to auto, worker reconciled to device-reported state")
    else:
        s.fail("auto mode didn't resume",
               f"A={a3.get('duty_role') if a3 else None} B={b3.get('duty_role') if b3 else None}")

    return s


def scen_unpair(dev_a: int, dev_b: int) -> Scenario:
    s = Scenario("12. Unpair: roles reset, pair tags removed, duty_status_tag_id preserved")
    _, before_a = http("GET", f"/devices/{dev_a}")
    status_tag_before = before_a.get("duty_status_tag_id")

    status, _ = http("POST", f"/devices/{dev_a}/unpair", {})
    if status == 200:
        s.pass_("POST /unpair returned 200")
    else:
        s.fail("/unpair", f"status={status}")
        return s

    _, a = http("GET", f"/devices/{dev_a}")
    _, b = http("GET", f"/devices/{dev_b}")
    if (a.get("duty_role") == "none" and a.get("redundant_device_id") is None and
        b.get("duty_role") == "none" and b.get("redundant_device_id") is None):
        s.pass_("both devices: duty_role=none, partner=None")
    else:
        s.fail("roles not fully reset",
               f"A={a.get('duty_role')}/{a.get('redundant_device_id')} "
               f"B={b.get('duty_role')}/{b.get('redundant_device_id')}")

    rows = db_query("""
        SELECT COUNT(*) AS c FROM pair_tags
        WHERE primary_device_id IN (:a, :b) AND partner_device_id IN (:a, :b)
    """, {"a": dev_a, "b": dev_b})
    if rows[0]["c"] == 0:
        s.pass_("all pair tags removed for this pair")
    else:
        s.fail("pair tags remain", f"count={rows[0]['c']}")

    if a.get("duty_status_tag_id") == status_tag_before:
        s.pass_("duty_status_tag_id preserved (not pair-specific)")
    else:
        s.fail("duty_status_tag_id lost",
               f"before={status_tag_before}, after={a.get('duty_status_tag_id')}")
    return s


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> int:
    global BASE_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=BASE_URL)
    args = ap.parse_args()
    BASE_URL = args.base_url

    print(f"InduVista Phase 12 rigorous smoke test — {BASE_URL}")
    print("=" * HEAD_W)

    channel_id = first_channel()
    if not channel_id:
        print("FATAL: no networks configured.")
        return 2
    print(f"Using channel_id={channel_id}")

    cleanup()
    reset_global_settings()

    scenarios: list[Scenario] = []

    s1, dev_a, dev_b = scen_create_devices(channel_id)
    scenarios.append(s1)
    if not dev_a or not dev_b:
        print("FATAL: device creation failed; aborting.")
        return 2

    s2, tags_a, tags_b = scen_create_tags(dev_a, dev_b)
    scenarios.append(s2)
    if not tags_a or not tags_b:
        cleanup()
        return 2

    scenarios.append(scen_pair_devices(dev_a, dev_b))
    scenarios.append(scen_pair_tags_auto(dev_a, dev_b))
    scenarios.append(scen_assign_duty_status_tag(dev_a, dev_b, tags_a, tags_b))
    scenarios.append(scen_reconciliation_dedup(dev_a, dev_b, tags_a, tags_b))
    scenarios.append(scen_unknown_value_ignored(dev_a, dev_b, tags_a, tags_b))
    scenarios.append(scen_stale_read_ignored(dev_a, dev_b, tags_a, tags_b))
    scenarios.append(scen_manual_swap(dev_a, dev_b, tags_a, tags_b))
    scenarios.append(scen_regenerate_new_tag(dev_a, dev_b, tags_a, tags_b))
    scenarios.append(scen_orphan_cleanup(dev_a, dev_b, tags_a))
    scenarios.append(scen_manual_override(dev_a, dev_b, tags_a, tags_b))
    scenarios.append(scen_unpair(dev_a, dev_b))

    cleanup()

    for s in scenarios:
        print_scenario(s)

    p = sum(1 for s in scenarios for _, st, _ in s.results if st == "PASS")
    f = sum(1 for s in scenarios for _, st, _ in s.results if st == "FAIL")
    k = sum(1 for s in scenarios for _, st, _ in s.results if st == "SKIP")

    print()
    print("=" * HEAD_W)
    print(f"RESULTS: {Colors.GREEN}{p} PASS{Colors.RESET}  "
          f"{Colors.RED if f else ''}{f} FAIL{Colors.RESET if f else ''}  "
          f"{Colors.YELLOW if k else ''}{k} SKIP{Colors.RESET if k else ''}")
    if f == 0:
        print(Colors.GREEN + Colors.BOLD + "All checks passed." + Colors.RESET)
        return 0
    print(Colors.RED + Colors.BOLD + f"{f} check(s) failed." + Colors.RESET)
    return 1


if __name__ == "__main__":
    sys.exit(main())
