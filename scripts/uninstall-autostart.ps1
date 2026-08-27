# Disable backend auto-start: removes the Startup launcher and stops any running
# backend on port 8000. Run:  .\scripts\uninstall-autostart.ps1

$ErrorActionPreference = "Continue"

$startup = [Environment]::GetFolderPath("Startup")
$vbsPath = Join-Path $startup "HLTV-Fantasy-Backend.vbs"
if (Test-Path $vbsPath) {
    Remove-Item $vbsPath -Force
    Write-Host "Removed autostart launcher: $vbsPath"
} else {
    Write-Host "No autostart launcher found (nothing to remove)."
}

# Free port 8000 so the always-on backend actually stops.
try {
    $listeners = netstat -ano | Select-String "127.0.0.1:8000" | Where-Object { $_.Line -match "LISTENING" }
    foreach ($line in $listeners) {
        $parts = ($line -replace "\s+", " ").Trim().Split(" ")
        if ($parts.Length -ge 5) {
            $procId = [int]$parts[-1]
            if ($procId -gt 0) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped backend process $procId."
            }
        }
    }
} catch {
    Write-Host "Could not stop the backend automatically; it will not restart after the next logout."
}

# Also stop the watchdog PowerShell loop if it's still alive (it would restart
# the backend otherwise). Best-effort match on the runner script name.
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run-backend.ps1*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped watchdog loop $($_.ProcessId)."
    }

Write-Host "Auto-start disabled."
