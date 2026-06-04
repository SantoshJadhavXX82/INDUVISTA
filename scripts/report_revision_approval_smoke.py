#!/usr/bin/env python3
"""
Phase B5 smoke — revision activation requires the approver role.
Against a RUNNING stack. Throwaway, self-cleaning.

Proves:
  * an ENGINEER cannot activate a revision (403)
  * an APPROVER can (200) — the new rung works
  * ADMIN can (200) — higher than approver
  * drafting stays at engineer+ (engineer CAN create a draft)

Creates two temporary users (engineer, approver) and disables them after.

Stdlib only:  $env:SMOKE_PASS="..."; python scripts\\report_revision_approval_smoke.py
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
    args = ap.parse_args()
    if not args.password:
        print("ERROR: no password. Pass --password or set SMOKE_PASS."); return 1

    admin = Api(args.base)
    base = "/api/report-config"
    users = "/api/admin/users"
    sfx = str(int(time.time()))
    eng_name, apr_name = f"b5_eng_{sfx}", f"b5_apr_{sfx}"
    pw = "Smoke#Passw0rd"
    print("Phase B5 smoke — approver-gated activation")
    try:
        admin.login(args.user, args.password); ok("admin login")
    except Exception as e:
        bad("admin login", str(e)); return 1

    rid = eng_id = apr_id = None
    try:
        # provision temp users
        eng_id = admin.req("POST", users, body={"username": eng_name, "role": "engineer",
                           "auth_provider": "local", "password": pw, "must_change_password": False})["id"]
        apr_id = admin.req("POST", users, body={"username": apr_name, "role": "approver",
                           "auth_provider": "local", "password": pw, "must_change_password": False})["id"]
        ok("create engineer + approver users", f"eng={eng_id} apr={apr_id}")

        rid = admin.req("POST", f"{base}/definitions",
                        body={"name": f"B5_APPROVAL_{sfx}", "category": "on_demand",
                              "template_html": "", "page_size": "A4", "orientation": "portrait"})["id"]
        ok("create throwaway report", f"id={rid}")

        # engineer can draft (drafting stays at engineer+)
        eng = Api(args.base); eng.login(eng_name, pw)
        r1 = eng.req("POST", f"{base}/definitions/{rid}/revisions", body={"notes": "eng draft"})
        ok("engineer can create a draft", f"rev={r1['revision_no']}")

        # engineer CANNOT activate -> 403
        try:
            eng.req("POST", f"{base}/definitions/{rid}/revisions/{r1['id']}/activate")
            bad("engineer activate is blocked", "expected 403, got success")
        except ApiError as e:
            (ok if e.status == 403 else bad)("engineer activate is blocked", f"HTTP {e.status}")

        # approver CAN activate -> 200
        apr = Api(args.base); apr.login(apr_name, pw)
        a1 = apr.req("POST", f"{base}/definitions/{rid}/revisions/{r1['id']}/activate")
        (ok if a1["status"] == "active" else bad)("approver can activate", f"status={a1['status']}")

        # admin CAN activate (a new draft) -> 200
        r2 = admin.req("POST", f"{base}/definitions/{rid}/revisions", body={"notes": "admin draft"})
        a2 = admin.req("POST", f"{base}/definitions/{rid}/revisions/{r2['id']}/activate")
        (ok if a2["status"] == "active" else bad)("admin can activate", f"status={a2['status']}")

    finally:
        try:
            if rid is not None:
                admin.req("DELETE", f"{base}/definitions/{rid}")
        except Exception as e:
            bad("cleanup report", str(e))
        for uid, label in ((eng_id, "engineer"), (apr_id, "approver")):
            try:
                if uid is not None:
                    admin.req("DELETE", f"{users}/{uid}")
            except Exception:
                pass
        ok("cleanup")

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
