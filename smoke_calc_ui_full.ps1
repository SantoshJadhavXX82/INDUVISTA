# =============================================================================
# InduVista - Full Calculation UI Smoke Test (Phase 16.0c)
#
# Exercises every backend API the calc-blocks UI relies on, validates the
# worker actually computes correctly, and verifies the data flow:
#
#   1.  Block schemas + types endpoints (UI catalog)
#   2.  Output tag creation (/api/calc/output-tags) - valid + error paths
#   3.  Binary ADD calc creation + worker tick + value validation
#   4.  N-ary ADD with mixed tags and constants
#   5.  Edit (PUT) - change execution_rate_ms
#   6.  Toggle enabled
#   7.  Delete
#   8.  Worker writes to BOTH tag_values (hypertable) AND latest_tag_values
#   9.  /api/calc/current-values returns calc outputs
#   10. Cleanup of all created data
#
# Idempotent: all created entities are named TEST_SMOKE_* so re-runs can clean
# them up first. Run with -CleanupOnly to just remove leftovers from a prior
# failed run.
#
# Usage:
#   .\smoke_calc_ui_full.ps1
#   .\smoke_calc_ui_full.ps1 -CleanupOnly
# =============================================================================

param(
    [switch]$CleanupOnly,
    [string]$BackendUrl = "http://localhost:8000"
)

$ErrorActionPreference = 'Continue'

$script:tests = @()
$script:created_calc_ids = @()
$script:created_tag_ids = @()

$BASE = $BackendUrl

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

function Pass($name, $detail = "") {
    $script:tests += [pscustomobject]@{ N = $script:tests.Count+1; Name = $name; Status = "PASS"; Detail = $detail }
    Write-Host ("  [{0,2}] {1,-58} " -f $script:tests.Count, $name) -NoNewline
    Write-Host "PASS" -ForegroundColor Green
}

function Fail($name, $detail) {
    $script:tests += [pscustomobject]@{ N = $script:tests.Count+1; Name = $name; Status = "FAIL"; Detail = $detail }
    Write-Host ("  [{0,2}] {1,-58} " -f $script:tests.Count, $name) -NoNewline
    Write-Host "FAIL" -ForegroundColor Red
    if ($detail) {
        $short = if ($detail.Length -gt 160) { $detail.Substring(0,160) + "..." } else { $detail }
        Write-Host "       $short" -ForegroundColor DarkGray
    }
}

function Skip($name, $reason) {
    $script:tests += [pscustomobject]@{ N = $script:tests.Count+1; Name = $name; Status = "SKIP"; Detail = $reason }
    Write-Host ("  [{0,2}] {1,-58} " -f $script:tests.Count, $name) -NoNewline
    Write-Host "SKIP" -ForegroundColor Yellow
    if ($reason) {
        Write-Host "       $reason" -ForegroundColor DarkGray
    }
}

function Section($title) {
    Write-Host ""
    Write-Host "----- $title -----" -ForegroundColor Cyan
}

function Get-ErrorBody($err) {
    # Pull body out of an HttpResponseException for clear failure messages.
    try {
        if ($err.Exception.Response) {
            $stream = $err.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $body = $reader.ReadToEnd()
            return "HTTP $([int]$err.Exception.Response.StatusCode): $body"
        }
    } catch {}
    return $err.Exception.Message
}

function Try-Rest {
    param([string]$method, [string]$path, $body = $null, [int[]]$expectStatus = @(200, 201, 204))
    $url = "$BASE$path"
    $params = @{
        Uri = $url
        Method = $method
        ContentType = "application/json"
        TimeoutSec = 15
    }
    if ($body -ne $null) {
        $params.Body = ($body | ConvertTo-Json -Depth 20 -Compress)
    }
    try {
        return Invoke-RestMethod @params
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        if ($expectStatus -contains $code) {
            return $null   # Caller knew this would fail; not really an error
        }
        throw (Get-ErrorBody $_)
    }
}

function Try-Rest-Expect-Error {
    param([string]$method, [string]$path, $body, [int]$expectStatus)
    $url = "$BASE$path"
    $params = @{
        Uri = $url
        Method = $method
        ContentType = "application/json"
        TimeoutSec = 15
        Body = ($body | ConvertTo-Json -Depth 20 -Compress)
    }
    try {
        $null = Invoke-RestMethod @params
        return @{ ok = $false; detail = "Expected HTTP $expectStatus but request succeeded" }
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        if ($code -eq $expectStatus) {
            return @{ ok = $true; detail = "" }
        }
        return @{ ok = $false; detail = "Expected HTTP $expectStatus got $code; body=$(Get-ErrorBody $_)" }
    }
}

