# =============================================================================
# Phase 16.0f - Rigorous Smoke v2.1 (backend + audit)
#
# v2.1: rebuilt against actual API shapes after probe diagnostic.
#   - block-types uses 'code' field, not 'block_type'
#   - Categories are lowercase
#   - block_config.inputs uses {tag: N} or {value: V} (no 'kind', no 'mode')
#   - 2xx accepted as success (not strict 200)
#   - prereq null-guards prevent cascade failures
#
# Run: .\smoke_calc_ui_full_v2.ps1
# Exit code: 0 if all pass, 1 if any fail
# =============================================================================

$ErrorActionPreference = 'Continue'
$BASE   = "http://localhost:8000"
$STAMP  = Get-Date -Format yyyyMMddHHmmss
$PREFIX = "SMOKE_${STAMP}_"

$script:pass     = 0
$script:fail     = 0
$script:failures = @()
$script:section  = ""

$startTime = Get-Date


# ===========================================================================
# Helpers
# ===========================================================================

function Section {
    param([string]$Name)
    $script:section = $Name
    Write-Host ""
    Write-Host "[ $Name ]" -ForegroundColor Cyan
}

function T {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body | Out-Null
        $script:pass++
        Write-Host "  [PASS] $Name" -ForegroundColor Green
    } catch {
        $script:fail++
        $script:failures += [pscustomobject]@{
            section = $script:section
            name    = $Name
            error   = $_.Exception.Message
        }
        Write-Host "  [FAIL] $Name" -ForegroundColor Red
        Write-Host "         $($_.Exception.Message)" -ForegroundColor DarkRed
    }
}

function Invoke-Api {
    param(
        [Parameter(Mandatory=$true)][string]$Method,
        [Parameter(Mandatory=$true)][string]$Path,
        $Body = $null
    )
    $params = @{
        Uri             = "$BASE$Path"
        Method          = $Method
        ContentType     = "application/json"
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 10 -Compress)
    }
    try {
        $resp = Invoke-WebRequest @params
        $parsed = $null
        if ($resp.Content) {
            try { $parsed = $resp.Content | ConvertFrom-Json } catch { $parsed = $resp.Content }
        }
        return [pscustomobject]@{
            Status = [int]$resp.StatusCode
            Body   = $parsed
        }
    } catch [System.Net.WebException] {
        $exc = $_.Exception
        $status = 0
        $body = $null
        if ($exc.Response) {
            $status = [int]$exc.Response.StatusCode
            try {
                $stream = $exc.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream)
                $raw = $reader.ReadToEnd()
                try { $body = $raw | ConvertFrom-Json } catch { $body = $raw }
            } catch { }
        }
        return [pscustomobject]@{
            Status = $status
            Body   = $body
            Error  = $exc.Message
        }
    }
}

function Assert-2xx {
    param([int]$Status, [string]$Msg = "expected 2xx")
    if ($Status -lt 200 -or $Status -ge 300) {
        throw "$Msg, got $Status"
    }
}

function Assert-Eq {
    param($Expected, $Actual, [string]$Msg = "values differ")
    if ($Expected -ne $Actual) {
        throw "$Msg`n  expected: $Expected`n  actual:   $Actual"
    }
}

function Assert-True {
    param($Cond, [string]$Msg = "condition is false")
    if (-not $Cond) { throw $Msg }
}

function Assert-NotNull {
    param($Val, [string]$Msg = "value is null")
    if ($null -eq $Val) { throw $Msg }
}

function Clean-Smoke {
    param([string]$Pattern = "SMOKE_")
    $all = Invoke-Api -Method GET -Path "/api/calc/definitions"
    if ($all.Status -eq 200 -and $all.Body) {
        foreach ($d in $all.Body) {
            if ($d.tag_name -and $d.tag_name.StartsWith($Pattern)) {
                Invoke-Api -Method DELETE -Path "/api/calc/definitions/$($d.id)" | Out-Null
            }
        }
    }
}


# ===========================================================================
# RUN
# ===========================================================================

Write-Host "=============================================" -ForegroundColor Yellow
Write-Host "Phase 16.0f Smoke v2.1 - backend + audit" -ForegroundColor Yellow
Write-Host "Prefix: $PREFIX" -ForegroundColor Yellow
Write-Host "Time:   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Yellow
Write-Host "=============================================" -ForegroundColor Yellow

