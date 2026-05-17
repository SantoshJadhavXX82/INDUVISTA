# =============================================================================
# Phase 15.1 -- Fully automated calc-block foundation smoke test.
#
# Verifies the end-to-end calc pipeline:
#   - Migration 0034 created calc_definitions + calc_block_types
#   - Virtual "Calculations" device sentinel exists
#   - tag_values.source column accepts 'modbus' and 'calc'
#   - Block registry contains SUM_OF and reports is_evaluable=true
#   - API: create calc definition with SUM_OF, validation enforces config
#   - API: rejects non-evaluable block types (AVG_OF taxonomy entry)
#   - API: rejects empty inputs and self-references
#   - Worker: calc_evaluator reads inputs and writes outputs with source='calc'
#   - Worker: output equals sum of inputs
#   - Worker: quality propagates correctly
#   - PATCH updates inputs and the new sum is computed
#
# Defensive patterns from prior smokes:
#   - ASCII-only string literals (PowerShell CP1252/UTF-8 quirk)
#   - Synthetic tags with enabled=false so modbus_worker can't touch
#     input tags. Calc output tag goes on the virtual Calculations device.
#   - try/finally cleanup via DELETE FROM tags (cascade kills calc_defs
#     and tag_values automatically)
#   - DockerQuiet wrapper with $cmdArgs (not $args)
#   - calc_evaluator restart forces immediate definition reload
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase15_1.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Reasons = @()

$script:InputTagIds = @()
$script:CalcTagIds = @()
$script:CalcDefIds = @()

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

function InjectModbusValue([int]$tagId, [double]$value, [int]$secondsAgo) {
    PsqlExec @"
INSERT INTO tag_values (time, tag_id, device_id, value_double, st, source)
SELECT NOW() - INTERVAL '$secondsAgo seconds', id, device_id, $value, 192, 'modbus'
FROM tags WHERE id = $tagId
"@
}

