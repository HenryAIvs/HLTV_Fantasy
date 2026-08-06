import math
from typing import Any, Dict, List, Optional, Tuple


TOP_TIERS = (5, 10, 20, 30, 50)
SAMPLE_PRIOR_SCALE = 0.5

# Average player degradation curve: how much the typical player's rating vs
# top-N opponents sits below their overall rating, per tier. Fitted from all
# players who have real per-tier data, then used to estimate rank-adjusted
# ratings for players who lack that data (see predict_rating_vs_rank).
_AVERAGE_BUCKET_OFFSETS: Dict[int, float] = {}


def set_average_bucket_offsets(offsets: Dict[int, float]) -> None:
    global _AVERAGE_BUCKET_OFFSETS
    _AVERAGE_BUCKET_OFFSETS = {int(k): float(v) for k, v in (offsets or {}).items()}


def get_average_bucket_offsets() -> Dict[int, float]:
    return dict(_AVERAGE_BUCKET_OFFSETS)


def fit_average_bucket_offsets(player_rows) -> Dict[int, float]:
    """Maps-weighted mean of (rating-vs-top-N minus overall) per tier, across
    players with real per-tier data."""
    sums = {t: 0.0 for t in TOP_TIERS}
    weights = {t: 0.0 for t in TOP_TIERS}
    for row in player_rows or []:
        for bucket in build_player_bucket_rows(row):
            tier = int(bucket.get("tier") or 0)
            if tier not in TOP_TIERS:
                continue
            maps = _safe_float(bucket.get("maps")) or 0.0
            delta = _safe_float(bucket.get("raw_bucket_delta"))
            if maps <= 0 or delta is None:
                continue
            sums[tier] += float(delta) * float(maps)
            weights[tier] += float(maps)
    return {t: sums[t] / weights[t] for t in TOP_TIERS if weights[t] > 0}
BUCKET_RANGES = {
    5: (1, 5),
    10: (6, 10),
    20: (11, 20),
    30: (21, 30),
    50: (31, 50),
}
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


def _sample_shrinkage_weight(maps: Any, total_maps: Any, tier: int) -> float:
    m = _safe_float(maps)
    if m is None or m <= 0:
        return 0.0
    total = _safe_float(total_maps)
    if total is None or total <= 0:
        return 1.0
    rank_min, rank_max = BUCKET_RANGES.get(int(tier), (int(tier), int(tier)))
    bucket_width = max(1.0, float(rank_max - rank_min + 1))
    expected_bucket_maps = max(1.0, float(total) * (bucket_width / 50.0))
    prior_maps = max(1.0, expected_bucket_maps * SAMPLE_PRIOR_SCALE)
    return float(m) / (float(m) + prior_maps)


def _total_recent_maps_proxy(row: Dict[str, Any]) -> Optional[float]:
    maps_50 = _get_tier_maps(row, 50)
    if maps_50 is not None and maps_50 > 0:
        return maps_50
    maps_values = [_get_tier_maps(row, tier) for tier in TOP_TIERS]
    valid = [float(v) for v in maps_values if v is not None and v > 0]
    return max(valid) if valid else None


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
    total_maps = _total_recent_maps_proxy(row)
    for tier in (*TOP_TIERS, 100):
        entry = anchor_map.get(int(tier))
        if not entry:
            continue
        raw_delta, maps = entry
        shrinkage_weight = _sample_shrinkage_weight(maps, total_maps, int(tier))
        delta = float(raw_delta) * shrinkage_weight
        rank_min, rank_max = BUCKET_RANGES.get(int(tier), (int(tier), int(tier)))
        midpoint = (float(rank_min) + float(rank_max)) / 2.0
        rows.append(
            {
                "tier": int(tier),
                "tier_label": _tier_label(int(tier)),
                "rank_min": int(rank_min),
                "rank_max": int(rank_max),
                "rank_midpoint": midpoint,
                "raw_bucket_delta": float(raw_delta),
                "raw_bucket_rating": float(base + raw_delta),
                "shrinkage_weight": float(shrinkage_weight),
                "bucket_delta": float(delta),
                "bucket_rating": float(base + delta),
                "maps": float(maps),
            }
        )
    return rows


def _linear_interpolate(points: List[Tuple[float, float]], x: float) -> float:
    if not points:
        return 1.0
    ordered = sorted(points, key=lambda p: p[0])
    if x <= ordered[0][0]:
        return float(ordered[0][1])
    if x >= ordered[-1][0]:
        return float(ordered[-1][1])

    for idx in range(1, len(ordered)):
        x0, y0 = ordered[idx - 1]
        x1, y1 = ordered[idx]
        if x <= x1:
            if x1 == x0:
                return float(y1)
            t = (float(x) - float(x0)) / (float(x1) - float(x0))
            return float(y0) + t * (float(y1) - float(y0))
    return float(ordered[-1][1])


def build_player_interpolated_rows(bucket_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    points: List[Tuple[float, float]] = []
    for row in bucket_rows:
        tier = int(row.get("tier") or 0)
        if tier not in TOP_TIERS:
            continue
        rating = _safe_float(row.get("bucket_rating"))
        if rating is None:
            continue
        points.append((float(tier), float(rating)))

    if not points:
        return []

    ranks = [1, 5, 10, 20, 30, 50]
    return [
        {
            "rank": int(rank),
            "rank_label": str(rank),
            "bucket_rating": _linear_interpolate(points, float(rank)),
        }
        for rank in ranks
    ]


def predict_rating_vs_rank(row: Dict[str, Any], opponent_rank: Any) -> Optional[float]:
    """
    Predict player rating against an opponent at HLTV rank X.

    Uses the same non-overlapping Top-X bucket subtraction as the graph,
    then linearly interpolates between Top-X boundary anchors. Ranks beyond the
    stored Top-50 range return None so callers can fall back to overall rating.
    """
    try:
        rank = float(opponent_rank)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rank) or rank <= 0 or rank > 50:
        return None

    points: List[Tuple[float, float]] = []
    for bucket in build_player_bucket_rows(row):
        tier = int(bucket.get("tier") or 0)
        if tier not in TOP_TIERS:
            continue
        rating = _safe_float(bucket.get("bucket_rating"))
        if rating is None:
            continue
        points.append((float(tier), float(rating)))

    if not points:
        # No per-tier data: estimate from the player's overall rating plus the
        # average degradation curve, so rank still matters instead of a flat
        # overall rating for everyone.
        overall = _safe_float(row.get("rating"))
        if overall is not None and _AVERAGE_BUCKET_OFFSETS:
            est = [(float(t), overall + off) for t, off in _AVERAGE_BUCKET_OFFSETS.items()]
            return _linear_interpolate(est, rank)
        return None
    return _linear_interpolate(points, rank)


def build_player_topx_graph(row: Dict[str, Any]) -> Dict[str, Any]:
    base_rating = _safe_float(row.get("rating"))
    bucket_rows = build_player_bucket_rows(row)
    sample_maps = float(sum(float(r.get("maps") or 0.0) for r in bucket_rows if int(r.get("tier") or 0) in TOP_TIERS))
    total_maps_proxy = _total_recent_maps_proxy(row)
    return {
        "base_rating": float(base_rating) if base_rating is not None else 1.0,
        "sample_maps": sample_maps,
        "total_maps_proxy": float(total_maps_proxy) if total_maps_proxy is not None else None,
        "bucket_rows": bucket_rows,
        "graph_rows": build_player_interpolated_rows(bucket_rows),
    }
