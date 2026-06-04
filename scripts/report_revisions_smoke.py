#!/usr/bin/env python3
"""
Phase B3a smoke — report revision model, against a RUNNING stack.

Throwaway, self-cleaning. Proves:
  * a new report is status=draft, no active revision
  * snapshotting creates a draft revision (with config + validation)
  * activating sets the report active + records activated_by
  * a second activation supersedes the first and repoints active_revision_id
  * a revision whose validation FAILS (batch period) cannot be activated (409)

Render/scheduler are NOT exercised — B3a is runtime-dormant by design.

Stdlib only. Run from the host:
  $env:SMOKE_PASS="..."; python scripts\\report_revisions_smoke.py
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
    print("Phase B3a smoke — revision model")
    try:
        api.login(args.user, args.password); ok("login")
    except Exception as e:
        bad("login", str(e)); return 1

    rid = None
    try:
        rid = api.req("POST", f"{base}/definitions",
                      body={"name": "B3_REVISIONS_TMP", "category": "on_demand",
                            "template_html": "", "page_size": "A4", "orientation": "portrait"})["id"]
        ok("create throwaway report", f"id={rid}")

        d = api.req("GET", f"{base}/definitions/{rid}")
        (ok if d["status"] == "draft" and d.get("active_revision_id") in (None,) else bad)(
            "new report -> status draft, no active revision", f"status={d['status']}")

        # draft revision 1
        r1 = api.req("POST", f"{base}/definitions/{rid}/revisions", body={"notes": "first cut"})
        (ok if r1["status"] == "draft" and r1["revision_no"] == 1 else bad)(
            "create draft revision 1", f"no={r1['revision_no']} status={r1['status']}")
        (ok if r1.get("validation", {}).get("overall") else bad)(
            "draft carries a validation result", f"overall={r1.get('validation', {}).get('overall')}")
        (ok if "definition" in (r1.get("config") or {}) and "bindings" in (r1.get("config") or {}) else bad)(
            "draft config snapshot has definition + bindings")

        lst = api.req("GET", f"{base}/definitions/{rid}/revisions")
        (ok if any(x["id"] == r1["id"] for x in lst) else bad)("list shows revision 1", f"n={len(lst)}")

        # activate revision 1
        a1 = api.req("POST", f"{base}/definitions/{rid}/revisions/{r1['id']}/activate")
        (ok if a1["status"] == "active" and a1.get("activated_by") else bad)(
            "activate revision 1", f"status={a1['status']} by={a1.get('activated_by')}")
        d = api.req("GET", f"{base}/definitions/{rid}")
        (ok if d["active_revision_id"] == r1["id"] and d["status"] == "active" else bad)(
            "report now active + points at revision 1", f"status={d['status']} ptr={d['active_revision_id']}")

        # revision 2 supersedes 1
        r2 = api.req("POST", f"{base}/definitions/{rid}/revisions", body={"notes": "second cut"})
        api.req("POST", f"{base}/definitions/{rid}/revisions/{r2['id']}/activate")
        r1_after = api.req("GET", f"{base}/definitions/{rid}/revisions/{r1['id']}")
        d = api.req("GET", f"{base}/definitions/{rid}")
        (ok if r1_after["status"] == "superseded" and d["active_revision_id"] == r2["id"] else bad)(
            "activating revision 2 supersedes 1 + repoints", f"r1={r1_after['status']} ptr={d['active_revision_id']}")

        # validation gate: batch period -> draft -> activate must 409
        api.req("PUT", f"{base}/definitions/{rid}/period-rule",
                body={"period_type": "batch", "period_rule": "previous_completed"})
        r3 = api.req("POST", f"{base}/definitions/{rid}/revisions", body={"notes": "bad period"})
        (ok if r3.get("validation", {}).get("overall") == "failed" else bad)(
            "draft with batch period validates failed", f"overall={r3.get('validation', {}).get('overall')}")
        try:
            api.req("POST", f"{base}/definitions/{rid}/revisions/{r3['id']}/activate")
            bad("activate failing revision is blocked", "expected 409, got success")
        except ApiError as e:
            (ok if e.status == 409 else bad)("activate failing revision is blocked", f"HTTP {e.status}")
        api.req("DELETE", f"{base}/definitions/{rid}/period-rule")

    finally:
        try:
            if rid is not None:
                api.req("DELETE", f"{base}/definitions/{rid}")
                ok("cleanup (cascade removes revisions)")
        except Exception as e:
            bad("cleanup", str(e))

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
