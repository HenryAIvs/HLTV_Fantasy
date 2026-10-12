"""Team renames.

An organisation change (3DMAX became Envy in October 2026) keeps the roster,
so its results, rankings history and map history belong to the new name. An
alias maps the old normalised name onto the new one; every name normaliser
(`_norm_team_name` in events.py and event_db.py) ends by resolving aliases, so
stored results under the old name train and score as the new team, ranking
pages that still list the old name update the new team's row, and the win
model's per-team state is keyed once. Adding an alias also re-keys the three
history tables that store the normalised name.
"""
from __future__ import annotations

import re
import threading
import zlib
from typing import Any, Dict, List, Optional

from backend.data.db import connect

_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Dict[str, Any]]] = None  # old_key -> alias row
_TABLE_READY = False
_MAX_HOPS = 5


def normalize(name: str) -> str:
    """The raw normalised form (no alias resolution)."""
    return re.sub(r"[^a-z0-9]+", "", str(name or "").strip().lower())


def key(name: str) -> str:
    """The canonical key of a team name: normalised, aliases resolved."""
    return canonical(normalize(name))


def ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    conn = connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS team_aliases (
                old_key          TEXT PRIMARY KEY,
                new_key          TEXT NOT NULL,
                old_name         TEXT,
                new_name         TEXT,
                old_team_id      INTEGER,
                new_team_id      INTEGER,
                old_hltv_team_id INTEGER,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    _TABLE_READY = True


def _load() -> Dict[str, Dict[str, Any]]:
    global _CACHE
    cached = _CACHE
    if cached is not None:
        return cached
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        ensure_table()
        conn = connect()
        try:
            rows = conn.execute("SELECT * FROM team_aliases").fetchall()
        finally:
            conn.close()
        _CACHE = {str(r["old_key"]): dict(r) for r in rows}
        return _CACHE


def invalidate() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


def canonical(norm_key: str) -> str:
    """Follow renames from a normalised key to the current team's key."""
    aliases = _load()
    if not aliases:
        return norm_key
    seen = 0
    current = norm_key
    while current in aliases and seen < _MAX_HOPS:
        current = str(aliases[current]["new_key"])
        seen += 1
    return current


def is_alias_source(norm_key: str) -> bool:
    return norm_key in _load()


def list_aliases() -> List[Dict[str, Any]]:
    return sorted(_load().values(), key=lambda r: str(r.get("created_at") or ""))


def former(norm_key: str) -> List[Dict[str, Any]]:
    """Aliases that resolve to this team (its earlier names), newest first."""
    target = canonical(norm_key)
    rows = [r for r in _load().values() if canonical(str(r["new_key"])) == target]
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)


def signature() -> int:
    """An integer that changes whenever the alias set changes (model staleness)."""
    rows = list_aliases()
    if not rows:
        return 0
    text = "|".join(f"{r['old_key']}>{r['new_key']}@{r.get('created_at')}" for r in rows)
    return int(zlib.crc32(text.encode("utf-8"))) + 1


def add_alias(old_team_id: int, new_team_id: int) -> Dict[str, Any]:
    """Record that the team row `old_team_id` is the earlier name of
    `new_team_id`, and move the old name's stored history under the new key."""
    from backend.data.team_db import get_team_by_id

    old = get_team_by_id(int(old_team_id))
    new = get_team_by_id(int(new_team_id))
    if not old or not new:
        raise ValueError("Both teams must exist")
    old_key = normalize(old.get("name") or "")
    new_key = normalize(new.get("name") or "")
    if not old_key or not new_key or old_key == new_key:
        raise ValueError("The two teams must have different names")
    if canonical(new_key) == old_key:
        raise ValueError("That would make the rename circular")
    ensure_table()
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO team_aliases (old_key, new_key, old_name, new_name, old_team_id, new_team_id, old_hltv_team_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(old_key) DO UPDATE SET
                new_key = excluded.new_key, old_name = excluded.old_name, new_name = excluded.new_name,
                old_team_id = excluded.old_team_id, new_team_id = excluded.new_team_id,
                old_hltv_team_id = excluded.old_hltv_team_id, created_at = datetime('now')
            """,
            (old_key, new_key, old.get("name"), new.get("name"), int(old_team_id), int(new_team_id), old.get("hltv_team_id")),
        )
        moved = _rekey_history(conn, old_key, new_key)
        conn.commit()
    finally:
        conn.close()
    invalidate()
    row = _load().get(old_key) or {}
    return {**row, "moved": moved}


def remove_alias(old_key: str) -> bool:
    """Drop an alias. Re-keyed history stays under the new name."""
    ensure_table()
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM team_aliases WHERE old_key = ?", (str(old_key),))
        conn.commit()
        removed = cur.rowcount > 0
    finally:
        conn.close()
    invalidate()
    return removed


def _rekey_history(conn, old_key: str, new_key: str) -> Dict[str, int]:
    """Move ranking snapshots and archived map-stat windows stored under the
    old key to the new one. Where both names have a row for the same date or
    window the new name's row wins, except that an archived map-stat window
    keeps whichever row holds more maps (the new team id's page may have had
    none yet)."""
    moved: Dict[str, int] = {}
    for table in ("hltv_rankings_history", "vrs_rankings_history"):
        cur = conn.execute(
            f"UPDATE OR IGNORE {table} SET normalized_name = ? WHERE normalized_name = ?", (new_key, old_key)
        )
        moved[table] = cur.rowcount
        conn.execute(f"DELETE FROM {table} WHERE normalized_name = ?", (old_key,))
    rows = conn.execute(
        "SELECT start_date, end_date, LENGTH(maps_json) AS n FROM historical_team_map_stats WHERE normalized_name = ?",
        (old_key,),
    ).fetchall()
    count = 0
    for r in rows:
        existing = conn.execute(
            "SELECT LENGTH(maps_json) AS n FROM historical_team_map_stats WHERE normalized_name = ? AND start_date = ? AND end_date = ?",
            (new_key, r["start_date"], r["end_date"]),
        ).fetchone()
        if existing is not None and int(existing["n"] or 0) >= int(r["n"] or 0):
            conn.execute(
                "DELETE FROM historical_team_map_stats WHERE normalized_name = ? AND start_date = ? AND end_date = ?",
                (old_key, r["start_date"], r["end_date"]),
            )
            continue
        if existing is not None:
            conn.execute(
                "DELETE FROM historical_team_map_stats WHERE normalized_name = ? AND start_date = ? AND end_date = ?",
                (new_key, r["start_date"], r["end_date"]),
            )
        conn.execute(
            "UPDATE historical_team_map_stats SET normalized_name = ? WHERE normalized_name = ? AND start_date = ? AND end_date = ?",
            (new_key, old_key, r["start_date"], r["end_date"]),
        )
        count += 1
    moved["historical_team_map_stats"] = count
    return moved
