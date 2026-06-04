#!/usr/bin/env python3
"""
Template live-preview smoke — RUNNING stack.

Proves POST /report-config/definitions/{id}/preview:
  * renders the IN-PROGRESS template from the request body (blocks or html)
  * returns HTML with the compiled output (header/table) + resolved tags
  * PERSISTS NOTHING — the saved definition's mode/blocks are unchanged after
  * empty-blocks preview -> 400

Self-cleaning. Stdlib only:
  $env:SMOKE_PASS="..."; python scripts\report_preview_smoke.py
Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations
import argparse, json, os, sys, time
from urllib import error as urlerror, parse as urlparse, request as urlreq

PASS = FAIL = 0
RC = "/api/report-config"


class ApiError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")


class Api:
    def __init__(self, base): self.base = base.rstrip("/"); self.token = None
    def _h(self, body):
        h = {"Accept": "application/json"}
        if body is not None: h["Content-Type"] = "application/json"
        if self.token: h["Authorization"] = f"Bearer {self.token}"
        return h
    def raw(self, method, path, params=None, body=None):
        url = f"{self.base}{path}"
        if params:
            pairs = []
            for k, v in params.items():
                if isinstance(v, (list, tuple)): pairs.extend((k, str(x)) for x in v)
                elif v is not None: pairs.append((k, str(v)))
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
        if not raw: return None
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
    print("Template live-preview smoke")
    try:
        a.login(args.user, args.password); ok("admin login")
    except Exception as e:
        bad("admin login", str(e)); print("-" * 60); print("PASS 0 FAIL 1"); return 1

    try:
        tags = a.req("GET", "/api/tags", params={"limit": 5}) or []
    except Exception:
        tags = []
    if isinstance(tags, list) and len(tags) >= 2:
        id1, name1, id2 = tags[0]["id"], tags[0]["name"], tags[1]["id"]
        ok("fetched 2 tags", f"{name1} (#{id1}), #{id2}")
    else:
        id1, name1, id2 = 1, "tag_1", 2
        ok("no tags — synthetic ids")

    rid = None
    rname = f"preview_smoke_{os.getpid()}_{int(time.time())}"
    blocks = [
        {"id": "h1", "type": "header", "title_source": "report_name", "show_generated": True},
        {"id": "tbl1", "type": "tag_table", "columns": [
            {"key": "name", "label": "Tag", "kind": "data"},
            {"key": "display", "label": "Value", "kind": "data"},
        ]},
    ]
    try:
        # Create an HTML-mode report (so we can prove preview-with-blocks does NOT persist).
        created = a.req("POST", f"{RC}/definitions", body={
            "name": rname, "category": "on_demand", "template_mode": "html",
            "template_html": "<p>orig</p>",
        })
        rid = created["id"]
        a.req("PUT", f"{RC}/definitions/{rid}/tags", body={"tag_ids": [id1, id2]})
        ok("create html-mode report + bind tags", f"id={rid}")

        # Preview a BLOCKS template via the body (report itself stays html-mode).
        html = a.req("POST", f"{RC}/definitions/{rid}/preview", body={
            "template_mode": "blocks", "template_blocks": blocks,
            "template_html": "", "page_size": "A4", "orientation": "portrait",
        })
        html = html if isinstance(html, str) else (html or "")
        (ok if "rpt-header" in html and "rpt-table" in html else bad)(
            "preview renders compiled blocks (header+table)")
        (ok if (name1 in html) or name1 == "tag_1" else bad)("preview table shows bound tag", name1)
        (ok if "{{" not in html and "{tag:" not in html else bad)("no unrendered Jinja remains")

        # The saved definition must be UNCHANGED by preview.
        after = a.req("GET", f"{RC}/definitions/{rid}")
        (ok if after.get("template_mode") == "html" and not after.get("template_blocks") else bad)(
            "preview did NOT persist (still html, no blocks)",
            f"mode={after.get('template_mode')} blocks={after.get('template_blocks')}")

        # HTML-mode preview from the body too.
        html2 = a.req("POST", f"{RC}/definitions/{rid}/preview", body={
            "template_mode": "html",
            "template_html": "<h1>{{ report.name }}</h1>",
            "page_size": "A4", "orientation": "portrait",
        })
        html2 = html2 if isinstance(html2, str) else (html2 or "")
        (ok if rname in html2 else bad)("html-mode preview renders {{ report.name }}")

        # Negative: blocks mode with no blocks -> 400.
        try:
            a.req("POST", f"{RC}/definitions/{rid}/preview", body={
                "template_mode": "blocks", "template_blocks": [],
            })
            bad("empty-blocks preview rejected", "expected 400, got 200")
        except ApiError as e:
            (ok if e.status == 400 else bad)("empty-blocks preview -> 400", f"status={e.status}")

    finally:
        if rid:
            try: a.req("DELETE", f"{RC}/definitions/{rid}")
            except Exception: pass
        ok("cleanup test report")

    print("-" * 60)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
