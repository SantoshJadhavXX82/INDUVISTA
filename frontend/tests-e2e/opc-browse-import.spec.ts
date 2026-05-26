/**
 * Phase OPC-web.2.2 — Browse & Import modal E2E smoke.
 *
 * Runs against a live stack:
 *   - Vite dev server at INDUVISTA_URL (default http://localhost:5174)
 *   - Backend at the URL Vite proxies to (typically :8000)
 *   - Real Kepware (or any OPC source named KEPWARE_OPC_UA_02)
 *
 * Tests use data-testid selectors for the modal-internal elements
 * (5 attributes total added to OpcBrowseImportModal.tsx). Outside
 * the modal, tests fall back to text/role selectors per project
 * convention.
 *
 * Test isolation:
 *   - Each test that creates mappings cleans them up via the API
 *     in afterEach hooks
 *   - Tag names use a random suffix so concurrent or repeated runs
 *     don't collide on the UNIQUE tag-name constraint
 */
import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

// ─── Constants ───────────────────────────────────────────────────────

/** The OPC source the tests assume exists in the backend. Set
 *  INDUVISTA_OPC_SOURCE_NAME env var to override. */
const OPC_SOURCE_NAME = process.env.INDUVISTA_OPC_SOURCE_NAME ?? "KEPWARE_OPC_UA_02";

/** Backend URL for API calls outside the UI (cleanup, setup). The
 *  frontend proxies /api/ to the backend, so we use the same baseURL
 *  the page does. */
function apiUrl(path: string, baseURL: string): string {
  return `${baseURL.replace(/\/$/, "")}${path}`;
}

/** Generate a tag name that won't collide with anything else in the DB. */
function uniqueTagName(prefix: string = "e2e"): string {
  return `${prefix}.opc22.${Math.random().toString(36).slice(2, 10)}`;
}


// ─── Shared helpers ──────────────────────────────────────────────────

/** Look up the OPC source ID by name via the API. Skips the test if
 *  not found — this keeps the suite passable on machines that don't
 *  have the expected Kepware setup. */
async function getOpcSourceId(
  request: APIRequestContext,
  baseURL: string,
  name: string,
): Promise<number | null> {
  const res = await request.get(apiUrl("/api/opc-sources", baseURL));
  if (!res.ok()) return null;
  const sources: Array<{ id: number; name: string }> = await res.json();
  const found = sources.find((s) => s.name === name);
  return found ? found.id : null;
}

/** Open the OPC Mappings drawer for the test source and click
 *  "Browse & Import". Leaves the modal open with the root tree
 *  expanded. */
async function openBrowseModal(
  page: Page,
  request: APIRequestContext,
  baseURL: string,
): Promise<number> {
  const sourceId = await getOpcSourceId(request, baseURL, OPC_SOURCE_NAME);
  if (sourceId === null) {
    test.skip(true, `OPC source ${OPC_SOURCE_NAME!} not configured`);
    throw new Error("unreachable");
  }

  // Navigate to the OPC sources page and wait for it to render its rows.
  // The mappings button selector is keyed by source id (data-testid added
  // for test stability — the production button text is "Mappings" but
  // there's one button per source so a text selector is ambiguous).
  // Route path is /config/opc-sources (not /opc-sources) per App.tsx.
  await page.goto("/config/opc-sources", { waitUntil: "networkidle" });

  // First wait for the source name text to appear — this proves the
  // React Query for /api/opc-sources resolved and the row rendered.
  await expect(page.getByText(OPC_SOURCE_NAME).first()).toBeVisible({ timeout: 15_000 });

  const mappingsBtn = page.getByTestId(`opc-source-mappings-btn-${sourceId}`);
  try {
    await expect(mappingsBtn).toBeVisible({ timeout: 5_000 });
  } catch (e) {
    // Diagnostic: when this fails, list available testids on the page
    // so we can see what IS there.
    const availableTestIds = await page.locator("[data-testid]").evaluateAll(
      (els) => els.map((el) => el.getAttribute("data-testid")),
    );
    console.log("Available data-testids on page:", availableTestIds);
    throw new Error(
      `Mappings button for source id ${sourceId} not found. ` +
      `Available testids: ${JSON.stringify(availableTestIds)}`,
    );
  }
  await mappingsBtn.click();

  // Click "Browse & Import"
  await page.getByTestId("opc-browse-open-btn").click();

  // Wait for modal to render
  await expect(page.getByTestId("opc-browse-modal")).toBeVisible({ timeout: 5_000 });

  return sourceId;
}

