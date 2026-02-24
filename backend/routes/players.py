from fastapi import APIRouter, HTTPException
import random
import time
from player_db import get_player, get_all_players, add_or_update_player, delete_player
from hltv_featured_ratings import get_featured_ratings


router = APIRouter()
TIER_FIELD_MAP = {
    5: ("rating_top5", "maps_top5"),
    10: ("rating_top10", "maps_top10"),
    20: ("rating_top20", "maps_top20"),
    30: ("rating_top30", "maps_top30"),
    50: ("rating_top50", "maps_top50"),
}


def _save_featured_top_ratings(player_id: int, *, headless: bool = False) -> dict:
    player = get_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}")

    featured_payload = get_featured_ratings(player_id, tops=[5, 10, 20, 30, 50], headed=not headless)
    featured = featured_payload.get("featured_ratings") or {}
    updates = {}
    for tier, fields in TIER_FIELD_MAP.items():
        rating_field, maps_field = fields
        row = featured.get(tier) or featured.get(str(tier)) or {}
        rating = row.get("rating")
        maps = row.get("maps")

        # If HLTV does not show a value for a tier, keep existing DB value when present,
        # otherwise fill with 0 so the UI always gets a concrete value.
        existing_rating = player.get(rating_field)
        existing_maps = player.get(maps_field)
        safe_rating = rating if rating is not None else (existing_rating if existing_rating is not None else 0.0)
        safe_maps = maps if maps is not None else (existing_maps if existing_maps is not None else 0)

        updates[rating_field] = float(safe_rating)
        updates[maps_field] = int(safe_maps)

    add_or_update_player(
        player_id=player_id,
        name=player.get("name"),
        rating=player.get("rating"),
        price=player.get("price"),
        best_role=player.get("best_role"),
        major_win_pct=player.get("major_win_pct"),
        minor_win_pct=player.get("minor_win_pct"),
        boosters_json=player.get("boosters_json"),
        roles_json=player.get("roles_json"),
        **updates,
    )

    return {
        "status": "ok",
        "player_id": player_id,
        "updated_fields": sorted(updates.keys()),
        "startDate": featured_payload.get("startDate"),
        "endDate": featured_payload.get("endDate"),
    }


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


@router.post("/{player_id}/fetch-top-ratings")
def fetch_top_ratings(player_id: int, payload: dict | None = None):
    body = payload or {}
    headless = bool(body.get("headless", False))

    try:
        return _save_featured_top_ratings(player_id, headless=headless)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed fetching HLTV ratings: {exc}") from exc

@router.post("/fetch-top-ratings-batch")
def fetch_top_ratings_batch(payload: dict):
    raw_ids = payload.get("player_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="player_ids must be a non-empty list")

    player_ids = []
    for v in raw_ids:
        if isinstance(v, str) and "," in v:
            parts = [p.strip() for p in v.split(",")]
        else:
            parts = [v]
        for p in parts:
            try:
                pid = int(p)
            except Exception:
                raise HTTPException(status_code=400, detail=f"Invalid player id: {p}")
            if pid <= 0:
                raise HTTPException(status_code=400, detail=f"Invalid player id: {p}")
            player_ids.append(pid)

    player_ids = sorted(set(player_ids))
    headless = bool(payload.get("headless", False))
    retries = int(payload.get("retries", 2))
    min_delay_seconds = float(payload.get("min_delay_seconds", 8.0))
    max_delay_seconds = float(payload.get("max_delay_seconds", 15.0))
    retry_backoff_seconds = float(payload.get("retry_backoff_seconds", 20.0))
    if retries < 0:
        raise HTTPException(status_code=400, detail="retries must be >= 0")
    if min_delay_seconds < 0 or max_delay_seconds < 0:
        raise HTTPException(status_code=400, detail="Delays must be >= 0")
    if min_delay_seconds > max_delay_seconds:
        raise HTTPException(status_code=400, detail="min_delay_seconds cannot exceed max_delay_seconds")

    results = []
    for idx, pid in enumerate(player_ids):
        attempts = 0
        last_error = None
        last_success = None
        for attempt in range(retries + 1):
            attempts = attempt + 1
            try:
                last_success = _save_featured_top_ratings(pid, headless=headless)
                last_error = None
                break
            except HTTPException as exc:
                last_error = exc.detail if hasattr(exc, "detail") else str(exc)
                if attempt < retries:
                    backoff = retry_backoff_seconds * (attempt + 1)
                    time.sleep(max(0.0, backoff))
            except Exception as exc:
                last_error = str(exc)
                if attempt < retries:
                    backoff = retry_backoff_seconds * (attempt + 1)
                    time.sleep(max(0.0, backoff))

        if last_error is None and last_success is not None:
            results.append(
                {
                    "player_id": pid,
                    "status": "ok",
                    "attempts": attempts,
                    "updated_fields": last_success.get("updated_fields", []),
                    "startDate": last_success.get("startDate"),
                    "endDate": last_success.get("endDate"),
                }
            )
        else:
            results.append(
                {
                    "player_id": pid,
                    "status": "error",
                    "attempts": attempts,
                    "error": last_error or "Unknown error",
                }
            )

        if idx < len(player_ids) - 1:
            time.sleep(random.uniform(min_delay_seconds, max_delay_seconds))

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return {
        "status": "ok",
        "total": len(player_ids),
        "ok": ok_count,
        "failed": len(player_ids) - ok_count,
        "min_delay_seconds": min_delay_seconds,
        "max_delay_seconds": max_delay_seconds,
        "retries": retries,
        "results": results,
    }


@router.delete("/{player_id}")
def remove_player(player_id: int):
    delete_player(player_id)
    return {"status": "ok"}
