r"""Fully automated smoke for the device fault policy (Phase 2b API + 2c engine).

End-to-end, no manual steps. It:
  A. round-trips the policy through the API (PATCH -> GET persisted),
  B. checks the policy change is audited,
  C. drives the modbus worker to SUBSTITUTE on read failure and verifies the
     row in latest_tag_values (st=28, st_reason='SUBSTITUTED', value=sentinel),
  D. drives it to HOLD_LAST and verifies (st=72, st_reason='HOLD_LAST', value
     frozen at the last good reading),
  E. renders a live preview and asserts the Substituted / Held markers appear.

It controls Docker (restart worker, stop/start the simulator) and reads the DB
via `docker exec ... psql`, then ALWAYS restores the device's original policy,
restarts the simulator, and bounces the worker in a finally block.

Run on the host (Docker + the app must be up):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\device_fault_policy_smoke.py

Env (all optional except SMOKE_PASS):
  SMOKE_BASE (http://localhost:8000), SMOKE_USER (admin), SMOKE_PASS (required),
  FP_DEVICE_ID (auto: the device whose host is 'modbus_simulator'),
  FP_SIM (svj_modbus_simulator), FP_WORKER (svj_modbus_worker),
  FP_PG (svj_postgres), FP_PG_USER (induvista_admin), FP_PG_DB (induvista),
  FP_WAIT_GOOD (18), FP_POLL_TIMEOUT (50), FP_POLL_EVERY (3).
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
SIM = os.environ.get("FP_SIM", "svj_modbus_simulator")
WORKER = os.environ.get("FP_WORKER", "svj_modbus_worker")
PG = os.environ.get("FP_PG", "svj_postgres")
PG_USER = os.environ.get("FP_PG_USER", "induvista_admin")
PG_DB = os.environ.get("FP_PG_DB", "induvista")
WAIT_GOOD = int(os.environ.get("FP_WAIT_GOOD", "18"))
POLL_TIMEOUT = int(os.environ.get("FP_POLL_TIMEOUT", "50"))
POLL_EVERY = int(os.environ.get("FP_POLL_EVERY", "3"))
SENTINEL = -777.25
NAME = "__fault_policy_smoke__"

results = []  # (label, passed, hard)


def check(label, passed, hard=True):
    results.append((label, bool(passed), hard))
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    return passed


# ---------- HTTP ------------------------------------------------------------
def _req(method, path, token=None, body=None, raw=False, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            txt = resp.read().decode()
            return resp.status, (txt if raw else (json.loads(txt) if txt else None))
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode() if raw else None)


def wait_ready(max_seconds=60):
    print(f"Waiting for backend at {BASE} (up to {max_seconds}s)...")
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            st, body = _req("GET", "/health", timeout=4)
            if st == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def login():
    st, body = _req("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200:
        sys.exit(f"login failed (HTTP {st}): {str(body)[:200]}")
    return body["access_token"]


# ---------- Docker + psql ---------------------------------------------------
def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _psql(sql):
    p = _docker("exec", PG, "psql", "-U", PG_USER, "-d", PG_DB,
                "-t", "-A", "-F", "|", "-c", sql)
    if p.returncode != 0:
        raise RuntimeError(f"psql failed: {p.stderr.strip()}")
    return p.stdout.strip()


def restart_worker():
    _docker("restart", WORKER)
    time.sleep(4)


def poll_until(tag_id, predicate, timeout=POLL_TIMEOUT):
    """Poll latest_tag_values for tag_id until predicate(value, st, reason) or timeout.
    Returns (ok, value, st, reason)."""
    deadline = time.time() + timeout
    last = (None, None, None)
    while time.time() < deadline:
        row = _psql(f"SELECT value_double, st, st_reason FROM latest_tag_values "
                    f"WHERE tag_id={tag_id};")
        if row:
            parts = (row.splitlines()[0]).split("|")
            v = float(parts[0]) if parts[0] not in ("", None) else None
            st = int(parts[1]) if len(parts) > 1 and parts[1] else None
            reason = parts[2] if len(parts) > 2 else None
            last = (v, st, reason)
            if predicate(v, st, reason):
                return (True, v, st, reason)
        time.sleep(POLL_EVERY)
    return (False, *last)


# ---------- target discovery ------------------------------------------------
def pick_device(token):
    if os.environ.get("FP_DEVICE_ID"):
        did = int(os.environ["FP_DEVICE_ID"])
        st, d = _req("GET", f"/api/devices/{did}", token=token)
        if st != 200:
            sys.exit(f"FP_DEVICE_ID {did}: GET HTTP {st}")
        return d
    st, devs = _req("GET", "/api/devices", token=token)
    if st != 200:
        sys.exit(f"GET /devices HTTP {st}")
    # The device fed by the modbus simulator (host matches the sim hostname).
    cand = [d for d in devs if (d.get("host") or "") == "modbus_simulator"]
    if not cand:
        cand = [d for d in devs if d.get("host") and d.get("protocol") not in ("computed", "opc_ua")]
    if not cand:
        sys.exit("no modbus device fed by the simulator found (set FP_DEVICE_ID)")
    return cand[0]


def numeric_tag_on(dev_id):
    row = _psql(f"SELECT id, name FROM tags WHERE device_id={dev_id} "
                f"AND deleted_at IS NULL AND data_type IN ('float32','float64','int16','uint16','int32','uint32') "
                f"ORDER BY id LIMIT 1;")
    if not row:
        sys.exit(f"no numeric tag on device {dev_id}")
    tid, name = row.split("|")
    return int(tid), name


# ---------- report preview (engine -> render) -------------------------------
def find_or_create_def(token, dev_id, tag_id):
    st, defs = _req("GET", "/api/report-config/definitions", token=token)
    ex = next((d for d in defs if d.get("name") == NAME), None) if st == 200 else None
    blocks = [{"id": "s1", "type": "stream_table",
               "columns": [{"label": "DEV", "device_id": dev_id}],
               "sections": [{"name": "DATA", "rows": [
                   {"label": "tag", "cells": [tag_id], "decimals": 2}]}]}]
    if ex:
        return ex["id"], blocks
    st, body = _req("POST", "/api/report-config/definitions", token=token, body={
        "name": NAME, "category": "on_demand", "report_type": "fault_policy_smoke",
        "page_size": "A4", "orientation": "portrait",
        "template_mode": "blocks", "template_blocks": blocks})
    if st not in (200, 201):
        sys.exit(f"create def failed (HTTP {st}): {str(body)[:200]}")
    return body["id"], blocks


def preview_has(token, did, blocks, tag_id, css_class):
    st, html = _req("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                    body={"template_mode": "blocks", "template_blocks": blocks, "force_live": True,
                          "tag_ids": [tag_id], "page_size": "A4", "orientation": "portrait"})
    return st == 200 and (css_class in (html or ""))


# ---------- main ------------------------------------------------------------
def set_policy(token, dev_id, **fields):
    st, body = _req("PATCH", f"/api/devices/{dev_id}", token=token, body=fields)
    return st, body


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    if not wait_ready():
        sys.exit("backend did not become ready in time.")
    token = login()
    dev = pick_device(token)
    dev_id, dev_name = dev["id"], dev["name"]
    tag_id, tag_name = numeric_tag_on(dev_id)
    print(f"Target device: {dev_name} (#{dev_id}) | tag: {tag_name} (#{tag_id}) "
          f"| sim={SIM} worker={WORKER}")
    original = {k: dev.get(k) for k in
                ("fault_mode", "substitute_value", "hold_mode", "max_hold_sec")}
    print(f"Original policy: {original}")
    did, blocks = find_or_create_def(token, dev_id, tag_id)

    try:
        # ---- A. API config round-trip --------------------------------------
        print("\n[A api round-trip]")
        st, _ = set_policy(token, dev_id, fault_mode="substitute", substitute_value=SENTINEL)
        check("PATCH substitute accepted", st == 200)
        st, d = _req("GET", f"/api/devices/{dev_id}", token=token)
        check("GET shows fault_mode=substitute", st == 200 and d.get("fault_mode") == "substitute")
        check("GET shows substitute_value persisted",
              d.get("substitute_value") == SENTINEL)
        st, _ = set_policy(token, dev_id, fault_mode="hold_last", hold_mode="max_age", max_hold_sec=120)
        st, d = _req("GET", f"/api/devices/{dev_id}", token=token)
        check("PATCH hold_last persisted (mode+max_age)",
              d.get("fault_mode") == "hold_last" and d.get("hold_mode") == "max_age"
              and d.get("max_hold_sec") == 120)
        # invalid value rejected by the enum
        st, _ = set_policy(token, dev_id, fault_mode="banana")
        check("invalid fault_mode rejected (422)", st == 422)

        # ---- B. audit ------------------------------------------------------
        print("\n[B audit]")
        st, audit = _req("GET", "/api/audit-log?action=device.&limit=50", token=token)
        events = audit.get("events", []) if (st == 200 and isinstance(audit, dict)) else []
        fault_evt = any("fault_mode" in json.dumps(e.get("details") or e).lower() for e in events)
        check(f"policy change recorded in audit log ({len(events)} device.* events)", fault_evt)

        # ---- C. engine: SUBSTITUTE ----------------------------------------
        print("\n[C engine substitute]")
        _docker("start", SIM); time.sleep(2)
        set_policy(token, dev_id, fault_mode="substitute", substitute_value=SENTINEL)
        restart_worker()
        _docker("stop", SIM)
        ok, v, st_, reason = poll_until(
            tag_id, lambda v, s, r: r == "SUBSTITUTED" and s == 28)
        check(f"worker wrote SUBSTITUTED (st={st_}, reason={reason})",
              ok and st_ == 28 and reason == "SUBSTITUTED")
        check(f"substituted value == sentinel ({v})", v is not None and abs(v - SENTINEL) < 1e-6)
        check("preview renders Substituted marker",
              preview_has(token, did, blocks, tag_id, "rpt-q-substituted"))

        # ---- D. engine: HOLD_LAST -----------------------------------------
        print("\n[D engine hold_last]")
        _docker("start", SIM)
        set_policy(token, dev_id, fault_mode="hold_last", hold_mode="indefinite", max_hold_sec=None)
        restart_worker()
        # wait for a fresh GOOD read (captures last-good in the worker)
        good_ok, good_v, _, _ = poll_until(
            tag_id, lambda v, s, r: v is not None and s is not None and s >= 128,
            timeout=WAIT_GOOD + 20)
        check(f"device read GOOD before failure (value={good_v})", good_ok)
        _docker("stop", SIM)
        ok, v, st_, reason = poll_until(
            tag_id, lambda v, s, r: r == "HOLD_LAST" and s == 72)
        check(f"worker wrote HOLD_LAST (st={st_}, reason={reason})",
              ok and st_ == 72 and reason == "HOLD_LAST")
        check("held value present (frozen, not null)", v is not None)
        check("held value is NOT the substitute sentinel", v is None or abs(v - SENTINEL) > 1e-6)
        check("preview renders Held marker",
              preview_has(token, did, blocks, tag_id, "rpt-q-held"))

    finally:
        # ---- ALWAYS restore -------------------------------------------------
        print("\n[restore] original policy + simulator + worker")
        _docker("start", SIM)
        set_policy(token, dev_id,
                   fault_mode=original.get("fault_mode") or "missing",
                   substitute_value=original.get("substitute_value"),
                   hold_mode=original.get("hold_mode") or "indefinite",
                   max_hold_sec=original.get("max_hold_sec"))
        restart_worker()
        print("  restored.")

    # ---- summary -----------------------------------------------------------
    hard = [p for (_, p, h) in results if h]
    soft = [(l, p) for (l, p, h) in results if not h]
    print("\n========== RESULTS ==========")
    print(f"HARD: {sum(hard)}/{len(hard)} passed")
    for l, p in soft:
        print(f"SOFT [{'ok ' if p else 'fail'}] {l}")
    if all(hard):
        print("RESULT: ALL GREEN")
        sys.exit(0)
    print("RESULT: FAILURES PRESENT")
    sys.exit(1)


if __name__ == "__main__":
    main()
