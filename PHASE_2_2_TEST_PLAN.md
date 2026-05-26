# Phase OPC-web.2.2 — Smoke Test Suite

Automated tests for the OPC Browse + Bulk Import feature. Two layers:

1. **Backend integration tests** — `backend/tests/integration/test_opc_browse_import.py`
   - 9 tests covering both new endpoints (`/browse` and `/mappings/bulk`)
   - Run against live backend talking to live Kepware
   - Each test self-cleans created mappings via teardown

2. **Frontend E2E tests** — `frontend/tests-e2e/opc-browse-import.spec.ts`
   - 11 Playwright tests covering the Browse & Import modal
   - Run against Vite dev server + live backend + live Kepware
   - Tests use `data-testid` selectors for stability

## Prerequisites

- Docker stack up: `docker compose up -d`
- Vite dev server: `cd frontend; npm run dev` (for UI tests)
- Kepware running with KEPWARE_OPC_UA_02 source configured
- CONDENSATE1.FLC1.MTR1 exists in the Kepware project

## One-shot run

From `D:\INDUVISTA`:

```powershell
.\run_opc_22_smoke.ps1
```

This runs **both** test layers. First run takes ~3-5 minutes (~30s for
backend tests, plus ~120MB Chromium download for Playwright first time).
Subsequent runs are ~2 minutes.

## Variants

```powershell
# Watch the browser drive the UI (slow-mo, visible window)
.\run_opc_22_smoke.ps1 -Headed

# Just backend tests (skip Playwright entirely)
.\run_opc_22_smoke.ps1 -BackendOnly

# Just UI tests (assume backend is verified)
.\run_opc_22_smoke.ps1 -UiOnly

# Install Playwright + Chromium without running anything
.\run_opc_22_smoke.ps1 -InstallOnly

# Override URLs (default localhost:5174 / localhost:8000)
.\run_opc_22_smoke.ps1 -BaseUrl http://localhost:5175 -BackendUrl http://localhost:8001
```

## What each test catches

### Backend (pytest)

| Test | Catches |
|---|---|
| `test_browse_objects_returns_kepware_projects` | Backend → Kepware connection broken; root browse shape wrong; sort order regression |
| `test_browse_mtr1_returns_60_variables` | NodeId drill-down regression; data type mapping (Double→float64) regression; per-child error fallback masking real bugs |
| `test_browse_invalid_node_returns_502` | Error handling regression — bad NodeIds returning 200/500 instead of 502 |
| `test_browse_already_mapped_flag` | `is_mapped` flag stops reflecting DB state (UI would lose ability to grey out checkboxes) |
| `test_bulk_create_happy_path` | Bulk endpoint contract regression; per-row results malformed |
| `test_bulk_create_partial_failure` | Dup detection broken; rollback-instead-of-savepoint regression; error messages opaque |
| `test_bulk_create_empty_list_rejected` | Pydantic `min_length=1` removed/regressed |
| `test_bulk_create_exceeds_500_rejected` | Soft cap removed (would allow accidental 5000-tag bulk imports) |
| `test_bulk_import_triggers_hot_reload` | `updated_at` not bumped on bulk import (worker would never pick up new mappings) |

### Frontend (Playwright)

| Test | Catches |
|---|---|
| 1. modal opens, top-level folders visible | Modal render broken; backend → modal data flow broken |
| 2. system folders hidden by default | `is_system` flag UI logic regression |
| 3. show-system-folders toggle works | Toggle handler broken |
| 4. drill into MTR1 shows variables | Lazy load broken; folder click handler broken |
| 5. filter narrows variable list | Filter input wiring broken |
| 6. ticking a variable populates selection | Selection state regression; auto-name generation regression |
| 7. prefix updates auto-generated names | `computeTagName` regression |
| 8. import button disabled with empty selection | Button-state-from-selection regression |
| 9. successful bulk import shows success result | End-to-end happy path: tick → import → result panel |
| 10. partial failure shows per-row errors | Error UX regression on failed rows |
| 11. already-mapped variables greyed out | `is_mapped` UI regression (would let operators duplicate-import) |

## Triage

### "Backend tests skipped — no OPC source"

The test source name is hardcoded to `KEPWARE_OPC_UA_02`. Either:
- Create that source pointing at Kepware, OR
- Override: `$env:OPC_TEST_SOURCE_NAME = "MyOpcSource"; .\run_opc_22_smoke.ps1`

### "Playwright skipped — frontend not reachable"

Start the Vite dev server in another terminal:

```powershell
cd D:\INDUVISTA\frontend
npm run dev
```

Then re-run the script. Default port is 5174.

### "Test timed out waiting for `[data-testid=opc-browse-folder-MTR1]`"

The tree didn't drill down to MTR1. Possible causes:
- Backend `/browse` endpoint hanging on Kepware
- Modal not opening (check `[data-testid=opc-browse-modal]` is visible)
- CONDENSATE1 doesn't have FLC1.MTR1 in your Kepware setup

Run `-Headed` to watch what's happening.

### "Couldn't tick test variables — already mapped"

Test 9 imports KPW_PRESS1_KP + KPW_TEMP1_KP. If those are already
production-mapped, the test skips cleanly. Either:
- Delete those mappings via the UI, or
- Pick different test variables in the spec file

### "Bulk create exceeds 500 rejected — got 200"

This means the Pydantic `max_length=500` is gone. Restore it in
`OpcBulkMappingRequest`:

```python
items: list[OpcBulkMappingItem] = Field(..., min_length=1, max_length=500)
```

## Failure artifacts

When Playwright tests fail, screenshots, video traces, and HTML report
are written under:

```
frontend/test-results/
frontend/playwright-report/index.html
```

Open the report:

```powershell
cd D:\INDUVISTA\frontend
npx playwright show-report
```

## What the test suite does NOT cover

- **Real-time worker subscription after import.** Tests verify the
  mapping rows land in the DB and `updated_at` bumps; they don't wait
  ~30s for the reloader to actually re-subscribe. The dedicated
  `test_bulk_import_triggers_hot_reload` test exists but is marked
  `@slow` and skipped by default.
- **Performance.** No assertion that browse returns in <1s. Add one
  if it becomes an issue.
- **Multi-source concurrency.** Tests one source at a time.
- **Visual regression.** Icons, layout, animations — these stay manual.
- **Security policies != None.** Tests use whatever security the source
  is configured with. Browse against Basic256Sha256 needs cert setup
  to test.

## Files added

```
backend/tests/conftest.py                                      (updated)
backend/tests/integration/__init__.py                          (new, empty)
backend/tests/integration/test_opc_browse_import.py            (new, 9 tests)
frontend/src/components/opc/OpcBrowseImportModal.tsx           (5 testids added)
frontend/src/components/opc/OpcMappingsDrawer.tsx              (1 testid added)
frontend/tests-e2e/opc-browse-import.spec.ts                   (new, 11 tests)
run_opc_22_smoke.ps1                                           (new, runner)
PHASE_2_2_TEST_PLAN.md                                         (this file)
```
