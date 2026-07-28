# match_engine.py
#
# Shared match-level utilities that are independent of any particular
# tournament structure. Bracket code (Swiss, single-elim, etc.) can call
# into these helpers to evaluate win probabilities, simulate outcomes,
# and apply fantasy-scoring side effects for each match.

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable
import random

from backend.data.player_db import get_player
from backend.services.rating_picker import pick_match_rating
from backend.swiss_stage.fantasy_scoring import (
    compute_booster_points,
    compute_rating_points,
    compute_role_points,
    compute_win_points,
)
from backend.swiss_stage.swiss_models import TeamState
from backend.services.team_strength import _get_hltv_rank, get_team_winrate


BOOSTER_NAMES = {
    0: "Best Pistol Round",
    1: "Bottom of scoreboard",
    2: "Clutch",
    3: "Top of scoreboard",
    4: "Avenger",
    5: "Bait",
    6: "Rambo",
    7: "Flash",
    8: "Mister consistent",
    9: "Kobe",
    10: "Saver",
    11: "Assist",
    12: "Aim bot",
    13: "Quad",
    14: "Carry",
    15: "Cannon fodder",
    16: "Farmer",
    17: "Hellcase",
}


@dataclass(frozen=True)
class MatchResult:
    """Outcome of a single simulated match."""

    winner_id: int
    loser_id: int
    win_probability: float  # P(team_a beats team_b) from the perspective of team_a


WinProbabilityFn = Callable[[int, int, str], float]
RandomFn = Callable[[], float]


@lru_cache(maxsize=10000)
def _get_player_cached(player_id: int):
    return get_player(player_id)


def calculate_win_probability(
    team_a_id: int,
    team_b_id: int,
    match_type: str,
    winrate_model: WinProbabilityFn | None = None,
) -> float:
    """
    Calculate P(team A beats team B) using the provided winrate model.
    Defaults to the HLTV-rank-based model in team_strength.get_team_winrate.
    """
    model = winrate_model or get_team_winrate
    return model(team_a_id, team_b_id, match_type)


def simulate_match_outcome(
    team_a: TeamState,
    team_b: TeamState,
    match_type: str,
    winrate_fn: WinProbabilityFn | None = None,
    rng: RandomFn | None = None,
) -> MatchResult:
    """
    Simulate a single head-to-head match and return the winner/loser IDs
    plus the win probability that was used (P(team_a wins)).
    """
    prob_a_wins = calculate_win_probability(
        team_a.team_id, team_b.team_id, match_type, winrate_fn
    )
    draw = (rng or random.random)()

    if draw < prob_a_wins:
        return MatchResult(team_a.team_id, team_b.team_id, prob_a_wins)
    return MatchResult(team_b.team_id, team_a.team_id, prob_a_wins)


def apply_fantasy_points_for_team(
    team: TeamState,
    opponent_team_id: int,
    win_probability: float,
    did_win: bool,
    match_number: int,
    match_type: str,
    player_rows_by_id: dict[int, dict] | None = None,
    team_rank_by_id: dict[int, int] | None = None,
) -> None:
    """
    Apply fantasy point side effects for every player on a single team,
    using the same rating/role/win/booster logic previously baked into
    the Swiss round simulation.

    win_probability should be the chance of THIS team winning the match.
    The caller can reuse the same base probability for both teams by
    passing p for team A and (1 - p) for team B.
    """
    is_bo1 = match_type.lower() == "bo1"
    if team_rank_by_id is not None and opponent_team_id in team_rank_by_id:
        opponent_rank = int(team_rank_by_id[opponent_team_id])
    else:
        opponent_rank = _get_hltv_rank(opponent_team_id)

    for player in team.players.values():
        if player_rows_by_id is not None:
            row = player_rows_by_id.get(player.player_id)
        else:
            row = _get_player_cached(player.player_id)
        if row is not None:
            match_rating = pick_match_rating(row, opponent_rank)
        else:
            match_rating = player.rating

        original_rating = player.rating
        player.rating = match_rating

        rating_pts = compute_rating_points(player)
        if is_bo1:
            rating_pts *= 0.5  # BO1 halves the rating contribution

        role_pts = compute_role_points(player)
        win_pts = compute_win_points(win_probability, did_win)
        booster_pts = compute_booster_points(player, match_number)
        booster_idx = match_number - 1
        booster_id = None
        if 0 <= booster_idx < len(player.booster_ids):
            booster_id = player.booster_ids[booster_idx]
            if booster_id < 0:
                booster_id = None
        booster_rate = float(player.boosters[booster_idx]) if 0 <= booster_idx < len(player.boosters) else 0.0
        booster_options = [
            {
                "booster_id": int(option_id),
                "booster_name": BOOSTER_NAMES.get(int(option_id), f"Booster {int(option_id)}"),
                "booster_trigger_rate": float(option_rate),
                "booster_points": 5.0 * float(option_rate),
            }
            for option_id, option_rate in sorted(
                (player.booster_rates or {}).items(),
                key=lambda item: float(item[1]),
                reverse=True,
            )
            if float(option_rate) > 0
        ]

        total = rating_pts + role_pts + win_pts + booster_pts

        player.rating_points_total += rating_pts
        player.role_points_total += role_pts
        player.win_points_total += win_pts
        player.booster_points_total += booster_pts
        player.total_points += total
        player.point_breakdown.append(
            {
                "match_number": int(match_number),
                "match_type": str(match_type).upper(),
                "opponent_team_id": int(opponent_team_id),
                "opponent_rank": int(opponent_rank),
                "did_win": bool(did_win),
                "win_probability": float(win_probability),
                "rating_used": float(match_rating),
                "rating_points": float(rating_pts),
                "win_points": float(win_pts),
                "role_id": player.role_id,
                "role_major_pct": float(player.major_pct),
                "role_minor_pct": float(player.minor_pct),
                "role_points": float(role_pts),
                "booster_slot": int(match_number),
                "booster_id": booster_id,
                "booster_name": BOOSTER_NAMES.get(booster_id, f"Booster {booster_id}") if booster_id is not None else f"Booster slot {match_number}",
                "booster_trigger_rate": booster_rate,
                "booster_points": float(booster_pts),
                "booster_options": booster_options,
                "total_points": float(total),
            }
        )

        player.rating = original_rating
