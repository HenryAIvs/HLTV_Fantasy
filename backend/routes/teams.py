from fastapi import APIRouter, HTTPException
from team_db import (
    get_all_teams,
    get_team_by_id,
    get_team_by_name,
    add_or_update_team,
    delete_team,
)


router = APIRouter()


@router.get("/")
def list_teams():
    return get_all_teams()


@router.get("/{team_id}")
def fetch_team(team_id: int):
    team = get_team_by_id(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.get("/by-name/{name}")
def fetch_team_by_name(name: str):
    team = get_team_by_name(name)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.post("/")
def upsert_team(payload: dict):
    """
    Requires: name, hltv_rank, vrs_rank, win_rate, player_ids (len==5).
    """
    required = ["name", "hltv_rank", "vrs_rank", "win_rate", "player_ids"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
    add_or_update_team(**payload)
    return {"status": "ok"}


@router.delete("/{team_id}")
def remove_team(team_id: int):
    delete_team(team_id)
    return {"status": "ok"}
