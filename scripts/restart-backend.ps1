# Restart the always-on backend so it picks up new code. The autostart watchdog
# (run-backend.ps1) brings it back within a few seconds; if autostart isn't
# installed this just stops it. Run:  .\scripts\restart-backend.ps1

$ErrorActionPreference = "Continue"

try {
    $listeners = netstat -ano | Select-String "127.0.0.1:8000" | Where-Object { $_.Line -match "LISTENING" }
    $stopped = $false
    foreach ($line in $listeners) {
        $parts = ($line -replace "\s+", " ").Trim().Split(" ")
        if ($parts.Length -ge 5) {
            $procId = [int]$parts[-1]
            if ($procId -gt 0) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped backend process $procId; watchdog will restart it with the latest code."
                $stopped = $true
            }
        }
    }
    if (-not $stopped) { Write-Host "No backend was running on port 8000." }
} catch {
    Write-Host "Could not restart the backend automatically."
}
