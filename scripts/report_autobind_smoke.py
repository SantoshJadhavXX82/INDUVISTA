#!/usr/bin/env python3
"""
Auto-bind smoke: a tag used in a block but not bound in the Data tab should be
auto-added to the report's tags on save, and resolve (not render blank).

Builds a throwaway report whose stream table references a real tag WITHOUT
calling set-tags, then asserts:
  * the tag now appears in the report's Data tab (GET .../tags) — auto-bind on save,
  * the live preview resolves it (tooltip shows the real tag name, not tag_<id>).
Cleans up after itself. Non-invasive.

Usage (PowerShell):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\report_autobind_smoke.py
"""
import json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
NAME = "ZZ Autobind Smoke (auto)"


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
    try:
        st, _ = _req("GET", "/health", timeout=5)
        if st != 200:
            sys.exit(f"backend /health HTTP {st}")
    except Exception:
        sys.exit("backend not reachable on " + BASE + " — wait for startup, then retry.")


def login():
    st, body = _req("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200:
        sys.exit(f"login failed (HTTP {st}): {str(body)[:200]}")
    return body["access_token"]


def pick_tag(token):
    st, devs = _req("GET", "/api/devices", token=token)
    if st != 200:
        sys.exit(f"GET /devices HTTP {st}")
    order = sorted(devs, key=lambda d: (d.get("name") != "STATION_FG", d.get("name", "")))
    for d in order:
        st, tags = _req("GET", f"/api/tags?device_id={d['id']}&limit=1000", token=token)
        if st == 200 and tags:
            return d["name"], tags[0]["id"], tags[0]["name"]
    sys.exit("no device with tags found")


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    preflight()
    token = login()
    dev, tag_id, tag_name = pick_tag(token)
    print(f"Using {dev} | tag: {tag_name} (#{tag_id}) — NOT pre-bound")

    sblk = {"id": "s1", "type": "stream_table",
            "columns": [{"label": "C", "device_id": None}],
            "sections": [{"name": "DATA", "rows": [
                {"label": "v", "cells": [tag_id], "decimals": 2}]}]}

    # create WITHOUT setting tags — the block reference alone should auto-bind it
    st, body = _req("POST", "/api/report-config/definitions", token=token,
                    body={"name": NAME, "category": "on_demand", "report_type": "autobind_smoke",
                          "page_size": "A4", "orientation": "portrait",
                          "template_mode": "blocks", "template_blocks": [sblk]})
    if st not in (200, 201):
        sys.exit(f"create failed (HTTP {st}): {str(body)[:300]}")
    did = body["id"]

    st, bound = _req("GET", f"/api/report-config/definitions/{did}/tags", token=token)
    bound_ids = [b["tag_id"] for b in (bound or [])]
    print(f"Data tab after save: {bound_ids}")

    # preview with the blocks but NO explicit tag_ids — must still resolve
    st_p, html = _req("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                      body={"template_mode": "blocks", "template_blocks": [sblk],
                            "force_live": True, "page_size": "A4", "orientation": "portrait"})

    hard = [
        ("create returned an id", isinstance(did, int)),
        ("referenced tag auto-bound to Data tab on save", tag_id in bound_ids),
        ("preview returns 200", st_p == 200),
        ("value resolves (real tag name in tooltip)", f"Source: {tag_name}" in html),
        ("value is NOT the unresolved fallback", f"Source: tag_{tag_id}" not in html),
    ]
    print("--- HARD CHECKS ---")
    ok = True
    for label, passed in hard:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    st_del, _ = _req("DELETE", f"/api/report-config/definitions/{did}", token=token)
    print(f"cleanup: deleted #{did} (HTTP {st_del})")
    print(f"{sum(1 for _, p in hard if p)}/{len(hard)} hard checks passed.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
