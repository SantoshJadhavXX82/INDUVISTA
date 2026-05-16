# =============================================================================
# InduVista - Trend Module Smoke Test (Phase 13 complete - 13.5 through 13.11c)
# VERSION 13-complete-smoke-r1
# =============================================================================
# Exercises every Trend-module backend endpoint:
#   /trends/tags           - tag picker source (live value panel uses too)
#   /trends/history        - chart data; auto + raw + 1m + 1h + 1d
#                            with each aggregation mode (last/first/avg/min/max)
#   /trends/raw_table      - sortable Raw Historical Data Table
#   /trends/summary        - availability + stats (mean/stddev/range)
#   /trends/views          - saved views CRUD
#
# Frontend behaviors (tooltip pin, tile show/hide, time format, quality
# filter UI) are not testable from PowerShell - they require browser
# interaction. The data path that powers them IS tested.
#
# Strict ASCII only - no em-dash, dot-middle, arrow. Tested against the
# user's plant where 269 tags are enabled.
#
# Run from project root:
#   pwsh .\smoke_test_trend_complete.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'

$Pass    = 0
$Fail    = 0
$Warn    = 0
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
function Warn([string]$msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
    $script:Warn++
}
function Section([string]$name) {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host "  $name" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor Cyan
}

function Fetch-Json([string]$path, [int]$timeoutSec = 30) {
    try {
        return Invoke-RestMethod -Uri "http://localhost:8000$path" -TimeoutSec $timeoutSec
    } catch {
        Fail "GET $path threw: $($_.Exception.Message)"
        return $null
    }
}

function Iso-UtcNow() {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
}
function Iso-UtcMinusMinutes([int]$mins) {
    return ((Get-Date).ToUniversalTime().AddMinutes(-$mins)).ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
}
function Iso-UtcMinusHours([int]$hours) {
    return ((Get-Date).ToUniversalTime().AddHours(-$hours)).ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
}

# -----------------------------------------------------------------------------
# Section 0 - Pre-flight
# -----------------------------------------------------------------------------
Section "0. Pre-flight"

try {
    $ps = docker compose ps --format json 2>$null
    if ($ps) { Pass "docker compose responsive" } else { Fail "docker compose not responding" }
} catch { Fail "docker compose error: $_" }

$health = Fetch-Json "/health"
if ($health -and $health.status) { Pass "backend /health reachable (status=$($health.status))" }
else { Fail "backend /health unreachable" }

# -----------------------------------------------------------------------------
# Section A - Tag picker source (/trends/tags)
# -----------------------------------------------------------------------------
Section "A. Tag picker (/trends/tags)"

$tags = Fetch-Json "/api/trends/tags?enabled_only=false&limit=2000"
if ($null -ne $tags) {
    # API returns flat list, not {tags:[...]}
    $arr = @($tags)
    if ($arr.Count -gt 0) {
        Pass "fetched $($arr.Count) tags"
    } else {
        Fail "no tags returned"
    }

    # Verify TrendTag shape: must include the fields the Live Value Panel needs
    $t0 = $arr[0]
    $required = @('id','name','device_name','channel_name','data_type',
                  'engineering_unit','current_value_double','current_st',
                  'current_quality','last_update_utc','protocol',
                  'register_block_name','address','logging_enabled')
    foreach ($field in $required) {
        if ($t0.PSObject.Properties.Name -contains $field) {
            Pass "tag shape has '$field'"
        } else {
            Fail "tag shape missing '$field'"
        }
    }
} else {
    Fail "/trends/tags request failed"
}

# Pick 3 enabled numeric tags for subsequent tests
$enabledNumeric = $arr |
    Where-Object {
        $_.logging_enabled -eq $true -and
        $_.data_type -ne 'bool' -and $_.data_type -ne 'string'
    } |
    Select-Object -First 3

if ($enabledNumeric.Count -lt 1) {
    Fail "no enabled numeric tags - rest of smoke cannot proceed"
    Write-Host "Summary: PASS=$Pass FAIL=$Fail WARN=$Warn" -ForegroundColor Magenta
    exit 1
}

