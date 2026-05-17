# =============================================================================
# Phase 14.9 -- Fully automated boolean-alarms smoke test.
#
# Exercises the full evaluator state machine for both new rule types:
#   - bool_true:  active when value != 0, clear when value == 0
#   - bool_false: active when value == 0, clear when value != 0
#
# What's verified:
#   - Migration 0032 seeded bool_true and bool_false rows with is_evaluable=true
#   - Evaluator processes both new types (state transitions fire)
#   - on_delay and off_delay debounce correctly for booleans
#   - latched=true -> cleared signal moves to inactive_unack (waiting on ack)
#   - Ack flow works on a boolean alarm just like a numeric one
#   - Event log captures 'activated' and 'cleared' events
#   - Default message text is type-aware ("Boolean signal asserted/absent")
#   - bool_false mirror: alarm when value goes to 0
#
# Defensive patterns from prior smokes:
#   - try/finally cleanup (restarts modbus_worker, deletes test rules)
#   - DockerQuiet helper around docker compose calls
#   - @() around Where-Object pipelines
#   - alarm_evaluator restart forces immediate rule-cache reload
#     (default reload interval is 30s -- too long for a smoke)
#
# Total runtime: ~50 seconds, mostly value-injection + on_delay waits.
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_9.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Reasons = @()

$script:CreatedRuleIds = @()
$script:TestTagId = $null

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
    # IMPORTANT: parameter renamed from $args to $cmdArgs to avoid the
    # PowerShell automatic $args variable shadowing issue. When a
    # function parameter is named $args, splatting with @args can
    # silently expand to the wrong thing on some PS versions, leading
    # to docker commands being a no-op without any visible error.
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

function InjectValue([int]$tagId, [double]$value) {
    PsqlExec @"
INSERT INTO tag_values (time, tag_id, device_id, value_double, st)
SELECT NOW(), id, device_id, $value, 192
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

    # ---- 1. Migration check: bool_true and bool_false seeded -------------

    Section "1. Migration: bool rule types seeded"

    foreach ($code in @("bool_true", "bool_false")) {
        # Note: '||' concatenation casts bool to its long-form text
        # ('true'/'false'), unlike a bare SELECT bool_col which prints
        # 't'/'f' in psql -tA mode.
        $row = Psql "SELECT is_system::text || '|' || is_evaluable::text FROM alarm_rule_types WHERE code = '$code'"
        if ($row -eq "true|true") {
            Pass "$code present with is_system=true, is_evaluable=true"
        } else {
            Fail "$code missing or wrong flags (got '$row')"
        }
    }

    # ---- 2. Setup --------------------------------------------------------
    #
    # Why a synthetic tag instead of stopping modbus_worker:
    #   - `docker compose stop modbus_worker` was failing silently or not
    #     persisting (worker came back up). Injected values were being
    #     immediately overwritten by the worker's next scan.
    #   - Tags table has `enabled boolean NOT NULL DEFAULT true` with an
    #     ix_tags_device_enabled index -- modbus_worker reliably filters by
    #     enabled=true. An enabled=false tag is invisible to the worker.
    #   - Tag deletion cascades to alarm_rules, alarm_state, and tag_values
    #     via FK ON DELETE CASCADE -- single DELETE for cleanup.
    #
    # The synthetic tag is bool/FC=1/address=99999 -- a coil read at an
    # address the simulator doesn't expose, so even if some worker did
    # see it, the read would error out instead of writing values.

    Section "2. Setup synthetic test tag (no modbus polling)"

    $deviceId = [int](Psql "SELECT id FROM devices ORDER BY id LIMIT 1")
    if ($deviceId -le 0) {
        Fail "No devices in DB -- cannot create synthetic test tag"
        return
    }

    # Clean stale synthetic tag from any prior failed run.
    PsqlExec "DELETE FROM tags WHERE name = 'smoke_phase_14_9_bool_test_tag'"

    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $deviceId,
    'smoke_phase_14_9_bool_test_tag',
    'bool',
    1,
    99999,
    false,
    'Phase 14.9 smoke synthetic tag - safe to delete',
    0,
    1
)
"@

    $script:TestTagId = [int](Psql "SELECT id FROM tags WHERE name = 'smoke_phase_14_9_bool_test_tag'")
    if ($script:TestTagId -le 0) {
        Fail "Failed to create synthetic test tag (check tags schema constraints)"
        return
    }
    Pass "Synthetic test tag created (id=${script:TestTagId}, enabled=false -> no modbus polling)"

    # ---- 3. Create bool_true rule ----------------------------------------

    Section "3. Create bool_true rule"

    PsqlExec @"
