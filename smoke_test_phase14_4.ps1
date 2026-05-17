# =============================================================================
# Phase 14.4b — Fully automated shelve/unshelve smoke test.
#
# Self-contained verification of:
#   - Service health (backend, alarm_evaluator, postgres)
#   - OpenAPI registration of the 3 new endpoints
#   - Schema migration (alarm_state has shelved_until, shelve_user_id)
#   - Every state-transition path through the shelve/unshelve API
#   - Evaluator auto-unshelve when shelved_until expires
#   - Event log captures all expected entries
#   - Cleanup runs even if the smoke fails midway
#
# Defensive structure (lessons from yesterday's PowerShell rodeo):
#   - All Where-Object pipelines wrapped in @() so single-result unwrap doesn't
#     bite. ${var} interpolation around colons. INSERT-then-SELECT split for
#     getting RETURNING ids. Docker compose calls wrapped in EAP=Continue so
#     stderr progress isn't treated as a terminating error. modbus_worker
#     stopped during the smoke so it can't overwrite synthetic state.
#
# Total runtime ~90s (most of which is the 70s wait for the auto-unshelve
# expiry test in section 12).
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_4.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Reasons = @()

# Capture state so the finally{} block can restore even on hard exit
$script:ModbusWasStopped = $false
$script:TestRuleId       = $null
$script:TestTagId        = $null

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

# Run a docker compose subcommand quietly, swallowing stderr progress so
# PowerShell doesn't escalate it to a terminating error.
function DockerQuiet([string[]]$args) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & docker compose @args 2>&1 | Out-Null
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

# HTTP helper that captures status + body separately for both success and
# error responses, so smoke assertions can check expected 4xx responses
# without the script bailing.
function Http {
    param(
        [string]$Method,
        [string]$Url,
        [object]$Body = $null
    )
    $params = @{
        Method      = $Method
        Uri         = $Url
        ContentType = "application/json"
    }
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
        try {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $detail = $reader.ReadToEnd()
        } catch {}
        return @{ ok = $false; status = $status; body = $detail }
    }
}

