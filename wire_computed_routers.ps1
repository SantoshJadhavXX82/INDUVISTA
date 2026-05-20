# Wire-in patcher for Phase 17.0a new routers.
#
# Idempotently adds two import lines and two include_router calls to
# D:\INDUVISTA\backend\app\main.py.
#
# Run: powershell -ExecutionPolicy Bypass -File .\wire_computed_routers.ps1
#
$ErrorActionPreference = "Stop"

$mainPath = "D:\INDUVISTA\backend\app\main.py"
if (-not (Test-Path $mainPath)) {
    Write-Error "main.py not found at $mainPath"
    exit 1
}

$content = Get-Content $mainPath -Raw

# --- Imports ---------------------------------------------------------------
$importComputedDevices = "from app.api import computed_devices"
$importComputedTags    = "from app.api import computed_tags"

if ($content -notmatch [regex]::Escape($importComputedDevices)) {
    # Find the last "from app.api import" line and add after it.
    $lines = $content -split "`r?`n"
    $lastApiImportIdx = -1
    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match "^from app\.api import ") {
            $lastApiImportIdx = $i
        }
    }
    if ($lastApiImportIdx -lt 0) {
        Write-Error "Could not find an existing 'from app.api import' line; cannot patch."
        exit 1
    }
    $before = $lines[0..$lastApiImportIdx]
    $after  = $lines[($lastApiImportIdx + 1)..($lines.Length - 1)]
    $patched = @($before) + @($importComputedDevices, $importComputedTags) + @($after)
    $content = $patched -join "`r`n"
    Write-Host "+ added imports"
} else {
    Write-Host "= imports already present"
}

# --- include_router calls --------------------------------------------------
$includeDevices = "app.include_router(computed_devices.router)"
$includeTags    = "app.include_router(computed_tags.router)"

if ($content -notmatch [regex]::Escape($includeDevices)) {
    # Find the last include_router line and add after it.
    $lines = $content -split "`r?`n"
    $lastIncludeIdx = -1
    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match "^app\.include_router\(") {
            $lastIncludeIdx = $i
        }
    }
    if ($lastIncludeIdx -lt 0) {
        Write-Error "Could not find an existing 'app.include_router(...)' line; cannot patch."
        exit 1
    }
    $before = $lines[0..$lastIncludeIdx]
    $after  = $lines[($lastIncludeIdx + 1)..($lines.Length - 1)]
    $patched = @($before) + @($includeDevices, $includeTags) + @($after)
    $content = $patched -join "`r`n"
    Write-Host "+ added include_router calls"
} else {
    Write-Host "= include_router calls already present"
}

# --- Backup + write --------------------------------------------------------
Copy-Item $mainPath "$mainPath.bak_phase17a" -Force
Set-Content -Path $mainPath -Value $content -NoNewline

Write-Host ""
Write-Host "main.py patched. Backup: $mainPath.bak_phase17a"
Write-Host "Restart backend to apply: docker compose restart backend"
