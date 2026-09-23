"""
FastAPI entrypoint that exposes the existing Python logic to the Electron UI.
Run with: `python backend/main.py`
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes import players, teams, simulation, bracket, best_team, playoff, groups, admin, events, schedule, assets
from backend.services.backend_port import APP_ID, HOST, PORT_FILE, select_port, write_port_info
from backend.data.event_db import ensure_event_schema
from backend.data.player_db import ensure_schema, ensure_topx_windows_schema
from backend.data.team_db import ensure_team_schema
from backend.data.schedule_db import ensure_schedule_schema
from backend.services.scheduler import scheduler
from backend.services.public_access import PublicAccessMiddleware, admin_token
from backend.routes import auth as auth_routes
from backend.data.auth_db import ensure_auth_schema

SCHEMA_INITIALIZERS = (
    ensure_schema,
    ensure_topx_windows_schema,
    ensure_team_schema,
    ensure_event_schema,
    simulation.ensure_simulation_schema,
    best_team.ensure_best_team_schema,
    playoff.ensure_playoff_schema,
    groups.ensure_groups_schema,
    players.ensure_topx_batch_schema,
    teams.ensure_map_stats_import_schema,
    teams.ensure_rankings_refresh_schema,
    events.ensure_historical_stats_job_schema,
    events.ensure_veto_backfill_job_schema,
    events.ensure_map_sb_job_schema,
    admin.ensure_trigger_backfill_schema,
    ensure_schedule_schema,
    ensure_auth_schema,
)

ROUTERS = (
    (players.router, "/players", "players"),
    (teams.router, "/teams", "teams"),
    (simulation.router, "/simulate", "simulation"),
    (bracket.router, "/bracket", "bracket"),
    (best_team.router, "/best-team", "best-team"),
    (playoff.router, "/playoff", "playoff"),
    (groups.router, "/groups", "groups"),
    (admin.router, "/admin", "admin"),
    (events.router, "/events", "events"),
    (schedule.router, "/schedule", "schedule"),
    (assets.router, "/assets", "assets"),
    (auth_routes.router, "/auth", "auth"),
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    for ensure in SCHEMA_INITIALIZERS:
        ensure()
    try:
        players.refresh_average_rating_curve()
    except Exception:
        logging.getLogger(__name__).warning("Could not fit average rating curve at startup", exc_info=True)
    # Nightly data-ingestion scheduler runs for as long as the backend is up.
    try:
        scheduler.start()
    except Exception:
        logging.getLogger(__name__).warning("Could not start data scheduler", exc_info=True)
    # Warm the heavy in-process caches off the request path: the stored groups
    # run (~1 s blob parse) and every event's format detection (~1 s burst).
    import threading

    def _warm() -> None:
        # The win-probability model the simulators use: train it when the data
        # or the feature set changed since it was last stored (~25 s).
        try:
            events.ensure_production_map_model()
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning("Could not refresh the app map model", exc_info=True)
        events.warm_kind_cache()  # ~1 s; needed by the first Tournament open
        groups.warm_caches()  # ~10 s: outcome sample + the three default Top 5 queries
        try:
            playoff.seed_playoff_queries()  # parse the combos blob + cache the default queries
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_warm, name="cache-warmup", daemon=True).start()
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="CS Fantasy API", version="0.1.0", lifespan=_lifespan)
    # Public callers (the distributed app, anything through the tunnel) only
    # reach the read-only surface; the operator's own machine and holders of
    # the admin token get everything. Added before CORS so refusals still
    # carry the CORS headers.
    app.add_middleware(PublicAccessMiddleware)
    admin_token()  # make sure .runtime/admin-token.txt exists for the operator
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router, prefix, tag in ROUTERS:
        app.include_router(router, prefix=prefix, tags=[tag])

    @app.get("/public/config")
    def public_config() -> dict:
        """What the distributed app needs to know about this server. The
        operator can raise the minimum client version or post a notice
        without a restart via .runtime/public-config.json, e.g.
        {"min_client_version": "0.1.2", "message": "Maintenance 22:00 UTC"}.
        An app older than min_client_version shows an update screen."""
        from backend.data.event_db import get_active_event_id
        from backend.services.public_access import public_config_overrides

        try:
            active = get_active_event_id()
        except Exception:
            active = None
        return {
            "app": APP_ID,
            "name": "CS Fantasy Toolkit",
            "api_version": "0.1.0",
            "min_client_version": "0.1.0",
            "active_event_id": active,
            "message": "",
            # Sign-in the distributed app must complete before reading data.
            "auth": {"provider": "google", "required": auth_routes.sign_in_required(), "configured": auth_routes.google_configured()},
            **public_config_overrides(),
        }

    @app.get("/health")
    def health() -> dict:
        # Liveness + identity probe: the Electron launcher, the autostart
        # watchdog and a second backend launch all use `app` to tell one of OUR
        # backends apart from some other program that happens to own the port.
        return {
            "status": "ok",
            "app": APP_ID,
            "port": getattr(app.state, "port", None),
            "pid": os.getpid(),
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # Bind first (memorized port, else the next free one), memorize the result,
    # then serve on that exact socket. See backend/services/backend_port.py.
    sock, port = select_port()
    app.state.port = port
    info = write_port_info(port, os.getpid())
    logging.getLogger(__name__).info("Backend listening on %s (memorized in %s)", info["url"], PORT_FILE)
    uvicorn.Server(uvicorn.Config(app, host=HOST, port=port)).run(sockets=[sock])
