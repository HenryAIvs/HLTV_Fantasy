# team_strength.py

import math
import sqlite3
import json
import os
from typing import Dict, Tuple

DB_PATH = "fantasy_players.db"
PARAMS_PATH = "winrate_params.json"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_hltv_rank(team_id: int) -> int:
    """
    Fetch hltv_rank for a given team_id from the teams table.
    If missing or invalid, fall back to a large rank (weaker team).
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT hltv_rank FROM teams WHERE team_id = ?",
            (team_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return 100  # unknown team, treat as weak

    try:
        rank = int(row["hltv_rank"])
        if rank <= 0:
            raise ValueError
        return rank
    except Exception:
        return 100


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# Fitted logistic parameters from Team odd.xlsx
_A_OFFSET = -0.04246783059403746
_B_SLOPE  =  0.4270428576181436

_PARAM_CACHE_MTIME = None
_PARAM_CACHE_VALS: Tuple[float, float] | None = None


def _load_params_from_file() -> Tuple[float, float]:
    global _PARAM_CACHE_MTIME, _PARAM_CACHE_VALS
    try:
        mtime = os.path.getmtime(PARAMS_PATH)
    except OSError:
        return _A_OFFSET, _B_SLOPE

    if _PARAM_CACHE_VALS is not None and _PARAM_CACHE_MTIME == mtime:
        return _PARAM_CACHE_VALS

    try:
        with open(PARAMS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        a = float(data.get("a_offset", _A_OFFSET))
        b = float(data.get("b_slope", _B_SLOPE))
        _PARAM_CACHE_MTIME = mtime
        _PARAM_CACHE_VALS = (a, b)
        return a, b
    except Exception:
        return _A_OFFSET, _B_SLOPE


def _get_params() -> Tuple[float, float]:
    """
    Return (a_offset, b_slope), allowing overrides via winrate_params.json.
    """
    return _load_params_from_file()


def get_team_winrate(teamA_id: int, teamB_id: int, match_type: str = "bo3") -> float:
    """
    Return P(A beats B) using HLTV global ranks and the calibrated logistic model.

      - Look up HLTV ranks for both teams from DB.
      - Convert ranks to strength via s = -ln(rank).
      - Compute d = sA - sB = ln(rankB / rankA).
      - P(A wins) = sigmoid(A_OFFSET + B_SLOPE * d).

    match_type is accepted for signature compatibility, but this model
    does not currently vary by BO1/BO3; any such effects are handled
    elsewhere in scoring.
    """

    rankA = _get_hltv_rank(teamA_id)
    rankB = _get_hltv_rank(teamB_id)

    # strength difference: d = ln(rankB/rankA)
    ra = max(1, rankA)
    rb = max(1, rankB)
    d = math.log(rb / ra)

    a_offset, b_slope = _get_params()
    z = a_offset + b_slope * d
    pA = _logistic(z)
    return pA
