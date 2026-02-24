import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = str(Path(__file__).resolve().with_name("fantasy_players.db"))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_event_schema() -> None:
    conn = connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id    INTEGER PRIMARY KEY,
                imported_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_teams (
                event_id    INTEGER NOT NULL,
                team_name   TEXT NOT NULL,
                player1_id  INTEGER,
                player2_id  INTEGER,
                player3_id  INTEGER,
                player4_id  INTEGER,
                player5_id  INTEGER,
                PRIMARY KEY (event_id, team_name),
                FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_player_prices (
                event_id   INTEGER NOT NULL,
                player_id  INTEGER NOT NULL,
                team_name  TEXT,
                price      INTEGER NOT NULL,
                PRIMARY KEY (event_id, player_id),
                FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                singleton_id    INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                active_event_id INTEGER
            );
            """
        )
        conn.execute("INSERT OR IGNORE INTO app_state (singleton_id, active_event_id) VALUES (1, NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_player_prices_event ON event_player_prices(event_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_player_prices_player ON event_player_prices(player_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_teams_event ON event_teams(event_id)")
        conn.commit()
    finally:
        conn.close()


def _normalize_team_snapshot(team: Dict[str, Any]) -> Dict[str, Any]:
    team_name = str(team.get("team_name", "")).strip()
    player_ids_raw = team.get("player_ids") or []
    player_ids = [int(x) for x in player_ids_raw if str(x).strip()]
    player_ids = player_ids[:5]
    while len(player_ids) < 5:
        player_ids.append(0)

    raw_prices = team.get("prices_by_player") or {}
    prices_by_player = {}
    for pid, price in raw_prices.items():
        try:
            prices_by_player[int(pid)] = int(price)
        except Exception:
            continue

    return {
        "team_name": team_name,
        "player_ids": player_ids,
        "prices_by_player": prices_by_player,
    }


def upsert_event_snapshot(event_id: int, teams: List[Dict[str, Any]]) -> None:
    eid = int(event_id)
    normalized_teams = [_normalize_team_snapshot(t) for t in (teams or [])]

    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO events (event_id, imported_at)
            VALUES (?, datetime('now'))
            ON CONFLICT(event_id) DO UPDATE SET imported_at = excluded.imported_at
            """,
            (eid,),
        )
        conn.execute("DELETE FROM event_teams WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM event_player_prices WHERE event_id = ?", (eid,))

        for team in normalized_teams:
            if not team["team_name"]:
                continue
            pids = team["player_ids"]
            conn.execute(
                """
                INSERT INTO event_teams (
                    event_id, team_name, player1_id, player2_id, player3_id, player4_id, player5_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (eid, team["team_name"], pids[0], pids[1], pids[2], pids[3], pids[4]),
            )
            for pid, price in team["prices_by_player"].items():
                conn.execute(
                    """
                    INSERT INTO event_player_prices (event_id, player_id, team_name, price)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(event_id, player_id) DO UPDATE SET
                        team_name = excluded.team_name,
                        price = excluded.price
                    """,
                    (eid, int(pid), team["team_name"], int(price)),
                )
        conn.commit()
    finally:
        conn.close()


def set_active_event(event_id: Optional[int]) -> None:
    conn = connect()
    try:
        if event_id is None:
            conn.execute("UPDATE app_state SET active_event_id = NULL WHERE singleton_id = 1")
        else:
            conn.execute(
                "UPDATE app_state SET active_event_id = ? WHERE singleton_id = 1",
                (int(event_id),),
            )
        conn.commit()
    finally:
        conn.close()


def get_active_event_id() -> Optional[int]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT active_event_id FROM app_state WHERE singleton_id = 1"
        ).fetchone()
        if not row:
            return None
        val = row["active_event_id"]
        return int(val) if val is not None else None
    finally:
        conn.close()


def list_events() -> List[Dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                e.event_id,
                e.imported_at,
                (SELECT COUNT(*) FROM event_teams et WHERE et.event_id = e.event_id) AS team_count,
                (SELECT COUNT(*) FROM event_player_prices ep WHERE ep.event_id = e.event_id) AS player_count,
                (SELECT MIN(price) FROM event_player_prices ep WHERE ep.event_id = e.event_id) AS min_price,
                (SELECT MAX(price) FROM event_player_prices ep WHERE ep.event_id = e.event_id) AS max_price
            FROM events e
            ORDER BY e.imported_at DESC, e.event_id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_event_price(player_id: int, event_id: Optional[int] = None) -> Optional[int]:
    eid = get_active_event_id() if event_id is None else int(event_id)
    if eid is None:
        return None
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT price
            FROM event_player_prices
            WHERE event_id = ? AND player_id = ?
            """,
            (eid, int(player_id)),
        ).fetchone()
        if not row:
            return None
        return int(row["price"])
    finally:
        conn.close()


def get_event_price_map(event_id: Optional[int] = None) -> Dict[int, int]:
    eid = get_active_event_id() if event_id is None else int(event_id)
    if eid is None:
        return {}
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT player_id, price
            FROM event_player_prices
            WHERE event_id = ?
            """,
            (eid,),
        ).fetchall()
        return {int(r["player_id"]): int(r["price"]) for r in rows}
    finally:
        conn.close()


def get_event_detail(event_id: int) -> Optional[Dict[str, Any]]:
    eid = int(event_id)
    conn = connect()
    try:
        event_row = conn.execute(
            "SELECT event_id, imported_at FROM events WHERE event_id = ?",
            (eid,),
        ).fetchone()
        if not event_row:
            return None

        team_rows = conn.execute(
            """
            SELECT team_name, player1_id, player2_id, player3_id, player4_id, player5_id
            FROM event_teams
            WHERE event_id = ?
            ORDER BY lower(team_name)
            """,
            (eid,),
        ).fetchall()

        player_rows = conn.execute(
            """
            SELECT ep.player_id, p.name AS player_name, ep.team_name, ep.price
            FROM event_player_prices ep
            LEFT JOIN players p ON p.player_id = ep.player_id
            WHERE ep.event_id = ?
            ORDER BY lower(ep.team_name), lower(COALESCE(p.name, '')), ep.player_id
            """,
            (eid,),
        ).fetchall()

        out_teams = []
        for tr in team_rows:
            out_teams.append(
                {
                    "team_name": tr["team_name"],
                    "player_ids": [tr["player1_id"], tr["player2_id"], tr["player3_id"], tr["player4_id"], tr["player5_id"]],
                }
            )

        out_players = [dict(r) for r in player_rows]
        return {
            "event_id": int(event_row["event_id"]),
            "imported_at": event_row["imported_at"],
            "teams": out_teams,
            "players": out_players,
        }
    finally:
        conn.close()

