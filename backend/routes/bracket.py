from fastapi import APIRouter, HTTPException
from swiss_stage.team_initialization import initialize_teams
from swiss_stage.swiss_bracket import simulate_single_swiss_run


router = APIRouter()


@router.post("/swiss-run")
def swiss_run(payload: dict):
    """
    Run a single Swiss bracket and return the final TeamState objects.
    Expects:
      - team_ids: list[int]
      - vrs_ranks: dict[int, int]
      - bo3_mode: str
    """
    required = ["team_ids", "vrs_ranks", "bo3_mode"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    team_ids = payload["team_ids"]
    vrs_ranks = payload["vrs_ranks"]
    bo3_mode = payload["bo3_mode"]

    team_states = simulate_single_swiss_run(
        team_ids=team_ids,
        vrs_ranks=vrs_ranks,
        bo3_mode=bo3_mode,
        initialize_teams=initialize_teams,
    )

    # Serialize dataclasses to dicts for JSON response
    serialized = {}
    for tid, ts in team_states.items():
        serialized[tid] = {
            "team_id": ts.team_id,
            "wins": ts.wins,
            "losses": ts.losses,
            "qualified": ts.qualified,
            "eliminated": ts.eliminated,
            "players": {
                pid: {
                    "total_points": p.total_points,
                    "rating_points_total": p.rating_points_total,
                    "win_points_total": p.win_points_total,
                    "role_points_total": p.role_points_total,
                    "booster_points_total": p.booster_points_total,
                }
                for pid, p in ts.players.items()
            },
        }
    return serialized
