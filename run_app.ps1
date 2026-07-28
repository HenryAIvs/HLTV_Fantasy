# Simple one-command launcher for the Electron + FastAPI app on Windows.
# Usage:  .\run_app.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Ensure no stale backend is already bound to 127.0.0.1:8000
try {
    $listeners = netstat -ano | Select-String "127.0.0.1:8000" | Where-Object { $_.Line -match "LISTENING" }
    foreach ($line in $listeners) {
        $parts = ($line -replace "\s+", " ").Trim().Split(" ")
        if ($parts.Length -ge 5) {
            $procId = [int]$parts[-1]
            if ($procId -gt 0) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        }
    }
} catch {
    # Non-fatal: continue startup even if netstat parsing fails
}

# 1) Python venv + backend deps
$venvPath = Join-Path $root ".venv"
if (-not (Test-Path $venvPath)) {
    python -m venv $venvPath
}
$venvPy = Join-Path $venvPath "Scripts\python.exe"

# HLTV scraping default: headed browser so Cloudflare cookies persist in the profile.
$env:HLTV_HEADLESS = "0"

& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt

# 2) Frontend deps and build + Electron launch
Push-Location (Join-Path $root "electron")
if (-not (Test-Path (Join-Path $pwd "node_modules"))) {
    npm install
}
$buildOk = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    cmd /c npm run -s renderer:build
    if ($LASTEXITCODE -eq 0) {
        $buildOk = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $buildOk) {
    throw "Renderer build failed after 3 attempts."
}

# Launch Electron directly to avoid flaky nested npm script spawning on some Windows setups.
cmd /c .\node_modules\.bin\electron.cmd .
Pop-Location
