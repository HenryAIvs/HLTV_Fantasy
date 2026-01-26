# rating_picker.py

import math
from typing import Any, Dict, Optional


def _safe_float(v: Any) -> Optional[float]:
    """
    Convert a value to float if possible, otherwise return None.
    Treat None and NaN as empty.
    """
    try:
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _get_tier_rating(row: Dict[str, Any], tier: int) -> Optional[float]:
    """
    Given a player row and a tier (5,10,20,30,50),
    return rating_top{tier} if present, else None.
    """
    key = f"rating_top{tier}"
    if key not in row:
        return None
    return _safe_float(row.get(key))


def pick_match_rating(row: Dict[str, Any], opp_hltv_rank: int) -> float:
    """
    Decide which rating to use for a given player against a specific opponent.

    Assumes `row` is a dict from the `players` table with columns:
      - rating
      - rating_top5, rating_top10, rating_top20, rating_top30, rating_top50
        (nullable / NaN if insufficient maps)

    Logic:

    1. Base rating:
         base_rating = row["rating"]  (your original/general rating)
         If missing, default to 1.0.

    2. Map opponent HLTV rank to a preferred tier:
         1–5   -> tier 5
         6–10  -> tier 10
         11–20 -> tier 20
         21–30 -> tier 30
         31–50 -> tier 50
         >50   -> use base rating directly (ignore tiers)

    3. For a given preferred tier, apply fallback:
         rank 1–5   -> [5, 10, 20, 30, 50]
         rank 6–10  -> [10, 20, 30, 50]
         rank 11–20 -> [20, 30, 50]
         rank 21–30 -> [30, 50]
         rank 31–50 -> [50]

       For each tier in the chain, if rating_top{tier} is non-empty,
       we use that rating.

    4. If all tiers in the chain are empty, we fall back to base_rating.

    Examples:

      - vs rank 14 (GL) -> preferred chain [20,30,50]
          try rating_top20; if empty, try top30; then top50; else base_rating.

      - vs rank 26 (M80) -> preferred chain [30,50]
          try rating_top30; if empty, try top50; else base_rating.

      - vs rank 60 -> use base_rating.

    """

    # Base rating: your original overall rating
    base_rating = _safe_float(row.get("rating"))
    if base_rating is None:
        base_rating = 1.0

    # Normalise opponent rank
    r = opp_hltv_rank or 999

    # Decide fallback chain based on opponent rank
    if r <= 5:
        preferred_chain = [5, 10, 20, 30, 50]
    elif r <= 10:
        preferred_chain = [10, 20, 30, 50]
    elif r <= 20:
        preferred_chain = [20, 30, 50]
    elif r <= 30:
        preferred_chain = [30, 50]
    elif r <= 50:
        preferred_chain = [50]
    else:
        # vs lower-tier opposition: just use base rating
        return base_rating

    # Try each tier in the fallback chain
    for tier in preferred_chain:
        rt = _get_tier_rating(row, tier)
        if rt is not None:
            return rt

    # If everything is empty, use base rating
    return base_rating
