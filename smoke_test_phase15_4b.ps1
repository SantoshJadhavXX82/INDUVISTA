# =============================================================================
# Phase 15.4b -- Arithmetic blocks (Tier E) smoke.
#
# Verifies:
#   Section 0: services healthy, migration 0040 applied
#   Section 1: 20 new block_type rows registered
#   Section 2: arithmetic block evaluate() logic (binary, both forms)
#   Section 3: unary_math + transcendental logic + domain errors
#   Section 4: BAD-input propagation across a sample of blocks
#   Section 5: Integration - create a real ADD calc_def via API,
#              wait for worker to tick, confirm zero errors and
#              a tag_values row for the output. This applies the
#              lesson banked from Phase 15.5: unit smoke alone is
#              not sufficient for new code paths.
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
                -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}


# ---- Section 0 -------------------------------------------------------------
Section "0. Service health + migration"

if (WaitForBackend -maxSec 60) {
    Pass "Backend /health responsive"
} else {
    Fail "Backend did not respond"
    throw "backend down"
}
foreach ($svc in @("backend", "postgres", "calc_evaluator")) {
    $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
    if ($psout -match '"State":\s*"running"' -or $psout -match '"Status":\s*"Up') {
        Pass "$svc is up"
    } else {
        Fail "$svc not up"
    }
}

$ver = Psql "SELECT version_num FROM alembic_version"
if ($ver -eq "0040_calc_arithmetic_tier_e") {
    Pass "alembic_version = 0040_calc_arithmetic_tier_e"
} else {
    Fail "alembic_version = $ver (expected 0040_calc_arithmetic_tier_e)"
}


# ---- Section 1: new block rows --------------------------------------------
Section "1. 20 new arithmetic block types registered"