try {
    # ---- 0. Service health ------------------------------------------------

    Section "0. Service health"

    foreach ($svc in @("backend", "alarm_evaluator", "postgres")) {
        $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
        if ($psout -match '"State":\s*"running"' -or
            $psout -match '"Status":\s*"Up') {
            Pass "$svc is running"
        } else {
            Fail "$svc is not running (state check inconclusive)"
        }
    }

    # ---- 1. OpenAPI registration of new endpoints -------------------------

    Section "1. OpenAPI registration"

    $openapi = Invoke-RestMethod http://localhost:8000/openapi.json
    $alarmPaths = @($openapi.paths.PSObject.Properties.Name |
        Where-Object { $_ -like "/api/alarms*" })

    foreach ($expected in @(
        "/api/alarms/shelved",
        "/api/alarms/rules/{rule_id}/shelve",
        "/api/alarms/rules/{rule_id}/unshelve"
    )) {
        if ($alarmPaths -contains $expected) {
            Pass "endpoint registered: $expected"
        } else {
            Fail "missing endpoint: $expected"
        }
    }

    # ---- 2. Schema migration check ----------------------------------------

    Section "2. Schema has shelve columns"

    $shelvedUntilCol = Psql @"
SELECT column_name FROM information_schema.columns
WHERE table_name='alarm_state' AND column_name='shelved_until'
"@
    if ($shelvedUntilCol -eq "shelved_until") {
        Pass "alarm_state.shelved_until column exists"
    } else {
        Fail "alarm_state.shelved_until column is missing"
    }

    $shelveUserCol = Psql @"
SELECT column_name FROM information_schema.columns
WHERE table_name='alarm_state' AND column_name='shelve_user_id'
"@
    if ($shelveUserCol -eq "shelve_user_id") {
        Pass "alarm_state.shelve_user_id column exists"
    } else {
        Fail "alarm_state.shelve_user_id column is missing"
    }

    # ---- 3. Setup ---------------------------------------------------------

    Section "3. Setup fixture"

    $tagsCount = [int](Psql "SELECT count(*) FROM tags")
    if ($tagsCount -lt 1) {
        Fail "tags table is empty"
        exit 1
    }
    $script:TestTagId = [int](Psql "SELECT id FROM tags ORDER BY id LIMIT 1")
    $tagName = Psql "SELECT name FROM tags WHERE id = ${script:TestTagId}"
    Pass "Fixture tag id=${script:TestTagId}, name=$tagName"

    # Stop the modbus worker so it can't overwrite our synthetic state.
    # Note: doesn't affect this smoke's API operations - we never wait
    # for the evaluator to react to a real value crossing a threshold.
    DockerQuiet @("stop", "modbus_worker")
    $script:ModbusWasStopped = $true
    Pass "modbus_worker stopped"

    # Clean any leftover 'hi' rules on this tag from prior runs
    PsqlExec "DELETE FROM alarm_rules WHERE tag_id = ${script:TestTagId} AND rule_type = 'hi'"

    # Create the test rule with extreme delays so the evaluator can't
    # spontaneously transition anything during the smoke (every API
    # operation should be visible without contention).
    PsqlExec @"
INSERT INTO alarm_rules (tag_id, rule_type, severity, threshold, deadband,
                         on_delay_sec, off_delay_sec, latched, enabled)
VALUES (${script:TestTagId}, 'hi', 'high', 999999.0, 0,
        3600, 3600, true, true)
"@
    $script:TestRuleId = [int](Psql @"
SELECT id FROM alarm_rules
WHERE tag_id = ${script:TestTagId} AND rule_type = 'hi'
ORDER BY id DESC LIMIT 1
"@)
    Pass "Test rule created (id=${script:TestRuleId}, threshold=999999, delays=3600s each)"

    $initialState = Psql "SELECT state FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ($initialState -eq "normal") {
        Pass "Initial alarm_state is 'normal' (trigger fired)"
    } else {
        Fail "Initial state is '$initialState' (expected 'normal')"
    }

    # ---- 4. Shelve from normal state --------------------------------------

    Section "4. Shelve from 'normal' state (preemptive mute)"

    $shelveResult = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/shelve" `
        -Body @{ duration_minutes = 60; user_id = 7; comment = "test shelve" }

    if ($shelveResult.ok -and $shelveResult.body.event_type -eq "shelved") {
        Pass "POST /shelve returned 'shelved' event id=$($shelveResult.body.id)"
    } else {
        Fail "Shelve failed: status=$($shelveResult.status), body=$($shelveResult.body)"
    }

    $stateAfter = Psql "SELECT state FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ($stateAfter -eq "shelved") {
        Pass "State transitioned 'normal' -> 'shelved'"
    } else {
        Fail "Expected state 'shelved', got '$stateAfter'"
    }

    $shelveUserId = Psql "SELECT shelve_user_id FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ($shelveUserId -eq "7") {
        Pass "shelve_user_id captured from request"
    } else {
        Fail "Expected shelve_user_id=7, got '$shelveUserId'"
    }

    $shelvedUntilSet = Psql "SELECT shelved_until IS NOT NULL AND shelved_until > NOW() FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ($shelvedUntilSet -eq "t") {
        Pass "shelved_until set to a future time"
    } else {
        Fail "shelved_until is not in the future (got '$shelvedUntilSet')"
    }

    # ---- 5. GET /shelved should list this rule ----------------------------

    Section "5. GET /api/alarms/shelved"

    $shelvedList = Http -Method GET -Url "http://localhost:8000/api/alarms/shelved"
    if (-not $shelvedList.ok) {
        Fail "GET /shelved failed: $($shelvedList.status)"
    }
    $thisRule = @(@($shelvedList.body) | Where-Object { $_.rule_id -eq ${script:TestRuleId} })
    if ($thisRule.Count -eq 1) {
        Pass "Shelved list contains our rule"
    } else {
        Fail "Shelved list missing our rule (count=$($thisRule.Count))"
    }

    if ($thisRule.Count -ge 1 -and $thisRule[0].state -eq "shelved" -and $thisRule[0].shelved_until) {
        Pass "Response includes state='shelved' and shelved_until"
    } else {
        Fail "Response missing state or shelved_until fields"
    }

    # ---- 6. GET /active should NOT list this rule -------------------------

    Section "6. GET /api/alarms/active excludes shelved"

    $activeList = Http -Method GET -Url "http://localhost:8000/api/alarms/active"
    $inActive = @(@($activeList.body) | Where-Object { $_.rule_id -eq ${script:TestRuleId} })
    if ($inActive.Count -eq 0) {
        Pass "Shelved rule does NOT appear in active list"
    } else {
        Fail "Shelved rule leaked into active list (count=$($inActive.Count))"
    }

    # ---- 7. Re-shelve extends expiry --------------------------------------

    Section "7. Re-shelve extends expiry"

    $beforeExpiry = Psql "SELECT extract(epoch from shelved_until) FROM alarm_state WHERE rule_id = ${script:TestRuleId}"

    $reShelveResult = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/shelve" `
        -Body @{ duration_minutes = 240; user_id = 7; comment = "extend" }

    if ($reShelveResult.ok) {
        Pass "Re-shelve succeeded (no 409 conflict)"
    } else {
        Fail "Re-shelve rejected: $($reShelveResult.status)"
    }

    $afterExpiry = Psql "SELECT extract(epoch from shelved_until) FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ([double]$afterExpiry -gt [double]$beforeExpiry) {
        Pass "shelved_until extended forward by re-shelve ($beforeExpiry -> $afterExpiry)"
    } else {
        Fail "shelved_until did NOT advance ($beforeExpiry -> $afterExpiry)"
    }

    # ---- 8. Unshelve restores normal state --------------------------------

    Section "8. POST /unshelve"

    $unshelveResult = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/unshelve" `
        -Body @{ user_id = 7; comment = "manual unshelve" }

    if ($unshelveResult.ok -and $unshelveResult.body.event_type -eq "unshelved") {
        Pass "Unshelve returned 'unshelved' event"
    } else {
        Fail "Unshelve failed: status=$($unshelveResult.status)"
    }

    $stateAfterUnshelve = Psql "SELECT state FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ($stateAfterUnshelve -eq "normal") {
        Pass "State transitioned 'shelved' -> 'normal'"
    } else {
        Fail "Expected 'normal', got '$stateAfterUnshelve'"
    }

    $cleared = Psql "SELECT shelved_until IS NULL AND shelve_user_id IS NULL FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ($cleared -eq "t") {
        Pass "shelved_until and shelve_user_id cleared on unshelve"
    } else {
        Fail "shelved_until or shelve_user_id not cleared"
    }

    $shelvedListAfter = Http -Method GET -Url "http://localhost:8000/api/alarms/shelved"
    $stillShelved = @(@($shelvedListAfter.body) | Where-Object { $_.rule_id -eq ${script:TestRuleId} })
    if ($stillShelved.Count -eq 0) {
        Pass "Rule no longer appears in /shelved"
    } else {
        Fail "Rule still in /shelved after unshelve"
    }

    # ---- 9. Double unshelve rejected --------------------------------------

    Section "9. Unshelve when not shelved is rejected"

    $doubleUnshelve = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/unshelve" `
        -Body @{ user_id = 7 }

    if ($doubleUnshelve.status -eq 409) {
        Pass "Second unshelve returned 409 Conflict"
    } else {
        Fail "Expected 409, got $($doubleUnshelve.status)"
    }

    # ---- 10. Shelve from active_unack -------------------------------------

    Section "10. Shelve from 'active_unack' state"

    # Manually flip state to active_unack to simulate evaluator firing.
    # We're not relying on the evaluator here - just testing the API path.
    PsqlExec @"
