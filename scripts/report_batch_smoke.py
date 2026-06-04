#!/usr/bin/env python3
"""
Batch-override + batch-period smoke — against a RUNNING stack.

Self-cleaning (deletes its own batch rows + throwaway report). Proves:

  * POST /report-config/batches/start opens a batch; a second start -> 409
  * POST /report-config/batches/stop closes it; a second stop -> 404
  * a report with period_type=batch / previous_completed renders the COMPLETED
    batch's window (period_start/period_end == that run's start/end)
  * period_type=batch / current renders the OPEN batch's window (start..~now)
  * with no batch available, validation reports the period as FAILED

Stdlib only. Run from the host:

  $env:SMOKE_PASS="..."; python scripts\\report_batch_smoke.py
  # or: python scripts/report_batch_smoke.py --password <admin-pw>

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from urllib import error as urlerror
from urllib import parse as urlparse
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
        self.token = self.req("POST", "/api/auth/login",
                              body={"username": user, "password": pw})["access_token"]


def ok(n, d=""):
    global PASS; PASS += 1; print(f"  [PASS] {n}" + (f" — {d}" if d else ""))


def bad(n, d=""):
    global FAIL; FAIL += 1; print(f"  [FAIL] {n}" + (f" — {d}" if d else ""))


def render_json(api, rid):
    st, ct, raw = api.raw("POST", f"/api/report-config/definitions/{rid}/render",
                          params={"format": "json"})
    return json.loads(raw.decode("utf-8"))


def set_period(api, rid, rule):
    api.req("PUT", f"/api/report-config/definitions/{rid}/period-rule",
            body={"period_type": "batch", "period_rule": rule})


def period_check_level(validation):
    for r in (validation or {}).get("results", []) or []:
        if r.get("category") == "period":
            return r.get("level")
    return None


def _parse(t):
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("SMOKE_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--user", default=os.getenv("SMOKE_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SMOKE_PASS"))
    args = ap.parse_args()
    if not args.password:
        print("ERROR: no password. Pass --password or set SMOKE_PASS.")
        return 1

    api = Api(args.base)
    print("Batch-override + batch-period smoke")

    try:
        api.login(args.user, args.password)
        ok("login mints bearer token")
    except Exception as e:
        bad("login", str(e)); print("-" * 60); print("PASS 0 FAIL 1"); return 1

    made_batches: list[int] = []
    rid = None
    try:
        # Clean slate: if a batch is open from a prior run, close it.
        cur = api.req("GET", "/api/report-config/batches/current")
        if cur:
            api.req("POST", "/api/report-config/batches/stop")

        # --- start / double-start / stop / double-stop ---
        b1 = api.req("POST", "/api/report-config/batches/start", body={"batch_no": "B-SMOKE-1"})
        made_batches.append(b1["id"])
        (ok if b1.get("status") == "open" else bad)("start opens a batch", f"id={b1['id']} status={b1.get('status')}")

        try:
            api.req("POST", "/api/report-config/batches/start", body={"batch_no": "B-SMOKE-DUP"})
            bad("second start blocked -> 409", "accepted (should 409)")
        except ApiError as e:
            (ok if e.status == 409 else bad)("second start blocked -> 409", f"status={e.status}")

        b1c = api.req("POST", "/api/report-config/batches/stop")
        (ok if b1c.get("status") == "closed" and b1c.get("ended_at") else bad)(
            "stop closes the batch", f"status={b1c.get('status')} ended={b1c.get('ended_at')}")

        try:
            api.req("POST", "/api/report-config/batches/stop")
            bad("second stop -> 404", "accepted (should 404)")
        except ApiError as e:
            (ok if e.status == 404 else bad)("second stop -> 404", f"status={e.status}")

        # --- a report covering the previous completed batch ---
        tmpl = "<p>{{ report.period_start }}..{{ report.period_end }}</p>"
        d = api.req("POST", "/api/report-config/definitions",
                    body={"name": "BATCH_SMOKE_TMP", "category": "on_demand",
                          "template_html": tmpl, "page_size": "A4", "orientation": "portrait"})
        rid = d["id"]
        ok("create throwaway report", f"id={rid}")

        set_period(api, rid, "previous_completed")
        out = render_json(api, rid)
        rep = out.get("report") or {}
        ps, pe = rep.get("period_start"), rep.get("period_end")
        # The completed batch's window should match b1 start/end (compare instants).
        match = (_parse(ps) == _parse(b1c.get("started_at"))
                 and _parse(pe) == _parse(b1c.get("ended_at")))
        (ok if match else bad)("batch/previous -> completed batch window",
                               f"got {ps}..{pe} vs {b1c.get('started_at')}..{b1c.get('ended_at')}")

        # --- a report covering the current OPEN batch ---
        b2 = api.req("POST", "/api/report-config/batches/start", body={"batch_no": "B-SMOKE-2"})
        made_batches.append(b2["id"])
        set_period(api, rid, "current")
        out2 = render_json(api, rid)
        rep2 = out2.get("report") or {}
        ps2, pe2 = rep2.get("period_start"), rep2.get("period_end")
        start_ok = (_parse(ps2) == _parse(b2.get("started_at")))
        e2 = _parse(pe2)
        end_ok = e2 is not None and e2 >= _parse(b2.get("started_at"))
        (ok if start_ok and end_ok else bad)("batch/current -> open batch window (start..now)",
                                             f"{ps2}..{pe2}")

        # close + delete the open batch so 'no batch' can be tested cleanly
        api.req("POST", "/api/report-config/batches/stop")
        for bid in made_batches:
            try:
                api.req("DELETE", f"/api/report-config/batches/{bid}")
            except Exception:
                pass
        made_batches.clear()

        # --- no batch available -> validation FAILED + render is a clean 400 ---
        set_period(api, rid, "current")
        v = api.req("POST", f"/api/report-config/definitions/{rid}/validate")
        lvl = period_check_level(v)
        (ok if lvl == "failed" else bad)("no open batch -> period validation failed",
                                         f"period level={lvl}")
        try:
            api.req("POST", f"/api/report-config/definitions/{rid}/render",
                    params={"format": "json"})
            bad("render with no open batch -> 400 (not 500)", "did not error")
        except ApiError as e:
            (ok if e.status == 400 else bad)("render with no open batch -> 400 (not 500)",
                                             f"status={e.status}")

    finally:
        for bid in made_batches:
            try:
                api.req("DELETE", f"/api/report-config/batches/{bid}")
            except Exception:
                pass
        if rid is not None:
            try:
                api.req("DELETE", f"/api/report-config/definitions/{rid}")
                ok("cleanup throwaway report")
            except Exception as e:
                bad("cleanup throwaway report", str(e))

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