function Query-DB($sql) {
    # Returns rows as pipe-separated strings (psql -tA mode).
    $output = docker compose exec -T postgres psql -U induvista_admin -d induvista -tA -c $sql 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "psql failed: $output"
    }
    return @($output | Where-Object { $_ -ne "" })
}

function Wait-For-Tick {
    param([int]$calcId, [int]$timeoutSec = 10)
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $defs = Invoke-RestMethod -Uri "$BASE/api/calc/definitions" -TimeoutSec 5
            $d = $defs | Where-Object { $_.id -eq $calcId }
            if ($d -and $d.total_executions -gt 0) { return $d }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    return $null
}


# ----------------------------------------------------------------------------
# Cleanup utility - delete any leftover TEST_SMOKE_* entities
# ----------------------------------------------------------------------------
function Cleanup-Leftovers {
    Write-Host "Cleaning up leftover TEST_SMOKE_* entities..." -ForegroundColor DarkGray

    # Delete calcs whose output tag name starts with TEST_SMOKE_
    try {
        $defs = Invoke-RestMethod -Uri "$BASE/api/calc/definitions" -TimeoutSec 10
        foreach ($d in $defs) {
            if ($d.tag_name -and $d.tag_name -like "TEST_SMOKE_*") {
                try {
                    Invoke-RestMethod -Uri "$BASE/api/calc/definitions/$($d.id)" -Method DELETE -TimeoutSec 5 | Out-Null
                    Write-Host "  removed calc #$($d.id) (tag=$($d.tag_name))" -ForegroundColor DarkGray
                } catch {
                    Write-Host "  failed to remove calc #$($d.id): $($_.Exception.Message)" -ForegroundColor DarkGray
                }
            }
        }
    } catch {
        Write-Host "  could not list calcs: $($_.Exception.Message)" -ForegroundColor DarkGray
    }

    # Delete tags named TEST_SMOKE_* directly (no DELETE endpoint for output-tags;
    # use raw SQL).
    try {
        $rows = Query-DB "SELECT id FROM tags WHERE name LIKE 'TEST_SMOKE_%';"
        if ($rows.Count -gt 0) {
            $ids = ($rows -join ",")
            $cleanup_sql = "DELETE FROM tag_values WHERE tag_id IN ($ids);" +
                           "DELETE FROM latest_tag_values WHERE tag_id IN ($ids);" +
                           "DELETE FROM tags WHERE id IN ($ids);"
            $null = Query-DB $cleanup_sql
            Write-Host "  removed $($rows.Count) leftover tag(s)" -ForegroundColor DarkGray
        }
    } catch {
        Write-Host "  cleanup SQL failed: $($_.Exception.Message)" -ForegroundColor DarkGray
    }
}


# ----------------------------------------------------------------------------
# 0. Pre-flight
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host " InduVista - Calculation UI Smoke Test" -ForegroundColor Cyan
Write-Host " Backend: $BASE" -ForegroundColor DarkGray
Write-Host " Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host "===========================================================" -ForegroundColor Cyan

Cleanup-Leftovers

if ($CleanupOnly) {
    Write-Host "`nCleanup-only mode; exiting." -ForegroundColor Yellow
    exit 0
}


# ----------------------------------------------------------------------------
# Section 1: Block schemas + types endpoints
# ----------------------------------------------------------------------------

Section "1. Block schemas + types catalog (UI initial load)"

try {
    $schemas = Invoke-RestMethod -Uri "$BASE/api/calc/block-schemas" -TimeoutSec 10
    $schemaKeys = @($schemas.PSObject.Properties.Name)
    if ($schemaKeys.Count -ge 60) {
        Pass "block-schemas endpoint returns >= 60 schemas" "count=$($schemaKeys.Count)"
    } else {
        Fail "block-schemas endpoint returns >= 60 schemas" "got $($schemaKeys.Count)"
    }
} catch {
    Fail "block-schemas endpoint reachable" $_.Exception.Message
    $schemas = $null
}

