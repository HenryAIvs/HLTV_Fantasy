"""The Leader role's percentages from the win model, not from HLTV history.

The Leader role triggers on the team's own result: the major trigger is a
win without dropping a map (2-0 in a Bo3, a Bo1 win), the minor one a win
with a map dropped, and a loss costs the role's penalty. HLTV's per-player
trigger percentages for it are the team's past results, which flatter a
team that spent the season beating weak opposition and say nothing about
the field it faces now. So, for every valuation, the Leader percentages of
each player are replaced by the team's odds of those results in THIS event:
the map win probability behind the model's series odds against each other
team, pushed through the best-of formula and averaged over the field.

  major(team) = mean over opponents of P(win without dropping a map)
  minor(team) = mean over opponents of P(win with a map dropped)

Every other role keeps HLTV's trigger rates. The odds are set once per run
(`use(...)`), read when team states are built (player_loading) and when the
roster planner scores roles, and stored with the run so a stored run keeps
the percentages it was valued with.
"""
from __future__ import annotations

import contextvars
import math
from contextlib import contextmanager
from typing import Callable, Dict, Iterable, Iterator, Optional

LEADER_ROLE_ID = 3

_ODDS: contextvars.ContextVar[Optional[Dict[int, Dict[str, float]]]] = contextvars.ContextVar("leader_odds", default=None)


def series_probability(p_map: float, wins_needed: int) -> float:
    """P(win a best-of series) from a per-map probability (the model's formula)."""
    wins_needed = max(1, int(wins_needed))
    total = 2 * wins_needed - 1
    p = min(1.0, max(0.0, float(p_map)))
    return float(sum(math.comb(total, k) * p ** k * (1.0 - p) ** (total - k) for k in range(wins_needed, total + 1)))


def map_probability(series_p: float, wins_needed: int) -> float:
    """The per-map probability behind a series probability (the model values
    one map probability per pairing, so the best-of formula inverts)."""
    wins_needed = max(1, int(wins_needed))
    target = min(1.0, max(0.0, float(series_p)))
    if wins_needed == 1:
        return target
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if series_probability(mid, wins_needed) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def score_odds(series_p: float, wins_needed: int) -> Dict[str, float]:
    """{major, minor, win}: P(win without dropping a map), P(win with one
    dropped), P(win) for one pairing."""
    wins_needed = max(1, int(wins_needed))
    p = map_probability(series_p, wins_needed)
    win = series_probability(p, wins_needed)
    major = p ** wins_needed
    return {"major": float(major), "minor": float(max(0.0, win - major)), "win": float(win)}


def field_odds(team_ids: Iterable[int], win_prob: Callable[[int, int], float], wins_needed: int) -> Dict[int, Dict[str, float]]:
    """Each team's Leader percentages against the field: the score odds
    averaged over every other team in the event."""
    ids = [int(t) for t in team_ids]
    out: Dict[int, Dict[str, float]] = {}
    for a in ids:
        rows = [score_odds(float(win_prob(a, b)), wins_needed) for b in ids if b != a]
        if not rows:
            out[a] = {"major": 0.0, "minor": 0.0, "win": 0.0}
            continue
        out[a] = {k: float(sum(r[k] for r in rows) / len(rows)) for k in ("major", "minor", "win")}
    return out


def role_score(major: float, minor: float) -> float:
    return 5.0 * float(major) + 2.0 * float(minor) - 2.0 * (1.0 - float(major) - float(minor))


@contextmanager
def use(odds: Optional[Dict[int, Dict[str, float]]]) -> Iterator[None]:
    """Value teams with these Leader percentages inside the block (None keeps
    HLTV's)."""
    token = _ODDS.set({int(t): dict(v) for t, v in (odds or {}).items()} if odds else None)
    try:
        yield
    finally:
        _ODDS.reset(token)


def current() -> Optional[Dict[int, Dict[str, float]]]:
    return _ODDS.get()


def for_team(team_id: int, odds: Optional[Dict] = None) -> Optional[Dict[str, float]]:
    table = odds if odds is not None else current()
    if not table:
        return None
    return table.get(int(team_id)) or table.get(str(team_id))


def apply_to_roles(roles_obj, team_id: int, odds: Optional[Dict] = None):
    """A player's roles table with the Leader entry replaced by the team's
    odds, when odds are in scope; otherwise unchanged."""
    lead = for_team(team_id, odds)
    if lead is None or not isinstance(roles_obj, dict):
        return roles_obj
    out = dict(roles_obj)
    out[str(LEADER_ROLE_ID)] = {"major": float(lead.get("major") or 0.0), "minor": float(lead.get("minor") or 0.0)}
    return out


def stored(odds: Dict[int, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    return {str(t): {k: float(v) for k, v in row.items()} for t, row in (odds or {}).items()}


def loaded(stored_odds) -> Optional[Dict[int, Dict[str, float]]]:
    if not stored_odds:
        return None
    return {int(t): {k: float(v) for k, v in row.items()} for t, row in stored_odds.items()}