Write-Host ""
Write-Host "Pre-cleanup..." -ForegroundColor Gray
Clean-Smoke


# ---------------------------------------------------------------------------
Section "1. Health"
# ---------------------------------------------------------------------------

T "GET /health returns 200" {
    $r = Invoke-Api GET /health
    Assert-2xx $r.Status
}

T "GET /api/calc/block-types returns >= 60 schemas" {
    $r = Invoke-Api GET /api/calc/block-types
    Assert-2xx $r.Status
    Assert-NotNull $r.Body
    Assert-True ($r.Body.Count -ge 60) "expected >=60 block types, got $($r.Body.Count)"
}

T "GET /api/calc/definitions returns 2xx" {
    $r = Invoke-Api GET /api/calc/definitions
    Assert-2xx $r.Status
}

T "GET /api/audit-log returns 2xx with {total, events}" {
    $r = Invoke-Api GET /api/audit-log
    Assert-2xx $r.Status
    Assert-True ($r.Body.PSObject.Properties.Name -contains 'total')  "missing 'total'"
    Assert-True ($r.Body.PSObject.Properties.Name -contains 'events') "missing 'events'"
}

T "GET /api/audit-log/actions returns array" {
    $r = Invoke-Api GET /api/audit-log/actions
    Assert-2xx $r.Status
}


# ---------------------------------------------------------------------------
Section "2. Block schema integrity"
# ---------------------------------------------------------------------------

# Real categories are fine-grained, lowercase. The stateful family is split:
# timer / counter / edge_detector / latch — no umbrella 'stateful' category.
$expectedCategories = @('arithmetic', 'aggregation', 'selection', 'comparison', 'logical', 'conditional')
$expectedStatefulFamily = @('timer', 'counter', 'edge_detector', 'latch')

T "block-types covers all core categories (lowercase)" {
    $r = Invoke-Api GET /api/calc/block-types
    $cats = $r.Body | ForEach-Object { $_.category } | Sort-Object -Unique
    $missing = @()
    foreach ($c in $expectedCategories) {
        if ($cats -notcontains $c) { $missing += $c }
    }
    Assert-True ($missing.Count -eq 0) "missing categories: $($missing -join ', '); present: $($cats -join ', ')"
}

T "block-types covers at least one stateful-family category" {
    $r = Invoke-Api GET /api/calc/block-types
    $cats = $r.Body | ForEach-Object { $_.category } | Sort-Object -Unique
    $found = @($expectedStatefulFamily | Where-Object { $cats -contains $_ })
    Assert-True ($found.Count -ge 1) "no stateful family category present; expected one of: $($expectedStatefulFamily -join ', ')"
}

T "every block-type has code + label + category" {
    $r = Invoke-Api GET /api/calc/block-types
    $issues = @()
    foreach ($bt in $r.Body) {
        if (-not $bt.code)     { $issues += "id=$($bt.id) missing code" }
        if (-not $bt.label)    { $issues += "$($bt.code) missing label" }
        if (-not $bt.category) { $issues += "$($bt.code) missing category" }
    }
    Assert-True ($issues.Count -eq 0) "$($issues.Count) issues: $($issues[0..4] -join '; ')"
}

T "ADD entry exists with category=arithmetic" {
    $r = Invoke-Api GET /api/calc/block-types
    $add = $r.Body | Where-Object { $_.code -eq 'ADD' }
    Assert-NotNull $add "ADD block type not found"
    Assert-Eq "arithmetic" $add.category
    Assert-True ($add.is_evaluable -eq $true) "ADD not marked is_evaluable"
}

T "SUM_OF entry exists with category=aggregation" {
    $r = Invoke-Api GET /api/calc/block-types
    $sum = $r.Body | Where-Object { $_.code -eq 'SUM_OF' }
    Assert-NotNull $sum "SUM_OF block type not found"
    Assert-Eq "aggregation" $sum.category
}


# ---------------------------------------------------------------------------
Section "3. Tag creation"
# ---------------------------------------------------------------------------

$tagAdd  = $null
$tagBool = $null

