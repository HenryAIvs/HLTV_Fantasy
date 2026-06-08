import json
import math
import sqlite3
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException

from backend.routes.simulation import load_latest_simulation
from backend.data.player_db import DB_PATH, get_player
from backend.services.team_optimizer import (
    iter_valid_rosters,
    parse_optimizer_payload,
    serialize_roster,
)
from swiss_stage.fantasy_montecarlo import simulate_swiss_fantasy
from backend.data.team_db import get_all_teams

router = APIRouter()

BEST_TEAM_JOBS = {}
BEST_TEAM_JOBS_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_best_team_schema() -> None:
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS best_team_runs (
                cache_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                total_teams INTEGER NOT NULL DEFAULT 0,
                player_count INTEGER NOT NULL DEFAULT 0,
                players_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS best_team_combos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_id TEXT NOT NULL,
                total_ev REAL NOT NULL,
                cost INTEGER NOT NULL,
                p1_id INTEGER NOT NULL,
                p2_id INTEGER NOT NULL,
                p3_id INTEGER NOT NULL,
                p4_id INTEGER NOT NULL,
                p5_id INTEGER NOT NULL,
                r1 TEXT NOT NULL,
                r2 TEXT NOT NULL,
                r3 TEXT NOT NULL,
                r4 TEXT NOT NULL,
                r5 TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_runs_created ON best_team_runs(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_combos_cache_ev ON best_team_combos(cache_id, total_ev DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_combos_cache_cost ON best_team_combos(cache_id, cost ASC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_combos_cache_p1 ON best_team_combos(cache_id, p1_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_combos_cache_p2 ON best_team_combos(cache_id, p2_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_combos_cache_p3 ON best_team_combos(cache_id, p3_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_combos_cache_p4 ON best_team_combos(cache_id, p4_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_best_team_combos_cache_p5 ON best_team_combos(cache_id, p5_id)")
        conn.commit()
    finally:
        conn.close()


def _contains_pid_sql(pid: int) -> str:
    return f"(p1_id = {pid} OR p2_id = {pid} OR p3_id = {pid} OR p4_id = {pid} OR p5_id = {pid})"


def _order_by_sql(sort_key: str) -> str:
    if sort_key == "ev_asc":
        return "total_ev ASC, id ASC"
    if sort_key == "cost_asc":
        return "cost ASC, total_ev DESC"
    if sort_key == "cost_desc":
        return "cost DESC, total_ev DESC"
    if sort_key == "cpp_desc":
        return "(total_ev / CASE WHEN cost <= 0 THEN 1 ELSE cost END) DESC, total_ev DESC"
    return "total_ev DESC, id ASC"


def _build_query_filters(include_ids, exclude_ids, search_q: str, players_meta: dict):
    clauses = []
    include = [int(x) for x in (include_ids or []) if str(x).strip()]
    exclude = [int(x) for x in (exclude_ids or []) if str(x).strip()]
    for pid in include:
        clauses.append(_contains_pid_sql(pid))
    for pid in exclude:
        clauses.append(f"NOT {_contains_pid_sql(pid)}")

    q = (search_q or "").strip().lower()
    if q:
        pid_candidates = set()
        if q.isdigit():
            qid = int(q)
            for p in players_meta.values():
                if int(p.get("player_id", 0)) == qid or int(p.get("team_id", 0)) == qid:
                    pid_candidates.add(int(p["player_id"]))
        else:
            for p in players_meta.values():
                name = str(p.get("name", "")).lower()
                team_name = str(p.get("team_name", "")).lower()
                if q in name or q in team_name:
                    pid_candidates.add(int(p["player_id"]))

        search_sub = []
        for pid in sorted(pid_candidates):
            search_sub.append(_contains_pid_sql(pid))
        role_like = q.replace("'", "''")
        search_sub.append(f"(lower(r1) LIKE '%{role_like}%' OR lower(r2) LIKE '%{role_like}%' OR lower(r3) LIKE '%{role_like}%' OR lower(r4) LIKE '%{role_like}%' OR lower(r5) LIKE '%{role_like}%')")
        clauses.append("(" + " OR ".join(search_sub) + ")")

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)
    return where_sql


def _serialize_combo_row(row: sqlite3.Row, players_meta: dict) -> dict:
    pids = [int(row["p1_id"]), int(row["p2_id"]), int(row["p3_id"]), int(row["p4_id"]), int(row["p5_id"])]
    roles = [row["r1"], row["r2"], row["r3"], row["r4"], row["r5"]]
    players = []
    for pid, role_name in zip(pids, roles):
        meta = players_meta.get(str(pid)) or {}
        players.append(
            {
                "player_id": pid,
                "name": meta.get("name", f"Player {pid}"),
                "team_id": int(meta.get("team_id", 0)),
                "price": int(meta.get("price", 0)),
                "rating_ev": float(meta.get("rating_ev", 0.0)),
                "win_ev": float(meta.get("win_ev", 0.0)),
                "role_ev": float(meta.get("role_ev", 0.0)),
                "booster_ev": float(meta.get("booster_ev", 0.0)),
                "total_ev": float(meta.get("total_ev", 0.0)),
                "role_name": str(role_name),
            }
        )
    return {
        "total_ev": float(row["total_ev"]),
        "cost": int(row["cost"]),
        "players": players,
    }


def _query_cached_best_teams(cache_id: str, include_ids, exclude_ids, search_q: str, sort_key: str, page: int, page_size: int):
    conn = _connect()
    try:
        run_row = conn.execute(
            "SELECT total_teams, players_json FROM best_team_runs WHERE cache_id = ?",
            (cache_id,),
        ).fetchone()
        if not run_row:
            raise HTTPException(status_code=404, detail="cache_id not found")
        players_meta = json.loads(run_row["players_json"])

        where_sql = _build_query_filters(include_ids, exclude_ids, search_q, players_meta)
        order_sql = _order_by_sql(sort_key)

        total_teams = int(run_row["total_teams"])
        filtered_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM best_team_combos WHERE cache_id = ?{where_sql.replace(' WHERE ', ' AND ')}",
            (cache_id,),
        ).fetchone()["c"]

        top_rows = conn.execute(
            f"""
            SELECT * FROM best_team_combos
            WHERE cache_id = ?{where_sql.replace(' WHERE ', ' AND ')}
            ORDER BY {order_sql}
            LIMIT 10
            """,
            (cache_id,),
        ).fetchall()

        start = page * page_size
        page_rows = conn.execute(
            f"""
            SELECT * FROM best_team_combos
            WHERE cache_id = ?{where_sql.replace(' WHERE ', ' AND ')}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            (cache_id, page_size, start),
        ).fetchall()

        return {
            "cache_id": cache_id,
            "total_teams": total_teams,
            "filtered_count": int(filtered_count),
            "top_teams": [_serialize_combo_row(r, players_meta) for r in top_rows],
            "page_teams": [_serialize_combo_row(r, players_meta) for r in page_rows],
            "page": page,
            "page_size": page_size,
        }
    finally:
        conn.close()


def _compute_best_teams_from_results(results: dict, payload: dict, progress_callback=None, finalize_callback=None) -> dict:
    options = parse_optimizer_payload(payload)
    budget = options["budget"]
    max_per_team = options["max_per_team"]
    exclude = options["exclude"]
    include = options["include"]

    team_name_map = {int(t["team_id"]): t.get("name", "") for t in get_all_teams()}

    def expected_games(team_result):
        p30 = team_result.get("3-0", 0.0)
        p31 = team_result.get("3-1", 0.0)
        p32 = team_result.get("3-2", 0.0)
        p23 = team_result.get("2-3", 0.0)
        p13 = team_result.get("1-3", 0.0)
        p03 = team_result.get("0-3", 0.0)
        return 3.0 * (p30 + p03) + 4.0 * (p31 + p13) + 5.0 * (p32 + p23)

    players_info = []
    expected_games_by_team = {tid: expected_games(res) for tid, res in results.items()}

    for tid, team_res in results.items():
        EG = expected_games_by_team.get(tid, 0.0)
        for pid_key, comps in (team_res.get("players", {}) or {}).items():
            pid = int(pid_key)
            if pid in exclude:
                continue
            row = get_player(pid)
            if not row:
                continue
            boosters_json = row.get("boosters_json", "")
            booster_ev = 0.0
            try:
                obj = json.loads(boosters_json) if boosters_json else {}
                if isinstance(obj, dict) and obj:
                    rates = [float(v) for v in obj.values() if isinstance(v, (int, float, str))]
                    rates = [r for r in rates if math.isfinite(r)]
                    if rates:
                        booster_ev = 5.0 * (sum(rates) / len(rates)) * max(0.0, EG)
            except Exception:
                booster_ev = 0.0

            rating_ev = float(comps.get("rating", 0.0))
            win_ev = float(comps.get("win", 0.0))
            role_ev = float(comps.get("role", 0.0))
            total_ev = rating_ev + win_ev + role_ev + booster_ev
            players_info.append(
                {
                    "player_id": pid,
                    "name": row.get("name", f"Player {pid}"),
                    "team_id": int(tid),
                    "team_name": team_name_map.get(int(tid), ""),
                    "price": int(row.get("price", 0)),
                    "rating_ev": rating_ev,
                    "win_ev": win_ev,
                    "role_ev": role_ev,
                    "booster_ev": booster_ev,
                    "total_ev": total_ev,
                }
            )

    if len(players_info) < 5:
        return {"error": "Not enough players after exclusions"}

    if include:
        available_ids = {p["player_id"] for p in players_info}
        missing_includes = [pid for pid in include if pid not in available_ids]
        if missing_includes:
            return {"error": f"Included players not available: {missing_includes}"}

    players_info_sorted = sorted(players_info, key=lambda x: -x["total_ev"])
    total_combinations = math.comb(len(players_info_sorted), 5)

    cache_id = uuid.uuid4().hex
    players_meta = {str(p["player_id"]): p for p in players_info_sorted}
    conn = _connect()
    top_entries = []
    total_valid = 0
    processed_combinations = 0
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO best_team_runs (cache_id, created_at, total_teams, player_count, players_json)
            VALUES (?, ?, 0, ?, ?)
            """,
            (cache_id, time.time(), len(players_info_sorted), json.dumps(players_meta)),
        )

        for roster in iter_valid_rosters(players_info_sorted, include, budget, max_per_team, progress_callback):
            processed_combinations = int(roster["processed"])
            combo_ids = roster["pids"]
            total_cost = int(roster["cost"])
            total_ev = float(roster["total_ev"])
            role_names = roster["roles"]
            conn.execute(
                """
                INSERT INTO best_team_combos (
                    cache_id, total_ev, cost,
                    p1_id, p2_id, p3_id, p4_id, p5_id,
                    r1, r2, r3, r4, r5
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_id,
                    total_ev,
                    int(total_cost),
                    int(combo_ids[0]),
                    int(combo_ids[1]),
                    int(combo_ids[2]),
                    int(combo_ids[3]),
                    int(combo_ids[4]),
                    role_names[0],
                    role_names[1],
                    role_names[2],
                    role_names[3],
                    role_names[4],
                ),
            )
            total_valid += 1

            entry = {"total_ev": total_ev, "cost": int(total_cost), "pids": combo_ids, "roles": role_names}
            if len(top_entries) < 10:
                top_entries.append(entry)
            else:
                worst_idx = min(range(len(top_entries)), key=lambda i: top_entries[i]["total_ev"])
                if entry["total_ev"] > top_entries[worst_idx]["total_ev"]:
                    top_entries[worst_idx] = entry

            if finalize_callback and (total_valid % 5000 == 0):
                finalize_callback(0.5, f"Persisting {total_valid:,} valid teams")

        if finalize_callback:
            finalize_callback(0.8, "Finalizing run metadata")
        conn.execute(
            "UPDATE best_team_runs SET total_teams = ? WHERE cache_id = ?",
            (int(total_valid), cache_id),
        )
        conn.commit()
        if finalize_callback:
            finalize_callback(1.0, "Done")
    except Exception:
        conn.rollback()
        conn.execute("DELETE FROM best_team_combos WHERE cache_id = ?", (cache_id,))
        conn.execute("DELETE FROM best_team_runs WHERE cache_id = ?", (cache_id,))
        conn.commit()
        raise
    finally:
        conn.close()

    top_entries.sort(key=lambda x: x["total_ev"], reverse=True)
    top_teams = [
        serialize_roster(players_meta, entry["pids"], entry["roles"], entry["total_ev"], entry["cost"])
        for entry in top_entries[:10]
    ]

    return {
        "cache_id": cache_id,
        "top_teams": top_teams,
        "total_teams": int(total_valid),
        "player_count": len(players_info_sorted),
        "processed_combinations": int(processed_combinations or total_combinations),
        "total_combinations": int(total_combinations),
    }


def _run_best_team_job(job_id: str, results: dict, payload: dict) -> None:
    def _update_progress(processed: int, total: int) -> None:
        with BEST_TEAM_JOBS_LOCK:
            job = BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["phase"] = "processing"
            job["processed_combinations"] = int(processed)
            job["total_combinations"] = int(total)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["updated_at"] = time.time()

    def _update_finalize(progress: float, step: str) -> None:
        with BEST_TEAM_JOBS_LOCK:
            job = BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["phase"] = "finalizing"
            job["finalize_progress"] = max(0.0, min(1.0, float(progress)))
            job["finalize_step"] = step
            job["updated_at"] = time.time()

    with BEST_TEAM_JOBS_LOCK:
        job = BEST_TEAM_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["phase"] = "processing"
        job["updated_at"] = time.time()

    try:
        result = _compute_best_teams_from_results(
            results,
            payload,
            progress_callback=_update_progress,
            finalize_callback=_update_finalize,
        )
        with BEST_TEAM_JOBS_LOCK:
            job = BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["phase"] = "completed"
            job["result"] = result
            job["processed_combinations"] = int(result.get("processed_combinations", 0))
            job["total_combinations"] = int(result.get("total_combinations", 0))
            total = max(0, job["total_combinations"])
            done = max(0, job["processed_combinations"])
            job["progress"] = 0.0 if total <= 0 else min(1.0, float(done) / float(total))
            job["finalize_progress"] = 1.0
            job["finalize_step"] = "Done"
            job["updated_at"] = time.time()
    except Exception as exc:
        with BEST_TEAM_JOBS_LOCK:
            job = BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["phase"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = time.time()


@router.post("/")
def find_best_team(payload: dict):
    required = ["team_ids", "vrs_ranks", "bo3_mode", "n_sims"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
    team_ids = payload["team_ids"]
    if len(team_ids) < 2 or len(team_ids) % 2 != 0:
        raise HTTPException(status_code=400, detail="team_ids must contain an even number of teams (Swiss requires pairs).")
    results = simulate_swiss_fantasy(
        team_ids=team_ids,
        vrs_ranks=payload["vrs_ranks"],
        bo3_mode=payload["bo3_mode"],
        n_sims=int(payload["n_sims"]),
    )
    return _compute_best_teams_from_results(results, payload)


@router.post("/from-latest")
def find_best_team_from_latest(payload: dict | None = None):
    latest = load_latest_simulation()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored Swiss simulation found. Run Group Stage first.")
    return _compute_best_teams_from_results(latest["results"], payload or {})


@router.post("/from-latest/start")
def start_best_team_from_latest(payload: dict | None = None):
    latest = load_latest_simulation()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored Swiss simulation found. Run Group Stage first.")

    job_id = uuid.uuid4().hex
    with BEST_TEAM_JOBS_LOCK:
        BEST_TEAM_JOBS[job_id] = {
            "status": "queued",
            "phase": "queued",
            "error": "",
            "progress": 0.0,
            "processed_combinations": 0,
            "total_combinations": 0,
            "finalize_progress": 0.0,
            "finalize_step": "",
            "result": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    worker = threading.Thread(target=_run_best_team_job, args=(job_id, latest["results"], payload or {}), daemon=True)
    worker.start()
    return {"job_id": job_id}


@router.get("/job/{job_id}")
def get_best_team_job(job_id: str):
    with BEST_TEAM_JOBS_LOCK:
        job = BEST_TEAM_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_id not found")
        out = dict(job)
    return {
        "job_id": job_id,
        "status": out.get("status", "queued"),
        "phase": out.get("phase", "queued"),
        "error": out.get("error", ""),
        "progress": out.get("progress", 0.0),
        "processed_combinations": out.get("processed_combinations", 0),
        "total_combinations": out.get("total_combinations", 0),
        "finalize_progress": out.get("finalize_progress", 0.0),
        "finalize_step": out.get("finalize_step", ""),
        "result": out.get("result"),
    }


@router.post("/query")
def query_cached_best_teams(payload: dict):
    cache_id = (payload.get("cache_id") or "").strip()
    if not cache_id:
        raise HTTPException(status_code=400, detail="cache_id is required")

    include_ids = payload.get("include_player_ids") or []
    exclude_ids = payload.get("exclude_player_ids") or []
    search_q = payload.get("search") or ""
    sort_key = payload.get("sort_key") or "ev_desc"
    page = max(0, int(payload.get("page", 0)))
    page_size = min(max(1, int(payload.get("page_size", 200))), 500)

    return _query_cached_best_teams(cache_id, include_ids, exclude_ids, search_q, sort_key, page, page_size)


@router.delete("/cache/{cache_id}")
def delete_cached_best_teams(cache_id: str):
    conn = _connect()
    try:
        conn.execute("DELETE FROM best_team_combos WHERE cache_id = ?", (cache_id,))
        conn.execute("DELETE FROM best_team_runs WHERE cache_id = ?", (cache_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


@router.get("/latest")
def get_latest_cached_best_teams():
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT cache_id, total_teams, created_at FROM best_team_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"exists": False}
        return {
            "exists": True,
            "cache_id": row["cache_id"],
            "total_teams": int(row["total_teams"]),
            "created_at": float(row["created_at"]),
        }
    finally:
        conn.close()
