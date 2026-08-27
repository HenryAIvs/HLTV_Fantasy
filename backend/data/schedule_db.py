"""Persistence for the nightly data-ingestion scheduler: one config row plus a
run-history log. Kept in the main SQLite DB alongside the rest of the app state.
"""

import threading
import time
from typing import Any, Dict, List, Optional

from backend.data.db import connect as _connect

_schema_lock = threading.Lock()
_schema_ready = False

# Task keys the scheduler knows how to run, in the order it runs them (they all
# serialize on the shared HLTV browser anyway). Events first: a new fantasy
# event's teams get rankings/matches the same night.
TASK_KEYS = ("events", "rankings", "matches", "ratings")

_DEFAULT_CONFIG = {
    "enabled": 1,
    "run_time": "00:00",
    "do_events": 1,
    "do_rankings": 1,
    "do_matches": 1,
    "do_ratings": 1,
    "matches_lookback_days": 3,
}


def ensure_schedule_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_config (
                    singleton_id          INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    enabled               INTEGER NOT NULL DEFAULT 1,
                    run_time              TEXT    NOT NULL DEFAULT '00:00',
                    do_rankings           INTEGER NOT NULL DEFAULT 1,
                    do_matches            INTEGER NOT NULL DEFAULT 1,
                    do_ratings            INTEGER NOT NULL DEFAULT 1,
                    matches_lookback_days INTEGER NOT NULL DEFAULT 3,
                    updated_at            REAL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    task        TEXT NOT NULL,
                    trigger     TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    message     TEXT,
                    started_at  REAL,
                    finished_at REAL
                );
                """
            )
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(schedule_config)").fetchall()}
            if "do_events" not in cols:
                conn.execute("ALTER TABLE schedule_config ADD COLUMN do_events INTEGER NOT NULL DEFAULT 1")
            conn.execute(
                "INSERT OR IGNORE INTO schedule_config (singleton_id) VALUES (1)"
            )
            conn.commit()
        finally:
            conn.close()
        _schema_ready = True


def get_schedule_config() -> Dict[str, Any]:
    ensure_schedule_schema()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM schedule_config WHERE singleton_id = 1").fetchone()
    finally:
        conn.close()
    if not row:
        return dict(_DEFAULT_CONFIG)
    cfg = {k: row[k] for k in row.keys()}
    for flag in ("enabled", "do_events", "do_rankings", "do_matches", "do_ratings"):
        cfg[flag] = bool(cfg.get(flag))
    cfg["matches_lookback_days"] = int(cfg.get("matches_lookback_days") or 3)
    return cfg


def update_schedule_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    ensure_schedule_schema()
    allowed = {
        "enabled": lambda v: 1 if v else 0,
        "do_events": lambda v: 1 if v else 0,
        "do_rankings": lambda v: 1 if v else 0,
        "do_matches": lambda v: 1 if v else 0,
        "do_ratings": lambda v: 1 if v else 0,
        "run_time": lambda v: _normalize_time(str(v)),
        "matches_lookback_days": lambda v: max(1, min(30, int(v))),
    }
    sets = []
    vals: List[Any] = []
    for key, coerce in allowed.items():
        if key in patch and patch[key] is not None:
            try:
                sets.append(f"{key} = ?")
                vals.append(coerce(patch[key]))
            except (TypeError, ValueError):
                continue
    if sets:
        sets.append("updated_at = ?")
        vals.append(time.time())
        conn = _connect()
        try:
            conn.execute(
                f"UPDATE schedule_config SET {', '.join(sets)} WHERE singleton_id = 1",
                tuple(vals),
            )
            conn.commit()
        finally:
            conn.close()
    return get_schedule_config()


def _normalize_time(value: str) -> str:
    parts = str(value or "").strip().split(":")
    hh = max(0, min(23, int(parts[0]))) if parts and parts[0] != "" else 0
    mm = max(0, min(59, int(parts[1]))) if len(parts) > 1 and parts[1] != "" else 0
    return f"{hh:02d}:{mm:02d}"


def start_run(task: str, trigger: str) -> int:
    ensure_schedule_schema()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO schedule_runs (task, trigger, status, started_at) VALUES (?, ?, 'running', ?)",
            (str(task), str(trigger), time.time()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def finish_run(run_id: int, status: str, message: str = "") -> None:
    ensure_schedule_schema()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE schedule_runs SET status = ?, message = ?, finished_at = ? WHERE id = ?",
            (str(status), str(message)[:2000], time.time(), int(run_id)),
        )
        conn.commit()
    finally:
        conn.close()


def fail_orphan_runs() -> int:
    """Close out run rows stuck as 'running' — they belong to a previous
    process that died mid-task and would otherwise show as running forever."""
    ensure_schedule_schema()
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE schedule_runs SET status = 'error', message = 'Interrupted by backend restart', finished_at = ? "
            "WHERE status = 'running'",
            (time.time(),),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    ensure_schedule_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM schedule_runs ORDER BY id DESC LIMIT ?", (int(max(1, min(500, limit))),)
        ).fetchall()
    finally:
        conn.close()
    return [{k: r[k] for k in r.keys()} for r in rows]


def last_scheduled_run_ts() -> float:
    """Start time of the most recent *scheduled* (not manual) run, so the loop
    knows whether today's slot has already fired (survives process restarts)."""
    ensure_schedule_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT MAX(started_at) AS ts FROM schedule_runs WHERE trigger = 'scheduled'"
        ).fetchone()
    finally:
        conn.close()
    return float((row["ts"] if row else 0) or 0.0)


def last_success_by_task() -> Dict[str, float]:
    """Most recent successful finish time per task, for the UI 'last run' column."""
    ensure_schedule_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT task, MAX(finished_at) AS ts FROM schedule_runs WHERE status = 'success' GROUP BY task"
        ).fetchall()
    finally:
        conn.close()
    return {str(r["task"]): float(r["ts"] or 0.0) for r in rows}
