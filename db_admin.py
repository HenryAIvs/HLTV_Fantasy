# db_admin.py
import sqlite3

DB_PATH = "fantasy_players.db"

def wipe_database():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Delete all rows but keep schema
    cur.execute("DELETE FROM players;")
    cur.execute("DELETE FROM teams;")

    conn.commit()
    conn.close()
