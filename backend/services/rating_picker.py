from typing import Any, Dict

def pick_match_rating(row: Dict[str, Any], opp_hltv_rank: int) -> float:
    """
    Return the player's stored overall rating.

    `opp_hltv_rank` is kept in the signature so existing callers do not
    need to change when match rating selection is flat.
    """
    del opp_hltv_rank
    try:
        value = float(row.get("rating"))
    except (TypeError, ValueError, AttributeError):
        return 1.0
    return value
