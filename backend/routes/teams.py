from datetime import date, datetime, timezone
import json
import logging
import sqlite3
import threading
import time

from fastapi import APIRouter, HTTPException

from backend.hltv_rankings import (
    HLTVRankingError,
    RankingPageParseError,
    TeamNotRankedError,
    get_hltv_team_map_stats,
    get_latest_hltv_rankings,
    get_latest_vrs_rankings,
    get_team_hltv_rating_and_points_on_date,
    get_team_vrs_rank_and_points_on_date,
)
from backend.data.team_db import (
    connect as connect_team_db,
    get_all_teams,
    get_team_by_id,
    get_team_by_name,
    add_or_update_team,
    delete_team,
    update_team_map_stats,
)


router = APIRouter()
logger = logging.getLogger(__name__)
MAP_STATS_IMPORT_JOBS: dict[str, dict] = {}
MAP_STATS_IMPORT_WORKERS: dict[str, threading.Thread] = {}
MAP_STATS_IMPORT_JOBS_LOCK = threading.Lock()


def ensure_map_stats_import_schema() -> None:
    conn = connect_team_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS team_map_stats_import_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                items_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                progress REAL NOT NULL DEFAULT 0,
                processed_teams INTEGER NOT NULL DEFAULT 0,
                total_teams INTEGER NOT NULL DEFAULT 0,
                ok INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                pause_requested INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_team_map_stats_jobs_updated ON team_map_stats_import_jobs(updated_at DESC)")
        conn.commit()
    finally:
        conn.close()