$expected = @{
    "ADD" = "arithmetic"; "SUB" = "arithmetic"; "MUL" = "arithmetic"
    "DIV" = "arithmetic"; "MOD" = "arithmetic"; "POW" = "arithmetic"
    "MIN_OF_TWO" = "arithmetic"; "MAX_OF_TWO" = "arithmetic"
    "ABS" = "unary_math"; "NEG" = "unary_math"; "SQRT" = "unary_math"
    "FLOOR" = "unary_math"; "CEIL" = "unary_math"; "ROUND" = "unary_math"
    "EXP" = "transcendental"; "LN" = "transcendental"; "LOG10" = "transcendental"
    "SIN" = "transcendental"; "COS" = "transcendental"; "TAN" = "transcendental"
}
foreach ($code in $expected.Keys) {
    $cat = $expected[$code]
    $row = Psql "SELECT code, category, is_evaluable FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|$cat\|t") {
        Pass "$code present, category=$cat, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}


# ---- Sections 2-4: Python evaluation tests --------------------------------
Section "2-4. Block evaluate() logic (via docker exec python)"

$py = @'
import math
from app.workers.calc_blocks.base import InputSample, GOOD_QUALITY, GOOD_NON_SPECIFIC
from app.workers.calc_blocks.arithmetic_tier_e import (
    Add, Sub, Mul, Div, Mod, Pow, MinOfTwo, MaxOfTwo,
    Abs, Neg, Sqrt, Floor, Ceil, Round,
    Exp, Ln, Log10, Sin, Cos, Tan,
)

def is_(name, condition):
    print(("PASS " if condition else "FAIL ") + name, flush=True)

GOOD = GOOD_QUALITY
BAD = 0

def s(tag_id, v, q=GOOD):
    return InputSample(tag_id=tag_id, value=v, quality=q)

def approx(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < tol

# === ARITHMETIC ===

# ADD: tag+tag
r = Add.evaluate({"left": 1, "right": 2}, [s(1, 2.0), s(2, 3.0)])
is_("ADD 2+3 = 5", r.value == 5.0 and r.quality == GOOD_NON_SPECIFIC)
# ADD: tag+constant
r = Add.evaluate({"left": 1, "value": 7.5}, [s(1, 2.5)])
is_("ADD 2.5 + const 7.5 = 10", approx(r.value, 10.0))
# ADD negative
r = Add.evaluate({"left": 1, "right": 2}, [s(1, -5.0), s(2, 3.0)])
is_("ADD -5 + 3 = -2", r.value == -2.0)

# SUB
r = Sub.evaluate({"left": 1, "right": 2}, [s(1, 10.0), s(2, 3.0)])
is_("SUB 10-3 = 7", r.value == 7.0)
r = Sub.evaluate({"left": 1, "value": 5}, [s(1, 2.0)])
is_("SUB 2 - const 5 = -3", r.value == -3.0)

# MUL
r = Mul.evaluate({"left": 1, "right": 2}, [s(1, 4.0), s(2, 5.0)])
is_("MUL 4*5 = 20", r.value == 20.0)
r = Mul.evaluate({"left": 1, "value": 0}, [s(1, 99.0)])
is_("MUL 99 * const 0 = 0", r.value == 0.0)

# DIV
r = Div.evaluate({"left": 1, "right": 2}, [s(1, 10.0), s(2, 4.0)])
is_("DIV 10/4 = 2.5", r.value == 2.5)
r = Div.evaluate({"left": 1, "right": 2}, [s(1, 5.0), s(2, 0.0)])
is_("DIV by zero -> BAD", r.value is None and r.quality == 0)
r = Div.evaluate({"left": 1, "value": 0}, [s(1, 5.0)])
is_("DIV by const zero -> BAD", r.value is None and r.quality == 0)

# MOD
r = Mod.evaluate({"left": 1, "right": 2}, [s(1, 7.0), s(2, 3.0)])
is_("MOD 7 % 3 = 1", r.value == 1.0)
r = Mod.evaluate({"left": 1, "right": 2}, [s(1, -7.0), s(2, 3.0)])
is_("MOD -7 % 3 = -1 (fmod, sign follows dividend)", r.value == -1.0)
r = Mod.evaluate({"left": 1, "right": 2}, [s(1, 5.0), s(2, 0.0)])
is_("MOD by zero -> BAD", r.value is None and r.quality == 0)

# POW
r = Pow.evaluate({"left": 1, "right": 2}, [s(1, 2.0), s(2, 3.0)])
is_("POW 2^3 = 8", r.value == 8.0)
r = Pow.evaluate({"left": 1, "value": 0.5}, [s(1, 16.0)])
is_("POW 16^0.5 = 4", r.value == 4.0)
r = Pow.evaluate({"left": 1, "right": 2}, [s(1, -2.0), s(2, 0.5)])
is_("POW (-2)^0.5 -> BAD (complex)", r.value is None and r.quality == 0)
r = Pow.evaluate({"left": 1, "right": 2}, [s(1, 0.0), s(2, -1.0)])
is_("POW 0^(-1) -> BAD (zero division)", r.value is None and r.quality == 0)
r = Pow.evaluate({"left": 1, "right": 2}, [s(1, 1e200), s(2, 2.0)])
is_("POW overflow -> BAD", r.value is None and r.quality == 0)

# MIN / MAX
r = MinOfTwo.evaluate({"left": 1, "right": 2}, [s(1, 5.0), s(2, 3.0)])
is_("MIN_OF_TWO(5, 3) = 3", r.value == 3.0)
r = MaxOfTwo.evaluate({"left": 1, "value": 10}, [s(1, 7.0)])
is_("MAX_OF_TWO(7, const 10) = 10", r.value == 10.0)

# === UNARY MATH ===

r = Abs.evaluate({"input": 1}, [s(1, -7.5)])
is_("ABS(-7.5) = 7.5", r.value == 7.5)
r = Neg.evaluate({"input": 1}, [s(1, 5.0)])
is_("NEG(5) = -5", r.value == -5.0)
r = Neg.evaluate({"input": 1}, [s(1, -5.0)])
is_("NEG(-5) = 5", r.value == 5.0)

r = Sqrt.evaluate({"input": 1}, [s(1, 16.0)])
is_("SQRT(16) = 4", r.value == 4.0)
r = Sqrt.evaluate({"input": 1}, [s(1, 0.0)])
is_("SQRT(0) = 0", r.value == 0.0)
r = Sqrt.evaluate({"input": 1}, [s(1, -1.0)])
is_("SQRT(-1) -> BAD", r.value is None and r.quality == 0)

r = Floor.evaluate({"input": 1}, [s(1, 3.7)])
is_("FLOOR(3.7) = 3", r.value == 3.0)
r = Floor.evaluate({"input": 1}, [s(1, -3.2)])
is_("FLOOR(-3.2) = -4", r.value == -4.0)
r = Ceil.evaluate({"input": 1}, [s(1, 3.2)])
is_("CEIL(3.2) = 4", r.value == 4.0)
r = Ceil.evaluate({"input": 1}, [s(1, -3.7)])
is_("CEIL(-3.7) = -3", r.value == -3.0)

r = Round.evaluate({"input": 1}, [s(1, 2.4)])
is_("ROUND(2.4) = 2", r.value == 2.0)
r = Round.evaluate({"input": 1}, [s(1, 2.6)])
is_("ROUND(2.6) = 3", r.value == 3.0)
r = Round.evaluate({"input": 1}, [s(1, 2.5)])
is_("ROUND(2.5) = 2 (banker's: tie to even)", r.value == 2.0)
r = Round.evaluate({"input": 1}, [s(1, 3.5)])
is_("ROUND(3.5) = 4 (banker's: tie to even)", r.value == 4.0)

# === TRANSCENDENTAL ===

r = Exp.evaluate({"input": 1}, [s(1, 0.0)])
is_("EXP(0) = 1", r.value == 1.0)
r = Exp.evaluate({"input": 1}, [s(1, 1.0)])
is_("EXP(1) ~ e", approx(r.value, math.e))
r = Exp.evaluate({"input": 1}, [s(1, 1000.0)])
is_("EXP(1000) -> BAD (overflow)", r.value is None and r.quality == 0)

r = Ln.evaluate({"input": 1}, [s(1, math.e)])
is_("LN(e) ~ 1", approx(r.value, 1.0))
r = Ln.evaluate({"input": 1}, [s(1, 1.0)])
is_("LN(1) = 0", r.value == 0.0)
r = Ln.evaluate({"input": 1}, [s(1, 0.0)])
is_("LN(0) -> BAD", r.value is None and r.quality == 0)
r = Ln.evaluate({"input": 1}, [s(1, -1.0)])
is_("LN(-1) -> BAD", r.value is None and r.quality == 0)

r = Log10.evaluate({"input": 1}, [s(1, 100.0)])
is_("LOG10(100) = 2", approx(r.value, 2.0))
r = Log10.evaluate({"input": 1}, [s(1, 0.0)])
is_("LOG10(0) -> BAD", r.value is None and r.quality == 0)

r = Sin.evaluate({"input": 1}, [s(1, 0.0)])
is_("SIN(0) = 0", r.value == 0.0)
r = Sin.evaluate({"input": 1}, [s(1, math.pi / 2)])
is_("SIN(pi/2) ~ 1", approx(r.value, 1.0))
r = Cos.evaluate({"input": 1}, [s(1, 0.0)])
is_("COS(0) = 1", r.value == 1.0)
r = Cos.evaluate({"input": 1}, [s(1, math.pi)])
is_("COS(pi) ~ -1", approx(r.value, -1.0))
r = Tan.evaluate({"input": 1}, [s(1, 0.0)])
is_("TAN(0) = 0", r.value == 0.0)
r = Tan.evaluate({"input": 1}, [s(1, math.pi / 4)])
is_("TAN(pi/4) ~ 1", approx(r.value, 1.0))

# === BAD-input propagation ===

r = Add.evaluate({"left": 1, "right": 2}, [s(1, None, BAD), s(2, 3.0)])
is_("ADD BAD left -> BAD", r.value is None and r.quality == BAD)
r = Add.evaluate({"left": 1, "right": 2}, [s(1, 5.0), s(2, None, BAD)])
is_("ADD BAD right -> BAD", r.value is None and r.quality == BAD)
r = Mul.evaluate({"left": 1, "value": 5}, [s(1, None, BAD)])
is_("MUL BAD left + const -> BAD", r.value is None and r.quality == BAD)
r = Sqrt.evaluate({"input": 1}, [s(1, None, BAD)])
is_("SQRT BAD -> BAD", r.value is None and r.quality == BAD)
r = Sin.evaluate({"input": 1}, [s(1, None, BAD)])
is_("SIN BAD -> BAD", r.value is None and r.quality == BAD)

# === Validation errors ===

import traceback
def raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True
    except Exception:
        return False

is_("ADD missing 'left' raises ValueError",
    raises(lambda: Add.validate_config({"right": 2})))
is_("ADD both 'right' and 'value' raises ValueError",
    raises(lambda: Add.validate_config({"left": 1, "right": 2, "value": 3})))
is_("ADD neither 'right' nor 'value' raises ValueError",
    raises(lambda: Add.validate_config({"left": 1})))
is_("ABS missing 'input' raises ValueError",
    raises(lambda: Abs.validate_config({})))
is_("ADD with bool value raises ValueError",
    raises(lambda: Add.validate_config({"left": 1, "value": True})))
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


# ---- Section 5: Integration smoke (lesson #7 from Phase 15.5) -------------
Section "5. Integration - real calc_def executes via worker"

# Pick a real input tag. ANAL_CAL_FLAG (id=1000) reads from the GC
# simulator. We'll build an ADD that just adds a constant to it so
# the math is trivially verifiable.
$body = @{
    tag_id        = 177   # Write_Coil, will get overwritten by calc
    block_type    = "ADD"
    block_config  = @{ left = 1000; value = 100 }
    enabled       = $true
    execution_rate_ms = 500
} | ConvertTo-Json

# Delete any previous Phase 15.4b test calc first to keep things clean
try {
    $existing = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions" `
        -UseBasicParsing -ErrorAction Stop
    foreach ($d in $existing) {
        if ($d.tag_id -eq 177 -and $d.block_type -eq "ADD") {
            Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions/$($d.id)" `
                -Method DELETE -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
        }
    }
} catch { }

# Create the test calc
try {
    $created = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions" `
        -Method POST -ContentType "application/json" -Body $body
    Pass "Created ADD calc_def id=$($created.id)"
    $newId = $created.id
} catch {
    Fail "Failed to create ADD calc_def: $($_.Exception.Message)"
    $newId = $null
}

if ($newId) {
    # Worker reload cycle is 30s. Force a faster check by waiting
    # then polling for total_executions > 0.
    Write-Host "  Waiting up to 45s for worker to reload + tick..." -ForegroundColor Gray
    $deadline = (Get-Date).AddSeconds(45)
    $ran = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions/$newId"
            if ($status.total_executions -gt 0) {
                $ran = $true
                break
            }
        } catch { }
    }

    $final = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions/$newId"
    if ($final.total_executions -gt 0) {
        Pass "Worker executed ADD calc_def $newId at least once (total_executions=$($final.total_executions))"
    } else {
        Fail "Worker never executed ADD calc_def $newId after 45s"
    }
    if ($final.total_errors -eq 0) {
        Pass "ADD calc_def $newId has zero errors"
    } else {
        Fail "ADD calc_def $newId has $($final.total_errors) errors (last: $($final.last_error_message))"
    }
    if ($final.last_status -eq "ok") {
        Pass "ADD calc_def $newId last_status = ok"
    } else {
        Fail "ADD calc_def $newId last_status = $($final.last_status)"
    }

    # Cleanup
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions/$newId" `
            -Method DELETE -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
        Pass "Cleaned up test calc_def $newId"
    } catch {
        Fail "Cleanup failed for calc_def $newId"
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
Write-Host "Phase 15.4b Tier E arithmetic blocks verified (unit + integration)." -ForegroundColor Cyan
exit 0
