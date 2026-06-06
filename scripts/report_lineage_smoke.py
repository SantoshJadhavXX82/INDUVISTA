#!/usr/bin/env python3
"""
RS-Lineage v1 smoke test (host-run, against the live API).

Proves that:
  * every report value carries a provenance hover tooltip (class="rpt-prov"),
  * the tooltip contains the audit fields (Source / Origin),
  * disabling lineage (+ quality) removes ALL value-wrapping spans,
  * the lineage CSS ships in the output.

Builds a throwaway report ("ZZ Lineage Smoke (auto)"), previews it with
lineage on and off (force_live), checks the HTML, then deletes it.
Non-invasive: never stops simulators or touches real reports.

Usage (PowerShell):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\report_lineage_smoke.py
Env: SMOKE_BASE (default http://localhost:8000), SMOKE_USER (default admin).
"""
import json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
NAME = "ZZ Lineage Smoke (auto)"


def _req(method, path, token=None, body=None, raw=False, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            p = resp.read()
            return (resp.status, p.decode("utf-8", "replace")) if raw \
                else (resp.status, (json.loads(p) if p else None))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def preflight():
    """Fail fast with a clear message instead of hanging if backend is mid-reload."""
    try:
        st, body = _req("GET", "/health", timeout=5)
        if st != 200:
            sys.exit(f"backend /health HTTP {st} — is svj_backend up?")
    except Exception:
        sys.exit("backend not reachable on " + BASE +
                 " — wait for 'Application startup complete' (docker logs svj_backend) then retry.")


def login():
    st, body = _req("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200:
        sys.exit(f"login failed (HTTP {st}): {str(body)[:200]}")
    return body["access_token"]


def pick_device_and_tag(token):
    st, devs = _req("GET", "/api/devices", token=token)
    if st != 200:
        sys.exit(f"GET /devices HTTP {st}")
    order = sorted(devs, key=lambda d: (d.get("name") != "FUEL_GAS_FC001", d.get("name", "")))
    for d in order:
        st, tags = _req("GET", f"/api/tags?device_id={d['id']}&limit=1000", token=token)
        if st != 200 or not tags:
            continue
        good = next((t for t in tags if "FR_INUSE" in t["name"]), tags[0])
        return d["id"], d["name"], good["id"], good["name"]
    sys.exit("no device with tags found")


def pick_computed_tag(token):
    """Find a computed/Station tag (carries a derivation), or None if none exist."""
    st, devs = _req("GET", "/api/devices", token=token)
    if st != 200:
        return None
    comp = [d for d in devs if (d.get("protocol") == "computed"
                                or "STATION" in (d.get("name") or "").upper())]
    for d in comp:
        st, tags = _req("GET", f"/api/tags?device_id={d['id']}&limit=1000", token=token)
        if st == 200 and tags:
            return d["name"], tags[0]["id"], tags[0]["name"]
    return None


def stream_block(fc_id, good_tag, comp_tag=None):
    rows = [{"label": "flow", "cells": [good_tag], "decimals": 2}]
    if comp_tag is not None:
        rows.append({"label": "computed", "cells": [comp_tag], "decimals": 2})
    return {"id": "s1", "type": "stream_table",
            "columns": [{"label": "FC A", "device_id": fc_id}],
            "sections": [{"name": "DATA", "rows": rows}]}


def find_or_create(token, blocks):
    st, defs = _req("GET", "/api/report-config/definitions", token=token)
    if st != 200:
        sys.exit(f"GET /definitions HTTP {st}")
    ex = next((d for d in defs if d.get("name") == NAME), None)
    if ex:
        return ex["id"]
    st, body = _req("POST", "/api/report-config/definitions", token=token,
                    body={"name": NAME, "category": "on_demand", "report_type": "lineage_smoke",
                          "page_size": "A4", "orientation": "portrait",
                          "template_mode": "blocks", "template_blocks": blocks})
    if st not in (200, 201):
        sys.exit(f"create definition failed (HTTP {st}): {str(body)[:300]}")
    return body["id"]


def preview(token, did, blocks):
    return _req("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                body={"template_mode": "blocks", "template_blocks": blocks, "force_live": True,
                      "page_size": "A4", "orientation": "portrait"})


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    preflight()
    token = login()
    fc_id, fc_name, good_tag, good_name = pick_device_and_tag(token)
    print(f"Device: {fc_name} (#{fc_id}) | tag: {good_name} (#{good_tag})")
    comp = pick_computed_tag(token)
    comp_tag = None
    if comp:
        c_dev, comp_tag, c_name = comp
        print(f"Computed: {c_dev} | tag: {c_name} (#{comp_tag})")
    else:
        print("Computed: none found — derivation check will be skipped.")

    sblk = stream_block(fc_id, good_tag, comp_tag)
    did = find_or_create(token, [sblk])

    st_on, on = preview(token, did, [sblk])
    off_blocks = [{"id": "rs", "type": "report_style",
                   "lineage": {"enabled": False}, "quality": {"enabled": False}}, sblk]
    st_off, off = preview(token, did, off_blocks)

    hard = [
        ("preview ON returns 200", st_on == 200),
        ("values carry a provenance span (class=rpt-prov)", 'class="rpt-prov' in on),
        ("tooltip includes Source field", 'title="Source:' in on),
        ("tooltip includes Origin field", "Origin:" in on),
        ("lineage CSS shipped", ".rpt-prov{cursor:help" in on),
        ("preview OFF returns 200", st_off == 200),
        ("disabling lineage + quality removes all value spans",
         ('class="rpt-prov' not in off) and ('class="rpt-q-' not in off)),
    ]
    if comp_tag is not None:
        hard.append(("computed value shows a Derivation line", "Derivation:" in on))

    print("--- HARD CHECKS ---")
    ok = True
    for label, passed in hard:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    st_del, _ = _req("DELETE", f"/api/report-config/definitions/{did}", token=token)
    print(f"cleanup: deleted smoke report #{did} (HTTP {st_del})")
    print(f"{sum(1 for _, p in hard if p)}/{len(hard)} hard checks passed.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