T "POST /api/calc/output-tags creates float64 tag" {
    $body = @{ name = "${PREFIX}ADD_OUT"; data_type = "float64"; description = "smoke ADD out" }
    $r = Invoke-Api POST /api/calc/output-tags $body
    Assert-2xx $r.Status
    Assert-NotNull $r.Body.id
    $script:tagAdd = $r.Body
}

T "POST creates bool tag (for future bool-output calcs)" {
    $body = @{ name = "${PREFIX}CMP_OUT"; data_type = "bool" }
    $r = Invoke-Api POST /api/calc/output-tags $body
    Assert-2xx $r.Status
    $script:tagBool = $r.Body
}

T "duplicate tag name on same device returns 409" {
    $body = @{ name = "${PREFIX}ADD_OUT"; data_type = "float64" }
    $r = Invoke-Api POST /api/calc/output-tags $body
    Assert-Eq 409 $r.Status "expected 409 Conflict, got $($r.Status)"
}


# ---------------------------------------------------------------------------
Section "4. Calc creation (real block_config shape)"
# ---------------------------------------------------------------------------

# Find a real input tag (something non-SMOKE that exists).
$inputTagId = $null
$tagsResp = Invoke-Api GET /api/tags
if ($tagsResp.Status -eq 200 -and $tagsResp.Body) {
    $first = $tagsResp.Body | Where-Object { -not ($_.name -like 'SMOKE_*') } | Select-Object -First 1
    if ($first) { $inputTagId = $first.id }
}
Assert-NotNull $inputTagId
Write-Host "  (using input tag id=$inputTagId)" -ForegroundColor Gray

$calcAdd = $null

T "POST creates ARITHMETIC.ADD calc (real shape)" {
    Assert-NotNull $script:tagAdd "tagAdd prereq missing"
    $body = @{
        tag_id            = $script:tagAdd.id
        block_type        = "ADD"
        block_config      = @{
            inputs = @(
                @{ tag   = $inputTagId },
                @{ value = 10.5 }
            )
        }
        execution_rate_ms = 1000
        enabled           = $true
    }
    $r = Invoke-Api POST /api/calc/definitions $body
    Assert-2xx $r.Status "ADD create failed status=$($r.Status) body=$($r.Body | ConvertTo-Json -Depth 5 -Compress)"
    Assert-NotNull $r.Body.id
    Assert-Eq "ADD" $r.Body.block_type
    $script:calcAdd = $r.Body
}


# ---------------------------------------------------------------------------
Section "5. Read"
# ---------------------------------------------------------------------------

T "GET /api/calc/definitions includes the new ADD calc" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    $r = Invoke-Api GET /api/calc/definitions
    Assert-2xx $r.Status
    $found = $r.Body | Where-Object { $_.id -eq $script:calcAdd.id }
    Assert-NotNull $found "calc id=$($script:calcAdd.id) not in list"
}

T "GET /api/calc/definitions/{id} returns single object" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    $r = Invoke-Api GET "/api/calc/definitions/$($script:calcAdd.id)"
    Assert-2xx $r.Status
    Assert-True (-not ($r.Body -is [array])) "expected single object, got array"
    Assert-Eq $script:calcAdd.id $r.Body.id
    Assert-Eq "ADD" $r.Body.block_type
}

T "GET nonexistent calc id returns 404" {
    $r = Invoke-Api GET /api/calc/definitions/99999999
    Assert-Eq 404 $r.Status
}


# ---------------------------------------------------------------------------
Section "6. Update (PATCH)"
# ---------------------------------------------------------------------------

T "PATCH toggle enabled false" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    $r = Invoke-Api PATCH "/api/calc/definitions/$($script:calcAdd.id)" @{ enabled = $false }
    Assert-2xx $r.Status
    Assert-Eq $false $r.Body.enabled
}

T "PATCH toggle enabled back to true" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    $r = Invoke-Api PATCH "/api/calc/definitions/$($script:calcAdd.id)" @{ enabled = $true }
    Assert-2xx $r.Status
    Assert-Eq $true $r.Body.enabled
}

