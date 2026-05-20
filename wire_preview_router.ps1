# Wire the preview router into main.py (idempotent - safe to run twice).
#
# Adds:
#   from app.api.preview import router as preview_router
#   app.include_router(preview_router)

$mainPath = "D:\INDUVISTA\backend\app\main.py"

if (-not (Test-Path $mainPath)) {
    Write-Error "main.py not found at $mainPath"
    exit 1
}

$content = Get-Content $mainPath -Raw

$importLine = "from app.api.preview import router as preview_router"
$includeLine = "app.include_router(preview_router)"

$changed = $false

# Add import after the last existing 'from app.api...' line
if ($content -notmatch [regex]::Escape($importLine)) {
    # Find the last 'from app.api' import line
    $lines = $content -split "`r?`n"
    $lastApiImportIdx = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^from app\.api\.") {
            $lastApiImportIdx = $i
        }
    }
    if ($lastApiImportIdx -ge 0) {
        $lines = $lines[0..$lastApiImportIdx] + @($importLine) + $lines[($lastApiImportIdx + 1)..($lines.Count - 1)]
        $content = $lines -join "`r`n"
        Write-Host "  + added import: $importLine"
        $changed = $true
    } else {
        Write-Warning "Could not find any 'from app.api.' lines; please add the import manually:"
        Write-Warning "    $importLine"
    }
}

# Add include_router after the last existing include_router(...)
if ($content -notmatch [regex]::Escape($includeLine)) {
    $lines = $content -split "`r?`n"
    $lastIncludeIdx = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*app\.include_router\(") {
            $lastIncludeIdx = $i
        }
    }
    if ($lastIncludeIdx -ge 0) {
        $lines = $lines[0..$lastIncludeIdx] + @($includeLine) + $lines[($lastIncludeIdx + 1)..($lines.Count - 1)]
        $content = $lines -join "`r`n"
        Write-Host "  + added router include: $includeLine"
        $changed = $true
    } else {
        Write-Warning "Could not find any 'app.include_router(...)' lines; please add manually:"
        Write-Warning "    $includeLine"
    }
}

if ($changed) {
    Set-Content -Path $mainPath -Value $content -NoNewline
    Write-Host "  -> updated $mainPath"
} else {
    Write-Host "  preview router already wired (no changes)"
}
