from typing import Any, Dict

from backend.services.rating_curve import predict_rating_vs_rank

def pick_match_rating(row: Dict[str, Any], opp_hltv_rank: int) -> float:
    """
    Return a player rating estimate for the opponent's HLTV rank.

    For top-50 opponents, use the same bucket-subtracted linear
    interpolation as the Top-X graph. If Top-X data is incomplete or the
    opponent is outside the Top 50, fall back to the stored overall rating.
    """
    predicted = predict_rating_vs_rank(row, opp_hltv_rank)
    if predicted is not None:
        return float(predicted)
    try:
        value = float(row.get("rating"))
    except (TypeError, ValueError, AttributeError):
        return 1.0
    return value
