import sqlite3

DB_PATH = "fantasy_players.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_table(table_name: str):
    conn = get_conn()
    rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
    conn.close()
    return rows
