# Exposes the local backend to the internet through a Cloudflare Tunnel as an
# always-on Windows service - no port forwarding, TLS handled by Cloudflare,
# works on a dynamic IP.
#
# Prerequisites: a domain on Cloudflare (free plan is fine) and cloudflared
# (winget install Cloudflare.cloudflared). Run once from an elevated PowerShell:
#
#   .\scripts\install-tunnel.ps1 -Hostname api.your-domain.com
#
# It logs you into Cloudflare (browser - pick the zone that owns the hostname),
# creates the tunnel, routes the hostname to it, writes the config and installs
# the service. Safe to re-run: existing pieces are reused and the DNS record is
# updated in place. Afterwards put https://api.your-domain.com in site/api.json
# and electron/public-config.json.
#
# The service runs with an explicit --config and --logfile under ProgramData.
# Relying on the SYSTEM account's profile folder (cloudflared's default lookup)
# did not work on the operator PC and left nothing readable to debug.
param(
    [Parameter(Mandatory = $true)] [string] $Hostname,
    [string] $TunnelName = "hltv-fantasy",
    [int] $BackendPort = 8000,
    [string] $ServiceDir = "C:\ProgramData\HLTVFantasy\cloudflared",
    [string] $LogFile = "C:\ProgramData\HLTVFantasy\logs\cloudflared.log"
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from a PowerShell opened as Administrator - installing the service needs it."
}

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Host "cloudflared not found. Installing with winget..." -ForegroundColor Yellow
    winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
    $cloudflared = Get-Command cloudflared -ErrorAction Stop
}
$exe = $cloudflared.Source

# --- 1) Cloudflare login (once) -------------------------------------------
$cfDir = Join-Path $env:USERPROFILE ".cloudflared"
if (-not (Test-Path (Join-Path $cfDir "cert.pem"))) {
    Write-Host "Logging in to Cloudflare (a browser window opens; pick the domain that owns $Hostname)..."
    & $exe tunnel login
}

# --- 2) Tunnel (reuse by name, else create) --------------------------------
$existing = (& $exe tunnel list --output json | ConvertFrom-Json) | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1
if (-not $existing) {
    & $exe tunnel create $TunnelName | Out-Host
    $existing = (& $exe tunnel list --output json | ConvertFrom-Json) | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1
}
$tunnelId = $existing.id
$credentials = Join-Path $cfDir "$tunnelId.json"
if (-not (Test-Path $credentials)) {
    throw "Tunnel '$TunnelName' ($tunnelId) exists but its credentials file $credentials is missing. Delete the tunnel under Zero Trust > Networks > Tunnels and re-run."
}

# --- 3) Config for the service, under ProgramData --------------------------
New-Item -ItemType Directory -Force -Path $ServiceDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
$serviceCredentials = Join-Path $ServiceDir "$tunnelId.json"
Copy-Item $credentials $serviceCredentials -Force
$configPath = Join-Path $ServiceDir "config.yml"
$config = @"
tunnel: $tunnelId
credentials-file: $serviceCredentials
ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:$BackendPort
  - service: http_status:404
"@
# UTF-8 without BOM (Windows PowerShell's -Encoding utf8 writes a BOM).
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, $config, $utf8)
Write-Host "Wrote $configPath"
# Keep a copy in the user profile so `cloudflared tunnel run` works for manual tests.
[System.IO.File]::WriteAllText((Join-Path $cfDir "config.yml"), ($config -replace [regex]::Escape($serviceCredentials), $credentials), $utf8)

# --- 4) DNS: hostname -> tunnel (updated in place on re-runs) --------------
& $exe tunnel route dns --overwrite-dns $TunnelName $Hostname | Out-Host

# --- 5) Windows service with explicit config + log file --------------------
$svc = Get-Service -Name cloudflared -ErrorAction SilentlyContinue
if (-not $svc) {
    & $exe service install | Out-Host
    $svc = Get-Service -Name cloudflared -ErrorAction Stop
}
if ($svc.Status -ne "Stopped") { Stop-Service cloudflared -Force }
# Same thing `sc.exe config binPath=` would do, without the quoting pitfalls.
$imagePath = "`"$exe`" --config `"$configPath`" --logfile `"$LogFile`" tunnel run"
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\cloudflared" -Name ImagePath -Value $imagePath
Set-Service cloudflared -StartupType Automatic
if (Test-Path $LogFile) { Clear-Content $LogFile }
Start-Service cloudflared

# --- 6) Verify --------------------------------------------------------------
$connected = $false
for ($i = 0; $i -lt 15 -and -not $connected; $i++) {
    Start-Sleep -Seconds 2
    if ((Test-Path $LogFile) -and (Select-String -Path $LogFile -Pattern "Registered tunnel connection" -Quiet)) { $connected = $true }
}
if (Test-Path $LogFile) { Get-Content $LogFile -Tail 6 | Out-Host }
$svc = Get-Service -Name cloudflared
if ($svc.Status -ne "Running") { throw "cloudflared service is $($svc.Status). See $LogFile" }
if (-not $connected) { Write-Host "Service is running but no tunnel connection was logged yet; check $LogFile" -ForegroundColor Yellow }
Write-Host "Tunnel '$TunnelName' is serving https://$Hostname -> http://127.0.0.1:$BackendPort (service: cloudflared, log: $LogFile)" -ForegroundColor Green
try {
    $r = Invoke-WebRequest -Uri "https://$Hostname/public/config" -UseBasicParsing -TimeoutSec 20
    Write-Host "https://$Hostname/public/config -> HTTP $($r.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "Could not fetch https://$Hostname/public/config from this PC yet ($($_.Exception.Message)). A freshly created hostname can stay cached as missing by the local DNS resolver for up to 30 minutes; the log above shows whether the tunnel itself connected." -ForegroundColor Yellow
}
Write-Host "Next: set apiBase to https://$Hostname in site/api.json and electron/public-config.json."