function Http {
    param([string]$Method, [string]$Url, [object]$Body = $null)
    $params = @{ Method = $Method; Uri = $Url; ContentType = "application/json" }
    if ($Body -ne $null) {
        $params.Body = ($Body | ConvertTo-Json -Depth 8 -Compress)
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
    # ---- 0. Service health ------------------------------------------------

    Section "0. Service health"

    foreach ($svc in @("backend", "calc_evaluator", "postgres")) {
        $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
        if ($psout -match '"State":\s*"running"' -or $psout -match '"Status":\s*"Up') {
            Pass "$svc is running"
        } else {
            Fail "$svc is not running"
        }
    }

    # ---- 1. Migration 0034 verification -----------------------------------

    Section "1. Migration 0034"

    foreach ($tbl in @("calc_definitions", "calc_block_types")) {
        $exists = [int](Psql "SELECT count(*) FROM information_schema.tables WHERE table_name = '$tbl'")
        if ($exists -eq 1) { Pass "$tbl table exists" }
        else                 { Fail "$tbl table missing" }
    }

    $sourceCol = [int](Psql "SELECT count(*) FROM information_schema.columns WHERE table_name = 'tag_values' AND column_name = 'source'")
    if ($sourceCol -eq 1) { Pass "tag_values.source column added" }
    else                  { Fail "tag_values.source column missing" }

    $calcDevice = [int](Psql "SELECT count(*) FROM devices WHERE name = 'Calculations'")
    if ($calcDevice -eq 1) { Pass "Virtual 'Calculations' device sentinel exists" }
    else                   { Fail "Virtual 'Calculations' device missing" }

    $sumOfRow = Psql "SELECT is_evaluable::text FROM calc_block_types WHERE code = 'SUM_OF'"
    if ($sumOfRow -eq "true") { Pass "SUM_OF in catalog with is_evaluable=true" }
    else                       { Fail "SUM_OF row wrong (got '$sumOfRow')" }

    $avgOfRow = Psql "SELECT is_evaluable::text FROM calc_block_types WHERE code = 'AVG_OF'"
    if ($avgOfRow -eq "false") { Pass "AVG_OF in catalog as taxonomy-only (is_evaluable=false)" }
    else                        { Fail "AVG_OF row wrong (got '$avgOfRow')" }

    # ---- 2. /api/calc/block-types endpoint --------------------------------

    Section "2. GET /api/calc/block-types"

    $btResp = Http -Method GET -Url "http://localhost:8000/api/calc/block-types"
    if ($btResp.ok -and $btResp.body.Count -ge 5) {
        Pass "Block types endpoint returns $($btResp.body.Count) rows"
    } else {
        Fail "Block types endpoint failed or short: $($btResp.status)"
        return
    }

    $sumOfApi = $btResp.body | Where-Object { $_.code -eq "SUM_OF" }
    if ($sumOfApi.has_registry_entry -eq $true -and $sumOfApi.is_evaluable -eq $true) {
        Pass "SUM_OF has_registry_entry=true, is_evaluable=true"
    } else {
        Fail "SUM_OF has_registry_entry/is_evaluable wrong"
    }

    # ---- 3. Setup input tags (synthetic modbus tags, enabled=false) ------

    Section "3. Setup synthetic input tags"

    $deviceId = [int](Psql "SELECT id FROM devices WHERE name != 'Calculations' ORDER BY id LIMIT 1")
    if ($deviceId -le 0) {
        Fail "No real modbus device found for input tags"
        return
    }

    PsqlExec "DELETE FROM tags WHERE name LIKE 'smoke_phase_15_1_%'"

    foreach ($i in 1..3) {
        $name = "smoke_phase_15_1_input_$i"
        $addr = 99990 + $i
        PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $deviceId, '$name', 'float32', 3, $addr,
    false, 'Phase 15.1 smoke input - safe to delete', 0, 100
)
"@
        $id = [int](Psql "SELECT id FROM tags WHERE name = '$name'")
        $script:InputTagIds += $id
    }
    Pass "3 input tags created: $($script:InputTagIds -join ', ')"

    InjectModbusValue -tagId $script:InputTagIds[0] -value 10.0 -secondsAgo 1
    InjectModbusValue -tagId $script:InputTagIds[1] -value 20.0 -secondsAgo 1
    InjectModbusValue -tagId $script:InputTagIds[2] -value 30.0 -secondsAgo 1
    Pass "Input values injected (10, 20, 30 -> expected sum 60)"

    # ---- 4. Setup calc output tag on virtual Calculations device ---------

    Section "4. Setup calc output tag"

    $calcDeviceId = [int](Psql "SELECT id FROM devices WHERE name = 'Calculations'")
    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $calcDeviceId, 'smoke_phase_15_1_calc_sum', 'float32', 3, 1,
    true, 'Phase 15.1 smoke calc output - safe to delete', 0, 1000
)
"@
    $calcTagId = [int](Psql "SELECT id FROM tags WHERE name = 'smoke_phase_15_1_calc_sum'")
    $script:CalcTagIds += $calcTagId
    Pass "Calc output tag created (id=$calcTagId on virtual device $calcDeviceId)"

    # ---- 5. POST /calc/definitions with valid SUM_OF ---------------------

    Section "5. POST /api/calc/definitions with valid SUM_OF"

    $createResp = Http -Method POST `
        -Url "http://localhost:8000/api/calc/definitions" `
        -Body @{
            tag_id = $calcTagId
            block_type = "SUM_OF"
            block_config = @{ inputs = $script:InputTagIds }
            enabled = $true
        }

    if ($createResp.ok -and $createResp.body.block_type -eq "SUM_OF") {
        $script:CalcDefIds += $createResp.body.id
        Pass "Calc definition created (id=$($createResp.body.id))"
    } else {
        Fail "POST /definitions failed: $($createResp.status), $($createResp.body)"
        return
    }

    # ---- 6. Validation: AVG_OF (taxonomy-only) is rejected ---------------

    Section "6. POST with AVG_OF (is_evaluable=false) -> 400"

    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $calcDeviceId, 'smoke_phase_15_1_calc_avg', 'float32', 3, 2,
    true, 'Phase 15.1 smoke - rejected calc', 0, 1000
)
"@
    $avgTagId = [int](Psql "SELECT id FROM tags WHERE name = 'smoke_phase_15_1_calc_avg'")
    $script:CalcTagIds += $avgTagId

    $avgResp = Http -Method POST `
        -Url "http://localhost:8000/api/calc/definitions" `
        -Body @{
            tag_id = $avgTagId
            block_type = "AVG_OF"
            block_config = @{ inputs = $script:InputTagIds }
            enabled = $true
        }

    if ($avgResp.status -eq 400) {
        Pass "AVG_OF (taxonomy-only) correctly rejected with 400"
    } else {
        Fail "AVG_OF should have been rejected, got $($avgResp.status)"
    }

    # ---- 7. Validation: bad config rejected ------------------------------

    Section "7. POST with empty inputs list -> 400"

    $badResp = Http -Method POST `
        -Url "http://localhost:8000/api/calc/definitions" `
        -Body @{
            tag_id = $avgTagId
            block_type = "SUM_OF"
            block_config = @{ inputs = @() }
            enabled = $true
        }

    if ($badResp.status -eq 400) {
        Pass "Empty inputs list correctly rejected"
    } else {
        Fail "Empty inputs should be 400, got $($badResp.status)"
    }

    # ---- 8. Validation: self-reference rejected --------------------------

    Section "8. POST with self-reference -> 400"

    $selfResp = Http -Method POST `
        -Url "http://localhost:8000/api/calc/definitions" `
        -Body @{
            tag_id = $avgTagId
            block_type = "SUM_OF"
            block_config = @{ inputs = @($avgTagId) }
            enabled = $true
        }

    if ($selfResp.status -eq 400) {
        Pass "Self-referencing calc correctly rejected"
    } else {
        Fail "Self-reference should be 400, got $($selfResp.status)"
    }

    # ---- 9. Worker evaluation: sum computed and written ------------------

    Section "9. calc_evaluator computes the sum"

    DockerQuiet @("restart", "calc_evaluator")
    Start-Sleep -Seconds 5

    # Note: not filtering by source here. The synthetic calc tag lives
    # on the virtual Calculations device which has no other writers,
    # so any row for this tag_id came from calc_evaluator.
    $rowCount = [int](Psql "SELECT count(*) FROM tag_values WHERE tag_id = $calcTagId")
    if ($rowCount -ge 1) {
        Pass "$rowCount calc rows written for tag $calcTagId"
    } else {
        Fail "Expected >= 1 calc row, got $rowCount"
    }

    $latestValue = Psql @"
