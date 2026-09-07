# Disable backend auto-start: removes the Startup launcher and stops any running
# backend (on whatever port it memorized). Run:  .\scripts\uninstall-autostart.ps1

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "backend-port.ps1")

$startup = [Environment]::GetFolderPath("Startup")
$vbsPath = Join-Path $startup "HLTV-Fantasy-Backend.vbs"
if (Test-Path $vbsPath) {
    Remove-Item $vbsPath -Force
    Write-Host "Removed autostart launcher: $vbsPath"
} else {
    Write-Host "No autostart launcher found (nothing to remove)."
}

# Stop the watchdog PowerShell loop FIRST (it would restart the backend
# otherwise). Best-effort match on the runner script name.
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run-backend.ps1*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped watchdog loop $($_.ProcessId)."
    }

# Then stop the backend itself (memorized pid / port, never assumes 8000).
try {
    $stopped = Stop-BackendProcesses
    foreach ($procId in $stopped) { Write-Host "Stopped backend process $procId." }
} catch {
    Write-Host "Could not stop the backend automatically; it will not restart after the next logout."
}

Write-Host "Auto-start disabled."