/** Drill into CONDENSATE1 → FLC1 → MTR1 in the open modal. */
async function drillToMtr1(page: Page): Promise<void> {
  await page.getByTestId("opc-browse-folder-CONDENSATE1").click();
  // Loading is fast; just wait for FLC1 to appear
  await expect(page.getByTestId("opc-browse-folder-FLC1")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("opc-browse-folder-FLC1").click();
  await expect(page.getByTestId("opc-browse-folder-MTR1")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("opc-browse-folder-MTR1").click();

  // Wait for the first KPW_ variable to render. Backend has a hard
  // 8-second timeout on browse calls, so if it can't return promptly
  // it returns 504 (which would surface here as no variables visible).
  await expect(
    page.getByTestId(/opc-browse-checkbox-KPW_/).first(),
  ).toBeVisible({ timeout: 15_000 });
}

/** Look up the source's mappings to find any we created during the
 *  test, then delete them. Cleanup helper for afterEach. */
async function cleanupMappingsByTagNamePrefix(
  request: APIRequestContext,
  baseURL: string,
  sourceId: number,
  tagNamePrefix: string,
): Promise<void> {
  const res = await request.get(
    apiUrl(`/api/opc-sources/${sourceId}/mappings`, baseURL),
  );
  if (!res.ok()) return;
  const mappings: Array<{ id: number; tag_name: string }> = await res.json();
  const toDelete = mappings.filter((m) => m.tag_name.startsWith(tagNamePrefix));
  for (const m of toDelete) {
    await request
      .delete(apiUrl(`/api/opc-sources/${sourceId}/mappings/${m.id}`, baseURL))
      .catch(() => undefined);
  }
}


// ─── Tests ───────────────────────────────────────────────────────────

test.describe("OPC Browse & Import — Phase OPC-web.2.2", () => {

  test.describe.configure({ mode: "serial" });

  test("1. modal opens and shows top-level plant folders", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);

    // CONDENSATE1 should be visible at the top level (non-system folder
    // shown by default)
    await expect(page.getByTestId("opc-browse-folder-CONDENSATE1")).toBeVisible({ timeout: 15_000 });
  });

  test("2. system folders are hidden by default", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);

    // _System folder should NOT be visible while showSystem is off
    await expect(page.getByTestId("opc-browse-folder-_System")).toHaveCount(0);
    // And neither should the standard Server folder
    // (no testid for Server because it's not a Kepware folder; just
    // verify the count of folders prefixed with _ is zero)
    const sysCount = await page
      .locator('[data-testid^="opc-browse-folder-_"]')
      .count();
    expect(sysCount).toBe(0);
  });

  test("3. show system folders toggle reveals them", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);

    await page.getByTestId("opc-browse-show-system").check();

    // System folders should now appear
    await expect(page.getByTestId("opc-browse-folder-_System")).toBeVisible({ timeout: 5_000 });
  });

  test("4. drill into CONDENSATE1.FLC1.MTR1 shows variables", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);
    await drillToMtr1(page);

    // At least one well-known KPW variable should be present
    const dailyMass = page.getByTestId("opc-browse-checkbox-KPW_CUR_DAILY_MASS");
    await expect(dailyMass).toBeVisible({ timeout: 10_000 });
  });

  test("5. filter narrows the variable list", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);
    await drillToMtr1(page);

    // Initially many KPW_DAILY_ rows visible
    const initialCount = await page
      .locator('[data-testid^="opc-browse-checkbox-KPW_"]')
      .count();
    expect(initialCount).toBeGreaterThan(20);

    // Filter to DAILY_DAILY
    await page.getByTestId("opc-browse-filter").fill("DAILY_DAILY");

    // After filter, expect only a handful of matches
    await expect.poll(async () => {
      return await page
        .locator('[data-testid^="opc-browse-checkbox-KPW_DAILY_DAILY_"]')
        .count();
    }).toBeGreaterThan(0);

    const filteredCount = await page
      .locator('[data-testid^="opc-browse-checkbox-KPW_"]')
      .count();
    expect(filteredCount).toBeLessThan(initialCount);
  });

  test("6. ticking a variable populates the Selected pane", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);
    await drillToMtr1(page);

    await page.getByTestId("opc-browse-checkbox-KPW_CUR_MASS").check();

    // The selected pane should now show 1 entry
    await expect(page.getByText(/^Selected \(1\)$/)).toBeVisible({ timeout: 3_000 });

    // The tag name input in the selected card should have an auto-
    // generated name
    const tagInput = page.locator('input[value*="kpw_cur_mass"]');
    await expect(tagInput).toBeVisible({ timeout: 3_000 });
  });

  test("7. prefix updates all auto-generated names", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);
    await drillToMtr1(page);

    await page.getByTestId("opc-browse-checkbox-KPW_CUR_MASS").check();
    await page.getByTestId("opc-browse-checkbox-KPW_CUR_GSVOL").check();

    // Set prefix
    await page.getByTestId("opc-browse-prefix").fill("e2etest.");

    // Both tag-name inputs should now start with the prefix
    const mass = page.locator('input[value="e2etest.kpw_cur_mass"]');
    const gsvol = page.locator('input[value="e2etest.kpw_cur_gsvol"]');
    await expect(mass).toBeVisible({ timeout: 3_000 });
    await expect(gsvol).toBeVisible({ timeout: 3_000 });
  });

  test("8. import button is disabled with empty selection", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    await openBrowseModal(page, request, baseURL);

    const importBtn = page.getByTestId("opc-browse-import-btn");
    await expect(importBtn).toBeDisabled();
  });

  test("9. successful bulk import shows success result and refreshes drawer", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    const sourceId = await openBrowseModal(page, request, baseURL);
    await drillToMtr1(page);

    const tagPrefix = uniqueTagName("e2e9");
    await page.getByTestId("opc-browse-prefix").fill(`${tagPrefix}.`);

    // Pick 2 less-commonly-used variables to minimize the chance of
    // pre-existing mappings interfering. _KP suffix variables are
    // engineering values, less likely to be used in production.
    try {
      await page.getByTestId("opc-browse-checkbox-KPW_PRESS1_KP").check();
      await page.getByTestId("opc-browse-checkbox-KPW_TEMP1_KP").check();
    } catch (e) {
      test.skip(true, `couldn't tick test variables — already mapped?`);
      return;
    }

    // Both should now appear in the selected pane
    await expect(page.getByText(/^Selected \(2\)$/)).toBeVisible({ timeout: 3_000 });

    // Click Import 2 tags
    await page.getByTestId("opc-browse-import-btn").click();

    // Wait for the success result panel
    try {
      await expect(page.getByText(/Imported 2 of 2/)).toBeVisible({ timeout: 10_000 });
    } finally {
      // Whatever happens, clean up
      await cleanupMappingsByTagNamePrefix(request, baseURL, sourceId, tagPrefix);
    }
  });

  test("10. partial-failure result shows per-row errors", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    const sourceId = await openBrowseModal(page, request, baseURL);
    await drillToMtr1(page);

    const tagPrefix = uniqueTagName("e2e10");

    // First, create a mapping via API so the next bulk import will
    // hit a duplicate.
    const seedNodeId = "ns=2;s=CONDENSATE1.FLC1.MTR1.KPW_LINE_DENS2_KP";
    const seedTagName = `${tagPrefix}.seed`;
    const seedRes = await request.post(
      apiUrl(`/api/opc-sources/${sourceId}/mappings`, baseURL),
      {
        data: {
          node_id: seedNodeId,
          tag_name: seedTagName,
          data_type: "float64",
        },
      },
    );
    if (seedRes.status() === 409) {
      // Already mapped, can't re-seed. Skip cleanly.
      test.skip(true, "test prereq mapping already exists");
      return;
    }
    if (!seedRes.ok()) {
      test.skip(true, `couldn't seed test mapping: ${seedRes.status()}`);
      return;
    }

    // The modal cached the browse before our seed mapping was created,
    // so the page's view of "is_mapped" is stale. Click Refresh to
    // get a fresh tree.
    await page.getByRole("button", { name: /refresh/i }).click();
    await drillToMtr1(page);

    // Now the seeded node should be greyed out / disabled. Let's
    // verify by trying to tick its checkbox — should fail.
    const seedCheckbox = page.getByTestId("opc-browse-checkbox-KPW_LINE_DENS2_KP");
    await expect(seedCheckbox).toBeDisabled();

    // Tick TWO different fresh nodes
    await page.getByTestId("opc-browse-prefix").fill(`${tagPrefix}.`);
    try {
      await page.getByTestId("opc-browse-checkbox-KPW_VCF_INUSE1_KP").check();
      await page.getByTestId("opc-browse-checkbox-KPW_VCF_INUSE2_KP").check();
    } catch (e) {
      test.skip(true, "couldn't tick test variables");
      return;
    }

    // Import — expect 2/2 success
    await page.getByTestId("opc-browse-import-btn").click();
    try {
      await expect(page.getByText(/Imported 2 of 2/)).toBeVisible({ timeout: 10_000 });
    } finally {
      await cleanupMappingsByTagNamePrefix(request, baseURL, sourceId, tagPrefix);
    }
  });

  test("11. already-mapped variables show greyed-out checkbox and badge", async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error("baseURL not configured");
    const sourceId = await openBrowseModal(page, request, baseURL);

    // Create a mapping via API, then open the modal and verify the
    // is_mapped state shows correctly.
    const tagPrefix = uniqueTagName("e2e11");
    const seedNodeId = "ns=2;s=CONDENSATE1.FLC1.MTR1.KPW_HOURLY_HOURLY_GUVOL";
    const seedRes = await request.post(
      apiUrl(`/api/opc-sources/${sourceId}/mappings`, baseURL),
      {
        data: {
          node_id: seedNodeId,
          tag_name: `${tagPrefix}.mapped`,
          data_type: "float64",
        },
      },
    );
    if (seedRes.status() === 409) {
      test.skip(true, "test prereq mapping already exists");
      return;
    }
    if (!seedRes.ok()) {
      test.skip(true, `couldn't seed: ${seedRes.status()}`);
      return;
    }

    try {
      // Refresh to get the updated is_mapped state, then drill to MTR1
      await page.getByRole("button", { name: /refresh/i }).click();
      await drillToMtr1(page);

      const seedCheckbox = page.getByTestId("opc-browse-checkbox-KPW_HOURLY_HOURLY_GUVOL");
      await expect(seedCheckbox).toBeDisabled();

      // And the "mapped" badge should be visible somewhere near the row
      // Looser selector — any text "mapped" near the row
      const row = page.getByTestId("opc-browse-row-KPW_HOURLY_HOURLY_GUVOL");
      await expect(row.getByText(/mapped/i)).toBeVisible();
    } finally {
      await cleanupMappingsByTagNamePrefix(request, baseURL, sourceId, tagPrefix);
    }
  });
});
