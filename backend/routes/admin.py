import json
import re
import math
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend.data.db_admin import wipe_database
from backend.data.event_db import set_active_event, upsert_event_snapshot
from backend.data.player_db import add_or_update_player, get_player
from backend.data.team_db import add_or_update_team, get_team_by_name
from backend.services.hltv_browser import HLTVBrowserError, fetch_hltv_json
from backend.services.rating_picker import pick_match_rating
from backend.services.team_strength import PARAMS_PATH
from swiss_stage.fantasy_scoring import (
    compute_rating_points,
    compute_role_points,
    compute_win_points,
    compute_booster_points,
)
from swiss_stage.swiss_models import PlayerState

router = APIRouter()

# Maps from our internal booster/role indices to HLTV IDs used in triggerRates JSON
BOOSTER_SOURCE_IDS = [
    2, 3, 5, 8, 9, 13, 16, 18, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30
]
ROLE_SOURCE_IDS = [
    0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13
]

TIER_FIELD_MAP = {
    5: ("rating_top5", "maps_top5"),
    10: ("rating_top10", "maps_top10"),
    20: ("rating_top20", "maps_top20"),
    30: ("rating_top30", "maps_top30"),
    50: ("rating_top50", "maps_top50"),
}


def _import_money_draft_data(money: Dict[str, Any], event_id: Optional[int] = None) -> Dict[str, int]:
    teams = money.get("teams") or []
    imported_players = 0
    imported_teams = 0
    event_teams_snapshot: List[Dict[str, Any]] = []

    for team_block in teams:
        team_name = team_block.get("teamData", {}).get("name", "").strip()
        if not team_name:
            continue

        player_entries = team_block.get("players", []) or []
        player_ids = []
        prices_by_player: Dict[int, int] = {}

        for entry in player_entries:
            p_data = entry.get("playerData", {}) or {}
            pid_obj = p_data.get("fantasyPlayerId", {}) or {}
            pid = pid_obj.get("playerId")
            name = p_data.get("name", "").strip()
            cost = entry.get("cost", 0)

            if pid is None or not name:
                continue

            pid = int(pid)
            player_ids.append(pid)
            prices_by_player[pid] = int(cost)

            add_or_update_player(
                player_id=pid,
                name=name,
                # Do not import overall rating from event JSON.
                # Overall rating is sourced from player stats page fetch (Top-X flow).
                rating=None,
                price=int(cost),
                best_role="",
                major_win_pct=0.0,
                minor_win_pct=0.0,
                boosters_json=None,
                roles_json=None,
            )
            imported_players += 1

        ids = player_ids[:5] if len(player_ids) >= 5 else player_ids + [0] * (5 - len(player_ids))
        existing_team = get_team_by_name(team_name)
        hltv_rank = int((existing_team or {}).get("hltv_rank") or 999)
        vrs_rank = int((existing_team or {}).get("vrs_rank") or 999)
        win_rate = float((existing_team or {}).get("win_rate") or 0.5)
        add_or_update_team(
            name=team_name,
            hltv_rank=hltv_rank,
            hltv_points=int((existing_team or {}).get("hltv_points") or 0),
            vrs_rank=vrs_rank,
            vrs_points=int((existing_team or {}).get("vrs_points") or 0),
            win_rate=win_rate,
            player_ids=ids,
        )
        imported_teams += 1
        event_teams_snapshot.append(
            {
                "team_name": team_name,
                "player_ids": ids,
                "prices_by_player": prices_by_player,
            }
        )

    if event_id is not None:
        upsert_event_snapshot(int(event_id), event_teams_snapshot)
        set_active_event(int(event_id))

    return {"imported_players": imported_players, "imported_teams": imported_teams}


@router.post("/wipe")
def wipe():
    """
    Deletes all players and teams but keeps the schema.
    """
    wipe_database()
    return {"status": "ok"}


@router.post("/import-hltv")
def import_hltv(payload: Dict[str, Any]):
    """
    Import players/teams from a pasted HLTV fantasy JSON (moneyDraftData).
    This avoids scraping and works offline with a provided JSON blob.
    """
    raw_json = payload.get("fantasy_json")
    if not raw_json:
        raise HTTPException(status_code=400, detail="fantasy_json is required")

    try:
        data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    money = data.get("moneyDraftData", {})
    teams = money.get("teams") or []
    if not teams:
        raise HTTPException(status_code=400, detail="moneyDraftData.teams missing or empty")

    counts = _import_money_draft_data(money)
    return {"status": "ok", **counts}


