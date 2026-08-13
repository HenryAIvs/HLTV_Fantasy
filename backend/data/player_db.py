# player_db.py
import json
import time
from typing import Any, Dict, List, Optional

from backend.data.db import DB_PATH, connect  # noqa: F401  (DB_PATH re-exported for routes)

# Top-X timeframe windows (months of HLTV history). The players table's shared
# rating_topN/maps_topN columns are a materialized cache of the ACTIVE window;
# player_topx_windows archives every imported window so they can coexist and be
# switched without re-scraping.
_TOPX_TIERS = (5, 10, 20, 30, 50)
_TOPX_TIER_COLS = tuple(
    col for t in _TOPX_TIERS for col in (f"rating_top{t}", f"maps_top{t}")
)


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
                maps_top50     REAL,
                last_topx_import_at REAL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_price ON players(price);")
        cols = conn.execute("PRAGMA table_info(players)").fetchall()
        col_names = {row["name"] for row in cols}
        if "last_topx_import_at" not in col_names:
            conn.execute("ALTER TABLE players ADD COLUMN last_topx_import_at REAL")
        conn.commit()
    finally:
        conn.close()


def ensure_topx_windows_schema() -> None:
    """Archive table for per-window Top-X data + the active-window setting.
    On first run, seeds the 3-month window from the players' current shared
    columns so pre-existing data isn't lost when windows are introduced."""
    conn = connect()
    try:
        cols_sql = ",\n                ".join(f"{c} REAL" for c in _TOPX_TIER_COLS)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS player_topx_windows (
                player_id INTEGER NOT NULL,
                months    INTEGER NOT NULL,
                overall_rating REAL,
                {cols_sql},
                imported_at REAL,
                PRIMARY KEY (player_id, months)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topx_settings (
                singleton_id  INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                active_window INTEGER NOT NULL DEFAULT 3
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO topx_settings (singleton_id, active_window) VALUES (1, 3)")

        # One-time seed: fold existing shared-column data into the 3-month window.
        seeded = conn.execute("SELECT 1 FROM player_topx_windows LIMIT 1").fetchone()
        if not seeded:
            maps_cols = [f"maps_top{t}" for t in _TOPX_TIERS]
            where_any = " OR ".join(f"COALESCE({m},0) > 0" for m in maps_cols)
            insert_cols = ["player_id", "months", "overall_rating", *_TOPX_TIER_COLS, "imported_at"]
            placeholders = ",".join("?" for _ in insert_cols)
            for p in conn.execute(f"SELECT * FROM players WHERE {where_any}").fetchall():
                values = [int(p["player_id"]), 3, p["rating"]]
                values += [p[c] for c in _TOPX_TIER_COLS]
                values.append(p["last_topx_import_at"])
                conn.execute(
                    f"INSERT OR IGNORE INTO player_topx_windows ({','.join(insert_cols)}) VALUES ({placeholders})",
                    values,
                )
        conn.commit()
    finally:
        conn.close()


def get_active_topx_window() -> int:
    conn = connect()
    try:
        ensure_topx_windows_schema_lazy(conn)
        row = conn.execute("SELECT active_window FROM topx_settings WHERE singleton_id = 1").fetchone()
        return int(row["active_window"]) if row and row["active_window"] else 3
    finally:
        conn.close()


def ensure_topx_windows_schema_lazy(conn) -> None:
    # Cheap guard so window helpers work even if ensure_topx_windows_schema
    # hasn't been called yet this process (tables use IF NOT EXISTS elsewhere).
    try:
        conn.execute("SELECT 1 FROM topx_settings LIMIT 1")
    except Exception:
        conn.close()
        ensure_topx_windows_schema()


def set_active_topx_window(months: int) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE topx_settings SET active_window = ? WHERE singleton_id = 1",
            (int(months),),
        )
        conn.commit()
    finally:
        conn.close()


def save_player_topx_window(
    player_id: int,
    months: int,
    overall_rating: Optional[float],
    tier_values: Dict[str, Any],
    imported_at: Optional[float] = None,
) -> None:
    """Archive one player's tier data for a given window. tier_values holds
    rating_topN/maps_topN keys (as produced by the import updates dict)."""
    cols = ["player_id", "months", "overall_rating", *_TOPX_TIER_COLS, "imported_at"]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c} = excluded.{c}" for c in cols if c not in ("player_id", "months"))
    values = [int(player_id), int(months), overall_rating]
    values += [tier_values.get(c) for c in _TOPX_TIER_COLS]
    values.append(imported_at if imported_at is not None else time.time())
    conn = connect()
    try:
        conn.execute(
            f"""
            INSERT INTO player_topx_windows ({','.join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(player_id, months) DO UPDATE SET {updates}
            """,
            values,
        )
        conn.commit()
    finally:
        conn.close()


