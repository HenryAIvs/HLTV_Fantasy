"""
FastAPI entrypoint that exposes the existing Python logic to the Electron UI.
Run with: `python backend/main.py`
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes import players, teams, simulation, bracket, best_team, playoff, admin


def create_app() -> FastAPI:
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
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
