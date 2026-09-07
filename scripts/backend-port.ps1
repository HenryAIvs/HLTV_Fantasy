# Shared helpers for finding the always-on backend. Dot-source from the other
# scripts:   . (Join-Path $PSScriptRoot "backend-port.ps1")
#
# The backend picks a free port at startup (memorized port first, else the next
# free one in 8000-8099) and memorizes it in .runtime\backend-port.json together
# with its pid — see backend/services/backend_port.py. Nothing here assumes 8000.

$script:BackendRoot = Split-Path -Parent $PSScriptRoot
$script:BackendPortFile = Join-Path $script:BackendRoot ".runtime\backend-port.json"
$script:BackendDefaultPort = 8000

function Get-BackendPortInfo {
    # Returns the memorized record (port, pid, url, ...) or $null.
    if (-not (Test-Path $script:BackendPortFile)) { return $null }
    try {
        $info = Get-Content $script:BackendPortFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $info -or -not $info.port) { return $null }
        $info.port = [int]$info.port
        return $info
    } catch { return $null }
}

function Get-BackendPort {
    $info = Get-BackendPortInfo
    if ($info) { return [int]$info.port }
    return $script:BackendDefaultPort
}

function Get-BackendUrl {
    return "http://127.0.0.1:$(Get-BackendPort)"
}

function Test-PortOpen {
    param([int]$Port = (Get-BackendPort))
    # Raw TCP connect: Invoke-WebRequest can hang past its TimeoutSec and froze
    # the old watchdog loop, so we never use it for liveness.
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(1500)) { $c.EndConnect($iar); $c.Close(); return $true }
        $c.Close()
        return $false
    } catch { return $false }
}

function Test-ProcessAlive {
    param($ProcId)
    if (-not $ProcId) { return $false }
    try { $null = Get-Process -Id ([int]$ProcId) -ErrorAction Stop; return $true } catch { return $false }
}

function Test-BackendAlive {
    # True when the memorized backend process is alive AND its port answers.
    $info = Get-BackendPortInfo
    if (-not $info) { return $false }
    return ((Test-ProcessAlive $info.pid) -and (Test-PortOpen $info.port))
}

function Stop-BackendProcesses {
    # Stops the memorized backend by pid, plus anything else that is a
    # `python -m backend.main` listening on the memorized port (or legacy 8000).
    # Only processes whose command line is our backend module are touched.
    $stopped = @()
    $info = Get-BackendPortInfo
    $candidates = @()
    if ($info -and $info.pid) { $candidates += [int]$info.pid }
    $ports = @((Get-BackendPort), $script:BackendDefaultPort) | Select-Object -Unique
    foreach ($port in $ports) {
        $listeners = netstat -ano | Select-String "127.0.0.1:$port " | Where-Object { $_.Line -match "LISTENING" }
        foreach ($line in $listeners) {
            $parts = ($line -replace "\s+", " ").Trim().Split(" ")
            if ($parts.Length -ge 5) { $candidates += [int]$parts[-1] }
        }
    }
    foreach ($procId in ($candidates | Select-Object -Unique)) {
        if ($procId -le 0) { continue }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        if ($proc.CommandLine -notlike "*backend.main*") { continue }
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        $stopped += $procId
    }
    return $stopped
}
