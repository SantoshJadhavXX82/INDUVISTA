#!/usr/bin/env python3
r"""
INDUVISTA smoke test - fast automated health check after a change.

Runs four sections and prints a PASS/WARN/FAIL summary (exit 1 on any FAIL):

  1. Docker services    - core containers running (postgres, valkey, backend)
  2. Backend + RBAC      - API reachable; unauthenticated /api call is rejected
  3. Authenticated API   - (needs a token) current-values uses the fast path,
                           the computed-tags list loads, and for the first calc
                           the flow / used-by / inputs endpoints return valid
                           shapes. This exercises everything touched recently.
  4. Frontend typecheck  - `npm run build` compiles the TypeScript, which is
                           what catches the undefined-variable / type errors
                           behind the white-screen regressions (a plain runtime
                           click-through would NOT catch those before deploy).

TOKEN: section 3 needs a bearer token. Grab it from the browser:
  DevTools -> Application -> Local Storage -> key "induvista:token"
Then pass it via --token "<value>" or set INDUVISTA_TOKEN.
Without a token, section 3 is skipped (reported as WARN, not FAIL).

Usage (PowerShell):
    python smoke_test.py --token "<paste>"
    # or skip the slow frontend build:
    python smoke_test.py --token "<paste>" --skip-frontend
    # custom locations:
    python smoke_test.py --base-url http://localhost:8000 --frontend-dir C:\INDUVISTA\frontend
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


# ---- result tracking -------------------------------------------------------
PASS = 0
WARN = 0
FAIL = 0


def _c(text, color):
    # ANSI colors; harmless if the terminal ignores them.
    codes = {"green": "92", "yellow": "93", "red": "91", "cyan": "96", "bold": "1"}
    return f"\033[{codes.get(color,'0')}m{text}\033[0m"


def ok(msg):
    global PASS
    PASS += 1
    print("  " + _c("[PASS]", "green") + " " + msg)


def warn(msg):
    global WARN
    WARN += 1
    print("  " + _c("[WARN]", "yellow") + " " + msg)


def bad(msg):
    global FAIL
    FAIL += 1
    print("  " + _c("[FAIL]", "red") + " " + msg)


def section(title):
    print("\n" + _c(title, "cyan"))


# ---- HTTP helper (stdlib only) ---------------------------------------------
def http(url, token=None, method="GET", timeout=15):
    """Return (status:int, body:str). status=-1 means the request never
    reached a server (connection refused / DNS / timeout)."""
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return -1, str(e)


def parse_json(body):
    try:
        return json.loads(body)
    except Exception:
        return None


# ---- sections --------------------------------------------------------------
def check_docker():
    section("1) Docker services")
    try:
        out = subprocess.run(
            "docker ps --format \"{{.Names}}\"",
            shell=True, capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        bad(f"could not run docker: {e}")
        return
    if out.returncode != 0:
        bad(f"`docker ps` failed: {out.stderr.strip() or out.stdout.strip()}")
        return
    names = set(out.stdout.split())
    for core in ("svj_postgres", "svj_valkey", "svj_backend"):
        if core in names:
            ok(f"container {core} running")
        else:
            bad(f"container {core} NOT running")
    for opt in ("svj_modbus_worker", "svj_modbus_simulator_1", "svj_modbus_simulator_2"):
        if opt in names:
            ok(f"container {opt} running")
        else:
            warn(f"optional container {opt} not running (fine if its profile is off)")


def check_rbac(base):
    section("2) Backend reachable + RBAC active")
    status, _ = http(f"{base}/api/calc/current-values")
    if status == -1:
        bad(f"backend unreachable at {base} (is svj_backend up and port mapped?)")
    elif status == 401:
        ok("backend up; unauthenticated /api call rejected with 401 (RBAC active)")
    elif status == 200:
        warn("unauthenticated /api call returned 200 - RBAC may be disabled")
    else:
        warn(f"unauthenticated /api call returned unexpected {status}")


def _first_calc_id(base, token):
    for path in ("/api/calc/definitions", "/api/computed-tags"):
        status, body = http(f"{base}{path}", token=token)
        if status == 200:
            data = parse_json(body)
            items = data if isinstance(data, list) else (
                data.get("items") if isinstance(data, dict) else None)
            if isinstance(items, list):
                ok(f"calc list loaded from {path} ({len(items)} tags)")
                if items:
                    cid = items[0].get("id")
                    if cid is not None:
                        return cid, path
                return None, path
    bad("could not load a calc list from /api/calc/definitions or /api/computed-tags")
    return None, None


def check_auth_api(base, token):
    section("3) Authenticated API")
    if not token:
        warn("no token provided - skipping (set --token or INDUVISTA_TOKEN). "
             "Grab it from DevTools > Application > Local Storage > induvista:token")
        return

    # current-values + fast-path assertion
    status, body = http(f"{base}/api/calc/current-values", token=token)
    if status == 200:
        ok("current-values 200")
        j = parse_json(body)
        if isinstance(j, dict) and "values" in j:
            ok("current-values has values{}")
            src = j.get("_source")
            if src == "latest_tag_values":
                ok("current-values served from latest_tag_values (fast path)")
            else:
                warn(f"current-values _source={src!r} (expected latest_tag_values)")
        else:
            bad("current-values body missing values{}")
    elif status == 401:
        bad("current-values 401 - token rejected/expired; re-grab induvista:token")
    else:
        bad(f"current-values returned {status}")

    # list -> first id -> per-definition endpoints
    cid, _ = _first_calc_id(base, token)
    if cid is None:
        warn("no calc id available - skipping flow / used-by / inputs checks")
        return

    # flow
    status, body = http(f"{base}/api/calc/definitions/{cid}/flow", token=token)
    if status == 200 and isinstance(parse_json(body), dict):
        ok(f"flow 200 + JSON (id={cid})")
    else:
        bad(f"flow returned {status} (id={cid})")

    # used-by (the new endpoint) + shape
    status, body = http(f"{base}/api/calc/definitions/{cid}/used-by", token=token)
    if status == 200:
        j = parse_json(body)
        if isinstance(j, dict) and isinstance(j.get("consumers"), list):
            ok(f"used-by 200 + consumers[] (n={len(j['consumers'])}, id={cid})")
        else:
            bad(f"used-by 200 but missing consumers[] (id={cid})")
    else:
        bad(f"used-by returned {status} (id={cid})")

    # inputs
    status, body = http(f"{base}/api/calc/definitions/{cid}/inputs", token=token)
    if status == 200 and parse_json(body) is not None:
        ok(f"inputs 200 + JSON (id={cid})")
    else:
        bad(f"inputs returned {status} (id={cid})")


def check_frontend(frontend_dir, skip):
    section("4) Frontend typecheck / build")
    if skip:
        warn("skipped (--skip-frontend)")
        return
    if not os.path.isdir(frontend_dir):
        bad(f"frontend dir not found: {frontend_dir}")
        return
    print("  running `npm run build` (compiles TS - catches the errors behind white screens)...")
    try:
        out = subprocess.run(
            "npm run build",
            shell=True, cwd=frontend_dir, capture_output=True, text=True, timeout=600,
        )
    except Exception as e:
        bad(f"could not run npm: {e}")
        return
    if out.returncode == 0:
        ok("frontend build/typecheck succeeded")
    else:
        bad(f"frontend build/typecheck FAILED (exit {out.returncode})")
        tail = (out.stdout + "\n" + out.stderr).strip().splitlines()[-25:]
        for line in tail:
            print("    " + line)


def main():
    ap = argparse.ArgumentParser(description="INDUVISTA smoke test")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--token", default=os.environ.get("INDUVISTA_TOKEN", ""))
    ap.add_argument("--frontend-dir", default=r"C:\INDUVISTA\frontend")
    ap.add_argument("--skip-frontend", action="store_true")
    ap.add_argument("--skip-docker", action="store_true")
    args = ap.parse_args()

    print(_c("INDUVISTA smoke test", "bold"))
    print(f"base-url: {args.base_url}   token: {'set' if args.token else 'NOT set'}")

    if not args.skip_docker:
        check_docker()
    check_rbac(args.base_url)
    check_auth_api(args.base_url, args.token)
    check_frontend(args.frontend_dir, args.skip_frontend)

    print("\n" + _c("==== SUMMARY ====", "bold"))
    print("  " + _c(f"PASS: {PASS}", "green")
          + "   " + _c(f"WARN: {WARN}", "yellow")
          + "   " + _c(f"FAIL: {FAIL}", "red"))
    if FAIL:
        print(_c("RESULT: FAILED", "red"))
        sys.exit(1)
    print(_c("RESULT: PASSED", "green"))
    sys.exit(0)


if __name__ == "__main__":
    main()
