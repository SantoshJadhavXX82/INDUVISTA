# =============================================================================
# Phase 16.0c - Wire GET /api/calc/current-values into main.py.
#
# Adds two lines to D:\INDUVISTA\backend\app\main.py:
#   1. from app.api import calc_current_values
#   2. app.include_router(calc_current_values.router)
#
# Idempotent: re-runs are no-ops. One-time backup at
# main.py.bak_calc_current_values.
# =============================================================================

$ErrorActionPreference = 'Stop'

$mainPath = "D:\INDUVISTA\backend\app\main.py"

if (-not (Test-Path $mainPath)) {
    throw "main.py not found at $mainPath. Adjust the path if your file lives elsewhere."
}

$bak = "$mainPath.bak_calc_current_values"
if (-not (Test-Path $bak)) {
    Copy-Item $mainPath $bak
    Write-Host "Backup: $bak" -ForegroundColor Gray
}

$content = Get-Content $mainPath -Raw
$changed = $false


Write-Host ""
Write-Host "=== main.py import ===" -ForegroundColor Cyan

if ($content -match "from\s+app\.api\s+import[^\n]*\bcalc_current_values\b") {
    Write-Host "  calc_current_values already imported. Skipping." -ForegroundColor Yellow
} else {
    $apiImports = [regex]::Matches(
        $content,
        "(?m)^from\s+app\.api\s+import\s+(?<names>[^\r\n]+)$"
    )
    if ($apiImports.Count -gt 0) {
        $last = $apiImports[$apiImports.Count - 1]
        $oldLine = $last.Value
        $newLine = $oldLine.TrimEnd() + ", calc_current_values"
        $content = $content.Substring(0, $last.Index) +
                   $newLine +
                   $content.Substring($last.Index + $last.Length)
        $changed = $true
        Write-Host "  Appended calc_current_values to existing 'from app.api import ...' line" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: could not find any 'from app.api import' line" -ForegroundColor Red
        Write-Host "  Add manually near the top of main.py:" -ForegroundColor Yellow
        Write-Host "      from app.api import calc_current_values" -ForegroundColor Yellow
    }
}


Write-Host ""
Write-Host "=== main.py router include ===" -ForegroundColor Cyan

if ($content -match "include_router\s*\(\s*calc_current_values\.router") {
    Write-Host "  calc_current_values.router already included. Skipping." -ForegroundColor Yellow
} else {
    $includes = [regex]::Matches(
        $content,
        "(?m)^(?<indent>\s*)app\.include_router\s*\([^\r\n]+\)\s*\r?\n"
    )
    if ($includes.Count -gt 0) {
        $last = $includes[$includes.Count - 1]
        $indent = $last.Groups['indent'].Value
        $insertAt = $last.Index + $last.Length
        $newInclude = "${indent}app.include_router(calc_current_values.router)`r`n"
        $content = $content.Substring(0, $insertAt) + $newInclude + $content.Substring($insertAt)
        $changed = $true
        Write-Host "  Inserted app.include_router(calc_current_values.router)" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: could not find any 'app.include_router(...)' pattern" -ForegroundColor Red
    }
}


if ($changed) {
    Set-Content -Path $mainPath -Value $content -NoNewline
}

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan

$final = Get-Content $mainPath -Raw

$importOk  = $final -match "from\s+app\.api\s+import[^\n]*\bcalc_current_values\b"
$includeOk = $final -match "include_router\s*\(\s*calc_current_values\.router"

if ($importOk)  { Write-Host "  [OK]   import calc_current_values" -ForegroundColor Green }
else            { Write-Host "  [FAIL] import calc_current_values" -ForegroundColor Red   }
if ($includeOk) { Write-Host "  [OK]   app.include_router(calc_current_values.router)" -ForegroundColor Green }
else            { Write-Host "  [FAIL] app.include_router(calc_current_values.router)" -ForegroundColor Red   }

Write-Host ""
if ($importOk -and $includeOk) {
    Write-Host "Router wired. Rebuild backend so the import takes effect:" -ForegroundColor Green
    Write-Host "    docker compose build backend" -ForegroundColor Gray
    Write-Host "    docker compose up -d --force-recreate backend" -ForegroundColor Gray
} else {
    exit 1
}
