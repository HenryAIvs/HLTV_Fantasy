# db_admin.py
import sqlite3
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parents[2] / "fantasy_players.db")

def wipe_database():
    # Ensure tables exist before delete to avoid OperationalError on first run.
    from player_db import ensure_schema
    from team_db import ensure_team_schema
    from event_db import ensure_event_schema

    ensure_schema()
    ensure_team_schema()
    ensure_event_schema()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Delete all rows but keep schema
    cur.execute("DELETE FROM players;")
    cur.execute("DELETE FROM teams;")
    cur.execute("DELETE FROM event_player_prices;")
    cur.execute("DELETE FROM event_teams;")
    cur.execute("DELETE FROM events;")
    cur.execute("UPDATE app_state SET active_event_id = NULL WHERE singleton_id = 1;")

    conn.commit()
    conn.close()
