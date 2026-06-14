# commit_push.ps1 - safe "commit and push" for INDUVISTA.
#
# Usage (from anywhere):
#     C:\INDUVISTA\scripts\commit_push.ps1 "your commit message"
#
# Refuses to commit if .env / keys are tracked, or if a known secret pattern
# appears in the staged diff, so credentials never reach GitHub.
#
# Hardened (2026-06): git failures are NATIVE exit codes, not PowerShell
# errors, so $ErrorActionPreference="Stop" does NOT catch them. Every git
# call is now followed by an explicit $LASTEXITCODE check, so a rejected
# push (e.g. large-file / pre-receive hook) fails LOUDLY instead of printing
# a false "Done".
param([Parameter(Mandatory = $true)][string]$Message)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")   # repo root

function Invoke-Git {
    # Run git, stream output, and abort the script if it returns non-zero.
    param([Parameter(Mandatory = $true)][string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("ERROR: 'git " + ($GitArgs -join ' ') +
                    "' failed with exit code $LASTEXITCODE.") -ForegroundColor Red
        Write-Host "Nothing further was done. Fix the error above and retry." -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
}

# 1) secret files must not be tracked
$tracked = git ls-files | Select-String -Pattern '(^|/)(\.env|.*\.pem|.*\.key)$'
if ($tracked) {
    Write-Host "REFUSING: a secret file is tracked by git:" -ForegroundColor Red
    $tracked | ForEach-Object { Write-Host ("  " + $_) }
    Write-Host "Fix: git rm --cached <file>   (keeps the local copy)" -ForegroundColor Yellow
    exit 1
}

Invoke-Git @('add', '-A')

# 2) scan the staged diff for secret values / connection strings.
#    Exclude this script from its own scan: it legitimately contains the secret
#    PATTERNS as literal text, which would otherwise match itself.
$secretHits = git diff --cached -- . ":(exclude)scripts/commit_push.ps1" | Select-String -Pattern 'change_this_password|POSTGRES_PASSWORD=|postgresql\+psycopg2://[^@]*:[^@]*@|BEGIN .*PRIVATE KEY'
if ($secretHits) {
    Write-Host "REFUSING: possible secret in staged changes:" -ForegroundColor Red
    $secretHits | Select-Object -First 5 | ForEach-Object { Write-Host ("  " + $_) }
    Write-Host "Unstage the offending file (e.g. git reset HEAD .env) and retry." -ForegroundColor Yellow
    exit 1
}

# 2b) warn on large files about to be committed (GitHub hard limit is 100 MB).
$big = git diff --cached --name-only | ForEach-Object {
    if (Test-Path $_) {
        $f = Get-Item $_
        if ($f.Length -gt 50MB) {
            [pscustomobject]@{ Path = $_; MB = [math]::Round($f.Length / 1MB, 1) }
        }
    }
}
if ($big) {
    Write-Host "REFUSING: large file(s) staged (GitHub rejects >100 MB; warns >50 MB):" -ForegroundColor Red
    $big | ForEach-Object { Write-Host ("  {0}  ({1} MB)" -f $_.Path, $_.MB) -ForegroundColor Red }
    Write-Host "Add them to .gitignore and 'git rm --cached <file>', then retry." -ForegroundColor Yellow
    exit 1
}

# 3) nothing to do?
$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "Nothing to commit - working tree clean." -ForegroundColor DarkGray
    exit 0
}

# 4) commit and push - each checked.
Invoke-Git @('commit', '-m', $Message)
Write-Host "Committed locally. Pushing..." -ForegroundColor DarkGray
Invoke-Git @('push', 'origin', 'HEAD')

# 5) only reached if BOTH commit and push succeeded.
Write-Host "Done. Committed AND pushed to origin:" -ForegroundColor Green
Write-Host ("  " + $Message) -ForegroundColor Green
