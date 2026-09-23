# team_strength.py
#
# P(team A beats team B) for the simulators, from the map model trained on
# the stored HLTV results (backend.routes.events): a per-map logistic model
# over ranks, six-month map stats, the veto pick and the per-player rating,
# averaged over the active map pool and pushed through the best-of formula
# for the match type. The model is trained on every usable stored map at
# startup and refreshed by the nightly map-model task.

import threading
import time
from typing import Any, Dict, Tuple

from backend.data.db import connect as _connect


# Ranks pinned by a valuation running on a frozen input snapshot (an event
# that has already started): consulted before the live teams table.
_RANK_OVERRIDE: dict = {}


class rank_overrides:
    """Context manager: `with rank_overrides({team_id: hltv_rank}): ...` makes
    the win model use those ranks instead of the teams table."""

    def __init__(self, mapping: dict | None):
        self.mapping = {int(k): int(v) for k, v in (mapping or {}).items() if v}

    def __enter__(self):
        self._saved = dict(_RANK_OVERRIDE)
        _RANK_OVERRIDE.clear()
        _RANK_OVERRIDE.update(self.mapping)
        return self

    def __exit__(self, *exc):
        _RANK_OVERRIDE.clear()
        _RANK_OVERRIDE.update(self._saved)
        return False


_WINS_NEEDED = {"bo1": 1, "bo3": 2, "bo5": 3}
_UNRANKED = 100  # a team with no HLTV rank is treated as a weak one
_PROFILE_TTL_SECONDS = 300.0  # rankings and map stats change nightly
_profile_cache: Dict[int, Tuple[float, Dict[str, Any]]] = {}
_prob_cache: Dict[tuple, float] = {}
_cache_lock = threading.Lock()


def _events():
    # Imported lazily: backend.routes.events reaches this module through the
    # match engine, so a module-level import would be circular.
    from backend.routes import events

    return events


def _team_profile(team_id: int) -> Dict[str, Any]:
    """Name key, ranks and current six-month map stats for a team."""
    now = time.monotonic()
    with _cache_lock:
        hit = _profile_cache.get(int(team_id))
        if hit and now - hit[0] < _PROFILE_TTL_SECONDS:
            return hit[1]
    events = _events()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT name, hltv_rank, vrs_rank, map_stats_json FROM teams WHERE team_id = ?",
            (int(team_id),),
        ).fetchone()
    finally:
        conn.close()

    def _rank(value: Any) -> int | None:
        try:
            rank = int(value)
        except Exception:
            return None
        return rank if rank > 0 else None

    profile = {
        "key": events._norm_team_name(str(row["name"] or "")) if row is not None else "",
        "hltv_rank": (_rank(row["hltv_rank"]) if row is not None else None) or _UNRANKED,
        "vrs_rank": _rank(row["vrs_rank"]) if row is not None else None,
        "map_stats": events._parse_team_map_stats(row["map_stats_json"]) if row is not None else {},
    }
    with _cache_lock:
        _profile_cache[int(team_id)] = (now, profile)
    return profile


def get_hltv_rank(team_id: int) -> int:
    """The team's HLTV rank: a pinned override if a snapshot valuation set
    one, else the teams table (unranked counts as weak)."""
    rank = _RANK_OVERRIDE.get(int(team_id))
    if rank:
        return max(1, int(rank))
    return int(_team_profile(team_id)["hltv_rank"])


def clear_caches() -> None:
    """Drop the per-team profiles and cached probabilities (after a retrain)."""
    with _cache_lock:
        _profile_cache.clear()
        _prob_cache.clear()


def get_team_winrate(teamA_id: int, teamB_id: int, match_type: str = "bo3") -> float:
    """P(A beats B) in a series of the given type from the trained map model."""
    events = _events()
    production = events.get_production_map_model()
    if not production:
        events.ensure_production_map_model()
        production = events.get_production_map_model()
    if not production:
        raise RuntimeError("No win model is trained yet: import HLTV results first.")
    rank_a = get_hltv_rank(teamA_id)
    rank_b = get_hltv_rank(teamB_id)
    wins_needed = _WINS_NEEDED.get(str(match_type or "bo3").lower(), 2)
    key = (int(teamA_id), int(teamB_id), rank_a, rank_b, wins_needed, production.get("trained_at"))
    with _cache_lock:
        cached = _prob_cache.get(key)
    if cached is not None:
        return cached
    a = {**_team_profile(teamA_id), "hltv_rank": rank_a}
    b = {**_team_profile(teamB_id), "hltv_rank": rank_b}
    p = events.production_series_probability(production, a, b, wins_needed)
    with _cache_lock:
        if len(_prob_cache) > 200000:
            _prob_cache.clear()
        _prob_cache[key] = p
    return p
