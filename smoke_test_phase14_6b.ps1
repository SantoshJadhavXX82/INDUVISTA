# =============================================================================
# Phase 14.6b — Fully automated rule-types CRUD smoke test.
#
# Verifies:
#   - Migration 0031 ran (table + FK + seeded 6 system types)
#   - Old CHECK constraint on rule_type was dropped (name-agnostic check)
#   - is_evaluable is true for hi_hi/hi/lo/lo_lo, false for
#     deviation/rate_of_change
#   - GET list / GET one / POST / PATCH / DELETE work
#   - is_evaluable is NOT settable from API (system-managed flag)
#   - Custom rule types created with is_evaluable=false (read-only default)
#   - System / in-use protection on DELETE
#   - Format validation (code regex, duplicate code, duplicate rank)
#   - Rule creation with unknown rule_type returns friendly 400
#   - Rule creation with custom rule_type succeeds (FK accepts it)
#
# Same defensive patterns as 14.6: try/finally, DockerQuiet, @() around
# Where-Object, ${var} interpolation.
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_6b.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Reasons = @()

$script:CreatedRuleTypeIds = @()
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

function Psql([string]$sql) {
    $output = docker compose exec -T postgres `
        psql -U induvista_admin -d induvista -tA -c $sql 2>&1
    return ($output | Out-String).Trim()
}

function PsqlExec([string]$sql) {
    $null = docker compose exec -T postgres `
        psql -U induvista_admin -d induvista -c $sql 2>&1
}

function Http {
    param([string]$Method, [string]$Url, [object]$Body = $null)
    $params = @{
        Method = $Method
        Uri = $Url
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
        try { $detail = $_.ErrorDetails.Message } catch {}
        return @{ ok = $false; status = $status; body = $detail }
    }
}

