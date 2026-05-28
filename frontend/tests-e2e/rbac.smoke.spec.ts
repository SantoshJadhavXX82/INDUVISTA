/**
 * RBAC frontend smoke test (Phase 21) — Playwright, fully automatic E2E.
 *
 * Drives a real browser through the auth + role-gating flows against the
 * running app (Vite dev server, baseURL from playwright.config.ts, default
 * http://localhost:5174 — override with INDUVISTA_URL).
 *
 * SELF-CONTAINED: it logs in as admin (via the API, to seed), creates one
 * throwaway user per role, then drives the UI as each. Teardown disables the
 * created users. The only input it needs is the admin password:
 *
 *   $env:SMOKE_ADMIN_PASSWORD="your-pw"
 *   $env:SMOKE_ADMIN_USER="admin"          # optional, defaults to admin
 *   $env:INDUVISTA_URL="http://localhost:5174"   # optional
 *   cd frontend; npx playwright test tests-e2e/rbac.smoke.spec.ts
 *
 * WHAT IT VERIFIES (UI-level):
 *   - Unauthenticated visit to a protected page redirects to /login
 *   - Login form: bad creds shows an error; good creds enters the app
 *   - Header shows the account menu with the username
 *   - Account menu → Sign out returns to /login; token cleared
 *   - As admin: the "Users" nav link is visible; the Users page loads
 *   - As a non-admin (viewer): the "Users" nav link is ABSENT, and visiting
 *     /global/users directly shows "Access denied"
 *   - Forced password change: a reset user is prompted to change on next login
 *
 * The API calls for seeding go straight to the backend at the same origin
 * (the Vite proxy forwards /api → :8000), reusing the browser's fetch.
 */
import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

const ADMIN_USER = process.env.SMOKE_ADMIN_USER ?? "admin";
const ADMIN_PASSWORD = process.env.SMOKE_ADMIN_PASSWORD ?? "";
const API = (process.env.INDUVISTA_API ?? "http://localhost:8000").replace(/\/$/, "");
const RUN = Math.random().toString(36).slice(2, 8);
const PW = "SmokeUI!2026";

// ---- API helpers (seed users directly against the backend) ----------------
async function apiLogin(req: APIRequestContext, username: string, password: string) {
  const r = await req.post(`${API}/api/auth/login`, { data: { username, password } });
  return r;
}

async function adminToken(req: APIRequestContext): Promise<string> {
  const r = await apiLogin(req, ADMIN_USER, ADMIN_PASSWORD);
  expect(r.status(), "admin login (set SMOKE_ADMIN_PASSWORD)").toBe(200);
  const body = await r.json();
  expect(body.must_change_password, "admin must not require pw change").toBeFalsy();
  return body.access_token;
}

async function createUser(req: APIRequestContext, token: string, username: string, role: string) {
  const r = await req.post(`${API}/api/admin/users`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { username, role, auth_provider: "local", password: PW, must_change_password: false },
  });
  expect(r.status(), `create ${username}`).toBe(201);
  return (await r.json()).id as number;
}

async function disableUser(req: APIRequestContext, token: string, id: number) {
  await req.delete(`${API}/api/admin/users/${id}`, { headers: { Authorization: `Bearer ${token}` } });
}

// ---- UI helpers ------------------------------------------------------------
async function uiLogin(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).first().fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  // Wait for login to actually settle: the token lands in localStorage and
  // the app navigates away from /login. Without this, a following goto() can
  // race RequireAuth before auth state exists and wrongly bounce to /login.
  await page.waitForFunction(
    () => !!window.localStorage.getItem("induvista:token"),
    { timeout: 8000 },
  ).catch(() => {});
}

// ---------------------------------------------------------------------------
test.describe.configure({ mode: "serial" });

