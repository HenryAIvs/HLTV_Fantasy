# HLTV Fantasy

Desktop app for building and simulating HLTV fantasy teams. An Electron + React
frontend talks to a local FastAPI backend over `http://127.0.0.1:8000`; data
lives in a local SQLite database (`fantasy_players.db`).

## Run

```powershell
.\run_app.ps1
```

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