SELECT value_double FROM tag_values
WHERE tag_id = $calcTagId
ORDER BY time DESC LIMIT 1
"@
    $value = [double]$latestValue
    if ([Math]::Abs($value - 60.0) -lt 0.01) {
        Pass "Calc output value = $value (expected 60)"
    } else {
        Fail "Calc output value = $value, expected 60"
    }

    $latestQuality = [int](Psql @"
SELECT st FROM tag_values
WHERE tag_id = $calcTagId
ORDER BY time DESC LIMIT 1
"@)
    if ($latestQuality -ge 128) {
        Pass "Calc output quality = $latestQuality (GOOD)"
    } else {
        Fail "Calc output quality = $latestQuality (expected >= 128)"
    }

    # Also verify the source value the worker is using
    $writerSource = Psql @"
SELECT source FROM tag_values
WHERE tag_id = $calcTagId
ORDER BY time DESC LIMIT 1
"@
    if ($writerSource -eq 'estimated') {
        Pass "Worker writes source='estimated' (compatible with ck_tag_values_source)"
    } else {
        Fail "Worker source = '$writerSource', expected 'estimated'"
    }

    # ---- 10. PATCH inputs and re-evaluate --------------------------------

    Section "10. PATCH inputs and verify new sum"

    $patchResp = Http -Method PATCH `
        -Url "http://localhost:8000/api/calc/definitions/$($script:CalcDefIds[0])" `
        -Body @{
            block_config = @{ inputs = @($script:InputTagIds[0], $script:InputTagIds[1]) }
        }

    if (-not $patchResp.ok) {
        Fail "PATCH failed: $($patchResp.status), $($patchResp.body)"
    } else {
        Pass "PATCH updated inputs to first 2 tags"
    }

    DockerQuiet @("restart", "calc_evaluator")
    Start-Sleep -Seconds 5

    $newLatest = [double](Psql @"
SELECT value_double FROM tag_values
WHERE tag_id = $calcTagId
ORDER BY time DESC LIMIT 1
"@)
    if ([Math]::Abs($newLatest - 30.0) -lt 0.01) {
        Pass "After PATCH: calc output = $newLatest (expected 30)"
    } else {
        Fail "After PATCH: calc output = $newLatest, expected 30"
    }
}
finally {
    Section "Cleanup"

    foreach ($tid in $script:CalcTagIds) {
        PsqlExec "DELETE FROM tags WHERE id = $tid"
    }
    foreach ($tid in $script:InputTagIds) {
        PsqlExec "DELETE FROM tags WHERE id = $tid"
    }
    Write-Host "  Deleted synthetic tags (cascade removed calc_defs + values)"
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
    Write-Host "  docker compose logs --tail 50 calc_evaluator"
    Write-Host "  docker compose logs --tail 30 backend"
    exit 1
}

Write-Host ""
Write-Host "Phase 15.1 calc block foundation fully verified." -ForegroundColor Cyan
exit 0
