#!/usr/bin/env python3
"""
RS-Lineage UI smoke (Playwright) — exercises the BROWSER interactions the API
smokes can't: the drag handle on block cards, and clicking a value to open the
provenance drawer.

It logs in via the API, injects the token into localStorage (skips the login
form), discovers a report that has blocks, opens it from the list BY NAME (the
app keeps report selection in state, not the URL), clicks Preview, clicks a
value in the preview iframe, and asserts the "Value provenance" drawer.
On failure it saves a screenshot next to this script.

SETUP (one time):
    pip install playwright
    playwright install chromium

RUN (PowerShell):
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\report_ui_smoke.py
    #   optional: $env:REPORT_NAME = "Fuel Gas - Current Report"   (pick a specific report)
    #   optional: $env:HEADED = "1"                                 (watch it run)

Env: UI_BASE (default http://localhost:5173), API_BASE (default http://localhost:8000),
     SMOKE_USER (default admin), SMOKE_PASS (required), REPORT_NAME (optional), HEADED.
"""
import json, os, sys, urllib.request, urllib.error

UI_BASE = os.environ.get("UI_BASE", "http://localhost:5173").rstrip("/")
API_BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
REPORT_NAME = os.environ.get("REPORT_NAME")
TOKEN_KEY = "induvista:token"
HEADED = os.environ.get("HEADED") == "1"
SHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_smoke_fail.png")


def _api(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API_BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(r, timeout=20) as resp:
        p = resp.read()
        return json.loads(p) if p else None


def get_token():
    try:
        return _api("POST", "/api/auth/login", body={"username": USER, "password": PASS})["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit(f"login failed (HTTP {e.code}): {e.read().decode()[:200]}")


def discover_report_name(token):
    """A report that has blocks (so the preview renders values)."""
    if REPORT_NAME:
        return REPORT_NAME
    defs = _api("GET", "/api/report-config/definitions", token=token) or []
    for d in defs:
        if d.get("template_mode") == "blocks" and d.get("template_blocks"):
            return d["name"]
    # fall back to the first report of any kind
    return defs[0]["name"] if defs else None


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) first.")
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        sys.exit("Playwright not installed. Run:\n  pip install playwright\n  playwright install chromium")

    token = get_token()
    name = discover_report_name(token)
    if not name:
        sys.exit("No report definitions found to test.")
    print(f"Target report: {name}")

    results = []

    def check(label, cond):
        results.append((label, bool(cond)))
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not HEADED)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(f"window.localStorage.setItem('{TOKEN_KEY}', '{token}');")
        page = ctx.new_page()
        ok = False
        try:
            page.goto(UI_BASE + "/reports/config", wait_until="networkidle", timeout=30000)
            if "/login" in page.url:
                sys.exit("Redirected to /login — token injection failed (check UI_BASE / credentials).")

            # Open the report from the list by its name (selection is state, not URL).
            row = page.locator("ul li button").filter(has_text=name)
            if row.count() == 0:
                row = page.locator("ul li button")  # fall back to first
            row.first.wait_for(state="visible", timeout=20000)
            row.first.click()
            check("opened report from the list", True)

            # The per-report editor defaults to the Content tab; click it to be sure.
            ctab = page.get_by_role("button", name="Content")
            if ctab.count() > 0:
                try: ctab.first.click()
                except Exception: pass

            # Drag handle present on block cards (Content tab).
            page.wait_for_timeout(500)
            check("block cards have a drag handle",
                  page.locator("[title='Drag to reorder']").count() > 0)

            # Click Preview / Refresh preview.
            import re
            prev = page.get_by_role("button", name=re.compile(r"preview", re.I))
            prev.first.wait_for(state="visible", timeout=20000)
            check("Preview button found", True)
            prev.first.click()

            # Wait for a value span inside the preview iframe, then click it.
            frame = page.frame_locator("iframe[title='Report preview']")
            value = frame.locator(".rpt-prov").first
            value.wait_for(state="visible", timeout=25000)
            check("preview rendered a value span", True)
            value.click()

            drawer = page.locator("[aria-label='Value provenance']")
            drawer.wait_for(state="visible", timeout=8000)
            check("clicking a value opens the provenance drawer", drawer.is_visible())
            txt = drawer.inner_text().lower()
            check("drawer shows Source tag + Quality + Origin",
                  all(s in txt for s in ("source tag", "quality", "origin")))

            page.locator("[aria-label='Close']").first.click()
            page.wait_for_timeout(300)
            check("drawer closes", page.locator("[aria-label='Value provenance']").count() == 0)

            ok = all(c for _, c in results)
        except PWTimeout as e:
            print(f"  [FAIL] timed out: {str(e)[:160]}")
            results.append(("interaction completed", False))
        except Exception as e:
            print(f"  [FAIL] {str(e)[:200]}")
            results.append(("interaction completed", False))
        finally:
            ok = all(c for _, c in results) and len(results) > 0
            if not ok:
                try:
                    page.screenshot(path=SHOT, full_page=True)
                    print(f"  screenshot saved: {SHOT}")
                except Exception:
                    pass
            browser.close()

    n = sum(1 for _, c in results if c)
    print(f"\n{n}/{len(results)} UI checks passed.")
    print("RESULT:", "ALL GREEN" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
