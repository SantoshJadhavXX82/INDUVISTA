#!/usr/bin/env python3
"""
Phase B4a smoke — report job log, against a RUNNING stack. Throwaway, self-cleaning.

Proves:
  * each on-demand render records a job (status succeeded, trigger_kind on_demand,
    format + timing captured)
  * a render that fails mid-render records a FAILED job (with an error)
  * both the per-report and recent-jobs list endpoints work

Stdlib only:  $env:SMOKE_PASS="..."; python scripts\\report_jobs_smoke.py
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("SMOKE_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--user", default=os.getenv("SMOKE_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SMOKE_PASS"))
    args = ap.parse_args()
    if not args.password:
        print("ERROR: no password. Pass --password or set SMOKE_PASS."); return 1

    api = Api(args.base)
    base = "/api/report-config"
    print("Phase B4a smoke — report job log")
    try:
        api.login(args.user, args.password); ok("login")
    except Exception as e:
        bad("login", str(e)); return 1

    rid = None
    try:
        rid = api.req("POST", f"{base}/definitions",
                      body={"name": "B4_JOBS_TMP", "category": "on_demand",
                            "template_html": "<p>{{ report.name }}</p>",
                            "page_size": "A4", "orientation": "portrait"})["id"]
        ok("create throwaway report", f"id={rid}")

        api.req("POST", f"{base}/definitions/{rid}/render?format=json")
        api.req("POST", f"{base}/definitions/{rid}/render?format=pdf")
        ok("two on-demand renders (json, pdf)")

        jobs = api.req("GET", f"{base}/definitions/{rid}/jobs")
        succeeded = [j for j in jobs if j["status"] == "succeeded"]
        (ok if len(succeeded) >= 2 else bad)("renders recorded as jobs", f"{len(succeeded)} succeeded")
        (ok if all(j["trigger_kind"] == "on_demand" for j in succeeded) else bad)("trigger_kind = on_demand")
        (ok if {"json", "pdf"} <= {j["formats"] for j in succeeded} else bad)(
            "formats captured", f"{sorted({j['formats'] for j in succeeded})}")
        (ok if all(j["snapshot_at"] and j["duration_ms"] is not None for j in succeeded) else bad)(
            "timing captured (snapshot_at + duration_ms)")

        recent = api.req("GET", f"{base}/jobs?limit=100")
        (ok if any(j["report_id"] == rid for j in recent) else bad)("recent-jobs endpoint lists our jobs")

        # Force an in-render failure: invalid template, then render pdf.
        api.req("PATCH", f"{base}/definitions/{rid}", body={"template_html": "{% if %}"})
        try:
            api.req("POST", f"{base}/definitions/{rid}/render?format=pdf")
            bad("bad template render fails", "expected 400")
        except ApiError as e:
            (ok if e.status == 400 else bad)("bad template render fails", f"HTTP {e.status}")
        jobs2 = api.req("GET", f"{base}/definitions/{rid}/jobs")
        newest = jobs2[0] if jobs2 else {}
        (ok if newest.get("status") == "failed" and newest.get("error") else bad)(
            "failed render recorded as a failed job", f"status={newest.get('status')}")

    finally:
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