if ($schemas) {
    # ADD's schema should now be a mode_select (Phase 16.0b)
    $addSchema = $schemas.ADD
    if ($addSchema -and $addSchema.fields -and $addSchema.fields[0].type -eq "mode_select") {
        Pass "ADD schema uses mode_select" ""
    } else {
        Fail "ADD schema uses mode_select" "ADD.fields[0].type=$($addSchema.fields[0].type)"
    }

    # ADD's mode_select should have binary + n_ary modes
    $modes = @($addSchema.fields[0].options.value)
    if ($modes -contains "binary" -and $modes -contains "n_ary") {
        Pass "ADD has binary + n_ary modes" "modes=$($modes -join ',')"
    } else {
        Fail "ADD has binary + n_ary modes" "modes=$($modes -join ',')"
    }

    # MUL also has mode_select
    $mulSchema = $schemas.MUL
    if ($mulSchema -and $mulSchema.fields[0].type -eq "mode_select") {
        Pass "MUL schema uses mode_select" ""
    } else {
        Fail "MUL schema uses mode_select" ""
    }

    # SUB stays binary-only
    $subSchema = $schemas.SUB
    if ($subSchema -and $subSchema.fields[0].type -eq "tag_ref") {
        Pass "SUB stays binary-only (tag_ref + tag_or_constant)" ""
    } else {
        Fail "SUB stays binary-only" "SUB.fields[0].type=$($subSchema.fields[0].type)"
    }

    # N-ary mode inputs should be tag_or_constant_list
    $naryMode = $addSchema.fields[0].options | Where-Object { $_.value -eq "n_ary" }
    if ($naryMode.fields[0].type -eq "tag_or_constant_list") {
        Pass "ADD N-ary mode uses tag_or_constant_list" ""
    } else {
        Fail "ADD N-ary mode uses tag_or_constant_list" "type=$($naryMode.fields[0].type)"
    }
}

try {
    $types = Invoke-RestMethod -Uri "$BASE/api/calc/block-types" -TimeoutSec 10
    if ($types.Count -ge 60) {
        Pass "block-types catalog returns >= 60 types" "count=$($types.Count)"
    } else {
        Fail "block-types catalog returns >= 60 types" "got $($types.Count)"
    }
} catch {
    Fail "block-types endpoint reachable" $_.Exception.Message
}


# ----------------------------------------------------------------------------
# Section 2: Discover environment (suitable input tags)
# ----------------------------------------------------------------------------

Section "2. Environment discovery"

try {
    $rows = Query-DB @"
SELECT id, name, data_type FROM tags
WHERE data_type IN ('float32', 'float64', 'int16', 'uint16', 'int32', 'uint32', 'bool')
  AND enabled = true
  AND device_id != (SELECT id FROM devices WHERE protocol = 'manual' LIMIT 1)
ORDER BY id
LIMIT 5;
"@
    if ($rows.Count -ge 1) {
        $script:input_tags = @($rows | ForEach-Object {
            $parts = $_ -split '\|'
            [pscustomobject]@{ id = [int]$parts[0]; name = $parts[1]; data_type = $parts[2] }
        })
        Pass "Discover >=1 input tag for calc inputs" "found $($script:input_tags.Count): $(($script:input_tags | ForEach-Object { "#$($_.id) $($_.name)" }) -join ', ')"
    } else {
        Fail "Discover >=1 input tag for calc inputs" "no enabled non-manual numeric/bool tags"
        $script:input_tags = @()
    }
} catch {
    Fail "Discover input tags via psql" $_.Exception.Message
    $script:input_tags = @()
}

if ($script:input_tags.Count -lt 1) {
    Write-Host "`nNo input tags available; aborting." -ForegroundColor Red
    exit 1
}

$input_tag_1 = $script:input_tags[0].id
$input_tag_2 = if ($script:input_tags.Count -ge 2) { $script:input_tags[1].id } else { $input_tag_1 }


# ----------------------------------------------------------------------------
# Section 3: Output tag creation
# ----------------------------------------------------------------------------

Section "3. Output tag creation (POST /api/calc/output-tags)"

# 3a. Valid creation
try {
    $body = @{ name = "TEST_SMOKE_OUT_BINARY"; data_type = "float64"; description = "smoke" }
    $tag = Try-Rest "POST" "/api/calc/output-tags" $body
    if ($tag -and $tag.id) {
        $script:created_tag_ids += $tag.id
        $tag_binary = $tag.id
        Pass "Create output tag (float64)" "id=$($tag.id), name=$($tag.name)"
    } else {
        Fail "Create output tag (float64)" "no id returned"
    }
} catch {
    Fail "Create output tag (float64)" $_.Exception.Message
}

