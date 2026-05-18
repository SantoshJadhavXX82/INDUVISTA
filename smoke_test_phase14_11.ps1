# =============================================================================
# Phase 14.11 -- Alarm rules bulk import smoke (v2).
#
# v2 fixes:
#   - Section 0 now polls /health until backend is serving (handles
#     the startup race between `docker compose up` returning and
#     uvicorn finishing app import).
#   - UploadFile builds the multipart body as a byte array, preserving
#     binary XLSX content. Previous version UTF-8-decoded the bytes
#     which corrupted ZIP archives.
#
# Run from project root:
#   powershell.exe -ExecutionPolicy Bypass -File .\smoke_test_phase14_11.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'
# PS 5.1 Invoke-WebRequest is dramatically slowed by progress-bar
# rendering in script context. Silencing it eliminates spurious
# timeouts in the WaitForBackend polling loop.
$ProgressPreference = 'SilentlyContinue'

$Pass    = 0
$Fail    = 0
$Reasons = @()

$script:CreatedTagIds = @()

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

function WaitForBackend([int]$maxSec = 60) {
    # Polls /health until it returns 200 or we hit the timeout.
    # Uses 127.0.0.1 explicitly to avoid Windows' IPv6-first DNS
    # behavior, which can add multi-second retries before falling
    # back to IPv4.
    $deadline = (Get-Date).AddSeconds($maxSec)
    $lastErr = "no attempts made"
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" `
                -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            if ($r.StatusCode -eq 200) {
                return $true
            }
            $lastErr = "status=$($r.StatusCode)"
        } catch {
            $lastErr = "$($_.Exception.Message)"
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "  Last poll error after $attempt attempts: $lastErr" -ForegroundColor Yellow
    return $false
}

# Multipart upload helper. CRITICAL: build the body as a byte array.
# UTF-8 decoding binary content (e.g. XLSX, a ZIP archive) corrupts
# non-text bytes and produces nonsense at the server.
function UploadFile {
    param(
        [string]$filePath,
        [bool]$dryRun = $true,
        [bool]$strict = $true
    )
    $url = "http://localhost:8000/api/alarms/rules/import?dry_run=$($dryRun.ToString().ToLower())&strict=$($strict.ToString().ToLower())"
    $boundary = [System.Guid]::NewGuid().ToString()
    $LF = "`r`n"

    $fileBytes = [System.IO.File]::ReadAllBytes($filePath)
    $fileName = [System.IO.Path]::GetFileName($filePath)

    $topText = "--$boundary$LF" +
               "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"$LF" +
               "Content-Type: application/octet-stream$LF$LF"
    $bottomText = "$LF--$boundary--$LF"

    $topBytes = [System.Text.Encoding]::UTF8.GetBytes($topText)
    $bottomBytes = [System.Text.Encoding]::UTF8.GetBytes($bottomText)

    # Concatenate as raw bytes - no UTF-8 round-trip on file content.
    $total = $topBytes.Length + $fileBytes.Length + $bottomBytes.Length
    $body = New-Object byte[] $total
    [System.Array]::Copy($topBytes, 0, $body, 0, $topBytes.Length)
    [System.Array]::Copy($fileBytes, 0, $body, $topBytes.Length, $fileBytes.Length)
    [System.Array]::Copy($bottomBytes, 0, $body,
        $topBytes.Length + $fileBytes.Length, $bottomBytes.Length)

    try {
        $resp = Invoke-RestMethod -Uri $url -Method POST `
            -ContentType "multipart/form-data; boundary=$boundary" `
            -Body $body
        return @{ ok = $true; body = $resp }
    } catch {
        $detail = $null
        try { $detail = $_.ErrorDetails.Message } catch {}
        $status = 0
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        return @{ ok = $false; status = $status; body = $detail }
    }
}

