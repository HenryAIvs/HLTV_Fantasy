import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from backend.swiss_stage.fantasy_montecarlo import simulate_swiss_fantasy
from backend.data.db import connect as _connect
from backend.data.singleton_state import SingletonState
from backend.data.team_db import get_all_teams



router = APIRouter()
SIM_JOBS = {}
SIM_JOBS_LOCK = threading.Lock()


_SIM_STATE = SingletonState(
    "swiss_simulation_state", result_column="results_json", result_key="results", iso_timestamps=True, keyed=True
)


def _event_key(event_id=None) -> int:
    """Stored swiss runs are keyed by fantasy event (the active one unless a
    read asks for ?event_id=)."""
    from backend.data.event_db import get_active_event_id

    try:
        if event_id:
            return int(event_id)
    except (TypeError, ValueError):
        pass
    return int(get_active_event_id() or 1)


def ensure_simulation_schema() -> None:
    _SIM_STATE.ensure_table()


def save_latest_simulation(payload: dict, results: dict, event_id=None) -> None:
    _SIM_STATE.save(payload, results, key=_event_key(event_id))


def load_latest_simulation(event_id=None) -> dict | None:
    return _SIM_STATE.load(key=_event_key(event_id))


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

    # Bo mode comes from the event's detected format (Swiss Bo3 events run
    # 'all'; Major-style Bo1-with-Bo3-deciders runs 'elim_qual').
    bo3_mode = str(payload.get("bo3_mode") or "elim_qual")
    if bo3_mode not in ("all", "none", "elim_qual"):
        raise HTTPException(status_code=400, detail=f"Unknown bo3_mode '{bo3_mode}'")

    return {
        "team_ids": team_ids,
        "vrs_ranks": vrs_ranks,
        "bo3_mode": bo3_mode,
        "n_sims": int(payload["n_sims"]),
    }


# Simulation count for an automatic (baked) Swiss run.
BAKE_SIMS = 20000


def bake_event_swiss(
    event_id: int, trigger: str = "import", only_if_missing: bool = False, refresh_inputs: bool = False
) -> dict:
    """Swiss counterpart of groups.bake_event_valuations, called through
    event_pipeline.bake_event: the event page's seed order and Bo rules, the
    stage simulated, then the roster combinations the Top 5 tab reads. Never
    raises; the scheduler records the returned status."""
    from backend.routes import best_team as best_team_routes
    from backend.routes import events as events_routes

    started = time.time()
    event_id = int(event_id)
    if only_if_missing and load_latest_simulation(event_id):
        return {"status": "exists", "event_id": event_id, "reason": "already baked; not refreshed"}
    try:
        ctx = events_routes.get_event_swiss_context(event_id)
    except HTTPException as exc:
        return {"status": "skipped", "event_id": event_id, "reason": f"swiss stage: {exc.detail}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "event_id": event_id, "reason": f"swiss stage: {str(exc)[:200]}"}
    team_ids = [int(t) for t in (ctx.get("team_ids") or []) if t]
    if len(team_ids) < 2:
        return {"status": "skipped", "event_id": event_id, "reason": "no teams of the swiss stage match our teams"}
    seeds = {int(t): int(s) for t, s in (ctx.get("seed_by_team_id") or {}).items()}
    try:
        payload = _normalize_sim_payload(
            {"team_ids": team_ids, "vrs_ranks": seeds, "bo3_mode": ctx.get("bo3_mode") or "elim_qual", "n_sims": BAKE_SIMS}
        )
    except HTTPException as exc:
        return {"status": "skipped", "event_id": event_id, "reason": f"invalid swiss setup: {exc.detail}"}
    payload["event_id"] = event_id
    payload["baked"] = {"trigger": trigger, "at": started, "refresh_inputs": bool(refresh_inputs)}
    if ctx.get("unmatched"):
        payload["unmatched"] = list(ctx.get("unmatched") or [])
    try:
        result = simulate_swiss_fantasy(
            team_ids=payload["team_ids"],
            vrs_ranks=payload["vrs_ranks"],
            bo3_mode=payload["bo3_mode"],
            n_sims=int(payload["n_sims"]),
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "event_id": event_id, "reason": f"swiss run failed: {str(exc)[:300]}"}
    save_latest_simulation(payload, result, event_id)
    sim_seconds = round(time.time() - started, 1)
    try:
        combos = best_team_routes._compute_best_teams_from_results(result, {})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "event_id": event_id, "reason": f"combinations failed: {str(exc)[:300]}", "sim_seconds": sim_seconds}
    return {
        "status": "ok",
        "event_id": event_id,
        "kind": "swiss",
        "teams": len(team_ids),
        "sims": int(payload["n_sims"]),
        "bo3_mode": payload["bo3_mode"],
        "rosters": int(combos.get("total_teams") or 0),
        "unmatched": payload.get("unmatched") or [],
        "sim_seconds": sim_seconds,
        "seconds": round(time.time() - started, 1),
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
      - bo3_mode: ignored; CS2 Swiss uses elim_qual
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
def get_latest_simulation(event_id: int | None = None):
    row = load_latest_simulation(event_id)
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
        conn.execute("DELETE FROM swiss_simulation_state WHERE singleton_id = ?", (_event_key(None),))
        conn.commit()
    finally:
        conn.close()
    _SIM_STATE.invalidate()
    return {"status": "ok"}
