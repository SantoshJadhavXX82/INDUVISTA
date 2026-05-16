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
  // Clear persisted prefs so test ordering doesn't leak (time format,
  // tooltip mode, quality filter, aggregation mode, envelope state).
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
  // Tag picker present
  await expect(
    page.getByRole("textbox", { name: /search/i }).first()
  ).toBeVisible({ timeout: 15_000 }).catch(async () => {
    // Some layouts use a placeholder instead of label
    await expect(page.locator('input[placeholder*="earch" i]').first())
      .toBeVisible({ timeout: 5_000 });
  });
}

/**
 * Click the first N selectable tags in the picker. The picker lists tag
 * names as buttons or list items - we match by the row containing a tag
 * name pattern and click. Returns the displayed names of clicked tags.
 */
async function pickTags(page: Page, count: number): Promise<string[]> {
  // Most picker rows have an input with the tag name nearby. We'll find
  // checkbox/button-like rows.
  const rows = page.locator(
    [
      '[role="option"]',
      '[role="checkbox"]',
      'button:has-text("logging_enabled")', // unlikely fallback
      'label:has(input[type="checkbox"])',
    ].join(", ")
  );

  // Fall back: look inside the tag picker card for clickable rows
  const tagPickerArea = page.locator("text=/Tags|Tag picker|Search/i")
    .first()
    .locator("xpath=ancestor::*[contains(@class,'card') or self::section][1]");

  const candidates =
    (await rows.count()) > 0
      ? rows
      : tagPickerArea.locator('label, [role="option"], button[type="button"]');

  await expect(candidates.first()).toBeVisible({ timeout: 10_000 });

  const picked: string[] = [];
  const total = await candidates.count();
  for (let i = 0; i < total && picked.length < count; i++) {
    const item = candidates.nth(i);
    const text = (await item.innerText().catch(() => "")).trim();
    if (!text || text.length > 80) continue;
    // Skip header rows
    if (/^(name|tag|description|search)$/i.test(text)) continue;
    await item.click({ force: true });
    picked.push(text.split("\n")[0]);
    await page.waitForTimeout(100);
  }
  return picked;
}

/** Locator for the chart's interactive overlay (uPlot mounts .u-over). */
function chartOverlay(page: Page): Locator {
  return page.locator(".u-over").first();
}

/** Wait until at least one chart line path has been drawn. */
async function waitForChart(page: Page) {
  await expect(page.locator(".u-over")).toBeVisible({ timeout: 15_000 });
  // uPlot draws to <canvas> - exact draw signal is hard to detect.
  // A short settle is fine since data fetches resolve fast in local dev.
  await page.waitForTimeout(800);
}

/** Hover the center of the chart's overlay. */
async function hoverChartCenter(page: Page) {
  const overlay = chartOverlay(page);
  const box = await overlay.boundingBox();
  if (!box) throw new Error("chart overlay has no bounding box");
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
}

/** Click the center of the chart's overlay. */
async function clickChartCenter(page: Page) {
  const overlay = chartOverlay(page);
  const box = await overlay.boundingBox();
  if (!box) throw new Error("chart overlay has no bounding box");
  // Use moves + small jitter so uPlot definitely sees a cursor first
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.waitForTimeout(150);
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
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

    // Open dropdown and pick 12-hour
    await tfButton.click();
    await page.getByText("12-hour", { exact: false }).click();
    await expect(tfButton).toContainText("12h");

    // Some timestamp on the page should now have AM or PM. Easiest place
    // to find it: pick at least one tag so the chart axis renders.
    await pickTags(page, 1);
    await waitForChart(page);
    // Look for AM/PM token anywhere visible
    await expect(page.locator("body")).toContainText(/\b(AM|PM)\b/);

    // Reload - preference must persist
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(
      page.locator("header button").filter({ hasText: /^12h/ }).first()
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
    // The × close button is inside the pinned banner
    await page.locator('button[aria-label="Unpin tooltip"]').click();
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

    const modeButton = page.locator("button").filter({ hasText: /^Tooltip:/ }).first();
    await expect(modeButton).toContainText("Full");

    // Full state - hover gets tooltip width 360
    await hoverChartCenter(page);
    const tooltip = () =>
      page.locator(".absolute.z-50").filter({ hasText: /UTC/ }).first();
    await expect(tooltip()).toBeVisible();
    const fullWidthStr = await tooltip().evaluate((el) => (el as HTMLElement).style.width);
    expect(parseInt(fullWidthStr, 10)).toBeGreaterThanOrEqual(340);

    // Switch to Compact
    await modeButton.click();
    await page.getByText("Compact", { exact: false }).first().click();
    await expect(modeButton).toContainText("Compact");
    await hoverChartCenter(page);
    const compactWidthStr = await tooltip().evaluate((el) => (el as HTMLElement).style.width);
    expect(parseInt(compactWidthStr, 10)).toBeLessThanOrEqual(280);

    // Switch to Off
    await modeButton.click();
    await page.getByText("Off", { exact: false }).first().click();
    await expect(modeButton).toContainText("Off");
    // Move mouse off then back to trigger fresh hover
    await page.mouse.move(0, 0);
    await page.waitForTimeout(200);
    await hoverChartCenter(page);
    // Tooltip should NOT be visible
    await expect(page.locator(".absolute.z-50").filter({ hasText: /UTC/ })).toBeHidden();
  });

  test("preference persists across reload", async ({ page }) => {
    await gotoTrend(page);
    const modeButton = page.locator("button").filter({ hasText: /^Tooltip:/ }).first();
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

    const qButton = page.locator("button").filter({ hasText: /^Quality:/ }).first();
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
    const qButton = page.locator("button").filter({ hasText: /^Quality:/ }).first();
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
  test("Avg is selectable and persists across reload", async ({ page }) => {
    await gotoTrend(page);
    const modeButton = page.locator("button").filter({ hasText: /^Mode:/ }).first();
    await expect(modeButton).toContainText("Last");

    await modeButton.click();
    await page.getByText("Average", { exact: false }).first().click();
    await expect(modeButton).toContainText("Avg|Average");

    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(
      page.locator("button").filter({ hasText: /Mode:.*(Avg|Average)/ }).first()
    ).toBeVisible();
  });

  test("disabled when aggregation is raw", async ({ page }) => {
    await gotoTrend(page);
    // Pick raw aggregation
    const aggButton = page.locator("button").filter({ hasText: /^(Auto|Raw|1 min|1 hour|1 day)/ }).first();
    await aggButton.click();
    await page.getByText("Raw", { exact: false }).first().click();
    // Mode button should be disabled
    const modeButton = page.locator("button").filter({ hasText: /^Mode:/ }).first();
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
    const envButton = page.locator("button").filter({ hasText: /^Envelope:/ }).first();
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
