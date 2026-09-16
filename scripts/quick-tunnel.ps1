# Temporary public URL for the local backend (no account, no domain): runs a
# Cloudflare quick tunnel in the foreground and prints the trycloudflare.com
# address. The address changes every run, so this is for testing the public
# build only — use install-tunnel.ps1 for the real thing.
param([int] $BackendPort = 8000)

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Host "cloudflared not found. Installing with winget..." -ForegroundColor Yellow
    winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
    $cloudflared = Get-Command cloudflared -ErrorAction Stop
}
Write-Host "Starting a quick tunnel to http://127.0.0.1:$BackendPort (Ctrl+C stops it)..."
& $cloudflared.Source tunnel --url "http://127.0.0.1:$BackendPort"
