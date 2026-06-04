#!/usr/bin/env python3
"""
Auditor-capability smoke — full automated check against a RUNNING stack.

Self-cleaning. Verifies every backend contract the auditor feature (and its UI)
relies on:

  Capability plumbing
    * POST /api/admin/users persists can_audit (create-form path)
    * GET  /api/admin/users returns can_audit per user (Users table column)
    * PATCH /api/admin/users/{id} {can_audit} toggles it (per-row toggle path)
    * login response + GET /api/auth/me carry can_audit (drives nav visibility)

  Audit-log gating (GET /api/audit-log)
    * admin                         -> 200
    * plain viewer                  -> 403  (used to be open to everyone)
    * viewer WITH can_audit         -> 200
    * grant can_audit  + re-login   -> 200  (claim refreshes on next login)
    * revoke can_audit + re-login   -> 403

Stdlib only:
  $env:SMOKE_PASS="..."; python scripts\auditor_smoke.py
  # or: python scripts/auditor_smoke.py --password <admin-pw>

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlreq

PASS = FAIL = 0
PW = "Auditor#Smoke1"  # throwaway local password for the test users


class ApiError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def _headers(self, body):
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def raw(self, method, path, params=None, body=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urlparse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req = urlreq.Request(url, data=data, headers=self._headers(data), method=method)
        try:
            with urlreq.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urlerror.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace"), url)

    def req(self, method, path, params=None, body=None):
        st, ct, raw = self.raw(method, path, params=params, body=body)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8")) if "application/json" in ct else raw

    def login(self, user, pw):
        """Login and keep the token; returns the full login response."""
        resp = self.req("POST", "/api/auth/login", body={"username": user, "password": pw})
        self.token = resp["access_token"]
        return resp


def ok(n, d=""):
    global PASS; PASS += 1; print(f"  [PASS] {n}" + (f" — {d}" if d else ""))


def bad(n, d=""):
    global FAIL; FAIL += 1; print(f"  [FAIL] {n}" + (f" — {d}" if d else ""))


def audit_status(base, token):
    """HTTP status of GET /api/audit-log for the given token."""
    a = Api(base); a.token = token
    try:
        a.req("GET", "/api/audit-log", params={"limit": 1})
        return 200
    except ApiError as e:
        return e.status


def fresh_login_can_audit(base, user, pw):
    """Login fresh and return (login_response_can_audit, audit_status)."""
    a = Api(base)
    resp = a.login(user, pw)
    return resp.get("can_audit"), audit_status(base, a.token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("SMOKE_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--user", default=os.getenv("SMOKE_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SMOKE_PASS"))
    args = ap.parse_args()
    if not args.password:
        print("ERROR: no password. Pass --password or set SMOKE_PASS.")
        return 1

    admin = Api(args.base)
    print("Auditor-capability smoke — full check")
    try:
        ar = admin.login(args.user, args.password)
        ok("admin login")
        (ok if "can_audit" in ar else bad)("login response carries can_audit field",
                                           f"can_audit={ar.get('can_audit')}")
    except Exception as e:
        bad("admin login", str(e)); print("-" * 60); print("PASS 0 FAIL 1"); return 1

    suffix = f"{os.getpid()}_{int(time.time())}"
    viewer_name = f"smoke_viewer_{suffix}"
    auditor_name = f"smoke_auditor_{suffix}"
    ids: list[int] = []

    try:
        # --- create-form path: can_audit persisted on create ---
        v = admin.req("POST", "/api/admin/users",
                      body={"username": viewer_name, "role": "viewer", "password": PW,
                            "must_change_password": False, "can_audit": False})
        ids.append(v["id"])
        au = admin.req("POST", "/api/admin/users",
                       body={"username": auditor_name, "role": "viewer", "password": PW,
                             "must_change_password": False, "can_audit": True})
        ids.append(au["id"])
        (ok if v.get("can_audit") is False else bad)("create viewer -> can_audit false",
                                                     f"can_audit={v.get('can_audit')}")
        (ok if au.get("can_audit") is True else bad)("create auditor -> can_audit true",
                                                     f"can_audit={au.get('can_audit')}")

        # --- Users table column path: list carries can_audit ---
        users = admin.req("GET", "/api/admin/users")
        by_name = {u["username"]: u for u in users}
        (ok if by_name.get(viewer_name, {}).get("can_audit") is False
            and by_name.get(auditor_name, {}).get("can_audit") is True else bad)(
            "list users returns correct can_audit per user")

        # --- gating ---
        (ok if audit_status(args.base, admin.token) == 200 else bad)("admin reads audit log (200)")

        vca, vstatus = fresh_login_can_audit(args.base, viewer_name, PW)
        (ok if vca is False else bad)("viewer login response can_audit=false (nav hidden)", f"can_audit={vca}")
        (ok if vstatus == 403 else bad)("plain viewer denied audit log (403)", f"status={vstatus}")

        aca, astatus = fresh_login_can_audit(args.base, auditor_name, PW)
        (ok if aca is True else bad)("auditor login response can_audit=true (nav shown)", f"can_audit={aca}")
        (ok if astatus == 200 else bad)("auditor allowed audit log (200)", f"status={astatus}")

        # --- /me reflects the capability ---
        aapi = Api(args.base); aapi.login(auditor_name, PW)
        me = aapi.req("GET", "/api/auth/me")
        (ok if me and me.get("can_audit") is True else bad)("auditor /me shows can_audit=true",
                                                            f"can_audit={me and me.get('can_audit')}")

        # --- per-row toggle: GRANT then re-login flips viewer to allowed ---
        admin.req("PATCH", f"/api/admin/users/{v['id']}", body={"can_audit": True})
        gca, gstatus = fresh_login_can_audit(args.base, viewer_name, PW)
        (ok if gca is True and gstatus == 200 else bad)(
            "grant can_audit + re-login -> can_audit true, audit 200", f"can_audit={gca} status={gstatus}")

        # --- per-row toggle: REVOKE then re-login removes access ---
        admin.req("PATCH", f"/api/admin/users/{au['id']}", body={"can_audit": False})
        rca, rstatus = fresh_login_can_audit(args.base, auditor_name, PW)
        (ok if rca is False and rstatus == 403 else bad)(
            "revoke can_audit + re-login -> can_audit false, audit 403", f"can_audit={rca} status={rstatus}")

    finally:
        for uid in ids:
            try:
                admin.req("DELETE", f"/api/admin/users/{uid}", params={"hard": "true"})
            except Exception:
                pass
        ok("cleanup test users")

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
