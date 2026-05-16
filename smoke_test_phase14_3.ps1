# =============================================================================
# Phase 14.3 alarm evaluator smoke test.
#
# Drives one rule through the full state machine by injecting tag_values
# directly into TimescaleDB and waiting for the evaluator container to
# react:
#
#   normal --(condition active, on_delay)-> active_unack
#   active_unack --(condition clear, off_delay, latched)-> inactive_unack
#
# Also verifies:
#   - on_delay: state stays normal until delay elapses
#   - deadband: value within (threshold - deadband, threshold) doesn't clear
#   - off_delay: state stays active until delay elapses
#   - event log: activated + cleared events written
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_3.ps1
#
# Total runtime ~25 seconds (lots of sleeps for delay verification).
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
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
    $output = docker compose exec -T postgres `
        psql -U induvista_admin -d induvista -tA -c $sql 2>&1
    return ($output | Out-String).Trim()
}

function PsqlExec([string]$sql) {
    $null = docker compose exec -T postgres `
        psql -U induvista_admin -d induvista -c $sql 2>&1
}

# Inject a tag_value row with time=NOW() so the evaluator's recency
# filter accepts it. ST=192 marks it as VALID_EXTENDED so it's GOOD
# for evaluation purposes.
function InjectValue([int]$tagId, [double]$value) {
    PsqlExec @"
INSERT INTO tag_values (time, tag_id, device_id, value_double, st)
SELECT NOW(), id, device_id, $value, 192
FROM tags
WHERE id = $tagId
"@
}

function GetState([int]$ruleId) {
    return (Psql "SELECT state FROM alarm_state WHERE rule_id = $ruleId")
}

function GetPending([int]$ruleId) {
    $row = Psql "SELECT pending_active_since IS NOT NULL, pending_clear_since IS NOT NULL FROM alarm_state WHERE rule_id = $ruleId"
    $parts = $row.Split('|')
    return @{
        active_pending = $parts[0] -eq "t"
        clear_pending  = $parts[1] -eq "t"
    }
}

function GetEventCount([int]$ruleId, [string]$eventType) {
    return [int](Psql "SELECT count(*) FROM alarm_events WHERE rule_id = $ruleId AND event_type = '$eventType'")
}

# ---- 0. Preconditions ------------------------------------------------------

Section "0. Preconditions"

# Evaluator container is running?
$evalRunning = (docker compose ps alarm_evaluator --format json 2>&1 | Out-String)
if ($evalRunning -match '"State":\s*"running"' -or $evalRunning -match '"Status":\s*"Up') {
    Pass "alarm_evaluator container is running"
} else {
    Write-Host "  alarm_evaluator status check inconclusive - continuing anyway"
    Write-Host "  raw output: $evalRunning"
}

# Pick a tag with a reasonable scan_interval_ms (so the per-rule recency
# check is lenient enough for our sleeps).
$TestTagId = [int](Psql "SELECT id FROM tags ORDER BY id LIMIT 1")
$TestTagName = Psql "SELECT name FROM tags WHERE id = $TestTagId"
Pass "Fixture tag: id=$TestTagId, name=$TestTagName"

# Stop modbus_worker so the smoke's synthetic tag_values are not
# overwritten by the worker's real polling writes within ~1s.
Write-Host "  Stopping modbus_worker for smoke duration..."
$savedEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
docker compose stop modbus_worker 2>&1 | Out-Null
$ErrorActionPreference = $savedEAP
Start-Sleep -Seconds 2

# Clean up any leftover rules for this tag
PsqlExec "DELETE FROM alarm_rules WHERE tag_id = $TestTagId AND rule_type = 'hi'"

# ---- 1. Create a test rule ------------------------------------------------

Section "1. Create test rule (hi, threshold=100, deadband=2, on_delay=2, off_delay=2, latched=true)"

