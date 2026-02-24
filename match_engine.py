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

from player_db import get_player
from rating_picker import pick_match_rating
from swiss_stage.fantasy_scoring import (
    compute_booster_points,
    compute_rating_points,
    compute_role_points,
    compute_win_points,
)
from swiss_stage.swiss_models import TeamState
from team_strength import _get_hltv_rank, get_team_winrate


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

        total = rating_pts + role_pts + win_pts + booster_pts

        player.rating_points_total += rating_pts
        player.role_points_total += role_pts
        player.win_points_total += win_pts
        player.booster_points_total += booster_pts
        player.total_points += total

        player.rating = original_rating
