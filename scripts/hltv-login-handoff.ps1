# Hand the HLTV login from YOUR desktop session to the always-on backend.
#
# The 24/7 backend runs as SYSTEM with no desktop and its own Chrome profile, so
# nobody can solve HLTV's sign-in captcha in its browser. Run this as yourself:
# it reuses the login already in your own profile (or opens a window for you to
# sign in once), then posts the session cookies to the backend. The autologin
# cookie lasts about a year.
#
#   .\scripts\hltv-login-handoff.ps1                       # normal use
#   .\scripts\hltv-login-handoff.ps1 --include-cloudflare  # also hand over a Cloudflare clearance
#   .\scripts\hltv-login-handoff.ps1 --headed              # open the sign-in window straight away

$root = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }
Set-Location $root
& $venvPy -m backend.tools.hltv_session_handoff @args
exit $LASTEXITCODE
