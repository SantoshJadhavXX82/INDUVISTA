# commit_push.ps1 - safe "commit and push" for INDUVISTA.
#
# Usage (from anywhere):
#     D:\INDUVISTA\scripts\commit_push.ps1 "your commit message"
#
# Refuses to commit if .env / keys are tracked, or if a known secret pattern
# appears in the staged diff, so credentials never reach GitHub.
param([Parameter(Mandatory = $true)][string]$Message)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")   # repo root

# 1) secret files must not be tracked
$tracked = git ls-files | Select-String -Pattern '(^|/)(\.env|.*\.pem|.*\.key)$'
if ($tracked) {
    Write-Host "REFUSING: a secret file is tracked by git:" -ForegroundColor Red
    $tracked | ForEach-Object { Write-Host ("  " + $_) }
    Write-Host "Fix: git rm --cached <file>   (keeps the local copy)" -ForegroundColor Yellow
    exit 1
}

git add -A

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

# 3) nothing to do?
$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "Nothing to commit - working tree clean." -ForegroundColor DarkGray
    exit 0
}

# 4) commit and push
git commit -m $Message
git push origin HEAD
Write-Host "Done. Committed and pushed to origin:" -ForegroundColor Green
Write-Host ("  " + $Message) -ForegroundColor Green