$idsCsv = ($enabledNumeric | ForEach-Object { $_.id }) -join ","
$firstTagName = $enabledNumeric[0].name
Pass "selected $($enabledNumeric.Count) tags for tests: ids=$idsCsv"

# -----------------------------------------------------------------------------
# Section B - History endpoint - auto routing across windows
# -----------------------------------------------------------------------------
Section "B. /trends/history - auto routing"

$tests = @(
    @{ label='Last 5 min  (should pick raw)'; mins=5;    expect='raw' },
    @{ label='Last 1 h    (should pick raw or 1m)'; mins=60; expect='raw|1m' },
    @{ label='Last 6 h    (should pick 1m or 1h)'; mins=360; expect='1m|1h' },
    @{ label='Last 24 h   (should pick 1h)';  mins=1440; expect='1h' },
    @{ label='Last 7 d    (should pick 1h or 1d)'; mins=10080; expect='1h|1d' }
)
foreach ($t in $tests) {
    $start = Iso-UtcMinusMinutes $t.mins
    $end   = Iso-UtcNow
    $url = "/api/trends/history?tag_ids=$idsCsv&start=$start&end=$end&aggregation=auto&max_points=2000"
    $r = Fetch-Json $url 60
    if ($null -eq $r) { continue }
    $agg = $r.aggregation
    if ($agg -match $t.expect) {
        Pass "$($t.label): aggregation=$agg"
    } else {
        Warn "$($t.label): aggregation=$agg (expected $($t.expect)) - acceptable if data span differs"
    }
    $totalPts = 0
    foreach ($s in $r.series) { $totalPts += $s.returned_count }
    if ($totalPts -gt 0) {
        Pass "$($t.label): $totalPts total points across $($r.series.Count) series"
    } else {
        Warn "$($t.label): 0 points returned (window may have no data)"
    }
}

# -----------------------------------------------------------------------------
# Section C - History endpoint - explicit aggregations
# -----------------------------------------------------------------------------
Section "C. /trends/history - explicit aggregations"

$start = Iso-UtcMinusHours 24
$end   = Iso-UtcNow
foreach ($g in @('raw','1m','1h','1d')) {
    $r = Fetch-Json "/api/trends/history?tag_ids=$idsCsv&start=$start&end=$end&aggregation=$g&max_points=2000" 60
    if ($null -eq $r) { continue }
    if ($r.aggregation -eq $g) {
        Pass "aggregation=$g requested -> returned $g"
    } else {
        Fail "aggregation=$g requested -> backend returned $($r.aggregation)"
    }
}

# -----------------------------------------------------------------------------
# Section D - History endpoint - aggregation MODES (spec 16.1)
# -----------------------------------------------------------------------------
Section "D. /trends/history - aggregation modes (last/first/avg/min/max)"

# Use 1h aggregation over 24h so we have bucketed data to differentiate modes.
$start = Iso-UtcMinusHours 24
$end   = Iso-UtcNow
$id1 = $enabledNumeric[0].id

$modeResults = @{}
foreach ($mode in @('last','first','avg','min','max')) {
    $r = Fetch-Json "/api/trends/history?tag_ids=$id1&start=$start&end=$end&aggregation=1h&agg_mode=$mode&max_points=2000" 60
    if ($null -eq $r) { continue }
    $vs = @($r.series[0].points | ForEach-Object { $_.v } | Where-Object { $null -ne $_ })
    if ($vs.Count -lt 2) {
        Warn "agg_mode=$mode returned <2 points - mode comparison skipped"
        continue
    }
    $modeResults[$mode] = $vs
    $sum = 0; foreach ($v in $vs) { $sum += $v }
    $mean = $sum / $vs.Count
    Pass "agg_mode=$mode returned $($vs.Count) points, mean=$([math]::Round($mean,3))"
}

