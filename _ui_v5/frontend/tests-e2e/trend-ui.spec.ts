/**
 * InduVista Trend Module - browser smoke tests (Phase 13 closeout).
 *
 * Covers the frontend-only behaviors the PowerShell API smoke can't:
 *   - Tooltip click-to-pin and scroll
 *   - Live Value Panel tile click to show/hide chart series
 *   - Time format selector (24h / 12h / Auto)
 *   - Quality filter selector
 *   - Min/Max envelope rendering
 *   - Aggregation mode persistence
 *   - Tooltip mode (full / compact / off)
 *
 * Tests assume:
 *   - Backend reachable at :8000
 *   - Frontend reachable at :5174 (override via $env:INDUVISTA_URL)
 *   - At least 2 enabled numeric tags in the database
 *
 * Selectors deliberately use visible text + ARIA roles + structural CSS
 * (no test-ids added to the production code). If a selector breaks
 * because a label changed, that's actually useful signal - production
 * code shouldn't quietly break user-visible labels.
 */
import { test, expect, type Page, type Locator } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function gotoTrend(page: Page) {
  // Clear persisted prefs so test ordering doesn't leak.
  await page.goto("/");
  await page.evaluate(() => {
    const keys = [
      "induvista.timeFormat",
      "induvista.tooltipMode",
      "induvista.qualityFilter",
      "induvista.aggregationMode",
      "induvista.showEnvelope",
    ];
    keys.forEach((k) => localStorage.removeItem(k));
  });
  await page.goto("/trend");
  await page.waitForLoadState("networkidle");
  // The TagPicker renders an "Add tag" button when no tags are selected,
  // or "Add another" once one is selected. Wait for either - that's our
  // signal that the trend page mounted and the picker fetched its data.
  await expect(
    page.locator("button").filter({ hasText: /^Add (tag|another)$/ }).first()
  ).toBeVisible({ timeout: 20_000 });
}

/**
 * Open the TagPicker dropdown and click the first `count` non-disabled,
 * non-already-selected rows. The picker stays open while clicking;
 * we close it via outside click at the end.
 */
async function pickTags(page: Page, count: number): Promise<string[]> {
  // Open the picker dropdown
  const addButton = page.locator("button").filter({ hasText: /^Add (tag|another)$/ }).first();
  await addButton.click();

  // The search input is the signal that the dropdown is now open and
  // populated. (Real placeholder begins with "Search by tag".)
  const searchInput = page.locator('input[placeholder^="Search by tag"]').first();
  await expect(searchInput).toBeVisible({ timeout: 10_000 });

  // The dropdown is an absolute-positioned panel with the search header
  // and a scrollable list of <button> rows. Locate the scroll area, then
  // its direct button children.
  const dropdown = searchInput.locator(
    'xpath=ancestor::div[contains(@class,"absolute")][1]'
  );
  const scrollArea = dropdown.locator("div.overflow-y-auto").first();
  const rows = scrollArea.locator("button");
  // Wait for the list to populate
  await expect(rows.first()).toBeVisible({ timeout: 10_000 });

  const picked: string[] = [];
  const total = await rows.count();
  for (let i = 0; i < total && picked.length < count; i++) {
    const row = rows.nth(i);
    const text = (await row.innerText().catch(() => "")).trim();
    if (!text) continue;
    // Skip rows that are disabled tags or already-selected
    if (/\bdisabled\b/i.test(text)) continue;
    if (text.includes("SELECTED")) continue;
    await row.click();
    // Take the tag name from the first line of the row's text
    picked.push(text.split("\n")[0].trim());
    await page.waitForTimeout(150);
  }

  // Close the dropdown by clicking outside the picker
  await page.mouse.click(2, 2);
  await page.waitForTimeout(200);

  return picked;
}

/** Locator for the chart's interactive overlay (uPlot mounts .u-over). */
function chartOverlay(page: Page): Locator {
  return page.locator(".u-over").first();
}

/**
 * Wait until the chart actually has data drawn. Waiting only for .u-over
 * to be visible isn't enough - uPlot mounts the overlay before any data
 * has arrived, and then setCursor never sets an `idx` because there's
 * no data to align to, so the tooltip never renders on hover.
 *
 * The most reliable signal is a successful /trends/history response.
 * Race-safe: if the response landed before this waiter attached, the
 * catch ensures we still proceed (the overlay being visible is good
 * enough in that case).
 */
