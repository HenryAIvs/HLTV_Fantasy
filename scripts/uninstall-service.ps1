# Remove the 24/7 SYSTEM service task installed by install-service.ps1 and stop
# the backend it runs. Needs admin (re-launches itself with a UAC prompt).
#     .\scripts\uninstall-service.ps1
# To go back to the logon-time launcher afterwards: .\scripts\install-autostart.ps1
# (Power settings are left as they are; `powercfg /change standby-timeout-ac 25`
# restores the 25-minute sleep timeout if you want it back.)

$ErrorActionPreference = "Continue"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Administrator rights are required to remove the SYSTEM task; re-launching with a UAC prompt..."
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`""
    exit
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$root = Split-Path -Parent $scriptDir
Set-Location $root
. (Join-Path $scriptDir "backend-port.ps1")

$taskName = "HLTVFantasyBackend"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed scheduled task '$taskName'."
} else {
    Write-Host "No scheduled task '$taskName' found (nothing to remove)."
}

# The task's watchdog loop and backend may still be alive; stop them.
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run-backend.ps1*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Stopped watchdog loop $($_.ProcessId)." }
$stopped = Stop-BackendProcesses
foreach ($procId in $stopped) { Write-Host "Stopped backend process $procId." }

Write-Host "Service removed. The backend will no longer start at boot."
