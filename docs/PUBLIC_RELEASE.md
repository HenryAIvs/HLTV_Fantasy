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

1. Bump `version` in `electron/package.json` (`cd electron && npm version 0.1.4 --no-git-tag-version`).
2. Commit and push `main`.

That is all. The `Release` workflow runs on every push to `main`; when the
package version has no release yet it builds `CS-Fantasy-Toolkit-Setup.exe`
and `latest.yml`, creates the `v<version>` tag and the release, and attaches
the files (one release per tag, created by the workflow's upload step; never
create it by hand first). Pushes where the version is already released do
nothing. The download button always points at
`releases/latest/download/CS-Fantasy-Toolkit-Setup.exe`, and installed apps
pick the update up at their next check (every 30 minutes, on window focus,
or 10 s after launch) and offer a restart.

If a tag ever ends up with two releases, the download URL breaks: delete both
releases and push a new version.

To build locally instead: `cd electron && npm run dist` (installer in
`electron/release/`), or `npm run dist:dir` for an unpacked folder.

## Running the public build against your own machine

`cd electron && npm run public` starts the checkout in public mode: no local
backend is spawned, the API base comes from `api.json` (or `HLTV_API_BASE`),
and the backend treats it as a public client. `?public` on the Vite dev URL
previews the trimmed renderer.

## Operator access from elsewhere

Public callers reach read-only `GET` data under `/players`, `/teams`,
`/events` and `/assets` (everything the Database tab and the modals read),
the stored Tournament results, and the Top 5 queries. `PUBLIC_GET_DENY` in
`backend/services/public_access.py` keeps the exceptions operator-only: routes
that fetch from HLTV live, model training, page snapshots and job status. New
read-only endpoints under those prefixes are public automatically; anything
that writes or scrapes must be `POST` or go on the deny list.

To use the full API from another machine send `X-Admin-Token: <token>`; the
token is `HLTV_ADMIN_TOKEN` if set, else `.runtime/admin-token.txt` (created
on first start). Limits per public client: heavy queries
`HLTV_PUBLIC_RATE_LIMIT` per minute (default 60) with
`HLTV_PUBLIC_MAX_CONCURRENT` running at once (default 3); everything else
except assets `HLTV_PUBLIC_LIGHT_RATE_LIMIT` per minute (default 600).

## Forcing an update, posting a notice

`GET /public/config` carries `min_client_version` and `message`. To change
either without restarting the backend, write `.runtime/public-config.json`:

```json
{"min_client_version": "0.1.2", "message": "Maintenance tonight 22:00 UK"}
```

An installed app older than `min_client_version` shows an "Update required"
screen (with Check for updates and a website link) instead of the tabs; a
non-empty `message` shows as a notice bar above the tabs. The app probes
`/public/config` every 30 s, so changes take effect within a minute, and the
same probe drives the "Can't reach the server" screen during an outage.
Delete the file to go back to the defaults.

## Which event the public app shows

Public users pick any imported event from the Event dropdown on the
Tournament page (or View on the Events tab); the choice is remembered per
install and defaults to the server's current event. Stored runs (groups,
playoff, Swiss) are keyed by fantasy event, and every read endpoint takes
`?event_id=` to serve another event's run. Runs started from the operator app
always target the active event.

Valuations are frozen once an event starts: the nightly bake refreshes a
groups event only until its first match, and playoff runs only change when
you press Run Combinations. Playoff Top 5 queries are cached and warmed at
startup and after each run.

## What the public app needs published

Groups and playoff events follow the same pipeline: the valuation is baked
when the event is imported (groups draw or playoff bracket from the event
page, exact enumeration, roster combinations, Top 5 caches warmed), refreshed
by the nightly run with that night's ratings until the event's first match,
and frozen from then on. If the draw or bracket is not published yet the
nightly run keeps retrying. Run Groups / Run Playoff Bracket / Run
Combinations in the operator app are manual overrides only. Swiss events are
still manual: run them from the operator app once per event.
