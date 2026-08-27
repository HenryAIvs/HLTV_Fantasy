"""Serve locally cached HLTV images (team logos, player photos) to the UI."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.services.image_cache import (
    backfill_images,
    cache_status,
    image_path,
    sniff_content_type,
    start_backfill,
)

router = APIRouter()

# no-cache (not no-store): the renderer may reuse images while revalidating,
# so quality upgrades from a re-harvest show up on a plain reload instead of
# being pinned for a day.
_CACHE_HEADERS = {"Cache-Control": "no-cache"}


@router.get("/team/{hltv_team_id}")
def team_logo(hltv_team_id: int):
    path = image_path("team", hltv_team_id)
    if not path:
        raise HTTPException(status_code=404, detail="No cached logo for that team")
    data = path.read_bytes()
    return Response(content=data, media_type=sniff_content_type(data), headers=_CACHE_HEADERS)


@router.get("/player/{player_id}")
def player_photo(player_id: int):
    path = image_path("player", player_id)
    if not path:
        raise HTTPException(status_code=404, detail="No cached photo for that player")
    data = path.read_bytes()
    return Response(content=data, media_type=sniff_content_type(data), headers=_CACHE_HEADERS)


@router.get("/status")
def assets_status():
    return cache_status()


@router.post("/backfill")
def assets_backfill(payload: dict | None = None):
    body = payload or {}
    if body.get("wait"):
        return backfill_images(force=bool(body.get("force")))
    return start_backfill(force=bool(body.get("force")))