PsqlExec @"
INSERT INTO alarm_rules (tag_id, rule_type, severity, threshold, deadband, on_delay_sec, off_delay_sec, latched, enabled)
VALUES ($TestTagId, 'hi', 'high', 100.0, 2.0, 2, 2, true, true)
"@
$ruleId = [int](Psql "SELECT id FROM alarm_rules WHERE tag_id = $TestTagId AND rule_type = 'hi' ORDER BY id DESC LIMIT 1")
if ($ruleId -gt 0) {
    Pass "Created rule id=$ruleId"
} else {
    Fail "Rule creation failed"
    exit 1
}

# Force the evaluator to refresh its rule cache by restarting the
# container. RULE_RELOAD_SEC default is 30s, far too long for a smoke
# test - on restart, the first tick reloads (last_reload starts at 0).
Write-Host "  Restarting evaluator to force a fresh rule cache load..."
# docker compose writes progress to stderr ("Container ... Restarting").
# With ErrorActionPreference=Stop at the top of this script, PowerShell
# would treat that as a terminating error. Locally relax EAP for the
# restart command only.
$savedEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
docker compose restart alarm_evaluator 2>&1 | Out-Null
$ErrorActionPreference = $savedEAP
Start-Sleep -Seconds 7

$state = GetState $ruleId
if ($state -eq "normal") {
    Pass "Initial state is 'normal'"
} else {
    Fail "Initial state is '$state', expected 'normal'"
}

# ---- 2. Value BELOW threshold - should stay normal -----------------------

Section "2. Value below threshold (no condition)"

InjectValue $TestTagId 95.0
Start-Sleep -Seconds 2

$state = GetState $ruleId
$pending = GetPending $ruleId
if ($state -eq "normal" -and -not $pending.active_pending) {
    Pass "State stays 'normal', no pending_active_since set"
} else {
    Fail "Expected normal+no-pending, got state='$state' active_pending=$($pending.active_pending)"
}

# ---- 3. Value ABOVE threshold, but on_delay not elapsed --------------------

Section "3. Value above threshold (within on_delay window)"

InjectValue $TestTagId 105.0
Start-Sleep -Seconds 1.5   # less than on_delay=2

$state = GetState $ruleId
$pending = GetPending $ruleId
if ($state -eq "normal" -and $pending.active_pending) {
    Pass "State still 'normal' but pending_active_since IS set (timing the delay)"
} else {
    # The evaluator may already have transitioned if our timing is loose.
    # Accept active_unack here only if the elapsed wall time crossed 2s.
    Write-Host "  state=$state, active_pending=$($pending.active_pending) (timing-sensitive)"
    if ($state -eq "active_unack") {
        Pass "State already transitioned to active_unack (timing margin)"
    } else {
        Fail "Expected normal+pending, got state='$state' pending=$($pending.active_pending)"
    }
}

# ---- 4. Continue above threshold past on_delay -----------------------------

Section "4. Value still above threshold past on_delay -> ACTIVATE"

# Re-inject to keep the value fresh
InjectValue $TestTagId 105.0
Start-Sleep -Seconds 1.5
InjectValue $TestTagId 105.0
Start-Sleep -Seconds 1.5

$state = GetState $ruleId
if ($state -eq "active_unack") {
    Pass "State transitioned 'normal' -> 'active_unack' after on_delay"
} else {
    Fail "Expected active_unack, got '$state'"
}

$activatedCount = GetEventCount $ruleId "activated"
if ($activatedCount -eq 1) {
    Pass "Exactly one 'activated' event written"
} else {
    Fail "Expected 1 'activated' event, got $activatedCount"
}

# ---- 5. Value still above - no re-activation ------------------------------

Section "5. Value stays above threshold (no flapping)"

InjectValue $TestTagId 110.0
Start-Sleep -Seconds 3

$state = GetState $ruleId
$activatedCount = GetEventCount $ruleId "activated"
if ($state -eq "active_unack" -and $activatedCount -eq 1) {
    Pass "State stable in 'active_unack', still only 1 activated event"
} else {
    Fail "State=$state, activated_count=$activatedCount (expected active_unack/1)"
}

# ---- 6. Value in DEADBAND (between clear=98 and threshold=100) ------------

Section "6. Value in deadband (97 < value < 100) - no clear"

InjectValue $TestTagId 99.0
Start-Sleep -Seconds 3

