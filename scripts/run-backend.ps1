# Always-on backend runner: starts the FastAPI backend and restarts it if it
# ever exits. Intended to be launched hidden at logon (see install-autostart.ps1)
# so the backend — and its nightly data scheduler — is always available and the
# Electron app just connects to it.
#
# Design notes, learned the hard way:
# - Logs live under LOCALAPPDATA, NOT the repo: the repo is in OneDrive, whose
#   sync locks wedged the old shared-file `*>>` redirect and froze the loop.
# - The child python owns its log files via Start-Process redirects; this
#   process never holds a log handle.
# - Liveness = raw TCP connect to 127.0.0.1:8000 (Invoke-WebRequest can hang
#   past its TimeoutSec and froze the old loop).
# - HLTV scraping needs a HEADED Chrome (Cloudflare cookies), which only works
#   in an interactive desktop session — hence logon-time, not a service.

$ErrorActionPreference = "Continue"

# Repo root = parent of this script's folder (scripts\..).
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$root = Split-Path -Parent $scriptDir
Set-Location $root

# Single-instance guard via named mutex: two watchdogs fight over the backend.
# First one in wins; the mutex dies with the process, so a crashed watchdog
# never blocks a new one.
$script:instanceMutex = New-Object System.Threading.Mutex($false, "HLTVFantasyBackendWatchdog")
if (-not $script:instanceMutex.WaitOne(0)) {
    Write-Host "Another run-backend watchdog is already running; exiting."
    exit 0
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }

# Headed browser so Cloudflare cookies persist in the Chrome profile.
$env:HLTV_HEADLESS = "0"

$logDir = Join-Path $env:LOCALAPPDATA "HLTVFantasy\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$pointer = Join-Path $logDir "backend-latest.txt"
# Keep only the newest 20 log files.
Get-ChildItem $logDir -Filter "backend-*.log*" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 20 |
    Remove-Item -Force -ErrorAction SilentlyContinue

function Test-PortOpen {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", 8000, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(1500)) { $c.EndConnect($iar); $c.Close(); return $true }
        $c.Close()
        return $false
    } catch { return $false }
}

while ($true) {
    if (Test-PortOpen) {
        # Another instance owns port 8000; wait rather than crash-looping.
        Start-Sleep -Seconds 15
        continue
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $out = Join-Path $logDir "backend-$stamp.log"
    $err = Join-Path $logDir "backend-$stamp.err.log"
    Set-Content -Path $pointer -Value $out -Encoding utf8
    try {
        $p = Start-Process -FilePath $venvPy -ArgumentList "-m", "backend.main" `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
        $p.WaitForExit()
    } catch {
        Add-Content -Path $err -Value "watchdog: failed to start backend: $_" -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 5
}
