import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from swiss_stage.fantasy_montecarlo import simulate_swiss_fantasy
from backend.data.team_db import get_all_teams
from backend.data.player_db import DB_PATH


router = APIRouter()
SIM_JOBS = {}
SIM_JOBS_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_simulation_schema() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS swiss_simulation_state (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                payload_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_latest_simulation(payload: dict, results: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO swiss_simulation_state (singleton_id, payload_json, results_json, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                results_json = excluded.results_json,
                updated_at = excluded.updated_at
            """,
            (
                json.dumps(payload),
                json.dumps(results),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_latest_simulation() -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT payload_json, results_json, updated_at
            FROM swiss_simulation_state
            WHERE singleton_id = 1
            """
        ).fetchone()
        if not row:
            return None
        return {
            "payload": json.loads(row["payload_json"]),
            "results": json.loads(row["results_json"]),
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def _normalize_sim_payload(payload: dict) -> dict:
    required = ["team_ids", "vrs_ranks", "bo3_mode", "n_sims"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    vrs_ranks_raw = payload.get("vrs_ranks", {})
    vrs_ranks = {int(k): int(v) for k, v in vrs_ranks_raw.items()}
    team_ids = payload.get("team_ids") or []
    if team_ids:
        db_vrs = {t["team_id"]: t.get("vrs_rank", 999) for t in get_all_teams()}
        for tid in team_ids:
            if tid not in vrs_ranks:
                vrs_ranks[tid] = db_vrs.get(tid, 999)

    return {
        "team_ids": team_ids,
        "vrs_ranks": vrs_ranks,
        "bo3_mode": payload["bo3_mode"],
        "n_sims": int(payload["n_sims"]),
    }


def _run_sim_job(job_id: str, payload: dict) -> None:
    def _update_progress(processed: int, total: int) -> None:
        with SIM_JOBS_LOCK:
            job = SIM_JOBS.get(job_id)
            if not job:
                return
            job["processed_sims"] = int(processed)
            job["total_sims"] = int(total)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["updated_at"] = time.time()

    with SIM_JOBS_LOCK:
        job = SIM_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = time.time()

    try:
        result = simulate_swiss_fantasy(
            team_ids=payload["team_ids"],
            vrs_ranks=payload["vrs_ranks"],
            bo3_mode=payload["bo3_mode"],
            n_sims=int(payload["n_sims"]),
            progress_callback=_update_progress,
        )
        save_latest_simulation(payload, result)
        with SIM_JOBS_LOCK:
            job = SIM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["result"] = result
            job["processed_sims"] = int(payload["n_sims"])
            job["total_sims"] = int(payload["n_sims"])
            job["progress"] = 1.0
            job["updated_at"] = time.time()
    except Exception as exc:
        with SIM_JOBS_LOCK:
            job = SIM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = time.time()


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
    normalized_payload = _normalize_sim_payload(payload)

    result = simulate_swiss_fantasy(
        team_ids=normalized_payload["team_ids"],
        vrs_ranks=normalized_payload["vrs_ranks"],
        bo3_mode=normalized_payload["bo3_mode"],
        n_sims=int(normalized_payload["n_sims"]),
    )
    save_latest_simulation(normalized_payload, result)
    return result


@router.post("/start")
def start_simulation(payload: dict):
    normalized_payload = _normalize_sim_payload(payload)
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    with SIM_JOBS_LOCK:
        SIM_JOBS[job_id] = {
            "status": "queued",
            "error": "",
            "progress": 0.0,
            "processed_sims": 0,
            "total_sims": int(normalized_payload["n_sims"]),
            "result": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    worker = threading.Thread(target=_run_sim_job, args=(job_id, normalized_payload), daemon=True)
    worker.start()
    return {"job_id": job_id}


@router.get("/job/{job_id}")
def get_simulation_job(job_id: str):
    with SIM_JOBS_LOCK:
        job = SIM_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_id not found")
        out = dict(job)

    return {
        "job_id": job_id,
        "status": out.get("status", "queued"),
        "error": out.get("error", ""),
        "progress": out.get("progress", 0.0),
        "processed_sims": out.get("processed_sims", 0),
        "total_sims": out.get("total_sims", 0),
        "result": out.get("result"),
    }


@router.get("/latest")
def get_latest_simulation():
    row = load_latest_simulation()
    if not row:
        return {"exists": False}
    return {
        "exists": True,
        "payload": row["payload"],
        "results": row["results"],
        "updated_at": row["updated_at"],
    }


@router.delete("/latest")
def reset_latest_simulation():
    conn = _connect()
    try:
        conn.execute("DELETE FROM swiss_simulation_state WHERE singleton_id = 1")
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}