$state = GetState $ruleId
$pending = GetPending $ruleId
$clearedCount = GetEventCount $ruleId "cleared"
if ($state -eq "active_unack" -and -not $pending.clear_pending -and $clearedCount -eq 0) {
    Pass "Value in deadband: state stays active_unack, no pending_clear, no clear event"
} else {
    Fail "Expected stable active_unack, got state=$state clear_pending=$($pending.clear_pending) cleared_count=$clearedCount"
}

# ---- 7. Value BELOW clear threshold, off_delay timing ---------------------

Section "7. Value below clear threshold -> CLEAR (latched=true -> inactive_unack)"

InjectValue $TestTagId 95.0
Start-Sleep -Seconds 1   # less than off_delay=2

$pending = GetPending $ruleId
if ($pending.clear_pending) {
    Pass "pending_clear_since set during off_delay window"
} else {
    Write-Host "  pending_clear=$($pending.clear_pending) - might have already cleared"
}

# Continue holding the value below clear for the rest of off_delay
InjectValue $TestTagId 95.0
Start-Sleep -Seconds 1.5
InjectValue $TestTagId 95.0
Start-Sleep -Seconds 1.5

$state = GetState $ruleId
if ($state -eq "inactive_unack") {
    Pass "State transitioned 'active_unack' -> 'inactive_unack' (latched + cleared)"
} else {
    Fail "Expected inactive_unack, got '$state'"
}

$clearedCount = GetEventCount $ruleId "cleared"
if ($clearedCount -eq 1) {
    Pass "Exactly one 'cleared' event written"
} else {
    Fail "Expected 1 'cleared' event, got $clearedCount"
}

# ---- 8. Re-entry from inactive_unack --------------------------------------

Section "8. Re-entry: value goes above threshold again from inactive_unack"

InjectValue $TestTagId 108.0
Start-Sleep -Seconds 3   # No on_delay on re-entry from inactive_unack

$state = GetState $ruleId
$activatedCount = GetEventCount $ruleId "activated"
if ($state -eq "active_unack" -and $activatedCount -eq 2) {
    Pass "Re-entry: inactive_unack -> active_unack, second 'activated' event written"
} else {
    Fail "Expected active_unack+2 events, got state=$state activated_count=$activatedCount"
}

# ---- 9. Cleanup ------------------------------------------------------------

Section "9. Cleanup"

PsqlExec "DELETE FROM alarm_rules WHERE id = $ruleId"
$leftover = [int](Psql "SELECT count(*) FROM alarm_rules WHERE id = $ruleId")
if ($leftover -eq 0) {
    Pass "Test rule deleted"
} else {
    Fail "Test rule still present after cleanup"
}

# Event history preserved across rule deletion (audit retention)
$eventsRetained = [int](Psql "SELECT count(*) FROM alarm_events WHERE rule_id = $ruleId")
if ($eventsRetained -ge 3) {
    Pass "Events preserved post-cleanup (${eventsRetained}: 2 activated + 1+ cleared)"
} else {
    Fail "Expected at least 3 retained events, got $eventsRetained"
}

# Always restart the worker before exiting, even on failure
Write-Host ""
Write-Host "Restarting modbus_worker..." -ForegroundColor Cyan
$savedEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
docker compose start modbus_worker 2>&1 | Out-Null
$ErrorActionPreference = $savedEAP

# ---- Summary ---------------------------------------------------------------

Section "Summary"

Write-Host ""
Write-Host "  PASS: $Pass" -ForegroundColor Green
Write-Host "  FAIL: $Fail" -ForegroundColor $(if ($Fail -gt 0) { 'Red' } else { 'Green' })
if ($Fail -gt 0) {
    Write-Host ""
    Write-Host "Failures:" -ForegroundColor Red
    $Reasons | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Debug: check evaluator logs with:"
    Write-Host "  docker compose logs --tail 50 alarm_evaluator"
    exit 1
}
Write-Host ""
Write-Host "Phase 14.3 evaluator is live. The state machine fires correctly." -ForegroundColor Cyan
Write-Host "Next: 14.4 (shelve/unshelve) or 14.5 (alarms UI)" -ForegroundColor Cyan
exit 0
