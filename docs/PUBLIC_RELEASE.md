# Publishing the app

How the public build, the hosted backend, the website and the updater fit
together, and the steps that only the operator (you) can do.

## The pieces

| Piece | Where | What it does |
|---|---|---|
| Public app | `electron/` built by electron-builder | Installed app. Never runs a backend; talks to the hosted one. Hides Events, Dev Lab and Scheduling and every control that scrapes or rewrites stored runs. |
| Backend access layer | `backend/services/public_access.py` | Requests from this machine (or with the admin token) get everything; everyone else gets the read-only surface only, rate limited. |
| Website | `site/` → GitHub Pages | Landing page with the download button, plus `api.json`, which tells installed apps where the backend lives. |
| Release workflow | `.github/workflows/release.yml` | On a `v*` tag: builds the NSIS installer and publishes it and `latest.yml` to a GitHub Release. |
| Updater | electron-updater in `electron/main.js` | Installed apps check the GitHub Release feed on start and every six hours, download in the background, and prompt to restart. |
| Tunnel | `scripts/install-tunnel.ps1` | Exposes the local backend as `https://api.<your-domain>` through Cloudflare, no port forwarding. |

## One-time setup

1. **GitHub Pages**: repository Settings → Pages → Source: *GitHub Actions*. The
   `Website` workflow deploys `site/` on every push to `main` that touches it.
   The site lives at `https://henryaivs.github.io/HLTV_Fantasy/`.
2. **Domain + Cloudflare**: add your domain to Cloudflare (free plan), then run
   `scripts/install-tunnel.ps1 -Hostname api.your-domain.com` in an elevated
   PowerShell. It installs `cloudflared` as a service that forwards the
   hostname to the backend on this machine. When the browser asks which zone
   to authorise, pick the domain that owns the hostname. The script is safe to
   re-run. The service uses `C:\ProgramData\HLTVFantasy\cloudflared\config.yml`
   and logs to `C:\ProgramData\HLTVFantasy\logs\cloudflared.log`; "Registered
   tunnel connection" in that log means it is up. For a quick test without a
   domain, `scripts/quick-tunnel.ps1` prints a temporary `trycloudflare.com` URL.

   Live since 2026-09-16: `https://api.csfantasy.co.uk` (zone csfantasy.co.uk,
   tunnel `hltv-fantasy`).
3. **Point the apps at the backend**: put the public URL in `site/api.json`
   (`apiBase`) and in `electron/public-config.json` (the bundled fallback),
   commit, push. Installed apps read `api.json` at every start, so the backend
   can move later without a new release.
4. **Code signing (strongly recommended before a wide release)**: without a
   certificate Windows shows a SmartScreen notice on first run, and PCs with
   Smart App Control switched on (the default on many new Windows 11 installs,
   including this one) refuse to run the unsigned installer at all, with
   "An Application Control policy has blocked this file". An OV or EV
   code-signing certificate (Azure Trusted Signing is the cheapest route,
   about $10/month) fixes both. To sign, add `win.certificateFile` /
   `certificatePassword` (or the Trusted Signing settings) to the `build`
   block in `electron/package.json` and the secrets to the workflow.

## Releasing a version

1. Bump `version` in `electron/package.json`.
2. Commit, then `git tag v0.1.1 && git push origin main v0.1.1`.
3. The `Release` workflow builds `CS-Fantasy-Toolkit-Setup.exe` and
   `latest.yml` and attaches them to the release. The download button always
   points at `releases/latest/download/CS-Fantasy-Toolkit-Setup.exe`.
4. Installed apps pick the update up within six hours (or at next start).

To build locally instead: `cd electron && npm run dist` (installer in
`electron/release/`), or `npm run dist:dir` for an unpacked folder.

## Running the public build against your own machine

`cd electron && npm run public` starts the checkout in public mode: no local
backend is spawned, the API base comes from `api.json` (or `HLTV_API_BASE`),
and the backend treats it as a public client. `?public` on the Vite dev URL
previews the trimmed renderer.

## Operator access from elsewhere

Public callers only reach `GET` data endpoints and the Top 5 queries. To use
the full API from another machine send `X-Admin-Token: <token>`; the token is
`HLTV_ADMIN_TOKEN` if set, else `.runtime/admin-token.txt` (created on first
start). Per-client rate limits and the concurrency cap are
`HLTV_PUBLIC_RATE_LIMIT` (per minute, default 60) and
`HLTV_PUBLIC_MAX_CONCURRENT` (default 3).

## What the public app needs published

The scheduler bakes the active event's valuations nightly; the Top 5 queries
run live against them. Playoff and Swiss combinations still come from the
stored runs on this machine, so run them from the operator app once per event.
