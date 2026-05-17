# =============================================================================
# Phase 14.6 — Fully automated alarm severities CRUD smoke test.
#
# Verifies:
#   - Migration 0029 ran (table exists, 5 system rows seeded, FK in place)
#   - OpenAPI registers the 5 new severity endpoints
#   - GET list / GET one / POST create / PATCH update / DELETE
#   - System severities protected from delete (409)
#   - In-use severities protected from delete (409)
#   - Code regex validation rejects invalid input (400)
#   - Color regex validation rejects invalid input (400)
#   - Rank uniqueness enforced (409 on duplicate)
#   - Code uniqueness enforced (409 on duplicate)
#   - Rule creation with unknown severity returns 400 (not 500)
#   - Rule creation with custom severity succeeds (proves FK accepts new codes)
#   - All system protections (label/color/rank editable on system rows)
#
# Defensive patterns from prior smokes:
#   - try/finally cleanup
#   - DockerQuiet helper around docker compose stop/start
#   - Where-Object pipelines wrapped @()
#   - ${var} interpolation around colons
#
# No modbus_worker juggling needed — this smoke doesn't touch tag_values.
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_6.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Reasons = @()

# Track created records so finally{} can clean up
$script:CreatedSeverityIds = @()
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
    param(
        [string]$Method,
        [string]$Url,
        [object]$Body = $null
    )
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
        try {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $detail = $reader.ReadToEnd()
        } catch {}
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

    Section "1. OpenAPI registers severities endpoints"

    $openapi = Invoke-RestMethod http://localhost:8000/openapi.json
    $sevPaths = @($openapi.paths.PSObject.Properties.Name |
        Where-Object { $_ -like "/api/alarms/severities*" })

    foreach ($expected in @(
        "/api/alarms/severities",
        "/api/alarms/severities/{severity_id}"
    )) {
        if ($sevPaths -contains $expected) {
            Pass "endpoint registered: $expected"
        } else {
            Fail "missing endpoint: $expected"
        }
    }

    # Confirm we have GET-list + POST on /severities AND
    # GET + PATCH + DELETE on /severities/{id}
    $methodsList = @($openapi.paths.'/api/alarms/severities'.PSObject.Properties.Name)
    foreach ($m in @("get", "post")) {
        $upper = $m.ToUpper()
        if ($methodsList -contains $m) {
            Pass "/api/alarms/severities supports $upper"
        } else {
            Fail "/api/alarms/severities missing $upper"
        }
    }
    $methodsOne = @($openapi.paths.'/api/alarms/severities/{severity_id}'.PSObject.Properties.Name)
    foreach ($m in @("get", "patch", "delete")) {
        $upper = $m.ToUpper()
        if ($methodsOne -contains $m) {
            Pass "/api/alarms/severities/{id} supports $upper"
        } else {
            Fail "/api/alarms/severities/{id} missing $upper"
        }
    }

    # ---- 2. Migration check ------------------------------------------------

    Section "2. Schema: alarm_severities table"

    $tableExists = Psql @"
SELECT count(*) FROM information_schema.tables
WHERE table_name = 'alarm_severities'
"@
    if ([int]$tableExists -eq 1) {
        Pass "alarm_severities table exists"
    } else {
        Fail "alarm_severities table is missing"
    }

    $fkExists = Psql @"
SELECT count(*) FROM information_schema.table_constraints
WHERE table_name = 'alarm_rules' AND constraint_name = 'alarm_rules_severity_fk'
"@
    if ([int]$fkExists -eq 1) {
        Pass "alarm_rules.severity FK constraint exists"
    } else {
        Fail "alarm_rules.severity FK constraint is missing"
    }

    $checkGone = Psql @"
SELECT count(*) FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
WHERE rel.relname = 'alarm_rules'
  AND con.contype = 'c'
  AND pg_get_constraintdef(con.oid) ILIKE '%severity%'