@router.post("/import-hltv-event")
def import_hltv_event(payload: Dict[str, Any]):
    """
    Fetch HLTV fantasy JSON for a given event id and import teams/players.
    """
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id.isdigit():
        raise HTTPException(status_code=400, detail="event_id must be numeric")

    url = f"https://www.hltv.org/fantasy/{event_id}/leagues/create/json"
    try:
        data = fetch_hltv_json(url)
    except HLTVBrowserError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV data with SeleniumBase UC: {e}")

    money = data.get("moneyDraftData", {})
    if not money:
        raise HTTPException(status_code=400, detail="moneyDraftData missing in response")

    counts = _import_money_draft_data(money, event_id=int(event_id))
    return {"status": "ok", **counts, "event_id": event_id}


@router.post("/import-trigger-rates")
def import_trigger_rates(payload: Dict[str, Any]):
    """
    Paste-in importer for triggerRates JSON (playerTriggerRates array).
    Updates boosters_json and roles_json for players.
    """
    raw = payload.get("trigger_json")
    if not raw:
        raise HTTPException(status_code=400, detail="trigger_json is required")

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    ptr_list = data.get("playerTriggerRates") or []
    if not ptr_list:
        raise HTTPException(status_code=400, detail="playerTriggerRates missing or empty")

    updated = 0
    updated_players_info: List[Dict[str, Any]] = []
    seen_player_ids = set()
    for entry in ptr_list:
        pid = entry.get("playerId", {}).get("playerId")
        if pid is None:
            continue
        booster_map_raw = entry.get("boosterIdToTriggerRate", {}) or {}
        role_map_raw = entry.get("roleIdToTriggerRate", {}) or {}

        # Preserve existing core info so NOT NULL cols are satisfied
        from backend.data.player_db import get_player  # local import to avoid circular

        existing = get_player(int(pid))
        name = (existing or {}).get("name") or f"Player {pid}"
        rating = (existing or {}).get("rating", 0.0)
        price = (existing or {}).get("price", 0)
        best_role = (existing or {}).get("best_role", "")
        major_win_pct = (existing or {}).get("major_win_pct", 0.0)
        minor_win_pct = (existing or {}).get("minor_win_pct", 0.0)

        # Start from existing booster/role JSON so we don't lose prior data
        existing_boosters = {}
        existing_roles = {}
        try:
            if existing and existing.get("boosters_json"):
                existing_boosters = json.loads(existing["boosters_json"])
        except Exception:
            existing_boosters = {}
        try:
            if existing and existing.get("roles_json"):
                existing_roles = json.loads(existing["roles_json"])
        except Exception:
            existing_roles = {}

        boosters_json = dict(existing_boosters)
        if booster_map_raw:
            boosters_json = {}
            for idx, src_id in enumerate(BOOSTER_SOURCE_IDS):
                obj = booster_map_raw.get(str(src_id), {}) or {}
                val = float(obj.get("value", 0.0))
                boosters_json[str(idx)] = val

        roles_json = dict(existing_roles)
        if role_map_raw:
            roles_json = {}
            for idx, src_id in enumerate(ROLE_SOURCE_IDS):
                rdata = role_map_raw.get(str(src_id), {}) or {}
                small = rdata.get("smallPoints", {}) or {}
                maxp = rdata.get("maxPoints", {}) or {}
                roles_json[str(idx)] = {
                    "major": float(maxp.get("value", 0.0)),
                    "minor": float(small.get("value", 0.0)),
                }

        # Pick best role by highest major trigger; fall back to existing values
        best_role_id = best_role
        best_major = major_win_pct
        best_minor = minor_win_pct
        if roles_json:
            best = max(roles_json.items(), key=lambda kv: kv[1].get("major", 0.0))
            best_role_id = str(best[0])
            best_major = float(best[1].get("major", 0.0))
            best_minor = float(best[1].get("minor", 0.0))

        add_or_update_player(
            player_id=int(pid),
            name=name,
            rating=rating,
            price=price,
            best_role=best_role_id,
            major_win_pct=best_major,
            minor_win_pct=best_minor,
            boosters_json=boosters_json,
            roles_json=roles_json,
        )
        updated += 1
        if int(pid) not in seen_player_ids:
            updated_players_info.append({"player_id": int(pid), "name": name})
            seen_player_ids.add(int(pid))

    updated_player_names = [p["name"] for p in updated_players_info]
    return {
        "status": "ok",
        "updated_players": updated,
        "updated_players_info": updated_players_info,
        "updated_player_names": updated_player_names,
    }


