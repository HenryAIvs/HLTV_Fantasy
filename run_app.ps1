# Simple one-command launcher for the Electron + FastAPI app on Windows.
# Usage:  .\run_app.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# 1) Python venv + backend deps
$venvPath = Join-Path $root ".venv"
if (-not (Test-Path $venvPath)) {
    python -m venv $venvPath
}
$venvPy = Join-Path $venvPath "Scripts\python.exe"

& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt

# 2) Frontend deps and build + Electron launch
Push-Location (Join-Path $root "electron")
if (-not (Test-Path (Join-Path $pwd "node_modules"))) {
    npm install
}
npm run app
Pop-Location