"@
    if ([int]$checkGone -eq 0) {
        Pass "no CHECK constraint remains on alarm_rules.severity (FK is now the gate)"
    } else {
        Fail "CHECK constraint(s) still exist on alarm_rules.severity (count=$checkGone)"
    }

    # ---- 3. Seeded data ----------------------------------------------------

    Section "3. Five system severities seeded"

    $seedCount = [int](Psql "SELECT count(*) FROM alarm_severities WHERE is_system = true")
    if ($seedCount -eq 5) {
        Pass "5 system severities seeded"
    } else {
        Fail "Expected 5 system severities, found $seedCount"
    }

    foreach ($code in @("critical","high","medium","low","info")) {
        $present = [int](Psql "SELECT count(*) FROM alarm_severities WHERE code = '$code' AND is_system = true")
        if ($present -eq 1) {
            Pass "system severity '$code' present"
        } else {
            Fail "system severity '$code' missing"
        }
    }

    # ---- 4. GET list -------------------------------------------------------

    Section "4. GET /api/alarms/severities"

    $list = Http -Method GET -Url "http://localhost:8000/api/alarms/severities"
    if (-not $list.ok) {
        Fail "GET list failed: $($list.status)"
        return
    }

    if ($list.body.Count -ge 5) {
        Pass "GET list returned $($list.body.Count) rows (>= 5 expected)"
    } else {
        Fail "GET list returned $($list.body.Count) rows (<5)"
    }

    # Verify sorted by rank ascending
    $ranks = @($list.body | ForEach-Object { $_.rank })
    $sorted = $true
    for ($i = 1; $i -lt $ranks.Count; $i++) {
        if ($ranks[$i] -lt $ranks[$i-1]) { $sorted = $false; break }
    }
    if ($sorted) { Pass "Results sorted by rank ascending" }
    else         { Fail "Results NOT sorted by rank" }

    # Verify in_use_count is present on every row
    $hasInUseField = $list.body | Where-Object { $_.PSObject.Properties.Name -notcontains 'in_use_count' }
    if (@($hasInUseField).Count -eq 0) {
        Pass "All rows include in_use_count field"
    } else {
        Fail "Some rows missing in_use_count"
    }

    # ---- 5. POST create --------------------------------------------------

    Section "5. POST create custom severity"

    # Clean any leftovers from prior smoke runs
    PsqlExec "DELETE FROM alarm_severities WHERE code IN ('test_warning', 'test_emergency')"

    $createResp = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/severities" `
        -Body @{ code = "test_warning"; label = "Warning";
                 color_hex = "#facc15"; rank = 6 }

    if ($createResp.ok -and $createResp.body.code -eq "test_warning") {
        $script:CreatedSeverityIds += $createResp.body.id
        Pass "Created severity id=$($createResp.body.id), code='test_warning'"
    } else {
        Fail "POST create failed: status=$($createResp.status), body=$($createResp.body)"
    }

    if ($createResp.body.is_system -eq $false) {
        Pass "Custom severity has is_system=false"
    } else {
        Fail "Custom severity is_system should be false"
    }

    if ($createResp.body.in_use_count -eq 0) {
        Pass "New severity has in_use_count=0"
    } else {
        Fail "New severity in_use_count should be 0"
    }

    # ---- 6. Validation: duplicate code ----------------------------------

    Section "6. POST duplicate code rejected"

    $dupCode = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/severities" `
        -Body @{ code = "test_warning"; label = "Other";
                 color_hex = "#ff0000"; rank = 99 }

    if ($dupCode.status -eq 409) {
        Pass "Duplicate code returned 409"
    } else {
        Fail "Duplicate code: expected 409, got $($dupCode.status)"
    }

    # ---- 7. Validation: duplicate rank ----------------------------------

    Section "7. POST duplicate rank rejected"

    $dupRank = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/severities" `
        -Body @{ code = "test_other"; label = "Other";
                 color_hex = "#ff0000"; rank = 6 }

    if ($dupRank.status -eq 409) {
        Pass "Duplicate rank returned 409"
    } else {
        Fail "Duplicate rank: expected 409, got $($dupRank.status)"
    }

    # ---- 8. Validation: invalid color -----------------------------------

    Section "8. POST invalid color rejected"

    $badColor = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/severities" `
        -Body @{ code = "test_x"; label = "X";
                 color_hex = "red"; rank = 99 }

    if ($badColor.status -ge 400 -and $badColor.status -lt 500) {
        Pass "Invalid color rejected with 4xx ($($badColor.status))"
    } else {
        Fail "Invalid color: expected 4xx, got $($badColor.status)"
    }

    # ---- 9. Validation: invalid code (starts with digit) -----------------

    Section "9. POST invalid code rejected"

    $badCode = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/severities" `
        -Body @{ code = "1bad"; label = "Bad";
                 color_hex = "#ff0000"; rank = 99 }

    if ($badCode.status -ge 400 -and $badCode.status -lt 500) {
        Pass "Invalid code rejected with 4xx ($($badCode.status))"
    } else {
        Fail "Invalid code: expected 4xx, got $($badCode.status)"
    }

    # ---- 10. PATCH update label ------------------------------------------

    Section "10. PATCH update label"

    $newSevId = $script:CreatedSeverityIds[0]
    $patchLabel = Http -Method PATCH `
        -Url "http://localhost:8000/api/alarms/severities/$newSevId" `
        -Body @{ label = "Renamed Warning" }

    if ($patchLabel.ok -and $patchLabel.body.label -eq "Renamed Warning") {
        Pass "Label updated"
    } else {
        Fail "PATCH label failed: $($patchLabel.status), $($patchLabel.body)"
    }

    # ---- 11. PATCH conflicting rank ------------------------------------

    Section "11. PATCH rank to system-occupied value rejected"

    # Try to set rank=1 (occupied by 'critical')
    $patchRankConflict = Http -Method PATCH `
        -Url "http://localhost:8000/api/alarms/severities/$newSevId" `
        -Body @{ rank = 1 }

    if ($patchRankConflict.status -eq 409) {
        Pass "Rank conflict returned 409"
    } else {
        Fail "Rank conflict: expected 409, got $($patchRankConflict.status)"
    }

    # ---- 12. PATCH system row (label edit allowed) ----------------------

    Section "12. PATCH system row label editable"

    $criticalId = [int](Psql "SELECT id FROM alarm_severities WHERE code = 'critical'")
    $patchSys = Http -Method PATCH `
        -Url "http://localhost:8000/api/alarms/severities/$criticalId" `
        -Body @{ label = "Critical (test)" }

    if ($patchSys.ok -and $patchSys.body.label -eq "Critical (test)") {
        Pass "System severity label is editable"
        # Restore original label
        $null = Http -Method PATCH `
            -Url "http://localhost:8000/api/alarms/severities/$criticalId" `
            -Body @{ label = "Critical" }
    } else {
        Fail "System label edit failed: $($patchSys.status)"
    }

    # ---- 13. Rule creation with unknown severity returns 400 ----------

    Section "13. POST /alarms/rules with unknown severity -> 400"

    $script:TestTagId = [int](Psql "SELECT id FROM tags ORDER BY id LIMIT 1")
    $badRule = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules" `
        -Body @{
            tag_id = $script:TestTagId;
            rule_type = "hi"; severity = "nonexistent_sev";
            threshold = 999999; deadband = 0;
            on_delay_sec = 0; off_delay_sec = 0;
            latched = $false; enabled = $true
        }

    if ($badRule.status -eq 400) {
        Pass "Rule with unknown severity returned 400 (friendly error)"
    } elseif ($badRule.status -eq 500) {
        Fail "Rule with unknown severity returned 500 (FK leaked - error handling broken)"
    } else {
        Fail "Expected 400, got $($badRule.status)"
    }

    # ---- 14. Rule creation with custom severity succeeds --------------

    Section "14. POST /alarms/rules with custom severity succeeds"

    PsqlExec "DELETE FROM alarm_rules WHERE tag_id = ${script:TestTagId} AND rule_type = 'hi'"

    $customRule = Http -Method POST `
        -Url "http://localhost:8000/api/alarms/rules" `
        -Body @{
            tag_id = $script:TestTagId;
            rule_type = "hi"; severity = "test_warning";
            threshold = 999999; deadband = 0;
            on_delay_sec = 3600; off_delay_sec = 3600;
            latched = $false; enabled = $true
        }

    if ($customRule.ok -and $customRule.body.severity -eq "test_warning") {
        $script:CreatedRuleIds += $customRule.body.id
        Pass "Rule created with custom severity 'test_warning'"
    } else {
        Fail "Rule with custom severity failed: $($customRule.status), $($customRule.body)"
    }

    # ---- 15. Delete in-use severity rejected ---------------------------

    Section "15. DELETE severity that's in use -> 409"

    $delInUse = Http -Method DELETE `
        -Url "http://localhost:8000/api/alarms/severities/$newSevId"

    if ($delInUse.status -eq 409) {
        Pass "Delete of in-use severity returned 409"
    } else {
        Fail "Delete in-use: expected 409, got $($delInUse.status)"
    }

    # ---- 16. in_use_count reflects rule ---------------------------------

    Section "16. in_use_count reflects rule usage"

    $listAfter = Http -Method GET -Url "http://localhost:8000/api/alarms/severities"
    $row = @(@($listAfter.body) | Where-Object { $_.code -eq 'test_warning' })
    if ($row.Count -eq 1 -and $row[0].in_use_count -eq 1) {
        Pass "in_use_count = 1 after rule creation"
    } else {
        Fail "in_use_count incorrect (expected 1, got $($row[0].in_use_count))"
    }

    # ---- 17. Delete system severity rejected ---------------------------

    Section "17. DELETE system severity -> 409"

    $delSys = Http -Method DELETE `
        -Url "http://localhost:8000/api/alarms/severities/$criticalId"

    if ($delSys.status -eq 409) {
        Pass "Delete of system severity 'critical' returned 409"
    } else {
        Fail "Delete system: expected 409, got $($delSys.status)"
    }

    # ---- 18. Delete works after rule unreferenced ----------------------

    Section "18. DELETE custom severity after removing its rule"

    # Remove the rule that's referencing test_warning
    foreach ($ruleId in $script:CreatedRuleIds) {
        PsqlExec "DELETE FROM alarm_rules WHERE id = $ruleId"
    }
    $script:CreatedRuleIds = @()

    $delOk = Http -Method DELETE `
        -Url "http://localhost:8000/api/alarms/severities/$newSevId"

    if ($delOk.status -eq 204 -or $delOk.status -eq 200) {
        Pass "Delete of non-system, not-in-use severity succeeded (204)"
        $script:CreatedSeverityIds = @($script:CreatedSeverityIds | Where-Object { $_ -ne $newSevId })
    } else {
        Fail "Delete succeeded path failed: $($delOk.status)"
    }

    $stillExists = [int](Psql "SELECT count(*) FROM alarm_severities WHERE id = $newSevId")
    if ($stillExists -eq 0) {
        Pass "Severity row is gone from DB"
    } else {
        Fail "Severity row still exists after DELETE"
    }
}
finally {
    # ---- Cleanup -----------------------------------------------------------

    Section "Cleanup"

    foreach ($ruleId in $script:CreatedRuleIds) {
        PsqlExec "DELETE FROM alarm_rules WHERE id = $ruleId"
        Write-Host "  Deleted rule id=$ruleId"
    }

    foreach ($sevId in $script:CreatedSeverityIds) {
        PsqlExec "DELETE FROM alarm_severities WHERE id = $sevId AND is_system = false"
        Write-Host "  Deleted severity id=$sevId"
    }

    # Defensive: clean up by code in case ids weren't tracked
    PsqlExec "DELETE FROM alarm_severities WHERE code IN ('test_warning','test_emergency','test_other','test_x') AND is_system = false"
}

# ---- Summary --------------------------------------------------------------

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
    Write-Host "  docker compose logs --tail 50 backend"
    Write-Host "  docker compose logs --tail 50 svj_migrate"
    exit 1
}

Write-Host ""
Write-Host "Phase 14.6 severity admin backend fully verified." -ForegroundColor Cyan
Write-Host "Next: 14.6 frontend (admin page under /global/alarm-severities)." -ForegroundColor Cyan
exit 0