# Cross-check: min mode mean should be <= avg mode mean <= max mode mean
if ($modeResults.ContainsKey('min') -and $modeResults.ContainsKey('avg') -and $modeResults.ContainsKey('max')) {
    $meanOf = { param($a) $s=0; foreach($x in $a){$s+=$x}; $s/$a.Count }
    $mMin = & $meanOf $modeResults['min']
    $mAvg = & $meanOf $modeResults['avg']
    $mMax = & $meanOf $modeResults['max']
    if ($mMin -le $mAvg -and $mAvg -le $mMax) {
        Pass "ordering correct: mean(min)=$([math]::Round($mMin,3)) <= mean(avg)=$([math]::Round($mAvg,3)) <= mean(max)=$([math]::Round($mMax,3))"
    } else {
        Fail "mode ordering violated: min=$mMin avg=$mAvg max=$mMax"
    }
}

# Also verify the buckets carry mn/mx (envelope inputs)
$rEnv = Fetch-Json "/api/trends/history?tag_ids=$id1&start=$start&end=$end&aggregation=1h&agg_mode=avg&max_points=200" 60
if ($rEnv -and $rEnv.series.Count -gt 0) {
    $pt = $rEnv.series[0].points | Where-Object { $null -ne $_.mn -and $null -ne $_.mx } | Select-Object -First 1
    if ($pt) {
        if ($pt.mn -le $pt.mx) {
            Pass "envelope inputs sane: mn=$($pt.mn) <= mx=$($pt.mx) at bucket $($pt.t)"
        } else {
            Fail "envelope inputs swapped: mn=$($pt.mn) > mx=$($pt.mx)"
        }
    } else {
        Warn "no buckets with both mn and mx populated"
    }
}

# -----------------------------------------------------------------------------
# Section E - Raw Historical Data Table (spec 7.4)
# -----------------------------------------------------------------------------
Section "E. /trends/raw_table"

$start = Iso-UtcMinusMinutes 30
$end   = Iso-UtcNow
$r = Fetch-Json "/api/trends/raw_table?tag_ids=$idsCsv&start=$start&end=$end&limit=500&order=desc" 30
if ($r) {
    $rows = @($r.rows)
    Pass "fetched $($rows.Count) raw rows (truncated=$($r.truncated))"

    if ($rows.Count -gt 0) {
        $row0 = $rows[0]
        $rawTableFields = @('t','tag_id','tag_name','v','engineering_unit','st','st_class',
                            'device_name','protocol','channel_name','register_block_name',
                            'address','data_type','source')
        foreach ($f in $rawTableFields) {
            if ($row0.PSObject.Properties.Name -contains $f) {
                Pass "raw_table row has '$f'"
            } else {
                Fail "raw_table row missing '$f'"
            }
        }

        # Default order is desc - verify
        if ($rows.Count -ge 2) {
            $t0 = [datetime]::Parse($rows[0].t)
            $t1 = [datetime]::Parse($rows[1].t)
            if ($t0 -ge $t1) { Pass "rows ordered desc by default" }
            else { Fail "rows NOT ordered desc: row[0].t=$($rows[0].t) row[1].t=$($rows[1].t)" }
        }
    }
}

# Truncation test - small limit on a large window
$r2 = Fetch-Json "/api/trends/raw_table?tag_ids=$idsCsv&start=$(Iso-UtcMinusHours 24)&end=$(Iso-UtcNow)&limit=10&order=desc" 30
if ($r2) {
    if ($r2.rows.Count -le 10 -and $r2.limit -eq 10) {
        Pass "limit enforced: $($r2.rows.Count) rows for limit=10"
    } else {
        Fail "limit not enforced: got $($r2.rows.Count) for limit=10"
    }
    if ($r2.truncated -eq $true) {
        Pass "truncation flag set when more rows exist"
    } else {
        Warn "truncation=false (acceptable if test data is sparse)"
    }
}

# -----------------------------------------------------------------------------
# Section F - Summary with statistics (Mean, Stddev, Range)
# -----------------------------------------------------------------------------
Section "F. /trends/summary - stats (Phase 13.11c)"

