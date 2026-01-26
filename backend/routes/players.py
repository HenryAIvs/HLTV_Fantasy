from fastapi import APIRouter, HTTPException
from player_db import get_player, get_all_players, add_or_update_player, delete_player


router = APIRouter()


@router.get("/")
def list_players():
    return get_all_players()


@router.get("/{player_id}")
def fetch_player(player_id: int):
    player = get_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.post("/")
def upsert_player(payload: dict):
    """
    Minimal upsert endpoint that mirrors player_db.add_or_update_player args.
    Expects at least player_id; other fields are optional and will be ignored
    if missing (None leaves existing values untouched).
    """
    if "player_id" not in payload:
        raise HTTPException(status_code=400, detail="player_id is required")
    add_or_update_player(**payload)
    return {"status": "ok"}


@router.delete("/{player_id}")
def remove_player(player_id: int):
    delete_player(player_id)
    return {"status": "ok"}
