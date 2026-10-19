from typing import Any, Dict

from backend.services.rating_curve import predict_rating_vs_rank

def pick_match_rating(row: Dict[str, Any], opp_hltv_rank: int, opponent_team_id: Any = None) -> float:
    """
    The player's expected rating for this match.

    Rows built for a valuation carry `model_ratings` (services/rating_model):
    the learned model's expected rating against each team of the event,
    keyed by team id — that is the number used when the opponent is known.
    Otherwise the Top-X curve: for top-50 opponents the bucket-subtracted
    interpolation of the Top-X graph, else the stored overall rating.
    """
    if opponent_team_id is not None:
        learned = row.get("model_ratings")
        if isinstance(learned, dict):
            value = learned.get(str(int(opponent_team_id)))
            if value is not None:
                return float(value)
    predicted = predict_rating_vs_rank(row, opp_hltv_rank)
    if predicted is not None:
        return float(predicted)
    try:
        value = float(row.get("rating"))
    except (TypeError, ValueError, AttributeError):
        return 1.0
    return value