UPDATE alarm_state
SET state = 'active_unack', last_change_time = NOW(),
    current_value = 1000.0, current_quality = 192
WHERE rule_id = ${script:TestRuleId}
"@

    $shelveActive = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/shelve" `
        -Body @{ duration_minutes = 30; user_id = 7 }

    if ($shelveActive.ok) {
        $newState = Psql "SELECT state FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
        if ($newState -eq "shelved") {
            Pass "active_unack -> shelved succeeded via API"
        } else {
            Fail "Expected 'shelved', got '$newState'"
        }
    } else {
        Fail "Shelve from active_unack rejected: $($shelveActive.status)"
    }

    # Unshelve back to normal for next test
    $null = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/unshelve" `
        -Body @{ user_id = 7 }

    # ---- 11. Shelve a disabled rule is rejected --------------------------

    Section "11. Shelve a disabled rule rejected"

    # Force state to 'disabled' (operator turned the rule off)
    PsqlExec "UPDATE alarm_state SET state = 'disabled' WHERE rule_id = ${script:TestRuleId}"

    $shelveDisabled = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/shelve" `
        -Body @{ duration_minutes = 30; user_id = 7 }

    if ($shelveDisabled.status -eq 409) {
        Pass "Shelve on disabled rule returned 409"
    } else {
        Fail "Expected 409 on disabled-rule shelve, got $($shelveDisabled.status)"
    }

    # Restore to normal for next test
    PsqlExec "UPDATE alarm_state SET state = 'normal' WHERE rule_id = ${script:TestRuleId}"

    # ---- 12. Evaluator auto-unshelve at expiry ---------------------------

    Section "12. Evaluator auto-unshelves when shelved_until expires (~70s)"

    # Shelve for 1 minute - the evaluator should auto-transition to
    # normal within ~1s of expiry.
    $autoShelve = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/${script:TestRuleId}/shelve" `
        -Body @{ duration_minutes = 1; user_id = 7; comment = "auto-expiry test" }

    if ($autoShelve.ok) {
        Pass "Shelved for 1 min"
    } else {
        Fail "Auto-expiry shelve setup failed: $($autoShelve.status)"
    }

    Write-Host "  Waiting 70 seconds for the evaluator to detect expiry..." -ForegroundColor DarkGray
    for ($i = 70; $i -gt 0; $i -= 10) {
        Write-Host -NoNewline "  ${i}s remaining...`r"
        Start-Sleep -Seconds 10
    }
    Write-Host "                              "

    $autoState = Psql "SELECT state FROM alarm_state WHERE rule_id = ${script:TestRuleId}"
    if ($autoState -eq "normal") {
        Pass "Evaluator auto-transitioned to 'normal' after expiry"
    } else {
        Fail "Expected 'normal' after auto-expiry, got '$autoState'"
    }

    $autoEvent = Psql @"
