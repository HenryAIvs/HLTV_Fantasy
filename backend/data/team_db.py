import sqlite3
from pathlib import Path
from typing import List, Dict, Optional

DB_PATH = str(Path(__file__).resolve().parents[2] / "fantasy_players.db")


def connect() -> sqlite3.Connection:
    """Returns a database connection with dict-like row access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_team_schema() -> None:
    """Creates the teams table if it does not already exist."""
    conn = connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
            team_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL UNIQUE,
            hltv_rank     INTEGER,
            hltv_points   INTEGER,
            vrs_rank      INTEGER,
            vrs_points    INTEGER,
            win_rate      REAL,

            player1_id    INTEGER,
            player2_id    INTEGER,
            player3_id    INTEGER,
            player4_id    INTEGER,
            player5_id    INTEGER,

            FOREIGN KEY(player1_id) REFERENCES players(player_id),
            FOREIGN KEY(player2_id) REFERENCES players(player_id),
            FOREIGN KEY(player3_id) REFERENCES players(player_id),
            FOREIGN KEY(player4_id) REFERENCES players(player_id),
            FOREIGN KEY(player5_id) REFERENCES players(player_id)
        );
        """
    )
    # Backward-compatible migration for existing databases.
    cols = conn.execute("PRAGMA table_info(teams)").fetchall()
    col_names = {row["name"] for row in cols}
    if "hltv_points" not in col_names:
        conn.execute("ALTER TABLE teams ADD COLUMN hltv_points INTEGER")
    if "vrs_points" not in col_names:
        conn.execute("ALTER TABLE teams ADD COLUMN vrs_points INTEGER")
    conn.commit()
    conn.close()


def add_or_update_team(
    *,
    name: str,
    hltv_rank: int,
    hltv_points: Optional[int] = None,
    vrs_rank: int,
    vrs_points: Optional[int] = None,
    win_rate: float,
    player_ids: List[int],  # exactly 5 HLTV player IDs
) -> None:
    """Insert a new team or update existing team based on name."""
    if len(player_ids) != 5:
        raise ValueError("player_ids must contain exactly 5 player IDs.")

    conn = connect()
    conn.execute(
        """
        INSERT INTO teams (
            name, hltv_rank, hltv_points, vrs_rank, win_rate,
            vrs_points,
            player1_id, player2_id, player3_id, player4_id, player5_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            hltv_rank = excluded.hltv_rank,
            hltv_points = excluded.hltv_points,
            vrs_rank = excluded.vrs_rank,
            vrs_points = excluded.vrs_points,
            win_rate = excluded.win_rate,
            player1_id = excluded.player1_id,
            player2_id = excluded.player2_id,
            player3_id = excluded.player3_id,
            player4_id = excluded.player4_id,
            player5_id = excluded.player5_id;
        """,
        (
            name,
            hltv_rank,
            hltv_points,
            vrs_rank,
            vrs_points,
            win_rate,
            player_ids[0],
            player_ids[1],
            player_ids[2],
            player_ids[3],
            player_ids[4],
        ),
    )
    conn.commit()
    conn.close()


def get_team_by_id(team_id: int) -> Optional[Dict]:
    conn = connect()
    row = conn.execute(
        "SELECT * FROM teams WHERE team_id = ?", (team_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_team_by_name(name: str) -> Optional[Dict]:
    conn = connect()
    row = conn.execute(
        "SELECT * FROM teams WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_teams() -> List[Dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM teams ORDER BY hltv_rank ASC NULLS LAST"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_team(team_id: int) -> None:
    conn = connect()
    conn.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))
    conn.commit()
    conn.close()