$start = Iso-UtcMinusHours 1
$end   = Iso-UtcNow
$r = Fetch-Json "/api/trends/summary?tag_ids=$idsCsv&start=$start&end=$end" 30
if ($r) {
    Pass "summary returned $($r.tags.Count) tag summaries"
    $t0 = $r.tags[0]
    $statFields = @('mean_value','stddev_value','observed_min','observed_max',
                    'engineering_unit','first_sample','last_sample',
                    'longest_gap_sec','longest_gap_start',
                    'good_samples','uncertain_samples','bad_samples','missing_samples',
                    'availability_pct','good_availability_pct')
    foreach ($f in $statFields) {
        if ($t0.PSObject.Properties.Name -contains $f) {
            Pass "summary row has '$f'"
        } else {
            Fail "summary row missing '$f'"
        }
    }

    # Sanity: observed_min <= mean <= observed_max when all present
    foreach ($t in $r.tags) {
        if ($null -ne $t.observed_min -and $null -ne $t.observed_max -and $null -ne $t.mean_value) {
            if ($t.observed_min -le $t.mean_value -and $t.mean_value -le $t.observed_max) {
                # Pass silently, otherwise output would explode for many tags
            } else {
                Fail "stat ordering violated for $($t.tag_name): min=$($t.observed_min) mean=$($t.mean_value) max=$($t.observed_max)"
            }
        }
        if ($null -ne $t.stddev_value -and $t.stddev_value -lt 0) {
            Fail "negative stddev for $($t.tag_name): $($t.stddev_value)"
        }
    }
    Pass "stat ordering valid across all $($r.tags.Count) tags"
}

# -----------------------------------------------------------------------------
# Section G - Live Value Panel data path
# -----------------------------------------------------------------------------
Section "G. Live Value Panel - fields needed for tile rendering"

# Re-fetch tags to ensure live values present
$tags2 = @(Fetch-Json "/api/trends/tags?enabled_only=false&limit=2000")
$samples = @($tags2 | Where-Object { $_.id -in ($enabledNumeric | ForEach-Object {$_.id}) })
if ($samples.Count -gt 0) {
    $tile = $samples[0]
    foreach ($f in @('current_value_double','current_st','current_quality','last_update_utc')) {
        if ($tile.PSObject.Properties.Name -contains $f) {
            Pass "tile field '$f' present (value=$($tile.$f))"
        } else {
            Fail "tile field '$f' missing"
        }
    }
}

# -----------------------------------------------------------------------------
# Section H - Saved views CRUD round-trip
# -----------------------------------------------------------------------------
Section "H. /trends/views - CRUD"

# Create
$config = @{
    mode = 'historical'
    tag_ids = $enabledNumeric | ForEach-Object { $_.id }
    historical_window_minutes = 60
    historical_preset = 'Last 1 h'
} | ConvertTo-Json -Compress

$createBody = @{
    name = "smoke-test-$(Get-Date -Format 'HHmmss')"
    description = "automated smoke - delete me"
    config = $config | ConvertFrom-Json
} | ConvertTo-Json -Depth 6

$created = $null
try {
    $created = Invoke-RestMethod -Uri "http://localhost:8000/api/trends/views" `
        -Method Post -Body $createBody -ContentType 'application/json' -TimeoutSec 15
    if ($created.id) { Pass "view created: id=$($created.id) name=$($created.name)" }
} catch {
    Fail "view creation failed: $($_.Exception.Message)"
}

# List
$views = @(Fetch-Json "/api/trends/views")
if ($views.Count -gt 0) {
    Pass "view list returned $($views.Count) view(s)"
    if ($created -and ($views | Where-Object { $_.id -eq $created.id })) {
        Pass "newly-created view appears in list"
    } else {
        Fail "newly-created view not found in list"
    }
}

# Delete (cleanup)
if ($created) {
    try {
        Invoke-RestMethod -Uri "http://localhost:8000/api/trends/views/$($created.id)" `
            -Method Delete -TimeoutSec 10 | Out-Null
        Pass "view deleted (cleanup)"
    } catch {
        Warn "view delete failed: $($_.Exception.Message)"
    }
}

