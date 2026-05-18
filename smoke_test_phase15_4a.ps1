# =============================================================================
# Phase 15.4a -- Conditional, comparison, and logical blocks smoke.
#
# Verifies:
#   Section 0: services healthy, migration 0038 applied
#   Section 1: IF_THEN_ELSE flipped to is_evaluable=true
#   Section 2: 10 new rows in calc_block_types
#              (6 comparison + 4 logical)
#   Section 3: block evaluate() logic for all 11 blocks via docker exec
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
if ($ver -eq "0038_calc_conditional_logic") {
    Pass "alembic_version = 0038_calc_conditional_logic"
} else {
    Fail "alembic_version = $ver (expected 0038_calc_conditional_logic)"
}


# ---- Section 1: IF_THEN_ELSE flip -----------------------------------------
Section "1. IF_THEN_ELSE flipped to evaluable"

$ifEval = Psql "SELECT is_evaluable FROM calc_block_types WHERE code = 'IF_THEN_ELSE'"
if ($ifEval -eq "t") {
    Pass "IF_THEN_ELSE.is_evaluable = true"
} else {
    Fail "IF_THEN_ELSE.is_evaluable = $ifEval (expected t)"
}


# ---- Section 2: new block rows --------------------------------------------
Section "2. 10 new block types registered (6 comparison + 4 logical)"

$expectedComparison = @('GT','LT','GTE','LTE','EQ','NE')
foreach ($code in $expectedComparison) {
    $row = Psql "SELECT code, category, is_evaluable FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|comparison\|t") {
        Pass "$code present, category=comparison, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}

$expectedLogical = @('AND_OF','OR_OF','XOR_OF','NOT')
foreach ($code in $expectedLogical) {
    $row = Psql "SELECT code, category, is_evaluable FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|logical\|t") {
        Pass "$code present, category=logical, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}

$compCount = [int](Psql "SELECT count(*) FROM calc_block_types WHERE category = 'comparison'")
if ($compCount -eq 6) {
    Pass "comparison category has exactly 6 rows"
} else {
    Fail "comparison category has $compCount rows (expected 6)"
}
$logCount = [int](Psql "SELECT count(*) FROM calc_block_types WHERE category = 'logical'")
if ($logCount -eq 4) {
    Pass "logical category has exactly 4 rows"
} else {
    Fail "logical category has $logCount rows (expected 4)"
}


# ---- Section 3: block evaluate() logic -------------------------------------
Section "3. Block evaluate() logic (via docker exec python)"

$py = @'
from app.workers.calc_blocks.base import InputSample, GOOD_QUALITY, GOOD_NON_SPECIFIC
from app.workers.calc_blocks.conditional_logic_tier_c import (
    IfThenElse,
    GreaterThan, LessThan, GreaterThanOrEqual, LessThanOrEqual,
    Equal, NotEqual,
    AndOf, OrOf, XorOf, Not,
)

def is_(name, condition):
    print(("PASS " if condition else "FAIL ") + name, flush=True)

GOOD = GOOD_QUALITY
BAD = 0

# -- IF_THEN_ELSE ----
r = IfThenElse.evaluate({"condition": 1, "then_value": 2, "else_value": 3}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=42.0, quality=GOOD),
    InputSample(tag_id=3, value=99.0, quality=GOOD),
])
is_("IF_THEN_ELSE condition TRUE picks then_value", r.value == 42.0)

r = IfThenElse.evaluate({"condition": 1, "then_value": 2, "else_value": 3}, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=42.0, quality=GOOD),
    InputSample(tag_id=3, value=99.0, quality=GOOD),
])
is_("IF_THEN_ELSE condition FALSE picks else_value", r.value == 99.0)

r = IfThenElse.evaluate({"condition": 1, "then_value": 2, "else_value": 3}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=42.0, quality=GOOD),
    InputSample(tag_id=3, value=99.0, quality=GOOD),
])
is_("IF_THEN_ELSE condition BAD -> BAD output", r.value is None and r.quality == BAD)

r = IfThenElse.evaluate({"condition": 1, "then_value": 2, "else_value": 3}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=None, quality=BAD),
    InputSample(tag_id=3, value=99.0, quality=GOOD),
])
is_("IF_THEN_ELSE chosen branch BAD propagates", r.value is None and r.quality == BAD)

# -- Comparison: tag-vs-tag ----
r = GreaterThan.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=10.0, quality=GOOD),
    InputSample(tag_id=2, value=5.0, quality=GOOD),
])
is_("GT 10>5 = 1.0", r.value == 1.0 and r.quality == GOOD_NON_SPECIFIC)

r = GreaterThan.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=5.0, quality=GOOD),
    InputSample(tag_id=2, value=10.0, quality=GOOD),
])
is_("GT 5>10 = 0.0", r.value == 0.0)

r = LessThan.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=5.0, quality=GOOD),
    InputSample(tag_id=2, value=10.0, quality=GOOD),
])
is_("LT 5<10 = 1.0", r.value == 1.0)

r = GreaterThanOrEqual.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=5.0, quality=GOOD),
    InputSample(tag_id=2, value=5.0, quality=GOOD),
])
is_("GTE 5>=5 = 1.0", r.value == 1.0)