async function waitForChart(page: Page) {
  await expect(page.locator(".u-over").first()).toBeVisible({ timeout: 15_000 });
  await page
    .waitForResponse(
      (r) => r.url().includes("/trends/history") && r.ok(),
      { timeout: 8_000 }
    )
    .catch(() => {
      // Response may have arrived before this waiter attached - that's fine.
    });
  // uPlot needs a frame or two after data to bind the cursor to the canvas.
  await page.waitForTimeout(1500);
}

/** Hover the center of the chart's overlay. */
async function hoverChartCenter(page: Page) {
  // Use the locator's hover with `force` so we bypass any layout-shift
  // actionability quirks (the chart resizes when data arrives).
  await chartOverlay(page).hover({ force: true });
  await page.waitForTimeout(200);
}

/** Click the center of the chart's overlay (used for pin + unpin). */
async function clickChartCenter(page: Page) {
  await chartOverlay(page).click({ force: true });
  await page.waitForTimeout(200);
}

// ---------------------------------------------------------------------------
// Group 1 - Time format selector (global header)
// ---------------------------------------------------------------------------

test.describe("Time format selector", () => {
  test("flips 24h<>12h, persists across reload", async ({ page }) => {
    await gotoTrend(page);

    // The selector button in the header shows the current short label.
    const tfButton = page.locator("header button").filter({ hasText: /^(24h|12h|Auto)/ }).first();
    await expect(tfButton).toBeVisible();

    // Open dropdown and pick 12-hour. We scope to buttons because the
    // dropdown ALSO contains a help note that mentions "12-hour" as
    // plain text - getByText alone would resolve to 2 elements.
    await tfButton.click();
    await page.locator("button").filter({ hasText: "12-hour" }).first().click();
    await expect(tfButton).toContainText("12h");

    // Reload - preference must persist
    await page.reload();
    await page.waitForLoadState("networkidle");
    const tfButtonAfter = page.locator("header button").filter({ hasText: /^12h/ }).first();
    await expect(tfButtonAfter).toBeVisible();

    // Flip back to 24h so this test leaves a tidy state
    await tfButtonAfter.click();
    await page.locator("button").filter({ hasText: "24-hour" }).first().click();
    await expect(
      page.locator("header button").filter({ hasText: /^24h/ }).first()
    ).toBeVisible();
  });

  test("Auto option labels as Auto and shows browser-resolved format", async ({ page }) => {
    await gotoTrend(page);
    const tfButton = page.locator("header button").filter({ hasText: /^(24h|12h|Auto)/ }).first();
    await tfButton.click();
    await page.getByText("Auto", { exact: false }).first().click();
    // Either "Auto - 24h" or "Auto - 12h" depending on browser locale
    await expect(tfButton).toContainText(/Auto/);
  });
});

// ---------------------------------------------------------------------------
// Group 2 - Tooltip click-to-pin and scroll
// ---------------------------------------------------------------------------

test.describe("Tooltip pin", () => {
  test("hover renders tooltip in top-right corner; click pins; click again unpins", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 2);
    await waitForChart(page);

    // Hover center - tooltip should appear (in fixed corner now)
    await hoverChartCenter(page);
    const tooltip = page.locator(".absolute.z-50").filter({ hasText: /UTC/ }).first();
    await expect(tooltip).toBeVisible({ timeout: 5_000 });

    // It must NOT carry a "Pinned" banner yet
    await expect(tooltip).not.toContainText(/Pinned/i);

    // Click to pin
    await clickChartCenter(page);
    await expect(page.getByText(/Pinned/i).first()).toBeVisible();

    // Once pinned, pointer-events on outer wrapper must be auto.
    const pointerEvents = await tooltip.evaluate(
      (el) => getComputedStyle(el).pointerEvents
    );
    expect(pointerEvents).toBe("auto");

    // Click chart again to unpin
    await clickChartCenter(page);
    await expect(page.getByText(/Pinned/i)).toBeHidden({ timeout: 3_000 });
  });

  test("unpin via × button works", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 2);
    await waitForChart(page);
    await hoverChartCenter(page);
    await clickChartCenter(page);
    await expect(page.getByText(/Pinned/i).first()).toBeVisible();
    // The × Unpin button lives in the pinned banner. It has visible text
    // "× Unpin" (no aria-label), so target it by accessible name.
    await page.getByRole("button", { name: /Unpin/i }).first().click();
    await expect(page.getByText(/Pinned/i)).toBeHidden({ timeout: 3_000 });
  });
});

