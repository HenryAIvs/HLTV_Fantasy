# Exposes the local backend to the internet through a Cloudflare Tunnel as an
# always-on Windows service — no port forwarding, TLS handled by Cloudflare,
# works on a dynamic IP.
#
# Prerequisites: a domain on Cloudflare (free plan is fine) and cloudflared
# (winget install Cloudflare.cloudflared). Run once from an elevated PowerShell:
#
#   .\scripts\install-tunnel.ps1 -Hostname api.your-domain.com
#
# It logs you into Cloudflare (browser), creates the tunnel, routes the
# hostname to it, writes the config and installs the service. Afterwards put
# https://api.your-domain.com in site/api.json and electron/public-config.json.
param(
    [Parameter(Mandatory = $true)] [string] $Hostname,
    [string] $TunnelName = "hltv-fantasy",
    [int] $BackendPort = 8000
)

$ErrorActionPreference = "Stop"
$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Host "cloudflared not found. Installing with winget..." -ForegroundColor Yellow
    winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
    $cloudflared = Get-Command cloudflared -ErrorAction Stop
}

$cfDir = Join-Path $env:USERPROFILE ".cloudflared"
if (-not (Test-Path (Join-Path $cfDir "cert.pem"))) {
    Write-Host "Logging in to Cloudflare (a browser window opens; pick the domain)..."
    & $cloudflared.Source tunnel login
}

$existing = (& $cloudflared.Source tunnel list --output json | ConvertFrom-Json) | Where-Object { $_.name -eq $TunnelName }
if (-not $existing) {
    & $cloudflared.Source tunnel create $TunnelName | Out-Host
    $existing = (& $cloudflared.Source tunnel list --output json | ConvertFrom-Json) | Where-Object { $_.name -eq $TunnelName }
}
$tunnelId = $existing.id
$credentials = Join-Path $cfDir "$tunnelId.json"

$config = @"
tunnel: $tunnelId
credentials-file: $credentials
ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:$BackendPort
  - service: http_status:404
"@
$configPath = Join-Path $cfDir "config.yml"
$config | Out-File -FilePath $configPath -Encoding utf8
Write-Host "Wrote $configPath"

& $cloudflared.Source tunnel route dns $TunnelName $Hostname | Out-Host

# The service reads C:\Windows\System32\config\systemprofile\.cloudflared\config.yml
# when running as SYSTEM; give it a copy of the config and credentials.
$systemDir = "C:\Windows\System32\config\systemprofile\.cloudflared"
New-Item -ItemType Directory -Force -Path $systemDir | Out-Null
Copy-Item $configPath (Join-Path $systemDir "config.yml") -Force
Copy-Item $credentials (Join-Path $systemDir "$tunnelId.json") -Force
(Get-Content (Join-Path $systemDir "config.yml")) -replace [regex]::Escape($credentials), (Join-Path $systemDir "$tunnelId.json") |
    Set-Content (Join-Path $systemDir "config.yml") -Encoding utf8

$svc = Get-Service -Name cloudflared -ErrorAction SilentlyContinue
if ($svc) {
    Restart-Service cloudflared
} else {
    & $cloudflared.Source service install
    Start-Service cloudflared
}
Write-Host "Tunnel '$TunnelName' is serving https://$Hostname -> http://127.0.0.1:$BackendPort" -ForegroundColor Green
Write-Host "Next: set apiBase to https://$Hostname in site/api.json and electron/public-config.json."
