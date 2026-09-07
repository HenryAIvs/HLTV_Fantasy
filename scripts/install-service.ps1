# Make the HLTV Fantasy backend a true 24/7 service: a Task Scheduler task that
# runs scripts\run-backend.ps1 as SYSTEM at boot, before anyone signs in, and
# restarts it if it ever dies. No password is stored, so it survives account
# password changes and Hello-only sign-in. Also turns off sleep on AC power.
#
# Needs admin once (it re-launches itself with a UAC prompt). Run:
#     .\scripts\install-service.ps1
# Afterwards hand the HLTV login to the service (it has its own Chrome profile):
#     .\scripts\hltv-login-handoff.ps1
# Remove with:  .\scripts\uninstall-service.ps1
#
# Design notes:
# - SYSTEM cannot decrypt your Chrome profile (DPAPI is per user), so the
#   service scrapes with its own profile under C:\ProgramData\HLTVFantasy and
#   logs there too (run-backend.ps1 detects SYSTEM and switches both).
# - HLTV scraping is headless (verified to pass Cloudflare), so no desktop is
#   needed; the only thing needing a human is the HLTV sign-in captcha, which
#   the hand-off script solves from your session and posts to the service.
# - The task supersedes the logon-time launcher (install-autostart.ps1); that
#   launcher is removed here so two watchdogs never compete.

$ErrorActionPreference = "Stop"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Administrator rights are required to register a SYSTEM task; re-launching with a UAC prompt..."
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`""
    exit
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$root = Split-Path -Parent $scriptDir
Set-Location $root
. (Join-Path $scriptDir "backend-port.ps1")

$taskName = "HLTVFantasyBackend"
$runner = Join-Path $root "scripts\run-backend.ps1"
$serviceRoot = Join-Path $env:ProgramData "HLTVFantasy"

# --- venv + deps, so the service works from a clean box.
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating Python virtual environment..."
    python -m venv (Join-Path $root ".venv")
}
if (-not (Test-Path $venvPy)) { throw "venv Python not found at $venvPy (is Python installed and on PATH?)." }
Write-Host "Installing backend dependencies..."
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $root "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed (exit $LASTEXITCODE)." }

# --- Retire the logon-time launcher and whatever it is running.
$vbsPath = Join-Path ([Environment]::GetFolderPath("Startup")) "HLTV-Fantasy-Backend.vbs"
if (Test-Path $vbsPath) {
    Remove-Item $vbsPath -Force
    Write-Host "Removed logon-time launcher $vbsPath (the service replaces it)."
}
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run-backend.ps1*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Stopped watchdog loop $($_.ProcessId)." }
$stopped = Stop-BackendProcesses
foreach ($procId in $stopped) { Write-Host "Stopped backend process $procId." }

# --- Never sleep on AC power: the scheduler cannot fire while the PC sleeps.
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
Write-Host "Sleep and hibernate on AC power disabled (display timeout unchanged)."

# --- Service-owned locations (profile + logs) under ProgramData.
New-Item -ItemType Directory -Force -Path (Join-Path $serviceRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $serviceRoot "chrome-profile") | Out-Null

# --- Register the task.
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd `
    -StartWhenAvailable -MultipleInstances IgnoreNew -Hidden `
    -RestartCount 30 -RestartInterval (New-TimeSpan -Minutes 1)
# PT0S = no execution time limit (the cmdlet's TimeSpan.Zero does not always stick).
$settings.ExecutionTimeLimit = "PT0S"
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "HLTV Fantasy always-on backend (watchdog + FastAPI + nightly data scheduler). Installed by scripts\install-service.ps1." `
    -Force | Out-Null
Write-Host "Registered scheduled task '$taskName' (runs as SYSTEM at startup)."

# --- Start it now and wait for the backend to memorize its port.
Remove-Item (Join-Path $root ".runtime\backend-port.json") -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $taskName
$up = $false
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-BackendAlive) { $up = $true; break }
}
if ($up) {
    Write-Host "Backend is up as SYSTEM at $(Get-BackendUrl)." -ForegroundColor Green
    Write-Host "Logs: $serviceRoot\logs   Chrome profile: $serviceRoot\chrome-profile"
    Write-Host ""
    Write-Host "NEXT STEP (as your normal user, once): .\scripts\hltv-login-handoff.ps1" -ForegroundColor Yellow
    Write-Host "It signs the service's browser in to HLTV, which the trigger-rates backfill needs."
} else {
    Write-Host "The task started but the backend is not answering yet. Check $serviceRoot\logs (newest backend-*.err.log)." -ForegroundColor Yellow
    Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, LastTaskResult | Format-List
}
