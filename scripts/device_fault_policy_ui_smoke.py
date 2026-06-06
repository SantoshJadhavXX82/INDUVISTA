r"""Fully automated UI smoke for the device fault-policy form (Phase 2b).

Logs in via the API, injects the token into localStorage (skips the login UI),
opens Devices -> the simulator-fed device, and drives the Read-failure policy
section: asserts it renders, that the conditional fields appear per mode, saves
a Substitute policy, reopens to confirm it PERSISTED, then restores 'missing'.

Setup (host):
    pip install playwright ; playwright install chromium
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python scripts\device_fault_policy_ui_smoke.py
    #   optional: $env:HEADED = "1"   (watch it run)

Env: UI_BASE (http://localhost:5173), API_BASE (http://localhost:8000),
     SMOKE_USER (admin), SMOKE_PASS (required),
     DEVICE_NAME (FLOWCOMP_001), HEADED.
"""
import json, os, sys, urllib.request, urllib.error

UI_BASE = os.environ.get("UI_BASE", "http://localhost:5173").rstrip("/")
API_BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "FLOWCOMP_001")
TOKEN_KEY = "induvista:token"
HEADED = os.environ.get("HEADED") == "1"

passed = 0
failed = 0


def check(label, ok):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}")
    return ok


def api_login():
    data = json.dumps({"username": USER, "password": PASS}).encode()
    r = urllib.request.Request(API_BASE + "/api/auth/login", data=data, method="POST")
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read().decode())["access_token"]


def open_device(page, PWTimeout):
    """Open the device drawer by clicking its row in the list."""
    page.get_by_text(DEVICE_NAME, exact=True).first.click()
    page.get_by_text(f"Device: {DEVICE_NAME}").wait_for(state="visible", timeout=10000)


def expand_policy(page):
    """Make sure the Read-failure policy <details> is open."""
    summary = page.get_by_text("Read-failure policy")
    summary.scroll_into_view_if_needed()
    # Open the <details> if the mode select isn't already visible.
    if not page.locator("#fault_mode").is_visible():
        summary.click()
    page.locator("#fault_mode").wait_for(state="visible", timeout=8000)


def main():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        sys.exit("playwright not installed. Run: pip install playwright ; playwright install chromium")

    token = api_login()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not HEADED)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(f"window.localStorage.setItem('{TOKEN_KEY}', '{token}');")
        page = ctx.new_page()
        try:
            page.goto(UI_BASE + "/config/devices", wait_until="networkidle", timeout=30000)
            if "/login" in page.url:
                sys.exit("Redirected to /login — token injection failed (check UI_BASE / credentials).")
            page.get_by_text(DEVICE_NAME, exact=True).first.wait_for(state="visible", timeout=15000)
            check("Devices list rendered the target device", True)

            # ---- open + section present ------------------------------------
            open_device(page, PWTimeout)
            check("device drawer opened", page.get_by_text(f"Device: {DEVICE_NAME}").is_visible())
            check("Read-failure policy section present",
                  page.get_by_text("Read-failure policy").count() > 0)

            # ---- conditional fields per mode -------------------------------
            expand_policy(page)
            page.select_option("#fault_mode", "hold_last")
            check("hold_last reveals Hold duration field",
                  page.locator("#hold_mode").is_visible())
            page.select_option("#hold_mode", "max_age")
            check("max_age reveals Max hold (seconds) field",
                  page.locator("#max_hold_sec").is_visible())
            page.select_option("#fault_mode", "substitute")
            check("substitute reveals Substitute value field",
                  page.locator("#substitute_value").is_visible())

            # ---- save a Substitute policy ----------------------------------
            page.fill("#substitute_value", "12.5")
            page.get_by_role("button", name="Save changes").click()
            # drawer closes on success
            page.get_by_text(f"Device: {DEVICE_NAME}").wait_for(state="hidden", timeout=12000)
            check("Save changes closed the drawer (saved)", True)

            # ---- reopen + verify PERSISTENCE -------------------------------
            page.get_by_text(DEVICE_NAME, exact=True).first.wait_for(state="visible", timeout=10000)
            open_device(page, PWTimeout)
            expand_policy(page)
            mode = page.input_value("#fault_mode")
            check(f"reopened: fault_mode persisted as substitute (got '{mode}')", mode == "substitute")
            val = page.input_value("#substitute_value")
            check(f"reopened: substitute_value persisted (got '{val}')",
                  val not in ("", None) and abs(float(val) - 12.5) < 1e-6)

        finally:
            # ---- restore to 'missing' so the smoke leaves no policy --------
            try:
                if not page.get_by_text(f"Device: {DEVICE_NAME}").is_visible():
                    open_device(page, PWTimeout)
                expand_policy(page)
                page.select_option("#fault_mode", "missing")
                page.get_by_role("button", name="Save changes").click()
                page.get_by_text(f"Device: {DEVICE_NAME}").wait_for(state="hidden", timeout=12000)
                print("  [restore] device set back to 'missing'")
            except Exception as e:
                print(f"  [restore] could not auto-restore ({e}); set fault_mode='missing' manually")
            browser.close()

    print(f"\n{passed}/{passed + failed} UI checks passed.")
    print("RESULT:", "ALL GREEN" if failed == 0 else "FAILURES PRESENT")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
