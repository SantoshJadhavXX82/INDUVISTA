#!/usr/bin/env python3
"""
Report-Studio DATA-LAYER master smoke (fully automated, restart-safe).

Exercises, end to end, against the live API:
  GROUP A  Auto-bind          - block-referenced tags are added to the Data tab
                                on save; bogus ids are filtered out.
  GROUP B  Live resolution    - preview resolves those tags (no tag_<id> fallback),
                                with NO explicit tag_ids passed.
  GROUP C  RS-Lineage         - every value has a provenance tooltip carrying
                                Source / Value / Quality / Captured / Origin;
                                computed/Station values add a Derivation line.
  GROUP D  RS-Fmt2 quality    - a missing value gets a color+symbol marker; the
                                quality CSS ships.
  GROUP E  Off-toggle         - disabling lineage+quality removes every wrapper
                                span while values still print.

It builds ONE throwaway report referencing a polled tag, a computed/Station tag,
and a deliberately-missing tag; runs all checks; prints the real tooltips for
visibility; then deletes the report. Non-invasive (never stops simulators).

It WAITS for the backend to become ready (up to 60s), so it is safe to run
immediately after `docker restart svj_backend`.

Usage (PowerShell):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\report_studio_data_smoke.py
Env: SMOKE_BASE (default http://localhost:8000), SMOKE_USER (default admin).
"""
import json, os, re, sys, time, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
NAME = "ZZ Studio Data Smoke (auto)"
BOGUS = 999999999  # never a real tag -> missing