# 3b. Second tag for N-ary
try {
    $body = @{ name = "TEST_SMOKE_OUT_NARY"; data_type = "float64" }
    $tag = Try-Rest "POST" "/api/calc/output-tags" $body
    $script:created_tag_ids += $tag.id
    $tag_nary = $tag.id
    Pass "Create output tag (N-ary)" "id=$($tag.id)"
} catch {
    Fail "Create output tag (N-ary)" $_.Exception.Message
}

# 3c. Invalid data_type rejected
$r = Try-Rest-Expect-Error "POST" "/api/calc/output-tags" @{ name = "TEST_SMOKE_BAD"; data_type = "double" } 422
if ($r.ok) {
    Pass "Reject 'double' (use float64 instead)" ""
} else {
    Fail "Reject 'double' (use float64 instead)" $r.detail
}

# 3d. Duplicate name rejected
$r = Try-Rest-Expect-Error "POST" "/api/calc/output-tags" @{ name = "TEST_SMOKE_OUT_BINARY"; data_type = "float64" } 409
if ($r.ok) {
    Pass "Reject duplicate tag name (409)" ""
} else {
    Fail "Reject duplicate tag name (409)" $r.detail
}


# ----------------------------------------------------------------------------
# Section 4: Binary ADD - create, tick, verify value
# ----------------------------------------------------------------------------

Section "4. Binary ADD calc (tag + constant) + worker integration"

if (-not $tag_binary) {
    Skip "Skipping section 4" "no output tag from section 3"
} else {
    $constant = 100.0
    $body = @{
        tag_id = $tag_binary
        block_type = "ADD"
        block_config = @{ left = $input_tag_1; value = $constant }
        execution_rate_ms = 1000
        enabled = $true
    }
    try {
        $calc = Try-Rest "POST" "/api/calc/definitions" $body
        $script:created_calc_ids += $calc.id
        $calc_binary = $calc
        Pass "Create binary ADD calc" "id=$($calc.id) (tag #$input_tag_1 + $constant)"
    } catch {
        Fail "Create binary ADD calc" $_.Exception.Message
    }

    if ($calc_binary) {
        # Wait for worker to tick
        $defAfter = Wait-For-Tick -calcId $calc_binary.id -timeoutSec 10
        if ($defAfter) {
            Pass "Worker ticked binary ADD" "executions=$($defAfter.total_executions), errors=$($defAfter.total_errors)"
        } else {
            Fail "Worker ticked binary ADD" "no executions within 10s"
        }

        # Verify it wrote to tag_values
        try {
            $rows = Query-DB "SELECT COUNT(*), MAX(value_double) FROM tag_values WHERE tag_id = $tag_binary AND source = 'estimated';"
            $parts = ($rows[0] -split '\|')
            if ([int]$parts[0] -gt 0) {
                Pass "tag_values hypertable has row(s)" "count=$($parts[0]), latest_value=$($parts[1])"
            } else {
                Fail "tag_values hypertable has row(s)" "no rows for tag #$tag_binary"
            }
        } catch {
            Fail "tag_values hypertable has row(s)" $_.Exception.Message
        }

        # Verify it wrote to latest_tag_values (Phase 16.0c fix)
        try {
            $rows = Query-DB "SELECT value_double, st FROM latest_tag_values WHERE tag_id = $tag_binary;"
            if ($rows.Count -ge 1) {
                $parts = ($rows[0] -split '\|')
                Pass "latest_tag_values snapshot has row" "value=$($parts[0]), st=$($parts[1])"
            } else {
                Fail "latest_tag_values snapshot has row" "no row for tag #$tag_binary (worker UPSERT may be broken)"
            }
        } catch {
            Fail "latest_tag_values snapshot has row" $_.Exception.Message
        }

        # Verify the computed value: should equal (input_value + constant)
        try {
            $rows = Query-DB "SELECT value_double FROM latest_tag_values WHERE tag_id = $input_tag_1;"
            if ($rows.Count -ge 1) {
                $inputValue = [double]$rows[0]
                $rows = Query-DB "SELECT value_double FROM latest_tag_values WHERE tag_id = $tag_binary;"
                if ($rows.Count -ge 1 -and $rows[0] -ne "") {
                    $outputValue = [double]$rows[0]
                    $expected = $inputValue + $constant
                    if ([Math]::Abs($outputValue - $expected) -lt 0.001) {
                        Pass "Binary ADD computed value matches expected" "output=$outputValue, expected=$expected"
                    } else {
                        Fail "Binary ADD computed value matches expected" "output=$outputValue, expected=$expected (input=$inputValue)"
                    }
                } else {
                    Skip "Binary ADD computed value matches expected" "output value null (input may have been BAD quality)"
                }
            } else {
                Skip "Binary ADD computed value matches expected" "input tag has no value in latest_tag_values"
            }
        } catch {
            Fail "Binary ADD computed value matches expected" $_.Exception.Message
        }
    }
}


