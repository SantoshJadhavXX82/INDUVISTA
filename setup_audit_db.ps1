# =============================================================================
# Phase 16.0g - Bootstrap the audit database on an EXISTING install.
#
# v3: renamed function parameters -Db -> -Database and -Sql -> -Statement
#     to avoid PowerShell common-parameter alias collision (-Db gets
#     auto-resolved to -Debug otherwise).
# =============================================================================

$ErrorActionPreference = 'Stop'

function Invoke-Psql {
    param(
        [Parameter(Mandatory=$true)][string]$Database,
        [Parameter(Mandatory=$true)][string]$Statement,
        [switch]$Quiet
    )
    if ($Quiet) {
        $out = docker compose exec -T postgres psql -U induvista_admin -d $Database -tA -c $Statement 2>&1
    } else {
        $out = docker compose exec -T postgres psql -U induvista_admin -d $Database -c $Statement 2>&1
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "psql FAILED ($Database): $Statement" -ForegroundColor Red
        Write-Host $out -ForegroundColor Red
        throw "psql exited with code $LASTEXITCODE"
    }
    return $out
}

Write-Host "=== Phase 16.0g audit database bootstrap (v3) ===" -ForegroundColor Cyan
Write-Host ""

# 1. Check if induvista_audit already exists.
Write-Host "Checking if induvista_audit exists..." -ForegroundColor Cyan
$exists = Invoke-Psql -Database 'postgres' -Statement "SELECT 1 FROM pg_database WHERE datname = 'induvista_audit';" -Quiet
if ($exists -match '1') {
    Write-Host "  induvista_audit already exists. Skipping CREATE." -ForegroundColor Yellow
} else {
    Write-Host "Creating induvista_audit..." -ForegroundColor Green
    # Each -c is its own autocommit statement. CREATE DATABASE works.
    Invoke-Psql -Database 'postgres' -Statement "CREATE DATABASE induvista_audit;" | Out-Null
    Write-Host "  Created." -ForegroundColor Green
}

# 2. Grant privileges (idempotent - GRANT is a no-op if already granted).
Write-Host "Granting privileges..." -ForegroundColor Cyan
Invoke-Psql -Database 'postgres' -Statement "GRANT ALL PRIVILEGES ON DATABASE induvista_audit TO induvista_admin;" | Out-Null

# 3. Enable timescaledb extension in the audit DB.
Write-Host "Enabling timescaledb extension in induvista_audit..." -ForegroundColor Cyan
Invoke-Psql -Database 'induvista_audit' -Statement "CREATE EXTENSION IF NOT EXISTS timescaledb;" | Out-Null

# 4. Verify everything is actually there.
Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan

$pgVersion = Invoke-Psql -Database 'induvista_audit' -Statement "SELECT split_part(version(), ' ', 2);" -Quiet
Write-Host "  postgres: $($pgVersion.Trim())" -ForegroundColor Gray

$tsVersion = Invoke-Psql -Database 'induvista_audit' -Statement "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';" -Quiet
if (-not $tsVersion.Trim()) {
    throw "timescaledb extension is missing from induvista_audit"
}
Write-Host "  timescaledb: $($tsVersion.Trim())" -ForegroundColor Gray

# 5. Check .env password sanity.
Write-Host ""
$envPath = "D:\INDUVISTA\.env"
if (Test-Path $envPath) {
    $env = Get-Content $envPath -Raw
    if ($env -match 'change_this_password') {
        Write-Host "WARNING: .env still contains 'change_this_password' placeholder." -ForegroundColor Red
        Write-Host "         Fix with:" -ForegroundColor Red
        Write-Host "         (Get-Content $envPath) -replace 'change_this_password','induvista_dev_2026' | Set-Content $envPath" -ForegroundColor Yellow
    } elseif ($env -notmatch 'AUDIT_DATABASE_URL') {
        Write-Host "WARNING: .env does not contain AUDIT_DATABASE_URL" -ForegroundColor Red
    } else {
        Write-Host "  .env looks OK (AUDIT_DATABASE_URL set, no placeholder)" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Audit database ready. Now:" -ForegroundColor Green
Write-Host "  1. .\harden_audit_startup.ps1   # try/except wrap" -ForegroundColor Gray
Write-Host "  2. docker compose restart backend" -ForegroundColor Gray
Write-Host "  3. docker compose logs --tail=20 backend | Select-String audit" -ForegroundColor Gray