try {
    # ---- 0. Service health -------------------------------------------------

    Section "0. Service health"

    foreach ($svc in @("backend", "postgres")) {
        $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
        if ($psout -match '"State":\s*"running"' -or
            $psout -match '"Status":\s*"Up') {
            Pass "$svc is running"
        } else {
            Fail "$svc is not running"
        }
    }

    # ---- 1. OpenAPI registration ------------------------------------------

    Section "1. OpenAPI registers rule-types endpoints"

    $openapi = Invoke-RestMethod http://localhost:8000/openapi.json
    $rtPaths = @($openapi.paths.PSObject.Properties.Name |
        Where-Object { $_ -like "/api/alarms/rule-types*" })

    foreach ($expected in @(
        "/api/alarms/rule-types",
        "/api/alarms/rule-types/{rule_type_id}"
    )) {
        if ($rtPaths -contains $expected) {
            Pass "endpoint registered: $expected"
        } else {
            Fail "missing endpoint: $expected"
        }
    }

    # ---- 2. Schema ---------------------------------------------------------

    Section "2. Schema: alarm_rule_types table"

    $tableExists = Psql @"
SELECT count(*) FROM information_schema.tables
WHERE table_name = 'alarm_rule_types'
"@
    if ([int]$tableExists -eq 1) {
        Pass "alarm_rule_types table exists"
    } else {
        Fail "alarm_rule_types table is missing"
    }

    $fkExists = Psql @"
SELECT count(*) FROM information_schema.table_constraints
WHERE table_name = 'alarm_rules' AND constraint_name = 'alarm_rules_rule_type_fk'
"@
    if ([int]$fkExists -eq 1) {
        Pass "alarm_rules.rule_type FK constraint exists"
    } else {
        Fail "alarm_rules.rule_type FK constraint is missing"
    }

    # Name-agnostic check — same pattern that fixed the severity false-positive
    $checkGone = Psql @"
SELECT count(*) FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
WHERE rel.relname = 'alarm_rules'
  AND con.contype = 'c'
  AND pg_get_constraintdef(con.oid) ILIKE '%rule_type%'
"@
    if ([int]$checkGone -eq 0) {
        Pass "no CHECK constraint remains on alarm_rules.rule_type (FK is the gate)"
    } else {
        Fail "CHECK constraint(s) still exist on rule_type (count=$checkGone)"
    }

    # ---- 3. Seeded data ----------------------------------------------------

    Section "3. Six system rule types seeded"

    $seedCount = [int](Psql "SELECT count(*) FROM alarm_rule_types WHERE is_system = true")
    if ($seedCount -eq 6) {
        Pass "6 system rule types seeded"
    } else {
        Fail "Expected 6 system rule types, found $seedCount"
    }

    foreach ($code in @("hi_hi","hi","lo","lo_lo","deviation","rate_of_change")) {
        $present = [int](Psql "SELECT count(*) FROM alarm_rule_types WHERE code = '$code' AND is_system = true")
        if ($present -eq 1) {
            Pass "system rule type '$code' present"
        } else {
            Fail "system rule type '$code' missing"
        }
    }

    # ---- 4. is_evaluable flags correctly distinguish working types --------

    Section "4. is_evaluable correctly set for system types"

    foreach ($code in @("hi_hi", "hi", "lo", "lo_lo")) {
        $ev = Psql "SELECT is_evaluable FROM alarm_rule_types WHERE code = '$code'"
        if ($ev -eq "t") {
            Pass "$code is_evaluable=true (evaluator has logic)"
        } else {
            Fail "$code is_evaluable should be true, got '$ev'"
        }
    }
    foreach ($code in @("deviation", "rate_of_change")) {
        $ev = Psql "SELECT is_evaluable FROM alarm_rule_types WHERE code = '$code'"
        if ($ev -eq "f") {
            Pass "$code is_evaluable=false (awaiting phase 14.7)"
        } else {
            Fail "$code is_evaluable should be false, got '$ev'"
        }
    }

    # ---- 5. GET list -------------------------------------------------------

    Section "5. GET /api/alarms/rule-types"

    $list = Http -Method GET -Url "http://localhost:8000/api/alarms/rule-types"
    if (-not $list.ok) {
        Fail "GET list failed: $($list.status)"
        return
    }

    if ($list.body.Count -ge 6) {
        Pass "GET list returned $($list.body.Count) rows (>= 6 expected)"
    } else {
        Fail "GET list returned $($list.body.Count) rows (<6)"
    }

    $ranks = @($list.body | ForEach-Object { $_.rank })
    $sorted = $true
    for ($i = 1; $i -lt $ranks.Count; $i++) {
        if ($ranks[$i] -lt $ranks[$i-1]) { $sorted = $false; break }
    }
    if ($sorted) { Pass "Results sorted by rank ascending" }
    else         { Fail "Results NOT sorted by rank" }

    # Verify in_use_count and is_evaluable fields present
    $missingFields = @($list.body | Where-Object {
        $_.PSObject.Properties.Name -notcontains 'in_use_count' -or
        $_.PSObject.Properties.Name -notcontains 'is_evaluable'
    })
    if ($missingFields.Count -eq 0) {
        Pass "All rows include in_use_count and is_evaluable"
    } else {
        Fail "Some rows missing in_use_count or is_evaluable"
    }

    # ---- 6. POST create custom rule type ----------------------------------

    Section "6. POST create custom rule type"

    PsqlExec "DELETE FROM alarm_rule_types WHERE code IN ('test_oscillation','test_freeze','test_other','test_x')"

    $createResp = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rule-types" `
        -Body @{
            code = "test_oscillation"
            label = "Oscillation"
            description = "Custom test type for oscillation detection"
            rank = 7
        }

    if ($createResp.ok -and $createResp.body.code -eq "test_oscillation") {
        $script:CreatedRuleTypeIds += $createResp.body.id
        Pass "Created rule type id=$($createResp.body.id)"
    } else {
        Fail "POST create failed: status=$($createResp.status), body=$($createResp.body)"
    }

    if ($createResp.body.is_system -eq $false) {
        Pass "Custom rule type has is_system=false"
    } else {
        Fail "Custom rule type is_system should be false"
    }

    if ($createResp.body.is_evaluable -eq $false) {
        Pass "Custom rule type defaults to is_evaluable=false (no evaluator support)"
    } else {
        Fail "Custom rule type is_evaluable should be false by default"
    }

    # ---- 7. Custom rule type's is_evaluable cannot be set via POST -------

    Section "7. is_evaluable is read-only from API"

    # The Pydantic model doesn't have is_evaluable field, so passing it
    # is silently ignored. Verify by trying to create with is_evaluable=true:
    $sneakResp = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rule-types" `
        -Body @{
            code = "test_freeze"
            label = "Frozen Value"
            description = "test"
            rank = 8
            is_evaluable = $true   # ignored by Pydantic (extra field)
        }

    if ($sneakResp.ok) {
        $script:CreatedRuleTypeIds += $sneakResp.body.id
        if ($sneakResp.body.is_evaluable -eq $false) {
            Pass "is_evaluable=true in request was ignored (still false on response)"
        } else {
            Fail "is_evaluable should have been ignored, got $($sneakResp.body.is_evaluable)"
        }
    } else {
        Fail "Couldn't create test_freeze: $($sneakResp.status)"
    }

    # ---- 8. Validation: duplicate code ------------------------------------

    Section "8. POST duplicate code rejected"

    $dupCode = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rule-types" `
        -Body @{ code = "test_oscillation"; label = "Other";
                 description = "x"; rank = 99 }

    if ($dupCode.status -eq 409) {
        Pass "Duplicate code returned 409"
    } else {
        Fail "Duplicate code: expected 409, got $($dupCode.status)"
    }

    # ---- 9. Validation: duplicate rank ------------------------------------

    Section "9. POST duplicate rank rejected"

    $dupRank = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rule-types" `
        -Body @{ code = "test_other"; label = "Other";
                 description = "x"; rank = 1 }  # rank 1 is hi_hi

    if ($dupRank.status -eq 409) {
        Pass "Duplicate rank returned 409"
    } else {
        Fail "Duplicate rank: expected 409, got $($dupRank.status)"
    }

    # ---- 10. Validation: invalid code -------------------------------------

    Section "10. POST invalid code rejected"

    $badCode = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rule-types" `
        -Body @{ code = "BadCode"; label = "Bad"; description = "x"; rank = 99 }

    if ($badCode.status -ge 400 -and $badCode.status -lt 500) {
        Pass "Invalid code rejected with 4xx ($($badCode.status))"
    } else {
        Fail "Invalid code: expected 4xx, got $($badCode.status)"
    }

    # ---- 11. PATCH update label + description ----------------------------

    Section "11. PATCH update label + description"

    $newId = $script:CreatedRuleTypeIds[0]
    $patchLabel = Http -Method PATCH `
        -Url "http://localhost:8000/api/alarms/rule-types/$newId" `
        -Body @{ label = "Oscillation (renamed)"; description = "Updated desc" }

    if ($patchLabel.ok -and $patchLabel.body.label -eq "Oscillation (renamed)" -and
        $patchLabel.body.description -eq "Updated desc") {
        Pass "Label and description updated"
    } else {
        Fail "PATCH failed: $($patchLabel.status), $($patchLabel.body)"
    }

    # ---- 12. PATCH is_evaluable is rejected/ignored ----------------------

    Section "12. PATCH is_evaluable is ignored"

    $patchEvaluable = Http -Method PATCH `
        -Url "http://localhost:8000/api/alarms/rule-types/$newId" `
        -Body @{ is_evaluable = $true; label = "Still Oscillation" }

    if ($patchEvaluable.ok -and $patchEvaluable.body.is_evaluable -eq $false) {
        Pass "is_evaluable=true in PATCH was ignored (still false)"
    } else {
        Fail "is_evaluable should remain false after PATCH attempt"
    }

    # ---- 13. PATCH system row label ------------------------------------

    Section "13. PATCH system row label is editable"

    $hiHiId = [int](Psql "SELECT id FROM alarm_rule_types WHERE code = 'hi_hi'")
    $patchSys = Http -Method PATCH `
        -Url "http://localhost:8000/api/alarms/rule-types/$hiHiId" `
        -Body @{ label = "High-High (test)" }

    if ($patchSys.ok -and $patchSys.body.label -eq "High-High (test)") {
        Pass "System rule type label is editable"
        # Restore
        $null = Http -Method PATCH `
            -Url "http://localhost:8000/api/alarms/rule-types/$hiHiId" `
            -Body @{ label = "High-High" }
    } else {
        Fail "System label edit failed: $($patchSys.status)"
    }

    # ---- 14. Rule creation with unknown rule_type returns 400 ----------

    Section "14. POST /alarms/rules with unknown rule_type -> 400"

    $script:TestTagId = [int](Psql "SELECT id FROM tags ORDER BY id LIMIT 1")
    $badRule = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules" `
        -Body @{
            tag_id = $script:TestTagId
            rule_type = "nonexistent_type"; severity = "high"
            threshold = 999999; deadband = 0
            on_delay_sec = 0; off_delay_sec = 0
            latched = $false; enabled = $true
        }

    if ($badRule.status -eq 400) {
        Pass "Rule with unknown rule_type returned 400 (friendly error)"
    } elseif ($badRule.status -eq 500) {
        Fail "Returned 500 - FK error leaked through, error handler broken"
    } else {
        Fail "Expected 400, got $($badRule.status)"
    }

    # ---- 15. Rule creation with custom rule_type succeeds --------------

    Section "15. POST /alarms/rules with custom rule_type succeeds"

    PsqlExec "DELETE FROM alarm_rules WHERE tag_id = ${script:TestTagId}"

    $customRule = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules" `
        -Body @{
            tag_id = $script:TestTagId
            rule_type = "test_oscillation"; severity = "high"
            threshold = 999999; deadband = 0
            on_delay_sec = 3600; off_delay_sec = 3600
            latched = $false; enabled = $true
        }

    if ($customRule.ok -and $customRule.body.rule_type -eq "test_oscillation") {
        $script:CreatedRuleIds += $customRule.body.id
        Pass "Rule created with custom rule_type 'test_oscillation'"
    } else {
        Fail "Rule with custom rule_type failed: $($customRule.status), $($customRule.body)"
    }

    # ---- 16. Delete in-use rule type rejected -------------------------

    Section "16. DELETE rule type in use -> 409"

    $delInUse = Http -Method DELETE `
        -Url "http://localhost:8000/api/alarms/rule-types/$newId"

    if ($delInUse.status -eq 409) {
        Pass "Delete of in-use rule type returned 409"
    } else {
        Fail "Delete in-use: expected 409, got $($delInUse.status)"
    }

    # ---- 17. Delete system rule type rejected ------------------------

    Section "17. DELETE system rule type -> 409"

    $delSys = Http -Method DELETE `
        -Url "http://localhost:8000/api/alarms/rule-types/$hiHiId"

    if ($delSys.status -eq 409) {
        Pass "Delete of system 'hi_hi' returned 409"
    } else {
        Fail "Delete system: expected 409, got $($delSys.status)"
    }

    # ---- 18. Delete works after rule unreferenced -------------------

    Section "18. DELETE after removing referencing rule"

    foreach ($ruleId in $script:CreatedRuleIds) {
        PsqlExec "DELETE FROM alarm_rules WHERE id = $ruleId"
    }
    $script:CreatedRuleIds = @()

    $delOk = Http -Method DELETE `
        -Url "http://localhost:8000/api/alarms/rule-types/$newId"

    if ($delOk.status -eq 204 -or $delOk.status -eq 200) {
        Pass "Delete of non-system, not-in-use rule type succeeded (204)"
        $script:CreatedRuleTypeIds = @($script:CreatedRuleTypeIds | Where-Object { $_ -ne $newId })
    } else {
        Fail "Delete success path failed: $($delOk.status)"
    }

    $stillExists = [int](Psql "SELECT count(*) FROM alarm_rule_types WHERE id = $newId")
    if ($stillExists -eq 0) {
        Pass "Rule type row is gone from DB"
    } else {
        Fail "Rule type row still exists after DELETE"
    }
}
finally {
    Section "Cleanup"

    foreach ($ruleId in $script:CreatedRuleIds) {
        PsqlExec "DELETE FROM alarm_rules WHERE id = $ruleId"
    }

    foreach ($rtId in $script:CreatedRuleTypeIds) {
        PsqlExec "DELETE FROM alarm_rule_types WHERE id = $rtId AND is_system = false"
    }

    PsqlExec "DELETE FROM alarm_rule_types WHERE code IN ('test_oscillation','test_freeze','test_other','test_x') AND is_system = false"
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
Write-Host "Phase 14.6b rule-types admin backend fully verified." -ForegroundColor Cyan
Write-Host "Next: 14.6b frontend (admin page under /global/alarm-types)." -ForegroundColor Cyan
exit 0