# ----------------------------------------------------------------------------
# Section 5: N-ary ADD with mixed tags + constants
# ----------------------------------------------------------------------------

Section "5. N-ary ADD with mixed tags + constants (Phase 16.0b)"

if (-not $tag_nary) {
    Skip "Skipping section 5" "no output tag from section 3"
} else {
    $const_a = 10.5
    $const_b = -2.5
    $body = @{
        tag_id = $tag_nary
        block_type = "ADD"
        block_config = @{
            inputs = @(
                @{ tag = $input_tag_1 },
                @{ value = $const_a },
                @{ tag = $input_tag_2 },
                @{ value = $const_b }
            )
        }
        execution_rate_ms = 1000
        enabled = $true
    }
    try {
        $calc = Try-Rest "POST" "/api/calc/definitions" $body
        $script:created_calc_ids += $calc.id
        $calc_nary = $calc
        Pass "Create N-ary ADD calc (2 tags + 2 constants)" "id=$($calc.id)"
    } catch {
        Fail "Create N-ary ADD calc (2 tags + 2 constants)" $_.Exception.Message
    }

    if ($calc_nary) {
        $defAfter = Wait-For-Tick -calcId $calc_nary.id -timeoutSec 10
        if ($defAfter) {
            Pass "Worker ticked N-ary ADD" "executions=$($defAfter.total_executions), errors=$($defAfter.total_errors)"
        } else {
            Fail "Worker ticked N-ary ADD" "no executions within 10s"
        }

        # Validate computed value
        try {
            $rows = Query-DB "SELECT value_double FROM latest_tag_values WHERE tag_id IN ($input_tag_1, $input_tag_2, $tag_nary) ORDER BY tag_id;"
            if ($rows.Count -eq 3 -or ($rows.Count -eq 2 -and $input_tag_1 -eq $input_tag_2)) {
                $values = @($rows | ForEach-Object { if ($_ -eq "") { $null } else { [double]$_ } })
                $expected = if ($input_tag_1 -eq $input_tag_2) {
                    # Duplicate tag - backend should have rejected this. Skip.
                    $null
                } else {
                    $values[0] + $const_a + $values[1] + $const_b
                }
                $outputValue = $values[-1]
                if ($expected -ne $null -and $outputValue -ne $null -and [Math]::Abs($outputValue - $expected) -lt 0.001) {
                    Pass "N-ary ADD computed value matches expected" "output=$outputValue, expected=$expected"
                } elseif ($expected -eq $null) {
                    Skip "N-ary ADD computed value matches expected" "only 1 unique input tag available"
                } else {
                    Fail "N-ary ADD computed value matches expected" "output=$outputValue, expected=$expected, values=$($values -join ',')"
                }
            } else {
                Skip "N-ary ADD computed value matches expected" "couldn't read all 3 values from latest_tag_values"
            }
        } catch {
            Fail "N-ary ADD computed value matches expected" $_.Exception.Message
        }
    }
}

# 5b. Reject mixing N-ary 'inputs' with binary 'left'/'right'/'value'
$r = Try-Rest-Expect-Error "POST" "/api/calc/definitions" @{
    tag_id = $tag_nary
    block_type = "ADD"
    block_config = @{
        inputs = @(@{tag=$input_tag_1}, @{value=5})
        left = $input_tag_1
    }
    execution_rate_ms = 1000
    enabled = $false
} 400
# Some backends return 422 instead; accept both
if (-not $r.ok) {
    $r = Try-Rest-Expect-Error "POST" "/api/calc/definitions" @{
        tag_id = $tag_nary
        block_type = "ADD"
        block_config = @{
            inputs = @(@{tag=$input_tag_1}, @{value=5})
            left = $input_tag_1
        }
        execution_rate_ms = 1000
        enabled = $false
    } 422
}
if ($r.ok) {
    Pass "Reject mixed N-ary + binary config shape" ""
} else {
    Fail "Reject mixed N-ary + binary config shape" $r.detail
}