@router.post("/import-top-ratings")
def import_top_ratings(payload: Dict[str, Any]):
    """
    Parse pasted "vs top X opponents" text and update rating_topX/maps_topX for a player.
    Payload: { player_id: int, text: str }
    """
    player_id = payload.get("player_id")
    text = payload.get("text") or ""
    if not player_id:
        raise HTTPException(status_code=400, detail="player_id is required")
    if not str(player_id).isdigit():
        raise HTTPException(status_code=400, detail="player_id must be numeric")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    # Find all rating/tier/maps tuples
    pattern = re.compile(r"(?P<rating>\d+\.\d+)\s*vs top\s*(?P<tier>\d+)\s*opponents.*?\(\s*(?P<maps>\d+)\s*maps", re.IGNORECASE | re.DOTALL)
    matches = pattern.findall(text)
    if not matches:
        raise HTTPException(status_code=400, detail="No 'vs top X opponents' entries found")

    # Collect parsed values per tier
    tier_data = {}
    for rating_str, tier_str, maps_str in matches:
        try:
            tier = int(tier_str)
        except Exception:
            continue
        if tier not in TIER_FIELD_MAP:
            continue
        try:
            rating_val = float(rating_str)
            maps_val = int(maps_str)
        except Exception:
            continue
        tier_data[tier] = (rating_val, maps_val)

    # For each target tier, if maps < 5, fall forward to the next available tier with >=5 maps
    tiers_sorted = sorted(TIER_FIELD_MAP.keys())

    def pick_for(target_tier):
        # prefer current tier if maps >=5
        if target_tier in tier_data and tier_data[target_tier][1] >= 5:
            return tier_data[target_tier]
        # otherwise look ahead
        for t in tiers_sorted:
            if t < target_tier:
                continue
            if t in tier_data and tier_data[t][1] >= 5:
                return tier_data[t]
        # fallback: if current tier exists but <5 maps, still return it
        if target_tier in tier_data:
            return tier_data[target_tier]
        return None

    updates = {}
    for t in tiers_sorted:
        val = pick_for(t)
        if not val:
            continue
        rating_val, maps_val = val
        r_field, m_field = TIER_FIELD_MAP[t]
        updates[r_field] = rating_val
        updates[m_field] = maps_val

    if not updates:
        raise HTTPException(status_code=400, detail="No supported tiers found (expect top 5/10/20/30/50)")

    # Ensure NOT NULL fields are present by pulling existing row
    from backend.data.player_db import get_player  # local import to avoid circular

    existing = get_player(int(player_id)) or {}
    name = existing.get("name") or f"Player {player_id}"
    rating = existing.get("rating", 0.0)
    price = existing.get("price", 0)
    best_role = existing.get("best_role", "")
    major_win_pct = existing.get("major_win_pct", 0.0)
    minor_win_pct = existing.get("minor_win_pct", 0.0)
    boosters_json = existing.get("boosters_json")
    roles_json = existing.get("roles_json")

    add_or_update_player(
        player_id=int(player_id),
        name=name,
        rating=rating,
        price=price,
        best_role=best_role,
        major_win_pct=major_win_pct,
        minor_win_pct=minor_win_pct,
        boosters_json=boosters_json,
        roles_json=roles_json,
        **updates,
    )
    return {"status": "ok", "updated_fields": list(updates.keys())}


@router.post("/fit-winrate")
def fit_winrate(payload: Dict[str, Any]):
    """
    Fit logistic parameters (a_offset, b_slope) from samples of ranks and odds/probabilities.
    Payload:
      {
        "samples": [
          {"rank_a": 5, "rank_b": 20, "odds_a": 1.80} OR {"rank_a":..., "rank_b":..., "prob_a":0.55},
          ...
        ]
      }
    prob_a can be implied from decimal odds_a as 1/odds_a.
    """
    samples: List[Dict[str, Any]] = payload.get("samples") or []
    if not samples:
        raise HTTPException(status_code=400, detail="samples is required")

    points = []
    for s in samples:
        try:
            ra = int(s.get("rank_a"))
            rb = int(s.get("rank_b"))
        except Exception:
            continue
        if ra <= 0 or rb <= 0:
            continue
        prob = s.get("prob_a")
        if prob is None and s.get("odds_a"):
            try:
                prob = 1.0 / float(s["odds_a"])
            except Exception:
                prob = None
        if prob is None:
            continue
        try:
            prob = float(prob)
        except Exception:
            continue
        if prob <= 0.0 or prob >= 1.0:
            continue
        d = math.log(max(1, rb) / max(1, ra))
        logit = math.log(prob / (1.0 - prob))
        points.append((d, logit))

    if len(points) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 valid samples with rank_a, rank_b, and odds/prob")

    ds = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_d = sum(ds) / len(ds)
    mean_y = sum(ys) / len(ys)
    num = sum((d - mean_d) * (y - mean_y) for d, y in points)
    den = sum((d - mean_d) ** 2 for d in ds)
    if den == 0:
        raise HTTPException(status_code=400, detail="Cannot fit slope (all rank differences identical)")
    b = num / den
    a = mean_y - b * mean_d

    payload_out = {
        "a_offset": a,
        "b_slope": b,
        "n_samples": len(points),
        "updated_at": time.time(),
    }
    with open(PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload_out, f)

    return payload_out


