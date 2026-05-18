# =============================================================================
# Phase 14.10 -- Frozen + Spike alarm rule type smoke.
#
# Verifies:
#   Section 0: services healthy + backend ready + evaluator running
#   Section 1: migration 0036 applied
#                 - frozen.is_evaluable = true
#                 - frozen.label = 'Frozen'
#                 - spike row exists with is_evaluable = true
#   Section 2: rule_types endpoint reflects 8 evaluable types (was 7)
#   Section 3: synthetic input tags created
#   Section 4: FROZEN positive - constant value triggers active_unack
#   Section 5: FROZEN negative - varying value stays normal
#   Section 6: SPIKE positive - large sample jump triggers active_unack
#   Section 7: SPIKE negative - small jump stays normal
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_10.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Pass = 0
$Fail = 0
$Reasons = @()
$script:CreatedTagIds = @()

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
function PsqlExec([string]$sql) {
    $null = docker compose exec -T postgres psql -U induvista_admin -d induvista -c $sql 2>&1
}
function DockerQuiet([string[]]$cmdArgs) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & docker compose @cmdArgs 2>&1 | Out-Null
    $ErrorActionPreference = $saved
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

# Insert a tag_values row at a specific time offset (seconds before now).
# Uses 'modbus' source which is allowed by the ck_tag_values_source CHECK.
function InjectValue {
    param([int]$tagId, [double]$value, [int]$secondsAgo, [int]$quality = 192)
    PsqlExec @"
INSERT INTO tag_values (time, tag_id, device_id, value_double, st, source)
SELECT NOW() - make_interval(secs => $secondsAgo), id, device_id, $value, $quality, 'modbus'
FROM tags WHERE id = $tagId
"@
}

# Create a synthetic tag for testing
function CreateTag {
    param([string]$name, [int]$address)
    $deviceId = [int](Psql "SELECT id FROM devices WHERE name != 'Calculations' ORDER BY id LIMIT 1")
    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $deviceId, '$name', 'float32', 3, $address,
    false, 'Phase 14.10 smoke - safe to delete', -10000, 10000
)
"@
    $id = [int](Psql "SELECT id FROM tags WHERE name = '$name'")
    $script:CreatedTagIds += $id
    return $id
}

# Create an alarm rule directly via SQL (avoids API auth complications)
function CreateRule {
    param(
        [int]$tagId,
        [string]$ruleType,
        [double]$threshold,
        [double]$deadband = 0,
        [int]$windowSeconds = 0,
        [string]$severity = 'high'
    )
    $windowVal = if ($windowSeconds -gt 0) { $windowSeconds } else { 'NULL' }
    PsqlExec @"
INSERT INTO alarm_rules (
    tag_id, rule_type, severity, threshold, deadband,
    on_delay_sec, off_delay_sec, latched, enabled, window_seconds
) VALUES (
    $tagId, '$ruleType', '$severity', $threshold, $deadband,
    0, 0, false, true, $windowVal
)
"@
    return [int](Psql "SELECT id FROM alarm_rules WHERE tag_id = $tagId AND rule_type = '$ruleType' ORDER BY id DESC LIMIT 1")
}

function GetState([int]$ruleId) {
    return Psql "SELECT state FROM alarm_state WHERE rule_id = $ruleId"
}