# 5c. Reject duplicate tag IDs in N-ary inputs
$r = Try-Rest-Expect-Error "POST" "/api/calc/definitions" @{
    tag_id = $tag_nary
    block_type = "ADD"
    block_config = @{ inputs = @(@{tag=$input_tag_1}, @{tag=$input_tag_1}) }
    execution_rate_ms = 1000
    enabled = $false
} 400
if (-not $r.ok) {
    $r = Try-Rest-Expect-Error "POST" "/api/calc/definitions" @{
        tag_id = $tag_nary
        block_type = "ADD"
        block_config = @{ inputs = @(@{tag=$input_tag_1}, @{tag=$input_tag_1}) }
        execution_rate_ms = 1000
        enabled = $false
    } 422
}
if ($r.ok) {
    Pass "Reject duplicate tag IDs in N-ary inputs" ""
} else {
    Fail "Reject duplicate tag IDs in N-ary inputs" $r.detail
}


# ----------------------------------------------------------------------------
# Section 6: Edit (PUT) - change execution_rate_ms
# ----------------------------------------------------------------------------

Section "6. Edit calc via PUT (used by edit modal)"

if (-not $calc_binary) {
    Skip "Skipping section 6" "no calc from section 4"
} else {
    $body = @{
        tag_id = $calc_binary.tag_id
        block_type = $calc_binary.block_type
        block_config = $calc_binary.block_config
        execution_rate_ms = 5000   # was 1000
        enabled = $true
    }
    try {
        $updated = Try-Rest "PUT" "/api/calc/definitions/$($calc_binary.id)" $body
        if ($updated.execution_rate_ms -eq 5000) {
            Pass "PUT updates execution_rate_ms" "1000 -> 5000"
        } else {
            Fail "PUT updates execution_rate_ms" "got rate=$($updated.execution_rate_ms)"
        }
    } catch {
        Fail "PUT updates execution_rate_ms" $_.Exception.Message
    }
}


# ----------------------------------------------------------------------------
# Section 7: Toggle enabled (uses PUT)
# ----------------------------------------------------------------------------

Section "7. Toggle enabled (used by power button in UI)"

if (-not $calc_binary) {
    Skip "Skipping section 7" "no calc from section 4"
} else {
    $body = @{
        tag_id = $calc_binary.tag_id
        block_type = $calc_binary.block_type
        block_config = $calc_binary.block_config
        execution_rate_ms = 5000
        enabled = $false   # was true
    }
    try {
        $updated = Try-Rest "PUT" "/api/calc/definitions/$($calc_binary.id)" $body
        if ($updated.enabled -eq $false) {
            Pass "PUT toggles enabled true -> false" ""
        } else {
            Fail "PUT toggles enabled true -> false" "got enabled=$($updated.enabled)"
        }
    } catch {
        Fail "PUT toggles enabled true -> false" $_.Exception.Message
    }

    # Toggle back
    $body.enabled = $true
    try {
        $updated = Try-Rest "PUT" "/api/calc/definitions/$($calc_binary.id)" $body
        if ($updated.enabled -eq $true) {
            Pass "PUT toggles enabled false -> true" ""
        } else {
            Fail "PUT toggles enabled false -> true" "got enabled=$($updated.enabled)"
        }
    } catch {
        Fail "PUT toggles enabled false -> true" $_.Exception.Message
    }
}


# ----------------------------------------------------------------------------
# Section 8: /api/calc/current-values endpoint
# ----------------------------------------------------------------------------

Section "8. Current values endpoint (used by value column in UI)"

