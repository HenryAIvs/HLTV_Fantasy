from swiss_stage.swiss_models import TeamState
from match_engine import simulate_match_outcome


def simulate_match(teamA: TeamState, teamB: TeamState, match_type: str):
    """
    Returns:
        winner_id, loser_id, win_prob
    where win_prob is the probability teamA wins.
    """
    result = simulate_match_outcome(teamA, teamB, match_type)
    return result.winner_id, result.loser_id, result.win_probability
