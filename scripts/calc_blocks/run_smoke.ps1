# =============================================================================
# Calc Blocks smoke runner -- wraps scripts/calc_blocks/smoke_test_all_blocks.py
# and surfaces results in the same Pass/Fail format as smoke_test_phase16_0a.ps1.
#
# Usage:
#   .\scripts\calc_blocks\run_smoke.ps1
#   .\scripts\calc_blocks\run_smoke.ps1 -Cleanup
#   .\scripts\calc_blocks\run_smoke.ps1 -Quick
#   .\scripts\calc_blocks\run_smoke.ps1 -Base http://localhost:8000
# =============================================================================
[CmdletBinding()]
param(
    [string] $Base = "http://127.0.0.1:8000",
    [switch] $Cleanup,
    [switch] $Quick,
    [int]    $Section = -1
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host ("=" * 70) -ForegroundColor Cyan
Write-Host "  Calc Blocks Smoke Test" -ForegroundColor Cyan
Write-Host ("=" * 70) -ForegroundColor Cyan
Write-Host "  Base:    $Base"
Write-Host "  Cleanup: $($Cleanup.IsPresent)"
Write-Host "  Quick:   $($Quick.IsPresent)"
Write-Host ""

# Prefer python from PATH; fall back to py launcher on Windows
$pythonExe = $null
foreach ($cand in @("python", "python3", "py")) {
    $found = Get-Command $cand -ErrorAction SilentlyContinue
    if ($found) { $pythonExe = $found.Path; break }
}
if (-not $pythonExe) {
    Write-Host "[FAIL] could not find python on PATH" -ForegroundColor Red
    exit 2
}
Write-Host "  Using python: $pythonExe"

# Resolve the smoke script path
$script = Join-Path $here "smoke_test_all_blocks.py"
if (-not (Test-Path $script)) {
    Write-Host "[FAIL] smoke script not found: $script" -ForegroundColor Red
    exit 2
}

$pyArgs = @($script, "--base", $Base)
if ($Cleanup)        { $pyArgs += "--cleanup" }
if ($Quick)          { $pyArgs += "--quick" }
if ($Section -ge 0)  { $pyArgs += @("--section", $Section.ToString()) }

# Run, stream output as-is so the Python script's coloured Pass/Fail shows up
& $pythonExe @pyArgs
$exit = $LASTEXITCODE

Write-Host ""
if ($exit -eq 0) {
    Write-Host "[OVERALL: PASS]" -ForegroundColor Green
} else {
    Write-Host "[OVERALL: FAIL] exit=$exit" -ForegroundColor Red
}
exit $exit
