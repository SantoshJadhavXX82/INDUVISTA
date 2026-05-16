# =============================================================================
# InduVista - Playwright UI smoke setup + runner
# VERSION ui-smoke-r1
# =============================================================================
# Installs @playwright/test (one-time, dev dep in frontend/) and the
# Chromium binary (~120 MB), then runs the UI smoke against the local
# Vite server.
#
# Prerequisites:
#   - frontend/ exists with package.json
#   - Vite dev server running at http://localhost:5174 (or override
#     via $env:INDUVISTA_URL before running)
#   - Backend reachable on :8000
#
# Run:
#   .\setup_and_run_ui_smoke.ps1                  -> headless run
#   .\setup_and_run_ui_smoke.ps1 -Headed          -> visible browser
#   .\setup_and_run_ui_smoke.ps1 -Debug           -> step-through debugger
#   .\setup_and_run_ui_smoke.ps1 -InstallOnly     -> just install, don't run
# =============================================================================

param(
    [switch]$Headed,
    [switch]$Debug,
    [switch]$InstallOnly,
    [string]$BaseUrl = "http://localhost:5174"
)

$ErrorActionPreference = 'Stop'

$frontDir = Join-Path $PSScriptRoot "frontend"
if (-not (Test-Path $frontDir)) {
    Write-Host "[FAIL] frontend/ directory not found at $frontDir" -ForegroundColor Red
    exit 1
}

Push-Location $frontDir
try {
    # ----- 1. Verify Node + npm -----
    try {
        $nodeV = (& node --version 2>$null).Trim()
        Write-Host "[OK] node $nodeV" -ForegroundColor Green
    } catch {
        Write-Host "[FAIL] node not found. Install Node.js 18+ first." -ForegroundColor Red
        exit 1
    }

    # ----- 2. Install @playwright/test if missing -----
    $hasPlaywright = Test-Path (Join-Path $frontDir "node_modules\@playwright\test")
    if (-not $hasPlaywright) {
        Write-Host ""
        Write-Host "Installing @playwright/test (dev dependency)..." -ForegroundColor Cyan
        & npm install -D @playwright/test
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[FAIL] npm install failed" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "[OK] @playwright/test already installed" -ForegroundColor Green
    }

    # ----- 3. Install Chromium browser binary if missing -----
    # Playwright stores browsers in a cache directory. The first run checks
    # via `playwright install --dry-run` would be ideal, but the most
    # reliable way is to just run install - it's idempotent.
    Write-Host ""
    Write-Host "Ensuring Chromium browser binary is present..." -ForegroundColor Cyan
    & npx playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Chromium install failed" -ForegroundColor Red
        exit 1
    }

    if ($InstallOnly) {
        Write-Host ""
        Write-Host "Install complete. Run again without -InstallOnly to execute tests." -ForegroundColor Green
        exit 0
    }

    # ----- 4. Reachability check -----
    Write-Host ""
    Write-Host "Checking frontend at $BaseUrl ..." -ForegroundColor Cyan
    try {
        $resp = Invoke-WebRequest -Uri $BaseUrl -TimeoutSec 5 -UseBasicParsing
        if ($resp.StatusCode -ne 200) {
            Write-Host "[WARN] $BaseUrl returned HTTP $($resp.StatusCode)" -ForegroundColor Yellow
        } else {
            Write-Host "[OK] frontend reachable" -ForegroundColor Green
        }
    } catch {
        Write-Host "[FAIL] $BaseUrl unreachable. Start the Vite dev server first:" -ForegroundColor Red
        Write-Host "       cd frontend; npm run dev" -ForegroundColor Yellow
        exit 1
    }

    try {
        $h = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 5
        Write-Host "[OK] backend /health = $($h.status)" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] backend /health not reachable - tag picker may be empty" -ForegroundColor Yellow
    }

    # ----- 5. Run the tests -----
    $env:INDUVISTA_URL = $BaseUrl
    Write-Host ""
    Write-Host "Running UI smoke against $BaseUrl ..." -ForegroundColor Cyan
    Write-Host ""

    $args = @()
    if ($Headed) { $args += "--headed" }
    if ($Debug)  { $args += "--debug" }

    & npx playwright test $args
    $exitCode = $LASTEXITCODE
    Write-Host ""
    if ($exitCode -eq 0) {
        Write-Host "[PASS] All UI smoke tests passed" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] $exitCode test(s) failed" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "HTML report: D:\INDUVISTA\frontend\playwright-report\index.html" -ForegroundColor Cyan
    Write-Host "View with:  cd frontend; npx playwright show-report" -ForegroundColor Yellow
    Write-Host "(NOT from project root - report path is relative to frontend/)" -ForegroundColor Yellow
    exit $exitCode
}
finally {
    Pop-Location
}