SELECT comment FROM alarm_events
WHERE rule_id = ${script:TestRuleId} AND event_type = 'unshelved'
ORDER BY event_time DESC LIMIT 1
"@
    if ($autoEvent -eq "shelve_expired") {
        Pass "Auto-unshelve event written with comment='shelve_expired'"
    } else {
        Fail "Auto-unshelve event missing or comment is '$autoEvent'"
    }

    # ---- 13. Event history contains all expected entries -----------------

    Section "13. Event history captures the full journey"

    $shelvedEventCount = [int](Psql @"
SELECT count(*) FROM alarm_events
WHERE rule_id = ${script:TestRuleId} AND event_type = 'shelved'
"@)
    if ($shelvedEventCount -ge 4) {
        Pass "$shelvedEventCount 'shelved' events written (expected >= 4)"
    } else {
        Fail "Expected >= 4 'shelved' events, got $shelvedEventCount"
    }

    $unshelvedEventCount = [int](Psql @"
SELECT count(*) FROM alarm_events
WHERE rule_id = ${script:TestRuleId} AND event_type = 'unshelved'
"@)
    if ($unshelvedEventCount -ge 3) {
        Pass "$unshelvedEventCount 'unshelved' events written (expected >= 3: 2 manual + 1 auto)"
    } else {
        Fail "Expected >= 3 'unshelved' events, got $unshelvedEventCount"
    }

    # Spot-check the user_id stuck on at least one event
    $userIdInEvent = Psql @"
SELECT count(*) FROM alarm_events
WHERE rule_id = ${script:TestRuleId} AND event_type = 'shelved' AND user_id = 7
"@
    if ([int]$userIdInEvent -ge 1) {
        Pass "user_id captured on shelve events"
    } else {
        Fail "user_id not captured on any shelve event"
    }
}
finally {
    # ---- Cleanup (runs even on hard failure) ------------------------------

    Section "Cleanup"

    if ($script:TestRuleId) {
        PsqlExec "DELETE FROM alarm_rules WHERE id = ${script:TestRuleId}"
        Write-Host "  Test rule deleted (events retained per audit policy)"
    }

    if ($script:ModbusWasStopped) {
        DockerQuiet @("start", "modbus_worker")
        Write-Host "  modbus_worker restarted"
    }
}

# ---- Summary -------------------------------------------------------------

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
    Write-Host "  docker compose logs --tail 50 alarm_evaluator"
    Write-Host "  docker compose logs --tail 50 backend"
    exit 1
}

Write-Host ""
Write-Host "Phase 14.4 shelve/unshelve fully verified." -ForegroundColor Cyan
Write-Host "Next: 14.6 severity admin." -ForegroundColor Cyan
exit 0
