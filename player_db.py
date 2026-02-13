# player_db.py
import sqlite3
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = str(Path(__file__).resolve().with_name("fantasy_players.db"))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    """
    Create/update the players table with JSON + rating tier columns.
    """
    conn = connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                player_id      INTEGER PRIMARY KEY,
                name           TEXT NOT NULL,
                rating         REAL DEFAULT 0.0,
                price          INTEGER DEFAULT 0,

                best_role      TEXT DEFAULT '',
                major_win_pct  REAL DEFAULT 0.0,
                minor_win_pct  REAL DEFAULT 0.0,

                -- JSON blobs for boosters and roles
                boosters_json  TEXT,
                roles_json     TEXT,

                -- Rating vs different opponent tiers (nullable)
                rating_top5    REAL,
                maps_top5      REAL,
                rating_top10   REAL,
                maps_top10     REAL,
                rating_top20   REAL,
                maps_top20     REAL,
                rating_top30   REAL,
                maps_top30     REAL,
                rating_top50   REAL,
                maps_top50     REAL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_price ON players(price);")
        conn.commit()
    finally:
        conn.close()


def _to_json_text(obj: Any) -> Optional[str]:
    """
    Normalise Python object to JSON string or None.
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        # assume already JSON
        return obj
    return json.dumps(obj)


def add_or_update_player(
    *,
    player_id: int,
    name: Optional[str] = None,
    rating: Optional[float] = None,
    price: Optional[int] = None,
    best_role: Optional[str] = None,
    major_win_pct: Optional[float] = None,
    minor_win_pct: Optional[float] = None,
    boosters_json: Any = None,
    roles_json: Any = None,
    rating_top5: Optional[float] = None,
    maps_top5: Optional[float] = None,
    rating_top10: Optional[float] = None,
    maps_top10: Optional[float] = None,
    rating_top20: Optional[float] = None,
    maps_top20: Optional[float] = None,
    rating_top30: Optional[float] = None,
    maps_top30: Optional[float] = None,
    rating_top50: Optional[float] = None,
    maps_top50: Optional[float] = None,
) -> None:
    """
    Upsert a player.

    Any argument left as None will NOT overwrite the existing DB value.
    boosters_json and roles_json can be dict/list or JSON string or None.

    This function uses COALESCE(excluded.col, col) so that passing None
    means "leave as-is", and passing a real value overwrites.
    """
    boosters_text = _to_json_text(boosters_json)
    roles_text = _to_json_text(roles_json)

    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO players (
                player_id,
                name,
                rating,
                price,
                best_role,
                major_win_pct,
                minor_win_pct,
                boosters_json,
                roles_json,
                rating_top5,
                maps_top5,
                rating_top10,
                maps_top10,
                rating_top20,
                maps_top20,
                rating_top30,
                maps_top30,
                rating_top50,
                maps_top50
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            ON CONFLICT(player_id) DO UPDATE SET
                name          = COALESCE(excluded.name, name),
                rating        = COALESCE(excluded.rating, rating),
                price         = COALESCE(excluded.price, price),
                best_role     = COALESCE(excluded.best_role, best_role),
                major_win_pct = COALESCE(excluded.major_win_pct, major_win_pct),
                minor_win_pct = COALESCE(excluded.minor_win_pct, minor_win_pct),
                boosters_json = COALESCE(excluded.boosters_json, boosters_json),
                roles_json    = COALESCE(excluded.roles_json, roles_json),
                rating_top5   = COALESCE(excluded.rating_top5, rating_top5),
                maps_top5     = COALESCE(excluded.maps_top5, maps_top5),
                rating_top10  = COALESCE(excluded.rating_top10, rating_top10),
                maps_top10    = COALESCE(excluded.maps_top10, maps_top10),
                rating_top20  = COALESCE(excluded.rating_top20, rating_top20),
                maps_top20    = COALESCE(excluded.maps_top20, maps_top20),
                rating_top30  = COALESCE(excluded.rating_top30, rating_top30),
                maps_top30    = COALESCE(excluded.maps_top30, maps_top30),
                rating_top50  = COALESCE(excluded.rating_top50, rating_top50),
                maps_top50    = COALESCE(excluded.maps_top50, maps_top50)
            ;
            """,
            (
                player_id,
                name,
                rating,
                price,
                best_role,
                major_win_pct,
                minor_win_pct,
                boosters_text,
                roles_text,
                rating_top5,
                maps_top5,
                rating_top10,
                maps_top10,
                rating_top20,
                maps_top20,
                rating_top30,
                maps_top30,
                rating_top50,
                maps_top50,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_player(player_id: int) -> Optional[Dict[str, Any]]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM players WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_player_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Case-insensitive lookup by player name.
    """
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM players WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_players() -> List[Dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM players ORDER BY rating DESC, name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_player_with_parsed_json(player_id: int) -> Optional[Dict[str, Any]]:
    """
    Return a player row plus parsed boosters/roles JSON as 'boosters' and 'roles'.
    """
    row = get_player(player_id)
    if not row:
        return None

    boosters = None
    roles = None

    if row.get("boosters_json"):
        try:
            boosters = json.loads(row["boosters_json"])
        except Exception:
            boosters = None

    if row.get("roles_json"):
        try:
            roles = json.loads(row["roles_json"])
        except Exception:
            roles = None

    row["boosters"] = boosters
    row["roles"] = roles
    return row


def delete_player(player_id: int) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM players WHERE player_id = ?", (player_id,))
        conn.commit()
    finally:
        conn.close()
