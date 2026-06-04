#!/usr/bin/env python3
"""
InduVista — Report Config smoke test (automated, self-cleaning).

Exercises the /api/report-config surface end-to-end against a RUNNING stack,
and as its centerpiece enumerates EVERY OPC UA tag (the union of tags across
all configured OPC sources' devices), attaches them all to a report, and
verifies the full set landed.

What it checks
--------------
  1.  health            GET /health is ok
  2.  auth              POST /api/auth/login mints a bearer token
  3.  opc sources       GET /api/opc-sources lists the configured sources
  4.  opc tags          GET /api/tags?device_id=<each source's device> → union
  5.  create report     POST /api/report-config/definitions (throwaway)
  6.  get report        GET  …/definitions/{id} echoes what we created
  7.  ADD ALL OPC TAGS  PUT  …/definitions/{id}/tags {tag_ids: <all opc>}
  8.  verify tags       GET  …/definitions/{id}/tags == the full OPC set
  9.  trigger link      create timed trigger → link → appears → unlink
  10. dest default      link destination with EMPTY fmt (the UI "use default"
                        case) → 204, stored as no-override         [bug fix]
  11. dest override     link with fmt="pdf,html" → stored as override
  12. dest bad fmt      link with fmt="xyz" → 400 (validation)      [bug fix]
  13. dest unlink       unlink destination → 204
  14. render json       POST …/render?format=json → 200 + valid JSON
  15. render html       POST …/render?format=html → 200 (template provided)
  16. render pdf        POST …/render?format=pdf → 200 (best-effort; WARN if
                        the PDF backend isn't installed)

Everything it creates (report, trigger, destination) is deleted at the end,
so the test is safe to run repeatedly against the live stack.

Usage
-----
  python scripts/report_config_smoke.py --password <admin-password>

  # or via env:
  SMOKE_PASS=... python scripts/report_config_smoke.py

Options
  --base       backend base URL          (default http://127.0.0.1:8000  or $SMOKE_BASE)
  --user       username                  (default admin                  or $SMOKE_USER)
  --password   password                  (required;                          $SMOKE_PASS)
  --keep       do not delete created entities (debugging)
  --no-color   plain output

Stdlib only — no pip install required. Exit code = number of failed checks.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlreq


# --------------------------------------------------------------------------- #
# Tiny HTTP client (stdlib, bearer auth, raw-bytes support for render)
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} from {url}: {body[:400]}")

    def detail(self) -> str:
        try:
            return json.loads(self.body).get("detail", self.body)
        except Exception:
            return self.body


class Api:
    def __init__(self, base: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def _url(self, path: str, params: dict | None = None) -> str:
        url = f"{self.base}{path}" if path.startswith("/") else f"{self.base}/{path}"
        if params:
            # keep empty strings (we deliberately send ?fmt= for the default case),
            # drop only None
            kept = {k: v for k, v in params.items() if v is not None}
            url += "?" + urlparse.urlencode(kept)
        return url

    def _headers(self, body: bytes | None) -> dict:
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request(self, method: str, path: str, *, params=None, body=None):
        """Return parsed JSON (or None for 204). Raises ApiError on non-2xx."""
        status, ctype, raw = self.raw(method, path, params=params, body=body)
        if not raw:
            return None
        if "application/json" in ctype:
            return json.loads(raw.decode("utf-8"))
        return raw

    def raw(self, method: str, path: str, *, params=None, body=None):
        """Return (status, content_type, body_bytes). Raises ApiError on non-2xx."""
        url = self._url(path, params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urlreq.Request(url, data=data, headers=self._headers(data), method=method)
        try:
            with urlreq.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urlerror.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise ApiError(e.code, err, url) from None

    # convenience verbs
    def get(self, path, **params):
        return self.request("GET", path, params=params or None)

    def post(self, path, body=None, **params):
        return self.request("POST", path, params=params or None, body=body)

    def put(self, path, body=None, **params):
        return self.request("PUT", path, params=params or None, body=body)

    def patch(self, path, body=None, **params):
        return self.request("PATCH", path, params=params or None, body=body)

    def delete(self, path, **params):
        return self.request("DELETE", path, params=params or None)

    def wait_ready(self, max_sec: int = 30) -> bool:
        deadline = time.time() + max_sec
        while time.time() < deadline:
            try:
                self.get("/health")
                return True
            except Exception:
                time.sleep(1.0)
        return False

    def login(self, username: str, password: str) -> dict:
        resp = self.post("/api/auth/login", {"username": username, "password": password})
        self.token = resp["access_token"]
        return resp


# --------------------------------------------------------------------------- #
# Test harness
# --------------------------------------------------------------------------- #
class Runner:
    def __init__(self, color: bool):
        self.color = color
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.warned = 0

    def _c(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def check(self, name: str, fn):
        """Run fn(); PASS if it returns truthy/None, FAIL on exception/AssertionError.
        fn may return the string 'SKIP: reason' or 'WARN: reason'."""
        try:
            result = fn()
            if isinstance(result, str) and result.startswith("SKIP"):
                self.skipped += 1
                print(f"  {self._c('33', '[SKIP]')} {name} — {result[5:].strip()}")
                return None
            if isinstance(result, str) and result.startswith("WARN"):
                self.warned += 1
                print(f"  {self._c('33', '[WARN]')} {name} — {result[5:].strip()}")
                return None
            self.passed += 1
            extra = f" — {result}" if isinstance(result, str) else ""
            print(f"  {self._c('32', '[PASS]')} {name}{extra}")
            return result
        except AssertionError as e:
            self.failed += 1
            print(f"  {self._c('31', '[FAIL]')} {name} — {e}")
        except ApiError as e:
            self.failed += 1
            print(f"  {self._c('31', '[FAIL]')} {name} — HTTP {e.status}: {e.detail()}")
        except Exception as e:
            self.failed += 1
            print(f"  {self._c('31', '[FAIL]')} {name} — {type(e).__name__}: {e}")
        return None

    def section(self, title: str):
        print("\n" + self._c("1;36", f"== {title} =="))

    def summary(self) -> int:
        print("\n" + self._c("1", "—" * 60))
        line = (f"PASS {self.passed}   FAIL {self.failed}   "
                f"SKIP {self.skipped}   WARN {self.warned}")
        col = "32" if self.failed == 0 else "31"
        print(self._c(col, line))
        if self.failed == 0:
            print(self._c("32", "Report Config smoke: GREEN"))
        else:
            print(self._c("31", f"Report Config smoke: {self.failed} FAILURE(S)"))
        return self.failed


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="InduVista Report Config smoke test")
    ap.add_argument("--base", default=os.getenv("SMOKE_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--user", default=os.getenv("SMOKE_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SMOKE_PASS"))
    ap.add_argument("--keep", action="store_true", help="do not delete created entities")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    color = (not args.no_color) and sys.stdout.isatty()
    r = Runner(color)
    api = Api(args.base)

    print(r._c("1", f"InduVista Report Config smoke  →  {args.base}  (user: {args.user})"))

    if not args.password:
        print(r._c("31", "ERROR: no password. Pass --password or set SMOKE_PASS."))
        return 2

    # created-entity ids for cleanup
    created = {"def": None, "trigger": None, "dest": None}

    # ---- connectivity + auth --------------------------------------------- #
    r.section("Connectivity & auth")
    if not api.wait_ready(30):
        print(r._c("31", f"ERROR: backend not reachable at {args.base} (is the stack up?)"))
        return 2
    r.check("health endpoint ok",
            lambda: (api.get("/health")["status"] == "ok") or _fail("status != ok"))
    r.check("login mints bearer token",
            lambda: bool(api.login(args.user, args.password).get("access_token")) or _fail("no token"))

    # ---- enumerate ALL OPC UA tags --------------------------------------- #
    r.section("OPC UA tag enumeration")
    opc_tag_ids: list[int] = []

    def _gather_opc():
        sources = api.get("/api/opc-sources")
        if not sources:
            return "SKIP no OPC sources configured — OPC-tag checks skipped"
        seen: set[int] = set()
        for s in sources:
            dev = s["device_id"]
            tags = api.get("/api/tags", device_id=dev, limit=1000)
            for t in tags:
                if t["id"] not in seen:
                    seen.add(t["id"])
                    opc_tag_ids.append(t["id"])
        opc_tag_ids.sort()
        if not opc_tag_ids:
            return "SKIP OPC source(s) present but no tags bound — nothing to add"
        return f"{len(sources)} source(s), {len(opc_tag_ids)} OPC tag(s): {opc_tag_ids}"

    r.check("enumerate OPC sources + their tags", _gather_opc)
    have_opc = len(opc_tag_ids) > 0

    # ---- create a throwaway report --------------------------------------- #
    r.section("Report definition lifecycle")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_name = f"__SMOKE__ OPC All Tags {stamp}"
    template = (
        "<h1>{{ report.name }}</h1>\n"
        "<p>Generated {{ report.generated_at }}</p>\n"
        "<table>{% for t in tags_list %}"
        "<tr><td>{{ t.name }}</td><td>{{ t.display }}</td></tr>"
        "{% endfor %}</table>"
    )

    def _create():
        d = api.post("/api/report-config/definitions", {
            "name": report_name,
            "description": "Automated smoke test — safe to delete.",
            "category": "on_demand",
            "report_type": "current",
            "template_html": template,
            "page_size": "A4",
            "orientation": "portrait",
            "enabled": True,
        })
        created["def"] = d["id"]
        assert d["name"] == report_name, "name not echoed"
        return f"id={d['id']}"
    r.check("create report definition", _create)

    def _get():
        d = api.get(f"/api/report-config/definitions/{created['def']}")
        assert d["category"] == "on_demand", "category mismatch"
        assert d["template_html"], "template not stored"
        return "fields echoed"
    if created["def"]:
        r.check("get report definition", _get)

    # ---- ADD ALL OPC TAGS + verify (centerpiece) ------------------------- #
    r.section("Add ALL OPC UA tags to the report")

    def _add_tags():
        if not have_opc:
            return "SKIP no OPC tags to add"
        api.put(f"/api/report-config/definitions/{created['def']}/tags",
                {"tag_ids": opc_tag_ids})
        return f"PUT {len(opc_tag_ids)} tag(s)"
    r.check("attach all OPC tags to report", _add_tags)

    def _verify_tags():
        if not have_opc:
            return "SKIP no OPC tags to verify"
        rows = api.get(f"/api/report-config/definitions/{created['def']}/tags")
        got = sorted(row["tag_id"] for row in rows)
        assert got == opc_tag_ids, (
            f"tag set mismatch: expected {len(opc_tag_ids)} ({opc_tag_ids}), "
            f"got {len(got)} ({got})")
        return f"all {len(got)} OPC tags present and correct"
    if created["def"]:
        r.check("verify report tag set == all OPC tags", _verify_tags)

    # ---- trigger link/unlink --------------------------------------------- #
    r.section("Triggers")

    def _make_trigger():
        t = api.post("/api/report-config/triggers", {
            "name": f"__SMOKE__ hourly {stamp}",
            "trigger_type": "timed",
            "period": "hourly",
            "at_minute": 0,
            "enabled": True,
        })
        created["trigger"] = t["id"]
        return f"id={t['id']}"
    r.check("create timed trigger", _make_trigger)

    def _link_trigger():
        api.put(f"/api/report-config/definitions/{created['def']}/triggers/{created['trigger']}")
        d = api.get(f"/api/report-config/definitions/{created['def']}")
        assert created["trigger"] in d["trigger_ids"], "trigger not linked"
        return "linked + visible on definition"
    if created["def"] and created["trigger"]:
        r.check("link trigger to report", _link_trigger)

    def _unlink_trigger():
        api.delete(f"/api/report-config/definitions/{created['def']}/triggers/{created['trigger']}")
        d = api.get(f"/api/report-config/definitions/{created['def']}")
        assert created["trigger"] not in d["trigger_ids"], "trigger still linked"
        return "unlinked"
    if created["def"] and created["trigger"]:
        r.check("unlink trigger from report", _unlink_trigger)

    # ---- destinations (incl. the fmt bug fix) ---------------------------- #
    r.section("Destinations (default / override / validation)")

    def _make_dest():
        d = api.post("/api/report-config/destinations", {
            "name": f"__SMOKE__ archive {stamp}",
            "dest_type": "folder",
            "target": "/tmp/induvista_smoke",
            "default_fmts": "pdf",
            "enabled": True,
        })
        created["dest"] = d["id"]
        return f"id={d['id']}"
    r.check("create folder destination", _make_dest)

    def _link_default():
        # The exact UI "use default" call: ?fmt= (empty). Pre-fix this 400'd.
        api.put(f"/api/report-config/definitions/{created['def']}/destinations/{created['dest']}", fmt="")
        d = api.get(f"/api/report-config/definitions/{created['def']}")
        assert created["dest"] in d["destination_ids"], "destination not linked"
        # destination_fmts keys come back as strings in JSON
        ov = d["destination_fmts"].get(str(created["dest"]), None)
        assert ov in ("", None), f"expected no override, got {ov!r}"
        return "linked with default formats (no override)"
    if created["def"] and created["dest"]:
        r.check("link destination with EMPTY fmt (default)", _link_default)

    def _link_override():
        api.put(f"/api/report-config/definitions/{created['def']}/destinations/{created['dest']}",
                fmt="pdf,html")
        d = api.get(f"/api/report-config/definitions/{created['def']}")
        ov = d["destination_fmts"].get(str(created["dest"]))
        assert ov == "pdf,html", f"override not stored, got {ov!r}"
        return "override 'pdf,html' stored"
    if created["def"] and created["dest"]:
        r.check("link destination with override set", _link_override)

    def _link_bad():
        try:
            api.put(f"/api/report-config/definitions/{created['def']}/destinations/{created['dest']}",
                    fmt="xyz")
        except ApiError as e:
            assert e.status == 400, f"expected 400, got {e.status}"
            return "invalid fmt rejected with 400"
        raise AssertionError("invalid fmt 'xyz' was accepted (should be 400)")
    if created["def"] and created["dest"]:
        r.check("reject invalid fmt", _link_bad)

    def _unlink_dest():
        api.delete(f"/api/report-config/definitions/{created['def']}/destinations/{created['dest']}")
        d = api.get(f"/api/report-config/definitions/{created['def']}")
        assert created["dest"] not in d["destination_ids"], "destination still linked"
        return "unlinked"
    if created["def"] and created["dest"]:
        r.check("unlink destination", _unlink_dest)

    # ---- render ---------------------------------------------------------- #
    r.section("On-demand render")

    def _render(fmt: str, soft: bool = False):
        def fn():
            try:
                status, ctype, body = api.raw(
                    "POST", f"/api/report-config/definitions/{created['def']}/render", params={"format": fmt})
            except ApiError as e:
                if soft:
                    return f"WARN {fmt} render returned {e.status}: {e.detail()} (backend may lack the renderer)"
                raise
            assert status == 200, f"status {status}"
            assert body, "empty body"
            if fmt == "json":
                json.loads(body)  # must parse
            return f"{fmt} → {len(body)} bytes"
        return fn

    if created["def"]:
        r.check("render JSON", _render("json"))
        r.check("render HTML", _render("html"))
        r.check("render PDF (best-effort)", _render("pdf", soft=True))

    # ---- cleanup --------------------------------------------------------- #
    r.section("Cleanup")
    if args.keep:
        print(r._c("33", "  [SKIP] --keep set; leaving created entities in place:"))
        print(f"         report={created['def']} trigger={created['trigger']} dest={created['dest']}")
    else:
        for label, path in (
            ("delete report definition", created["def"] and f"/api/report-config/definitions/{created['def']}"),
            ("delete trigger", created["trigger"] and f"/api/report-config/triggers/{created['trigger']}"),
            ("delete destination", created["dest"] and f"/api/report-config/destinations/{created['dest']}"),
        ):
            if path:
                r.check(label, (lambda p=path: (api.delete(p), "removed")[1]))

    failures = r.summary()
    return min(failures, 120)


def _fail(msg: str):
    raise AssertionError(msg)


if __name__ == "__main__":
    sys.exit(main())
