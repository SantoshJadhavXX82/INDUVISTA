# =============================================================================
# Phase 15.3 -- Tier B selection blocks smoke (FIXED).
#
# Fixes vs first revision:
#   - InputSample(tag_id=, value=, quality=) - tag_id is required per
#     the dataclass shape used by calc_evaluator.py latest_inputs()
#   - PowerShell error pref switched to 'Continue' around the docker
#     exec block so Python tracebacks are fully visible and the script
#     can decide what to do, instead of halting on first stderr line
# =============================================================================

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Pass = 0
$Fail = 0
$Reasons = @()

function Pass([string]$msg) {
    Write-Host "[PASS] $msg" -ForegroundColor Green
    $script:Pass++
}
function Fail([string]$msg) {
    Write-Host "[FAIL] $msg" -ForegroundColor Red
    $script:Fail++
    $script:Reasons += $msg
}
function Section([string]$name) {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host "  $name" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor Cyan
}
function Psql([string]$sql) {
    $out = docker compose exec -T postgres psql -U induvista_admin -d induvista -tA -c $sql 2>&1
    return ($out | Out-String).Trim()
}

function WaitForBackend([int]$maxSec = 60) {
    $deadline = (Get-Date).AddSeconds($maxSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" `
                -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}


# ---- Section 0 -------------------------------------------------------------
Section "0. Service health + migration"

foreach ($svc in @("backend", "postgres")) {
    $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
    if ($psout -match '"State":\s*"running"' -or $psout -match '"Status":\s*"Up') {
        Pass "$svc is up"
    } else {
        Fail "$svc not up"
    }
}
if (WaitForBackend -maxSec 60) {
    Pass "Backend /health responsive"
} else {
    Fail "Backend did not respond"
    throw "backend down"
}

$ver = Psql "SELECT version_num FROM alembic_version"
if ($ver -eq "0037_calc_selection_tier_b") {
    Pass "alembic_version = 0037_calc_selection_tier_b"
} else {
    Fail "alembic_version = $ver (expected 0037_calc_selection_tier_b)"
}


# ---- Section 1 -------------------------------------------------------------
Section "1. Migration registered 6 selection block types"

$expectedCodes = @(
    'FIRST_GOOD','LAST_GOOD','HOT_STANDBY',
    'HIGHEST_QUALITY','VOTING_M_OF_N','MUX_INDEX'
)
foreach ($code in $expectedCodes) {
    $row = Psql "SELECT code, category, is_evaluable, rank FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|selection\|t\|") {
        Pass "$code present, category=selection, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}

$selectionCount = [int](Psql "SELECT count(*) FROM calc_block_types WHERE category = 'selection'")
if ($selectionCount -eq 6) {
    Pass "selection category has exactly 6 rows"
} else {
    Fail "selection category has $selectionCount rows (expected 6)"
}


# ---- Section 2 -------------------------------------------------------------
Section "2. Block evaluate() logic (via docker exec python)"

# IMPORTANT: every InputSample(...) construction includes tag_id - required
# per the dataclass shape used throughout calc_evaluator.py.
$py = @'
from app.workers.calc_blocks.base import InputSample, GOOD_QUALITY, GOOD_NON_SPECIFIC
from app.workers.calc_blocks.selection_tier_b import (
    FirstGood, LastGood, HighestQuality, HotStandby, VotingMofN, MuxIndex,
)

def is_(name, condition):
    print(("PASS " if condition else "FAIL ") + name, flush=True)

GOOD = GOOD_QUALITY
BAD = 0

# -- FIRST_GOOD ----
r = FirstGood.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=50.0, quality=GOOD),
    InputSample(tag_id=3, value=60.0, quality=GOOD),
])
is_("FIRST_GOOD skips BAD, picks first GOOD", r.value == 50.0 and r.quality == GOOD_NON_SPECIFIC)

r = FirstGood.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=None, quality=BAD),
    InputSample(tag_id=3, value=99.0, quality=GOOD),
])
is_("FIRST_GOOD walks past multiple BAD inputs", r.value == 99.0)

r = FirstGood.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=None, quality=BAD),
    InputSample(tag_id=3, value=None, quality=BAD),
])
is_("FIRST_GOOD all BAD -> None with BAD quality", r.value is None and r.quality == BAD)

