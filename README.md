# HLTV Fantasy

Desktop app for building and simulating HLTV fantasy teams. An Electron + React
frontend talks to a local FastAPI backend on `127.0.0.1` (the backend picks a free
port at startup, 8000 by default, and memorizes it in `.runtime/backend-port.json`); data
lives in a local SQLite database (`fantasy_players.db`).

## Run

```powershell
.\run_app.ps1
```

## Run the backend 24/7 (SYSTEM service)

```powershell
.\scripts\install-service.ps1        # once, prompts for admin: SYSTEM task at boot, sleep off
.\scripts\hltv-login-handoff.ps1     # once, as yourself: gives the service browser your HLTV login
.\scripts\restart-backend.ps1        # after backend code changes (works for the SYSTEM service too)
.\scripts\uninstall-service.ps1      # remove the service task
```

The service scrapes HLTV with headless Chrome on its own profile under
`C:\ProgramData\HLTVFantasy` (logs live there too). HLTV's sign-in is
captcha-protected and the service has no desktop, so the hand-off script signs
in from your session and posts the session cookies to `POST /admin/hltv-session-cookies`;
the `autologin` cookie lasts about 400 days from the last real sign-in and HLTV
does not renew it, so once a year: `.\scripts\hltv-login-handoff.ps1 --headed`.
`GET /admin/hltv-session-status` reports whether the service browser is signed
in and how long the cookie has left; every nightly run re-checks it and the
Scheduling tab shows the result, warning 30 days ahead. The older logon-time launcher
(`scripts\install-autostart.ps1`) still works as an alternative that only runs
while you are signed in.

This creates/updates the Python venv, installs frontend deps, builds the
renderer, and launches Electron (which spawns the backend automatically).

For development with hot reload:

```powershell
# Terminal 1 — renderer dev server
cd electron; npm run renderer:dev
# Terminal 2 — Electron pointing at the dev server (spawns the backend)
cd electron; npm run dev
```

The backend can also be run standalone: `python -m backend.main`.

## Layout

```
backend/
  main.py           FastAPI app: routers, CORS, schema init on startup
  data/             SQLite access (db.py holds the shared DB path/connection)
  routes/           API endpoints (players, teams, events, simulation, ...)
  services/         Domain logic (match engine, ratings, HLTV scraping, ...)
  swiss_stage/      Swiss-bracket Monte Carlo simulation
electron/
  main.js           Electron entry; spawns the backend
  preload.js        Exposes a small fetch wrapper as window.api
  src/App.jsx       React UI
scripts/migrations/ One-off historical schema migrations (already applied)
```

`ensure_*_schema()` functions create tables idempotently at backend startup,
so a fresh clone works without running any migration scripts.

## HLTV scraping

Scraping uses SeleniumBase UC with a persistent Chrome profile in
`hltv_profile_seleniumbase/` (kept out of git) so Cloudflare cookies survive
between runs. Tunables via env vars: `HLTV_HEADLESS`, `HLTV_PROFILE_DIR`,
`HLTV_FETCH_DELAY_MIN`/`MAX`.
