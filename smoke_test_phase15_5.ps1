# =============================================================================
# Phase 15.5 -- Tier D stateful blocks smoke.
#
# Verifies:
#   Section 0: services healthy, migration 0039 applied
#   Section 1: calc_block_state table exists with FK
#   Section 2: 9 new block_type rows across 4 categories
#   Section 3: block evaluate() logic via docker exec python.
#              Tests use synthetic state + controlled now_wall so the
#              timer tests don't need real sleep().
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

foreach ($svc in @("backend", "postgres", "calc_evaluator")) {
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
if ($ver -eq "0039_calc_stateful_tier_d") {
    Pass "alembic_version = 0039_calc_stateful_tier_d"
} else {
    Fail "alembic_version = $ver (expected 0039_calc_stateful_tier_d)"
}


# ---- Section 1: state table ------------------------------------------------
Section "1. calc_block_state table created"

$tableExists = [int](Psql "SELECT count(*) FROM information_schema.tables WHERE table_name = 'calc_block_state'")
if ($tableExists -eq 1) {
    Pass "calc_block_state table exists"
} else {
    Fail "calc_block_state table missing"
}

$fkExists = [int](Psql "SELECT count(*) FROM information_schema.table_constraints WHERE constraint_name = 'calc_block_state_def_id_fk'")
if ($fkExists -eq 1) {
    Pass "FK calc_block_state -> calc_definitions exists"
} else {
    Fail "FK calc_block_state -> calc_definitions missing"
}


# ---- Section 2: new block rows --------------------------------------------
Section "2. 9 new stateful block types registered"

$expectedTimers = @('TON', 'TOF', 'TP')
foreach ($code in $expectedTimers) {
    $row = Psql "SELECT code, category, is_evaluable FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|timer\|t") {
        Pass "$code present, category=timer, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}

$expectedEdges = @('R_TRIG', 'F_TRIG')
foreach ($code in $expectedEdges) {
    $row = Psql "SELECT code, category, is_evaluable FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|edge_detector\|t") {
        Pass "$code present, category=edge_detector, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}

$expectedLatches = @('SR', 'RS')
foreach ($code in $expectedLatches) {
    $row = Psql "SELECT code, category, is_evaluable FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|latch\|t") {
        Pass "$code present, category=latch, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}

$expectedCounters = @('CTU', 'CTD')
foreach ($code in $expectedCounters) {
    $row = Psql "SELECT code, category, is_evaluable FROM calc_block_types WHERE code = '$code'"
    if ($row -match "$code\|counter\|t") {
        Pass "$code present, category=counter, is_evaluable=t"
    } else {
        Fail "$code row wrong: '$row'"
    }
}


# ---- Section 3: block evaluate() logic -------------------------------------
Section "3. Block evaluate() logic (via docker exec python)"

$py = @'
from app.workers.calc_blocks.base import InputSample, GOOD_QUALITY, GOOD_NON_SPECIFIC
from app.workers.calc_blocks.stateful_tier_d import (
    OnDelayTimer, OffDelayTimer, PulseTimer,
    RisingEdge, FallingEdge,
    SetReset, ResetSet,
    CountUp, CountDown,
)

def is_(name, condition):
    print(("PASS " if condition else "FAIL ") + name, flush=True)

GOOD = GOOD_QUALITY
BAD = 0

# ===== TON (on-delay) =====
# Input goes high at t=100.0, preset=500ms. Q should go high at t=100.5
cfg = {"input": 1, "preset_ms": 500}
state = {}

# t=100.0, IN=1: rising edge, start timer, Q=0 (just started)
r, state = OnDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 100.0)
is_("TON t=0ms rising edge -> Q=0, timer started", r.value == 0.0 and state["high_started_ts"] == 100.0)

# t=100.3, IN=1: still high, 300ms < 500ms, Q=0
r, state = OnDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 100.3)
is_("TON t=300ms (< preset) -> Q=0", r.value == 0.0)

# t=100.5, IN=1: 500ms elapsed, Q=1
r, state = OnDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 100.5)
is_("TON t=500ms (= preset) -> Q=1", r.value == 1.0)

# t=100.6, IN=0: input falls, Q=0 immediately, timer resets
r, state = OnDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 100.6)
is_("TON input falls -> Q=0, timer cleared", r.value == 0.0 and state["high_started_ts"] is None)

# Falls before preset: input high t=0..0.2 then low. Should never fire.
state = {}
r, state = OnDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 200.0)
r, state = OnDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 200.1)
r, state = OnDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 200.2)
is_("TON input falls before preset -> Q never went high", r.value == 0.0)

# ===== TOF (off-delay) =====
# IN high -> Q immediately high. IN goes low, Q stays high for preset_ms.
cfg = {"input": 1, "preset_ms": 500}
state = {}

r, state = OffDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 100.0)
is_("TOF input high -> Q=1 immediately", r.value == 1.0)

r, state = OffDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 100.1)
is_("TOF input just fell -> Q still 1 (within preset)", r.value == 1.0)

r, state = OffDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 100.5)
is_("TOF t=400ms after fall (< preset) -> Q=1", r.value == 1.0)

r, state = OffDelayTimer.evaluate(cfg, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 100.65)
is_("TOF t=550ms after fall (>= preset) -> Q=0", r.value == 0.0)

