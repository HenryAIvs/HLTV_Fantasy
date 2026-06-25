import sqlite3
import json
from typing import Dict, List

from swiss_stage.swiss_models import PlayerState

DB_PATH = "fantasy_players.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_json(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _compute_best_role_major_minor(roles_obj) -> tuple[int | None, float, float]:
    """
    roles_obj expected to be:
      { "role_id": {"major": x, "minor": y}, ... }

    We choose the role that maximises:
        role_points = 5*major + 2*minor - 2*(1 - major - minor)

    and return that role's (major, minor). If no roles found, return (0.0, 0.0).
    """
    if not isinstance(roles_obj, dict):
        return None, 0.0, 0.0

    best_score = -1e9
    best_role_id = None
    best_major = 0.0
    best_minor = 0.0

    for role_id_raw, vals in roles_obj.items():
        if not isinstance(vals, dict):
            continue
        try:
            role_id = int(role_id_raw)
        except Exception:
            role_id = None
        major = float(vals.get("major", 0.0))
        minor = float(vals.get("minor", 0.0))

        score = 5.0 * major + 2.0 * minor - 2.0 * (1.0 - major - minor)
        if score > best_score:
            best_score = score
            best_role_id = role_id
            best_major = major
            best_minor = minor

    if best_score < -1e8:
        return None, 0.0, 0.0
    return best_role_id, best_major, best_minor


def _compute_boosters_list(boosters_obj, top_k: int = 5) -> tuple[List[float], List[int]]:
    """
    boosters_obj expected to be dict:
      { "booster_id": value, ... }

    We take the top_k booster values sorted descending.
    """
    if not isinstance(boosters_obj, dict):
        return [0.0] * top_k, []

    items = []
    for booster_id_raw, v in boosters_obj.items():
        try:
            booster_id = int(booster_id_raw)
            fv = float(v)
        except Exception:
            booster_id = -1
            fv = 0.0
        items.append((booster_id, fv))

    items.sort(key=lambda item: item[1], reverse=True)
    trimmed = items[:top_k]
    while len(trimmed) < top_k:
        trimmed.append((-1, 0.0))
    return [value for _, value in trimmed], [booster_id for booster_id, _ in trimmed]


def _compute_booster_rates(boosters_obj) -> Dict[int, float]:
    if not isinstance(boosters_obj, dict):
        return {}
    out: Dict[int, float] = {}
    for booster_id_raw, value_raw in boosters_obj.items():
        try:
            booster_id = int(booster_id_raw)
            value = float(value_raw)
        except Exception:
            continue
        out[booster_id] = value
    return out


def load_team_players(team_id: int) -> Dict[int, PlayerState]:
    """
    Load players for a given team_id from the DB and build PlayerState objects
    using the rating, boosters_json, and roles_json fields in the players table.

    - rating from players.rating
    - major_pct/minor_pct from the role that maximises role_points
    - boosters = top 5 booster trigger rates
    """
    conn = _connect()
    try:
        team_row = conn.execute(
            """
            SELECT player1_id, player2_id, player3_id, player4_id, player5_id
            FROM teams
            WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()

        if not team_row:
            raise ValueError(f"Team {team_id} not found in teams table.")

        player_ids = [
            team_row["player1_id"],
            team_row["player2_id"],
            team_row["player3_id"],
            team_row["player4_id"],
            team_row["player5_id"],
        ]

        players: Dict[int, PlayerState] = {}

        for pid in player_ids:
            if pid is None or pid == 0:
                continue

            prow = conn.execute(
                "SELECT * FROM players WHERE player_id = ?",
                (pid,),
            ).fetchone()

            if not prow:
                continue

            d = dict(prow)
            rating = float(d.get("rating", 0.0))

            boosters_obj = _parse_json(d.get("boosters_json"))
            roles_obj = _parse_json(d.get("roles_json"))

            role_id, major_pct, minor_pct = _compute_best_role_major_minor(roles_obj)
            boosters_list, booster_ids = _compute_boosters_list(boosters_obj, top_k=5)
            booster_rates = _compute_booster_rates(boosters_obj)

            players[pid] = PlayerState(
                player_id=pid,
                rating=rating,
                major_pct=major_pct,
                minor_pct=minor_pct,
                boosters=boosters_list,
                role_id=role_id,
                booster_ids=booster_ids,
                booster_rates=booster_rates,
            )

        return players

    finally:
        conn.close()
