from typing import Any, Dict

import requests
from fastapi import APIRouter, HTTPException

from backend.routes.admin import _import_money_draft_data
from event_db import (
    get_active_event_id,
    get_event_detail,
    list_events,
    set_active_event,
)

router = APIRouter()


@router.get("/")
def list_all_events():
    return {
        "active_event_id": get_active_event_id(),
        "events": list_events(),
    }


@router.get("/active")
def get_active_event():
    active_id = get_active_event_id()
    if active_id is None:
        return {"active_event_id": None, "event": None}
    event = get_event_detail(active_id)
    return {"active_event_id": active_id, "event": event}


@router.get("/{event_id}")
def fetch_event(event_id: int):
    event = get_event_detail(int(event_id))
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/activate")
def activate_event(payload: Dict[str, Any]):
    event_id_raw = payload.get("event_id")
    if event_id_raw is None:
        raise HTTPException(status_code=400, detail="event_id is required")
    if not str(event_id_raw).isdigit():
        raise HTTPException(status_code=400, detail="event_id must be numeric")
    event_id = int(event_id_raw)

    event = get_event_detail(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    set_active_event(event_id)
    return {"status": "ok", "active_event_id": event_id}


@router.post("/import-hltv-event")
def import_hltv_event(payload: Dict[str, Any]):
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id.isdigit():
        raise HTTPException(status_code=400, detail="event_id must be numeric")

    url = f"https://www.hltv.org/fantasy/{event_id}/leagues/create/json"
    try:
        resp = requests.get(url, headers={"accept": "application/json"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV data: {exc}")

    money = data.get("moneyDraftData", {})
    if not money:
        raise HTTPException(status_code=400, detail="moneyDraftData missing in response")

    counts = _import_money_draft_data(money, event_id=int(event_id))
    return {"status": "ok", **counts, "event_id": int(event_id), "active_event_id": int(event_id)}

