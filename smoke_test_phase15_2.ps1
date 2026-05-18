# =============================================================================
# Phase 15.2 -- Aggregation tier + execution rate smoke.
#
# Verifies:
#   Section 0: services healthy
#   Section 1: migration 0035 applied (new column, sidecar table, catalog)
#   Section 2: API exposes new blocks via /block-types
#   Section 3: synthetic input tags created
#   Section 4: rate validation - bogus rate rejected with 400
#   Section 5-18: 14 new blocks each evaluate correctly with known inputs
#                  including the NIST reference example V5-STAT-01:
#                  STDDEV_OF([9.0, 10.0, 11.0]) = 1.0 exactly
#   Section 19: multi-rate scheduling - 1s block executes ~6x in 6s
#   Section 20: execution stats endpoint returns last_executed_at,
#                duration_ms, total_executions
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase15_2.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Reasons = @()

$script:InputTagIds = @()
$script:CalcTagIds = @()

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

function InjectModbusValue([int]$tagId, [double]$value, [int]$quality = 192) {
    PsqlExec @"
INSERT INTO tag_values (time, tag_id, device_id, value_double, st, source)
SELECT NOW() - INTERVAL '1 second', id, device_id, $value, $quality, 'modbus'
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

# Each block-test helper: creates a calc tag with the block, restarts
# evaluator, waits, verifies the latest output value within tolerance.
function TestBlock {
    param(
        [string]$blockName,
        [string]$blockType,
        [int[]]$inputIds,
        [hashtable]$extraConfig,
        [double]$expectedValue,
        [double]$tolerance = 0.001,
        [int]$rate = 1000
    )
    # Create a fresh output tag on the virtual Calculations device
    $calcDeviceId = [int](Psql "SELECT id FROM devices WHERE name = 'Calculations'")
    $tagName = "smoke_phase_15_2_${blockName}"
    PsqlExec "DELETE FROM tags WHERE name = '$tagName'"
    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $calcDeviceId, '$tagName', 'float32', 3, 8000,
    true, 'Phase 15.2 smoke - safe to delete', -10000, 10000
)
"@
    $tagId = [int](Psql "SELECT id FROM tags WHERE name = '$tagName'")
    $script:CalcTagIds += $tagId

    $blockConfig = @{ inputs = $inputIds }
    if ($extraConfig) {
        foreach ($k in $extraConfig.Keys) { $blockConfig[$k] = $extraConfig[$k] }
    }

    $resp = Http -Method POST `
        -Url "http://localhost:8000/api/calc/definitions" `
        -Body @{
            tag_id = $tagId
            block_type = $blockType
            block_config = $blockConfig
            enabled = $true
            execution_rate_ms = $rate
        }
    if (-not $resp.ok) {
        Fail "${blockName}: POST failed: $($resp.status) $($resp.body)"
        return
    }

    DockerQuiet @("restart", "calc_evaluator")
    Start-Sleep -Seconds 4

    $latest = Psql @"
SELECT value_double FROM tag_values
WHERE tag_id = $tagId
ORDER BY time DESC LIMIT 1
"@
    if ([string]::IsNullOrEmpty($latest)) {
        Fail "${blockName}: no output value found"
        return
    }
    $actual = [double]$latest
    if ([Math]::Abs($actual - $expectedValue) -lt $tolerance) {
        Pass "${blockName}: $actual (expected $expectedValue, tol $tolerance)"
    } else {
        Fail "${blockName}: $actual, expected $expectedValue (tol $tolerance)"
    }
}

