/**
 * InduVista UI screenshot capture.
 *
 * Walks every top-level route and saves a full-page PNG to ../screenshots/.
 * Not a smoke test - it asserts nothing about content, it just navigates and
 * captures whatever renders (including empty states when the backend on :8000
 * isn't running).
 *
 * Run with the dedicated config so the Vite dev server auto-starts:
 *   npx playwright test --config playwright.screenshots.config.ts
 *
 * Output: frontend/screenshots/<slug>.png
 */
import { test, type Page } from "@playwright/test";

// Each entry: [route, file slug]. Slugs avoid slashes so they're flat files.
const ROUTES: Array<[string, string]> = [
  ["/diagnostics", "diagnostics"],
  ["/dashboard", "dashboard"],
  ["/tags", "tag-explorer"],
  ["/data-gaps", "data-gaps"],
  ["/trend", "trend"],
  ["/alarms", "alarms"],
  ["/global/alarm-severities", "global-alarm-severities"],
  ["/global/alarm-types", "global-alarm-types"],
  ["/global/calc-blocks", "global-calc-blocks"],
  ["/audit-log", "audit-log"],
  ["/modbus/frames", "modbus-frames"],
  ["/modbus/registers", "modbus-registers"],
  ["/modbus/write-console", "modbus-write-console"],
  ["/modbus/write-audit", "modbus-write-audit"],
  ["/global/engineering-units", "global-engineering-units"],
  ["/global/groups", "global-groups"],
  ["/global/named-sets", "global-named-sets"],
  ["/global/duty-standby-values", "global-duty-standby-values"],
  ["/config/channels", "config-channels"],
  ["/config/devices", "config-devices"],
  ["/config/blocks", "config-blocks"],
];

async function settle(page: Page) {
  // networkidle can hang forever if the backend is down and the app keeps
  // retrying, so cap it and fall back to a fixed pause.
  await page.waitForLoadState("networkidle", { timeout: 5_000 }).catch(() => {});
  // Some pages show a "Loading…" placeholder briefly after networkidle while
  // they hydrate from a second fetch; give them room to paint final content.
  await page.waitForTimeout(1_800);
}

for (const [route, slug] of ROUTES) {
  test(`screenshot ${route}`, async ({ page }) => {
    await page.goto(route);
    await settle(page);
    const path = `screenshots/${slug}.png`;
    // Very tall pages exceed Chromium's max capture height; fall back to the
    // visible viewport rather than failing the whole run.
    await page
      .screenshot({ path, fullPage: true })
      .catch(() => page.screenshot({ path, fullPage: false }));
  });
}
