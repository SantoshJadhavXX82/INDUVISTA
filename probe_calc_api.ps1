# =============================================================================
# Diagnostic - probe the actual API response shapes so we can fix smoke v2.
# Run this and paste the output.
# =============================================================================

$BASE = "http://localhost:8000"

function Show-Json {
    param($Obj, [int]$Depth = 10)
    if ($null -eq $Obj) { Write-Host "(null)" -ForegroundColor DarkGray; return }
    $Obj | ConvertTo-Json -Depth $Depth
}

# ---------------------------------------------------------------------------
Write-Host "=== 1. /api/calc/block-types shape ===" -ForegroundColor Cyan
$bt = Invoke-RestMethod "$BASE/api/calc/block-types"

# Is it an array or a hash?
if ($bt -is [array]) {
    Write-Host "Response is an ARRAY of length: $($bt.Count)" -ForegroundColor Green
    Write-Host ""
    Write-Host "First entry keys:" -ForegroundColor Yellow
    $bt[0].PSObject.Properties.Name | ForEach-Object { Write-Host "  - $_" }
    Write-Host ""
    Write-Host "First entry (full JSON):" -ForegroundColor Yellow
    Show-Json $bt[0]
} else {
    Write-Host "Response is NOT an array. Type: $($bt.GetType().Name)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Top-level keys:" -ForegroundColor Yellow
    $bt.PSObject.Properties.Name | ForEach-Object { Write-Host "  - $_" }
    Write-Host ""
    Write-Host "First few characters of JSON:" -ForegroundColor Yellow
    $j = $bt | ConvertTo-Json -Depth 5
    Write-Host $j.Substring(0, [Math]::Min(2000, $j.Length))
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== 2. Look for ADD entry across plausible field names ===" -ForegroundColor Cyan
if ($bt -is [array]) {
    $add = $bt | Where-Object {
        $_.block_type -eq 'ADD' -or $_.type -eq 'ADD' -or
        $_.name -eq 'ADD' -or $_.id -eq 'ADD' -or $_.code -eq 'ADD'
    } | Select-Object -First 1
    if ($add) {
        Write-Host "Found ADD entry:" -ForegroundColor Green
        Show-Json $add
    } else {
        Write-Host "ADD not found via block_type/type/name/id/code. Sampling 5 entries:" -ForegroundColor Yellow
        $bt | Select-Object -First 5 | ForEach-Object { Show-Json $_ -Depth 3; Write-Host "---" }
    }
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== 3. Existing calc def schema (the response shape) ===" -ForegroundColor Cyan
$defs = Invoke-RestMethod "$BASE/api/calc/definitions"
Write-Host "Total defs: $($defs.Count)" -ForegroundColor Green
if ($defs.Count -gt 0) {
    Write-Host ""
    Write-Host "First def keys:" -ForegroundColor Yellow
    $defs[0].PSObject.Properties.Name | ForEach-Object { Write-Host "  - $_" }
    Write-Host ""
    Write-Host "First def (full):" -ForegroundColor Yellow
    Show-Json $defs[0]
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== 4. Single-def GET shape ===" -ForegroundColor Cyan
if ($defs.Count -gt 0) {
    $defId = $defs[0].id
    $single = Invoke-RestMethod "$BASE/api/calc/definitions/$defId"
    Write-Host "GET /api/calc/definitions/$defId returns:" -ForegroundColor Yellow
    if ($single -is [array]) {
        Write-Host "  ARRAY of length $($single.Count) (unexpected - usually single object)" -ForegroundColor Red
    } else {
        Write-Host "  Single object: keys = $($single.PSObject.Properties.Name -join ', ')"
    }
    Show-Json $single
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== 5. Minimal calc create attempt - capture 400 error body ===" -ForegroundColor Cyan

# Get a real tag id to wire to.
$tags = Invoke-RestMethod "$BASE/api/tags"
$inputTag  = $tags | Where-Object { -not $_.name.StartsWith('SMOKE_') } | Select-Object -First 1
$outputTag = $tags | Where-Object { $_.name.StartsWith('AUDIT_TEST_') } | Select-Object -First 1
if (-not $outputTag) {
    $body = @{ name = "PROBE_OUT_$(Get-Date -Format yyyyMMddHHmmss)"; data_type = "float64" } | ConvertTo-Json
    $outputTag = Invoke-RestMethod "$BASE/api/calc/output-tags" -Method POST -ContentType "application/json" -Body $body
}
Write-Host "Using input tag id=$($inputTag.id) name=$($inputTag.name)" -ForegroundColor Gray
Write-Host "Using output tag id=$($outputTag.id) name=$($outputTag.name)" -ForegroundColor Gray

$createBody = @{
    tag_id = $outputTag.id
    block_type = "ADD"
    block_config = @{
        mode   = "n_ary"
        inputs = @(
            @{ kind = "tag";      tag_id = $inputTag.id },
            @{ kind = "constant"; value = 10 }
        )
    }
    execution_rate_ms = 1000
    enabled = $true
} | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "POST body:" -ForegroundColor Yellow
Write-Host $createBody
Write-Host ""

try {
    $resp = Invoke-WebRequest "$BASE/api/calc/definitions" -Method POST -ContentType "application/json" -Body $createBody -UseBasicParsing
    Write-Host "RESULT: $($resp.StatusCode) (success)" -ForegroundColor Green
    Write-Host $resp.Content
    # Cleanup our probe def.
    $created = $resp.Content | ConvertFrom-Json
    Invoke-WebRequest "$BASE/api/calc/definitions/$($created.id)" -Method DELETE -UseBasicParsing | Out-Null
} catch {
    $status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    Write-Host "RESULT: $status FAIL" -ForegroundColor Red
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $body = $reader.ReadToEnd()
        Write-Host "Error body:" -ForegroundColor Yellow
        Write-Host $body
    }
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== 6. Methods accepted by /api/calc/definitions/{id} ===" -ForegroundColor Cyan
if ($defs.Count -gt 0) {
    $defId = $defs[0].id
    try {
        $resp = Invoke-WebRequest "$BASE/api/calc/definitions/$defId" -Method OPTIONS -UseBasicParsing
        Write-Host "OPTIONS allowed methods:" -ForegroundColor Yellow
        Write-Host "  Allow: $($resp.Headers.Allow)"
    } catch {
        Write-Host "OPTIONS failed (some APIs don't implement it). Probing PATCH directly:" -ForegroundColor Yellow
        try {
            $r = Invoke-WebRequest "$BASE/api/calc/definitions/$defId" -Method PATCH -ContentType "application/json" -Body '{}' -UseBasicParsing
            Write-Host "  PATCH (empty body): $($r.StatusCode)" -ForegroundColor Green
        } catch {
            $s = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
            Write-Host "  PATCH (empty body): $s" -ForegroundColor $(if ($s -in 200,204,400,422) { 'Green' } else { 'Red' })
        }
    }
}

Write-Host ""
Write-Host "=== Done. Copy this entire output and paste back. ===" -ForegroundColor Magenta
