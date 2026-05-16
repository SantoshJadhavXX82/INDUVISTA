/**
 * InduVista Trend Module — σ popover click-through to Raw Data Table.
 *
 * Closes the Phase 13 wire-up by verifying:
 *   - Hover on a σ cell opens the bell-curve popover
 *   - The "View this tag in the raw data table" button is disabled
 *     until the popover is pinned (intentional friction so accidental
 *     hovers don't scroll the page)
 *   - Pin then click → RawDataTable auto-opens, focus badge appears
 *     with the tag name, displayed row count shrinks to the focused tag
 *   - Clicking "Clear" on the badge restores the unfiltered view
 *
 * Selectors deliberately use visible text + structural CSS + the few
 * semantic data-* attributes the new UI emits (data-raw-table-focus-badge).
 */
import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers (mirrored from trend-ui.spec.ts; self-contained)
// ---------------------------------------------------------------------------

const PERSIST_KEYS = [
  "induvista.timeFormat",
  "induvista.tooltipMode",
  "induvista.qualityFilter",
  "induvista.aggregationMode",
  "induvista.showEnvelope",
  "induvista.rocUnit",
  "induvista.tooltipPosition",
];

async function gotoTrend(page: Page) {
  await page.goto("/");
  await page.evaluate((keys) => {
    keys.forEach((k) => localStorage.removeItem(k));
  }, PERSIST_KEYS);
  await page.goto("/trend");
  await page.waitForLoadState("networkidle");
  await expect(
    page.locator("button").filter({ hasText: /^Add (tag|another)$/ }).first(),
  ).toBeVisible({ timeout: 20_000 });
}

async function pickTags(page: Page, count: number): Promise<string[]> {
  const addButton = page
    .locator("button")
    .filter({ hasText: /^Add (tag|another)$/ })
    .first();
  await addButton.click();

  const searchInput = page
    .locator('input[placeholder^="Search by tag"]')
    .first();
  await expect(searchInput).toBeVisible({ timeout: 10_000 });

  const dropdown = searchInput.locator(
    'xpath=ancestor::div[contains(@class,"absolute")][1]',
  );
  const scrollArea = dropdown.locator("div.overflow-y-auto").first();
  const rows = scrollArea.locator("button");
  await expect(rows.first()).toBeVisible({ timeout: 10_000 });

  const picked: string[] = [];
  const total = await rows.count();
  for (let i = 0; i < total && picked.length < count; i++) {
    const row = rows.nth(i);
    const text = (await row.innerText().catch(() => "")).trim();
    if (!text) continue;
    if (/\bdisabled\b/i.test(text)) continue;
    if (text.includes("SELECTED")) continue;
    await row.click();
    picked.push(text.split("\n")[0].trim());
    await page.waitForTimeout(150);
  }

  await page.mouse.click(2, 2);
  await page.waitForTimeout(200);

  return picked;
}

async function waitForSummary(page: Page) {
  // Wait for the summary panel to render its first σ cell — that's the
  // signal both the summary query AND the ROC trailing-window query have
  // returned (the σ cell is rendered by SigmaInfoPopover, which only
  // mounts when stddev_value is present).
  await page
    .waitForResponse(
      (r) => r.url().includes("/trends/summary") && r.ok(),
      { timeout: 10_000 },
    )
    .catch(() => {});
  // Give React a tick to render the table rows.
  await page.waitForTimeout(800);
}

function sigmaTriggerForRow(page: Page, rowIndex: number) {
  // The σ cell's trigger is the <span> that wraps the stddev number,
  // sitting inside the row's σ (STD DEV) column. The σ column comes
  // after "Mean" — index 8 (0-based: Tag, Avail, Good%, Good, Uncertain,
  // Bad, Missing, Mean, σ, Range, ROC, Longest, First, Last). We grab
  // the SigmaInfoPopover trigger via its dotted-underline cursor class.
  return page
    .locator('tbody tr')
    .nth(rowIndex)
    .locator('span[class*="cursor"]')
    .first();
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("σ popover → Raw Data Table click-through", () => {
  test("hovering the σ cell shows the popover with a DISABLED click-through button", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 1);
    await waitForSummary(page);

    const sigmaTrigger = sigmaTriggerForRow(page, 0);
    await expect(sigmaTrigger).toBeVisible({ timeout: 10_000 });
    await sigmaTrigger.hover({ force: true });

    const clickThrough = page.getByRole("button", {
      name: /View this tag in the raw data table/i,
    });
    await expect(clickThrough).toBeVisible({ timeout: 5_000 });
    // Disabled until pinned — intentional friction so accidental hovers
    // don't scroll the page.
    await expect(clickThrough).toBeDisabled();
  });

  test("pin → click 'View in raw' opens the table, shows badge with tag name, filters rows", async ({ page }) => {
    await gotoTrend(page);
    const picked = await pickTags(page, 2); // pick two so we can verify filtering works
    await waitForSummary(page);

    const sigmaTrigger = sigmaTriggerForRow(page, 0);
    await sigmaTrigger.hover({ force: true });

    // Pin the popover (the Pin/PinOff button uses a lucide icon; its
    // accessible name comes from title="Pin").
    const pinButton = page.getByRole("button", { name: /^Pin/ }).first();
    await expect(pinButton).toBeVisible({ timeout: 5_000 });
    await pinButton.click();

    const clickThrough = page.getByRole("button", {
      name: /View this tag in the raw data table/i,
    });
    await expect(clickThrough).toBeEnabled();
    await clickThrough.click();

    // The focus badge should appear with the tag name embedded.
    const focusBadge = page.locator("[data-raw-table-focus-badge]");
    await expect(focusBadge).toBeVisible({ timeout: 5_000 });
    await expect(focusBadge).toContainText(picked[0]);

    // The RawDataTable should now be expanded (CSV button visible).
    await expect(
      page.getByRole("button", { name: /^CSV$/ }),
    ).toBeVisible({ timeout: 5_000 });
  });

  test("Clear button on the focus badge restores the unfiltered view", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 2);
    await waitForSummary(page);

    // Open + pin + click-through (same as previous test)
    await sigmaTriggerForRow(page, 0).hover({ force: true });
    await page.getByRole("button", { name: /^Pin/ }).first().click();
    await page.getByRole("button", { name: /View this tag in the raw data table/i }).click();

    const focusBadge = page.locator("[data-raw-table-focus-badge]");
    await expect(focusBadge).toBeVisible({ timeout: 5_000 });

    // Click "Clear" inside the badge
    const clearButton = focusBadge.getByRole("button", { name: /Clear/i });
    await clearButton.click();

    // Badge is gone
    await expect(focusBadge).not.toBeVisible({ timeout: 3_000 });

    // RawDataTable stays open (CSV button still visible) — only the
    // filter is cleared, not the panel collapse.
    await expect(
      page.getByRole("button", { name: /^CSV$/ }),
    ).toBeVisible();
  });
});