# -- LAST_GOOD ----
r = LastGood.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=10.0, quality=GOOD),
    InputSample(tag_id=2, value=20.0, quality=GOOD),
    InputSample(tag_id=3, value=None, quality=BAD),
])
is_("LAST_GOOD ignores trailing BAD, picks last GOOD", r.value == 20.0)

r = LastGood.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=10.0, quality=GOOD),
    InputSample(tag_id=2, value=None, quality=BAD),
    InputSample(tag_id=3, value=None, quality=BAD),
])
is_("LAST_GOOD walks back past multiple BAD inputs", r.value == 10.0)

# -- HIGHEST_QUALITY ----
r = HighestQuality.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=128),
    InputSample(tag_id=2, value=2.0, quality=192),
    InputSample(tag_id=3, value=3.0, quality=64),
])
is_("HIGHEST_QUALITY picks 192 over 128 and 64", r.value == 2.0 and r.quality == 192)

r = HighestQuality.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=64),
    InputSample(tag_id=2, value=2.0, quality=64),
    InputSample(tag_id=3, value=3.0, quality=64),
])
is_("HIGHEST_QUALITY all BAD -> picks first, output quality=BAD", r.value == 1.0 and r.quality == 64)

r = HighestQuality.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=None, quality=BAD),
    InputSample(tag_id=3, value=None, quality=BAD),
])
is_("HIGHEST_QUALITY all None -> None output", r.value is None)

# -- HOT_STANDBY ----
r = HotStandby.evaluate({"primary": 1, "standby": 2}, [
    InputSample(tag_id=1, value=50.0, quality=GOOD),
    InputSample(tag_id=2, value=60.0, quality=GOOD),
])
is_("HOT_STANDBY primary GOOD -> primary value", r.value == 50.0 and r.quality == GOOD_NON_SPECIFIC)

r = HotStandby.evaluate({"primary": 1, "standby": 2}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=60.0, quality=GOOD),
])
is_("HOT_STANDBY primary BAD -> standby value", r.value == 60.0 and r.quality == GOOD_NON_SPECIFIC)

r = HotStandby.evaluate({"primary": 1, "standby": 2}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=None, quality=BAD),
])
is_("HOT_STANDBY both BAD -> None", r.value is None and r.quality == BAD)

try:
    HotStandby.validate_config({"primary": 5, "standby": 5})
    is_("HOT_STANDBY rejects same tag for primary+standby", False)
except ValueError:
    is_("HOT_STANDBY rejects same tag for primary+standby", True)

# -- VOTING_M_OF_N ----
r = VotingMofN.evaluate(
    {"inputs": [1,2,3], "tolerance": 1.0},
    [
        InputSample(tag_id=1, value=10.0, quality=GOOD),
        InputSample(tag_id=2, value=10.0, quality=GOOD),
        InputSample(tag_id=3, value=100.0, quality=GOOD),
    ]
)
is_("VOTING 2-of-3 majority agreement -> median of cluster", r.value == 10.0 and r.quality == GOOD_NON_SPECIFIC)

r = VotingMofN.evaluate(
    {"inputs": [1,2,3], "tolerance": 1.0},
    [
        InputSample(tag_id=1, value=10.0, quality=GOOD),
        InputSample(tag_id=2, value=50.0, quality=GOOD),
        InputSample(tag_id=3, value=100.0, quality=GOOD),
    ]
)
is_("VOTING no agreement -> BAD", r.value is None)

r = VotingMofN.evaluate(
    {"inputs": [1,2,3,4,5], "tolerance": 0.5},
    [
        InputSample(tag_id=1, value=10.0, quality=GOOD),
        InputSample(tag_id=2, value=10.0, quality=GOOD),
        InputSample(tag_id=3, value=10.0, quality=GOOD),
        InputSample(tag_id=4, value=100.0, quality=GOOD),
        InputSample(tag_id=5, value=200.0, quality=GOOD),
    ]
)
is_("VOTING 3-of-5 cluster wins", r.value == 10.0)

r = VotingMofN.evaluate(
    {"inputs": [1,2,3,4,5], "tolerance": 0.5, "min_agreement": 4},
    [
        InputSample(tag_id=1, value=10.0, quality=GOOD),
        InputSample(tag_id=2, value=10.0, quality=GOOD),
        InputSample(tag_id=3, value=10.0, quality=GOOD),
        InputSample(tag_id=4, value=100.0, quality=GOOD),
        InputSample(tag_id=5, value=200.0, quality=GOOD),
    ]
)
is_("VOTING min_agreement=4 with only 3 agreeing -> BAD", r.value is None)