try {
    # ---- Section 0: Services + readiness ----------------------------------

    Section "0. Service health"

    foreach ($svc in @("backend", "postgres", "alarm_evaluator")) {
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

    # ---- Section 1: Migration 0036 ----------------------------------------

    Section "1. Migration 0036"

    $frozenEval = (Psql "SELECT is_evaluable FROM alarm_rule_types WHERE code='frozen'")
    if ($frozenEval -eq 't') {
        Pass "frozen.is_evaluable = true"
    } else {
        Fail "frozen.is_evaluable = $frozenEval"
    }

    $frozenLabel = (Psql "SELECT label FROM alarm_rule_types WHERE code='frozen'")
    if ($frozenLabel -eq 'Frozen') {
        Pass "frozen.label = 'Frozen' (capitalized)"
    } else {
        Fail "frozen.label = '$frozenLabel'"
    }

    $spikeExists = (Psql "SELECT count(*) FROM alarm_rule_types WHERE code='spike' AND is_evaluable=true")
    if ($spikeExists -eq '1') {
        Pass "spike rule type present and evaluable"
    } else {
        Fail "spike row missing or not evaluable: count=$spikeExists"
    }

    # ---- Section 2: rule-types endpoint ----------------------------------

    Section "2. /api/alarms/rule-types reflects new types"

    try {
        $types = Invoke-RestMethod -Uri "http://localhost:8000/api/alarms/rule-types" `
            -TimeoutSec 5
        $evaluable = ($types | Where-Object { $_.is_evaluable }).Count
        if ($evaluable -ge 8) {
            Pass "$evaluable evaluable types (>= 8 expected after frozen+spike)"
        } else {
            Fail "Only $evaluable evaluable types"
        }
        $spikeRow = $types | Where-Object { $_.code -eq 'spike' } | Select-Object -First 1
        if ($spikeRow -and $spikeRow.label -eq 'Spike') {
            Pass "API returns spike with label 'Spike'"
        } else {
            Fail "spike not properly exposed via API"
        }
    } catch {
        Fail "rule-types endpoint failed: $_"
    }

    # ---- Section 3: Synthetic input tags ---------------------------------

    Section "3. Synthetic input tags"

    # Clean any leftover smoke state
    PsqlExec "DELETE FROM alarm_rules WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'smoke_phase_14_10_%')"
    PsqlExec "DELETE FROM tags WHERE name LIKE 'smoke_phase_14_10_%'"

    $tagFrozenPos = CreateTag -name "smoke_phase_14_10_frozen_pos" -address 99800
    $tagFrozenNeg = CreateTag -name "smoke_phase_14_10_frozen_neg" -address 99801
    $tagSpikePos  = CreateTag -name "smoke_phase_14_10_spike_pos"  -address 99802
    $tagSpikeNeg  = CreateTag -name "smoke_phase_14_10_spike_neg"  -address 99803
    Pass "Four synthetic tags created"

    # ---- Section 4: FROZEN positive --------------------------------------

    Section "4. FROZEN positive: constant value triggers active_unack"

    # Inject 6 samples at value 50.0 over the past 12 seconds (every 2s).
    # Window of 15s catches them all; max-min = 0 <= threshold of 0.5.
    foreach ($t in @(2, 4, 6, 8, 10, 12)) {
        InjectValue -tagId $tagFrozenPos -value 50.0 -secondsAgo $t
    }
    # Plus one current value so the evaluator's "latest value" branch fires
    InjectValue -tagId $tagFrozenPos -value 50.0 -secondsAgo 0

    $ruleFrozenPos = CreateRule -tagId $tagFrozenPos -ruleType "frozen" `
        -threshold 0.5 -deadband 0.5 -windowSeconds 15

    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 5  # evaluator restart + at least 2 ticks

    $state = GetState -ruleId $ruleFrozenPos
    if ($state -eq 'active_unack') {
        Pass "Frozen rule transitioned to active_unack (value stuck at 50.0)"
    } else {
        Fail "Frozen positive: expected active_unack, got '$state'"
    }

    # ---- Section 5: FROZEN negative --------------------------------------

    Section "5. FROZEN negative: varying value stays normal"

    # Inject 6 samples ranging 40-60 - delta = 20, way above threshold 0.5
    InjectValue -tagId $tagFrozenNeg -value 40.0 -secondsAgo 12
    InjectValue -tagId $tagFrozenNeg -value 45.0 -secondsAgo 10
    InjectValue -tagId $tagFrozenNeg -value 50.0 -secondsAgo 8
    InjectValue -tagId $tagFrozenNeg -value 55.0 -secondsAgo 6
    InjectValue -tagId $tagFrozenNeg -value 60.0 -secondsAgo 4
    InjectValue -tagId $tagFrozenNeg -value 50.0 -secondsAgo 2
    InjectValue -tagId $tagFrozenNeg -value 50.0 -secondsAgo 0

    $ruleFrozenNeg = CreateRule -tagId $tagFrozenNeg -ruleType "frozen" `
        -threshold 0.5 -deadband 0.5 -windowSeconds 15

    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 5

    $state = GetState -ruleId $ruleFrozenNeg
    if ($state -eq 'normal') {
        Pass "Frozen rule stayed normal (value varying 40-60)"
    } else {
        Fail "Frozen negative: expected normal, got '$state'"
    }

    # ---- Section 6: SPIKE positive ---------------------------------------

    Section "6. SPIKE positive: large jump triggers active_unack"

    # Inject prior sample at 50.0, then current at 100.0 (delta=50, above threshold=10)
    InjectValue -tagId $tagSpikePos -value 50.0 -secondsAgo 4
    InjectValue -tagId $tagSpikePos -value 100.0 -secondsAgo 1

    $ruleSpikePos = CreateRule -tagId $tagSpikePos -ruleType "spike" `
        -threshold 10.0 -deadband 0

    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 5

    $state = GetState -ruleId $ruleSpikePos
    if ($state -eq 'active_unack') {
        Pass "Spike rule transitioned to active_unack (50 -> 100, delta 50 > 10)"
    } else {
        Fail "Spike positive: expected active_unack, got '$state'"
    }

    # ---- Section 7: SPIKE negative ---------------------------------------

    Section "7. SPIKE negative: small delta stays normal"

    # Two samples close together, delta=1 which is < threshold 10
    InjectValue -tagId $tagSpikeNeg -value 50.0 -secondsAgo 4
    InjectValue -tagId $tagSpikeNeg -value 51.0 -secondsAgo 1

    $ruleSpikeNeg = CreateRule -tagId $tagSpikeNeg -ruleType "spike" `
        -threshold 10.0 -deadband 0

    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 5

    $state = GetState -ruleId $ruleSpikeNeg
    if ($state -eq 'normal') {
        Pass "Spike rule stayed normal (50 -> 51, delta 1 < 10)"
    } else {
        Fail "Spike negative: expected normal, got '$state'"
    }

}
finally {
    Section "Cleanup"

    PsqlExec "DELETE FROM alarm_rules WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'smoke_phase_14_10_%')"
    PsqlExec "DELETE FROM tags WHERE name LIKE 'smoke_phase_14_10_%'"
    Write-Host "  Deleted synthetic tags + rules"
}

Section "Summary"
Write-Host ""
Write-Host "  PASS: $Pass" -ForegroundColor Green
Write-Host "  FAIL: $Fail" -ForegroundColor $(if ($Fail -gt 0) { 'Red' } else { 'Green' })

if ($Fail -gt 0) {
    Write-Host ""
    Write-Host "Failures:" -ForegroundColor Red
    $Reasons | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Debug helpers:" -ForegroundColor Yellow
    Write-Host "  docker compose logs --tail 60 alarm_evaluator"
    Write-Host "  docker compose exec postgres psql -U induvista_admin -d induvista -c 'SELECT r.id, r.rule_type, r.threshold, r.window_seconds, s.state FROM alarm_rules r JOIN alarm_state s USING(rule_id=r.id, id=s.rule_id) WHERE r.tag_id IN (SELECT id FROM tags WHERE name LIKE %14_10%)'"
    exit 1
}

Write-Host ""
Write-Host "Phase 14.10 frozen + spike alarm types verified." -ForegroundColor Cyan
exit 0
