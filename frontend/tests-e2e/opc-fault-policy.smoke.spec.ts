/**
 * Phase 2e.3 — device fault policy in the Devices UI for OPC sources.
 *
 * Proves the gate broadened from Modbus-only to acquired devices (Modbus +
 * OPC UA), in both the render path and the save path:
 *   1. an OPC UA device's form SHOWS the "Read-failure policy" section
 *   2. setting hold_last there PERSISTS (devices.fault_mode, verified via API)
 *   3. a computed device's form does NOT show the section (still excluded)
 *
 * SELF-CONTAINED: it uses devices already present in the dev DB rather than
 * seeding an OPC source (which needs an endpoint + mappings — too heavy for a
 * smoke). It skips gracefully if there is no OPC UA device, and restores the
 * device's original fault_mode in a finally block.
 *
 *   $env:SMOKE_ADMIN_PASSWORD="your-pw"
 *   $env:INDUVISTA_URL="http://localhost:5173"   # optional (Vite dev port)
 *   $env:INDUVISTA_API="http://localhost:8000"   # optional
 *   cd frontend; npx playwright test tests-e2e/opc-fault-policy.smoke.spec.ts
 *
 * ADJUST IF YOUR APP DIFFERS (each marked inline):
 *   - DEVICES_ROUTE — the route that renders the Devices list (default below)
 */
import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

const ADMIN_USER = process.env.SMOKE_ADMIN_USER ?? "admin";
const ADMIN_PASSWORD = process.env.SMOKE_ADMIN_PASSWORD ?? "";
const API = (process.env.INDUVISTA_API ?? "http://localhost:8000").replace(/\/$/, "");
const DEVICES_ROUTE = "/config/devices"; // ADJUST if your devices list lives elsewhere

type Device = { id: number; name: string; protocol: string; fault_mode?: string | null };

async function adminToken(req: APIRequestContext): Promise<string> {
  const r = await req.post(`${API}/api/auth/login`, {
    data: { username: ADMIN_USER, password: ADMIN_PASSWORD },
  });
  expect(r.status(), "admin login (set SMOKE_ADMIN_PASSWORD)").toBe(200);
  return (await r.json()).access_token as string;
}

async function listDevices(req: APIRequestContext, token: string): Promise<Device[]> {
  const r = await req.get(`${API}/api/devices`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.status(), "GET /api/devices").toBe(200);
  return (await r.json()) as Device[];
}

async function getDevice(req: APIRequestContext, token: string, id: number): Promise<Device> {
  const r = await req.get(`${API}/api/devices/${id}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.status()).toBe(200);
  return (await r.json()) as Device;
}

async function setFaultMode(req: APIRequestContext, token: string, id: number, mode: string) {
  await req.patch(`${API}/api/devices/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { fault_mode: mode },
  });
}

async function uiLogin(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).first().fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page, "should navigate off /login").not.toHaveURL(/\/login/, { timeout: 10_000 });
}

async function openDeviceForm(page: Page, name: string) {
  await page.goto(DEVICES_ROUTE, { waitUntil: "networkidle" });
  // Device rows are clickable (onClick -> setEditing(device)); click the name.
  await page.getByText(name, { exact: false }).first().click();
  // Form opens in a modal titled "Device: {name}".
  await expect(page.getByText(new RegExp(`Device:\\s*${name}`, "i"))).toBeVisible({ timeout: 10_000 });
}

test.describe("Phase 2e.3 — OPC device fault policy UI gate", () => {
  test("OPC shows Read-failure policy & persists; computed stays excluded", async ({ page, request }) => {
    const token = await adminToken(request);
    const devices = await listDevices(request, token);

    const opc = devices.find((d) => d.protocol === "opc_ua");
    const computed = devices.find((d) => d.protocol === "computed");
    test.skip(!opc, "no OPC UA device in this environment to test against");

    const original = (await getDevice(request, token, opc!.id)).fault_mode ?? "missing";

    try {
      await uiLogin(page, ADMIN_USER, ADMIN_PASSWORD);

      // (1) OPC device → the section is rendered (the broadened render gate).
      await openDeviceForm(page, opc!.name);
      const section = page.getByText(/Read-failure policy/i);
      await expect(section, "OPC device must show the fault-policy section").toBeVisible({
        timeout: 10_000,
      });

      // (2) Expand the <details> (collapsed by default when mode is 'missing'),
      // choose hold_last, save, and confirm it persisted via the API — the API
      // is the source of truth and also proves the save-path gate was broadened.
      await section.click(); // toggle the <details> open
      await page.getByLabel(/On read failure/i).selectOption("hold_last");
      await page.getByRole("button", { name: /^save/i }).click();
      await expect
        .poll(async () => (await getDevice(request, token, opc!.id)).fault_mode, {
          timeout: 10_000,
          message: "OPC device fault_mode should persist as hold_last",
        })
        .toBe("hold_last");

      // (3) Computed device → section must NOT render (still Modbus+OPC only).
      if (computed) {
        await openDeviceForm(page, computed.name);
        await expect(
          page.getByText(/Read-failure policy/i),
          "computed device must NOT show the fault-policy section",
        ).toHaveCount(0);
      }
    } finally {
      // Restore whatever the OPC device had before, regardless of outcome.
      await setFaultMode(request, token, opc!.id, original);
    }
  });
});
