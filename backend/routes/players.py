import time
import re
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend.data.player_db import add_or_update_player, delete_player, get_all_players, get_player
from backend.services.hltv_featured_ratings import HLTVFeaturedRatingsError, get_featured_ratings
from backend.services.rating_curve import build_player_topx_graph


router = APIRouter()
DEFAULT_TOPX_BATCH_CONCURRENCY = 1
MAX_TOPX_BATCH_CONCURRENCY = 8
TOPX_BATCH_JOBS = {}
TOPX_BATCH_JOBS_LOCK = threading.Lock()
TIER_FIELD_MAP = {
    5: ("rating_top5", "maps_top5"),
    10: ("rating_top10", "maps_top10"),
    20: ("rating_top20", "maps_top20"),
    30: ("rating_top30", "maps_top30"),
    50: ("rating_top50", "maps_top50"),
}
TOP_RATING_TEXT_RE = re.compile(
    r"(?P<rating>\d+\.\d+)\s*vs\s*top\s*(?P<tier>\d+)\s*opponents.*?\(\s*(?P<maps>\d+)\s*maps",
    re.IGNORECASE | re.DOTALL,
)


def _build_top_rating_updates(player: dict, featured: dict) -> dict:
    tier_data: dict[int, tuple[float, int]] = {}
    for tier in TIER_FIELD_MAP:
        row = featured.get(tier) or featured.get(str(tier)) or {}
        rating = row.get("rating")
        maps = row.get("maps")
        if rating is None or maps is None:
            continue
        tier_data[int(tier)] = (float(rating), int(maps))

    tiers_sorted = sorted(TIER_FIELD_MAP)

    def pick_for(target_tier: int) -> tuple[float, int] | None:
        current = tier_data.get(target_tier)
        if current and current[1] >= 5:
            return current
        for tier in tiers_sorted:
            if tier < target_tier:
                continue
            candidate = tier_data.get(tier)
            if candidate and candidate[1] >= 5:
                return candidate
        if current:
            return current
        return None

    updates: dict[str, float | int] = {"last_topx_import_at": time.time()}
    for tier in tiers_sorted:
        rating_field, maps_field = TIER_FIELD_MAP[tier]
        selected = pick_for(tier)
        if selected:
            rating_value, maps_value = selected
        else:
            rating_value = player.get(rating_field)
            maps_value = player.get(maps_field)
        updates[rating_field] = float(rating_value) if rating_value is not None else 0.0
        updates[maps_field] = int(maps_value) if maps_value is not None else 0
    return updates


def _parse_featured_ratings_text(text: str) -> dict[int, dict[str, float | int]]:
    featured: dict[int, dict[str, float | int]] = {}
    for match in TOP_RATING_TEXT_RE.finditer(text or ""):
        tier = int(match.group("tier"))
        if tier not in TIER_FIELD_MAP:
            continue
        rating = float(match.group("rating"))
        maps = int(match.group("maps"))
        current = featured.get(tier)
        if current is None or int(current.get("maps") or 0) < maps:
            featured[tier] = {"rating": rating, "maps": maps}
    if not featured:
        raise HTTPException(status_code=400, detail="No 'vs top X opponents' entries found in the supplied text.")
    return featured


def _build_featured_payload(player: dict, *, source_text: str | None = None) -> dict:
    if source_text and source_text.strip():
        return {
            "startDate": None,
            "endDate": None,
            "featured_ratings": _parse_featured_ratings_text(source_text),
        }
    return get_featured_ratings(
        int(player["player_id"]),
        player_name=player.get("name"),
        tops=sorted(TIER_FIELD_MAP),
    )


def _persist_featured_top_ratings(player: dict, featured_payload: dict) -> dict:
    player_id = int(player["player_id"])
    updates = _build_top_rating_updates(player, featured_payload.get("featured_ratings") or {})
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
        "startDate": featured_payload.get("startDate"),
        "endDate": featured_payload.get("endDate"),
        "updated_fields": sorted(updates.keys()),
        "player": get_player(player_id),
    }


def _save_featured_top_ratings(player_id: int, *, source_text: str | None = None) -> dict:
    player = get_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    featured_payload = _build_featured_payload(player, source_text=source_text)
    return _persist_featured_top_ratings(player, featured_payload)