def _req(method, path, token=None, body=None, raw=False, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        p = resp.read()
        return (resp.status, p.decode("utf-8", "replace")) if raw \
            else (resp.status, (json.loads(p) if p else None))


def _try(method, path, **kw):
    try:
        return _req(method, path, **kw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def wait_ready(max_seconds=60):
    print(f"Waiting for backend at {BASE} (up to {max_seconds}s)...")
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            st, body = _req("GET", "/health", timeout=4)
            if st == 200:
                j = json.loads(body) if isinstance(body, str) else body
                print(f"  ready: migration={j.get('migration_version')} "
                      f"uptime={j.get('uptime_sec')}s started={j.get('started_at')}")
                return
        except Exception:
            pass
        time.sleep(2)
        print("  ...not ready yet")
    sys.exit("backend did not become ready in time.")


def login():
    st, body = _try("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200:
        sys.exit(f"login failed (HTTP {st}): {str(body)[:200]}")
    return body["access_token"]


def _tags(token, dev_id):
    st, t = _try("GET", f"/api/tags?device_id={dev_id}&limit=1000", token=token)
    return t if st == 200 and t else []


def discover(token):
    st, devs = _try("GET", "/api/devices", token=token)
    if st != 200:
        sys.exit(f"GET /devices HTTP {st}")
    polled = computed = None
    for d in sorted(devs, key=lambda d: d.get("name", "")):
        tags = _tags(token, d["id"])
        if not tags:
            continue
        is_comp = (d.get("protocol") == "computed" or "STATION" in (d.get("name") or "").upper())
        if is_comp and computed is None:
            computed = (d["name"], tags[0]["id"], tags[0]["name"])
        if not is_comp and polled is None:
            polled = (d["name"], tags[0]["id"], tags[0]["name"])
    if polled is None and computed is not None:
        polled = computed
    if polled is None:
        sys.exit("no usable tags found")
    return polled, computed


def make_blocks(rows):
    return [{"id": "s1", "type": "stream_table",
             "columns": [{"label": "C", "device_id": None}],
             "sections": [{"name": "DATA", "rows": rows}]}]


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    wait_ready()
    token = login()

    (p_dev, p_id, p_name), comp = discover(token)
    print(f"\nPolled tag:   {p_name} (#{p_id}) on {p_dev}")
    if comp:
        c_dev, c_id, c_name = comp
        print(f"Computed tag: {c_name} (#{c_id}) on {c_dev}")
    else:
        c_id = c_name = None
        print("Computed tag: none found (derivation check will be skipped)")
    print(f"Missing tag:  #{BOGUS} (synthetic)\n")

    rows = [{"label": "polled", "cells": [p_id], "decimals": 2}]
    if c_id is not None:
        rows.append({"label": "computed", "cells": [c_id], "decimals": 2})
    rows.append({"label": "missing", "cells": [BOGUS], "decimals": 2})
    blocks = make_blocks(rows)

    # CREATE without setting tags — block refs alone must auto-bind real tags.
    st, body = _try("POST", "/api/report-config/definitions", token=token,
                    body={"name": NAME, "category": "on_demand", "report_type": "studio_data_smoke",
                          "page_size": "A4", "orientation": "portrait",
                          "template_mode": "blocks", "template_blocks": blocks})
    if st not in (200, 201):
        sys.exit(f"create failed (HTTP {st}): {str(body)[:300]}")
    did = body["id"]

    st, bound = _try("GET", f"/api/report-config/definitions/{did}/tags", token=token)
    bound_ids = [b["tag_id"] for b in (bound or [])]
    print(f"Data tab after save: {bound_ids}")

    # PREVIEW on (lineage+quality default), NO explicit tag_ids.
    _, on = _try("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                 body={"template_mode": "blocks", "template_blocks": blocks,
                       "force_live": True, "page_size": "A4", "orientation": "portrait"})
    st_on = 200 if on and on.startswith("<") or "rpt-" in (on or "") else 0
    # PREVIEW off (disable both).
    off_blocks = [{"id": "rs", "type": "report_style",
                   "lineage": {"enabled": False}, "quality": {"enabled": False}}] + blocks
    _, off = _try("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                  body={"template_mode": "blocks", "template_blocks": off_blocks,
                        "force_live": True, "page_size": "A4", "orientation": "portrait"})

    # appendix ON (report_style block enables the printed Data Lineage table)
    app_blocks = [{"id": "rs", "type": "report_style",
                   "lineage": {"enabled": True, "appendix": True}}] + blocks
    _, app = _try("POST", f"/api/report-config/definitions/{did}/preview", token=token, raw=True,
                  body={"template_mode": "blocks", "template_blocks": app_blocks,
                        "force_live": True, "page_size": "A4", "orientation": "portrait"})

    # Show the real tooltips for visibility.
    prov = [t for t in re.findall(r'title="([^"]*)"', on or "", re.S) if t.startswith("Source:")]
    print("\n--- provenance tooltips rendered (ON) ---")
    for t in prov:
        print("  * " + t.replace("\n", " | "))
    if not prov:
        print("  (none)")

    checks = []  # (group, label, passed)

    # GROUP A — auto-bind
    checks += [
        ("A auto-bind", "create returned an id", isinstance(did, int)),
        ("A auto-bind", "polled tag auto-bound to Data tab", p_id in bound_ids),
        ("A auto-bind", "bogus id NOT bound (filtered as non-tag)", BOGUS not in bound_ids),
    ]
    if c_id is not None:
        checks.append(("A auto-bind", "computed tag auto-bound to Data tab", c_id in bound_ids))

    # GROUP B — live resolution (no fallback)
    checks += [
        ("B resolution", "preview ON returned HTML", bool(on) and "rpt-" in on),
        ("B resolution", "polled value resolved (no tag_<id> fallback)",
         f"Source: tag_{p_id}" not in (on or "") and f"(#{p_id})" in (on or "")),
    ]
    if c_id is not None:
        checks.append(("B resolution", "computed value resolved (no fallback)",
                       f"Source: tag_{c_id}" not in (on or "") and f"(#{c_id})" in (on or "")))

    # GROUP C — lineage tooltip fields
    fields_ok = all(s in (on or "") for s in ("Source:", "Value:", "Quality:", "Captured:", "Origin:"))
    checks += [
        ("C lineage", "values carry a provenance span", 'class="rpt-prov' in (on or "")),
        ("C lineage", "tooltip has all 5 fields (Source/Value/Quality/Captured/Origin)", fields_ok),
    ]
    if c_id is not None:
        checks.append(("C lineage", "computed value shows a Derivation line", "Derivation:" in (on or "")))

    # GROUP D — quality markers
    checks += [
        ("D quality", "missing value shows a quality marker", 'rpt-q-missing"' in (on or "")),
        ("D quality", "quality CSS shipped", ".rpt-q-bad{color:#DC2626" in (on or "")),
    ]

    # GROUP E — off toggle
    checks += [
        ("E off-toggle", "preview OFF returned HTML", bool(off) and "rpt-" in off),
        ("E off-toggle", "no provenance spans when disabled", 'class="rpt-prov' not in (off or "")),
        ("E off-toggle", "no quality spans when disabled", 'class="rpt-q-' not in (off or "")),
    ]

    # GROUP F — side-panel data contract (data-p-* attributes the drawer reads)
    checks += [
        ("F side-panel", "value span carries data-p-name", "data-p-name=" in (on or "")),
        ("F side-panel", "value span carries data-p-id + data-p-origin",
         "data-p-id=" in (on or "") and "data-p-origin=" in (on or "")),
    ]
    if c_id is not None:
        checks.append(("F side-panel", "computed value carries data-p-deriv", "data-p-deriv=" in (on or "")))

    # GROUP G — printed lineage appendix (PDF/print-visible Data Lineage table)
    checks += [
        ("G appendix", "default preview has NO appendix", "rpt-lineage-appendix" not in (on or "")),
        ("G appendix", "appendix ON renders the Data Lineage table",
         'class="rpt-lineage-appendix"' in (app or "") and "Data Lineage" in (app or "")),
        ("G appendix", "appendix lists the polled tag", f"(#{p_id})" in (app or "")),
    ]
    if c_id is not None:
        checks.append(("G appendix", "appendix lists the computed tag", f"(#{c_id})" in (app or "")))

    # Report grouped.
    print("\n========== RESULTS ==========")
    ok = True
    cur = None
    for grp, label, passed in checks:
        if grp != cur:
            print(f"\n[{grp}]")
            cur = grp
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    st_del, _ = _try("DELETE", f"/api/report-config/definitions/{did}", token=token)
    n = sum(1 for *_, p in checks if p)
    print(f"\ncleanup: deleted #{did} (HTTP {st_del})")
    print(f"TOTAL: {n}/{len(checks)} checks passed.")
    print("RESULT:", "ALL GREEN" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
