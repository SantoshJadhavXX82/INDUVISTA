<#
.SYNOPSIS
  Install the InduVista host-stats agent as a Windows Scheduled Task.

.DESCRIPTION
  Registers a Scheduled Task that:
    - Runs pythonw.exe agent.py at every user logon (no admin needed)
    - Has no visible console window (pythonw, not python)
    - Restarts automatically on crash (up to 999 times, 1 min apart)
    - Survives PowerShell / cmd / Explorer closing -- it's session-independent
    - Shows up in Task Scheduler ("InduVistaHostAgent") so you can see/manage it

  Run this script once. Then you can close every terminal window -- the agent
  stays running, and the Diagnostics page stays on scope='host'.

.EXAMPLE
  cd D:\INDUVISTA\host_agent
  .\install-windows.ps1

.NOTES
  Re-running this script is safe -- it deletes any prior task and re-creates it,
  so you can use it to "reinstall" after Python version upgrades or moving the
  host_agent folder.
#>

$ErrorActionPreference = "Stop"

$TaskName    = "InduVistaHostAgent"
$AgentDir    = $PSScriptRoot                              # this folder
$AgentScript = Join-Path $AgentDir "agent.py"
$ReqsFile    = Join-Path $AgentDir "requirements.txt"

Write-Host ""
Write-Host "=== InduVista host agent -- install ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Sanity ----------------------------------------------------------------
if (-not (Test-Path $AgentScript)) {
    throw "agent.py not found at $AgentScript. Run this from the host_agent folder."
}
if (-not (Test-Path $ReqsFile)) {
    throw "requirements.txt not found at $ReqsFile."
}

# --- 2. Find Python (prefer pythonw for hidden execution) --------------------
# pythonw.exe is the *windowless* Python interpreter -- runs without spawning a
# console window. Every standard Python install on Windows ships it next to
# python.exe. We use python.exe for the pip install step (where we want to see
# errors), then pythonw.exe for the long-running task.
$python  = (Get-Command python  -ErrorAction SilentlyContinue).Source
$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source

if (-not $python) {
    throw @"
No 'python' on PATH.

Install Python first -- easiest options:
  - Microsoft Store: search 'Python 3.12', click Install (pip pre-enabled)
  - https://www.python.org/downloads/  (check 'Add Python to PATH' during install)

Then re-run this script.
"@
}

if (-not $pythonw) {
    # Some installs (uncommon) omit pythonw.exe. Fall back to python.exe -- the
    # task will work but flash a console window briefly at logon. Better than
    # nothing.
    Write-Warning "pythonw.exe not found; falling back to python.exe (may flash a console window at logon)."
    $pythonw = $python
}

Write-Host "Python (interactive): $python"
Write-Host "Python (windowless ): $pythonw"

# --- 3. Ensure pip + install deps --------------------------------------------
Write-Host ""
Write-Host "Ensuring pip is available..."
# `ensurepip` is part of stdlib since Python 3.4. On official python.org
# installs without "pip" checked, this is the one-liner that fixes it.
& $python -m ensurepip --upgrade --default-pip 2>&1 | Out-Null

Write-Host "Installing dependencies (user scope -- no admin required)..."
& $python -m pip install --user --upgrade -r $ReqsFile
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed. Check the output above."
}

# --- 4. Replace any existing task --------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host ""
    Write-Host "Existing task found -- removing it so we can re-create cleanly."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# --- 5. Register the new task ------------------------------------------------
# AtLogon: starts when YOU log in (your account, your processes).
# If you want it running even before any user logs in (e.g. server scenario),
# switch the trigger to AtStartup and add `-User "SYSTEM"` to Register-ScheduledTask.
$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument "`"$AgentScript`"" `
    -WorkingDirectory $AgentDir

$trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)        # 0 = "no limit"

# Run as the current user (interactive token, can read its own processes).
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "InduVista host-stats agent -- posts CPU/RAM/disk/GPU to backend every 5s." | Out-Null

Write-Host ""
Write-Host "Registered Scheduled Task '$TaskName'." -ForegroundColor Green

# --- 6. Start it now ---------------------------------------------------------
Start-ScheduledTask -TaskName $TaskName
Write-Host "Started. Waiting 10s for first push..."
Start-Sleep -Seconds 10

# --- 7. Verify the backend now sees scope='host' -----------------------------
try {
    $s = Invoke-RestMethod -Uri "http://localhost:8000/api/diagnostics/system-stats" -TimeoutSec 5
    if ($s.scope -eq "host") {
        Write-Host ""
        Write-Host "[OK] Backend now reads scope=host" -ForegroundColor Green
        Write-Host "  Hostname  : $($s.hostname)"
        Write-Host "  Platform  : $($s.platform)"
        Write-Host "  Memory    : $([Math]::Round($s.memory.used_bytes/1GB,1)) GB used / $([Math]::Round($s.memory.total_bytes/1GB,1)) GB total"
        Write-Host "  Drives    : $($s.disks.Count) ($(($s.disks | ForEach-Object {$_.mountpoint}) -join ', '))"
        Write-Host "  GPUs      : $($s.gpus.Count)"
        Write-Host "  Processes : $($s.top_processes.Count)"
    } else {
        Write-Host ""
        Write-Host "[!] Backend still reports scope=$($s.scope). The task is running but the push hasn't landed yet." -ForegroundColor Yellow
        Write-Host "  Check task status:  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
        Write-Host "  Try again in 15s:   Invoke-RestMethod http://localhost:8000/api/diagnostics/system-stats | Select scope"
    }
} catch {
    Write-Host ""
    Write-Host "[!] Couldn't reach the backend on http://localhost:8000 -- is it running?" -ForegroundColor Yellow
    Write-Host "  The task is registered and will keep trying. Once the backend is up, the page will flip to scope=host."
}

Write-Host ""
Write-Host "What you've got now:"
Write-Host "  - Agent auto-starts every time you log in (no PowerShell required)."
Write-Host "  - Restarts within 1 minute if it crashes."
Write-Host "  - Closing terminals / signing out / signing back in: agent keeps running."
Write-Host ""
Write-Host "To stop temporarily : Stop-ScheduledTask -TaskName $TaskName"
Write-Host "To start again      : Start-ScheduledTask -TaskName $TaskName"
Write-Host "To remove entirely  : .\uninstall-windows.ps1"
Write-Host "To inspect / edit   : taskschd.msc  ->  Task Scheduler Library  ->  $TaskName"
Write-Host ""