def _normalize_top_ratings_batch_items(payload: dict | None) -> list[dict]:
    body = payload or {}
    raw_items = body.get("items")
    if raw_items is None:
        raw_player_ids = body.get("player_ids")
        if raw_player_ids is None:
            raise HTTPException(status_code=400, detail="player_ids or items is required")
        raw_items = [{"player_id": value} for value in raw_player_ids]

    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(status_code=400, detail="At least one player is required")

    items: list[dict] = []
    seen: set[int] = set()
    for idx, raw in enumerate(raw_items, start=1):
        if isinstance(raw, dict):
            raw_player_id = raw.get("player_id")
            source_text = str(raw.get("text") or "")
        else:
            raw_player_id = raw
            source_text = ""
        try:
            player_id = int(raw_player_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid player_id at item {idx}") from exc
        if player_id <= 0:
            raise HTTPException(status_code=400, detail=f"Invalid player_id at item {idx}")
        if player_id in seen:
            continue
        seen.add(player_id)
        items.append({"player_id": player_id, "text": source_text})
    return items


def _resolve_batch_concurrency(raw_value: object, *, item_count: int) -> int:
    try:
        value = int(raw_value or DEFAULT_TOPX_BATCH_CONCURRENCY)
    except (TypeError, ValueError):
        value = DEFAULT_TOPX_BATCH_CONCURRENCY
    value = max(1, min(value, MAX_TOPX_BATCH_CONCURRENCY))
    return min(value, max(1, item_count))


def _run_top_ratings_batch(items: list[dict], concurrency: int, progress_callback=None) -> dict:
    ready_items: list[dict] = []
    results: list[dict] = []
    total = len(items)
    processed = 0
    ok_count = 0
    failed_count = 0

    def emit_progress(row: dict) -> None:
        if progress_callback:
            progress_callback(row, processed, total, ok_count, failed_count)

    for item in items:
        player_id = int(item["player_id"])
        player = get_player(player_id)
        if not player:
            processed += 1
            failed_count += 1
            row = {
                "status": "error",
                "player_id": player_id,
                "detail": "Player not found",
            }
            results.append(row)
            emit_progress(row)
            continue
        ready_items.append({**item, "player": player})

    if ready_items:
        abort_reason = ""
        for idx, item in enumerate(ready_items):
            player = item["player"]
            player_id = int(player["player_id"])
            player_name = player.get("name")
            try:
                featured_payload = _build_featured_payload(
                    player,
                    source_text=item.get("text"),
                )
                saved = _persist_featured_top_ratings(player, featured_payload)
                row = {
                    "status": "ok",
                    "player_id": player_id,
                    "player_name": player_name,
                    "startDate": saved.get("startDate"),
                    "endDate": saved.get("endDate"),
                }
                ok_count += 1
            except HTTPException as exc:
                row = {
                    "status": "error",
                    "player_id": player_id,
                    "player_name": player_name,
                    "detail": str(exc.detail),
                }
                failed_count += 1
            except HLTVFeaturedRatingsError as exc:
                detail = str(exc)
                row = {
                    "status": "error",
                    "player_id": player_id,
                    "player_name": player_name,
                    "detail": detail,
                }
                failed_count += 1
                if "cloudflare" in detail.lower() and not item.get("text"):
                    abort_reason = (
                        "Batch aborted after Cloudflare challenge to avoid repeated HLTV requests. "
                        "Complete one manual import first, then retry batch."
                    )
            except Exception as exc:
                row = {
                    "status": "error",
                    "player_id": player_id,
                    "player_name": player_name,
                    "detail": f"Failed fetching HLTV Top-X data: {exc}",
                }
                failed_count += 1

            processed += 1
            results.append(row)
            emit_progress(row)

            if abort_reason:
                for remaining in ready_items[idx + 1 :]:
                    skipped_row = {
                        "status": "error",
                        "player_id": int(remaining["player"]["player_id"]),
                        "player_name": remaining["player"].get("name"),
                        "detail": abort_reason,
                    }
                    processed += 1
                    failed_count += 1
                    results.append(skipped_row)
                    emit_progress(skipped_row)
                break

    order = {int(item["player_id"]): idx for idx, item in enumerate(items)}
    results.sort(key=lambda row: order.get(int(row["player_id"]), 999999))
    return {
        "status": "ok",
        "total": total,
        "ok": ok_count,
        "failed": failed_count,
        "concurrency": concurrency,
        "results": results,
    }


def _run_top_ratings_batch_job(job_id: str, items: list[dict], concurrency: int) -> None:
    with TOPX_BATCH_JOBS_LOCK:
        job = TOPX_BATCH_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = time.time()

    def _update_progress(row: dict, processed: int, total: int, ok_count: int, failed_count: int) -> None:
        with TOPX_BATCH_JOBS_LOCK:
            job = TOPX_BATCH_JOBS.get(job_id)
            if not job:
                return
            job["processed_players"] = int(processed)
            job["total_players"] = int(total)
            job["ok"] = int(ok_count)
            job["failed"] = int(failed_count)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["results"].append(row)
            if row.get("status") != "ok":
                job["last_error"] = str(row.get("detail") or "")
            job["updated_at"] = time.time()

    try:
        result = _run_top_ratings_batch(items, concurrency, progress_callback=_update_progress)
        with TOPX_BATCH_JOBS_LOCK:
            job = TOPX_BATCH_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["result"] = result
            job["processed_players"] = int(result["total"])
            job["total_players"] = int(result["total"])
            job["ok"] = int(result["ok"])
            job["failed"] = int(result["failed"])
            job["results"] = result["results"]
            job["progress"] = 1.0
            job["updated_at"] = time.time()
    except Exception as exc:
        with TOPX_BATCH_JOBS_LOCK:
            job = TOPX_BATCH_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(exc)
            job["last_error"] = str(exc)
            job["updated_at"] = time.time()


@router.get("/")
def list_players():
    return get_all_players()


@router.get("/{player_id}")
def fetch_player(player_id: int):
    player = get_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.get("/{player_id}/rating-curve")
def fetch_player_rating_curve(player_id: int):
    player = get_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    curve = build_player_topx_graph(player)
    return {
        "player_id": player_id,
        "player_name": player.get("name"),
        **curve,
    }


@router.post("/fetch-top-ratings-batch/start")
def start_fetch_top_ratings_batch(payload: dict | None = None):
    items = _normalize_top_ratings_batch_items(payload)
    concurrency = _resolve_batch_concurrency((payload or {}).get("concurrency"), item_count=len(items))
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    with TOPX_BATCH_JOBS_LOCK:
        for existing_job_id, existing in TOPX_BATCH_JOBS.items():
            if existing.get("status") in {"queued", "running"}:
                return {
                    "job_id": existing_job_id,
                    "status": existing.get("status"),
                    "reused": True,
                }
        TOPX_BATCH_JOBS[job_id] = {
            "status": "queued",
            "error": "",
            "last_error": "",
            "progress": 0.0,
            "processed_players": 0,
            "total_players": len(items),
            "ok": 0,
            "failed": 0,
            "concurrency": concurrency,
            "result": None,
            "results": [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    worker = threading.Thread(target=_run_top_ratings_batch_job, args=(job_id, items, concurrency), daemon=True)
    worker.start()
    return {"job_id": job_id}


@router.get("/fetch-top-ratings-batch/job/{job_id}")
def get_fetch_top_ratings_batch_job(job_id: str):
    with TOPX_BATCH_JOBS_LOCK:
        job = TOPX_BATCH_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_id not found")
        out = dict(job)

    return {
        "job_id": job_id,
        "status": out.get("status", "queued"),
        "error": out.get("error", ""),
        "last_error": out.get("last_error", ""),
        "progress": out.get("progress", 0.0),
        "processed_players": out.get("processed_players", 0),
        "total_players": out.get("total_players", 0),
        "ok": out.get("ok", 0),
        "failed": out.get("failed", 0),
        "concurrency": out.get("concurrency", DEFAULT_TOPX_BATCH_CONCURRENCY),
        "result": out.get("result"),
        "results": out.get("results", []),
    }


@router.post("/fetch-top-ratings-batch")
def fetch_top_ratings_batch(payload: dict | None = None):
    items = _normalize_top_ratings_batch_items(payload)
    concurrency = _resolve_batch_concurrency((payload or {}).get("concurrency"), item_count=len(items))
    return _run_top_ratings_batch(items, concurrency)


@router.post("/{player_id}/fetch-top-ratings")
def fetch_top_ratings(player_id: int, payload: dict | None = None):
    body = payload or {}
    source_text = str(body.get("text") or "")
    try:
        return _save_featured_top_ratings(player_id, source_text=source_text)
    except HTTPException:
        raise
    except HLTVFeaturedRatingsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed fetching HLTV Top-X data: {exc}") from exc


@router.post("/")
def upsert_player(payload: dict):
    if "player_id" not in payload:
        raise HTTPException(status_code=400, detail="player_id is required")
    add_or_update_player(**payload)
    return {"status": "ok"}


@router.delete("/{player_id}")
def remove_player(player_id: int):
    delete_player(player_id)
    return {"status": "ok"}
