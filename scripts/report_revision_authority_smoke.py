#!/usr/bin/env python3
"""
Phase B3b smoke — the ACTIVE revision is authoritative for render, against a
RUNNING stack. Throwaway, self-cleaning.

Proves the one behavior change, decisively, via report.period_start in JSON:
  * activated report ignores LIVE config edits (renders from its snapshot)
  * re-activating a new draft picks the change up
  * a report with NO active revision still renders from live (fallback intact)

Stdlib only:  $env:SMOKE_PASS="..."; python scripts\\report_revision_authority_smoke.py
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
    print("Phase B3b smoke — active revision is authoritative")
    try:
        api.login(args.user, args.password); ok("login")
    except Exception as e:
        bad("login", str(e)); return 1

    def render_ps(rid):
        d = api.req("POST", f"{base}/definitions/{rid}/render?format=json")
        return (d.get("report") or {}).get("period_start")

    def set_period(rid):
        api.req("PUT", f"{base}/definitions/{rid}/period-rule",
                body={"period_type": "hourly", "period_rule": "previous_completed", "enabled": True})

    r1 = r2 = None
    try:
        # R1 — activated, then live-edited
        r1 = api.req("POST", f"{base}/definitions",
                     body={"name": "B3B_AUTH_TMP", "category": "on_demand",
                           "template_html": "", "page_size": "A4", "orientation": "portrait"})["id"]
        ok("create report R1", f"id={r1}")
        (ok if render_ps(r1) is None else bad)("R1 (no period) -> live snapshot", "period_start null")

        rev1 = api.req("POST", f"{base}/definitions/{r1}/revisions", body={"notes": "no period"})
        api.req("POST", f"{base}/definitions/{r1}/revisions/{rev1['id']}/activate")
        ok("activate R1 revision 1 (snapshot has no period)")

        set_period(r1)  # LIVE edit after activation
        (ok if render_ps(r1) is None else bad)(
            "activated R1 ignores live period edit", "period_start still null (snapshot wins)")

        rev2 = api.req("POST", f"{base}/definitions/{r1}/revisions", body={"notes": "with period"})
        api.req("POST", f"{base}/definitions/{r1}/revisions/{rev2['id']}/activate")
        (ok if render_ps(r1) is not None else bad)(
            "re-activating R1 picks up the period", "period_start now set")

        # R2 — never activated; live path must still apply
        r2 = api.req("POST", f"{base}/definitions",
                     body={"name": "B3B_AUTH_TMP_2", "category": "on_demand",
                           "template_html": "", "page_size": "A4", "orientation": "portrait"})["id"]
        set_period(r2)
        (ok if render_ps(r2) is not None else bad)(
            "non-activated R2 uses live config (fallback intact)", "period_start set")

    finally:
        for rid, label in ((r1, "R1"), (r2, "R2")):
            try:
                if rid is not None:
                    api.req("DELETE", f"{base}/definitions/{rid}")
            except Exception as e:
                bad(f"cleanup {label}", str(e))
        ok("cleanup")

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
