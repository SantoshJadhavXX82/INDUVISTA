# =============================================================================
# Phase 16.0a -- Idempotent edits to base.py + main.py.
#
# What it does:
#   1. In base.py: adds the line `CONFIG_SCHEMA: dict = {}` to the
#      BaseBlock class, right after `CODE: str`. Skips if already present.
#   2. In main.py: adds `from app.api import calc_schemas` after the
#      LAST existing 'from app.api import ...' line, and
#      `app.include_router(calc_schemas.router)` after the LAST existing
#      `app.include_router(...)` call. Skips if either is already present.
#
# Safe to re-run. Reports what it changed and what stayed the same.
# If a pattern can't be matched (custom import style etc), reports
# clearly so you can edit by hand.
# =============================================================================

$ErrorActionPreference = 'Stop'

$basePath = "D:\INDUVISTA\backend\app\workers\calc_blocks\base.py"
$mainPath = "D:\INDUVISTA\backend\app\main.py"

if (-not (Test-Path $basePath)) { throw "base.py not found at $basePath" }
if (-not (Test-Path $mainPath)) { throw "main.py not found at $mainPath" }

# Make a one-time backup the first time we touch each file.
foreach ($p in @($basePath, $mainPath)) {
    $bak = "$p.bak_phase16_0a"
    if (-not (Test-Path $bak)) {
        Copy-Item $p $bak
        Write-Host "Backup: $bak" -ForegroundColor Gray
    }
}


# ----------------------------------------------------------------------------
# 1. base.py - add CONFIG_SCHEMA class attribute
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "=== base.py ===" -ForegroundColor Cyan

$base = Get-Content $basePath -Raw

if ($base -match "CONFIG_SCHEMA") {
    Write-Host "  CONFIG_SCHEMA already present, no change." -ForegroundColor Yellow
} else {
    # Match `CODE: str  # optional comment` line. Capture indent so we
    # insert the new line at the same indentation level.
    $pattern = "(?m)^(?<indent>\s+)CODE:\s*str(?<comment>[^\r\n]*)\r?\n"
    $regex = [regex]::new($pattern)
    $m = $regex.Match($base)

    if (-not $m.Success) {
        Write-Host "  ERROR: could not find 'CODE: str' line." -ForegroundColor Red
        Write-Host "  Add this line manually in base.py inside BaseBlock," -ForegroundColor Yellow
        Write-Host "  just below 'CODE: str':" -ForegroundColor Yellow
        Write-Host "      CONFIG_SCHEMA: dict = {}  # populated by calc_block_schemas.install_schemas()" -ForegroundColor Yellow
    } else {
        $indent = $m.Groups['indent'].Value
        $newLine = "${indent}CONFIG_SCHEMA: dict = {}  # populated by calc_block_schemas.install_schemas()`r`n"
        # Insert ONCE, right after the matched CODE: str line.
        $base = $base.Substring(0, $m.Index + $m.Length) +
                $newLine +
                $base.Substring($m.Index + $m.Length)
        Set-Content -Path $basePath -Value $base -NoNewline
        Write-Host "  Inserted CONFIG_SCHEMA line after CODE: str" -ForegroundColor Green
    }
}


# ----------------------------------------------------------------------------
# 2. main.py - add import + include_router
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "=== main.py ===" -ForegroundColor Cyan

$main = Get-Content $mainPath -Raw

# -- 2a. Add import if missing -------------------------------------------
if ($main -match "(?m)^\s*from app\.api import calc_schemas\b" -or
    $main -match "(?m)^\s*from app\.api\.calc_schemas\b") {
    Write-Host "  import calc_schemas already present, no change." -ForegroundColor Yellow
} else {
    # Find the LAST 'from app.api import ...' line.
    $importMatches = [regex]::Matches(
        $main, "(?m)^from app\.api(?:\.\w+)? import [^\r\n]+\r?\n")
    if ($importMatches.Count -eq 0) {
        Write-Host "  ERROR: no 'from app.api import ...' lines found." -ForegroundColor Red
        Write-Host "  Add this import manually near the other API imports:" -ForegroundColor Yellow
        Write-Host "      from app.api import calc_schemas" -ForegroundColor Yellow
    } else {
        $last = $importMatches[$importMatches.Count - 1]
        $insertAt = $last.Index + $last.Length
        $newImport = "from app.api import calc_schemas`r`n"
        $main = $main.Substring(0, $insertAt) +
                $newImport +
                $main.Substring($insertAt)
        Write-Host "  Inserted: from app.api import calc_schemas" -ForegroundColor Green
        Write-Host "    (after last 'from app.api import ...' on line $($last.Value.Trim()))" -ForegroundColor Gray
    }
}

# -- 2b. Add include_router if missing -----------------------------------
if ($main -match "calc_schemas\.router|include_router\(\s*calc_schemas") {
    Write-Host "  include_router(calc_schemas.router) already present, no change." -ForegroundColor Yellow
} else {
    # Find the LAST app.include_router(...) call.
    $routerMatches = [regex]::Matches(
        $main, "(?m)^app\.include_router\([^\r\n)]+\)\r?\n")
    if ($routerMatches.Count -eq 0) {
        Write-Host "  ERROR: no 'app.include_router(...)' calls found." -ForegroundColor Red
        Write-Host "  Add this manually next to the other include_router calls:" -ForegroundColor Yellow
        Write-Host "      app.include_router(calc_schemas.router)" -ForegroundColor Yellow
    } else {
        $last = $routerMatches[$routerMatches.Count - 1]
        $insertAt = $last.Index + $last.Length
        $newRouter = "app.include_router(calc_schemas.router)`r`n"
        $main = $main.Substring(0, $insertAt) +
                $newRouter +
                $main.Substring($insertAt)
        Write-Host "  Inserted: app.include_router(calc_schemas.router)" -ForegroundColor Green
        Write-Host "    (after last include_router call: $($last.Value.Trim()))" -ForegroundColor Gray
    }
}

Set-Content -Path $mainPath -Value $main -NoNewline


# ----------------------------------------------------------------------------
# 3. Verify
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan

$baseFinal = Get-Content $basePath -Raw
$mainFinal = Get-Content $mainPath -Raw

$checks = @(
    @{ Name = "base.py: CONFIG_SCHEMA in BaseBlock"
       Pass = ($baseFinal -match "CONFIG_SCHEMA\s*:\s*dict") }
    @{ Name = "main.py: import calc_schemas"
       Pass = ($mainFinal -match "(?m)^\s*from app\.api import calc_schemas\b|from app\.api\.calc_schemas\b") }
    @{ Name = "main.py: include_router(calc_schemas.router)"
       Pass = ($mainFinal -match "calc_schemas\.router") }
)
$allOk = $true
foreach ($c in $checks) {
    if ($c.Pass) {
        Write-Host "  [OK]   $($c.Name)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($c.Name)" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""
if ($allOk) {
    Write-Host "All edits applied. Rebuild and run smoke." -ForegroundColor Green
    Write-Host "    docker compose build backend calc_evaluator" -ForegroundColor Gray
    Write-Host "    docker compose up -d --force-recreate backend calc_evaluator" -ForegroundColor Gray
    Write-Host "    .\smoke_test_phase16_0a.ps1" -ForegroundColor Gray
} else {
    Write-Host "Some edits still need manual application - see messages above." -ForegroundColor Red
    Write-Host "Backups: $basePath.bak_phase16_0a, $mainPath.bak_phase16_0a" -ForegroundColor Gray
    exit 1
}
