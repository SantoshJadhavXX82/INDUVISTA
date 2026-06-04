#!/usr/bin/env python3
"""
Phase B1 smoke — report validation engine, against a RUNNING stack.

Throwaway, self-cleaning. Proves:
  * a bare report validates as WARNING (no tags / no destinations), not failed
  * an unresolvable period (shift, not implemented) -> FAILED with a period issue
  * clearing the period returns to WARNING
  * a PDF destination with no template -> FAILED with a layout issue

Stdlib only. Run from the host:
  $env:SMOKE_PASS="..."; python scripts\\report_validation_smoke.py

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error as urlerror
from urllib import request as urlreq

PASS = FAIL = 0


class ApiError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def _h(self, body):
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def req(self, method, path, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        r = urlreq.Request(url, data=data, headers=self._h(data), method=method)
        try:
            with urlreq.urlopen(r, timeout=30) as resp:
                raw = resp.read()
                ct = resp.headers.get("Content-Type", "")
        except urlerror.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace"), url)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8")) if "application/json" in ct else raw

    def login(self, user, pw):
        self.token = self.req("POST", "/api/auth/login",
                              body={"username": user, "password": pw})["access_token"]


def ok(n, d=""):
    global PASS; PASS += 1; print(f"  [PASS] {n}" + (f" — {d}" if d else ""))


def bad(n, d=""):
    global FAIL; FAIL += 1; print(f"  [FAIL] {n}" + (f" — {d}" if d else ""))


def has(res, category, level):
    return any(r["category"] == category and r["level"] == level for r in res.get("results", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("SMOKE_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--user", default=os.getenv("SMOKE_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SMOKE_PASS"))
    args = ap.parse_args()
    if not args.password:
        print("ERROR: no password. Pass --password or set SMOKE_PASS."); return 1

    api = Api(args.base)
    print("Phase B1 smoke — validation engine")
    try:
        api.login(args.user, args.password); ok("login")
    except Exception as e:
        bad("login", str(e)); return 1

    rid = did = None
    base = "/api/report-config"
    try:
        rid = api.req("POST", f"{base}/definitions",
                      body={"name": "B1_VALIDATE_TMP", "category": "on_demand",
                            "template_html": "", "page_size": "A4", "orientation": "portrait"})["id"]
        ok("create throwaway report", f"id={rid}")

        v = api.req("POST", f"{base}/definitions/{rid}/validate")
        (ok if v["overall"] == "warning" else bad)("bare report -> warning", f"overall={v['overall']}")
        (ok if has(v, "data", "warning") else bad)("warns: no tags bound")
        (ok if has(v, "delivery", "warning") else bad)("warns: no destinations")

        # unresolvable period -> failed
        api.req("PUT", f"{base}/definitions/{rid}/period-rule",
                body={"period_type": "shift", "period_rule": "previous_completed"})
        v = api.req("POST", f"{base}/definitions/{rid}/validate")
        (ok if v["overall"] == "failed" and has(v, "period", "failed") else bad)(
            "shift period -> failed", f"overall={v['overall']}")

        # clear period -> back to warning
        api.req("DELETE", f"{base}/definitions/{rid}/period-rule")
        v = api.req("POST", f"{base}/definitions/{rid}/validate")
        (ok if v["overall"] == "warning" else bad)("period cleared -> warning", f"overall={v['overall']}")

        # PDF destination + empty template -> layout failed
        did = api.req("POST", f"{base}/destinations",
                      body={"name": "B1_TMP_DEST", "dest_type": "folder",
                            "target": "/tmp/b1_validate", "default_fmts": "pdf", "enabled": True})["id"]
        api.req("PUT", f"{base}/definitions/{rid}/destinations/{did}?fmt=")
        v = api.req("POST", f"{base}/definitions/{rid}/validate")
        (ok if v["overall"] == "failed" and has(v, "layout", "failed") else bad)(
            "PDF dest, no template -> failed", f"overall={v['overall']}")

    finally:
        try:
            if rid is not None and did is not None:
                api.req("DELETE", f"{base}/definitions/{rid}/destinations/{did}")
        except Exception:
            pass
        try:
            if did is not None:
                api.req("DELETE", f"{base}/destinations/{did}")
        except Exception:
            pass
        try:
            if rid is not None:
                api.req("DELETE", f"{base}/definitions/{rid}")
                ok("cleanup")
        except Exception as e:
            bad("cleanup", str(e))

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
