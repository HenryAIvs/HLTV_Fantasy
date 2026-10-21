"""The Live page: current fantasy events as they stand (services/live_state)."""
from fastapi import APIRouter

from backend.services import live_state

router = APIRouter()


@router.get("/events")
def get_live_events() -> dict:
    return {"events": live_state.live_events()}
