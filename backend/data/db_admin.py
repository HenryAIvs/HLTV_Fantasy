# db_admin.py
from backend.data.db import connect


def wipe_database():
    # Ensure tables exist before delete to avoid OperationalError on first run.
    from backend.data.player_db import ensure_schema
    from backend.data.team_db import ensure_team_schema
    from backend.data.event_db import ensure_event_schema

    ensure_schema()
    ensure_team_schema()
    ensure_event_schema()

    conn = connect()
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