r = LessThanOrEqual.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=5.0, quality=GOOD),
    InputSample(tag_id=2, value=5.0, quality=GOOD),
])
is_("LTE 5<=5 = 1.0", r.value == 1.0)

# -- Comparison: tag-vs-constant ----
r = GreaterThan.evaluate({"left": 1, "value": 5.0}, [
    InputSample(tag_id=1, value=10.0, quality=GOOD),
])
is_("GT tag-vs-constant 10>5 = 1.0", r.value == 1.0)

r = LessThan.evaluate({"left": 1, "value": 100.0}, [
    InputSample(tag_id=1, value=50.0, quality=GOOD),
])
is_("LT tag-vs-constant 50<100 = 1.0", r.value == 1.0)

# -- EQ / NE with tolerance ----
r = Equal.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
])
is_("EQ strict 1.0==1.0 = 1.0", r.value == 1.0)

r = Equal.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0000001, quality=GOOD),
])
is_("EQ strict 1.0 != 1.0000001 = 0.0", r.value == 0.0)

r = Equal.evaluate({"left": 1, "right": 2, "tolerance": 0.001}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0000001, quality=GOOD),
])
is_("EQ tolerance=0.001 -> 1.0 within band = 1.0", r.value == 1.0)

r = NotEqual.evaluate({"left": 1, "right": 2, "tolerance": 0.001}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.5, quality=GOOD),
])
is_("NE tolerance=0.001, 1.0 vs 1.5 -> 1.0", r.value == 1.0)

# -- Comparison: BAD input propagation ----
r = GreaterThan.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=None, quality=BAD),
    InputSample(tag_id=2, value=5.0, quality=GOOD),
])
is_("GT left BAD -> BAD", r.value is None)

r = GreaterThan.evaluate({"left": 1, "right": 2}, [
    InputSample(tag_id=1, value=10.0, quality=GOOD),
    InputSample(tag_id=2, value=None, quality=BAD),
])
is_("GT right BAD -> BAD", r.value is None)

# -- Comparison validation ----
try:
    GreaterThan.validate_config({"left": 1})
    is_("GT rejects config with neither right nor value", False)
except ValueError:
    is_("GT rejects config with neither right nor value", True)

try:
    GreaterThan.validate_config({"left": 1, "right": 2, "value": 5})
    is_("GT rejects config with both right and value", False)
except ValueError:
    is_("GT rejects config with both right and value", True)

# -- AND_OF ----
r = AndOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
    InputSample(tag_id=3, value=1.0, quality=GOOD),
])
is_("AND_OF all true -> 1.0", r.value == 1.0)

r = AndOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
    InputSample(tag_id=3, value=1.0, quality=GOOD),
])
is_("AND_OF one false -> 0.0", r.value == 0.0)

r = AndOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=None, quality=BAD),
    InputSample(tag_id=3, value=1.0, quality=GOOD),
])
is_("AND_OF any BAD input -> BAD output", r.value is None)

# -- OR_OF ----
r = OrOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
    InputSample(tag_id=3, value=0.0, quality=GOOD),
])
is_("OR_OF all false -> 0.0", r.value == 0.0)

r = OrOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
    InputSample(tag_id=3, value=0.0, quality=GOOD),
])
is_("OR_OF one true -> 1.0", r.value == 1.0)

# -- XOR_OF (parity) ----
r = XorOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
    InputSample(tag_id=3, value=0.0, quality=GOOD),
])
is_("XOR_OF 1 true (odd) -> 1.0", r.value == 1.0)

r = XorOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
    InputSample(tag_id=3, value=0.0, quality=GOOD),
])
is_("XOR_OF 2 true (even) -> 0.0", r.value == 0.0)

r = XorOf.evaluate({"inputs": [1,2,3]}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
    InputSample(tag_id=3, value=1.0, quality=GOOD),
])
is_("XOR_OF 3 true (odd) -> 1.0", r.value == 1.0)

# -- NOT ----
r = Not.evaluate({"input": 1}, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
])
is_("NOT(1.0) = 0.0", r.value == 0.0)

r = Not.evaluate({"input": 1}, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
])
is_("NOT(0.0) = 1.0", r.value == 1.0)

r = Not.evaluate({"input": 1}, [
    InputSample(tag_id=1, value=None, quality=BAD),
])
is_("NOT(BAD) -> BAD", r.value is None)

# -- inputs() ordering ----
ids = IfThenElse.inputs({"condition": 5, "then_value": 6, "else_value": 7})
is_("IF_THEN_ELSE.inputs() returns [cond, then, else]", ids == [5, 6, 7])

ids = GreaterThan.inputs({"left": 8, "right": 9})
is_("GT.inputs() tag-vs-tag returns [left, right]", ids == [8, 9])

ids = GreaterThan.inputs({"left": 8, "value": 5.0})
is_("GT.inputs() tag-vs-constant returns [left] only", ids == [8])
'@

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
Write-Host "Phase 15.4a conditional/comparison/logical blocks verified." -ForegroundColor Cyan
exit 0