try {
    $cv = Invoke-RestMethod -Uri "$BASE/api/calc/current-values" -TimeoutSec 10
    if ($cv.values) {
        $keyCount = @($cv.values.PSObject.Properties.Name).Count
        Pass "current-values endpoint returns values" "$keyCount tags, source=$($cv._source)"
    } else {
        Fail "current-values endpoint returns values" "empty: $($cv._note)"
    }

    if ($tag_binary -and $cv.values."$tag_binary") {
        $entry = $cv.values."$tag_binary"
        Pass "Binary calc output appears in current-values" "value=$($entry.value), quality=$($entry.quality)"
    } elseif ($tag_binary) {
        Fail "Binary calc output appears in current-values" "tag #$tag_binary missing"
    }

    if ($tag_nary -and $cv.values."$tag_nary") {
        $entry = $cv.values."$tag_nary"
        Pass "N-ary calc output appears in current-values" "value=$($entry.value), quality=$($entry.quality)"
    } elseif ($tag_nary) {
        Fail "N-ary calc output appears in current-values" "tag #$tag_nary missing"
    }
} catch {
    Fail "current-values endpoint reachable" $_.Exception.Message
}


# ----------------------------------------------------------------------------
# Section 9: Delete (used by trash button)
# ----------------------------------------------------------------------------

Section "9. Delete calc via DELETE (used by trash button)"

if ($calc_binary) {
    try {
        $null = Invoke-RestMethod -Uri "$BASE/api/calc/definitions/$($calc_binary.id)" -Method DELETE -TimeoutSec 10
        # Confirm it's gone
        $defs = Invoke-RestMethod -Uri "$BASE/api/calc/definitions" -TimeoutSec 10
        $found = $defs | Where-Object { $_.id -eq $calc_binary.id }
        if (-not $found) {
            Pass "DELETE removes calc definition" "id=$($calc_binary.id)"
            $script:created_calc_ids = $script:created_calc_ids | Where-Object { $_ -ne $calc_binary.id }
        } else {
            Fail "DELETE removes calc definition" "calc id=$($calc_binary.id) still listed"
        }
    } catch {
        Fail "DELETE removes calc definition" $_.Exception.Message
    }
}


# ----------------------------------------------------------------------------
# Cleanup
# ----------------------------------------------------------------------------

Section "10. Cleanup"

foreach ($calcId in $script:created_calc_ids) {
    try {
        $null = Invoke-RestMethod -Uri "$BASE/api/calc/definitions/$calcId" -Method DELETE -TimeoutSec 5
    } catch {}
}
Write-Host "  removed $($script:created_calc_ids.Count) calc(s)" -ForegroundColor DarkGray

if ($script:created_tag_ids.Count -gt 0) {
    try {
        $ids = ($script:created_tag_ids -join ",")
        $null = Query-DB "DELETE FROM tag_values WHERE tag_id IN ($ids);
                          DELETE FROM latest_tag_values WHERE tag_id IN ($ids);
                          DELETE FROM tags WHERE id IN ($ids);"
        Write-Host "  removed $($script:created_tag_ids.Count) tag(s)" -ForegroundColor DarkGray
    } catch {
        Write-Host "  cleanup failed: $($_.Exception.Message)" -ForegroundColor DarkGray
    }
}


# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

$pass = ($script:tests | Where-Object { $_.Status -eq "PASS" }).Count
$fail = ($script:tests | Where-Object { $_.Status -eq "FAIL" }).Count
$skip = ($script:tests | Where-Object { $_.Status -eq "SKIP" }).Count
$total = $script:tests.Count

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host " SUMMARY" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ("  PASS:  {0,3} / {1}" -f $pass, $total) -ForegroundColor Green
if ($fail -gt 0) {
    Write-Host ("  FAIL:  {0,3} / {1}" -f $fail, $total) -ForegroundColor Red
}
if ($skip -gt 0) {
    Write-Host ("  SKIP:  {0,3} / {1}" -f $skip, $total) -ForegroundColor Yellow
}
Write-Host ""

if ($fail -gt 0) {
    Write-Host "Failed tests:" -ForegroundColor Red
    $script:tests | Where-Object { $_.Status -eq "FAIL" } | ForEach-Object {
        Write-Host ("  [{0,2}] {1}" -f $_.N, $_.Name) -ForegroundColor Red
        if ($_.Detail) {
            $short = if ($_.Detail.Length -gt 200) { $_.Detail.Substring(0,200) + "..." } else { $_.Detail }
            Write-Host "       $short" -ForegroundColor DarkGray
        }
    }
    Write-Host ""
    exit 1
} else {
    Write-Host "All tests passed." -ForegroundColor Green
    Write-Host ""
    exit 0
}
