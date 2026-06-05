#!/usr/bin/env python3
r"""
Build a feature-complete demo report from the FlowComp_001 device's tags.

Discovers the device + all its tags at runtime, assembles a blocks template
that exercises EVERY block type, creates the report, binds the tags, and writes
a rendered HTML preview you can open. Idempotent-ish: re-running creates a fresh
report (delete older copies in Report Config if you re-run).

Blocks used: header (custom title) · text (with {tag:Name}) · kpi_row ·
tag_table (data + formula + conditional formatting + numeric format) ·
columns (2-up, nested) · spacer · page_break · raw (monospace dense table with
status-flag suffix, the BP-metering style) · chart (placeholder).

Run on the host (stdlib only):
  $env:SMOKE_PASS="..."; python scripts\build_flowcomp_report.py
  # options: --device FlowComp_001  --name "FlowComp_001 Complex Demo"
"""
from __future__ import annotations
import argparse, json, os, sys, time
from urllib import error as urlerror, parse as urlparse, request as urlreq

RC = "/api/report-config"
DEVICE_DEFAULT = "FlowComp_001"
KEY_HINTS = ["ENERGY", "CALORIFIC", "DENSITY", "PRESSURE", "TEMPERATURE",
             "FLOW", "MASS", "METHANE", "VOLUME"]


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
        if params: url += "?" + urlparse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urlreq.Request(url, data=data, headers=self._h(data), method=method)
        try:
            with urlreq.urlopen(req, timeout=120) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urlerror.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace"), url)
    def req(self, method, path, params=None, body=None):
        st, ct, raw = self.raw(method, path, params=params, body=body)
        if not raw: return None
        return json.loads(raw.decode()) if "application/json" in ct else raw.decode()
    def login(self, u, p):
        self.token = self.req("POST", "/api/auth/login", body={"username": u, "password": p})["access_token"]


def short_label(name: str) -> str:
    s = name
    for p in ("CUR_", "CURRENT_", "CUR"):
        if s.upper().startswith(p):
            s = s[len(p):]; break
    return (s.replace("_", " ").strip().title() or name)[:22]


def pick_kpis(tags: list[dict], n: int = 4) -> list[dict]:
    chosen: list[dict] = []
    seen: set[int] = set()
    for h in KEY_HINTS:
        for t in tags:
            if t["id"] in seen:
                continue
            if h in t["name"].upper():
                chosen.append(t); seen.add(t["id"]); break
        if len(chosen) >= n:
            break
    for t in tags:
        if len(chosen) >= n:
            break
        if t["id"] not in seen:
            chosen.append(t); seen.add(t["id"])
    return chosen[:n]


# Raw block (monospace, dense, with the "x" status-flag suffix) — the escape
# hatch that proves the BP-metering layout is achievable today.
RAW_DENSE = """
<h3 style="font-family:'Courier New',monospace;font-size:12px;margin:8px 0 4px;
           border-bottom:2px solid #0040A0;color:#0040A0">CURRENT STATUS — ALL PARAMETERS (dense)</h3>
<table style="font-family:'Courier New',monospace;font-size:10px;width:100%;border-collapse:collapse">
  <tr style="border-bottom:1px solid #999"><th style="text-align:left">Parameter</th>
    <th style="text-align:right">Value</th><th style="text-align:left">&nbsp;Unit</th>
    <th style="text-align:center">Q</th></tr>
  {% for t in tags_list %}
  <tr><td>{{ t.name }}</td>
      <td style="text-align:right">{{ t.display }}</td>
      <td>&nbsp;{{ t.unit or '' }}</td>
      <td style="text-align:center;color:#c00">{{ 'x' if not t.quality_good else '' }}</td></tr>
  {% endfor %}
</table>
<p style="font-family:'Courier New',monospace;font-size:9px;color:#888">
  'x' marks a value whose quality is not Good (mirrors the metering-report status flag).</p>
"""