try {
    # ---- Section 0: Service health + readiness -----------------------------

    Section "0. Service health + backend readiness"

    foreach ($svc in @("backend", "postgres")) {
        $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
        if ($psout -match '"State":\s*"running"' -or $psout -match '"Status":\s*"Up') {
            Pass "$svc container is up"
        } else {
            Fail "$svc container not up"
        }
    }

    if (WaitForBackend -maxSec 60) {
        Pass "Backend /health returned 200 (uvicorn ready)"
    } else {
        Fail "Backend not ready within 60s"
        throw "backend not ready"
    }

    $openpyxlVer = (docker compose exec -T backend python -c "import openpyxl; print(openpyxl.__version__)" 2>&1 | Out-String).Trim()
    if ($openpyxlVer -match '^\d+\.\d+') {
        Pass "openpyxl installed (version $openpyxlVer)"
    } else {
        Fail "openpyxl not available: $openpyxlVer"
    }

    # ---- Section 1: Template endpoints -------------------------------------

    Section "1. Template endpoints"

    try {
        $csv = Invoke-WebRequest -Uri "http://localhost:8000/api/alarms/rules/import/template?format=csv" -UseBasicParsing
        if ($csv.StatusCode -eq 200 -and $csv.Content -match 'tag_name,rule_type') {
            Pass "CSV template downloads with correct headers"
        } else {
            Fail "CSV template missing headers"
        }
    } catch {
        Fail "CSV template fetch failed: $_"
    }

    try {
        $xlsx = Invoke-WebRequest -Uri "http://localhost:8000/api/alarms/rules/import/template?format=xlsx" -UseBasicParsing
        if ($xlsx.StatusCode -eq 200 -and $xlsx.RawContentLength -gt 1000) {
            Pass "XLSX template downloads ($($xlsx.RawContentLength) bytes)"
        } else {
            Fail "XLSX template too small or 404"
        }
    } catch {
        Fail "XLSX template fetch failed: $_"
    }

    # ---- Section 2: Synthetic input tags -----------------------------------

    Section "2. Synthetic input tags"

    PsqlExec "DELETE FROM alarm_rules WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'smoke_phase_14_11_%')"
    PsqlExec "DELETE FROM tags WHERE name LIKE 'smoke_phase_14_11_%'"

    $deviceId = [int](Psql "SELECT id FROM devices WHERE name != 'Calculations' ORDER BY id LIMIT 1")
    foreach ($suffix in @("tag_a", "tag_b")) {
        $name = "smoke_phase_14_11_$suffix"
        $addr = if ($suffix -eq "tag_a") { 99990 } else { 99991 }
        PsqlExec @"
INSERT INTO tags (
    device_id, name, data_type, function_code, address,
    enabled, description, min_value, max_value
) VALUES (
    $deviceId, '$name', 'float32', 3, $addr,
    false, 'Phase 14.11 smoke - safe to delete', -1000, 1000
)
"@
        $id = [int](Psql "SELECT id FROM tags WHERE name = '$name'")
        $script:CreatedTagIds += $id
    }
    Pass "Tags created: smoke_phase_14_11_tag_a, smoke_phase_14_11_tag_b"

    # ---- Section 3: Dry-run with mixed CSV ---------------------------------

    Section "3. Dry-run with mixed valid/invalid CSV"

    if (-not (Test-Path ".\sample_alarm_rules_smoke.csv")) {
        Fail "sample_alarm_rules_smoke.csv missing from project root"
        throw "missing sample"
    }

    $dryResp = UploadFile -filePath ".\sample_alarm_rules_smoke.csv" -dryRun $true -strict $true
    if (-not $dryResp.ok) {
        Fail "Dry-run upload failed: $($dryResp.status) $($dryResp.body)"
        throw "dry-run failed"
    }
    $summary = $dryResp.body

    if ($summary.total_rows -eq 10) {
        Pass "Total rows parsed = 10"
    } else {
        Fail "Expected 10 rows, got $($summary.total_rows)"
    }

    if ($summary.ok_count -eq 4) {
        Pass "OK count = 4"
    } else {
        Fail "Expected 4 OK, got $($summary.ok_count)"
    }
    if ($summary.duplicate_count -eq 1) {
        Pass "Duplicate count = 1 (intra-batch hi tag_a)"
    } else {
        Fail "Expected 1 duplicate, got $($summary.duplicate_count)"
    }
    if ($summary.error_count -eq 5) {
        Pass "Error count = 5"
    } else {
        Fail "Expected 5 errors, got $($summary.error_count)"
    }

    $row6 = $summary.rows | Where-Object { $_.row_number -eq 6 } | Select-Object -First 1
    if ($row6.status -eq 'error' -and ($row6.errors -join '|') -match 'taxonomy-only') {
        Pass "Row 6 (frozen) flagged taxonomy-only"
    } else {
        Fail "Row 6 expected taxonomy-only error, got: $($row6.errors)"
    }

    $row9 = $summary.rows | Where-Object { $_.row_number -eq 9 } | Select-Object -First 1
    if ($row9.status -eq 'error' -and ($row9.errors -join '|') -match 'window_seconds is required') {
        Pass "Row 9 (rate_of_change) flagged missing window_seconds"
    } else {
        Fail "Row 9 expected window_seconds error, got: $($row9.errors)"
    }

    if ($summary.dry_run -eq $true -and $summary.committed -eq $false) {
        Pass "Dry-run correctly did not commit"
    } else {
        Fail "Dry-run flags wrong: dry_run=$($summary.dry_run) committed=$($summary.committed)"
    }

    # ---- Section 4: Strict mode blocks commit ------------------------------

    Section "4. Strict mode blocks commit when errors present"

    $strictResp = UploadFile -filePath ".\sample_alarm_rules_smoke.csv" -dryRun $false -strict $true
    if ($strictResp.ok -and $strictResp.body.committed -eq $false -and $strictResp.body.error_count -gt 0) {
        Pass "Strict mode refused to commit (committed=false, $($strictResp.body.error_count) errors)"
    } else {
        Fail "Strict mode should have refused: $($strictResp.body | ConvertTo-Json -Compress)"
    }

    # ---- Section 5: Verify nothing committed -------------------------------

    Section "5. Verify zero rules in DB after blocked strict commit"

    $ruleCount = [int](Psql "SELECT count(*) FROM alarm_rules WHERE tag_id = ANY(ARRAY[$($script:CreatedTagIds -join ',')])")
    if ($ruleCount -eq 0) {
        Pass "Zero alarm_rules in DB for test tags (strict blocked all writes)"
    } else {
        Fail "Expected 0 rules, found $ruleCount - DB was modified!"
    }

    # ---- Section 6: Clean CSV commits in strict mode -----------------------

    Section "6. Clean CSV commits in strict mode"

    $cleanCsv = @"
tag_name,rule_type,severity,threshold,deadband,on_delay_sec,off_delay_sec,latched,window_seconds,message_template,enabled
smoke_phase_14_11_tag_a,hi_hi,critical,95.0,1.0,30,30,true,,Tag A critical high-high,true
smoke_phase_14_11_tag_a,hi,high,85.0,1.0,30,30,false,,,true
smoke_phase_14_11_tag_b,lo,medium,10.0,0.5,15,15,false,,Tag B low,true
smoke_phase_14_11_tag_b,rate_of_change,high,5.0,0.0,5,5,false,60,Fast change,true
"@
    $cleanPath = (Resolve-Path .).Path + "\smoke_14_11_clean.csv"
    [System.IO.File]::WriteAllText($cleanPath, $cleanCsv)

    $cleanResp = UploadFile -filePath $cleanPath -dryRun $false -strict $true
    if ($cleanResp.ok -and $cleanResp.body.committed -eq $true -and $cleanResp.body.ok_count -eq 4) {
        Pass "Clean CSV committed all 4 rows in strict mode"
    } else {
        Fail "Clean commit failed: $($cleanResp.body | ConvertTo-Json -Compress)"
    }

    # ---- Section 7: Verify post-commit state -------------------------------

    Section "7. Verify 4 rules in DB after commit"

    $postCount = [int](Psql "SELECT count(*) FROM alarm_rules WHERE tag_id = ANY(ARRAY[$($script:CreatedTagIds -join ',')])")
    if ($postCount -eq 4) {
        Pass "4 alarm_rules present in DB"
    } else {
        Fail "Expected 4 rules, found $postCount"
    }

    $ruleTypes = (Psql "SELECT string_agg(rule_type, ',' ORDER BY rule_type) FROM alarm_rules WHERE tag_id = ANY(ARRAY[$($script:CreatedTagIds -join ',')])")
    if ($ruleTypes -eq "hi,hi_hi,lo,rate_of_change") {
        Pass "Rule types in DB match expectation"
    } else {
        Fail "Rule types wrong: $ruleTypes"
    }

    # ---- Section 8: Re-import same CSV - level types duplicate -------------

    Section "8. Re-import clean CSV - level types should all duplicate"

    $reResp = UploadFile -filePath $cleanPath -dryRun $true -strict $true
    if ($reResp.ok) {
        $dupCount = $reResp.body.duplicate_count
        if ($dupCount -eq 3) {
            Pass "3 level-rule duplicates detected on re-import (hi_hi, hi, lo)"
        } else {
            Fail "Expected 3 duplicates, got $dupCount"
        }
    } else {
        Fail "Re-import dry-run failed"
    }

    # ---- Section 9: XLSX round-trip ----------------------------------------

    Section "9. XLSX round-trip"

    try {
        $xlsxBytes = (Invoke-WebRequest `
            -Uri "http://localhost:8000/api/alarms/rules/import/template?format=xlsx" `
            -UseBasicParsing).Content
        $xlsxPath = (Resolve-Path .).Path + "\smoke_14_11_template.xlsx"
        [System.IO.File]::WriteAllBytes($xlsxPath, $xlsxBytes)
        Pass "XLSX template saved locally ($($xlsxBytes.Length) bytes)"

        # Template has 2 example rows with unknown tags - expect 2 errors.
        $xlsxResp = UploadFile -filePath $xlsxPath -dryRun $true -strict $true
        if (-not $xlsxResp.ok) {
            Fail "XLSX upload failed: $($xlsxResp.status) $($xlsxResp.body)"
        } else {
            if ($xlsxResp.body.total_rows -eq 2) {
                Pass "XLSX parsed correctly: 2 rows extracted"
            } else {
                Fail "XLSX parse wrong: $($xlsxResp.body.total_rows) rows (body: $($xlsxResp.body | ConvertTo-Json -Compress))"
            }
            if ($xlsxResp.body.error_count -eq 2) {
                Pass "Both XLSX example rows correctly flagged as errors (unknown tags)"
            } else {
                Fail "Expected 2 errors from XLSX, got $($xlsxResp.body.error_count)"
            }
        }
    } catch {
        Fail "XLSX round-trip failed: $_"
    }
}
finally {
    Section "Cleanup"

    PsqlExec "DELETE FROM alarm_rules WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'smoke_phase_14_11_%')"
    PsqlExec "DELETE FROM tags WHERE name LIKE 'smoke_phase_14_11_%'"

    if (Test-Path ".\smoke_14_11_clean.csv")    { Remove-Item ".\smoke_14_11_clean.csv" }
    if (Test-Path ".\smoke_14_11_template.xlsx") { Remove-Item ".\smoke_14_11_template.xlsx" }

    Write-Host "  Deleted synthetic tags + rules + scratch files"
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
Write-Host "Phase 14.11 bulk alarm rule import verified." -ForegroundColor Cyan
exit 0
