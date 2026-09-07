# Restart the always-on backend so it picks up new code. The watchdog
# (run-backend.ps1, whether launched at logon or by the SYSTEM service task)
# brings it back within a few seconds. Run:  .\scripts\restart-backend.ps1
#
# Order of attempts:
#   1. POST /admin/restart on the memorized port — works even when the backend
#      runs as SYSTEM, which an unelevated shell cannot kill.
#   2. Fall back to stopping the memorized pid / any `backend.main` listener.

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "backend-port.ps1")

$url = Get-BackendUrl
$before = Get-BackendPortInfo
$requested = $false
try {
    $r = Invoke-RestMethod -Method Post -Uri "$url/admin/restart" -TimeoutSec 5
    Write-Host "Backend pid $($r.pid) is restarting; the watchdog relaunches it with the latest code."
    $requested = $true
} catch {
    Write-Host "Restart endpoint unavailable ($($_.Exception.Message)); stopping the process instead."
    try {
        $stopped = Stop-BackendProcesses
        if ($stopped.Count -gt 0) {
            Write-Host "Stopped backend process(es) $($stopped -join ', ')."
            $requested = $true
        } else {
            Write-Host "No backend was running on port $(Get-BackendPort)."
        }
    } catch {
        Write-Host "Could not restart the backend automatically: $_"
    }
}

if ($requested) {
    # Wait for the relaunched backend (new pid in the port file) to answer.
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        $now = Get-BackendPortInfo
        if ($now -and $before -and $now.pid -ne $before.pid -and (Test-BackendAlive)) {
            Write-Host "Backend is back: pid $($now.pid) at $($now.url)."
            exit 0
        }
    }
    Write-Host "Backend has not come back yet; the watchdog should relaunch it shortly (check the logs if it does not)."
}
