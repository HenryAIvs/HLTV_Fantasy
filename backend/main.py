"""
FastAPI entrypoint that exposes the existing Python logic to the Electron UI.
Run with: `python backend/main.py`
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes import players, teams, simulation, bracket, best_team, playoff, groups, admin, events, schedule, assets
from backend.data.event_db import ensure_event_schema
from backend.data.player_db import ensure_schema, ensure_topx_windows_schema
from backend.data.team_db import ensure_team_schema
from backend.data.schedule_db import ensure_schedule_schema
from backend.services.scheduler import scheduler

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
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="CS Fantasy API", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router, prefix, tag in ROUTERS:
        app.include_router(router, prefix=prefix, tags=[tag])

    @app.get("/health")
    def health() -> dict:
        # Cheap liveness probe: the Electron launcher hits this to decide whether
        # a backend is already running (so it connects instead of spawning a
        # second one), and the auto-start watchdog uses it too.
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