@router.post("/booster-calc")
def booster_calc(payload: Dict[str, Any]):
    """
    Utility calculator for per-match and expected fantasy components.
    Payload:
      {
        "player_id": int,            # required; loads Top-X profile for match rating prediction
        "major_pct": float,
        "minor_pct": float,
        "win_prob": float,           # expected per-match win probability [0,1]
        "opponent_ranks": [int...],  # required; one rank per match
        "booster_rates": [float...], # trigger rates per match (typically 5)
        "matches": int,              # optional, default 5
        "expected_games": float      # optional, e.g. 4.2
      }
    """
    try:
        major_pct = float(payload.get("major_pct", 0.0))
        minor_pct = float(payload.get("minor_pct", 0.0))
        win_prob = float(payload.get("win_prob", 0.5))
    except Exception:
        raise HTTPException(status_code=400, detail="major_pct, minor_pct, win_prob must be numeric")

    if not (0.0 <= win_prob <= 1.0):
        raise HTTPException(status_code=400, detail="win_prob must be between 0 and 1")

    player_id = payload.get("player_id")
    if player_id is None or str(player_id).strip() == "":
        raise HTTPException(status_code=400, detail="player_id is required")
    try:
        pid = int(player_id)
    except Exception:
        raise HTTPException(status_code=400, detail="player_id must be an integer")
    picker_row: Optional[Dict[str, Any]] = get_player(pid)
    if not picker_row:
        raise HTTPException(status_code=404, detail=f"player_id {pid} not found")

    raw_opponent_ranks = payload.get("opponent_ranks", []) or []
    opponent_ranks: List[int] = []
    for r in raw_opponent_ranks:
        try:
            opponent_ranks.append(int(r))
        except Exception:
            continue

    raw_rates = payload.get("booster_rates", []) or []
    booster_rates: List[float] = []
    for r in raw_rates:
        try:
            booster_rates.append(float(r))
        except Exception:
            continue

    matches = int(payload.get("matches", 5))
    if matches < 1:
        matches = 1
    if matches > 10:
        matches = 10
    if len(opponent_ranks) < matches:
        raise HTTPException(status_code=400, detail=f"opponent_ranks must contain at least {matches} values")

    player = PlayerState(
        player_id=pid,
        rating=float(picker_row.get("rating", 1.0)),
        major_pct=major_pct,
        minor_pct=minor_pct,
        boosters=booster_rates,
    )

    role_points = compute_role_points(player)
    win_points = compute_win_points(win_prob, did_win=True)

    per_match = []
    rating_points_sum = 0.0
    for match_no in range(1, matches + 1):
        opp_rank = opponent_ranks[match_no - 1]
        match_rating = float(pick_match_rating(picker_row, int(opp_rank)))

        player.rating = match_rating
        rating_points = compute_rating_points(player)
        booster_points = compute_booster_points(player, match_no)
        total_points = rating_points + role_points + win_points + booster_points
        rating_points_sum += rating_points
        per_match.append(
            {
                "match_number": match_no,
                "opponent_rank": int(opp_rank),
                "match_rating": match_rating,
                "rating_points": rating_points,
                "role_points": role_points,
                "win_points": win_points,
                "booster_points": booster_points,
                "total_points": total_points,
            }
        )

    avg_rating_points = (rating_points_sum / float(matches)) if matches > 0 else 0.0
    out: Dict[str, Any] = {
        "rating_points": avg_rating_points,
        "role_points": role_points,
        "win_points": win_points,
        "per_match": per_match,
    }

    if payload.get("expected_games") is not None:
        try:
            expected_games = float(payload.get("expected_games"))
        except Exception:
            raise HTTPException(status_code=400, detail="expected_games must be numeric")

        expected_games = max(0.0, min(float(matches), expected_games))
        whole = int(math.floor(expected_games))
        frac = expected_games - whole

        expected_booster = sum(p["booster_points"] for p in per_match[:whole])
        if whole < matches and frac > 0:
            expected_booster += frac * per_match[whole]["booster_points"]

        base_no_booster = (avg_rating_points + role_points + win_points) * expected_games
        expected_total = base_no_booster + expected_booster

        out["expected_games"] = expected_games
        out["expected_booster_points"] = expected_booster
        out["expected_total_points"] = expected_total

    return out
