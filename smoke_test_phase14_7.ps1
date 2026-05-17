# =============================================================================
# Phase 14.7 -- Fully automated deviation / rate-of-change smoke test.
#
# Exercises the rolling-window analytics added in 14.7:
#   - deviation:      |value - rolling_mean(window)| > threshold
#   - rate_of_change: |least-squares slope| > threshold (units/sec)
#
# Verified end-to-end through the evaluator state machine:
#   - Migration 0033 added window_seconds column and check constraint
#   - Both rule types are now is_evaluable=true
#   - alarm_rules.window_seconds round-trips through the API
#   - Deviation rule stays normal when value tracks the mean
#   - Deviation rule fires when value departs the mean by more than threshold
#   - Rate-of-change rule fires when value climbs at a slope > threshold
#   - Insufficient samples (< MIN_WINDOW_SAMPLES) does NOT cause flapping
#   - Default message text is type-aware for both new types
#
# Same defensive patterns as the 14.9 smoke:
#   - Synthetic tag with enabled=false so no worker writes to it
#   - try/finally cleanup via single DELETE (cascades to rules + values)
#   - ASCII-only string literals (PowerShell CP1252 / UTF-8 quirk)
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_7.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Reasons = @()

$script:TestTagId = $null
$script:CreatedRuleIds = @()

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

function DockerQuiet([string[]]$cmdArgs) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & docker compose @cmdArgs 2>&1 | Out-Null
    $ErrorActionPreference = $saved
}

function Psql([string]$sql) {
    $output = docker compose exec -T postgres `
        psql -U induvista_admin -d induvista -tA -c $sql 2>&1
    return ($output | Out-String).Trim()
}

function PsqlExec([string]$sql) {
    $null = docker compose exec -T postgres `
        psql -U induvista_admin -d induvista -c $sql 2>&1
}

# Inject one value at a controlled point in history (secondsAgo back from NOW).
function InjectValueAt([int]$tagId, [double]$value, [int]$secondsAgo) {
    PsqlExec @"
INSERT INTO tag_values (time, tag_id, device_id, value_double, st)
SELECT NOW() - INTERVAL '$secondsAgo seconds', id, device_id, $value, 192
FROM tags WHERE id = $tagId
"@
}

function Http {
    param([string]$Method, [string]$Url, [object]$Body = $null)
    $params = @{ Method = $Method; Uri = $Url; ContentType = "application/json" }
    if ($Body -ne $null) {
        $params.Body = ($Body | ConvertTo-Json -Depth 6 -Compress)
    }
    try {
        $resp = Invoke-RestMethod @params
        return @{ ok = $true; status = 200; body = $resp }
    } catch {
        $status = 0
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        $detail = $null
        try { $detail = $_.ErrorDetails.Message } catch {}
        return @{ ok = $false; status = $status; body = $detail }
    }
}

