#!/usr/bin/env python3
"""
Build the STATION_FG computed device — a virtual "station total" stream that
combines FUEL_GAS_FC001 + FUEL_GAS_FC002.

Combination rules (industrial-standard):
  * Extensive quantities (flows + totals) -> SUM_OF (additive).
  * Intensive quantities (velocity, pressure, temperature, densities, CV)
    -> WEIGHTED_AVG flow-weighted: line conditions weighted by gross volume
    flow (UVOL), standard conditions (std density, CV) by standard volume
    flow (CVOL). i.e. station = sum(x_i * Q_i) / sum(Q_i).
  * Stream status is per-meter and not combined (left out of the station).

The station tags reuse the SAME register names as the FCs (STR01_*), so in the
Report stream editor you just add a column -> pick STATION_FG, and the
per-column auto-map fills the measurements automatically.

Idempotent: re-running finds the existing device/tags and skips them.

Usage (PowerShell):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\build_station_fg.py
Env: SMOKE_BASE (default http://localhost:8000), SMOKE_USER (default admin).
"""
import json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
STATION = "STATION_FG"
FC_A, FC_B = "FUEL_GAS_FC001", "FUEL_GAS_FC002"

# Extensive -> SUM_OF
SUM_REGS = [
    "STR01_UVOL_FR_INUSE", "STR01_CVOL_FR_INUSE", "STR01_MASS_FR_INUSE", "STR01_ENERGY_FR_INUSE",
    "STR01_FWD_UVOL_DAILY_TOTAL", "STR01_FWD_CVOL_DAILY_TOTAL", "STR01_FWD_MASS_DAILY_TOTAL", "STR01_FWD_ENERGY_DAILY_TOTAL",
    "STR01_FWD_UVOL_TOTAL", "STR01_FWD_CVOL_TOTAL", "STR01_FWD_MASS_TOTAL", "STR01_FWD_ENERGY_TOTAL",
]
# Intensive -> WEIGHTED_AVG: reg -> weighting-flow reg
WAVG_REGS = {
    "STR01_GAS_VELOCITY_INUSE":   "STR01_UVOL_FR_INUSE",
    "STR01_METER_PRESS(Pf)_INUSE": "STR01_UVOL_FR_INUSE",
    "STR01_METER_TEMP(Tf)_INUSE":  "STR01_UVOL_FR_INUSE",
    "STR01_METER_DENSITY_INUSE":   "STR01_UVOL_FR_INUSE",
    "STR01_BASE_DENS_INUSE":       "STR01_CVOL_FR_INUSE",
    "STR01_REAL_CV_INUSE":         "STR01_CVOL_FR_INUSE",
}


def _req(method, path, token=None, body=None, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            p = resp.read()
            return resp.status, (json.loads(p) if p else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def login():
    st, body = _req("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200:
        sys.exit(f"login failed (HTTP {st}): {str(body)[:200]}")
    return body["access_token"]


def device_id(token, name):
    st, devs = _req("GET", "/api/devices", token=token)
    if st != 200:
        sys.exit(f"GET /devices HTTP {st}")
    d = next((x for x in devs if x["name"] == name), None)
    if not d:
        sys.exit(f"device {name} not found")
    return d["id"]


def tag_map(token, dev_id):
    st, tags = _req("GET", f"/api/tags?device_id={dev_id}&limit=1000", token=token)
    if st != 200:
        sys.exit(f"GET /tags HTTP {st}")
    return {t["name"]: {"id": t["id"], "unit": t.get("unit_code") or t.get("engineering_unit") or ""} for t in tags}


def ensure_station(token):
    st, devs = _req("GET", "/api/computed-devices", token=token)
    if st == 200:
        ex = next((x for x in devs if x["name"] == STATION), None)
        if ex:
            return ex["id"]
    st, body = _req("POST", "/api/computed-devices", token=token,
                    body={"name": STATION, "description": "Fuel-gas station total (FC001 + FC002)",
                          "enabled": True, "scan_interval_ms": 1000})
    if st not in (200, 201):
        sys.exit(f"create computed device failed (HTTP {st}): {str(body)[:300]}")
    return body["id"]


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    token = login()
    a_id, b_id = device_id(token, FC_A), device_id(token, FC_B)
    a, b = tag_map(token, a_id), tag_map(token, b_id)
    station_id = ensure_station(token)
    existing = tag_map(token, station_id)
    print(f"STATION_FG device #{station_id}; {len(existing)} tags already present")

    created, skipped, failed = 0, 0, 0

    def make(reg, block_type, block_config, unit):
        nonlocal created, skipped, failed
        if reg in existing:
            skipped += 1
            return
        payload = {"device_id": station_id, "name": reg, "data_type": "float64",
                   "engineering_unit": unit or None, "block_type": block_type,
                   "block_config": block_config, "execution_rate_ms": 1000, "enabled": True}
        st, body = _req("POST", "/api/computed-tags", token=token, body=payload)
        if st in (200, 201):
            created += 1
        else:
            failed += 1
            print(f"  FAIL {reg} ({block_type}): HTTP {st} {str(body)[:160]}")

    for reg in SUM_REGS:
        if reg in a and reg in b:
            make(reg, "SUM_OF", {"inputs": [a[reg]["id"], b[reg]["id"]]}, a[reg]["unit"])
        else:
            print(f"  skip {reg}: missing on a FC")

    for reg, wreg in WAVG_REGS.items():
        if reg in a and reg in b and wreg in a and wreg in b:
            make(reg, "WEIGHTED_AVG",
                 {"inputs": [a[reg]["id"], b[reg]["id"]],
                  "weights": [{"tag": a[wreg]["id"]}, {"tag": b[wreg]["id"]}]},
                 a[reg]["unit"])
        else:
            print(f"  skip {reg}: missing reg/weight on a FC")

    print(f"\nDone: {created} created, {skipped} already existed, {failed} failed.")
    print("Next: in a report's stream table, add a column -> device STATION_FG; "
          "the per-column measurements auto-map by name.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