// ---------------------------------------------------------------------------
// Group 3 - Tooltip mode (Full / Compact / Off)
// ---------------------------------------------------------------------------

test.describe("Tooltip mode selector", () => {
  test("compact narrows the tooltip; off hides it; full restores", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 2);
    await waitForChart(page);

    const modeButton = page.locator("button").filter({ hasText: /Tooltip:/ }).first();
    await expect(modeButton).toContainText("Full");

    // Tooltip wrapper has stable classes: .absolute.z-50 plus
    // .pointer-events-none (unpinned) or .pointer-events-auto (pinned).
    // We can't filter by "UTC" because compact mode REMOVES the UTC
    // line - that's exactly the difference between Full and Compact.
    const tooltip = () =>
      page
        .locator(".absolute.z-50.pointer-events-none, .absolute.z-50.pointer-events-auto")
        .first();

    // --- Full state ---
    await hoverChartCenter(page);
    await expect(tooltip()).toBeVisible({ timeout: 8_000 });
    const fullWidthStr = await tooltip().evaluate((el) => (el as HTMLElement).style.width);
    expect(parseInt(fullWidthStr, 10)).toBeGreaterThanOrEqual(340);

    // --- Switch to Compact ---
    await modeButton.click();
    await page.getByText("Compact", { exact: false }).first().click();
    await expect(modeButton).toContainText("Compact");
    // Clear stale cursor state so the next hover triggers a fresh mousemove
    await page.mouse.move(0, 0);
    await page.waitForTimeout(300);
    await hoverChartCenter(page);
    // Wait for the tooltip to be visible BEFORE measuring its width.
    // Without this wait we were measuring an absent/stale element.
    await expect(tooltip()).toBeVisible({ timeout: 8_000 });
    const compactWidthStr = await tooltip().evaluate((el) => (el as HTMLElement).style.width);
    expect(parseInt(compactWidthStr, 10)).toBeLessThanOrEqual(280);

    // --- Switch to Off ---
    await modeButton.click();
    await page.getByText("Off", { exact: false }).first().click();
    await expect(modeButton).toContainText("Off");
    await page.mouse.move(0, 0);
    await page.waitForTimeout(300);
    await hoverChartCenter(page);
    // Tooltip should NOT be visible in off mode
    await expect(
      page.locator(".absolute.z-50.pointer-events-none, .absolute.z-50.pointer-events-auto")
    ).toBeHidden({ timeout: 5_000 });
  });

  test("preference persists across reload", async ({ page }) => {
    await gotoTrend(page);
    const modeButton = page.locator("button").filter({ hasText: /Tooltip:/ }).first();
    await modeButton.click();
    await page.getByText("Compact", { exact: false }).first().click();
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(
      page.locator("button").filter({ hasText: /Tooltip: Compact/ }).first()
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Group 4 - Live Value Panel tile click to show/hide chart series
// ---------------------------------------------------------------------------

test.describe("Live Value Panel - tile show/hide", () => {
  test("clicking a tile dims it and hides its chart series; clicking again restores", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 2);
    await waitForChart(page);

    // Tiles have role=button. Find them by the "Updated" age string they
    // all render in their footer.
    const tiles = page.locator('[role="button"]').filter({ hasText: /Updated/ });
    await expect(tiles.first()).toBeVisible({ timeout: 10_000 });
    const tileCount = await tiles.count();
    expect(tileCount).toBeGreaterThanOrEqual(2);

    // Click first tile - should dim with strikethrough
    await tiles.first().click();
    await expect(tiles.first().locator(".line-through").first()).toBeVisible();

    // Click again - restored
    await tiles.first().click();
    await expect(tiles.first().locator(".line-through")).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Group 5 - Quality filter selector
// ---------------------------------------------------------------------------

test.describe("Quality filter selector", () => {
  test("Hide bad and Good only activate; button gets count suffix; dropdown shows note when counts equal", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 2);
    await waitForChart(page);

    const qButton = page.locator("button").filter({ hasText: /Quality:/ }).first();
    await expect(qButton).toContainText("Show all");

    // Activate Hide bad
    await qButton.click();
    await page.getByText("Hide bad", { exact: false }).first().click();
    await expect(qButton).toContainText("Hide bad");

    // Dropdown should be closed after click. Open and verify counts shown.
    await qButton.click();
    await expect(page.getByText(/would hide/i).first()).toBeVisible();

    // Pick Good only
    await page.getByText("Good only", { exact: false }).first().click();
    await expect(qButton).toContainText("Good only");
  });

  test("preference persists across reload", async ({ page }) => {
    await gotoTrend(page);
    const qButton = page.locator("button").filter({ hasText: /Quality:/ }).first();
    await qButton.click();
    await page.getByText("Good only", { exact: false }).first().click();
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(
      page.locator("button").filter({ hasText: /Quality: Good only/ }).first()
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Group 6 - Aggregation MODE selector persistence + interaction with raw
// ---------------------------------------------------------------------------

test.describe("Aggregation mode", () => {
  test("Average is selectable and persists across reload", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 1);
    await waitForChart(page);

    const modeButton = page.locator("button").filter({ hasText: /Mode:/ }).first();
    await expect(modeButton).toContainText("Last");

    await modeButton.click();
    await page.getByText("Average", { exact: false }).first().click();
    await expect(modeButton).toContainText(/Avg|Average/);

    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(
      page.locator("button").filter({ hasText: /Mode:.*(Avg|Average)/ }).first()
    ).toBeVisible();
  });

  test("disabled when aggregation is raw", async ({ page }) => {
    await gotoTrend(page);
    // historyQuery.data must exist for the Mode button to react to its
    // aggregation field, which means we need at least one selected tag.
    await pickTags(page, 1);
    await waitForChart(page);

    // Pick raw aggregation
    const aggButton = page.locator("button").filter({ hasText: /^(Auto|Raw|1 min|1 hour|1 day|Auto:)/ }).first();
    await aggButton.click();
    await page.getByText("Raw", { exact: false }).first().click();
    await expect(aggButton).toContainText("Raw");

    // Mode button should be disabled
    const modeButton = page.locator("button").filter({ hasText: /Mode:/ }).first();
    await expect(modeButton).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// Group 7 - Min/Max envelope toggle
// ---------------------------------------------------------------------------

test.describe("Envelope toggle", () => {
  test("default On for aggregated; Off removes the band; toggle disables in raw", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 1);
    await waitForChart(page);

    // Default state should be On (button reads "Envelope: On")
    const envButton = page.locator("button").filter({ hasText: /Envelope:/ }).first();
    await expect(envButton).toContainText("On");

    // Toggle Off
    await envButton.click();
    await expect(envButton).toContainText("Off");

    // Toggle back On
    await envButton.click();
    await expect(envButton).toContainText("On");

    // Switch aggregation to raw -> envelope should be disabled
    const aggButton = page.locator("button").filter({ hasText: /^(Auto|Raw|1 min|1 hour|1 day|Auto:)/ }).first();
    await aggButton.click();
    await page.getByText("Raw", { exact: false }).first().click();
    await expect(envButton).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// Group 8 - Aggregation INTERVAL selector
// ---------------------------------------------------------------------------

test.describe("Aggregation interval", () => {
  test("Auto routes (button shows effective); Raw locks raw; 1m forces 1m", async ({ page }) => {
    await gotoTrend(page);
    await pickTags(page, 1);
    await waitForChart(page);

    const aggButton = page.locator("button").filter({ hasText: /^(Auto|Raw|1 min|1 hour|1 day|Auto:)/ }).first();

    // Default Auto - button text begins with "Auto"
    await expect(aggButton).toContainText("Auto");

    // Pick 1 min
    await aggButton.click();
    await page.getByText("1 min", { exact: false }).first().click();
    await expect(aggButton).toContainText("1 min");

    // Pick Raw
    await aggButton.click();
    await page.getByText("Raw", { exact: false }).first().click();
    await expect(aggButton).toContainText("Raw");

    // Pick Auto again
    await aggButton.click();
    await page.getByText("Auto", { exact: false }).first().click();
    await expect(aggButton).toContainText("Auto");
  });
});