def get_topx_window_coverage() -> Dict[int, int]:
    """{months: player_count_with_data} across all archived windows."""
    conn = connect()
    try:
        ensure_topx_windows_schema_lazy(conn)
        where_any = " OR ".join(f"COALESCE(maps_top{t},0) > 0" for t in _TOPX_TIERS)
        rows = conn.execute(
            f"SELECT months, COUNT(*) AS c FROM player_topx_windows WHERE {where_any} GROUP BY months"
        ).fetchall()
        return {int(r["months"]): int(r["c"]) for r in rows}
    finally:
        conn.close()


def apply_topx_window_to_players(months: int) -> int:
    """Rebuild the players' shared tier columns from the given window's archive.
    Players with no archived data for the window have their tier columns cleared
    (0/0) so the rating curve treats them as 'no data' for that window."""
    conn = connect()
    try:
        wrows = {
            int(r["player_id"]): r
            for r in conn.execute(
                "SELECT * FROM player_topx_windows WHERE months = ?", (int(months),)
            ).fetchall()
        }
        set_tiers = ",".join(f"{c} = ?" for c in _TOPX_TIER_COLS)
        applied = 0
        for p in conn.execute("SELECT player_id FROM players").fetchall():
            pid = int(p["player_id"])
            w = wrows.get(pid)
            if w:
                vals = [w[c] for c in _TOPX_TIER_COLS]
                conn.execute(
                    f"UPDATE players SET rating = COALESCE(?, rating), {set_tiers}, last_topx_import_at = ? WHERE player_id = ?",
                    [w["overall_rating"], *vals, w["imported_at"], pid],
                )
                applied += 1
            else:
                zeros = [0.0 for _ in _TOPX_TIER_COLS]
                conn.execute(
                    f"UPDATE players SET {set_tiers}, last_topx_import_at = NULL WHERE player_id = ?",
                    [*zeros, pid],
                )
        conn.commit()
        return applied
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
    last_topx_import_at: Optional[float] = None,
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
                last_topx_import_at,
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
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
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
                last_topx_import_at = COALESCE(excluded.last_topx_import_at, last_topx_import_at),
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
                last_topx_import_at,
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
        if not row:
            return None
        out = dict(row)
        try:
            from backend.data.event_db import get_active_event_id, get_event_price

            active_event_id = get_active_event_id()
            event_price = get_event_price(player_id, active_event_id)
            out["active_event_id"] = active_event_id
            if event_price is not None:
                out["price"] = int(event_price)
        except Exception:
            pass
        return out
    finally:
        conn.close()


def get_all_players() -> List[Dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM players ORDER BY rating DESC, name"
        ).fetchall()
        players = [dict(r) for r in rows]
        try:
            from backend.data.event_db import get_active_event_id, get_event_price_map

            active_event_id = get_active_event_id()
            price_map = get_event_price_map(active_event_id)
            for p in players:
                pid = int(p["player_id"])
                if pid in price_map:
                    p["price"] = int(price_map[pid])
                p["active_event_id"] = active_event_id
        except Exception:
            pass
        return players
    finally:
        conn.close()


def delete_player(player_id: int) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM players WHERE player_id = ?", (player_id,))
        conn.commit()
    finally:
        conn.close()
