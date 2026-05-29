/**
 * Phase 23 per-control gating smoke test — Playwright E2E.
 *
 * Drives a real browser as each role (viewer / operator / engineer) and
 * asserts that gated controls are in the right state — proving the capability
 * model (view < operate < configure < administer) is wired correctly in the
 * UI. The backend independently enforces RBAC (test_rbac_smoke.py); this
 * verifies the front-of-house affordances match.
 *
 * SELF-CONTAINED: seeds throwaway users via the admin API, drives the UI,
 * hard-deletes them in teardown. Needs only the admin password:
 *
 *   $env:SMOKE_ADMIN_PASSWORD="your-pw"
 *   $env:INDUVISTA_URL="http://localhost:5173"     # optional (Vite dev)
 *   $env:INDUVISTA_API="http://localhost:8000"     # optional
 *   cd frontend; npx playwright test tests-e2e/gating.smoke.spec.ts
 *
 * WHAT IT VERIFIES (the capability tiers):
 *   viewer    — Tags 'Add tag' disabled
 *   operator  — Tags 'Add tag' + Alarms 'New rule' still disabled (can't
 *               configure), proving operate < configure
 *   engineer  — Tags 'Add tag' + Alarms 'New rule' ENABLED (configure tier)
 */
import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

const ADMIN_USER = process.env.SMOKE_ADMIN_USER ?? "admin";
const ADMIN_PASSWORD = process.env.SMOKE_ADMIN_PASSWORD ?? "";
const API = (process.env.INDUVISTA_API ?? "http://localhost:8000").replace(/\/$/, "");
const RUN = Math.random().toString(36).slice(2, 8);
const PW = "GateUI!2026";

async function adminToken(req: APIRequestContext): Promise<string> {
  const r = await req.post(`${API}/api/auth/login`, { data: { username: ADMIN_USER, password: ADMIN_PASSWORD } });
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

async function deleteUser(req: APIRequestContext, token: string, id: number) {
  await req.delete(`${API}/api/admin/users/${id}?hard=true`, { headers: { Authorization: `Bearer ${token}` } });
}

async function uiLogin(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).first().fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  // Wait for the token AND for the app to navigate off /login. Without both,
  // a following goto() can race RequireAuth and land on a half-rendered page.
  await page.waitForFunction(
    () => !!window.localStorage.getItem("induvista:token"),
    { timeout: 10000 },
  );
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10000 });
  await page.waitForLoadState("networkidle");
}

async function uiLogout(page: Page) {
  await page.evaluate(() => localStorage.clear());
}

let token = "";
const ids: Record<string, number> = {};

test.beforeAll(async ({ request }) => {
  test.skip(!ADMIN_PASSWORD, "SMOKE_ADMIN_PASSWORD not set");
  token = await adminToken(request);
  for (const role of ["viewer", "operator", "engineer"]) {
    ids[role] = await createUser(request, token, `gate_${role}_${RUN}`, role);
  }
});

test.afterAll(async ({ request }) => {
  if (!token) return;
  for (const role of Object.keys(ids)) {
    await deleteUser(request, token, ids[role]);
  }
});

async function dumpPage(page: Page, label: string) {
  // Diagnostic: print what the browser actually sees so a "not found" failure
  // tells us WHY (wrong page, no buttons, login, crash) instead of guessing.
  const url = page.url();
  const heading = await page.locator("h1, h2").allInnerTexts().catch(() => []);
  const buttons = await page.getByRole("button").allInnerTexts().catch(() => []);
  const bodyLen = (await page.locator("body").innerText().catch(() => "")).length;
  console.log(`\n[DUMP ${label}] url=${url}`);
  console.log(`[DUMP ${label}] headings=${JSON.stringify(heading)}`);
  console.log(`[DUMP ${label}] body chars=${bodyLen}`);
  console.log(`[DUMP ${label}] buttons(${buttons.length})=${JSON.stringify(buttons.slice(0, 40))}`);
}

async function addTagButton(page: Page) {
  await page.goto("/tags");
  await expect(page).toHaveURL(/\/tags/, { timeout: 10000 });
  await page.waitForLoadState("networkidle");
  const btn = page.getByRole("button", { name: /add tag/i }).first();
  if ((await btn.count()) === 0) {
    await dumpPage(page, "tags");
  }
  await expect(btn).toBeVisible({ timeout: 15000 });
  return btn;
}

async function newRuleButton(page: Page) {
  await page.goto("/alarms");
  await expect(page).toHaveURL(/\/alarms/, { timeout: 10000 });
  await page.waitForLoadState("networkidle");
  // 'New rule' lives on the Rules tab. The tab control may be a button or a
  // link/role=tab; try a few, non-fatally, then wait for the button.
  for (const name of [/^rules$/i, /rules/i]) {
    const tab = page.getByRole("tab", { name }).or(page.getByRole("button", { name })).first();
    if (await tab.count()) { await tab.click().catch(() => {}); break; }
  }
  const btn = page.getByRole("button", { name: /new rule/i }).first();
  if ((await btn.count()) === 0) {
    await dumpPage(page, "alarms");
  }
  await expect(btn).toBeVisible({ timeout: 15000 });
  return btn;
}

test.describe("viewer — read only", () => {
  test("Tags: Add tag is disabled", async ({ page }) => {
    test.skip(!ADMIN_PASSWORD, "no admin pw");
    await uiLogin(page, `gate_viewer_${RUN}`, PW);
    const btn = await addTagButton(page);
    await expect(btn).toBeVisible();
    await expect(btn).toBeDisabled();
    await uiLogout(page);
  });
});

test.describe("operator — operate tier (cannot configure)", () => {
  test("Tags: Add tag DISABLED for operator", async ({ page }) => {
    test.skip(!ADMIN_PASSWORD, "no admin pw");
    await uiLogin(page, `gate_operator_${RUN}`, PW);
    const btn = await addTagButton(page);
    await expect(btn).toBeVisible();
    await expect(btn).toBeDisabled();
    await uiLogout(page);
  });

  test("Alarms: New rule DISABLED for operator", async ({ page }) => {
    test.skip(!ADMIN_PASSWORD, "no admin pw");
    await uiLogin(page, `gate_operator_${RUN}`, PW);
    const nr = await newRuleButton(page);
    await expect(nr).toBeVisible();
    await expect(nr).toBeDisabled();
    await uiLogout(page);
  });
});

test.describe("engineer — configure tier", () => {
  test("Tags: Add tag is ENABLED", async ({ page }) => {
    test.skip(!ADMIN_PASSWORD, "no admin pw");
    await uiLogin(page, `gate_engineer_${RUN}`, PW);
    const btn = await addTagButton(page);
    await expect(btn).toBeVisible();
    await expect(btn).toBeEnabled();
    await uiLogout(page);
  });

  test("Alarms: New rule is ENABLED", async ({ page }) => {
    test.skip(!ADMIN_PASSWORD, "no admin pw");
    await uiLogin(page, `gate_engineer_${RUN}`, PW);
    const nr = await newRuleButton(page);
    await expect(nr).toBeVisible();
    await expect(nr).toBeEnabled();
    await uiLogout(page);
  });
});
