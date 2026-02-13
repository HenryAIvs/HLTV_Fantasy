# db_admin.py
import sqlite3
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().with_name("fantasy_players.db"))

def wipe_database():
    # Ensure tables exist before delete to avoid OperationalError on first run.
    from player_db import ensure_schema
    from team_db import ensure_team_schema

    ensure_schema()
    ensure_team_schema()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Delete all rows but keep schema
    cur.execute("DELETE FROM players;")
    cur.execute("DELETE FROM teams;")

    conn.commit()
    conn.close()
