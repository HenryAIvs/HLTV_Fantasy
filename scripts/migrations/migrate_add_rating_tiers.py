# migrate_add_rating_tiers.py
import sqlite3

from pathlib import Path
DB_PATH = str(Path(__file__).resolve().parents[2] / "fantasy_players.db")
TABLE = "players"

NEW_COLUMNS = [
    ("rating_top5", "REAL"),
    ("maps_top5", "REAL"),
    ("rating_top10", "REAL"),
    ("maps_top10", "REAL"),
    ("rating_top20", "REAL"),
    ("maps_top20", "REAL"),
    ("rating_top30", "REAL"),
    ("maps_top30", "REAL"),
    ("rating_top50", "REAL"),
    ("maps_top50", "REAL"),
]

def column_exists(conn, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    for row in cur.fetchall():
        if row[1] == column:
            return True
    return False

def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        for col_name, col_type in NEW_COLUMNS:
            if not column_exists(conn, TABLE, col_name):
                print(f"Adding column {col_name} {col_type}")
                cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col_name} {col_type}")
        conn.commit()
        print("Migration complete.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
