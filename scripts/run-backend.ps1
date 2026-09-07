# Always-on backend runner: starts the FastAPI backend and restarts it if it
# ever exits. Intended to be launched hidden at logon (see install-autostart.ps1)
# so the backend — and its nightly data scheduler — is always available and the
# Electron app just connects to it.
#
# Design notes, learned the hard way:
# - Logs live under LOCALAPPDATA, NOT the repo: when the repo lived in OneDrive,
#   sync locks wedged the old shared-file `*>>` redirect and froze the loop.
# - The child python owns its log files via Start-Process redirects; this
#   process never holds a log handle.
# - Liveness = memorized pid alive + raw TCP connect to the memorized port
#   (Invoke-WebRequest can hang past its TimeoutSec and froze the old loop).
# - The backend picks its own port: memorized one first, else the next free
#   port, written to .runtime\backend-port.json (helpers in backend-port.ps1).
#   Nothing here assumes 8000.
# - HLTV scraping runs in HEADLESS Chrome (verified 2026-09-07 to pass
#   Cloudflare), so no interactive desktop is required any more.

$ErrorActionPreference = "Continue"

# Repo root = parent of this script's folder (scripts\..).
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$root = Split-Path -Parent $scriptDir
Set-Location $root
. (Join-Path $scriptDir "backend-port.ps1")

# Single-instance guard via named mutex: two watchdogs fight over the backend.
# First one in wins; the mutex dies with the process, so a crashed watchdog
# never blocks a new one. "Global\" so the SYSTEM service (session 0) and a
# logon-session launcher see the same mutex.
$created = $false
try {
    $script:instanceMutex = New-Object System.Threading.Mutex($false, "Global\HLTVFantasyBackendWatchdog", [ref]$created)
} catch {
    $script:instanceMutex = New-Object System.Threading.Mutex($false, "HLTVFantasyBackendWatchdog", [ref]$created)
}
if (-not $script:instanceMutex.WaitOne(0)) {
    Write-Host "Another run-backend watchdog is already running; exiting."
    exit 0
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }

# Running as the SYSTEM service (scripts\install-service.ps1)? The interactive
# user's Chrome profile is undecryptable for SYSTEM and LOCALAPPDATA points into
# the system profile, so use service-owned locations under ProgramData instead.
# The HLTV login reaches that profile via scripts\hltv-login-handoff.ps1.
$isSystem = [Security.Principal.WindowsIdentity]::GetCurrent().IsSystem
$serviceRoot = Join-Path $env:ProgramData "HLTVFantasy"
if ($isSystem) {
    $env:HLTV_PROFILE_DIR = Join-Path $serviceRoot "chrome-profile"
    New-Item -ItemType Directory -Force -Path $env:HLTV_PROFILE_DIR | Out-Null
}

# Headless Chrome: passes Cloudflare without a desktop session (see memory note
# 2026-09-07). Set to "0" only if HLTV starts serving interactive challenges.
$env:HLTV_HEADLESS = "1"

if ($isSystem) {
    $logDir = Join-Path $serviceRoot "logs"
} else {
    $logDir = Join-Path $env:LOCALAPPDATA "HLTVFantasy\logs"
}
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$pointer = Join-Path $logDir "backend-latest.txt"
# Keep only the newest 20 log files.
Get-ChildItem $logDir -Filter "backend-*.log*" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 20 |
    Remove-Item -Force -ErrorAction SilentlyContinue

while ($true) {
    if (Test-BackendAlive) {
        # The memorized backend is alive on its memorized port; wait rather
        # than crash-looping. (A duplicate launch would exit by itself anyway:
        # the backend probes /health before picking another port.)
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
