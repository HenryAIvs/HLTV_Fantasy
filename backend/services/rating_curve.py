import math
from typing import Any, Dict, List, Optional, Tuple


TOP_TIERS = (5, 10, 20, 30, 50)
TIER_LABELS = {
    5: "Top 5",
    10: "6-10",
    20: "11-20",
    30: "21-30",
    50: "31-50",
    100: "Overall",
}


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _get_tier_rating(row: Dict[str, Any], tier: int) -> Optional[float]:
    return _safe_float(row.get(f"rating_top{tier}"))


def _get_tier_maps(row: Dict[str, Any], tier: int) -> Optional[float]:
    return _safe_float(row.get(f"maps_top{tier}"))


def _tier_label(tier: int) -> str:
    return TIER_LABELS.get(int(tier), f"Top {int(tier)}")


def _derive_bucket_from_cumulative(
    prev_rating: Optional[float],
    prev_maps: Optional[float],
    cur_rating: Optional[float],
    cur_maps: Optional[float],
) -> Optional[Tuple[float, float]]:
    if cur_rating is None or cur_maps is None or cur_maps <= 0:
        return None
    if prev_rating is None or prev_maps is None:
        return float(cur_rating), float(cur_maps)
    if cur_maps <= prev_maps:
        return None

    bucket_maps = float(cur_maps - prev_maps)
    weighted_sum = float(cur_rating) * float(cur_maps) - float(prev_rating) * float(prev_maps)
    bucket_rating = weighted_sum / bucket_maps
    if not math.isfinite(bucket_rating):
        return None
    return float(bucket_rating), float(bucket_maps)


def build_player_delta_anchors(row: Dict[str, Any]) -> List[Tuple[float, float, float]]:
    """
    Build (rank, delta, weight) where Top-X values are normalized into
    non-overlapping buckets:
    Top5, 6-10, 11-20, 21-30, 31-50.
    delta = bucket_rating - overall_rating.
    Includes Top 100 anchor at delta=0.
    """
    anchors: List[Tuple[float, float, float]] = []
    top_map_counts: List[float] = []
    overall = _safe_float(row.get("rating"))
    if overall is None:
        return anchors
    prev_rating: Optional[float] = None
    prev_maps: Optional[float] = None
    for tier in TOP_TIERS:
        cur_rating = _get_tier_rating(row, tier)
        cur_maps = _get_tier_maps(row, tier)
        derived = _derive_bucket_from_cumulative(prev_rating, prev_maps, cur_rating, cur_maps)
        if cur_rating is not None and cur_maps is not None and cur_maps > 0:
            prev_rating, prev_maps = cur_rating, cur_maps
        if derived is None:
            continue
        rating, maps = derived
        if rating is None or maps is None or maps <= 0:
            continue
        w = max(1.0, float(maps))
        delta = float(rating) - float(overall)
        anchors.append((float(tier), delta, w))
        top_map_counts.append(w)

    maps_50 = _get_tier_maps(row, 50)
    if maps_50 is not None and maps_50 > 0:
        w100 = float(maps_50)
    elif top_map_counts:
        w100 = max(top_map_counts)
    else:
        w100 = 1.0
    anchors.append((100.0, 0.0, w100))

    anchors.sort(key=lambda x: x[0])
    return anchors


def build_player_bucket_rows(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    overall = _safe_float(row.get("rating"))
    base = float(overall) if overall is not None else 1.0
    rows: List[Dict[str, Any]] = []
    anchor_map = {int(x): (float(y), float(w)) for x, y, w in build_player_delta_anchors(row)}
    for tier in (*TOP_TIERS, 100):
        entry = anchor_map.get(int(tier))
        if not entry:
            continue
        delta, maps = entry
        rows.append(
            {
                "tier": int(tier),
                "tier_label": _tier_label(int(tier)),
                "bucket_delta": float(delta),
                "bucket_rating": float(base + delta),
                "maps": float(maps),
            }
        )
    return rows


def build_player_topx_graph(row: Dict[str, Any]) -> Dict[str, Any]:
    base_rating = _safe_float(row.get("rating"))
    bucket_rows = build_player_bucket_rows(row)
    sample_maps = float(sum(float(r.get("maps") or 0.0) for r in bucket_rows))
    return {
        "base_rating": float(base_rating) if base_rating is not None else 1.0,
        "sample_maps": sample_maps,
        "bucket_rows": bucket_rows,
    }
