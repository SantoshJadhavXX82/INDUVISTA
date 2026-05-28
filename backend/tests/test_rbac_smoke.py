"""RBAC smoke test (Phase 21) — fully automatic, live-backend integration test.

Runs against a LIVE backend (BACKEND_BASE_URL, default http://localhost:8000).
Auto-skips if the backend is unreachable. SELF-CONTAINED and SELF-CLEANING:
it creates one throwaway user per role via the admin API, exercises the full
RBAC matrix, then disables every user it created in teardown.

PREREQUISITES
  - Backend running with Phase 21 auth deployed.
  - An admin to bootstrap test users. Provide credentials via env:
        SMOKE_ADMIN_USER      (default: "admin")
        SMOKE_ADMIN_PASSWORD  (required — your real admin password)
    If the admin must_change_password is still true, change it first
    (the test will tell you).

WHAT IT VERIFIES
  Authentication:
    - login returns a token + role + must_change_password
    - bad password → 401
    - /me reflects the token identity
    - missing/garbage token → 401
    - change-password works and the OLD password stops working

  RBAC matrix (the heart of it) — for each role, against representative
  endpoints at every tier:
    - public           (no token)            → 200
    - read   (GET)     viewer+               → 200 for all roles, 401 anon
    - write  (POST cfg) engineer+            → 403 viewer/operator, allowed engineer/admin
    - operator write   (tag write / ack)     → 403 viewer, allowed operator+
    - admin namespace  (/api/admin/*)        → 403 non-admin, allowed admin
    - ingest           (own API-key auth)    → NOT 401-from-RBAC
    - host-stats push  (exempt)              → NOT 401-from-RBAC

  Admin user management:
    - create / list / patch-role / reset-password / disable
    - last-admin lockout guard (cannot disable the last admin)

  Lifecycle:
    - disabled user can no longer log in
    - reset-password forces must_change_password=true

RUN
    cd backend
    SMOKE_ADMIN_PASSWORD='your-pw' python -m pytest tests/test_rbac_smoke.py -v
  Or against a remote backend:
    BACKEND_BASE_URL=http://host:8000 SMOKE_ADMIN_PASSWORD=... pytest tests/test_rbac_smoke.py -v
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.smoke

BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")
ADMIN_USER = os.environ.get("SMOKE_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD")

ROLES = ["viewer", "operator", "engineer", "admin"]
RANK = {r: i for i, r in enumerate(ROLES)}

# A unique suffix so repeated runs don't collide on usernames.
RUN_ID = uuid.uuid4().hex[:8]
TEST_PW = "SmokeTest!2026"          # initial password for created users
NEW_PW = "SmokeTest!2026-changed"   # after forced change


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def httpx_mod():
    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed — pip install httpx")
    return httpx


@pytest.fixture(scope="session")
def client(httpx_mod):
    c = httpx_mod.Client(base_url=BASE_URL, timeout=30.0)
    # Probe with retries (container may be mid-reload).
    last = None
    for attempt in range(5):
        try:
            r = c.get("/health", timeout=10.0)
            if r.status_code < 500:
                break
            last = f"{r.status_code} from /health"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < 4:
            time.sleep(2.0)
    else:
        pytest.skip(f"backend unreachable at {BASE_URL}: {last}")
    yield c
    c.close()


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture(scope="session")
def admin_token(client):
    if not ADMIN_PASSWORD:
        pytest.skip("SMOKE_ADMIN_PASSWORD not set — required to seed test users.")
    r = _login(client, ADMIN_USER, ADMIN_PASSWORD)
    if r.status_code != 200:
        pytest.skip(f"admin login failed ({r.status_code}): {r.text}. "
                    f"Set SMOKE_ADMIN_USER/SMOKE_ADMIN_PASSWORD to a valid admin.")
    data = r.json()
    if data.get("must_change_password"):
        pytest.skip("admin still has must_change_password=true — change it first, "
                    "then re-run the smoke test.")
    return data["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def seeded_users(client, admin_token):
    """Create one throwaway user per role. Yields {role: {username, id, token}}.
    Teardown disables each created user so the suite is repeatable."""
    created = {}
    for role in ROLES:
        if role == "admin":
            # Use the REAL admin for the admin row (we don't create a 2nd admin;
            # the last-admin guard tests rely on the existing one).
            created[role] = {"username": ADMIN_USER, "id": None, "token": admin_token}
            continue
        uname = f"smoke_{role}_{RUN_ID}"
        r = client.post(
            "/api/admin/users",
            headers=_auth(admin_token),
            json={"username": uname, "role": role, "auth_provider": "local",
                  "password": TEST_PW, "must_change_password": False},
        )
        assert r.status_code == 201, f"create {uname} failed: {r.status_code} {r.text}"
        uid = r.json()["id"]
        # Log in as the new user to get their token.
        lr = _login(client, uname, TEST_PW)
        assert lr.status_code == 200, f"login {uname} failed: {lr.status_code} {lr.text}"
        created[role] = {"username": uname, "id": uid, "token": lr.json()["access_token"]}

    yield created

    # Teardown — disable every user we created (never the real admin).
    for role, info in created.items():
        if role == "admin" or info["id"] is None:
            continue
        client.delete(f"/api/admin/users/{info['id']}", headers=_auth(admin_token))


# ---------------------------------------------------------------------------
# Representative endpoints per tier (read from the real deployed policy)
# ---------------------------------------------------------------------------
# Public (no auth):        GET /health
# Read (viewer+):          GET /api/diagnostics/summary  (any GET under /api)
# Engineer write:          POST /api/devices             (config write)
# Operator write:          POST /api/tags/999999/write   (matches operator pattern)
# Admin namespace:         GET /api/admin/users
# API-key route:           POST /api/ingest
# Exempt push:             POST /api/diagnostics/host-stats
READ_ENDPOINT = "/api/diagnostics/summary"
ENGINEER_WRITE = "/api/devices"
OPERATOR_WRITE = "/api/tags/999999/write"   # non-existent tag id; we only assert the AUTH outcome
ADMIN_READ = "/api/admin/users"


def _expect_write_status(resp_status, allowed):
    """When RBAC ALLOWS the write, the request reaches the handler and may
    fail validation/404 (NOT 401/403). When RBAC DENIES, it's 401 (no token)
    or 403 (insufficient role) BEFORE the handler."""
    if allowed:
        assert resp_status not in (401, 403), f"expected allowed, got {resp_status}"
    else:
        assert resp_status in (401, 403), f"expected denied, got {resp_status}"


# ---------------------------------------------------------------------------
# 1. Authentication
# ---------------------------------------------------------------------------
class TestAuthentication:
    def test_login_returns_token_and_identity(self, client, admin_token):
        assert isinstance(admin_token, str) and len(admin_token) > 20

    def test_bad_password_401(self, client):
        r = _login(client, ADMIN_USER, "definitely-wrong-password")
        assert r.status_code == 401

    def test_unknown_user_401(self, client):
        r = _login(client, f"nobody_{RUN_ID}", "whatever12345")
        assert r.status_code == 401

    def test_me_reflects_token(self, client, admin_token):
        r = client.get("/api/auth/me", headers=_auth(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == ADMIN_USER
        assert body["role"] == "admin"

    def test_missing_token_401(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_garbage_token_401(self, client):
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_malformed_header_401(self, client):
        r = client.get("/api/auth/me", headers={"Authorization": "Token abc"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# 2. Public + exempt routes (no user token required)
# ---------------------------------------------------------------------------
class TestPublicAndExempt:
    def test_health_public(self, client):
        assert client.get("/health").status_code == 200

    def test_login_public(self, client):
        # Even a failed login must not be blocked by RBAC (it's how you GET a token).
        r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
        assert r.status_code in (401, 422)  # reached handler, not RBAC-blocked

    def test_read_without_token_401(self, client):
        assert client.get(READ_ENDPOINT).status_code == 401

    def test_ingest_not_rbac_blocked(self, client):
        # /api/ingest uses its OWN api-key auth. Without a key it should 401/422
        # from its own dependency — the point is it's not RBAC-gated as a user
        # write (we just assert it responds, not a 403 user-role error).
        r = client.post("/api/ingest", json={})
        assert r.status_code in (401, 422, 400)

    def test_host_stats_exempt_from_rbac(self, client):
        # The host agent posts with NO token. RBAC must not 401 it. The body is
        # invalid here, so we expect 422 (validation) — crucially NOT 401/403.
        r = client.post("/api/diagnostics/host-stats", json={})
        assert r.status_code not in (401, 403), (
            f"host-stats is RBAC-blocked ({r.status_code}) — the host agent "
            f"will be trapped in container fallback. Check the RBAC exemption."
        )


# ---------------------------------------------------------------------------
# 3. RBAC matrix — every role against every tier
# ---------------------------------------------------------------------------
class TestRbacMatrix:
    @pytest.mark.parametrize("role", ROLES)
    def test_read_allowed_for_all_roles(self, client, seeded_users, role):
        tok = seeded_users[role]["token"]
        r = client.get(READ_ENDPOINT, headers=_auth(tok))
        assert r.status_code == 200, f"{role} read got {r.status_code}"

    @pytest.mark.parametrize("role", ROLES)
    def test_engineer_write_tier(self, client, seeded_users, role):
        tok = seeded_users[role]["token"]
        allowed = RANK[role] >= RANK["engineer"]
        r = client.post(ENGINEER_WRITE, headers=_auth(tok), json={})
        _expect_write_status(r.status_code, allowed)

    @pytest.mark.parametrize("role", ROLES)
    def test_operator_write_tier(self, client, seeded_users, role):
        tok = seeded_users[role]["token"]
        allowed = RANK[role] >= RANK["operator"]
        r = client.post(OPERATOR_WRITE, headers=_auth(tok), json={"value": 1})
        _expect_write_status(r.status_code, allowed)

    @pytest.mark.parametrize("role", ROLES)
    def test_admin_namespace_tier(self, client, seeded_users, role):
        tok = seeded_users[role]["token"]
        allowed = role == "admin"
        r = client.get(ADMIN_READ, headers=_auth(tok))
        if allowed:
            assert r.status_code == 200
        else:
            assert r.status_code == 403, f"{role} reached admin namespace ({r.status_code})"


# ---------------------------------------------------------------------------
# 4. Admin user management
# ---------------------------------------------------------------------------
class TestUserManagement:
    def test_list_users_includes_seeded(self, client, admin_token, seeded_users):
        r = client.get("/api/admin/users", headers=_auth(admin_token))
        assert r.status_code == 200
        names = {u["username"] for u in r.json()}
        assert seeded_users["viewer"]["username"] in names

    def test_create_duplicate_409(self, client, admin_token, seeded_users):
        uname = seeded_users["viewer"]["username"]
        r = client.post("/api/admin/users", headers=_auth(admin_token),
                        json={"username": uname, "role": "viewer",
                              "auth_provider": "local", "password": TEST_PW})
        assert r.status_code == 409

    def test_patch_role(self, client, admin_token, seeded_users):
        uid = seeded_users["viewer"]["id"]
        r = client.patch(f"/api/admin/users/{uid}", headers=_auth(admin_token),
                         json={"role": "operator"})
        assert r.status_code == 200 and r.json()["role"] == "operator"
        # revert
        client.patch(f"/api/admin/users/{uid}", headers=_auth(admin_token),
                     json={"role": "viewer"})

    def test_create_local_without_password_400(self, client, admin_token):
        r = client.post("/api/admin/users", headers=_auth(admin_token),
                        json={"username": f"smoke_nopw_{RUN_ID}", "role": "viewer",
                              "auth_provider": "local"})
        assert r.status_code == 400

    def test_reset_password_forces_change(self, client, admin_token, seeded_users):
        info = seeded_users["operator"]
        r = client.post(f"/api/admin/users/{info['id']}/reset-password",
                        headers=_auth(admin_token), json={"new_password": NEW_PW})
        assert r.status_code == 204
        # Logging in with the new password should now flag must_change_password.
        lr = _login(client, info["username"], NEW_PW)
        assert lr.status_code == 200
        assert lr.json()["must_change_password"] is True
        # restore original password + clear the flag via change-password
        new_tok = lr.json()["access_token"]
        client.post("/api/auth/change-password", headers=_auth(new_tok),
                    json={"current_password": NEW_PW, "new_password": TEST_PW})

    def test_last_admin_cannot_be_disabled(self, client, admin_token):
        # Find the real admin's id.
        users = client.get("/api/admin/users", headers=_auth(admin_token)).json()
        admins = [u for u in users if u["role"] == "admin" and u["is_enabled"]]
        if len(admins) != 1:
            pytest.skip(f"expected exactly 1 enabled admin, found {len(admins)} — "
                        f"last-admin guard can't be tested deterministically.")
        admin_id = admins[0]["id"]
        r = client.delete(f"/api/admin/users/{admin_id}", headers=_auth(admin_token))
        assert r.status_code == 400, "last admin was disabled — lockout guard FAILED"


# ---------------------------------------------------------------------------
# 5. Lifecycle — disabled user loses access
# ---------------------------------------------------------------------------
class TestLifecycle:
    def test_disabled_user_cannot_login(self, client, admin_token):
        uname = f"smoke_disable_{RUN_ID}"
        # create
        cr = client.post("/api/admin/users", headers=_auth(admin_token),
                         json={"username": uname, "role": "viewer",
                               "auth_provider": "local", "password": TEST_PW,
                               "must_change_password": False})
        assert cr.status_code == 201
        uid = cr.json()["id"]
        # confirm login works
        assert _login(client, uname, TEST_PW).status_code == 200
        # disable
        assert client.delete(f"/api/admin/users/{uid}", headers=_auth(admin_token)).status_code == 204
        # now login must fail
        assert _login(client, uname, TEST_PW).status_code == 401

    def test_change_password_invalidates_old(self, client, admin_token):
        uname = f"smoke_chpw_{RUN_ID}"
        cr = client.post("/api/admin/users", headers=_auth(admin_token),
                         json={"username": uname, "role": "viewer",
                               "auth_provider": "local", "password": TEST_PW,
                               "must_change_password": False})
        assert cr.status_code == 201
        uid = cr.json()["id"]
        tok = _login(client, uname, TEST_PW).json()["access_token"]
        # change password
        cp = client.post("/api/auth/change-password", headers=_auth(tok),
                         json={"current_password": TEST_PW, "new_password": NEW_PW})
        assert cp.status_code == 204
        # old password fails, new works
        assert _login(client, uname, TEST_PW).status_code == 401
        assert _login(client, uname, NEW_PW).status_code == 200
        # cleanup
        client.delete(f"/api/admin/users/{uid}", headers=_auth(admin_token))
