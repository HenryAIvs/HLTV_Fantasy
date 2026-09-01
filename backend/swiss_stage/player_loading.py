import json
import time
from typing import Dict, List

from backend.data.db import connect as _connect
from backend.swiss_stage.swiss_models import PlayerState




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


# Field-average trigger rate per booster, refreshed at most every few minutes
# (trigger backfills only land nightly, so a short TTL is plenty).
_BOOSTER_AVG_CACHE: dict = {"ts": 0.0, "avg": {}}
_BOOSTER_AVG_TTL_S = 300.0


def booster_rate_averages() -> Dict[int, float]:
    """Average trigger rate per booster across every player with booster data."""
    now = time.time()
    if now - _BOOSTER_AVG_CACHE["ts"] < _BOOSTER_AVG_TTL_S and _BOOSTER_AVG_CACHE["avg"]:
        return _BOOSTER_AVG_CACHE["avg"]
    sums: Dict[int, float] = {}
    counts: Dict[int, int] = {}
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT boosters_json FROM players WHERE boosters_json IS NOT NULL AND boosters_json != ''"
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        obj = _parse_json(r["boosters_json"])
        if not isinstance(obj, dict):
            continue
        for bid_raw, v in obj.items():
            try:
                bid = int(bid_raw)
                fv = float(v)
            except Exception:
                continue
            sums[bid] = sums.get(bid, 0.0) + fv
            counts[bid] = counts.get(bid, 0) + 1
    avg = {bid: sums[bid] / counts[bid] for bid in sums if counts.get(bid)}
    _BOOSTER_AVG_CACHE["ts"] = now
    _BOOSTER_AVG_CACHE["avg"] = avg
    return avg


def _compute_boosters_list(
    boosters_obj, top_k: int = 5, avg_rates: Dict[int, float] | None = None
) -> tuple[List[float], List[int], List[float]]:
    """
    boosters_obj expected to be dict:
      { "booster_id": value, ... }

    Slots go to the top_k boosters by EDGE over the field average for that
    booster (rate - average), best edge on the team's first match. A booster
    everyone triggers (Hellcase at ~100%) carries no edge, so it naturally
    drops out. The returned values stay the RAW trigger rates — scoring pays
    5 x raw rate; the edge only decides which boosters get slots and in what
    order.
    """
    if not isinstance(boosters_obj, dict):
        return [0.0] * top_k, [], [0.0] * top_k
    avg_rates = avg_rates or {}

    items = []
    for booster_id_raw, v in boosters_obj.items():
        try:
            booster_id = int(booster_id_raw)
            fv = float(v)
        except Exception:
            booster_id = -1
            fv = 0.0
        edge = fv - float(avg_rates.get(booster_id, 0.0))
        items.append((booster_id, fv, edge))

    items.sort(key=lambda item: item[2], reverse=True)
    trimmed = items[:top_k]
    while len(trimmed) < top_k:
        trimmed.append((-1, 0.0, 0.0))
    return (
        [value for _, value, _ in trimmed],
        [booster_id for booster_id, _, _ in trimmed],
        [edge for _, _, edge in trimmed],
    )


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
    - boosters = top 5 boosters by edge over the field-average trigger rate
    """
    avg_rates = booster_rate_averages()
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
            # rating is NULL for players never enriched by an event import.
            rating = float(d.get("rating") or 0.0)

            boosters_obj = _parse_json(d.get("boosters_json"))
            roles_obj = _parse_json(d.get("roles_json"))

            role_id, major_pct, minor_pct = _compute_best_role_major_minor(roles_obj)
            boosters_list, booster_ids, booster_edges = _compute_boosters_list(
                boosters_obj, top_k=5, avg_rates=avg_rates
            )
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
                booster_edges=booster_edges,
            )

        return players

    finally:
        conn.close()