def build_blocks(tags: list[dict]) -> list[dict]:
    kpis = pick_kpis(tags, 4)
    first_name = (kpis[0]["name"] if kpis else (tags[0]["name"] if tags else "TAG"))
    return [
        {"id": "hdr", "type": "header", "title_source": "custom",
         "custom_title": "FLOWCOMP_001 — CURRENT STATUS METERING REPORT",
         "show_generated": True, "show_logo": False},
        {"id": "intro", "type": "text",
         "content": f"Live snapshot of all FlowComp_001 parameters. "
                    f"Primary reading ({short_label(first_name)}) is currently {{tag:{first_name}}}."},
        {"id": "kpis", "type": "kpi_row",
         "items": [{"tag_id": t["id"], "label": short_label(t["name"])} for t in kpis]},
        {"id": "sp1", "type": "spacer", "height_mm": 4},
        {"id": "maintbl", "type": "tag_table", "columns": [
            {"key": "name", "label": "Parameter", "kind": "data"},
            {"key": "display", "label": "Value", "kind": "data"},
            {"key": "unit", "label": "Unit", "kind": "data"},
            {"key": "quality", "label": "Quality", "kind": "data"},
            {"key": "x2", "label": "Value x2 (demo)", "kind": "formula",
             "formula": "{value} * 2", "fmt": "0.00",
             "conditional": {"op": "<", "value": 0, "class": "bad"}},
        ]},
        {"id": "pb1", "type": "page_break"},
        {"id": "rawdense", "type": "raw", "html": RAW_DENSE},
        {"id": "sp2", "type": "spacer", "height_mm": 4},
        {"id": "signrow", "type": "columns", "count": 2, "panels": [
            [{"id": "prep", "type": "text", "content": "Prepared by: ______________________"}],
            [{"id": "appr", "type": "text", "content": "Approved by: ______________________"}],
        ]},
        {"id": "trend", "type": "chart", "title": "Trend (rendering pending — TC-3)"},
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("SMOKE_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--user", default=os.getenv("SMOKE_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SMOKE_PASS"))
    ap.add_argument("--device", default=DEVICE_DEFAULT)
    ap.add_argument("--name", default="FlowComp_001 Complex Demo")
    ap.add_argument("--out", default="flowcomp_report_preview.html")
    args = ap.parse_args()
    if not args.password:
        print("ERROR: set SMOKE_PASS or pass --password."); return 1

    a = Api(args.base)
    a.login(args.user, args.password)
    print(f"Logged in as {args.user}.")

    devices = a.req("GET", "/api/devices") or []
    want = args.device.replace("_", "").replace(" ", "").lower()
    dev = next((d for d in devices
                if d["name"].replace("_", "").replace(" ", "").lower() == want), None)
    if not dev:
        dev = next((d for d in devices
                    if want in d["name"].replace("_", "").replace(" ", "").lower()), None)
    if not dev:
        print(f"Device '{args.device}' not found. Available devices:")
        for d in devices:
            print(f"   #{d['id']}  {d['name']}")
        return 2
    print(f"Device: {dev['name']} (#{dev['id']})")

    tags = a.req("GET", "/api/tags", params={"device_id": dev["id"], "limit": 1000}) or []
    tags = [{"id": t["id"], "name": t["name"]} for t in tags]
    if not tags:
        print("That device has no tags — bind some tags to it first."); return 3
    print(f"Found {len(tags)} tags. KPI tiles: {', '.join(short_label(t['name']) for t in pick_kpis(tags))}")

    blocks = build_blocks(tags)

    existing = a.req("GET", f"{RC}/definitions") or []
    match = next((d for d in existing if d.get("name") == args.name), None)
    if match:
        rid = match["id"]
        a.req("PATCH", f"{RC}/definitions/{rid}", body={
            "template_mode": "blocks", "template_blocks": blocks,
            "page_size": "A4", "orientation": "portrait",
        })
        print(f"Updated existing report '{args.name}' (#{rid}).")
    else:
        created = a.req("POST", f"{RC}/definitions", body={
            "name": args.name, "description": "Auto-generated feature-complete demo.",
            "category": "on_demand", "report_code": "FC001-CS",
            "area": "Metering Skid", "equipment": dev["name"],
            "template_mode": "blocks", "template_blocks": blocks,
            "page_size": "A4", "orientation": "portrait",
        })
        rid = created["id"]
        print(f"Created report '{args.name}' (#{rid}).")
    a.req("PUT", f"{RC}/definitions/{rid}/tags", body={"tag_ids": [t["id"] for t in tags]})
    print(f"Bound {len(tags)} tags.")

    html = a.req("POST", f"{RC}/definitions/{rid}/preview", body={
        "template_mode": "blocks", "template_blocks": blocks,
        "template_html": "", "page_size": "A4", "orientation": "portrait",
    })
    html = html if isinstance(html, str) else (html or "")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Preview HTML written to: {os.path.abspath(args.out)}")
    print(f"Open it in a browser, or open the report in the app:")
    print(f"   Report Config -> '{args.name}' -> Content tab -> Preview")
    print(f"   (download PDF/HTML from the Download control for final layout)")
    print("\nBlocks used: header, text, kpi_row, tag_table (data+formula+conditional+fmt),")
    print("page_break, raw (dense monospace + status flag), columns (2-up), chart, spacer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
