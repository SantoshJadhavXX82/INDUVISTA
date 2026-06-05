#!/usr/bin/env python3
"""
Report Template Studio — fully automated smoke test.

Exercises the whole Phase-A feature set end-to-end against a running backend:
page size/orientation (A3 landscape), report theme (font + colors), section
heading styling (global + per-section), page header/footer tokens, time basis
(UTC) + localized timestamp, stream_table with per-column cells + banding,
KPI units/decimals, and enum->text status. It creates a throwaway report,
previews it live, asserts on the rendered HTML, then deletes it.

Usage (PowerShell):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\report_studio_smoke.py
Optional env: SMOKE_BASE (default http://localhost:8000), SMOKE_USER (default admin).
Exit code 0 = all hard checks passed, 1 = a hard check failed.
"""
import json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
NAME = "ZZ Studio Smoke (auto)"

def _req(method, path, token=None, body=None, raw=False, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            p = resp.read()
            return resp.status, (p.decode() if raw else (json.loads(p) if p else None))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def login():
    st, body = _req("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200:
        sys.exit(f"login failed (HTTP {st}): {str(body)[:200]}")
    return body["access_token"]

def pick_devices_and_tags(token):
    st, devs = _req("GET", "/api/devices", token=token)
    if st != 200 or not devs:
        sys.exit(f"no devices (HTTP {st})")
    pref = [d for d in devs if str(d["name"]).startswith("FUEL_GAS_FC")]
    chosen = (pref + [d for d in devs if d not in pref])[:2]
    if len(chosen) < 2:
        chosen = (chosen * 2)[:2]
    out = []
    for d in chosen:
        st, tags = _req("GET", f"/api/tags?device_id={d['id']}&limit=1000", token=token)
        tags = tags if st == 200 and isinstance(tags, list) else []
        by = {t["name"]: t["id"] for t in tags}
        status = by.get("STR01_STATUS_VALUE") or next((tid for n, tid in by.items() if "STATUS" in n), None)
        numeric = (by.get("STR01_GAS_VELOCITY_INUSE")
                   or next((tid for n, tid in by.items() if n not in ("STR01_STATUS_VALUE",) and "STATUS" not in n), None))
        out.append({"id": d["id"], "name": d["name"], "status": status, "numeric": numeric})
    return out

def build_blocks(d):
    a, b = d[0], d[1]
    return [
        {"id": "rstyle", "type": "report_style",
         "theme": {"font_family": "Tahoma", "font_size_pt": 10, "text_color": "#111827",
                   "heading_color": "#0B3A67", "section_color": "#FFFFFF", "section_bg": "#0B3A67",
                   "section_caps": True, "table_header_bg": "#0B3A67", "table_header_fg": "#FFFFFF",
                   "alt_row": "#F8FAFC"},
         "time": {"basis": "utc", "format": "%d-%b-%Y %H:%M", "show_suffix": True}},
        {"id": "pghdr", "type": "page_header", "left": "{report_title}", "center": "", "right": "{page_of}"},
        {"id": "pgftr", "type": "page_footer", "left": "Generated {generated_at}", "center": "", "right": "{timezone}"},
        {"id": "ttl", "type": "text", "content": "STUDIO SMOKE",
         "style": {"font": {"size_pt": 14, "weight": "bold"}, "text": {"color": "#0B3A67"}}},
        {"id": "k1", "type": "kpi_row",
         "items": [{"tag_id": a["numeric"], "label": "Velocity", "unit": "m\u00b3/h", "decimals": 2}]},
        {"id": "st1", "type": "stream_table", "band_rows": True,
         "columns": [{"label": "FC A", "device_id": a["id"]}, {"label": "FC B", "device_id": b["id"]}],
         "sections": [{"name": "DATA",
                       "style": {"background": {"type": "solid", "color": "#0B3A67"}, "text": {"color": "#FFFFFF"}},
                       "rows": [
                           {"label": "STREAM STATUS", "unit": "", "decimals": None, "cells": [a["status"], b["status"]]},
                           {"label": "GAS VELOCITY", "unit": "(M/S)", "decimals": 3, "cells": [a["numeric"], b["numeric"]]},
                       ]}]},
    ]

def find_or_create(token, blocks):
    st, defs = _req("GET", "/api/report-config/definitions", token=token)
    existing = next((x for x in (defs or []) if x.get("name") == NAME), None) if st == 200 else None
    payload = {"name": NAME, "category": "on_demand", "report_type": "studio_smoke",
               "page_size": "A3", "orientation": "landscape",
               "template_mode": "blocks", "template_blocks": blocks}
    if existing:
        did = existing["id"]
        _req("PATCH", f"/api/report-config/definitions/{did}", token=token, body=payload)
        return did
    st, body = _req("POST", "/api/report-config/definitions", token=token, body=payload)
    if st not in (200, 201):
        sys.exit(f"create definition failed (HTTP {st}): {str(body)[:300]}")
    return body["id"]

def theme_manager_check(token, did, dev):
    """Set a distinctive global default, verify a report with NO report_style
    block inherits it, then restore the previous default. Returns [(label, ok)]."""
    st, original = _req("GET", "/api/report-config/default-style", token=token)
    original = original if isinstance(original, dict) else {}
    try:
        test = {"theme": {"font_family": "Courier New", "heading_color": "#123456"}}
        st, _ = _req("PUT", "/api/report-config/default-style", token=token, body=test)
        if st != 200:
            return [("default-style PUT (admin)", False)]
        blocks = [
            {"id": "t", "type": "text", "content": "NO STYLE BLOCK"},
            {"id": "st1", "type": "stream_table",
             "columns": [{"label": "FC A", "device_id": dev[0]["id"]}],
             "sections": [{"name": "DATA", "rows": [
                 {"label": "V", "unit": "(M/S)", "decimals": 3, "cells": [dev[0]["numeric"]]}]}]},
        ]
        st, html = _req("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                        body={"template_mode": "blocks", "template_blocks": blocks, "force_live": True})
        ok = (st == 200 and "Courier New" in html and "#123456" in html)
        return [("global theme inherited (no report_style block)", ok)]
    finally:
        _req("PUT", "/api/report-config/default-style", token=token, body=original)


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    token = login()
    dev = pick_devices_and_tags(token)
    print(f"Devices: {dev[0]['name']} (status={dev[0]['status']}, num={dev[0]['numeric']}) | "
          f"{dev[1]['name']} (status={dev[1]['status']}, num={dev[1]['numeric']})")
    blocks = build_blocks(dev)
    ids = sorted({i for d in dev for i in (d["status"], d["numeric"]) if i})
    did = find_or_create(token, blocks)
    _req("PUT", f"/api/report-config/definitions/{did}/tags", token=token, body={"tag_ids": ids})
    st, html = _req("POST", f"/api/report-config/definitions/{did}/preview",
                    token=token, raw=True, timeout=40,
                    body={"template_mode": "blocks", "template_blocks": blocks, "force_live": True,
                          "page_size": "A3", "orientation": "landscape"})
    if st != 200:
        print(f"  preview HTTP {st}: {str(html)[:300]}")
        _cleanup(token, did)
        sys.exit("preview failed")

    # ---- assertions -------------------------------------------------------
    hard = [
        ("page A3 landscape",     "size: A3 landscape" in html),
        ("theme font (Tahoma)",   "Tahoma" in html and "body{font-family:" in html),
        ("section theme band",    ".rpt-stream .rpt-stream-sect{" in html),
        ("per-section style",     'class="rpt-stream-sect" style=' in html),
        ("stream row banding",    "tr.rpt-band>td{background" in html),
        ("kpi unit",              'class="kpi-unit">' in html),
        ("page-number counter",   "counter(page)" in html),
        ("footer generated box",  "@bottom-left { content:" in html),
        ("styled title color",    'class="rpt-text" style="' in html and "#0B3A67" in html),
    ]
    soft = [
        ("time basis UTC suffix", "UTC" in html),
        ("enum status -> text",   ("ONLINE" in html or "OFFLINE" in html)),
    ]
    hard += theme_manager_check(token, did, dev)
    print("\n--- HARD CHECKS ---")
    passed = 0
    for label, ok in hard:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        passed += 1 if ok else 0
    print("--- SOFT CHECKS (informational) ---")
    for label, ok in soft:
        print(f"  [{'ok ' if ok else 'n/a'}] {label}")
    print(f"\n{passed}/{len(hard)} hard checks passed.")
    _cleanup(token, did)
    sys.exit(0 if passed == len(hard) else 1)

def _cleanup(token, did):
    st, _ = _req("DELETE", f"/api/report-config/definitions/{did}", token=token)
    print(f"cleanup: deleted smoke report #{did} (HTTP {st})")

if __name__ == "__main__":
    main()