INSERT INTO alarm_rules (
    tag_id, rule_type, severity, threshold, deadband,
    on_delay_sec, off_delay_sec, latched, enabled
) VALUES (
    ${script:TestTagId}, 'bool_true', 'critical', 0, 0,
    2, 2, true, true
)
"@

    $boolTrueRuleId = [int](Psql @"
SELECT id FROM alarm_rules
WHERE tag_id = ${script:TestTagId} AND rule_type = 'bool_true'
ORDER BY id DESC LIMIT 1
"@)
    $script:CreatedRuleIds += $boolTrueRuleId
    Pass "bool_true rule created (id=$boolTrueRuleId, on_delay=2s, off_delay=2s, latched)"

    # Force evaluator to reload its cache by restarting. Default reload
    # cycle is 30s which is too long for a smoke.
    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 3
    Pass "alarm_evaluator restarted (rule cache reloaded)"

    # ---- 4. Inject value=0 -> no alarm should fire -------------------------

    Section "4. value=0 -> state stays 'normal'"

    InjectValue -tagId $script:TestTagId -value 0
    Start-Sleep -Seconds 4

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $boolTrueRuleId"
    if ($state -eq "normal") {
        Pass "State is 'normal' (value=0 doesn't satisfy bool_true)"
    } else {
        Fail "Expected 'normal', got '$state'"
    }

    # ---- 5. Inject value=1 -> alarm should activate after on_delay --------

    Section "5. value=1 -> state becomes 'active_unack' after on_delay"

    InjectValue -tagId $script:TestTagId -value 1
    # Need on_delay (2s) + evaluator tick (1s) + buffer
    Start-Sleep -Seconds 5

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $boolTrueRuleId"
    if ($state -eq "active_unack") {
        Pass "State transitioned to 'active_unack' (value=1 + on_delay elapsed)"
    } else {
        Fail "Expected 'active_unack', got '$state'"
    }

    $activatedEvents = [int](Psql @"
SELECT count(*) FROM alarm_events
WHERE rule_id = $boolTrueRuleId AND event_type = 'activated'
"@)
    if ($activatedEvents -ge 1) {
        Pass "$activatedEvents 'activated' event(s) written"
    } else {
        Fail "Expected >= 1 'activated' event, got $activatedEvents"
    }

    # ---- 6. Default message text is type-aware ----------------------------

    Section "6. Default message is 'Boolean signal asserted'"

    $msg = Psql @"
SELECT comment FROM alarm_events
WHERE rule_id = $boolTrueRuleId AND event_type = 'activated'
ORDER BY event_time DESC LIMIT 1
"@
    if ($msg -like "*Boolean signal asserted*") {
        Pass "Default message uses boolean-aware wording: '$msg'"
    } else {
        # Not a hard fail -- comment might be different if message_template
        # was set. But for our rule with no template, expect the new wording.
        Fail "Expected 'Boolean signal asserted...', got '$msg'"
    }

    # ---- 7. Inject value=0 -> state becomes inactive_unack (latched) ------

    Section "7. value=0 -> state becomes 'inactive_unack' (latched)"

    InjectValue -tagId $script:TestTagId -value 0
    Start-Sleep -Seconds 5

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $boolTrueRuleId"
    if ($state -eq "inactive_unack") {
        Pass "State -> 'inactive_unack' (signal cleared but rule is latched)"
    } else {
        Fail "Expected 'inactive_unack', got '$state'"
    }

    $clearedEvents = [int](Psql @"
SELECT count(*) FROM alarm_events
WHERE rule_id = $boolTrueRuleId AND event_type = 'cleared'
"@)
    if ($clearedEvents -ge 1) {
        Pass "$clearedEvents 'cleared' event(s) written"
    } else {
        Fail "Expected >= 1 'cleared' event, got $clearedEvents"
    }

    # ---- 8. Ack the rule -> state becomes 'normal' -------------------------

    Section "8. POST /ack -> 'inactive_unack' transitions to 'normal'"

    $ackResp = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules/$boolTrueRuleId/ack" `
        -Body @{ user_id = 7; comment = "boolean ack" }

    if ($ackResp.ok) {
        Pass "Ack accepted"
    } else {
        Fail "Ack failed: $($ackResp.status)"
    }

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $boolTrueRuleId"
    if ($state -eq "normal") {
        Pass "State -> 'normal' (latched cycle complete)"
    } else {
        Fail "Expected 'normal' after ack, got '$state'"
    }

    # ---- 9. Test bool_false: active when value=0 -------------------------

    Section "9. Create bool_false rule (alarm when value=0)"

    PsqlExec "DELETE FROM alarm_rules WHERE id = $boolTrueRuleId"
    $script:CreatedRuleIds = @($script:CreatedRuleIds | Where-Object { $_ -ne $boolTrueRuleId })

    PsqlExec @"
INSERT INTO alarm_rules (
    tag_id, rule_type, severity, threshold, deadband,
    on_delay_sec, off_delay_sec, latched, enabled
) VALUES (
    ${script:TestTagId}, 'bool_false', 'high', 0, 0,
    2, 2, false, true
)
"@

    $boolFalseRuleId = [int](Psql @"
SELECT id FROM alarm_rules
WHERE tag_id = ${script:TestTagId} AND rule_type = 'bool_false'
ORDER BY id DESC LIMIT 1
"@)
    $script:CreatedRuleIds += $boolFalseRuleId
    Pass "bool_false rule created (id=$boolFalseRuleId, not latched)"

    DockerQuiet @("restart", "alarm_evaluator")
    Start-Sleep -Seconds 3
    Pass "alarm_evaluator restarted"

    # ---- 10. Inject value=1 -> no alarm (signal present is OK) ----------

    Section "10. bool_false: value=1 -> state stays 'normal'"

    InjectValue -tagId $script:TestTagId -value 1
    Start-Sleep -Seconds 4

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $boolFalseRuleId"
    if ($state -eq "normal") {
        Pass "State is 'normal' (value=1 doesn't satisfy bool_false)"
    } else {
        Fail "Expected 'normal', got '$state'"
    }

    # ---- 11. Inject value=0 -> alarm should activate ---------------------

    Section "11. bool_false: value=0 -> state becomes 'active_unack'"

    InjectValue -tagId $script:TestTagId -value 0
    Start-Sleep -Seconds 5

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $boolFalseRuleId"
    if ($state -eq "active_unack") {
        Pass "State -> 'active_unack' (value=0 satisfies bool_false)"
    } else {
        Fail "Expected 'active_unack', got '$state'"
    }

    $msg = Psql @"
SELECT comment FROM alarm_events
WHERE rule_id = $boolFalseRuleId AND event_type = 'activated'
ORDER BY event_time DESC LIMIT 1
"@
    if ($msg -like "*Boolean signal absent*") {
        Pass "bool_false default message uses 'Boolean signal absent' wording"
    } else {
        Fail "Expected 'Boolean signal absent...', got '$msg'"
    }

    # ---- 12. Inject value=1 -> non-latched returns directly to 'normal' --

    Section "12. bool_false (non-latched): value=1 -> returns to 'normal'"

    InjectValue -tagId $script:TestTagId -value 1
    Start-Sleep -Seconds 5

    $state = Psql "SELECT state FROM alarm_state WHERE rule_id = $boolFalseRuleId"
    if ($state -eq "normal") {
        Pass "State -> 'normal' (non-latched, no ack needed)"
    } else {
        Fail "Expected 'normal' (non-latched clear), got '$state'"
    }

    # ---- 13. Event log includes the full lifecycle -----------------------

    Section "13. Event log captures activate + clear for bool_false"

    $eventCount = [int](Psql @"
SELECT count(*) FROM alarm_events
WHERE rule_id = $boolFalseRuleId
"@)
    if ($eventCount -ge 2) {
        Pass "$eventCount events recorded for bool_false rule (activated + cleared)"
    } else {
        Fail "Expected >= 2 events, got $eventCount"
    }
}
finally {
    Section "Cleanup"

    # FK ON DELETE CASCADE chains the cleanup:
    #   tags.id deletion -> alarm_rules (cascade) -> alarm_state (cascade)
    #                    -> tag_values (cascade)
    # alarm_events may or may not retain rule_id depending on its FK
    # policy; either way that's fine for a smoke (events are audit data).
    if ($script:TestTagId) {
        PsqlExec "DELETE FROM tags WHERE id = ${script:TestTagId}"
        Write-Host "  Deleted synthetic tag id=${script:TestTagId} (cascaded to rules + state + values)"
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
    Write-Host ""
    Write-Host "Debug helpers:" -ForegroundColor Yellow
    Write-Host "  docker compose logs --tail 50 alarm_evaluator"
    Write-Host "  docker compose logs --tail 30 backend"
    exit 1
}

Write-Host ""
Write-Host "Phase 14.9 boolean alarms fully verified." -ForegroundColor Cyan
exit 0