T "PATCH preserves untouched fields (block_type unchanged after enabled flip)" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    $before = Invoke-Api GET "/api/calc/definitions/$($script:calcAdd.id)"
    $r = Invoke-Api PATCH "/api/calc/definitions/$($script:calcAdd.id)" @{ enabled = $true }
    Assert-2xx $r.Status
    Assert-Eq $before.Body.block_type $r.Body.block_type "block_type changed unexpectedly"
    Assert-Eq $before.Body.tag_id $r.Body.tag_id "tag_id changed unexpectedly"
}

T "PATCH replace block_config" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    $newConfig = @{
        inputs = @(
            @{ value = 99 },
            @{ value = 1 }
        )
    }
    $r = Invoke-Api PATCH "/api/calc/definitions/$($script:calcAdd.id)" @{ block_config = $newConfig }
    Assert-2xx $r.Status
    Assert-NotNull $r.Body.block_config
}

T "PATCH nonexistent id returns 404" {
    $r = Invoke-Api PATCH /api/calc/definitions/99999999 @{ enabled = $false }
    Assert-Eq 404 $r.Status
}


# ---------------------------------------------------------------------------
Section "7. Validation matrix"
# ---------------------------------------------------------------------------

T "POST calc without tag_id returns 4xx" {
    $body = @{ block_type = "ADD"; block_config = @{ inputs = @() } }
    $r = Invoke-Api POST /api/calc/definitions $body
    Assert-True ($r.Status -ge 400 -and $r.Status -lt 500) "expected 4xx, got $($r.Status)"
}

T "POST calc with invalid block_type returns 4xx" {
    $body = @{
        tag_id       = $script:tagAdd.id
        block_type   = "TOTALLY_BOGUS_${STAMP}"
        block_config = @{ inputs = @() }
    }
    $r = Invoke-Api POST /api/calc/definitions $body
    Assert-True ($r.Status -ge 400 -and $r.Status -lt 500) "expected 4xx, got $($r.Status)"
}

T "POST output-tag with bad data_type returns 4xx" {
    $body = @{ name = "${PREFIX}BAD_TYPE"; data_type = "not_a_real_type" }
    $r = Invoke-Api POST /api/calc/output-tags $body
    Assert-True ($r.Status -ge 400 -and $r.Status -lt 500) "expected 4xx, got $($r.Status)"
}


# ---------------------------------------------------------------------------
Section "8. Audit log verification"
# ---------------------------------------------------------------------------

Start-Sleep -Milliseconds 500   # let audit writes flush

T "audit log shows tag.create events from this run" {
    $r = Invoke-Api GET "/api/audit-log?action=tag.create&limit=100"
    Assert-2xx $r.Status
    $smokeEvents = $r.Body.events | Where-Object {
        $_.target_label -and $_.target_label.StartsWith($PREFIX)
    }
    Assert-True ($smokeEvents.Count -ge 3) `
        "expected >=3 tag.create events for $PREFIX, got $($smokeEvents.Count)"
}

T "audit log filter by action prefix (action=tag.)" {
    $r = Invoke-Api GET "/api/audit-log?action=tag.&limit=200"
    Assert-2xx $r.Status
    Assert-True ($r.Body.events.Count -ge 1) "no events matched action=tag."
    foreach ($e in $r.Body.events) {
        Assert-True ($e.action.StartsWith("tag.")) "event $($e.id) has wrong action: $($e.action)"
    }
}

T "audit log filter by status=success" {
    $r = Invoke-Api GET "/api/audit-log?status=success&limit=50"
    Assert-2xx $r.Status
    foreach ($e in $r.Body.events) {
        Assert-Eq "success" $e.status
    }
}

T "audit log filter by status=denied finds the 409 duplicate" {
    $r = Invoke-Api GET "/api/audit-log?status=denied&limit=20"
    Assert-2xx $r.Status
    $found = $r.Body.events | Where-Object {
        $_.target_label -eq "${PREFIX}ADD_OUT" -and $_.action -eq "tag.create"
    }
    Assert-NotNull $found "denied event for duplicate ${PREFIX}ADD_OUT not found"
}

T "audit log pagination: limit=5" {
    $r = Invoke-Api GET "/api/audit-log?limit=5"
    Assert-2xx $r.Status
    Assert-True ($r.Body.events.Count -le 5) "limit not respected: got $($r.Body.events.Count)"
    Assert-Eq 5 $r.Body.limit
}

T "audit log time filter (since=now-1min)" {
    $since = (Get-Date).AddMinutes(-1).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $r = Invoke-Api GET "/api/audit-log?since=$since&limit=100"
    Assert-2xx $r.Status
    $minTime = (Get-Date).AddMinutes(-1).ToUniversalTime()
    foreach ($e in $r.Body.events) {
        $eventTime = [datetime]::Parse($e.occurred_at).ToUniversalTime()
        Assert-True ($eventTime -ge $minTime.AddSeconds(-5)) `
            "event $($e.id) at $($e.occurred_at) older than since filter"
    }
}

