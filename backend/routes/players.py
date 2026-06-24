import time
import re
import json
import sqlite3
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend.data.player_db import DB_PATH, add_or_update_player, delete_player, get_all_players, get_player
from backend.services.hltv_featured_ratings import HLTVFeaturedRatingsError, get_featured_ratings
from backend.services.rating_curve import build_player_topx_graph


router = APIRouter()
DEFAULT_TOPX_BATCH_CONCURRENCY = 1
MAX_TOPX_BATCH_CONCURRENCY = 8
TOPX_BATCH_JOBS = {}
TOPX_BATCH_JOBS_LOCK = threading.Lock()
TOPX_BATCH_WORKERS: dict[str, threading.Thread] = {}
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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_topx_batch_schema() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topx_batch_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                items_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                progress REAL NOT NULL DEFAULT 0,
                processed_players INTEGER NOT NULL DEFAULT 0,
                total_players INTEGER NOT NULL DEFAULT 0,
                ok INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                concurrency INTEGER NOT NULL DEFAULT 1,
                pause_requested INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL
            )
            """
        )
        cols = conn.execute("PRAGMA table_info(topx_batch_jobs)").fetchall()
        col_names = {row["name"] for row in cols}
        if "cancel_requested" not in col_names:
            conn.execute("ALTER TABLE topx_batch_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_topx_batch_jobs_updated ON topx_batch_jobs(updated_at DESC)")
        conn.commit()
    finally:
        conn.close()


def _status_from_row(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["items"] = json.loads(out.pop("items_json") or "[]")
    out["results"] = json.loads(out.pop("results_json") or "[]")
    out["pause_requested"] = bool(out.get("pause_requested"))
    out["cancel_requested"] = bool(out.get("cancel_requested"))
    out["progress"] = float(out.get("progress") or 0.0)
    return out


def _get_stored_topx_job(job_id: str) -> dict | None:
    ensure_topx_batch_schema()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM topx_batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _status_from_row(row) if row else None
    finally:
        conn.close()


def _save_topx_job(job: dict) -> None:
    ensure_topx_batch_schema()
    now = time.time()
    job["updated_at"] = now
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO topx_batch_jobs (
                job_id, status, items_json, results_json, error, last_error,
                progress, processed_players, total_players, ok, failed, concurrency,
                pause_requested, cancel_requested, created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                items_json = excluded.items_json,
                results_json = excluded.results_json,
                error = excluded.error,
                last_error = excluded.last_error,
                progress = excluded.progress,
                processed_players = excluded.processed_players,
                total_players = excluded.total_players,
                ok = excluded.ok,
                failed = excluded.failed,
                concurrency = excluded.concurrency,
                pause_requested = excluded.pause_requested,
                cancel_requested = excluded.cancel_requested,
                updated_at = excluded.updated_at,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at
            """,
            (
                str(job["job_id"]),
                str(job.get("status") or "queued"),
                json.dumps(job.get("items") or []),
                json.dumps(job.get("results") or []),
                str(job.get("error") or ""),
                str(job.get("last_error") or ""),
                float(job.get("progress") or 0.0),
                int(job.get("processed_players") or 0),
                int(job.get("total_players") or 0),
                int(job.get("ok") or 0),
                int(job.get("failed") or 0),
                int(job.get("concurrency") or DEFAULT_TOPX_BATCH_CONCURRENCY),
                1 if job.get("pause_requested") else 0,
                1 if job.get("cancel_requested") else 0,
                float(job.get("created_at") or now),
                now,
                job.get("started_at"),
                job.get("finished_at"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
    overall_rating = featured_payload.get("overall_rating")
    add_or_update_player(
        player_id=player_id,
        name=player.get("name"),
        rating=float(overall_rating) if overall_rating is not None else player.get("rating"),
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
        "overall_rating": float(overall_rating) if overall_rating is not None else None,
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
                    "overall_rating": saved.get("overall_rating"),
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


def _publish_topx_job(job: dict) -> None:
    with TOPX_BATCH_JOBS_LOCK:
        TOPX_BATCH_JOBS[str(job["job_id"])] = dict(job)
    _save_topx_job(job)


def _topx_job_response(job: dict) -> dict:
    return {
        "job_id": str(job["job_id"]),
        "status": job.get("status", "queued"),
        "error": job.get("error", ""),
        "last_error": job.get("last_error", ""),
        "progress": job.get("progress", 0.0),
        "processed_players": job.get("processed_players", 0),
        "total_players": job.get("total_players", 0),
        "ok": job.get("ok", 0),
        "failed": job.get("failed", 0),
        "concurrency": job.get("concurrency", DEFAULT_TOPX_BATCH_CONCURRENCY),
        "pause_requested": bool(job.get("pause_requested")),
        "cancel_requested": bool(job.get("cancel_requested")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "results": job.get("results", []),
        "result": {
            "status": "ok" if job.get("status") == "completed" else job.get("status", "queued"),
            "total": job.get("total_players", 0),
            "ok": job.get("ok", 0),
            "failed": job.get("failed", 0),
            "concurrency": job.get("concurrency", DEFAULT_TOPX_BATCH_CONCURRENCY),
            "results": job.get("results", []),
        } if job.get("status") in {"completed", "failed", "paused", "canceled"} else None,
    }


def _stored_running_job_is_active(job_id: str) -> bool:
    with TOPX_BATCH_JOBS_LOCK:
        worker = TOPX_BATCH_WORKERS.get(job_id)
        return bool(worker and worker.is_alive())


def _get_topx_job_for_response(job_id: str) -> dict:
    job = _get_stored_topx_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found")
    if job.get("status") in {"queued", "running", "pausing", "canceling"} and not _stored_running_job_is_active(job_id):
        job["status"] = "canceled" if job.get("cancel_requested") else "paused"
        job["last_error"] = job.get("last_error") or "Job was interrupted before completion. Resume to continue."
        job["error"] = ""
        job["pause_requested"] = False
        job["cancel_requested"] = False
        _publish_topx_job(job)
    return job


def _run_top_ratings_batch_job(job_id: str) -> None:
    job = _get_stored_topx_job(job_id)
    if not job:
        return

    if job.get("cancel_requested") or job.get("pause_requested"):
        job["status"] = "canceled" if job.get("cancel_requested") else "paused"
        job["pause_requested"] = False
        job["cancel_requested"] = False
        job["error"] = ""
        job["last_error"] = job.get("last_error") or ("Canceled" if job["status"] == "canceled" else "Paused")
        job["finished_at"] = time.time()
        _publish_topx_job(job)
        with TOPX_BATCH_JOBS_LOCK:
            TOPX_BATCH_WORKERS.pop(job_id, None)
        return

    job["status"] = "running"
    job["pause_requested"] = False
    job["cancel_requested"] = False
    job["error"] = ""
    job["started_at"] = job.get("started_at") or time.time()
    job["finished_at"] = None
    _publish_topx_job(job)

    try:
        items = job.get("items") or []
        results = list(job.get("results") or [])
        completed_ids = {int(row.get("player_id")) for row in results if row.get("player_id") is not None}
        processed = len(results)
        ok_count = sum(1 for row in results if row.get("status") == "ok")
        failed_count = sum(1 for row in results if row.get("status") != "ok")
        total = len(items)

        def save_progress(row: dict | None = None) -> dict:
            nonlocal processed, ok_count, failed_count, results, job
            if row is not None:
                results.append(row)
                processed += 1
                if row.get("status") == "ok":
                    ok_count += 1
                else:
                    failed_count += 1
                    job["last_error"] = str(row.get("detail") or "")
            job["results"] = results
            job["processed_players"] = int(processed)
            job["total_players"] = int(total)
            job["ok"] = int(ok_count)
            job["failed"] = int(failed_count)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            latest = _get_stored_topx_job(job_id) or {}
            if latest.get("cancel_requested"):
                job["cancel_requested"] = True
                job["pause_requested"] = False
                job["status"] = "canceling"
            if latest.get("pause_requested"):
                job["pause_requested"] = True
                if not job.get("cancel_requested"):
                    job["status"] = "pausing"
            _publish_topx_job(job)
            return job

        save_progress()

        for item in items:
            latest = _get_stored_topx_job(job_id) or job
            if latest.get("cancel_requested") or latest.get("pause_requested"):
                job.update(latest)
                job["status"] = "canceled" if latest.get("cancel_requested") else "paused"
                job["pause_requested"] = False
                job["cancel_requested"] = False
                job["last_error"] = job.get("last_error") or ("Canceled" if job["status"] == "canceled" else "Paused")
                job["finished_at"] = time.time()
                _publish_topx_job(job)
                return

            player_id = int(item["player_id"])
            if player_id in completed_ids:
                continue
            player = get_player(player_id)
            if not player:
                row = {"status": "error", "player_id": player_id, "detail": "Player not found"}
                completed_ids.add(player_id)
                save_progress(row)
                continue

            player_name = player.get("name")
            try:
                featured_payload = _build_featured_payload(player, source_text=item.get("text"))
                saved = _persist_featured_top_ratings(player, featured_payload)
                row = {
                    "status": "ok",
                    "player_id": player_id,
                    "player_name": player_name,
                    "startDate": saved.get("startDate"),
                    "endDate": saved.get("endDate"),
                    "overall_rating": saved.get("overall_rating"),
                }
            except HTTPException as exc:
                row = {
                    "status": "error",
                    "player_id": player_id,
                    "player_name": player_name,
                    "detail": str(exc.detail),
                }
            except HLTVFeaturedRatingsError as exc:
                row = {
                    "status": "error",
                    "player_id": player_id,
                    "player_name": player_name,
                    "detail": str(exc),
                }
            except Exception as exc:
                row = {
                    "status": "error",
                    "player_id": player_id,
                    "player_name": player_name,
                    "detail": f"Failed fetching HLTV Top-X data: {exc}",
                }

            completed_ids.add(player_id)
            save_progress(row)

        job["status"] = "completed"
        job["progress"] = 1.0
        job["finished_at"] = time.time()
        _publish_topx_job(job)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        job["last_error"] = str(exc)
        job["finished_at"] = time.time()
        _publish_topx_job(job)
    finally:
        with TOPX_BATCH_JOBS_LOCK:
            TOPX_BATCH_WORKERS.pop(job_id, None)


def _start_topx_worker(job_id: str) -> None:
    with TOPX_BATCH_JOBS_LOCK:
        worker = TOPX_BATCH_WORKERS.get(job_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(target=_run_top_ratings_batch_job, args=(job_id,), daemon=True)
        TOPX_BATCH_WORKERS[job_id] = worker
        worker.start()


@router.get("/")
def list_players():
    return get_all_players()


@router.post("/fetch-top-ratings-batch/start")
def start_fetch_top_ratings_batch(payload: dict | None = None):
    ensure_topx_batch_schema()
    items = _normalize_top_ratings_batch_items(payload)
    concurrency = _resolve_batch_concurrency((payload or {}).get("concurrency"), item_count=len(items))
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    latest = _get_latest_topx_batch_job(include_completed=False)
    if latest and latest.get("status") in {"queued", "running", "pausing", "canceling"}:
        return {"job_id": latest["job_id"], "status": latest.get("status"), "reused": True}

    now = time.time()
    job = {
        "job_id": job_id,
        "status": "queued",
        "error": "",
        "last_error": "",
        "progress": 0.0,
        "processed_players": 0,
        "total_players": len(items),
        "ok": 0,
        "failed": 0,
        "concurrency": concurrency,
        "pause_requested": False,
        "cancel_requested": False,
        "items": items,
        "results": [],
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    _publish_topx_job(job)
    _start_topx_worker(job_id)
    return {"job_id": job_id, "status": "queued"}


def _get_latest_topx_batch_job(*, include_completed: bool = True) -> dict | None:
    ensure_topx_batch_schema()
    statuses = "" if include_completed else "WHERE status IN ('queued', 'running', 'pausing', 'canceling', 'paused', 'failed')"
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM topx_batch_jobs {statuses} ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return _get_topx_job_for_response(str(row["job_id"]))
    finally:
        conn.close()


@router.get("/fetch-top-ratings-batch/latest")
def get_latest_fetch_top_ratings_batch_job():
    job = _get_latest_topx_batch_job()
    if not job:
        return {"exists": False}
    return {"exists": True, **_topx_job_response(job)}


@router.get("/fetch-top-ratings-batch/job/{job_id}")
def get_fetch_top_ratings_batch_job(job_id: str):
    return _topx_job_response(_get_topx_job_for_response(job_id))


@router.post("/fetch-top-ratings-batch/job/{job_id}/pause")
def pause_fetch_top_ratings_batch_job(job_id: str):
    job = _get_topx_job_for_response(job_id)
    if job.get("status") in {"completed", "failed", "canceled"}:
        return _topx_job_response(job)
    if job.get("status") == "canceling" or job.get("cancel_requested"):
        return _topx_job_response(job)
    job["pause_requested"] = True
    job["status"] = "pausing" if job.get("status") == "running" or _stored_running_job_is_active(job_id) else "paused"
    _publish_topx_job(job)
    return _topx_job_response(job)


@router.post("/fetch-top-ratings-batch/job/{job_id}/cancel")
def cancel_fetch_top_ratings_batch_job(job_id: str):
    job = _get_topx_job_for_response(job_id)
    if job.get("status") in {"completed", "failed", "canceled"}:
        return _topx_job_response(job)
    job["cancel_requested"] = True
    job["pause_requested"] = False
    job["last_error"] = "Cancel requested"
    job["status"] = "canceling" if job.get("status") == "running" or _stored_running_job_is_active(job_id) else "canceled"
    if job["status"] == "canceled":
        job["cancel_requested"] = False
        job["finished_at"] = time.time()
    _publish_topx_job(job)
    return _topx_job_response(job)


@router.post("/fetch-top-ratings-batch/job/{job_id}/resume")
def resume_fetch_top_ratings_batch_job(job_id: str):
    job = _get_topx_job_for_response(job_id)
    if job.get("status") in {"completed", "canceled"}:
        return _topx_job_response(job)
    if job.get("status") == "running" and _stored_running_job_is_active(job_id):
        return _topx_job_response(job)
    job["status"] = "queued"
    job["pause_requested"] = False
    job["cancel_requested"] = False
    job["error"] = ""
    _publish_topx_job(job)
    _start_topx_worker(job_id)
    return _topx_job_response(_get_topx_job_for_response(job_id))


@router.post("/fetch-top-ratings-batch")
def fetch_top_ratings_batch(payload: dict | None = None):
    items = _normalize_top_ratings_batch_items(payload)
    concurrency = _resolve_batch_concurrency((payload or {}).get("concurrency"), item_count=len(items))
    return _run_top_ratings_batch(items, concurrency)


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


@router.get("/{player_id}")
def fetch_player(player_id: int):
    player = get_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.delete("/{player_id}")
def remove_player(player_id: int):
    delete_player(player_id)
    return {"status": "ok"}