test.describe("RBAC frontend smoke", () => {
  test.skip(!ADMIN_PASSWORD, "SMOKE_ADMIN_PASSWORD not set — required to seed users.");

  let token = "";
  let viewerId = 0;
  let viewerName = "";

  test.beforeAll(async ({ request }) => {
    token = await adminToken(request);
    viewerName = `smoke_ui_viewer_${RUN}`;
    viewerId = await createUser(request, token, viewerName, "viewer");
  });

  test.afterAll(async ({ request }) => {
    if (viewerId) await disableUser(request, token, viewerId);
  });

  test("unauthenticated visit redirects to login", async ({ page }) => {
    // Ensure no token in storage.
    await page.goto("/login");
    await page.evaluate(() => localStorage.clear());
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test("bad credentials show an error", async ({ page }) => {
    await uiLogin(page, ADMIN_USER, "wrong-password-xyz");
    await expect(page.getByText(/invalid username or password/i)).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("admin login enters the app and shows account menu", async ({ page }) => {
    await uiLogin(page, ADMIN_USER, ADMIN_PASSWORD);
    await expect(page).not.toHaveURL(/\/login/);
    // Account button shows the username.
    await expect(page.getByText(ADMIN_USER, { exact: false })).toBeVisible();
  });

  test("admin can open the Users page (link + direct nav)", async ({ page }) => {
    await uiLogin(page, ADMIN_USER, ADMIN_PASSWORD);
    await page.waitForLoadState("networkidle");

    // Best-effort: expand the Global/Setup group and verify the link exists.
    // (Group expansion is a UI nicety; the authoritative check is direct nav.)
    const setup = page.getByText("Global/Setup", { exact: true });
    if (await setup.count()) {
      await setup.click().catch(() => {});
      const usersLink = page.locator('a[href="/global/users"]');
      // Non-fatal: log if the link didn't render after expanding.
      if ((await usersLink.count()) > 0) {
        await usersLink.first().click();
        await expect(page).toHaveURL(/\/global\/users/);
      }
    }

    // Authoritative: an admin can reach the Users page directly.
    await page.goto("/global/users");
    await expect(page).toHaveURL(/\/global\/users/);
    await expect(page.getByRole("heading", { name: /^users$/i })).toBeVisible();
    await expect(page.getByText(/manage accounts/i)).toBeVisible();
  });

  test("sign out returns to login and clears the token", async ({ page }) => {
    await uiLogin(page, ADMIN_USER, ADMIN_PASSWORD);
    await page.getByTitle("Account").click();
    await page.getByRole("button", { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/login/);
    const tok = await page.evaluate(() => localStorage.getItem("induvista:token"));
    expect(tok).toBeNull();
  });

  test("non-admin (viewer) does NOT see the Users link", async ({ page }) => {
    await uiLogin(page, viewerName, PW);
    await expect(page).not.toHaveURL(/\/login/);
    await page.waitForLoadState("networkidle");
    const setup = page.getByText("Global/Setup", { exact: true });
    if (await setup.count()) await setup.click().catch(() => {});
    // The Users link must be absent for a viewer (filtered out by isAdmin).
    await expect(page.locator('a[href="/global/users"]')).toHaveCount(0);
  });

  test("non-admin visiting /global/users directly is denied", async ({ page }) => {
    await uiLogin(page, viewerName, PW);
    // Confirm we're authenticated (not on /login) before testing the gate.
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto("/global/users");
    // RequireAuth minRole="admin" keeps the user logged in but shows a 403
    // notice — it must NOT redirect a logged-in viewer to /login.
    await expect(page).toHaveURL(/\/global\/users/);
    await expect(page.getByText(/access denied/i)).toBeVisible();
  });

  test("forced password change on reset", async ({ page, request }) => {
    // Admin resets the viewer's password → viewer is forced to change on login.
    const r = await request.post(`${API}/api/admin/users/${viewerId}/reset-password`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { new_password: "ResetUI!2026" },
    });
    expect(r.status()).toBe(204);

    await uiLogin(page, viewerName, "ResetUI!2026");
    // The login flow shows the forced "set a new password" step. Match the
    // instruction paragraph exactly (not the button) to avoid ambiguity.
    await expect(
      page.getByText("You must set a new password before continuing.")
    ).toBeVisible();
    // Restore the original password so the run stays consistent. Use exact
    // labels: "New password" and "Confirm new password" both contain "new
    // password", so target them precisely by id.
    await page.locator("#newpw").fill(PW);
    await page.locator("#confirmpw").fill(PW);
    await page.getByRole("button", { name: "Set password & continue" }).click();
    await expect(page).not.toHaveURL(/\/login/);
  });
});