try {
    # ---- Section 0: Service health ----------------------------------------

    Section "0. Service health"

    foreach ($svc in @("backend", "calc_evaluator", "postgres")) {
        $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
        if ($psout -match '"State":\s*"running"' -or $psout -match '"Status":\s*"Up') {
            Pass "$svc is running"
        } else {
            Fail "$svc is not running"
        }
    }

    # ---- Section 1: Migration 0035 -----------------------------------------

    Section "1. Migration 0035"

    $rateCol = [int](Psql "SELECT count(*) FROM information_schema.columns WHERE table_name = 'calc_definitions' AND column_name = 'execution_rate_ms'")
    if ($rateCol -eq 1) { Pass "calc_definitions.execution_rate_ms exists" }
    else                { Fail "execution_rate_ms missing" }

    $statsTbl = [int](Psql "SELECT count(*) FROM information_schema.tables WHERE table_name = 'calc_execution_stats'")
    if ($statsTbl -eq 1) { Pass "calc_execution_stats table exists" }
    else                 { Fail "calc_execution_stats table missing" }

    $newBlockCount = [int](Psql "SELECT count(*) FROM calc_block_types WHERE code IN ('MEDIAN_OF','MODE_OF','RANGE_OF','STDDEV_OF','VARIANCE_OF','PRODUCT_OF','GEOMETRIC_MEAN','HARMONIC_MEAN','WEIGHTED_AVG','RMS_OF','COUNT_GOOD','COUNT_NONZERO')")
    if ($newBlockCount -eq 12) { Pass "12 new block taxonomy rows present" }
    else                       { Fail "Expected 12 new blocks, found $newBlockCount" }

    $flipped = [int](Psql "SELECT count(*) FROM calc_block_types WHERE code IN ('AVG_OF','MIN_OF','MAX_OF') AND is_evaluable = true")
    if ($flipped -eq 3) { Pass "AVG/MIN/MAX flipped to is_evaluable=true" }
    else                { Fail "Expected 3 flipped, got $flipped" }

    $ifThenRank = [int](Psql "SELECT rank FROM calc_block_types WHERE code = 'IF_THEN_ELSE'")
    if ($ifThenRank -eq 100) { Pass "IF_THEN_ELSE moved to rank 100" }
    else                     { Fail "IF_THEN_ELSE rank = $ifThenRank, expected 100" }

    # ---- Section 2: API exposes new blocks ---------------------------------

    Section "2. GET /api/calc/block-types"

    $btResp = Http -Method GET -Url "http://localhost:8000/api/calc/block-types"
    if ($btResp.ok -and $btResp.body.Count -ge 17) {
        Pass "Block types endpoint returns $($btResp.body.Count) rows"
    } else {
        Fail "Expected >= 17 block types, got $($btResp.body.Count)"
    }

    $evaluableCount = ($btResp.body | Where-Object { $_.is_evaluable -and $_.has_registry_entry }).Count
    if ($evaluableCount -ge 15) {
        Pass "$evaluableCount blocks are both is_evaluable AND have registry entry"
    } else {
        Fail "Expected >= 15 fully-implemented blocks, got $evaluableCount"
    }

    # ---- Section 3: Synthetic input tags -----------------------------------

    Section "3. Synthetic input tags"

    PsqlExec "DELETE FROM tags WHERE name LIKE 'smoke_phase_15_2_%'"

    $deviceId = [int](Psql "SELECT id FROM devices WHERE name != 'Calculations' ORDER BY id LIMIT 1")
    # Create 4 input tags
    foreach ($i in 1..4) {
        $name = "smoke_phase_15_2_input_$i"
        $addr = 99980 + $i
        PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $deviceId, '$name', 'float32', 3, $addr,
    false, 'Phase 15.2 input - safe to delete', -1000, 1000
)
"@
        $id = [int](Psql "SELECT id FROM tags WHERE name = '$name'")
        $script:InputTagIds += $id
    }
    Pass "4 input tags created"

    # Inject canonical test values: 9, 10, 11, 12
    # (chosen so STDDEV_OF([9,10,11]) = 1.0 exactly per NIST V5-STAT-01)
    InjectModbusValue -tagId $script:InputTagIds[0] -value 9.0
    InjectModbusValue -tagId $script:InputTagIds[1] -value 10.0
    InjectModbusValue -tagId $script:InputTagIds[2] -value 11.0
    InjectModbusValue -tagId $script:InputTagIds[3] -value 12.0
    Pass "Values injected: [9, 10, 11, 12]"

    # ---- Section 4: Execution rate validation ------------------------------

    Section "4. Execution rate validation"

    $calcDeviceId = [int](Psql "SELECT id FROM devices WHERE name = 'Calculations'")
    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $calcDeviceId, 'smoke_phase_15_2_bogus_rate', 'float32', 3, 9001,
    true, 'will be deleted', -1000, 1000
)
"@
    $bogusTagId = [int](Psql "SELECT id FROM tags WHERE name = 'smoke_phase_15_2_bogus_rate'")
    $script:CalcTagIds += $bogusTagId

    $badResp = Http -Method POST `
        -Url "http://localhost:8000/api/calc/definitions" `
        -Body @{
            tag_id = $bogusTagId
            block_type = "SUM_OF"
            block_config = @{ inputs = @($script:InputTagIds[0]) }
            execution_rate_ms = 777
        }

    if ($badResp.status -eq 400 -or $badResp.status -eq 422) {
        Pass "Bogus rate 777ms rejected with $($badResp.status)"
    } else {
        Fail "Bogus rate should be rejected, got $($badResp.status)"
    }

    # ---- Sections 5-18: per-block correctness ------------------------------
    # Inputs are [9, 10, 11, 12]; assertions use the first 3 or all 4.

    $i1 = $script:InputTagIds[0]
    $i2 = $script:InputTagIds[1]
    $i3 = $script:InputTagIds[2]
    $i4 = $script:InputTagIds[3]

    Section "5. AVG_OF([9, 10, 11]) = 10.0"
    TestBlock -blockName "avg" -blockType "AVG_OF" -inputIds @($i1, $i2, $i3) -expectedValue 10.0

    Section "6. MIN_OF([9, 10, 11, 12]) = 9.0"
    TestBlock -blockName "min" -blockType "MIN_OF" -inputIds @($i1, $i2, $i3, $i4) -expectedValue 9.0

    Section "7. MAX_OF([9, 10, 11, 12]) = 12.0"
    TestBlock -blockName "max" -blockType "MAX_OF" -inputIds @($i1, $i2, $i3, $i4) -expectedValue 12.0

    Section "8. MEDIAN_OF([9, 10, 11, 12]) = 10.5"
    TestBlock -blockName "median" -blockType "MEDIAN_OF" -inputIds @($i1, $i2, $i3, $i4) -expectedValue 10.5

    Section "9. RANGE_OF([9, 10, 11, 12]) = 3.0"
    TestBlock -blockName "range" -blockType "RANGE_OF" -inputIds @($i1, $i2, $i3, $i4) -expectedValue 3.0

    Section "10. STDDEV_OF([9, 10, 11]) = 1.0  (NIST V5-STAT-01)"
    TestBlock -blockName "stddev" -blockType "STDDEV_OF" -inputIds @($i1, $i2, $i3) -expectedValue 1.0

    Section "11. VARIANCE_OF([9, 10, 11]) = 1.0"
    TestBlock -blockName "variance" -blockType "VARIANCE_OF" -inputIds @($i1, $i2, $i3) -expectedValue 1.0

    Section "12. RMS_OF([9, 10, 11]) = sqrt(302/3) = 10.0333..."
    TestBlock -blockName "rms" -blockType "RMS_OF" -inputIds @($i1, $i2, $i3) -expectedValue 10.03328 -tolerance 0.001

    Section "13. PRODUCT_OF([9, 10, 11]) = 990.0"
    TestBlock -blockName "product" -blockType "PRODUCT_OF" -inputIds @($i1, $i2, $i3) -expectedValue 990.0

    Section "14. GEOMETRIC_MEAN([9, 10, 11]) = 9.9666..."
    TestBlock -blockName "geomean" -blockType "GEOMETRIC_MEAN" -inputIds @($i1, $i2, $i3) -expectedValue 9.96655 -tolerance 0.001

    Section "15. HARMONIC_MEAN([9, 10, 11]) = 9.9325..."
    TestBlock -blockName "harmonic" -blockType "HARMONIC_MEAN" -inputIds @($i1, $i2, $i3) -expectedValue 9.93250 -tolerance 0.001

    Section "16. WEIGHTED_AVG([10, 20, 30] w=[1, 2, 3]) = 23.333..."
    # 10*1 + 20*2 + 30*3 = 140; 1+2+3 = 6; 140/6 = 23.333
    # Need to inject different values to test weighting properly
    InjectModbusValue -tagId $i1 -value 10.0
    InjectModbusValue -tagId $i2 -value 20.0
    InjectModbusValue -tagId $i3 -value 30.0
    TestBlock -blockName "weighted" -blockType "WEIGHTED_AVG" `
        -inputIds @($i1, $i2, $i3) `
        -extraConfig @{ weights = @(1, 2, 3) } `
        -expectedValue 23.3333 -tolerance 0.01
    # Restore original test values
    InjectModbusValue -tagId $i1 -value 9.0
    InjectModbusValue -tagId $i2 -value 10.0
    InjectModbusValue -tagId $i3 -value 11.0

    Section "17. COUNT_GOOD: 3 GOOD inputs out of 3"
    TestBlock -blockName "countgood" -blockType "COUNT_GOOD" -inputIds @($i1, $i2, $i3) -expectedValue 3.0

    Section "18. COUNT_NONZERO: 3 nonzero inputs out of 3"
    TestBlock -blockName "countnz" -blockType "COUNT_NONZERO" -inputIds @($i1, $i2, $i3) -expectedValue 3.0

    Section "18b. MODE_OF with repeated value"
    # Inject value 7 into multiple tags to make 7 the mode
    InjectModbusValue -tagId $i1 -value 7.0
    InjectModbusValue -tagId $i2 -value 7.0
    InjectModbusValue -tagId $i3 -value 9.0
    InjectModbusValue -tagId $i4 -value 5.0
    TestBlock -blockName "mode" -blockType "MODE_OF" -inputIds @($i1, $i2, $i3, $i4) -expectedValue 7.0

    # ---- Section 19: multi-rate scheduling ---------------------------------

    Section "19. Multi-rate scheduling: 1s block executes ~6x in 6s"

    # Restore canonical values
    InjectModbusValue -tagId $i1 -value 9.0
    InjectModbusValue -tagId $i2 -value 10.0
    InjectModbusValue -tagId $i3 -value 11.0

    PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $calcDeviceId, 'smoke_phase_15_2_rate_1s', 'float32', 3, 9100,
    true, 'phase 15.2 rate test', -1000, 1000
)
"@
    $rateTagId = [int](Psql "SELECT id FROM tags WHERE name = 'smoke_phase_15_2_rate_1s'")
    $script:CalcTagIds += $rateTagId

    $rateResp = Http -Method POST `
        -Url "http://localhost:8000/api/calc/definitions" `
        -Body @{
            tag_id = $rateTagId
            block_type = "SUM_OF"
            block_config = @{ inputs = @($i1, $i2, $i3) }
            execution_rate_ms = 1000
        }
    if (-not $rateResp.ok) {
        Fail "Rate test calc creation failed: $($rateResp.status)"
    } else {
        $rateDefId = [int]$rateResp.body.id
        Pass "1s-rate calc created (def_id=$rateDefId)"

        DockerQuiet @("restart", "calc_evaluator")
        Start-Sleep -Seconds 8  # Wait for ~6-7 ticks of a 1s block

        $statsResp = Http -Method GET -Url "http://localhost:8000/api/calc/definitions/$rateDefId/stats"
        if (-not $statsResp.ok) {
            Fail "Stats endpoint failed: $($statsResp.status)"
        } else {
            $totalExec = [int]$statsResp.body.total_executions
            if ($totalExec -ge 5 -and $totalExec -le 10) {
                Pass "1s-rate executed $totalExec times in ~8s (expected 5-10)"
            } else {
                Fail "1s-rate executed $totalExec times in 8s, expected 5-10"
            }
            if ($statsResp.body.last_status -eq 'ok' -or $statsResp.body.last_status -eq 'overrun') {
                Pass "last_status = $($statsResp.body.last_status)"
            } else {
                Fail "Unexpected last_status: $($statsResp.body.last_status)"
            }
            if ($statsResp.body.last_duration_ms -ge 0) {
                Pass "last_duration_ms = $($statsResp.body.last_duration_ms) ms"
            } else {
                Fail "last_duration_ms missing"
            }
        }
    }

    # ---- Section 20: list endpoint includes stats --------------------------

    Section "20. List endpoint includes joined stats"

    $listResp = Http -Method GET -Url "http://localhost:8000/api/calc/definitions"
    if ($listResp.ok -and $listResp.body.Count -ge 1) {
        $sample = $listResp.body | Where-Object { $_.tag_id -eq $rateTagId } | Select-Object -First 1
        if ($sample -and $sample.total_executions -gt 0) {
            Pass "List response includes total_executions=$($sample.total_executions) for rate test"
        } else {
            Fail "List response missing or zero execution stats"
        }
    } else {
        Fail "List endpoint failed"
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
    Write-Host "  Deleted synthetic tags (cascade removed calc_defs + stats)"
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
    Write-Host "  docker compose exec -T postgres psql -U induvista_admin -d induvista -c 'SELECT * FROM calc_execution_stats'"
    exit 1
}

Write-Host ""
Write-Host "Phase 15.2 aggregation tier + execution rate scheduling verified." -ForegroundColor Cyan
exit 0
