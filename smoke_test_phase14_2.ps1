# =============================================================================
# Phase 14.2 alarms API smoke test.
#
# Exercises every endpoint in app/api/alarms.py:
#   POST   /api/alarms/rules                create
#   GET    /api/alarms/rules                list (with filters)
#   GET    /api/alarms/rules/{id}           get one
#   PATCH  /api/alarms/rules/{id}           update
#   DELETE /api/alarms/rules/{id}           delete
#   GET    /api/alarms/active               currently-active list
#   GET    /api/alarms/history              event log
#   POST   /api/alarms/rules/{id}/ack       acknowledge
#
# The evaluator (Phase 14.3) is the normal writer of state transitions,
# so this test pokes alarm_state directly via psql to simulate an
# active alarm before exercising the ack action.
#
# Strict ASCII only.
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_2.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Base    = "http://localhost:8000/api/alarms"
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

# Wrap Invoke-RestMethod so we can capture both success bodies and
# error status codes without -ErrorAction Stop killing the script.
function Http {
    param(
        [string]$Method,
        [string]$Url,
        [object]$Body = $null,
        [int]$ExpectStatus = 200
    )
    $args = @{
        Method      = $Method
        Uri         = $Url
        ContentType = "application/json"
    }
    if ($Body -ne $null) {
        $args.Body = ($Body | ConvertTo-Json -Depth 6 -Compress)
    }
    try {
        $resp = Invoke-RestMethod @args
        $status = 200
        if ($Method -eq "POST") { $status = 201 }
        if ($Method -eq "DELETE") { $status = 204 }
        return @{ ok = $true; status = $status; body = $resp }
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

# ---- 0. Pick a fixture tag -------------------------------------------------

Section "0. Preconditions"

$tagsCount = Psql "SELECT count(*) FROM tags"
if ([int]$tagsCount -lt 1) {
    Fail "tags table is empty - need at least 1 tag"
    exit 1
}
$TestTagId = [int](Psql "SELECT id FROM tags ORDER BY id LIMIT 1")
$TestTagName = Psql "SELECT name FROM tags WHERE id = $TestTagId"
Pass "Fixture tag: id=$TestTagId, name=$TestTagName"

# Clean any leftover rules from a prior run (this tag may have a 'hi' rule
# left over from a failed previous pass).
$null = Psql "DELETE FROM alarm_rules WHERE tag_id = $TestTagId"

# ---- 1. Create a rule ------------------------------------------------------

Section "1. POST /api/alarms/rules"

$createBody = @{
    tag_id           = $TestTagId
    rule_type        = "hi"
    severity         = "high"
    threshold        = 100.0
    deadband         = 1.0
    on_delay_sec     = 5
    off_delay_sec    = 5
    latched          = $false
    enabled          = $true
    message_template = "{tag_name} exceeded {threshold}"
}
$r = Http -Method POST -Url "$Base/rules" -Body $createBody
if ($r.ok -and $r.body.id) {
    Pass "Created rule id=$($r.body.id)"
    $RuleId = $r.body.id
} else {
    Fail "POST /rules failed: status=$($r.status), body=$($r.body)"
    exit 1
}

if ($r.body.tag_name -eq $TestTagName) {
    Pass "Response includes tag_name='$($r.body.tag_name)'"
} else {
    Fail "Response missing or wrong tag_name (got '$($r.body.tag_name)')"
}

# Verify the trigger created an alarm_state row
$stateAfterCreate = Psql "SELECT state FROM alarm_state WHERE rule_id = $RuleId"
if ($stateAfterCreate -eq "normal") {
    Pass "Trigger auto-created alarm_state in 'normal' state"
} else {
    Fail "alarm_state for rule $RuleId is '$stateAfterCreate', expected 'normal'"
}

# ---- 2. Duplicate (same tag + 'hi') should 409 ----------------------------

Section "2. Duplicate rule rejected"

$r = Http -Method POST -Url "$Base/rules" -Body $createBody
if ($r.status -eq 409) {
    Pass "Second 'hi' rule on same tag rejected with 409"
} else {
    Fail "Expected 409, got status=$($r.status)"
}

# ---- 3. Invalid inputs -----------------------------------------------------

Section "3. Validation errors"

$badSeverity = $createBody.Clone()
$badSeverity.severity = "urgent"  # ISA-18.2 term we deliberately rejected
$r = Http -Method POST -Url "$Base/rules" -Body $badSeverity
if ($r.status -eq 422) {
    Pass "Bad severity 'urgent' rejected with 422 (Pydantic)"
} else {
    Fail "Expected 422 for bad severity, got $($r.status)"
}

$badType = $createBody.Clone()
$badType.rule_type = "made_up"
$r = Http -Method POST -Url "$Base/rules" -Body $badType
if ($r.status -eq 422) {
    Pass "Bad rule_type 'made_up' rejected with 422"
} else {
    Fail "Expected 422 for bad rule_type, got $($r.status)"
}

$badTag = $createBody.Clone()
$badTag.tag_id = 999999
$badTag.rule_type = "lo"  # avoid the unique conflict
$r = Http -Method POST -Url "$Base/rules" -Body $badTag
if ($r.status -eq 422 -or $r.status -eq 400) {
    Pass "Non-existent tag_id rejected with $($r.status)"
} else {
    Fail "Expected 422/400 for bad tag_id, got $($r.status)"
}

# ---- 4. List + filters -----------------------------------------------------

Section "4. GET /api/alarms/rules with filters"

$r = Http -Method GET -Url "$Base/rules"
if ($r.ok -and $r.body.Count -ge 1) {
    Pass "GET /rules returned $($r.body.Count) row(s)"
} else {
    Fail "GET /rules failed or empty"
}

$r = Http -Method GET -Url "$Base/rules?tag_id=$TestTagId"
$forThisTag = @($r.body | Where-Object { $_.tag_id -eq $TestTagId })
if ($forThisTag.Count -ge 1) {
    Pass "Filter ?tag_id=$TestTagId returned $($forThisTag.Count) row(s)"
} else {
    Fail "Tag filter returned no rows"
}

$r = Http -Method GET -Url "$Base/rules?severity=high"
$severityFiltered = @($r.body | Where-Object { $_.severity -ne "high" })
if ($severityFiltered.Count -eq 0) {
    Pass "Filter ?severity=high returned only high-severity rules"
} else {
    Fail "Severity filter leaked $($severityFiltered.Count) non-high rules"
}

# ---- 5. GET one ------------------------------------------------------------

Section "5. GET /api/alarms/rules/{id}"

$r = Http -Method GET -Url "$Base/rules/$RuleId"
if ($r.ok -and $r.body.id -eq $RuleId) {
    Pass "GET /rules/$RuleId returned the rule"
} else {
    Fail "GET /rules/$RuleId failed"
}

$r = Http -Method GET -Url "$Base/rules/999999"
if ($r.status -eq 404) {
    Pass "GET /rules/999999 returned 404"
} else {
    Fail "Expected 404 for missing rule, got $($r.status)"
}

# ---- 6. PATCH ---------------------------------------------------------------

Section "6. PATCH /api/alarms/rules/{id}"

$updatedAtBefore = Psql "SELECT updated_at FROM alarm_rules WHERE id = $RuleId"
Start-Sleep -Milliseconds 1100

$r = Http -Method PATCH -Url "$Base/rules/$RuleId" -Body @{
    severity  = "critical"
    threshold = 105.0
}
if ($r.ok -and $r.body.severity -eq "critical" -and $r.body.threshold -eq 105.0) {
    Pass "PATCH updated severity to 'critical' and threshold to 105.0"
} else {
    Fail "PATCH did not return updated values"
}

$updatedAtAfter = Psql "SELECT updated_at FROM alarm_rules WHERE id = $RuleId"
if ($updatedAtAfter -gt $updatedAtBefore) {
    Pass "DB trigger bumped updated_at ($updatedAtBefore -> $updatedAtAfter)"
} else {
    Fail "updated_at trigger did not fire"
}

# ---- 7. Active list (empty, then populated) -------------------------------

Section "7. GET /api/alarms/active"

$r = Http -Method GET -Url "$Base/active"
$thisRule = @($r.body | Where-Object { $_.rule_id -eq $RuleId })
if ($thisRule.Count -eq 0) {
    Pass "Active list does not include our rule yet (state is still 'normal')"
} else {
    Fail "Rule appeared in active list before being activated"
}

# Manually flip state to active_unack as if the evaluator had fired
$null = Psql @"
UPDATE alarm_state
SET state = 'active_unack',
    last_change_time = NOW(),
    current_value = 107.3,
    current_quality = 192
WHERE rule_id = $RuleId
"@

$r = Http -Method GET -Url "$Base/active"
$thisRule = @($r.body | Where-Object { $_.rule_id -eq $RuleId })
if ($thisRule.Count -eq 1 -and $thisRule[0].state -eq "active_unack") {
    Pass "Active list includes our rule (state=active_unack, value=$($thisRule[0].current_value))"
} else {
    Fail "Active list missing the activated rule (got count=$($thisRule.Count))"
}

# Severity sort: critical should be at or near the top
if ($r.body.Count -ge 1 -and $r.body[0].severity -eq "critical") {
    Pass "Active list sorted with critical at top"
} else {
    Fail "Severity sort incorrect; first row severity=$($r.body[0].severity)"
}

# ---- 8. Acknowledge --------------------------------------------------------

Section "8. POST /api/alarms/rules/{id}/ack"

$r = Http -Method POST -Url "$Base/rules/$RuleId/ack" -Body @{
    user_id = 42
    comment = "Acked by smoke test"
}
if ($r.ok -and $r.body.event_type -eq "acked") {
    Pass "Ack returned an 'acked' event id=$($r.body.id)"
    $AckEventId = $r.body.id
} else {
    Fail "Ack failed: status=$($r.status), body=$($r.body)"
}

if ($r.body.value -eq 107.3 -and $r.body.quality -eq 192) {
    Pass "Ack event captured current_value and current_quality"
} else {
    Fail "Ack event missing value/quality (got value=$($r.body.value), quality=$($r.body.quality))"
}

$stateAfterAck = Psql "SELECT state FROM alarm_state WHERE rule_id = $RuleId"
if ($stateAfterAck -eq "active_ack") {
    Pass "Post-ack state transitioned active_unack -> active_ack"
} else {
    Fail "Post-ack state is '$stateAfterAck', expected 'active_ack'"
}

# Second ack must 409 — already acked
$r = Http -Method POST -Url "$Base/rules/$RuleId/ack" -Body @{ user_id = 42 }
if ($r.status -eq 409) {
    Pass "Second ack on active_ack rejected with 409"
} else {
    Fail "Expected 409 on double-ack, got $($r.status)"
}

# Now simulate the alarm clearing, leaving it in inactive_unack
$null = Psql "UPDATE alarm_state SET state='inactive_unack', current_value=98.0 WHERE rule_id=$RuleId"
$r = Http -Method POST -Url "$Base/rules/$RuleId/ack" -Body @{ user_id = 42 }
if ($r.ok) {
    $finalState = Psql "SELECT state FROM alarm_state WHERE rule_id = $RuleId"
    if ($finalState -eq "normal") {
        Pass "Ack from inactive_unack transitions to 'normal'"
    } else {
        Fail "Expected final state 'normal', got '$finalState'"
    }
} else {
    Fail "Ack from inactive_unack failed: $($r.status)"
}

# ---- 9. History ------------------------------------------------------------

Section "9. GET /api/alarms/history"

$r = Http -Method GET -Url "$Base/history?rule_id=$RuleId"
if ($r.body.Count -ge 2) {
    Pass "History returned $($r.body.Count) events for this rule"
} else {
    Fail "Expected at least 2 acked events in history, got $($r.body.Count)"
}

$ackedEvents = @($r.body | Where-Object { $_.event_type -eq "acked" })
if ($ackedEvents.Count -ge 2) {
    Pass "Both ack events appear in history"
} else {
    Fail "Missing ack events in history (count=$($ackedEvents.Count))"
}

$r = Http -Method GET -Url "$Base/history?event_type=acked&limit=10"
$nonAcked = @($r.body | Where-Object { $_.event_type -ne "acked" })
if ($nonAcked.Count -eq 0) {
    Pass "event_type filter scoped results to 'acked' only"
} else {
    Fail "event_type filter leaked $($nonAcked.Count) non-acked rows"
}

# ---- 10. DELETE + cascade --------------------------------------------------

Section "10. DELETE /api/alarms/rules/{id}"

$eventCountBefore = [int](Psql "SELECT count(*) FROM alarm_events WHERE rule_id = $RuleId")

$r = Http -Method DELETE -Url "$Base/rules/$RuleId"
if ($r.ok) {
    Pass "DELETE returned success"
} else {
    Fail "DELETE failed: $($r.status)"
}

$r = Http -Method GET -Url "$Base/rules/$RuleId"
if ($r.status -eq 404) {
    Pass "Rule is gone (GET returns 404)"
} else {
    Fail "Rule still present after DELETE"
}

$stateAfterDelete = [int](Psql "SELECT count(*) FROM alarm_state WHERE rule_id = $RuleId")
if ($stateAfterDelete -eq 0) {
    Pass "alarm_state cascaded on rule deletion"
} else {
    Fail "alarm_state row survived (count=$stateAfterDelete)"
}

$eventCountAfter = [int](Psql "SELECT count(*) FROM alarm_events WHERE rule_id = $RuleId")
if ($eventCountAfter -eq $eventCountBefore -and $eventCountAfter -gt 0) {
    Pass "alarm_events preserved across rule deletion ($eventCountAfter events retained)"
} else {
    Fail "alarm_events lost rows (before=$eventCountBefore, after=$eventCountAfter)"
}

# ---- Summary ---------------------------------------------------------------

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
Write-Host "Phase 14.2 alarms API is wired in. Next: 14.3 evaluator." -ForegroundColor Cyan
exit 0
