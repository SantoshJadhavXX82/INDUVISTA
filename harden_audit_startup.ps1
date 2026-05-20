# =============================================================================
# Phase 16.0g hotfix - Harden the audit startup hook.
#
# Wraps ensure_audit_schema() in try/except so a misconfigured audit DB
# logs loudly but doesn't crash the backend. Idempotent.
# =============================================================================

$ErrorActionPreference = 'Stop'

$mainPath = "D:\INDUVISTA\backend\app\main.py"
if (-not (Test-Path $mainPath)) { throw "main.py not found at $mainPath" }

$content = Get-Content $mainPath -Raw

# Already hardened?
if ($content -match 'AUDIT SCHEMA SETUP FAILED at startup') {
    Write-Host "Startup hook already hardened. Nothing to do." -ForegroundColor Yellow
    exit 0
}

# Find and replace the bare hook with the try/except version.
$bareHook = @'
@app.on_event("startup")
def _audit_schema_startup() -> None:
    # Phase 16.0g: create audit_log table + hypertable + retention on
    # backend startup. Idempotent.
    ensure_audit_schema()
'@

$hardenedHook = @'
@app.on_event("startup")
def _audit_schema_startup() -> None:
    # Phase 16.0g: create audit_log table + hypertable + retention on
    # backend startup. Idempotent. Wrapped so a misconfigured audit DB
    # cannot crash the backend - audit failures degrade gracefully and
    # log loudly so operators notice.
    try:
        ensure_audit_schema()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "AUDIT SCHEMA SETUP FAILED at startup: %s. "
            "Backend continues, but audit will not be recorded until resolved.",
            e,
        )
'@

if ($content -notmatch [regex]::Escape($bareHook)) {
    Write-Host "ERROR: bare startup hook not found at expected location." -ForegroundColor Red
    Write-Host "       Either it's already been modified, or wire_audit_endpoints.ps1 wasn't run." -ForegroundColor Red
    exit 1
}

$content = $content -replace [regex]::Escape($bareHook), $hardenedHook
Set-Content $mainPath $content -NoNewline

Write-Host "Startup hook hardened. Verify:" -ForegroundColor Green
Select-String -Path $mainPath -Pattern 'AUDIT SCHEMA SETUP FAILED'