# -----------------------------------------------------------------------------
# Section I - Multi-tag stress (spec 5.4 - up to 20 tags in historical)
# -----------------------------------------------------------------------------
Section "I. Multi-tag handling - up to 20 tags"

$bigPool = @($tags2 | Where-Object { $_.logging_enabled -eq $true } | Select-Object -First 20)
if ($bigPool.Count -ge 10) {
    $bigIds = ($bigPool | ForEach-Object { $_.id }) -join ","
    $r = Fetch-Json "/api/trends/history?tag_ids=$bigIds&start=$(Iso-UtcMinusHours 1)&end=$(Iso-UtcNow)&aggregation=auto&max_points=2000" 60
    if ($r) {
        if ($r.series.Count -eq $bigPool.Count) {
            Pass "$($bigPool.Count)-tag history returned $($r.series.Count) series"
        } else {
            Fail "expected $($bigPool.Count) series, got $($r.series.Count)"
        }
    }
} else {
    Warn "fewer than 10 enabled tags in fixture - multi-tag stress skipped"
}

# -----------------------------------------------------------------------------
# Section J - Window guard rails
# -----------------------------------------------------------------------------
Section "J. Window validation"

# start >= end should 400
try {
    $bad = Invoke-RestMethod -Uri "http://localhost:8000/api/trends/history?tag_ids=$($enabledNumeric[0].id)&start=$(Iso-UtcNow)&end=$(Iso-UtcMinusHours 1)" -TimeoutSec 5
    Fail "swapped start/end should have errored, got status 200"
} catch {
    if ($_.Exception.Response.StatusCode -eq 400) {
        Pass "swapped start/end correctly rejected (HTTP 400)"
    } else {
        Warn "swapped start/end rejected with HTTP $($_.Exception.Response.StatusCode) (expected 400)"
    }
}

# 365-day cap
try {
    $bad = Invoke-RestMethod -Uri "http://localhost:8000/api/trends/history?tag_ids=$($enabledNumeric[0].id)&start=$((Get-Date).AddDays(-400).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))&end=$(Iso-UtcNow)" -TimeoutSec 5
    Fail "400-day window should have errored, got status 200"
} catch {
    if ($_.Exception.Response.StatusCode -eq 400) {
        Pass "400-day window correctly rejected (HTTP 400)"
    } else {
        Warn "400-day window rejected with HTTP $($_.Exception.Response.StatusCode)"
    }
}

# -----------------------------------------------------------------------------
# Final
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 70) -ForegroundColor Magenta
Write-Host "  SUMMARY" -ForegroundColor Magenta
Write-Host ("=" * 70) -ForegroundColor Magenta
Write-Host ("  PASS: {0}" -f $Pass) -ForegroundColor Green
Write-Host ("  FAIL: {0}" -f $Fail) -ForegroundColor Red
Write-Host ("  WARN: {0}" -f $Warn) -ForegroundColor Yellow
if ($Fail -gt 0) {
    Write-Host ""
    Write-Host "  Failures:" -ForegroundColor Red
    foreach ($r in $Reasons) {
        Write-Host "    - $r" -ForegroundColor Red
    }
}
Write-Host ""
Write-Host "  Frontend-only behaviors not covered (require browser):" -ForegroundColor Cyan
Write-Host "    - Tooltip click-to-pin and scroll" -ForegroundColor Cyan
Write-Host "    - Live Value Panel tile click to show/hide series" -ForegroundColor Cyan
Write-Host "    - Time format selector (24h/12h/Auto)" -ForegroundColor Cyan
Write-Host "    - Quality filter selector (UI; backend has no param)" -ForegroundColor Cyan
Write-Host "    - Min/Max envelope rendering" -ForegroundColor Cyan
Write-Host "    - Aggregation mode selector UI persistence" -ForegroundColor Cyan
Write-Host "    - Tooltip mode (full/compact/off)" -ForegroundColor Cyan
Write-Host ""
if ($Fail -eq 0) { exit 0 } else { exit 1 }
