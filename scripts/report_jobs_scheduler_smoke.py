#!/usr/bin/env python3
"""
Phase B4b smoke — SCHEDULED fires record a job, against a RUNNING stack.
Throwaway, self-cleaning. Slow by nature: it creates an every-minute timed
trigger and waits for the scheduler to fire (polls up to ~95s).

Proves a scheduled run is logged as a job (trigger_kind timed, status
succeeded/partial, format captured). Requires svj_report_scheduler running
with the B4b code.

Stdlib only:  $env:SMOKE_PASS="..."; python scripts\\report_jobs_scheduler_smoke.py
Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
    ap.add_argument("--wait", type=int, default=95, help="seconds to wait for a fire")
    args = ap.parse_args()
    if not args.password:
        print("ERROR: no password. Pass --password or set SMOKE_PASS."); return 1

    api = Api(args.base)
    base = "/api/report-config"
    print("Phase B4b smoke — scheduled fires record a job")
    try:
        api.login(args.user, args.password); ok("login")
    except Exception as e:
        bad("login", str(e)); return 1

    rid = tid = None
    try:
        rid = api.req("POST", f"{base}/definitions",
                      body={"name": "B4B_SCHED_TMP", "category": "periodic",
                            "template_html": "<p>{{ report.name }}</p>",
                            "page_size": "A4", "orientation": "portrait", "enabled": True})["id"]
        ok("create scheduled report", f"id={rid}")

        tid = api.req("POST", f"{base}/triggers",
                      body={"name": "B4B_SCHED_TRG", "trigger_type": "timed",
                            "period": "interval", "interval_minutes": 1, "enabled": True})["id"]
        api.req("PUT", f"{base}/definitions/{rid}/triggers/{tid}")
        ok("create + link every-minute timed trigger", f"id={tid}")

        print(f"  ... waiting up to {args.wait}s for the scheduler to fire (interval ~30s)")
        found = None
        deadline = time.time() + args.wait
        while time.time() < deadline:
            jobs = api.req("GET", f"{base}/definitions/{rid}/jobs")
            sched = [j for j in jobs if j["trigger_kind"] in ("timed", "tag")]
            if sched:
                found = sched[0]; break
            time.sleep(5)

        if not found:
            bad("scheduled fire recorded a job", f"none seen within {args.wait}s")
        else:
            ok("scheduled fire recorded a job", f"id={found['id']} trigger_kind={found['trigger_kind']}")
            (ok if found["status"] in ("succeeded", "partial") else bad)(
                "job status reflects the run", f"status={found['status']}")
            (ok if found.get("formats") and "pdf" in found["formats"] else bad)(
                "job captured the rendered format(s)", f"formats={found.get('formats')}")
            (ok if found.get("duration_ms") is not None else bad)("job captured duration")

    finally:
        try:
            if rid is not None and tid is not None:
                api.req("DELETE", f"{base}/definitions/{rid}/triggers/{tid}")
        except Exception:
            pass
        try:
            if rid is not None:
                api.req("DELETE", f"{base}/definitions/{rid}")
        except Exception as e:
            bad("cleanup report", str(e))
        try:
            if tid is not None:
                api.req("DELETE", f"{base}/triggers/{tid}")
        except Exception as e:
            bad("cleanup trigger", str(e))
        ok("cleanup")

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