# ===== TP (pulse) =====
cfg = {"input": 1, "preset_ms": 500}
state = {}

# Rising edge -> pulse starts
r, state = PulseTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 100.0)
is_("TP rising edge -> pulse starts, Q=1", r.value == 1.0)

# Input falls during pulse - Q stays high (TP is non-retriggerable but pulse runs to completion)
r, state = PulseTimer.evaluate(cfg, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 100.2)
is_("TP input falls during pulse -> Q stays 1", r.value == 1.0)

# After preset -> Q drops
r, state = PulseTimer.evaluate(cfg, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 100.6)
is_("TP after preset -> Q=0", r.value == 0.0 and state["pulse_started_ts"] is None)

# New rising edge can start new pulse
r, state = PulseTimer.evaluate(cfg, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 101.0)
is_("TP new rising edge after pulse done -> new pulse, Q=1", r.value == 1.0)

# ===== R_TRIG (rising edge) =====
state = {}
r, state = RisingEdge.evaluate({"input": 1}, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 0)
is_("R_TRIG IN=0 first call -> Q=0", r.value == 0.0)
r, state = RisingEdge.evaluate({"input": 1}, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 0)
is_("R_TRIG IN rises -> Q=1 for one cycle", r.value == 1.0)
r, state = RisingEdge.evaluate({"input": 1}, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 0)
is_("R_TRIG IN stays high -> Q=0 on next cycle", r.value == 0.0)

# ===== F_TRIG (falling edge) =====
state = {}
r, state = FallingEdge.evaluate({"input": 1}, [InputSample(tag_id=1, value=1.0, quality=GOOD)], state, 0)
r, state = FallingEdge.evaluate({"input": 1}, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 0)
is_("F_TRIG IN falls -> Q=1 for one cycle", r.value == 1.0)
r, state = FallingEdge.evaluate({"input": 1}, [InputSample(tag_id=1, value=0.0, quality=GOOD)], state, 0)
is_("F_TRIG IN stays low -> Q=0 on next cycle", r.value == 0.0)

# ===== SR (set-dominant latch) =====
cfg = {"set": 1, "reset": 2}
state = {}

# S=1, R=0 -> Q=1
r, state = SetReset.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
is_("SR S=1 R=0 -> Q=1", r.value == 1.0)

# S=0, R=0 -> Q stays 1
r, state = SetReset.evaluate(cfg, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
is_("SR S=0 R=0 -> Q latches at 1", r.value == 1.0)

# R=1 -> Q=0
r, state = SetReset.evaluate(cfg, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
], state, 0)
is_("SR R=1 -> Q=0", r.value == 0.0)

# Both S=1 R=1 - SR is set-dominant: Q=1
state = {"q": 0.0}
r, state = SetReset.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
], state, 0)
is_("SR S=1 R=1 -> set-dominant gives Q=1", r.value == 1.0)

# ===== RS (reset-dominant latch) =====
cfg = {"set": 1, "reset": 2}
state = {"q": 1.0}

# S=1 R=1 - RS is reset-dominant: Q=0
r, state = ResetSet.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
], state, 0)
is_("RS S=1 R=1 -> reset-dominant gives Q=0", r.value == 0.0)

# ===== CTU (up counter) =====
cfg = {"count_up": 1, "reset": 2}
state = {}

# CU rising edge -> CV=1
r, state = CountUp.evaluate(cfg, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
is_("CTU initial CU=0 -> CV=0", r.value == 0.0)

r, state = CountUp.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
is_("CTU CU rising edge -> CV=1", r.value == 1.0)

# CU stays high -> CV stays 1 (no new edge)
r, state = CountUp.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
is_("CTU CU stays high -> CV unchanged at 1", r.value == 1.0)

# CU falls and rises again -> CV=2
r, state = CountUp.evaluate(cfg, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
r, state = CountUp.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
is_("CTU second rising edge -> CV=2", r.value == 2.0)

# Reset -> CV=0
r, state = CountUp.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
], state, 0)
is_("CTU reset=1 -> CV=0", r.value == 0.0)

# ===== CTD (down counter) =====
cfg = {"count_down": 1, "load": 2, "load_value": 10}
state = {}

# Load -> CV=10
r, state = CountDown.evaluate(cfg, [
    InputSample(tag_id=1, value=0.0, quality=GOOD),
    InputSample(tag_id=2, value=1.0, quality=GOOD),
], state, 0)
is_("CTD load=1 -> CV=load_value=10", r.value == 10.0)

# CD rising edge -> CV=9
r, state = CountDown.evaluate(cfg, [
    InputSample(tag_id=1, value=1.0, quality=GOOD),
    InputSample(tag_id=2, value=0.0, quality=GOOD),
], state, 0)
is_("CTD CD rising edge -> CV=9", r.value == 9.0)

# ===== BAD input propagation =====
state = {"in_was_high": True, "high_started_ts": 99.0}
r, state2 = OnDelayTimer.evaluate(
    {"input": 1, "preset_ms": 500},
    [InputSample(tag_id=1, value=None, quality=BAD)],
    state, 100.0,
)
is_("TON BAD input -> BAD output, state preserved",
    r.value is None and state2 == state)
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
Write-Host "Phase 15.5 Tier D stateful blocks verified." -ForegroundColor Cyan
exit 0
