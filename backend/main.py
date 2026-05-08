"""
FastAPI entrypoint that exposes the existing Python logic to the Electron UI.
Run with: `python backend/main.py`
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes import players, teams, simulation, bracket, best_team, playoff, admin, events
from backend.data.event_db import ensure_event_schema
from backend.data.player_db import ensure_schema
from backend.data.team_db import ensure_team_schema
from backend.routes.simulation import ensure_simulation_schema
from backend.routes.best_team import ensure_best_team_schema
from backend.routes.playoff import ensure_playoff_schema


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="CS Fantasy API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(players.router, prefix="/players", tags=["players"])
    app.include_router(teams.router, prefix="/teams", tags=["teams"])
    app.include_router(simulation.router, prefix="/simulate", tags=["simulation"])
    app.include_router(bracket.router, prefix="/bracket", tags=["bracket"])
    app.include_router(best_team.router, prefix="/best-team", tags=["best-team"])
    app.include_router(playoff.router, prefix="/playoff", tags=["playoff"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(events.router, prefix="/events", tags=["events"])

    @app.on_event("startup")
    def _init_database() -> None:
        ensure_schema()
        ensure_team_schema()
        ensure_event_schema()
        ensure_simulation_schema()
        ensure_best_team_schema()
        ensure_playoff_schema()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