def _map_stats_job_from_row(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["items"] = json.loads(out.pop("items_json") or "[]")
    out["results"] = json.loads(out.pop("results_json") or "[]")
    out["pause_requested"] = bool(out.get("pause_requested"))
    out["cancel_requested"] = bool(out.get("cancel_requested"))
    out["progress"] = float(out.get("progress") or 0.0)
    return out


def _get_stored_map_stats_job(job_id: str) -> dict | None:
    ensure_map_stats_import_schema()
    conn = connect_team_db()
    try:
        row = conn.execute("SELECT * FROM team_map_stats_import_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _map_stats_job_from_row(row) if row else None
    finally:
        conn.close()


def _save_map_stats_job(job: dict) -> None:
    ensure_map_stats_import_schema()
    now = time.time()
    job["updated_at"] = now
    conn = connect_team_db()
    try:
        conn.execute(
            """
            INSERT INTO team_map_stats_import_jobs (
                job_id, status, items_json, results_json, error, last_error,
                progress, processed_teams, total_teams, ok, failed,
                pause_requested, cancel_requested, created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                items_json = excluded.items_json,
                results_json = excluded.results_json,
                error = excluded.error,
                last_error = excluded.last_error,
                progress = excluded.progress,
                processed_teams = excluded.processed_teams,
                total_teams = excluded.total_teams,
                ok = excluded.ok,
                failed = excluded.failed,
                pause_requested = excluded.pause_requested,
                cancel_requested = excluded.cancel_requested,
                updated_at = excluded.updated_at,
                started_at = COALESCE(excluded.started_at, team_map_stats_import_jobs.started_at),
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
                int(job.get("processed_teams") or 0),
                int(job.get("total_teams") or 0),
                int(job.get("ok") or 0),
                int(job.get("failed") or 0),
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


def _publish_map_stats_job(job: dict) -> None:
    with MAP_STATS_IMPORT_JOBS_LOCK:
        MAP_STATS_IMPORT_JOBS[str(job["job_id"])] = dict(job)
    _save_map_stats_job(job)


def _map_stats_worker_is_active(job_id: str) -> bool:
    with MAP_STATS_IMPORT_JOBS_LOCK:
        worker = MAP_STATS_IMPORT_WORKERS.get(job_id)
        return bool(worker and worker.is_alive())


def _map_stats_job_response(job: dict) -> dict:
    return {
        "job_id": str(job["job_id"]),
        "status": job.get("status", "queued"),
        "error": job.get("error", ""),
        "last_error": job.get("last_error", ""),
        "progress": job.get("progress", 0.0),
        "processed_teams": job.get("processed_teams", 0),
        "total_teams": job.get("total_teams", 0),
        "ok": job.get("ok", 0),
        "failed": job.get("failed", 0),
        "pause_requested": bool(job.get("pause_requested")),
        "cancel_requested": bool(job.get("cancel_requested")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "results": (job.get("results") or [])[-50:],
    }


def _get_map_stats_job_for_response(job_id: str) -> dict:
    job = _get_stored_map_stats_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found")
    if job.get("status") in {"queued", "running", "pausing", "canceling"} and not _map_stats_worker_is_active(job_id):
        job["status"] = "canceled" if job.get("cancel_requested") else "paused"
        job["last_error"] = job.get("last_error") or "Job was interrupted before completion. Resume to continue."
        job["error"] = ""
        job["pause_requested"] = False
        job["cancel_requested"] = False
        _publish_map_stats_job(job)
    return job


def _import_team_map_stats_item(item: dict) -> dict:
    team_id = int(item.get("team_id") or 0)
    hltv_team_id = int(item.get("hltv_team_id") or 0)
    team_name = str(item.get("team_name") or "").strip()
    if team_id <= 0:
        raise ValueError("team_id is required")
    if hltv_team_id <= 0:
        raise ValueError("hltv_team_id is required")
    stats = get_hltv_team_map_stats(hltv_team_id, team_name)
    update_team_map_stats(
        team_id,
        map_stats_json=json.dumps(stats.get("maps") or []),
        source_url=str(stats.get("url") or ""),
        hltv_team_id=hltv_team_id,
    )
    return {
        "team_id": team_id,
        "team_name": team_name,
        "hltv_team_id": hltv_team_id,
        "status": "ok",
        "source_url": stats.get("url"),
        "maps": len(stats.get("maps") or []),
    }


def _run_map_stats_import_job(job_id: str) -> None:
    job = _get_stored_map_stats_job(job_id)
    if not job:
        return
    if job.get("cancel_requested") or job.get("pause_requested"):
        job["status"] = "canceled" if job.get("cancel_requested") else "paused"
        job["pause_requested"] = False
        job["cancel_requested"] = False
        job["error"] = ""
        job["last_error"] = job.get("last_error") or ("Canceled" if job["status"] == "canceled" else "Paused")
        job["finished_at"] = time.time()
        _publish_map_stats_job(job)
        with MAP_STATS_IMPORT_JOBS_LOCK:
            MAP_STATS_IMPORT_WORKERS.pop(job_id, None)
        return

    job["status"] = "running"
    job["pause_requested"] = False
    job["cancel_requested"] = False
    job["error"] = ""
    job["started_at"] = job.get("started_at") or time.time()
    job["finished_at"] = None
    _publish_map_stats_job(job)
    try:
        items = job.get("items") or []
        results = list(job.get("results") or [])
        processed_ids = {int(row.get("team_id") or 0) for row in results if int(row.get("team_id") or 0) > 0}
        ok = len([r for r in results if r.get("status") == "ok"])
        failed = len([r for r in results if r.get("status") == "error"])

        for item in items:
            team_id = int(item.get("team_id") or 0)
            if team_id in processed_ids:
                continue
            latest = _get_stored_map_stats_job(job_id) or job
            if latest.get("cancel_requested") or latest.get("pause_requested"):
                job.update(latest)
                job["status"] = "canceled" if latest.get("cancel_requested") else "paused"
                job["pause_requested"] = False
                job["cancel_requested"] = False
                job["error"] = ""
                job["last_error"] = "Canceled" if job["status"] == "canceled" else "Paused"
                job["finished_at"] = time.time()
                _publish_map_stats_job(job)
                return
            try:
                row = _import_team_map_stats_item(item)
                ok += 1
            except Exception as exc:
                failed += 1
                row = {
                    "team_id": team_id,
                    "team_name": item.get("team_name") or "",
                    "hltv_team_id": item.get("hltv_team_id"),
                    "status": "error",
                    "detail": str(exc),
                }
                job["last_error"] = f"{row['team_name'] or team_id}: {exc}"
            results.append(row)
            processed_ids.add(team_id)
            processed = ok + failed
            total = int(job.get("total_teams") or len(items))
            job["results"] = results
            job["ok"] = ok
            job["failed"] = failed
            job["processed_teams"] = min(processed, total)
            job["progress"] = 0.0 if total <= 0 else min(1.0, float(processed) / float(total))
            _publish_map_stats_job(job)

        job["status"] = "completed"
        job["progress"] = 1.0
        job["finished_at"] = time.time()
        job["error"] = ""
        _publish_map_stats_job(job)
    except Exception as exc:
        logger.exception("Map stats import job failed: job_id=%s", job_id)
        job["status"] = "failed"
        job["error"] = str(exc)
        job["last_error"] = str(exc)
        job["finished_at"] = time.time()
        _publish_map_stats_job(job)
    finally:
        with MAP_STATS_IMPORT_JOBS_LOCK:
            MAP_STATS_IMPORT_WORKERS.pop(job_id, None)


def _start_map_stats_worker(job_id: str) -> None:
    with MAP_STATS_IMPORT_JOBS_LOCK:
        worker = MAP_STATS_IMPORT_WORKERS.get(job_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(target=_run_map_stats_import_job, args=(job_id,), daemon=True)
        MAP_STATS_IMPORT_WORKERS[job_id] = worker
        worker.start()


def _get_latest_map_stats_job(*, include_completed: bool = True) -> dict | None:
    ensure_map_stats_import_schema()
    statuses = "" if include_completed else "WHERE status IN ('queued', 'running', 'pausing', 'canceling', 'paused', 'failed')"
    conn = connect_team_db()
    try:
        row = conn.execute(f"SELECT * FROM team_map_stats_import_jobs {statuses} ORDER BY updated_at DESC LIMIT 1").fetchone()
        return _get_map_stats_job_for_response(str(row["job_id"])) if row else None
    finally:
        conn.close()


def _build_map_stats_import_items(payload: dict | None = None) -> list[dict]:
    body = payload or {}
    requested_ids = set()
    for raw_id in body.get("team_ids") or []:
        try:
            team_id = int(raw_id)
        except Exception:
            continue
        if team_id > 0:
            requested_ids.add(team_id)
    missing_only = bool(body.get("missing_only"))
    teams = get_all_teams()
    selected = [t for t in teams if not requested_ids or int(t.get("team_id") or 0) in requested_ids]
    if any(int(t.get("team_id") or 0) > 0 and not (int(t.get("hltv_team_id") or 0) > 0) for t in selected):
        refresh_hltv_for_all_teams_today()
        teams = get_all_teams()
        selected = [t for t in teams if not requested_ids or int(t.get("team_id") or 0) in requested_ids]
    items = []
    for team in selected:
        if missing_only and team.get("map_stats_json"):
            continue
        team_id = int(team.get("team_id") or 0)
        hltv_team_id = int(team.get("hltv_team_id") or 0)
        if team_id <= 0 or hltv_team_id <= 0:
            continue
        items.append(
            {
                "team_id": team_id,
                "team_name": str(team.get("name") or ""),
                "hltv_team_id": hltv_team_id,
            }
        )
    return items


@router.get("/")
def list_teams():
    return get_all_teams()


@router.get("/hltv-ranking/by-date")
def fetch_hltv_ranking_by_date(team_name: str, on_date: str):
    """
    Fetch HLTV ranking position ("hltv_rating") and points for a team on a specific date.
    `on_date` must be YYYY-MM-DD.
    """
    if not team_name or not team_name.strip():
        raise HTTPException(status_code=400, detail="team_name is required")

    try:
        parsed_date = date.fromisoformat(on_date)
    except Exception:
        raise HTTPException(status_code=400, detail="on_date must be in YYYY-MM-DD format")

    try:
        return get_team_hltv_rating_and_points_on_date(team_name, parsed_date)
    except TeamNotRankedError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RankingPageParseError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except HLTVRankingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/refresh-hltv-today")
def refresh_hltv_for_all_teams_today():
    """
    Refresh all teams' HLTV rank and HLTV points for today's date.
    """
    today = date.today()
    teams = get_all_teams()
    teams_by_name = {(t.get("name") or "").strip().lower(): t for t in teams if (t.get("name") or "").strip()}
    logger.info("HLTV refresh requested: date=%s existing_teams=%d", today.isoformat(), len(teams))

    results = []
    updated = 0
    failed = 0
    snapshot = None

    try:
        snapshot = get_latest_hltv_rankings()
        logger.info(
            "HLTV latest snapshot selected: requested=%s effective=%s",
            snapshot.get("requested_date"),
            snapshot.get("effective_date"),
        )
    except HLTVRankingError as exc:
        logger.error("HLTV refresh snapshot fetch failed: %s", exc)
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": len([t for t in teams if (t.get("name") or "").strip()]),
            "results": [
                {
                    "team_id": t.get("team_id"),
                    "team_name": (t.get("name") or "").strip(),
                    "status": "error",
                    "detail": str(exc),
                }
                for t in teams
                if (t.get("name") or "").strip()
            ],
        }

    rankings = snapshot.get("rankings_by_team") or {}
    effective_date = snapshot.get("effective_date")
    requested_date = snapshot.get("requested_date")
    days_back = int(snapshot.get("days_back") or 0)

    if not rankings:
        logger.warning("HLTV refresh snapshot contains no teams")
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": 0,
            "inserted": 0,
            "results": [],
        }

    inserted = 0
    for _, data in rankings.items():
        name = (data.get("team_name") or "").strip()
        if not name:
            continue
        try:
            existing = teams_by_name.get(name.lower()) or {}
            before_exists = bool(existing)
            hltv_team_id = data.get("hltv_team_id") or existing.get("hltv_team_id")
            add_or_update_team(
                name=name,
                hltv_rank=int(data.get("hltv_rating") or 999),
                hltv_points=int(data.get("points") or 0),
                vrs_rank=int(existing.get("vrs_rank") or 999),
                vrs_points=int(existing.get("vrs_points") or 0),
                win_rate=float(existing.get("win_rate") or 0.5),
                player_ids=[
                    int(existing.get("player1_id") or 0),
                    int(existing.get("player2_id") or 0),
                    int(existing.get("player3_id") or 0),
                    int(existing.get("player4_id") or 0),
                    int(existing.get("player5_id") or 0),
                ],
                hltv_team_id=hltv_team_id,
            )
            updated += 1
            if not before_exists:
                inserted += 1
            results.append(
                {
                    "team_id": existing.get("team_id"),
                    "team_name": name,
                    "status": "ok",
                    "hltv_rank": int(data.get("hltv_rating") or 999),
                    "hltv_points": int(data.get("points") or 0),
                    "hltv_team_id": hltv_team_id,
                    "requested_date": requested_date,
                    "effective_date": effective_date,
                    "days_back": days_back,
                    "inserted": not before_exists,
                }
            )
        except Exception as exc:
            logger.error("HLTV refresh error: team='%s' detail=%s", name, exc)
            results.append({"team_name": name, "status": "error", "detail": str(exc)})
            failed += 1

    logger.info("HLTV refresh complete: updated=%d inserted=%d failed=%d", updated, inserted, failed)
    return {
        "status": "ok",
        "date": today.isoformat(),
        "updated": updated,
        "inserted": inserted,
        "failed": failed,
        "results": results,
    }


@router.get("/vrs-ranking/by-date")
def fetch_vrs_ranking_by_date(team_name: str, on_date: str):
    if not team_name or not team_name.strip():
        raise HTTPException(status_code=400, detail="team_name is required")

    try:
        parsed_date = date.fromisoformat(on_date)
    except Exception:
        raise HTTPException(status_code=400, detail="on_date must be in YYYY-MM-DD format")

    try:
        return get_team_vrs_rank_and_points_on_date(team_name, parsed_date)
    except TeamNotRankedError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RankingPageParseError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except HLTVRankingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/refresh-vrs-today")
def refresh_vrs_for_all_teams_today():
    today = date.today()
    teams = get_all_teams()
    teams_by_name = {(t.get("name") or "").strip().lower(): t for t in teams if (t.get("name") or "").strip()}
    logger.info("VRS refresh requested: date=%s existing_teams=%d", today.isoformat(), len(teams))

    results = []
    updated = 0
    failed = 0

    try:
        snapshot = get_latest_vrs_rankings()
        logger.info(
            "VRS latest snapshot selected: requested=%s effective=%s",
            snapshot.get("requested_date"),
            snapshot.get("effective_date"),
        )
    except HLTVRankingError as exc:
        logger.error("VRS refresh snapshot fetch failed: %s", exc)
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": len([t for t in teams if (t.get("name") or "").strip()]),
            "results": [
                {
                    "team_id": t.get("team_id"),
                    "team_name": (t.get("name") or "").strip(),
                    "status": "error",
                    "detail": str(exc),
                }
                for t in teams
                if (t.get("name") or "").strip()
            ],
        }

    rankings = snapshot.get("rankings_by_team") or {}
    effective_date = snapshot.get("effective_date")
    requested_date = snapshot.get("requested_date")
    days_back = int(snapshot.get("days_back") or 0)

    if not rankings:
        logger.warning("VRS refresh snapshot contains no teams")
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": 0,
            "inserted": 0,
            "results": [],
        }

    inserted = 0
    for _, data in rankings.items():
        name = (data.get("team_name") or "").strip()
        if not name:
            continue
        try:
            existing = teams_by_name.get(name.lower()) or {}
            before_exists = bool(existing)
            add_or_update_team(
                name=name,
                hltv_rank=int(existing.get("hltv_rank") or 999),
                hltv_points=int(existing.get("hltv_points") or 0),
                vrs_rank=int(data.get("vrs_rank") or 999),
                vrs_points=int(data.get("points") or 0),
                win_rate=float(existing.get("win_rate") or 0.5),
                player_ids=[
                    int(existing.get("player1_id") or 0),
                    int(existing.get("player2_id") or 0),
                    int(existing.get("player3_id") or 0),
                    int(existing.get("player4_id") or 0),
                    int(existing.get("player5_id") or 0),
                ],
                hltv_team_id=existing.get("hltv_team_id"),
            )
            updated += 1
            if not before_exists:
                inserted += 1
            results.append(
                {
                    "team_id": existing.get("team_id"),
                    "team_name": name,
                    "status": "ok",
                    "vrs_rank": int(data.get("vrs_rank") or 999),
                    "vrs_points": int(data.get("points") or 0),
                    "requested_date": requested_date,
                    "effective_date": effective_date,
                    "days_back": days_back,
                    "inserted": not before_exists,
                }
            )
        except Exception as exc:
            logger.error("VRS refresh error: team='%s' detail=%s", name, exc)
            results.append({"team_name": name, "status": "error", "detail": str(exc)})
            failed += 1

    logger.info("VRS refresh complete: updated=%d inserted=%d failed=%d", updated, inserted, failed)
    return {
        "status": "ok",
        "date": today.isoformat(),
        "updated": updated,
        "inserted": inserted,
        "failed": failed,
        "results": results,
    }


@router.post("/refresh-rankings-today")
def refresh_all_rankings_for_all_teams_today():
    """
    Run HLTV + VRS refresh in one call.
    """
    hltv = refresh_hltv_for_all_teams_today()
    vrs = refresh_vrs_for_all_teams_today()
    return {
        "status": "ok",
        "date": date.today().isoformat(),
        "hltv": hltv,
        "vrs": vrs,
        "updated_total": int(hltv.get("updated", 0)) + int(vrs.get("updated", 0)),
        "inserted_total": int(hltv.get("inserted", 0)) + int(vrs.get("inserted", 0)),
        "failed_total": int(hltv.get("failed", 0)) + int(vrs.get("failed", 0)),
    }


@router.post("/map-stats-import/start")
def start_map_stats_import_job(payload: dict | None = None):
    latest = _get_latest_map_stats_job(include_completed=False)
    if latest and latest.get("status") in {"queued", "running", "pausing", "canceling"}:
        return {"job_id": latest["job_id"], "status": latest.get("status"), "reused": True}
    items = _build_map_stats_import_items(payload)
    if not items:
        raise HTTPException(status_code=400, detail="No teams with HLTV team ids were found for map-stats import.")
    now = time.time()
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    job = {
        "job_id": job_id,
        "status": "queued",
        "error": "",
        "last_error": "",
        "progress": 0.0,
        "processed_teams": 0,
        "total_teams": len(items),
        "ok": 0,
        "failed": 0,
        "pause_requested": False,
        "cancel_requested": False,
        "items": items,
        "results": [],
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    _publish_map_stats_job(job)
    _start_map_stats_worker(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/map-stats-import/latest")
def get_latest_map_stats_import_job():
    job = _get_latest_map_stats_job()
    if not job:
        return {"exists": False}
    return {"exists": True, **_map_stats_job_response(job)}


@router.get("/map-stats-import/job/{job_id}")
def get_map_stats_import_job(job_id: str):
    return _map_stats_job_response(_get_map_stats_job_for_response(job_id))


@router.post("/map-stats-import/job/{job_id}/pause")
def pause_map_stats_import_job(job_id: str):
    job = _get_map_stats_job_for_response(job_id)
    if job.get("status") in {"completed", "failed", "canceled"}:
        return _map_stats_job_response(job)
    if job.get("status") == "canceling" or job.get("cancel_requested"):
        return _map_stats_job_response(job)
    job["pause_requested"] = True
    job["status"] = "pausing" if job.get("status") == "running" or _map_stats_worker_is_active(job_id) else "paused"
    _publish_map_stats_job(job)
    return _map_stats_job_response(job)


@router.post("/map-stats-import/job/{job_id}/cancel")
def cancel_map_stats_import_job(job_id: str):
    job = _get_map_stats_job_for_response(job_id)
    if job.get("status") in {"completed", "failed", "canceled"}:
        return _map_stats_job_response(job)
    job["cancel_requested"] = True
    job["pause_requested"] = False
    job["last_error"] = "Cancel requested"
    job["status"] = "canceling" if job.get("status") == "running" or _map_stats_worker_is_active(job_id) else "canceled"
    if job["status"] == "canceled":
        job["cancel_requested"] = False
        job["finished_at"] = time.time()
    _publish_map_stats_job(job)
    return _map_stats_job_response(job)


@router.post("/map-stats-import/job/{job_id}/resume")
def resume_map_stats_import_job(job_id: str):
    job = _get_map_stats_job_for_response(job_id)
    if job.get("status") in {"completed", "canceled"}:
        return _map_stats_job_response(job)
    if job.get("status") == "running" and _map_stats_worker_is_active(job_id):
        return _map_stats_job_response(job)
    job["status"] = "queued"
    job["pause_requested"] = False
    job["cancel_requested"] = False
    job["error"] = ""
    job["finished_at"] = None
    _publish_map_stats_job(job)
    _start_map_stats_worker(job_id)
    return _map_stats_job_response(_get_map_stats_job_for_response(job_id))


@router.get("/{team_id}")
def fetch_team(team_id: int):
    team = get_team_by_id(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.post("/{team_id}/refresh-map-stats")
def refresh_team_map_stats(team_id: int, payload: dict | None = None):
    body = payload or {}
    team = get_team_by_id(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    hltv_team_id = body.get("hltv_team_id") or team.get("hltv_team_id")
    if hltv_team_id is None or str(hltv_team_id).strip() == "":
        raise HTTPException(status_code=400, detail="Set this team's HLTV team id before importing map stats.")
    try:
        hltv_team_id = int(hltv_team_id)
    except Exception:
        raise HTTPException(status_code=400, detail="A valid HLTV team id is required.")
    slug = str(body.get("slug") or team.get("name") or "").strip()
    try:
        stats = get_hltv_team_map_stats(hltv_team_id, slug)
    except RankingPageParseError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except HLTVRankingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    update_team_map_stats(
        int(team_id),
        map_stats_json=json.dumps(stats.get("maps") or []),
        source_url=str(stats.get("url") or ""),
        hltv_team_id=hltv_team_id,
    )
    return {
        "status": "ok",
        "team_id": int(team_id),
        "hltv_team_id": hltv_team_id,
        "source_url": stats.get("url"),
        "maps": stats.get("maps") or [],
    }


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
