# =============================================================================
# Phase 16.0g - Wire audit_log endpoints into main.py + add startup hook.
#
# Adds three things to D:\INDUVISTA\backend\app\main.py:
#   1. from app.api import audit_log
#   2. from app.utils.audit import ensure_audit_schema
#   3. app.include_router(audit_log.router)
#   4. @app.on_event("startup") that calls ensure_audit_schema()
#
# Idempotent. One-time backup at main.py.bak_audit.
# =============================================================================

$ErrorActionPreference = 'Stop'

$mainPath = "D:\INDUVISTA\backend\app\main.py"

if (-not (Test-Path $mainPath)) {
    throw "main.py not found at $mainPath"
}

$bak = "$mainPath.bak_audit"
if (-not (Test-Path $bak)) {
    Copy-Item $mainPath $bak
    Write-Host "Backup: $bak" -ForegroundColor Gray
}

$content = Get-Content $mainPath -Raw
$changed = $false


# ---------------------------------------------------------------------------
# 1. Add audit_log to existing 'from app.api import ...' line.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== audit_log router import ===" -ForegroundColor Cyan

if ($content -match "from\s+app\.api\s+import[^\n]*\baudit_log\b") {
    Write-Host "  audit_log already imported. Skipping." -ForegroundColor Yellow
} else {
    $apiImports = [regex]::Matches(
        $content,
        "(?m)^from\s+app\.api\s+import\s+(?<names>[^\r\n]+)$"
    )
    if ($apiImports.Count -gt 0) {
        $last = $apiImports[$apiImports.Count - 1]
        $newLine = $last.Value.TrimEnd() + ", audit_log"
        $content = $content.Substring(0, $last.Index) +
                   $newLine +
                   $content.Substring($last.Index + $last.Length)
        $changed = $true
        Write-Host "  Appended audit_log to existing 'from app.api import ...' line" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: could not find 'from app.api import' line" -ForegroundColor Red
    }
}


# ---------------------------------------------------------------------------
# 2. Add ensure_audit_schema import.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== ensure_audit_schema import ===" -ForegroundColor Cyan

if ($content -match "ensure_audit_schema") {
    Write-Host "  ensure_audit_schema already imported. Skipping." -ForegroundColor Yellow
} else {
    # Place after the last 'from app.' import.
    $appImports = [regex]::Matches(
        $content, "(?m)^from\s+app\.[^\s]+\s+import[^\r\n]+\r?\n"
    )
    if ($appImports.Count -gt 0) {
        $last = $appImports[$appImports.Count - 1]
        $insertAt = $last.Index + $last.Length
        $newImport = "from app.utils.audit import ensure_audit_schema`r`n"
        $content = $content.Substring(0, $insertAt) + $newImport + $content.Substring($insertAt)
        $changed = $true
        Write-Host "  Added 'from app.utils.audit import ensure_audit_schema'" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: could not find 'from app.' import" -ForegroundColor Red
    }
}


# ---------------------------------------------------------------------------
# 3. Add audit_log router include.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== audit_log.router include ===" -ForegroundColor Cyan

if ($content -match "include_router\s*\(\s*audit_log\.router") {
    Write-Host "  audit_log.router already included. Skipping." -ForegroundColor Yellow
} else {
    $includes = [regex]::Matches(
        $content,
        "(?m)^(?<indent>\s*)app\.include_router\s*\([^\r\n]+\)\s*\r?\n"
    )
    if ($includes.Count -gt 0) {
        $last = $includes[$includes.Count - 1]
        $indent = $last.Groups['indent'].Value
        $insertAt = $last.Index + $last.Length
        $newInclude = "${indent}app.include_router(audit_log.router)`r`n"
        $content = $content.Substring(0, $insertAt) + $newInclude + $content.Substring($insertAt)
        $changed = $true
        Write-Host "  Inserted app.include_router(audit_log.router)" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: could not find 'app.include_router' pattern" -ForegroundColor Red
    }
}


# ---------------------------------------------------------------------------
# 4. Add startup hook for ensure_audit_schema.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== startup hook ===" -ForegroundColor Cyan

if ($content -match "ensure_audit_schema\(\)") {
    Write-Host "  ensure_audit_schema() startup hook already present. Skipping." -ForegroundColor Yellow
} else {
    # Append a startup hook at the end of the file.
    $startupHook = @"

@app.on_event("startup")
def _audit_schema_startup() -> None:
    # Phase 16.0g: create audit_log table + hypertable + retention on
    # backend startup. Idempotent.
    ensure_audit_schema()
"@
    $content = $content.TrimEnd() + "`r`n" + $startupHook + "`r`n"
    $changed = $true
    Write-Host "  Appended startup hook" -ForegroundColor Green
}


# ---------------------------------------------------------------------------
# Save + verify.
# ---------------------------------------------------------------------------
if ($changed) {
    Set-Content -Path $mainPath -Value $content -NoNewline
}

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan
$final = Get-Content $mainPath -Raw

$checks = @(
    @{ name="import audit_log";              re='from\s+app\.api\s+import[^\n]*\baudit_log\b' },
    @{ name="import ensure_audit_schema";    re='ensure_audit_schema' },
    @{ name="include audit_log.router";      re='include_router\s*\(\s*audit_log\.router' },
    @{ name="startup hook ensure_audit_schema()"; re='ensure_audit_schema\(\)' }
)

$allOk = $true
foreach ($c in $checks) {
    if ($final -match $c.re) {
        Write-Host "  [OK]   $($c.name)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($c.name)" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""
if ($allOk) {
    Write-Host "Audit wiring complete. Now:" -ForegroundColor Green
    Write-Host "  1. Ensure .env has AUDIT_DATABASE_URL + AUDIT_RETENTION_DAYS" -ForegroundColor Gray
    Write-Host "  2. docker compose build backend && docker compose up -d --force-recreate backend" -ForegroundColor Gray
    Write-Host "  3. docker compose logs --tail=20 backend  # look for 'audit_log schema ready'" -ForegroundColor Gray
} else {
    exit 1
}
