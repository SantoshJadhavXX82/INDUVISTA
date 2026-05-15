<#
.SYNOPSIS
  Uninstall the InduVista host-stats agent's Scheduled Task.

.DESCRIPTION
  Stops and removes the "InduVistaHostAgent" task. Does NOT uninstall psutil
  or any other Python packages -- those are user-scope pip installs and can
  stay around harmlessly.

  After this, the Diagnostics page reverts to scope='container' fallback
  within ~30 seconds.
#>

$ErrorActionPreference = "Stop"
$TaskName = "InduVistaHostAgent"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "No task named '$TaskName' is registered. Nothing to do."
    exit 0
}

Write-Host "Stopping '$TaskName'..."
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Write-Host "Unregistering '$TaskName'..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

Write-Host "Done. The Diagnostics page will revert to scope='container' shortly."
