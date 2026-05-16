import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the InduVista Trend UI smoke suite.
 *
 * Default base URL is the local Vite dev server (5174). Override via:
 *   $env:INDUVISTA_URL = "http://localhost:5175"; npx playwright test
 *
 * Headed run (visible browser, slow-mo) for debugging:
 *   npx playwright test --headed --debug
 *
 * Update snapshots:
 *   npx playwright test --update-snapshots
 */
export default defineConfig({
  testDir: "./tests-e2e",
  fullyParallel: false,            // serial - we mutate localStorage + UI state
  timeout: 60_000,
  expect: { timeout: 8_000 },
  retries: process.env.CI ? 2 : 1, // 1 local retry hides occasional first-paint flakes
  workers: 1,                      // single worker - tests share UI session conceptually
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.INDUVISTA_URL ?? "http://localhost:5174",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