try {
    # ---- 0. Service health -------------------------------------------------

    Section "0. Service health"

    foreach ($svc in @("backend", "alarm_evaluator", "postgres")) {
        $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
        if ($psout -match '"State":\s*"running"' -or $psout -match '"Status":\s*"Up') {
            Pass "$svc is running"
        } else {
            Fail "$svc is not running"
        }
    }

    # ---- 1. Migration: window_seconds column + is_evaluable flips ----------

    Section "1. Migration 0033"

    $hasCol = [int](Psql @"
SELECT count(*) FROM information_schema.columns
WHERE table_name = 'alarm_rules' AND column_name = 'window_seconds'
"@)
    if ($hasCol -eq 1) {
        Pass "alarm_rules.window_seconds column exists"
    } else {
        Fail "alarm_rules.window_seconds column is missing"
    }

    $hasCheck = [int](Psql @"
SELECT count(*) FROM pg_constraint
WHERE conname = 'ck_alarm_rules_window_seconds'
"@)
    if ($hasCheck -eq 1) {
        Pass "CHECK constraint on window_seconds present"
    } else {
        Fail "ck_alarm_rules_window_seconds is missing"
    }

    foreach ($code in @("deviation", "rate_of_change")) {
        $ev = Psql "SELECT is_evaluable::text FROM alarm_rule_types WHERE code = '$code'"
        if ($ev -eq "true") {
            Pass "$code is_evaluable=true"
        } else {
            Fail "$code is_evaluable should be true, got '$ev'"
        }
    }

    # ---- 2. Synthetic test tag --------------------------------------------

    Section "2. Setup synthetic test tag"

    $deviceId = [int](Psql "SELECT id FROM devices ORDER BY id LIMIT 1")
    if ($deviceId -le 0) {
        Fail "No devices in DB"
        return
    }

    PsqlExec "DELETE FROM tags WHERE name = 'smoke_phase_14_7_test_tag'"

    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $deviceId, 'smoke_phase_14_7_test_tag', 'float32', 3, 99998,
    false, 'Phase 14.7 smoke synthetic tag - safe to delete', 0, 200
)
"@

    $script:TestTagId = [int](Psql "SELECT id FROM tags WHERE name = 'smoke_phase_14_7_test_tag'")
    if ($script:TestTagId -le 0) {
        Fail "Failed to create synthetic test tag"
        return
    }
    Pass "Synthetic tag created (id=${script:TestTagId})"

    # ---- 3. window_seconds round-trips through API ------------------------

    Section "3. POST /alarms/rules with window_seconds round-trips"

    $createResp = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules" `
        -Body @{
            tag_id = $script:TestTagId
            rule_type = "deviation"
            severity = "high"
            threshold = 5.0
            deadband = 0.5
            on_delay_sec = 2
            off_delay_sec = 2
            latched = $false
            enabled = $true
            window_seconds = 60
        }

    if ($createResp.ok -and $createResp.body.window_seconds -eq 60) {
        $script:CreatedRuleIds += $createResp.body.id
        Pass "Rule created with window_seconds=60, round-trip OK"
    } else {
        Fail "POST /rules failed or window_seconds missing: $($createResp.status), $($createResp.body)"
        return
    }

    $devRuleId = $createResp.body.id

    # ---- 4. Deviation rule: stable values keep state normal ---------------

    Section "4. Deviation: stable mean -> state stays normal"

    # 6 GOOD historical samples all around 50 over the past 12 seconds
    InjectValueAt -tagId $script:TestTagId -value 49.8 -secondsAgo 12
    InjectValueAt -tagId $script:TestTagId -value 50.2 -secondsAgo 10
    InjectValueAt -tagId $script:TestTagId -value 50.1 -secondsAgo 8
    InjectValueAt -tagId $script:TestTagId -value 49.9 -secondsAgo 6
    InjectValueAt -tagId $script:TestTagId -value 50.0 -secondsAgo 4
    InjectValueAt -tagId $script:TestTagId -value 50.1 -secondsAgo 1

    # Restart evaluator to force rule cache reload
    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 4

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $devRuleId"
    if ($state -eq "normal") {
        Pass "State stays normal (latest 50.1, mean ~50, |diff| < 5)"
    } else {
        Fail "Expected normal, got '$state'"
    }

    # ---- 5. Deviation rule: spike triggers active_unack -------------------

    Section "5. Deviation: spike to 70 -> state becomes active_unack"

    # Latest value jumps to 70. Mean of the window is ~50, so deviation
    # ~= 20 which exceeds the 5 threshold easily.
    InjectValueAt -tagId $script:TestTagId -value 70.0 -secondsAgo 0
    Start-Sleep -Seconds 5  # on_delay (2) + tick (1) + buffer

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $devRuleId"
    if ($state -eq "active_unack") {
        Pass "State -> active_unack (deviation ~20 > threshold 5)"
    } else {
        Fail "Expected active_unack, got '$state'"
    }

    $activatedEvents = [int](Psql @"
SELECT count(*) FROM alarm_events
WHERE rule_id = $devRuleId AND event_type = 'activated'
"@)
    if ($activatedEvents -ge 1) {
        Pass "$activatedEvents 'activated' event(s) written"
    } else {
        Fail "Expected >= 1 'activated' event, got $activatedEvents"
    }

    # ---- 6. Default message is deviation-aware -----------------------------

    Section "6. Default message includes 'Deviation from rolling mean'"

    $msg = Psql @"
SELECT comment FROM alarm_events
WHERE rule_id = $devRuleId AND event_type = 'activated'
ORDER BY event_time DESC LIMIT 1
"@
    if ($msg -like "*Deviation from rolling mean*") {
        Pass "Default message uses deviation-aware wording: '$msg'"
    } else {
        Fail "Expected 'Deviation from rolling mean...', got '$msg'"
    }

    # ---- 7. Cleanup deviation rule, create rate_of_change rule ----------

    Section "7. Switch to rate_of_change rule"

    PsqlExec "DELETE FROM alarm_rules WHERE id = $devRuleId"
    $script:CreatedRuleIds = @($script:CreatedRuleIds | Where-Object { $_ -ne $devRuleId })

    # Threshold of 0.5 unit/sec. We'll inject a clear linear ramp at
    # 1 unit/sec which should comfortably exceed it.
    $rocResp = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules" `
        -Body @{
            tag_id = $script:TestTagId
            rule_type = "rate_of_change"
            severity = "critical"
            threshold = 0.5
            deadband = 0.1
            on_delay_sec = 2
            off_delay_sec = 2
            latched = $false
            enabled = $true
            window_seconds = 60
        }

    if (-not $rocResp.ok) {
        Fail "POST rate_of_change rule failed: $($rocResp.status), $($rocResp.body)"
        return
    }
    $rocRuleId = $rocResp.body.id
    $script:CreatedRuleIds += $rocRuleId
    Pass "rate_of_change rule created (id=$rocRuleId, threshold 0.5/sec, window 60s)"

    # Clear historical values from the deviation test so the slope
    # calc isn't polluted by the spike to 70 we left lying around.
    PsqlExec "DELETE FROM tag_values WHERE tag_id = ${script:TestTagId}"

    # Linear ramp: 50 at -12s, 52 at -10s, 54 at -8s, 56 at -6s,
    # 58 at -4s, 60 at -2s, 62 at NOW. dy/dt = 1 unit/sec across
    # the window, twice the threshold.
    InjectValueAt -tagId $script:TestTagId -value 50 -secondsAgo 12
    InjectValueAt -tagId $script:TestTagId -value 52 -secondsAgo 10
    InjectValueAt -tagId $script:TestTagId -value 54 -secondsAgo 8
    InjectValueAt -tagId $script:TestTagId -value 56 -secondsAgo 6
    InjectValueAt -tagId $script:TestTagId -value 58 -secondsAgo 4
    InjectValueAt -tagId $script:TestTagId -value 60 -secondsAgo 2
    InjectValueAt -tagId $script:TestTagId -value 62 -secondsAgo 0

    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 5  # restart + on_delay + tick + buffer

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $rocRuleId"
    if ($state -eq "active_unack") {
        Pass "State -> active_unack (slope ~1.0 units/sec > threshold 0.5)"
    } else {
        Fail "Expected active_unack, got '$state'"
    }

    # ---- 8. Default message is RoC-aware ----------------------------------

    Section "8. Default message includes 'Rate of change'"

    $msg = Psql @"
SELECT comment FROM alarm_events
WHERE rule_id = $rocRuleId AND event_type = 'activated'
ORDER BY event_time DESC LIMIT 1
"@
    if ($msg -like "*Rate of change*") {
        Pass "Default message uses RoC-aware wording: '$msg'"
    } else {
        Fail "Expected 'Rate of change...', got '$msg'"
    }

    # ---- 9. Insufficient samples doesn't trigger transition --------------

    Section "9. Insufficient samples -> no flap"

    # Clean rule + values, create fresh deviation rule with NO history.
    PsqlExec "DELETE FROM alarm_rules WHERE id = $rocRuleId"
    PsqlExec "DELETE FROM tag_values WHERE tag_id = ${script:TestTagId}"
    $script:CreatedRuleIds = @($script:CreatedRuleIds | Where-Object { $_ -ne $rocRuleId })

    $sparseResp = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules" `
        -Body @{
            tag_id = $script:TestTagId
            rule_type = "deviation"
            severity = "medium"
            threshold = 5.0; deadband = 0.5
            on_delay_sec = 1; off_delay_sec = 1
            latched = $false; enabled = $true
            window_seconds = 10
        }
    $sparseRuleId = $sparseResp.body.id
    $script:CreatedRuleIds += $sparseRuleId

    # Only 2 historical points - below MIN_WINDOW_SAMPLES (3)
    InjectValueAt -tagId $script:TestTagId -value 50 -secondsAgo 5
    InjectValueAt -tagId $script:TestTagId -value 75 -secondsAgo 0

    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 5

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $sparseRuleId"
    if ($state -eq "normal") {
        Pass "Insufficient-data scenario stayed normal (no false trigger)"
    } else {
        Fail "Expected normal under insufficient data, got '$state'"
    }
}
finally {
    Section "Cleanup"
    if ($script:TestTagId) {
        PsqlExec "DELETE FROM tags WHERE id = ${script:TestTagId}"
        Write-Host "  Deleted synthetic tag id=${script:TestTagId} (cascaded)"
    }
}

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
Write-Host "Phase 14.7 deviation + rate_of_change fully verified." -ForegroundColor Cyan
exit 0
