# Simple one-command launcher for the Electron + FastAPI app on Windows.
# Usage:  .\run_app.ps1
#
# Hardened against the VS Code Python-extension venv auto-activation that can
# interrupt startup: Vite and Electron are invoked through Node / the Electron
# exe directly rather than via cmd.exe batch shims (npm.cmd / electron.cmd).
# Those shims are what surface "Terminate batch job (Y/N)?" when a Ctrl+C is
# injected mid-build. .vscode/settings.json also disables that auto-activation.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

function Stop-StaleBackend {
    # Free 127.0.0.1:8000 so a previous backend doesn't block the new one.
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
        # Non-fatal: continue startup even if netstat parsing fails.
    }
}
Stop-StaleBackend

# --- 1) Python venv + backend deps -----------------------------------------
$venvPath = Join-Path $root ".venv"
$venvPy = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating Python virtual environment..."
    python -m venv $venvPath
}
if (-not (Test-Path $venvPy)) {
    throw "venv Python not found at $venvPy (is Python installed and on PATH?)."
}

# HLTV scraping default: headed browser so Cloudflare cookies persist in the profile.
$env:HLTV_HEADLESS = "0"

Write-Host "Installing backend dependencies..."
& $venvPy -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "pip self-upgrade failed (exit $LASTEXITCODE)." }
& $venvPy -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed (exit $LASTEXITCODE)." }

# --- 2) Frontend deps + build (via Node directly, no cmd.exe shim) ----------
$electronDir = Join-Path $root "electron"
Push-Location $electronDir
try {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw "Node.js not found on PATH. Install Node.js, then re-run."
    }

    if (-not (Test-Path (Join-Path $pwd "node_modules"))) {
        Write-Host "Installing frontend dependencies (first run)..."
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit $LASTEXITCODE)." }
    }

    $viteJs = Join-Path $pwd "node_modules\vite\bin\vite.js"
    if (-not (Test-Path $viteJs)) {
        throw "Vite not found at $viteJs. Delete electron\node_modules and re-run to reinstall."
    }

    Write-Host "Building renderer..."
    $buildOk = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        & node $viteJs build
        if ($LASTEXITCODE -eq 0) { $buildOk = $true; break }
        Write-Host "Build attempt $attempt failed (exit $LASTEXITCODE); retrying in 2s..."
        Start-Sleep -Seconds 2
    }
    if (-not $buildOk) { throw "Renderer build failed after 3 attempts." }

    # --- 3) Launch Electron directly (its real exe, not electron.cmd) -------
    $electronExe = Join-Path $pwd "node_modules\electron\dist\electron.exe"
    if (-not (Test-Path $electronExe)) {
        throw "Electron binary not found at $electronExe. Delete electron\node_modules and re-run to reinstall."
    }
    Write-Host "Launching app..."
    & $electronExe .
}
finally {
    Pop-Location
}
