#!/usr/bin/env python3
"""
Phase A3a smoke — period-rule + bindings wiring, against a RUNNING stack.

Proves the opt-in integration without touching production data (throwaway
report, self-cleaning):

  * a report WITH a period rule renders through the aggregation path
    (report.period_start / period_end present in the JSON output)
  * a report WITHOUT a rule renders through the legacy live-snapshot path
    (period_start / period_end are null)
  * the new period-rule and bindings endpoints round-trip and validate
  * a binding-aware tag save preserves per-tag binding columns

Stdlib only. Run from the host:

  $env:SMOKE_PASS="..."; python scripts\\report_period_render_smoke.py
  # or: python scripts/report_period_render_smoke.py --password <admin-pw>

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlreq

PASS = FAIL = SKIP = 0


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


def skip(n, d=""):
    global SKIP; SKIP += 1; print(f"  [SKIP] {n}" + (f" — {d}" if d else ""))


def discover_tag_id(api):
    """Best-effort: a real tag id for binding tests. None if not found."""
    try:
        srcs = api.req("GET", "/api/opc-sources")
        for s in (srcs or []):
            dev = s.get("device_id") or s.get("device") or s.get("id")
            if dev is None:
                continue
            tags = api.req("GET", "/api/tags", params={"device_id": dev})
            if tags:
                return (tags[0].get("id") if isinstance(tags[0], dict) else None)
    except Exception:
        pass
    try:
        tags = api.req("GET", "/api/tags")
        if tags and isinstance(tags, list) and isinstance(tags[0], dict):
            return tags[0].get("id")
    except Exception:
        pass
    return None


def render_json(api, rid):
    st, ct, raw = api.raw("POST", f"/api/report-config/definitions/{rid}/render",
                          params={"format": "json"})
    return json.loads(raw.decode("utf-8"))


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
    print("Phase A3a smoke — period rule + bindings wiring")

    try:
        api.login(args.user, args.password)
        ok("login mints bearer token")
    except Exception as e:
        bad("login", str(e)); print("-" * 60); print("PASS 0 FAIL 1"); return 1

    rid = None
    try:
        d = api.req("POST", "/api/report-config/definitions",
                    body={"name": "A3A_SMOKE_TMP", "category": "on_demand",
                          "template_html": "", "page_size": "A4", "orientation": "portrait"})
        rid = d["id"]
        ok("create throwaway report", f"id={rid}")

        # --- snapshot path first (no rule) ---
        snap = render_json(api, rid)
        ps = (snap.get("report") or {}).get("period_start")
        (ok if ps is None else bad)("no rule -> live snapshot (period_start null)", f"period_start={ps}")

        # --- set a period rule -> period path ---
        api.req("PUT", f"/api/report-config/definitions/{rid}/period-rule",
                body={"period_type": "custom", "period_rule": "custom",
                      "custom_start_offset_min": -60, "custom_end_offset_min": 0})
        rule = api.req("GET", f"/api/report-config/definitions/{rid}/period-rule")
        (ok if rule and rule.get("period_type") == "custom" else bad)(
            "period-rule round-trips", f"got {rule and rule.get('period_type')}")

        per = render_json(api, rid)
        ps2 = (per.get("report") or {}).get("period_start")
        pe2 = (per.get("report") or {}).get("period_end")
        (ok if ps2 and pe2 else bad)("rule -> period path (window in output)",
                                     f"start={ps2} end={pe2}")

        # --- invalid custom rule -> 422 ---
        try:
            api.req("PUT", f"/api/report-config/definitions/{rid}/period-rule",
                    body={"period_type": "custom", "period_rule": "custom"})
            bad("custom without offsets -> 422", "accepted (should 422)")
        except ApiError as e:
            (ok if e.status == 422 else bad)("custom without offsets -> 422", f"status={e.status}")

        # --- delete rule -> back to snapshot ---
        api.req("DELETE", f"/api/report-config/definitions/{rid}/period-rule")
        snap2 = render_json(api, rid)
        ps3 = (snap2.get("report") or {}).get("period_start")
        (ok if ps3 is None else bad)("rule cleared -> snapshot again", f"period_start={ps3}")

        # --- bindings: invalid enum -> 422 (no real tag needed) ---
        try:
            api.req("PUT", f"/api/report-config/definitions/{rid}/bindings",
                    body={"bindings": [{"tag_id": 1, "data_function": "bogus"}]})
            bad("bindings reject bad data_function -> 422", "accepted")
        except ApiError as e:
            (ok if e.status == 422 else bad)("bindings reject bad data_function -> 422",
                                             f"status={e.status}")

        # --- bindings happy path + preservation (needs a real tag) ---
        tag_id = discover_tag_id(api)
        if tag_id is None:
            skip("bindings happy path", "no tag discovered")
            skip("tag save preserves binding", "no tag discovered")
        else:
            api.req("PUT", f"/api/report-config/definitions/{rid}/bindings",
                    body={"bindings": [{"tag_id": tag_id, "data_function": "average",
                                        "quality_rule": "good_only", "alias": "A"}]})
            got = api.req("GET", f"/api/report-config/definitions/{rid}/bindings")
            df = got[0]["data_function"] if got else None
            (ok if df == "average" else bad)("bindings set/get round-trips", f"data_function={df}")

            # simple tag-list save must NOT wipe the binding config
            api.req("PUT", f"/api/report-config/definitions/{rid}/tags",
                    body={"tag_ids": [tag_id]})
            got2 = api.req("GET", f"/api/report-config/definitions/{rid}/bindings")
            df2 = got2[0]["data_function"] if got2 else None
            (ok if df2 == "average" else bad)("tag save preserves binding", f"data_function={df2}")

    finally:
        if rid is not None:
            try:
                api.req("DELETE", f"/api/report-config/definitions/{rid}")
                ok("cleanup throwaway report")
            except Exception as e:
                bad("cleanup throwaway report", str(e))

    print("-" * 60)
    tail = f"PASS {PASS}   FAIL {FAIL}   SKIP {SKIP}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)")
    print(tail)
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
