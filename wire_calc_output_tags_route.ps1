# =============================================================================
# Phase 16.0b polish - Wire POST /api/calc/output-tags into main.py.
#
# Adds two lines to D:\INDUVISTA\backend\app\main.py:
#   1. from app.api import calc_output_tags
#   2. app.include_router(calc_output_tags.router)
#
# Idempotent: re-runs are no-ops. One-time backup at
# main.py.bak_calc_output_tags. If main.py uses a non-standard
# pattern, the script reports what to add by hand.
# =============================================================================

$ErrorActionPreference = 'Stop'

$mainPath = "D:\INDUVISTA\backend\app\main.py"

if (-not (Test-Path $mainPath)) {
    throw "main.py not found at $mainPath. Adjust the path if your file lives elsewhere."
}

$bak = "$mainPath.bak_calc_output_tags"
if (-not (Test-Path $bak)) {
    Copy-Item $mainPath $bak
    Write-Host "Backup: $bak" -ForegroundColor Gray
}

$content = Get-Content $mainPath -Raw
$changed = $false


# ----------------------------------------------------------------------------
# 1. Add the import if missing
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "=== main.py import ===" -ForegroundColor Cyan

if ($content -match "from\s+app\.api\s+import[^\n]*\bcalc_output_tags\b") {
    Write-Host "  calc_output_tags already imported. Skipping." -ForegroundColor Yellow
} else {
    # Append calc_output_tags to an existing `from app.api import ...`
    # line so we don't add an extra import statement. If multiple such
    # imports exist, target the last one.
    $apiImports = [regex]::Matches(
        $content,
        "(?m)^from\s+app\.api\s+import\s+(?<names>[^\r\n]+)$"
    )
    if ($apiImports.Count -gt 0) {
        $last = $apiImports[$apiImports.Count - 1]
        $oldLine = $last.Value
        # Append after the existing names, comma-separated.
        $newLine = $oldLine.TrimEnd() + ", calc_output_tags"
        $content = $content.Substring(0, $last.Index) +
                   $newLine +
                   $content.Substring($last.Index + $last.Length)
        $changed = $true
        Write-Host "  Appended calc_output_tags to existing 'from app.api import ...' line" -ForegroundColor Green
    } else {
        # Fallback: add a fresh import line after the last `from app.` import.
        $appImports = [regex]::Matches(
            $content, "(?m)^from\s+app\.[^\s]+\s+import[^\r\n]+\r?\n"
        )
        if ($appImports.Count -gt 0) {
            $lastImp = $appImports[$appImports.Count - 1]
            $insertAt = $lastImp.Index + $lastImp.Length
            $newImport = "from app.api import calc_output_tags`r`n"
            $content = $content.Substring(0, $insertAt) + $newImport + $content.Substring($insertAt)
            $changed = $true
            Write-Host "  Added 'from app.api import calc_output_tags' after the last app import" -ForegroundColor Green
        } else {
            Write-Host "  ERROR: could not find any 'from app.' import in main.py" -ForegroundColor Red
            Write-Host "  Add this manually near the top:" -ForegroundColor Yellow
            Write-Host "      from app.api import calc_output_tags" -ForegroundColor Yellow
        }
    }
}


# ----------------------------------------------------------------------------
# 2. Add the router include if missing
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "=== main.py router include ===" -ForegroundColor Cyan

if ($content -match "include_router\s*\(\s*calc_output_tags\.router") {
    Write-Host "  calc_output_tags.router already included. Skipping." -ForegroundColor Yellow
} else {
    # Find the last `app.include_router(...)` call and insert after it.
    $includes = [regex]::Matches(
        $content,
        "(?m)^(?<indent>\s*)app\.include_router\s*\([^\r\n]+\)\s*\r?\n"
    )
    if ($includes.Count -gt 0) {
        $last = $includes[$includes.Count - 1]
        $indent = $last.Groups['indent'].Value
        $insertAt = $last.Index + $last.Length
        $newInclude = "${indent}app.include_router(calc_output_tags.router)`r`n"
        $content = $content.Substring(0, $insertAt) + $newInclude + $content.Substring($insertAt)
        $changed = $true
        Write-Host "  Inserted app.include_router(calc_output_tags.router)" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: could not find any 'app.include_router(...)' pattern" -ForegroundColor Red
        Write-Host "  Add this manually inside the app setup:" -ForegroundColor Yellow
        Write-Host "      app.include_router(calc_output_tags.router)" -ForegroundColor Yellow
    }
}


# ----------------------------------------------------------------------------
# Write back + verify
# ----------------------------------------------------------------------------

if ($changed) {
    Set-Content -Path $mainPath -Value $content -NoNewline
}

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan

$final = Get-Content $mainPath -Raw

$importOk  = $final -match "from\s+app\.api\s+import[^\n]*\bcalc_output_tags\b"
$includeOk = $final -match "include_router\s*\(\s*calc_output_tags\.router"

if ($importOk)  { Write-Host "  [OK]   import calc_output_tags" -ForegroundColor Green }
else            { Write-Host "  [FAIL] import calc_output_tags" -ForegroundColor Red   }
if ($includeOk) { Write-Host "  [OK]   app.include_router(calc_output_tags.router)" -ForegroundColor Green }
else            { Write-Host "  [FAIL] app.include_router(calc_output_tags.router)" -ForegroundColor Red   }

Write-Host ""
if ($importOk -and $includeOk) {
    Write-Host "Router wired. Rebuild backend so the import takes effect:" -ForegroundColor Green
    Write-Host "    docker compose build backend" -ForegroundColor Gray
    Write-Host "    docker compose up -d --force-recreate backend" -ForegroundColor Gray
} else {
    Write-Host "Some edits need manual application - see messages above." -ForegroundColor Red
    exit 1
}
