import sqlite3
import json

from pathlib import Path
DB_PATH = str(Path(__file__).resolve().parents[2] / "fantasy_players.db")

BOOSTER_SOURCE_IDS = [
    2, 3, 5, 8, 9, 13, 16, 18, 19, 20,
    21, 22, 23, 26, 27, 28, 29, 30,
]

ROLE_SOURCE_IDS = [
    0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13,
]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute("SELECT * FROM players").fetchall()
    print(f"Found {len(rows)} players.")

    updated = 0

    for r in rows:
        pid = r["player_id"]
        boosters_text = r["boosters_json"]
        roles_text = r["roles_json"]

        # Only touch rows that currently have JSON
        if not boosters_text and not roles_text:
            continue

        try:
            old_boosters = json.loads(boosters_text) if boosters_text else {}
        except Exception:
            old_boosters = {}

        try:
            old_roles = json.loads(roles_text) if roles_text else {}
        except Exception:
            old_roles = {}

        # --- Remap boosters: your 0–17 from HLTV IDs ---
        new_boosters = {}
        for idx, src_id in enumerate(BOOSTER_SOURCE_IDS):
            obj_val = old_boosters.get(str(src_id), 0.0)
            try:
                val = float(obj_val)
            except Exception:
                val = 0.0
            new_boosters[str(idx)] = val

        # --- Remap roles: your 0–11 from HLTV role IDs (skip 3 and 4) ---
        new_roles = {}
        for idx, src_id in enumerate(ROLE_SOURCE_IDS):
            role_data = old_roles.get(str(src_id), {})
            if not isinstance(role_data, dict):
                role_data = {}
            major = float(role_data.get("major", 0.0))
            minor = float(role_data.get("minor", 0.0))
            new_roles[str(idx)] = {"major": major, "minor": minor}

        cur.execute(
            """
            UPDATE players
            SET boosters_json = ?, roles_json = ?
            WHERE player_id = ?
            """,
            (json.dumps(new_boosters), json.dumps(new_roles), pid),
        )
        updated += 1

    conn.commit()
    conn.close()
    print(f"Remapped boosters/roles for {updated} players.")


if __name__ == "__main__":
    main()
