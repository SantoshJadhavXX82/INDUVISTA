# InduVista — Trend UI Smoke (Playwright)

Browser-side automated tests covering the frontend behaviors the PowerShell
API smoke can't reach.

## What's covered

| Group | Test |
|---|---|
| 1 | Time format selector — flips 24h↔12h, persists across reload, Auto label |
| 2 | Tooltip pin — hover renders corner tooltip; click pins; click again unpins; × button unpins |
| 3 | Tooltip mode — Full/Compact/Off changes width; Off hides; persists |
| 4 | Live Value Panel — click tile dims it + hides chart series; click again restores |
| 5 | Quality filter — Hide bad / Good only activate; counts shown in dropdown; persists |
| 6 | Aggregation mode — Avg persists; disabled when aggregation=raw |
| 7 | Min/Max envelope — default On for aggregated; toggle Off; disabled in raw |
| 8 | Aggregation interval — Auto/Raw/1m/1h/1d each set the button label |

## Run

From the **project root** (not inside `frontend/`):

```powershell
# First time (installs @playwright/test + Chromium ~120 MB)
.\setup_and_run_ui_smoke.ps1

# Subsequent runs (skips install)
.\setup_and_run_ui_smoke.ps1

# Headed (visible browser) for debugging
.\setup_and_run_ui_smoke.ps1 -Headed

# Step-through debugger
.\setup_and_run_ui_smoke.ps1 -Debug

# Just install Playwright, don't run yet
.\setup_and_run_ui_smoke.ps1 -InstallOnly

# Override base URL (default :5174)
.\setup_and_run_ui_smoke.ps1 -BaseUrl http://localhost:5175
```

## Prerequisites

- Frontend Vite dev server running on :5174 (`cd frontend; npm run dev`)
- Backend reachable on :8000 (`docker compose up -d`)
- At least 2 enabled numeric tags in the database

## Report

After a run, an HTML report lives at `frontend/playwright-report/index.html`.
Open it with:

```powershell
cd frontend
npx playwright show-report
```

Failing tests get screenshots + video traces under `frontend/test-results/`.

## Adding tests

Drop a new `.spec.ts` file into `frontend/tests-e2e/`. The config picks up
anything matching `*.spec.ts`.

## Why no test-ids in production code?

The tests deliberately use visible text + ARIA roles + structural CSS
(`.u-over`, `.line-through`, `header button`). Adding `data-testid`
attributes everywhere is a maintenance burden and obscures intent. If a
selector here breaks because a label changed, that's actually useful
signal — user-facing labels shouldn't quietly drift.