try:
    VotingMofN.validate_config({"inputs": [1,2,3]})
    is_("VOTING_M_OF_N rejects config without tolerance", False)
except ValueError:
    is_("VOTING_M_OF_N rejects config without tolerance", True)

# -- MUX_INDEX ----
r = MuxIndex.evaluate(
    {"index": 1, "values": [2,3,4]},
    [
        InputSample(tag_id=1, value=0.0, quality=GOOD),
        InputSample(tag_id=2, value=100.0, quality=GOOD),
        InputSample(tag_id=3, value=200.0, quality=GOOD),
        InputSample(tag_id=4, value=300.0, quality=GOOD),
    ]
)
is_("MUX_INDEX index=0 -> values[0]", r.value == 100.0)

r = MuxIndex.evaluate(
    {"index": 1, "values": [2,3,4]},
    [
        InputSample(tag_id=1, value=2.0, quality=GOOD),
        InputSample(tag_id=2, value=100.0, quality=GOOD),
        InputSample(tag_id=3, value=200.0, quality=GOOD),
        InputSample(tag_id=4, value=300.0, quality=GOOD),
    ]
)
is_("MUX_INDEX index=2 -> values[2]", r.value == 300.0)

r = MuxIndex.evaluate(
    {"index": 1, "values": [2,3,4]},
    [
        InputSample(tag_id=1, value=5.0, quality=GOOD),
        InputSample(tag_id=2, value=100.0, quality=GOOD),
        InputSample(tag_id=3, value=200.0, quality=GOOD),
        InputSample(tag_id=4, value=300.0, quality=GOOD),
    ]
)
is_("MUX_INDEX out-of-range -> BAD", r.value is None and r.quality == BAD)

r = MuxIndex.evaluate(
    {"index": 1, "values": [2,3,4]},
    [
        InputSample(tag_id=1, value=None, quality=BAD),
        InputSample(tag_id=2, value=100.0, quality=GOOD),
        InputSample(tag_id=3, value=200.0, quality=GOOD),
        InputSample(tag_id=4, value=300.0, quality=GOOD),
    ]
)
is_("MUX_INDEX index BAD -> BAD output", r.value is None)

try:
    MuxIndex.validate_config({"index": 5, "values": [5, 6, 7]})
    is_("MUX_INDEX rejects index tag in values list", False)
except ValueError:
    is_("MUX_INDEX rejects index tag in values list", True)

# -- inputs() method ----
ids = MuxIndex.inputs({"index": 7, "values": [8, 9, 10]})
is_("MUX_INDEX.inputs() puts index first", ids == [7, 8, 9, 10])

ids = HotStandby.inputs({"primary": 11, "standby": 12})
is_("HOT_STANDBY.inputs() returns [primary, standby]", ids == [11, 12])
'@

# Switch off the global Stop pref so Python errors don't truncate the
# traceback. We capture exit code explicitly instead.
$savedPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$pyOutput = ($py | docker compose exec -T backend python -u 2>&1) | Out-String
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $savedPref

Write-Host $pyOutput

if ($exitCode -ne 0) {
    Fail "Python script exited with code $exitCode (full output above)"
} else {
    $passLines = ([regex]::Matches($pyOutput, "(?m)^PASS ")).Count
    $failLines = ([regex]::Matches($pyOutput, "(?m)^FAIL ")).Count
    $script:Pass += $passLines
    $script:Fail += $failLines
    if ($failLines -gt 0) {
        foreach ($m in [regex]::Matches($pyOutput, "(?m)^FAIL (.+)$")) {
            $script:Reasons += $m.Groups[1].Value
        }
    }
}


# ---- Summary ----
Section "Summary"
Write-Host ""
Write-Host "  PASS: $Pass" -ForegroundColor Green
Write-Host "  FAIL: $Fail" -ForegroundColor $(if ($Fail -gt 0) { 'Red' } else { 'Green' })

if ($Fail -gt 0) {
    Write-Host ""
    Write-Host "Failures:" -ForegroundColor Red
    $Reasons | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

Write-Host ""
Write-Host "Phase 15.3 Tier B selection blocks verified." -ForegroundColor Cyan
exit 0
