#!/usr/bin/env python3
"""
Sign-off / document-header smoke (Phase C) — against a RUNNING stack.

Throwaway report, self-cleaning. Proves the rendered output now carries the
report's identity (B2 fields) and the sign-off provenance of its active
revision:

  * before activation (live path): identity fields are present in JSON + XML,
    and signoff is null / <signoff status="draft"/>
  * after a revision is activated: signoff carries revision_no, status=active,
    prepared_by and approved_by (the admin who drafted + activated it)
  * identity survives into the snapshot path (read from the approved snapshot)
  * XML emits the identity metadata and a populated <signoff> element

Stdlib only. Run from the host:

  $env:SMOKE_PASS="..."; python scripts\\report_signoff_smoke.py
  # or: python scripts/report_signoff_smoke.py --password <admin-pw>

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


def render_xml(api, rid):
    st, ct, raw = api.raw("POST", f"/api/report-config/definitions/{rid}/render",
                          params={"format": "xml"})
    return raw.decode("utf-8")


def render_html(api, rid):
    st, ct, raw = api.raw("POST", f"/api/report-config/definitions/{rid}/render",
                          params={"format": "html"})
    return raw.decode("utf-8")


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
    print("Phase C smoke — document identity + sign-off")

    try:
        api.login(args.user, args.password)
        ok("login mints bearer token")
    except Exception as e:
        bad("login", str(e)); print("-" * 60); print("PASS 0 FAIL 1"); return 1

    # Unique report_code so reruns don't collide on the UNIQUE constraint.
    code = f"SIGNSMOKE-{os.getpid()}-{int(time.time())}"
    ident = {"report_code": code, "area": "Boiler House",
             "equipment": "BLR-01", "owner_dept": "Utilities"}

    rid = None
    try:
        tmpl = "{{ header_block() }}<table><tr><th>Tag</th></tr></table>{{ signoff_block() }}"
        d = api.req("POST", "/api/report-config/definitions",
                    body={"name": "SIGNOFF_SMOKE_TMP", "category": "on_demand",
                          "template_html": tmpl, "page_size": "A4", "orientation": "portrait",
                          **ident})
        rid = d["id"]
        ok("create report with identity", f"id={rid} code={code}")

        # --- live path: identity present, signoff null ---
        out = render_json(api, rid)
        rep = out.get("report") or {}
        id_ok = all(rep.get(k) == v for k, v in ident.items())
        (ok if id_ok else bad)("identity in JSON (live path)",
                               f"code={rep.get('report_code')} area={rep.get('area')} "
                               f"equip={rep.get('equipment')} dept={rep.get('owner_dept')}")
        so = out.get("signoff")
        (ok if so is None else bad)("signoff null before activation", f"signoff={so}")

        xml = render_xml(api, rid)
        (ok if f"<report_code>{code}</report_code>" in xml else bad)(
            "identity in XML metadata", "report_code element present" if code in xml else "missing")
        (ok if '<signoff status="draft"/>' in xml else bad)(
            "XML shows draft signoff", "draft element present")

        html = render_html(api, rid)
        band_ok = ('class="iv-report-header"' in html and code in html
                   and "&lt;header" not in html)
        (ok if band_ok else bad)("HTML header band renders (unescaped, with identity)",
                                 "header present" if band_ok else "missing/escaped")
        (ok if "Draft \u2014 not yet approved" in html else bad)(
            "HTML sign-off shows draft before activation")

        # --- activate a revision -> signoff populated ---
        rev = api.req("POST", f"/api/report-config/definitions/{rid}/revisions",
                      body={"notes": "phase C smoke"})
        rev_id = rev.get("id")
        rno = rev.get("revision_no")
        act = api.req("POST", f"/api/report-config/definitions/{rid}/revisions/{rev_id}/activate")
        (ok if act and act.get("status") == "active" else bad)(
            "activate revision", f"status={act and act.get('status')}")

        out2 = render_json(api, rid)
        so2 = out2.get("signoff") or {}
        sign_ok = (so2.get("status") == "active"
                   and so2.get("revision_no") == rno
                   and so2.get("approved_by") == args.user
                   and so2.get("prepared_by") == args.user)
        (ok if sign_ok else bad)("signoff populated after activation",
                                 f"rev={so2.get('revision_no')} status={so2.get('status')} "
                                 f"prepared_by={so2.get('prepared_by')} approved_by={so2.get('approved_by')}")

        rep2 = out2.get("report") or {}
        (ok if rep2.get("report_code") == code else bad)(
            "identity survives into snapshot path", f"code={rep2.get('report_code')}")

        xml2 = render_xml(api, rid)
        (ok if 'status="active"' in xml2 and f"<approved_by>{args.user}</approved_by>" in xml2 else bad)(
            "XML signoff populated", "active signoff with approver")

        html2 = render_html(api, rid)
        (ok if f"Approved by {args.user}" in html2 and "Revision 1" in html2 else bad)(
            "HTML sign-off footer shows approver after activation")

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
