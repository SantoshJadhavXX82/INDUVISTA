#!/usr/bin/env python3
"""
RS-Fmt2 quality-markers smoke test (host-run, against the live API).

Proves, end to end, that:
  * a MISSING value renders with a quality marker (color + symbol span),
  * the quality CSS ships in the output,
  * disabling markers (report_style.quality.enabled = false) removes ALL of them,
  * a good live value passes through un-marked (soft check; live quality varies).

It builds a throwaway report ("ZZ Quality Smoke (auto)"), previews it twice
(force_live), checks the HTML, then deletes the report. Non-invasive: it never
stops simulators or touches real reports.

Usage (PowerShell):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\report_quality_smoke.py
Env: SMOKE_BASE (default http://localhost:8000), SMOKE_USER (default admin).
"""
import json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
NAME = "ZZ Quality Smoke (auto)"
BOGUS_TAG = 999999999   # never a real tag -> resolves to a MISSING TagCtx


def _req(method, path, token=None, body=None, raw=False, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            p = resp.read()
            if raw:
                return resp.status, p.decode("utf-8", "replace")
            return resp.status, (json.loads(p) if p else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


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


def stream_block(fc_id, good_tag):
    return {
        "id": "s1", "type": "stream_table",
        "columns": [{"label": "FC A", "device_id": fc_id}],
        "sections": [{"name": "DATA", "rows": [
            {"label": "good", "cells": [good_tag], "decimals": 2},
            {"label": "missing", "cells": [BOGUS_TAG], "decimals": 2},
        ]}],
    }


def find_or_create(token, blocks):
    st, defs = _req("GET", "/api/report-config/definitions", token=token)
    if st != 200:
        sys.exit(f"GET /definitions HTTP {st}")
    ex = next((d for d in defs if d.get("name") == NAME), None)
    if ex:
        return ex["id"]
    payload = {"name": NAME, "category": "on_demand", "report_type": "quality_smoke",
               "page_size": "A4", "orientation": "portrait",
               "template_mode": "blocks", "template_blocks": blocks}
    st, body = _req("POST", "/api/report-config/definitions", token=token, body=payload)
    if st not in (200, 201):
        sys.exit(f"create definition failed (HTTP {st}): {str(body)[:300]}")
    return body["id"]


def preview(token, did, blocks, tag_ids):
    st, html = _req("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                    body={"template_mode": "blocks", "template_blocks": blocks, "force_live": True,
                          "tag_ids": tag_ids, "page_size": "A4", "orientation": "portrait"})
    return st, html


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    token = login()
    fc_id, fc_name, good_tag, good_name = pick_device_and_tag(token)
    print(f"Device: {fc_name} (#{fc_id}) | good tag: {good_name} (#{good_tag}) | missing tag: #{BOGUS_TAG}")

    sblk = stream_block(fc_id, good_tag)
    did = find_or_create(token, [sblk])
    bind = [good_tag, BOGUS_TAG]
    # Isolate quality: turn lineage OFF so spans aren't prefixed with rpt-prov and
    # this smoke tests the quality markers alone (the master smoke covers both).
    lin_off = {"id": "rs0", "type": "report_style", "lineage": {"enabled": False}}

    # ON (quality markers on, lineage off)
    st_on, on = preview(token, did, [lin_off, sblk], bind)
    # OFF (quality + lineage both off)
    off_blocks = [{"id": "rs", "type": "report_style",
                   "quality": {"enabled": False}, "lineage": {"enabled": False}}, sblk]
    st_off, off = preview(token, did, off_blocks, bind)

    hard = [
        ("preview ON returns 200", st_on == 200),
        ("MISSING value is marked (color + symbol span)", 'class="rpt-q-missing"' in on),
        ("quality CSS shipped in output", ".rpt-q-bad{color:#DC2626" in on),
        ("preview OFF returns 200", st_off == 200),
        ('disabling removes ALL markers', 'class="rpt-q-' not in off),
    ]
    on_spans = on.count('class="rpt-q-')
    soft = [
        (f"good live value passed through un-marked (1 span seen = {on_spans})", on_spans == 1),
    ]

    print("--- HARD CHECKS ---")
    ok = True
    for label, passed in hard:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print("--- SOFT CHECKS (informational; live quality varies) ---")
    for label, passed in soft:
        print(f"  [{'ok ' if passed else 'note'}] {label}")

    # cleanup
    st_del, _ = _req("DELETE", f"/api/report-config/definitions/{did}", token=token)
    print(f"cleanup: deleted smoke report #{did} (HTTP {st_del})")

    n = sum(1 for _, p in hard if p)
    print(f"{n}/{len(hard)} hard checks passed.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
