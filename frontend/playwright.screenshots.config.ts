import { defineConfig, devices } from "@playwright/test";

/**
 * Dedicated config for capturing UI screenshots (screenshots.spec.ts).
 *
 * Differs from playwright.config.ts: it auto-starts the Vite dev server on
 * :5173 (matching vite.config.ts) via `webServer`, so a single command both
 * boots the app and captures every route:
 *
 *   npx playwright test --config playwright.screenshots.config.ts
 *
 * Screenshots land in frontend/screenshots/. The backend on :8000 is optional;
 * pages that need it just render their empty/error states.
 */
export default defineConfig({
  testDir: "./tests-e2e",
  testMatch: /screenshots\.spec\.ts/,
  fullyParallel: true,
  timeout: 60_000,
  workers: 4,
  reporter: [["list"]],
  outputDir: "../test-results-ui/screenshot-artifacts",
  use: {
    baseURL: process.env.INDUVISTA_URL ?? "http://localhost:5173",
    viewport: { width: 1440, height: 900 },
    navigationTimeout: 20_000,
  },
  webServer: process.env.INDUVISTA_URL
    ? undefined
    : {
        command: "npm run dev",
        url: "http://localhost:5173",
        reuseExistingServer: true,
        timeout: 60_000,
      },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
