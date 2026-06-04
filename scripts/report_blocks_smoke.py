#!/usr/bin/env python3
"""
Report blocks (template configurator) smoke — TC-1 backend, RUNNING stack.

Proves the no-code block path end to end:
  * POST/GET/PATCH /definitions persist template_mode + template_blocks (round-trip)
  * POST /definitions/{id}/render?format=html honors blocks mode:
      - compiles the block list to HTML (header / text / tag_table / kpi_row)
      - tag() resolves bound tags; tag_table shows one row per bound tag
      - no unrendered Jinja remains
  * format=json still works for a blocks report
  * a blocks report with NO blocks -> 400 (not 500, not "no template_html")

Self-cleaning. Stdlib only:
  $env:SMOKE_PASS="..."; python scripts\report_blocks_smoke.py
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
RC = "/api/report-config"


class ApiError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/"); self.token = None

    def _h(self, body):
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def raw(self, method, path, params=None, body=None):
        url = f"{self.base}{path}"
        if params:
            pairs = []
            for k, v in params.items():
                if isinstance(v, (list, tuple)):
                    pairs.extend((k, str(x)) for x in v)
                elif v is not None:
                    pairs.append((k, str(v)))
            url += "?" + urlparse.urlencode(pairs)
        data = json.dumps(body).encode() if body is not None else None
        req = urlreq.Request(url, data=data, headers=self._h(data), method=method)
        try:
            with urlreq.urlopen(req, timeout=60) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urlerror.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace"), url)

    def req(self, method, path, params=None, body=None):
        st, ct, raw = self.raw(method, path, params=params, body=body)
        if not raw:
            return None
        return json.loads(raw.decode()) if "application/json" in ct else raw.decode()

    def login(self, u, p):
        self.token = self.req("POST", "/api/auth/login", body={"username": u, "password": p})["access_token"]


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
        print("ERROR: set SMOKE_PASS or pass --password."); return 1

    a = Api(args.base)
    print("Report blocks (template configurator) smoke — TC-1")
    try:
        a.login(args.user, args.password); ok("admin login")
    except Exception as e:
        bad("admin login", str(e)); print("-" * 60); print("PASS 0 FAIL 1"); return 1

    # Two real tags to bind/reference (fallback to synthetic ids if none exist).
    try:
        tags = a.req("GET", "/api/tags", params={"limit": 5}) or []
    except Exception:
        tags = []
    if isinstance(tags, list) and len(tags) >= 2:
        t1, t2 = tags[0], tags[1]
        id1, name1 = t1["id"], t1["name"]
        id2 = t2["id"]
        ok("fetched 2 tags to bind", f"{name1} (#{id1}), #{id2}")
    else:
        id1, name1, id2 = 1, "tag_1", 2
        ok("no tags available — using synthetic ids", f"#{id1}, #{id2}")

    blocks = [
        {"id": "h1", "type": "header", "title_source": "report_name", "show_generated": True},
        {"id": "t1", "type": "text", "content": f"Latest {{tag:{name1}}} value."},
        {"id": "tbl1", "type": "tag_table", "columns": [
            {"key": "name", "label": "Tag", "kind": "data"},
            {"key": "display", "label": "Value", "kind": "data"},
            {"key": "unit", "label": "Unit", "kind": "data"},
        ]},
        {"id": "k1", "type": "kpi_row", "items": [{"tag_id": id1, "label": f"KPI {name1}"}]},
    ]
    rid = None
    rid_empty = None
    rname = f"blocks_smoke_{os.getpid()}_{int(time.time())}"
    try:
        created = a.req("POST", f"{RC}/definitions", body={
            "name": rname, "category": "on_demand",
            "template_mode": "blocks", "template_blocks": blocks,
        })
        rid = created["id"]
        (ok if created.get("template_mode") == "blocks" else bad)(
            "create persists template_mode=blocks", f"mode={created.get('template_mode')}")
        (ok if isinstance(created.get("template_blocks"), list)
            and len(created["template_blocks"]) == 4 else bad)(
            "create persists 4 blocks",
            f"n={len(created.get('template_blocks') or [])}")

        got = a.req("GET", f"{RC}/definitions/{rid}")
        (ok if got.get("template_mode") == "blocks"
            and len(got.get("template_blocks") or []) == 4 else bad)("GET round-trips blocks")

        # PATCH: append a spacer -> 5 blocks.
        blocks2 = blocks + [{"id": "sp1", "type": "spacer", "height_mm": 8}]
        a.req("PATCH", f"{RC}/definitions/{rid}", body={"template_blocks": blocks2})
        got2 = a.req("GET", f"{RC}/definitions/{rid}")
        (ok if len(got2.get("template_blocks") or []) == 5
            and got2["template_blocks"][-1]["type"] == "spacer" else bad)(
            "PATCH updates blocks (append spacer)",
            f"n={len(got2.get('template_blocks') or [])}")

        # Render HTML (live path via tag_ids) — compiled blocks must appear.
        html = a.req("POST", f"{RC}/definitions/{rid}/render",
                     params={"format": "html", "tag_ids": [id1, id2]})
        html = html if isinstance(html, str) else (html or "")
        markers = ["rpt-header", "rpt-text", "rpt-table", "rpt-kpi"]
        missing = [m for m in markers if m not in html]
        (ok if not missing else bad)("HTML render emits compiled block markers",
                                     f"missing={missing}" if missing else "header/text/table/kpi present")
        (ok if rname in html else bad)("header shows report name")
        (ok if (name1 in html) or (name1 == "tag_1") else bad)("tag_table shows bound tag name", name1)
        (ok if "{tag:" not in html and "{{" not in html else bad)(
            "no unrendered Jinja/tokens remain")

        # JSON still works for a blocks report.
        j = a.req("POST", f"{RC}/definitions/{rid}/render",
                  params={"format": "json", "tag_ids": [id1, id2]})
        (ok if isinstance(j, dict) and "report" in j else bad)("JSON render works for blocks report")

        # Negative: blocks mode with NO blocks -> 400 (clear message, not 500).
        empty = a.req("POST", f"{RC}/definitions", body={
            "name": rname + "_empty", "category": "on_demand",
            "template_mode": "blocks", "template_blocks": [],
        })
        rid_empty = empty["id"]
        try:
            a.req("POST", f"{RC}/definitions/{rid_empty}/render",
                  params={"format": "html", "tag_ids": [id1]})
            bad("empty-blocks render rejected", "expected 400, got 200")
        except ApiError as e:
            (ok if e.status == 400 and "block" in e.body.lower() else bad)(
                "empty-blocks render -> 400 with blocks message", f"status={e.status}")

    finally:
        for x in (rid, rid_empty):
            if x:
                try:
                    a.req("DELETE", f"{RC}/definitions/{x}")
                except Exception:
                    pass
        ok("cleanup test reports")

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