T "audit log distinct actions includes tag.create" {
    $r = Invoke-Api GET /api/audit-log/actions
    Assert-2xx $r.Status
    Assert-True ($r.Body -contains "tag.create") "tag.create missing from distinct actions"
}


# ---------------------------------------------------------------------------
Section "9. Concurrency"
# ---------------------------------------------------------------------------

T "5 rapid sequential tag creates all succeed" {
    $created = 0
    for ($i = 1; $i -le 5; $i++) {
        $body = @{ name = "${PREFIX}RAPID_${i}"; data_type = "float64" }
        $r = Invoke-Api POST /api/calc/output-tags $body
        if ($r.Status -ge 200 -and $r.Status -lt 300) { $created++ }
    }
    Assert-Eq 5 $created
}

T "rapid PATCH on same def settles to last value" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    # Alternate enabled true/false 5 times. Sequence: false, true, false, true, false.
    # Use a field we know is unconstrained (enabled), not execution_rate_ms.
    $expected = $null
    for ($i = 0; $i -lt 5; $i++) {
        $val = ($i % 2 -eq 1)
        $r = Invoke-Api PATCH "/api/calc/definitions/$($script:calcAdd.id)" @{ enabled = $val }
        Assert-2xx $r.Status "PATCH iteration $i (enabled=$val) failed: $($r.Status)"
        $expected = $val
    }
    $final = Invoke-Api GET "/api/calc/definitions/$($script:calcAdd.id)"
    Assert-Eq $expected $final.Body.enabled "final enabled state wrong"
}


# ---------------------------------------------------------------------------
Section "10. Delete"
# ---------------------------------------------------------------------------

T "DELETE existing calc returns 2xx" {
    Assert-NotNull $script:calcAdd "calcAdd prereq missing"
    $r = Invoke-Api DELETE "/api/calc/definitions/$($script:calcAdd.id)"
    Assert-2xx $r.Status
}

T "DELETEd calc no longer in list" {
    $r = Invoke-Api GET /api/calc/definitions
    $found = $r.Body | Where-Object { $_.id -eq $script:calcAdd.id }
    Assert-True ($null -eq $found) "deleted calc still present"
}

T "DELETE nonexistent calc returns 404" {
    $r = Invoke-Api DELETE /api/calc/definitions/99999999
    Assert-Eq 404 $r.Status
}


# ---------------------------------------------------------------------------
# Final cleanup
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Post-cleanup..." -ForegroundColor Gray
Clean-Smoke


# ===========================================================================
# Summary
# ===========================================================================

$duration = (Get-Date) - $startTime

Write-Host ""
Write-Host "=============================================" -ForegroundColor Yellow
Write-Host "Results" -ForegroundColor Yellow
Write-Host "=============================================" -ForegroundColor Yellow
Write-Host "  passed:   $script:pass" -ForegroundColor Green
Write-Host "  failed:   $script:fail" -ForegroundColor $(if ($script:fail) { 'Red' } else { 'Green' })
Write-Host "  duration: $([math]::Round($duration.TotalSeconds, 1))s" -ForegroundColor Gray

if ($script:fail -gt 0) {
    Write-Host ""
    Write-Host "Failures:" -ForegroundColor Red
    foreach ($f in $script:failures) {
        Write-Host "  - [$($f.section)] $($f.name)" -ForegroundColor Red
        Write-Host "      $($f.error)" -ForegroundColor DarkRed
    }
    exit 1
}

Write-Host ""
Write-Host "All smoke tests passed." -ForegroundColor Green
exit 0
