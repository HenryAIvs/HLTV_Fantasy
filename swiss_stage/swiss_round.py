# swiss_stage/swiss_round.py

from typing import Dict, List, Tuple

from swiss_stage.pairing import generate_pairings
from swiss_stage.swiss_models import TeamState
from match_engine import (
    apply_fantasy_points_for_team,
    simulate_match_outcome,
)


def get_match_type(team: TeamState, bo3_mode: str) -> str:
    """
    Returns 'bo1' or 'bo3' depending on your rules.
    bo3_mode can be:
        - 'all'
        - 'none'
        - 'elim_qual' (BO3 only for elimination or qualification games)
    """
    wins, losses = team.wins, team.losses

    if bo3_mode == "all":
        return "bo3"
    if bo3_mode == "none":
        return "bo1"

    # elim_qual mode:
    #   Qualification matches = teams with 2 wins
    #   Elimination matches   = teams with 2 losses
    if wins == 2 or losses == 2:
        return "bo3"

    return "bo1"


def run_round(team_states: Dict[int, TeamState], bo3_mode: str) -> None:
    """
    Simulates a single Swiss round and updates all TeamState + PlayerState objects.

    team_states is a dict:
        { team_id: TeamState }
    """
    # Group teams by current record
    pools: Dict[tuple[int, int], List[TeamState]] = {}
    for team in team_states.values():
        if team.qualified or team.eliminated:
            continue
        key = (team.wins, team.losses)
        pools.setdefault(key, []).append(team)

    # Simulate each pool
    for _, pool in pools.items():
        if len(pool) % 2 != 0:
            raise ValueError("Pool has odd team count, which should not happen in Swiss.")

        pairings = generate_pairings(pool, team_states)

        for A, B in pairings:
            match_type = get_match_type(A, bo3_mode)

            # match_number is matches played + 1 for each team
            match_num_A = A.matches_played + 1
            match_num_B = B.matches_played + 1

            result = simulate_match_outcome(A, B, match_type)
            winner_id, loser_id, win_prob = (
                result.winner_id,
                result.loser_id,
                result.win_probability,
            )

            if winner_id == A.team_id:
                winner, loser = A, B
                did_win_A, did_win_B = True, False
            else:
                winner, loser = B, A
                did_win_A, did_win_B = False, True

            # Update records
            winner.record_win(loser.team_id)
            loser.record_loss(winner.team_id)

            # Apply fantasy points for both teams, with opponent-aware rating
            apply_fantasy_points_for_team(
                A, B.team_id, win_prob, did_win_A, match_num_A, match_type
            )
            apply_fantasy_points_for_team(
                B, A.team_id, 1.0 - win_prob, did_win_B, match_num_B, match_type
            )
