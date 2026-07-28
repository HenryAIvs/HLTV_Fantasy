import sqlite3

from pathlib import Path
DB_PATH = str(Path(__file__).resolve().parents[2] / "fantasy_players.db")


def column_exists(conn, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()

        # --- Ensure players has boosters_json & roles_json ---
        if not column_exists(conn, "players", "boosters_json"):
            print("Adding boosters_json to players...")
            cur.execute("ALTER TABLE players ADD COLUMN boosters_json TEXT")

        if not column_exists(conn, "players", "roles_json"):
            print("Adding roles_json to players...")
            cur.execute("ALTER TABLE players ADD COLUMN roles_json TEXT")

        # Optional: if you want these too (used in add_or_update_player)
        if not column_exists(conn, "players", "best_role"):
            print("Adding best_role to players...")
            cur.execute("ALTER TABLE players ADD COLUMN best_role TEXT DEFAULT ''")

        if not column_exists(conn, "players", "major_win_pct"):
            print("Adding major_win_pct to players...")
            cur.execute("ALTER TABLE players ADD COLUMN major_win_pct REAL DEFAULT 0.0")

        if not column_exists(conn, "players", "minor_win_pct"):
            print("Adding minor_win_pct to players...")
            cur.execute("ALTER TABLE players ADD COLUMN minor_win_pct REAL DEFAULT 0.0")

        # --- Ensure teams has vrs_rank (used by sim) ---
        if not column_exists(conn, "teams", "vrs_rank"):
            print("Adding vrs_rank to teams...")
            cur.execute("ALTER TABLE teams ADD COLUMN vrs_rank INTEGER DEFAULT 999")

        if not column_exists(conn, "teams", "hltv_rank"):
            print("Adding hltv_rank to teams...")
            cur.execute("ALTER TABLE teams ADD COLUMN hltv_rank INTEGER DEFAULT 999")

        if not column_exists(conn, "teams", "win_rate"):
            print("Adding win_rate to teams...")
            cur.execute("ALTER TABLE teams ADD COLUMN win_rate REAL DEFAULT 0.5")

        conn.commit()
        print("Migration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

