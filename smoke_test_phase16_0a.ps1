# =============================================================================
# Phase 16.0a -- Self-describing block schemas smoke.
#
# Verifies:
#   Section 0: services healthy, no migration this phase (Python-only change)
#   Section 1: every registered block has CONFIG_SCHEMA attached
#   Section 2: schemas are well-formed (fields list, each field has
#              key/label/type, types are from the known set)
#   Section 3: specific blocks have the expected field shape
#   Section 4: GET /api/calc/block-schemas works and returns all blocks
#   Section 5: A schema-driven POST round-trip works end-to-end
# =============================================================================

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Pass = 0
$Fail = 0
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
function WaitForBackend([int]$maxSec = 60) {
    $deadline = (Get-Date).AddSeconds($maxSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" `
                -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}


# ---- Section 0 -------------------------------------------------------------
Section "0. Service health"

if (WaitForBackend -maxSec 60) {
    Pass "Backend /health responsive"
} else {
    Fail "Backend did not respond"
    throw "backend down"
}
foreach ($svc in @("backend", "postgres", "calc_evaluator")) {
    $psout = (docker compose ps $svc --format json 2>&1 | Out-String)
    if ($psout -match '"State":\s*"running"' -or $psout -match '"Status":\s*"Up') {
        Pass "$svc is up"
    } else {
        Fail "$svc not up"
    }
}


# ---- Sections 1-3: Python-level checks ------------------------------------
Section "1-3. CONFIG_SCHEMA attached to every block (via docker exec python)"

$py = @'
from app.workers.calc_blocks import BLOCK_REGISTRY
from app.workers.calc_blocks.calc_block_schemas import BLOCK_SCHEMAS

def is_(name, condition):
    print(("PASS " if condition else "FAIL ") + name, flush=True)

# === Section 1: every block has CONFIG_SCHEMA ===
KNOWN_TYPES = {
    "tag_ref", "tag_ref_list", "tag_or_constant",
    "integer", "number", "number_list", "boolean", "enum",
}

all_blocks = sorted(BLOCK_REGISTRY.keys())
is_(f"Block registry has {len(all_blocks)} blocks", len(all_blocks) >= 60)

missing_schema = []
empty_schema = []
for code in all_blocks:
    cls = BLOCK_REGISTRY[code]
    schema = getattr(cls, "CONFIG_SCHEMA", None)
    if schema is None or schema == {}:
        empty_schema.append(code)
    elif "fields" not in schema:
        missing_schema.append(code)

is_(f"All {len(all_blocks)} blocks have non-empty CONFIG_SCHEMA "
    f"(missing: {empty_schema})", len(empty_schema) == 0)
is_(f"All schemas have a 'fields' list "
    f"(malformed: {missing_schema})", len(missing_schema) == 0)

# === Section 2: schemas well-formed ===
malformed_fields = []
unknown_types = []
for code in all_blocks:
    schema = getattr(BLOCK_REGISTRY[code], "CONFIG_SCHEMA", {})
    fields = schema.get("fields", [])
    for f in fields:
        if not isinstance(f, dict):
            malformed_fields.append(f"{code}: field not a dict")
            continue
        for required_key in ("key", "label", "type"):
            if required_key not in f:
                malformed_fields.append(f"{code}: field missing '{required_key}': {f}")
        if "type" in f and f["type"] not in KNOWN_TYPES:
            unknown_types.append(f"{code}: unknown field type {f['type']!r}")

is_(f"All fields have key/label/type "
    f"(malformed: {malformed_fields[:3]})", len(malformed_fields) == 0)
is_(f"All field types are in KNOWN_TYPES "
    f"(unknown: {unknown_types[:3]})", len(unknown_types) == 0)

# === Section 3: specific blocks have expected fields ===

def check(code, expected_keys, expected_first_type):
    schema = getattr(BLOCK_REGISTRY[code], "CONFIG_SCHEMA", {})
    fields = schema.get("fields", [])
    actual_keys = [f["key"] for f in fields]
    first_type = fields[0]["type"] if fields else None
    is_(f"{code} fields = {expected_keys} (got {actual_keys})",
        actual_keys == expected_keys)
    is_(f"{code} first field type = {expected_first_type}",
        first_type == expected_first_type)

# Aggregation: just "inputs"
check("SUM_OF", ["inputs"], "tag_ref_list")
check("AVG_OF", ["inputs"], "tag_ref_list")
check("WEIGHTED_AVG", ["inputs", "weights"], "tag_ref_list")

# Selection
check("HOT_STANDBY", ["primary", "standby"], "tag_ref")
check("MUX_INDEX", ["index", "inputs"], "tag_ref")

# Conditional
check("IF_THEN_ELSE", ["condition", "then_value", "else_value"], "tag_ref")

# Comparison
check("GT", ["left", "right"], "tag_ref")
check("EQ", ["left", "right", "tolerance"], "tag_ref")

# Logical
check("AND_OF", ["inputs"], "tag_ref_list")
check("NOT", ["input"], "tag_ref")

# Stateful
check("TON", ["input", "preset_ms"], "tag_ref")
check("R_TRIG", ["input"], "tag_ref")
check("SR", ["set", "reset"], "tag_ref")
check("CTD", ["count_down", "load", "load_value"], "tag_ref")

# Arithmetic
check("ADD", ["left", "right"], "tag_ref")
check("DIV", ["left", "right"], "tag_ref")
check("ABS", ["input"], "tag_ref")
check("LOG10", ["input"], "tag_ref")

# === Verify tag_or_constant fields appear where expected ===
arithmetic_binary = ["ADD", "SUB", "MUL", "DIV", "MOD", "POW", "MIN_OF_TWO", "MAX_OF_TWO"]
comparison = ["GT", "LT", "GTE", "LTE", "EQ", "NE"]
for code in arithmetic_binary + comparison:
    schema = getattr(BLOCK_REGISTRY[code], "CONFIG_SCHEMA", {})
    types = [f["type"] for f in schema.get("fields", [])]
    is_(f"{code} has a tag_or_constant field", "tag_or_constant" in types)

# === Verify bool filters appear on bool-only fields ===
bool_input_blocks = ["NOT", "R_TRIG", "F_TRIG", "TON", "TOF", "TP"]
for code in bool_input_blocks:
    schema = getattr(BLOCK_REGISTRY[code], "CONFIG_SCHEMA", {})
    f = schema["fields"][0]
    filter_ok = f.get("filter", {}).get("data_type") == ["bool"]
    is_(f"{code}.{f['key']} has bool filter", filter_ok)
'@

$savedPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$pyOutput = ($py | docker compose exec -T backend python -u 2>&1) | Out-String
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $savedPref

Write-Host $pyOutput

if ($exitCode -ne 0) {
    Fail "Python script exited with code $exitCode"
} else {
    $passLines = ([regex]::Matches($pyOutput, "(?m)^PASS ")).Count
    $failLines = ([regex]::Matches($pyOutput, "(?m)^FAIL ")).Count
    $script:Pass += $passLines
    $script:Fail += $failLines
    foreach ($m in [regex]::Matches($pyOutput, "(?m)^FAIL (.+)$")) {
        $script:Reasons += $m.Groups[1].Value
    }
}


# ---- Section 4: API endpoint --------------------------------------------
Section "4. GET /api/calc/block-schemas"

try {
    $schemas = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/block-schemas" `
        -UseBasicParsing -ErrorAction Stop
    Pass "Endpoint returns 200"
} catch {
    Fail "Endpoint failed: $($_.Exception.Message)"
    $schemas = $null
}

if ($schemas) {
    $codes = $schemas.PSObject.Properties.Name
    if ($codes.Count -ge 60) {
        Pass "Schemas returned for $($codes.Count) blocks"
    } else {
        Fail "Only $($codes.Count) schemas returned, expected >= 60"
    }

    foreach ($code in @("ADD", "TON", "SUM_OF", "GT", "MUX_INDEX",
                        "IF_THEN_ELSE", "SR", "CTU", "LOG10", "VOTING_M_OF_N")) {
        if ($schemas.$code -and $schemas.$code.fields) {
            Pass "API returns schema for $code with $($schemas.$code.fields.Count) fields"
        } else {
            Fail "API missing or malformed schema for $code"
        }
    }
}


# ---- Section 5: Schema-driven POST round-trip ---------------------------
Section "5. Schema-driven calc_def creation round-trip"

# Use ABS - simplest schema (just one tag_ref field), no conflict with
# the existing TON on tag 177 since we use a different output tag if
# available. We'll pick an output by querying current calc_defs.

try {
    $existing = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions" `
        -UseBasicParsing
    $used_output_tags = $existing | ForEach-Object { $_.tag_id }
} catch {
    $used_output_tags = @()
}

# Find a tag we can write to that isn't already a calc output.
# Tag 177 (Write_Coil) might be owned by Phase 15.5's TON. Try 178 as backup.
$candidate_output = 177
if ($used_output_tags -contains 177) { $candidate_output = 178 }
# If 178 also taken, we'll get a 409 and report it.

if ($schemas -and $schemas.ABS) {
    $abs_schema = $schemas.ABS
    # The schema told us ABS needs one field: key="input", type="tag_ref".
    # Build block_config from the schema's field structure.
    $block_config = @{}
    foreach ($f in $abs_schema.fields) {
        if ($f.key -eq "input") {
            $block_config[$f.key] = 1000   # ANAL_CAL_FLAG, known to exist
        }
    }

    $body = @{
        tag_id            = $candidate_output
        block_type        = "ABS"
        block_config      = $block_config
        enabled           = $true
        execution_rate_ms = 1000
    } | ConvertTo-Json

    try {
        $created = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions" `
            -Method POST -ContentType "application/json" -Body $body -ErrorAction Stop
        Pass "Created ABS calc_def id=$($created.id) via schema-derived config"

        # Cleanup
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/calc/definitions/$($created.id)" `
            -Method DELETE -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
        Pass "Cleaned up test calc_def $($created.id)"
    } catch {
        # If 409, output tag is in use. That's not a schema failure - it's
        # the same lesson #8 issue. Report informatively.
        $msg = $_.Exception.Message
        if ($msg -match "409") {
            Fail "Output tag $candidate_output already in use (lesson #8 - tag conflict)"
        } else {
            Fail "POST failed: $msg"
        }
    }
}


# ---- Summary ----
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
Write-Host "Phase 16.0a self-describing schemas verified." -ForegroundColor Cyan
Write-Host "Next: Phase 16.0b frontend renderer." -ForegroundColor Cyan
exit 0
