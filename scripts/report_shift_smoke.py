#!/usr/bin/env python3
"""
Shift-period smoke — proves the 'shift' period type resolves end-to-end against
a RUNNING stack, using the plant's configured shift schedule (system_settings
'shifts.config', or the 3-shift default).

Throwaway report, self-cleaning, no production data touched. Proves:

  * a report with a shift period rule RENDERS through the aggregation path
    (report.period_start / period_end present and non-empty in the JSON output)
  * the resolved window is a single, non-empty half-open interval (end > start)
  * report VALIDATION now reports the period as resolvable (period check PASSED),
    where before 'shift' was unimplemented and validation flagged it FAILED

Stdlib only. Run from the host:

  $env:SMOKE_PASS="..."; python scripts\\report_shift_smoke.py
  # or: python scripts/report_shift_smoke.py --password <admin-pw>

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
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


def _parse(ts):
    """Parse an ISO-8601 timestamp the API emits (tolerate trailing 'Z')."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def period_check_level(validation):
    for r in (validation or {}).get("results", []) or []:
        if r.get("category") == "period":
            return r.get("level")
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
    print("Shift-period smoke — shift window resolves end-to-end")

    try:
        api.login(args.user, args.password)
        ok("login mints bearer token")
    except Exception as e:
        bad("login", str(e)); print("-" * 60); print("PASS 0 FAIL 1"); return 1

    # Surface the schedule the backend will use (informational).
    try:
        cfg = api.req("GET", "/api/settings/shifts")
        starts = [s.get("start") for s in (cfg or {}).get("shifts", [])]
        print(f"     shift schedule: enabled={cfg and cfg.get('enabled')} starts={starts}")
    except Exception as e:
        print(f"     (could not read shift schedule: {e})")

    rid = None
    try:
        d = api.req("POST", "/api/report-config/definitions",
                    body={"name": "SHIFT_SMOKE_TMP", "category": "on_demand",
                          "template_html": "", "page_size": "A4", "orientation": "portrait"})
        rid = d["id"]
        ok("create throwaway report", f"id={rid}")

        # --- previous_completed shift ---
        api.req("PUT", f"/api/report-config/definitions/{rid}/period-rule",
                body={"period_type": "shift", "period_rule": "previous_completed"})
        rule = api.req("GET", f"/api/report-config/definitions/{rid}/period-rule")
        (ok if rule and rule.get("period_type") == "shift" else bad)(
            "shift period-rule round-trips", f"got {rule and rule.get('period_type')}")

        out = render_json(api, rid)
        rep = out.get("report") or {}
        ps, pe = rep.get("period_start"), rep.get("period_end")
        (ok if ps and pe else bad)("shift/previous -> window in output",
                                   f"start={ps} end={pe}")
        s, e = _parse(ps), _parse(pe)
        (ok if s and e and e > s else bad)("shift window is a valid non-empty interval",
                                           f"{ps} .. {pe}")

        # --- current shift also resolves ---
        api.req("PUT", f"/api/report-config/definitions/{rid}/period-rule",
                body={"period_type": "shift", "period_rule": "current"})
        out2 = render_json(api, rid)
        rep2 = out2.get("report") or {}
        ps2, pe2 = rep2.get("period_start"), rep2.get("period_end")
        s2, e2 = _parse(ps2), _parse(pe2)
        (ok if s2 and e2 and e2 > s2 else bad)("shift/current -> valid window",
                                               f"{ps2} .. {pe2}")

        # --- validation now treats shift as resolvable (period check PASSED) ---
        v = api.req("POST", f"/api/report-config/definitions/{rid}/validate")
        lvl = period_check_level(v)
        (ok if lvl == "passed" else bad)("validation: shift period resolves (period passed)",
                                         f"period level={lvl} overall={v and v.get('overall')}")

    finally:
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
