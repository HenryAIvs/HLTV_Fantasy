from fastapi import APIRouter, HTTPException
from swiss_stage.fantasy_montecarlo import simulate_swiss_fantasy
from team_db import get_all_teams


router = APIRouter()


@router.post("/")
def run_simulation(payload: dict):
    """
    Run the existing Swiss Monte Carlo simulation.
    Expects:
      - team_ids: list[int]
      - vrs_ranks: dict[int, int]
      - bo3_mode: str (elim_qual | all | none)
      - n_sims: int
    """
    required = ["team_ids", "vrs_ranks", "bo3_mode", "n_sims"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    # Fill missing vrs_ranks with DB values (or default) to avoid KeyError in workers
    vrs_ranks_raw = payload.get("vrs_ranks", {})
    vrs_ranks = {int(k): int(v) for k, v in vrs_ranks_raw.items()}
    if payload.get("team_ids"):
        db_vrs = {t["team_id"]: t.get("vrs_rank", 999) for t in get_all_teams()}
        for tid in payload["team_ids"]:
            if tid not in vrs_ranks:
                vrs_ranks[tid] = db_vrs.get(tid, 999)

    result = simulate_swiss_fantasy(
        team_ids=payload["team_ids"],
        vrs_ranks=vrs_ranks,
        bo3_mode=payload["bo3_mode"],
        n_sims=int(payload["n_sims"]),
    )
    return result
