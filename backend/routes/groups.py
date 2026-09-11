"""Double-elimination (GSL) group stage: X groups of 4, two qualify, two out.

Each group plays five BO3s — two opening matches, winners' match, elimination
match, decider — giving exactly 32 outcomes per group. Groups are independent,
so player expectations are exact per group and roster metrics that decompose
per group (average EV, ceiling) stay exact for any number of groups.
"""

import heapq
import itertools
import json
import re
import math
import os
import random
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend.data.db import connect as _connect
from backend.data.player_db import get_player
from backend.data.singleton_state import SingletonState
from backend.services import team_strength
from backend.services import roster_kernels as rk
from backend.routes.playoff import (
    _build_playoff_lookup_context,
    _clone_team_states,
    cached_win_prob,
    _filter_saved_combo_teams,
    _page_items,
    _play_match_deterministic,
    _sort_saved_combo_teams,
)
from backend.data.team_db import add_or_update_team, get_team_by_id, get_team_by_name
from backend.services.role_assignment import best_role_assignment_for_team, extract_role_scores_for_player
from backend.services.swiss_booster_assignment import (
    BOOSTER_NAMES,
    BOOSTER_POINT_VALUE,
    _max_weight_assignment,
    parse_booster_rates,
)
from backend.services.team_optimizer import iter_valid_rosters, parse_optimizer_payload, serialize_roster
from backend.services.roster_plan import (  # moved out of this module; shared with the playoff and swiss optimisers
    _NUM_ROLES,
    _ROLE_UNAVAILABLE,
    _booster_bound_lagrangian,
    _booster_slot_weights,
    _exact_role_assignment,
    _player_booster_sub,
    _player_booster_ub,
    _score_roster_exact,
    optimize_group_boosters_for_roster,
)
from backend.swiss_stage.fantasy_scoring import compute_padding_components
from backend.swiss_stage.swiss_models import TeamState
from backend.swiss_stage.team_initialization import initialize_teams

# A group plays at most 3 matches (opening + winners'/elimination + decider).
GROUP_MATCH_BASELINE = 3

# Above this many players, roster combinations explode (64 teams -> C(320,5)
# ~ 27 billion) and precomputing/storing every roster is impossible; queries
# switch to a live branch-and-bound search for the top rosters instead.
LIVE_OPTIMIZER_PLAYER_THRESHOLD = 48
LIVE_OPTIMIZER_MAX_K = 2000

router = APIRouter()

GROUPS_JOBS: Dict[str, Dict[str, Any]] = {}
GROUPS_JOBS_LOCK = threading.Lock()
GROUPS_BEST_TEAM_JOBS: Dict[str, Dict[str, Any]] = {}
GROUPS_BEST_TEAM_JOBS_LOCK = threading.Lock()

# Keyed by fantasy event: the stored simulation and its best-team combos belong
# to ONE tournament, so switching the active event must never surface another
# event's run (the FISSURE Playground tab was showing Porto's stored groups).
_GROUPS_STATE = SingletonState("groups_simulation_state", result_column="results_json", result_key="results", keyed=True)
_GROUPS_BEST_STATE = SingletonState("groups_best_team_state", keyed=True)
_GROUPS_BEST_META = SingletonState("groups_best_team_meta", keyed=True)


def _state_key() -> int:
    """Row key for the stored groups state: the active fantasy event id."""
    from backend.data.event_db import get_active_event_id

    return int(get_active_event_id() or 1)


# Live best-team queries memoized per (event, stored-run stamp, body).
_LIVE_QUERY_CACHE: Dict[tuple, dict] = {}
_LIVE_QUERY_CACHE_LOCK = threading.Lock()


def warm_caches() -> None:
    """Parse the active event's stored groups run into the state cache at
    startup (in a background thread) so the first Tournament open after a
    restart does not pay the ~1 s blob parse itself."""
    try:
        _GROUPS_STATE.load(key=_state_key())
    except Exception:
        pass

GROUP_MATCH_KEYS = ["opening_1", "opening_2", "winners", "elimination", "decider"]
GROUP_MATCH_LABELS = {
    "opening_1": "Opening 1",
    "opening_2": "Opening 2",
    "winners": "Winners' match",
    "elimination": "Elimination match",
    "decider": "Decider",
}


def ensure_groups_schema() -> None:
    for state in (_GROUPS_STATE, _GROUPS_BEST_STATE, _GROUPS_BEST_META, _INPUT_SNAPSHOT):
        state.ensure_table()


UNKNOWN_TEAM_RANK = 250


def _get_or_create_unknown_team(index: int) -> int:
    """Distinct generic opponents ('Unknown 1', 'Unknown 2', ...) at rank 250."""
    name = f"Unknown {index}"
    existing = get_team_by_name(name)
    if existing:
        return int(existing.get("team_id"))
    add_or_update_team(
        name=name,
        hltv_rank=UNKNOWN_TEAM_RANK,
        hltv_points=0,
        vrs_rank=UNKNOWN_TEAM_RANK,
        vrs_points=0,
        win_rate=0.5,
        player_ids=[0, 0, 0, 0, 0],
        hltv_team_id=None,
    )
    created = get_team_by_name(name) or {}
    return int(created.get("team_id") or 0)


def _normalize_groups_payload(payload: dict) -> dict:
    groups_raw = payload.get("groups") or []
    if not groups_raw or not isinstance(groups_raw, list):
        raise HTTPException(status_code=400, detail="groups must be a non-empty list of team lists")
    gf_raw = str(payload.get("group_format") or "").strip().lower()
    group_format = gf_raw if gf_raw in ("de8", "de8_top3") else "gsl4"
    size = 8 if group_format.startswith("de8") else 4
    quals_per_group = {"gsl4": 2, "de8": 4, "de8_top3": 3}[group_format]
    groups: List[List[int]] = []
    seen: set = set()
    unknown_counter = 0
    for group in groups_raw:
        ids: List[int] = []
        for x in group or []:
            if str(x).strip().lower() in ("unknown", "-1"):
                unknown_counter += 1
                tid = _get_or_create_unknown_team(unknown_counter)
                if tid <= 0:
                    raise HTTPException(status_code=500, detail="Failed to create an Unknown placeholder team")
                ids.append(tid)
            else:
                ids.append(int(x))
        if len(ids) != size or any(t <= 0 for t in ids):
            raise HTTPException(status_code=400, detail=f"Each group needs exactly {size} team IDs")
        if seen.intersection(ids):
            raise HTTPException(status_code=400, detail="A team appears in more than one group")
        seen.update(ids)
        groups.append(ids)
    combined = bool(payload.get("combined_playoffs"))
    stop_teams = int(payload.get("playoff_stop_teams") or 1)
    if combined and not combined_shape_supported(group_format, len(groups)):
        raise HTTPException(
            status_code=400,
            detail=reject_unsupported_combined_shape(group_format, len(groups), payload.get("event_id")),
        )
    if combined and group_format == "de8_top3":
        # Porto/Cologne shape: 2 groups of 8, top 3 each -> 6-team bracket with
        # the two group winners seeded straight into the semi-finals.
        if len(groups) != 2:
            raise HTTPException(
                status_code=400,
                detail="Top-3 double-elim groups with combined playoffs support exactly 2 groups.",
            )
        if stop_teams != 1:
            raise HTTPException(
                status_code=400,
                detail="The top-3 combined playoff plays the full 6-team bracket (playoff_stop_teams must be 1).",
            )
    elif combined:
        bracket_size = quals_per_group * len(groups)
        if bracket_size & (bracket_size - 1) != 0:
            raise HTTPException(
                status_code=400,
                detail="Combined playoffs need the total qualifier count to be a power of two.",
            )
        if stop_teams < 1 or stop_teams >= bracket_size or stop_teams & (stop_teams - 1) != 0:
            raise HTTPException(
                status_code=400,
                detail="playoff_stop_teams must be a power of two smaller than the bracket size (1 = play out the full bracket).",
            )
    return {
        "groups": groups,
        "group_format": group_format,
        "combined_playoffs": combined,
        "playoff_stop_teams": stop_teams,
        # The stored run is keyed by this event (see _state_key).
        "event_id": _state_key(),
    }


# Match-slot labels per group format, in play order (the enumerators' keys).
# (round key, short label, full name, bracket side, match slots folded into it).
# A team plays at most one slot per round, so folding sums cleanly. The first
# entry is the opening match every team plays.
_GROUP_STAGE_ROUNDS: Dict[str, List[tuple]] = {
    "gsl4": [
        ("opening", "Opening", "Opening match", "upper", ["opening_1", "opening_2"]),
        ("winners", "Winners'", "Winners' match", "upper", ["winners"]),
        ("elimination", "Elimination", "Elimination match", "lower", ["elimination"]),
        ("decider", "Decider", "Decider", "lower", ["decider"]),
    ],
    "de8": [
        ("ub_r1", "UB R1", "Opening match", "upper", ["opening_1", "opening_2", "opening_3", "opening_4"]),
        ("ub_sf", "UB SF", "Upper semi-final", "upper", ["upper_sf_1", "upper_sf_2"]),
        ("lb_r1", "LB R1", "Lower round 1", "lower", ["lower_r1_1", "lower_r1_2"]),
        ("lb_sf", "LB SF", "Lower semi-final", "lower", ["lower_sf_1", "lower_sf_2"]),
    ],
    "de8_top3": [
        ("ub_r1", "UB R1", "Opening match", "upper", ["opening_1", "opening_2", "opening_3", "opening_4"]),
        ("ub_sf", "UB SF", "Upper semi-final", "upper", ["upper_sf_1", "upper_sf_2"]),
        ("ub_final", "UB Final", "Upper final", "upper", ["upper_final"]),
        ("lb_r1", "LB R1", "Lower round 1", "lower", ["lower_r1_1", "lower_r1_2"]),
        ("lb_sf", "LB SF", "Lower semi-final", "lower", ["lower_sf_1", "lower_sf_2"]),
        ("lb_final", "LB Final", "Lower final", "lower", ["lower_final"]),
    ],
}
_PLAYOFF_ROUND_FULL = {"QF": "Quarter-final", "SF": "Semi-final", "Final": "Final", "R16": "Round of 16", "R32": "Round of 32"}


def _booster_meta(row: dict, p: Any) -> Optional[tuple]:
    """(booster_id, name, trigger rate, slot, edge) for a scored match row, or
    None when the slot has no booster. The slot is the team's match number, so
    a group stage always maps to one slot; playoff rounds vary with the path."""
    bid = row.get("booster_id")
    if bid is None:
        return None
    slot = int(row.get("booster_slot") or row.get("match_number") or 0)
    edges = getattr(p, "booster_edges", None) or []
    edge = float(edges[slot - 1]) if 0 < slot <= len(edges) else 0.0
    return (int(bid), str(row.get("booster_name") or f"Booster {bid}"), float(row.get("booster_trigger_rate") or 0.0), slot, edge)


def _add_booster_weight(bucket: Dict[int, list], meta: Optional[tuple], w: float) -> None:
    if meta is None or w <= 0.0:
        return
    bid = meta[0]
    cell = bucket.get(bid)
    if cell is None:
        bucket[bid] = [w, meta[1], meta[2], meta[3], meta[4]]
    else:
        cell[0] += w


def _booster_list(bucket: Dict[int, list]) -> List[Dict[str, Any]]:
    """Boosters seen in a stage with their share of the times it is played."""
    total = sum(v[0] for v in bucket.values())
    if total <= 0.0:
        return []
    rows = [
        {"booster_id": bid, "booster_name": v[1], "booster_rate": v[2], "slot": v[3], "edge": v[4], "share": v[0] / total}
        for bid, v in bucket.items()
    ]
    rows.sort(key=lambda r: -r["share"])
    return rows


def _new_stage_acc() -> Dict[str, Any]:
    return {
        "reach": {}, "wins": {}, "players": {}, "penalty": {}, "padding": {}, "boost": {},
        "elim": {}, "pad_prob": {}, "opp": {}, "opp_pts": {}, "role": {},
    }


def _accumulate_group_stage_stats(acc: Dict[str, Any], prob: float, states: Dict[int, TeamState], matches: List[dict]) -> None:
    """Attribute one exact group outcome to match slots: which matches each
    team played (and won) and against whom, each player's points per match
    (and per opponent) from the scorer's per-match breakdown rows, the booster
    each match used, where the team got knocked out (an ELIMINATION row follows
    the match that ended its run) and the padding added straight to totals for
    matches a quick qualifier skips."""
    team_keys: Dict[int, List[str]] = {}
    for m in matches:
        for tid in m.get("teams") or []:
            team_keys.setdefault(int(tid), []).append(str(m["key"]))
        w = int(m.get("winner") or 0)
        if w:
            wins = acc["wins"].setdefault(w, {})
            wins[str(m["key"])] = wins.get(str(m["key"]), 0.0) + prob
    for tid, keys in team_keys.items():
        reach = acc["reach"].setdefault(tid, {})
        for k in keys:
            reach[k] = reach.get(k, 0.0) + prob
    for tid, ts in states.items():
        tid = int(tid)
        keys = team_keys.get(tid, [])
        team_elim = acc["elim"].setdefault(tid, {})
        team_opp = acc["opp"].setdefault(tid, {})
        team_done = False  # elimination / opponent / padding odds are per team: take them from the first player
        for pid, p in ts.players.items():
            pid = int(pid)
            per_key = acc["players"].setdefault(pid, {})
            boost_keys = acc["boost"].setdefault(pid, {})
            opp_pts = acc["opp_pts"].setdefault(pid, {})
            seen = [0.0, 0.0, 0.0, 0.0]
            penalty = 0.0
            idx = 0
            last_key: Optional[str] = None
            for r in p.point_breakdown or []:
                if r.get("match_type") == "ELIMINATION":
                    pen = float(r.get("win_points") or 0.0)
                    penalty += pen
                    if not team_done and last_key is not None:
                        cell = team_elim.setdefault(last_key, [0.0, 0.0])
                        cell[0] += prob
                        cell[1] += prob * pen
                    continue
                if idx >= len(keys):
                    break
                key = keys[idx]
                idx += 1
                last_key = key
                comps = (float(r.get("rating_points") or 0), float(r.get("win_points") or 0),
                         float(r.get("role_points") or 0), float(r.get("booster_points") or 0))
                cell = per_key.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0.0])
                for i in range(4):
                    cell[i] += prob * comps[i]
                    seen[i] += comps[i]
                cell[4] += prob * sum(comps)
                _add_booster_weight(boost_keys.setdefault(key, {}), _booster_meta(r, p), prob)
                opp = int(r.get("opponent_team_id") or 0)
                oc = opp_pts.setdefault(key, {}).setdefault(opp, [0.0, 0.0, 0.0, 0.0])
                for i in range(4):
                    oc[i] += prob * comps[i]
                if pid not in acc["role"]:
                    acc["role"][pid] = (
                        r.get("role_id"), float(r.get("role_major_pct") or 0.0),
                        float(r.get("role_minor_pct") or 0.0), comps[2],
                    )
                if not team_done:
                    to = team_opp.setdefault(key, {}).setdefault(opp, [0.0, 0.0])
                    to[0] += prob
                    if r.get("did_win"):
                        to[1] += prob
            acc["penalty"][pid] = acc["penalty"].get(pid, 0.0) + prob * penalty
            seen[1] += penalty
            totals = (float(p.rating_points_total), float(p.win_points_total),
                      float(p.role_points_total), float(p.booster_points_total))
            pad_delta = [totals[i] - seen[i] for i in range(4)]
            pad = acc["padding"].setdefault(pid, [0.0, 0.0, 0.0, 0.0])
            for i in range(4):
                pad[i] += prob * pad_delta[i]
            if not team_done:
                if any(abs(d) > 1e-9 for d in pad_delta):
                    acc["pad_prob"][tid] = acc["pad_prob"].get(tid, 0.0) + prob
                team_done = True


def _real_game_sums(states: Dict[int, TeamState], tid: int) -> Dict[str, List[float]]:
    """Per player of `tid`: [rating, win, role, booster, games] summed over the
    matches the team actually played in this outcome (its per-match breakdown
    rows; eliminations and padding excluded). Feeds the playoff padding of a
    team that skips a playoff round: a missed match is padded with the
    player's average over the matches they did play."""
    ts = states.get(int(tid))
    out: Dict[str, List[float]] = {}
    if not ts:
        return out
    for pid, p in ts.players.items():
        sums = [0.0, 0.0, 0.0, 0.0, 0.0]
        for r in p.point_breakdown or []:
            if r.get("match_type") == "ELIMINATION":
                continue
            sums[0] += float(r.get("rating_points") or 0.0)
            sums[1] += float(r.get("win_points") or 0.0)
            sums[2] += float(r.get("role_points") or 0.0)
            sums[3] += float(r.get("booster_points") or 0.0)
            sums[4] += 1.0
        out[str(pid)] = sums
    return out


def _enumerate_group_outcomes(
    team_ids: List[int],
    group_index: int,
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    prob_cache: Dict,
    extra_rounds: int = 0,
    stage_acc: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """All 32 exact outcomes of one GSL group, with per-player fantasy points.

    extra_rounds = playoff rounds played after the groups (combined mode), so
    group-stage eliminations are penalized for every event round they miss,
    matching the -3-per-missed-match convention used everywhere else.
    """
    base_states = initialize_teams(team_ids, {tid: 999 for tid in team_ids})
    s1, s2, s3, s4 = team_ids
    outcomes: List[Dict[str, Any]] = []

    def play(states, a, b, winner, remaining_after):
        return _play_match_deterministic(
            states, a, b, winner, remaining_rounds_after=remaining_after, prob_cache=prob_cache,
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
        )

    def row(key, result, teams):
        w, l, p_win_a, _branch = result
        return {"key": key, "winner": w, "loser": l, "p_win_a": p_win_a, "teams": list(teams)}

    for w1 in (s1, s2):
        st1 = _clone_team_states(base_states)
        r1 = play(st1, s1, s2, w1, 0)
        m1 = row("opening_1", r1, [s1, s2])
        for w2 in (s3, s4):
            st2 = _clone_team_states(st1)
            r2 = play(st2, s3, s4, w2, 0)
            m2 = row("opening_2", r2, [s3, s4])
            wm_a, wm_b = m1["winner"], m2["winner"]
            em_a, em_b = m1["loser"], m2["loser"]
            for w3 in (wm_a, wm_b):
                st3 = _clone_team_states(st2)
                r3 = play(st3, wm_a, wm_b, w3, 0)
                m3 = row("winners", r3, [wm_a, wm_b])
                for w4 in (em_a, em_b):
                    st4 = _clone_team_states(st3)
                    # Elimination-match loser is out: misses the decider round
                    # plus any playoff rounds in combined mode.
                    r4 = play(st4, em_a, em_b, w4, 1 + extra_rounds)
                    m4 = row("elimination", r4, [em_a, em_b])
                    dm_a, dm_b = m3["loser"], m4["winner"]
                    for w5 in (dm_a, dm_b):
                        st5 = _clone_team_states(st4)
                        r5 = play(st5, dm_a, dm_b, w5, extra_rounds)
                        m5 = row("decider", r5, [dm_a, dm_b])
                        prob = r1[3] * r2[3] * r3[3] * r4[3] * r5[3]
                        # The winners'-match winner qualifies in 2 matches,
                        # skipping the decider — pad its players for that missing
                        # match so efficient qualifiers aren't under-scored,
                        # matching the Swiss stage's padding of early qualifiers.
                        wm_winner_state = st5.get(int(m3["winner"]))
                        if wm_winner_state:
                            missing = GROUP_MATCH_BASELINE - 2
                            for p in wm_winner_state.players.values():
                                pad = compute_padding_components(p)
                                p.rating_points_total += pad["rating"] * missing
                                p.role_points_total += pad["role"] * missing
                                p.win_points_total += pad["win"] * missing
                                p.booster_points_total += pad["booster"] * missing
                                p.total_points += (pad["rating"] + pad["role"] + pad["win"] + pad["booster"]) * missing
                        player_points: Dict[str, float] = {}
                        player_components: Dict[str, Dict[str, float]] = {}
                        player_breakdown: Dict[str, List[dict]] = {}
                        for ts in st5.values():
                            for pid, p in ts.players.items():
                                player_points[str(pid)] = float(p.total_points)
                                player_components[str(pid)] = {
                                    "total": float(p.total_points),
                                    "total_without_booster": float(
                                        p.rating_points_total + p.win_points_total + p.role_points_total
                                    ),
                                    "rating": float(p.rating_points_total),
                                    "win": float(p.win_points_total),
                                    "role": float(p.role_points_total),
                                    "booster": float(p.booster_points_total),
                                }
                                player_breakdown[str(pid)] = [dict(rowb) for rowb in p.point_breakdown]
                        if stage_acc is not None:
                            _accumulate_group_stage_stats(stage_acc, float(prob), st5, [m1, m2, m3, m4, m5])
                        outcomes.append(
                            {
                                "group": group_index,
                                "probability": float(prob),
                                "matches": [m1, m2, m3, m4, m5],
                                "qualified": [m3["winner"], m5["winner"]],
                                "eliminated": [m4["loser"], m5["loser"]],
                                "players": player_points,
                                "player_components": player_components,
                                "player_breakdown": player_breakdown,
                            }
                        )
    return outcomes


def _enumerate_group8_outcomes(
    team_ids: List[int],
    group_index: int,
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    prob_cache: Dict,
    extra_rounds: int = 0,
    stage_acc: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """All 1024 exact outcomes of one 8-team double-elimination group (top 4
    qualify). Opening winners meet in the upper semis (winner qualifies); their
    losers cross into the lower semis against the lower-round-1 winners; the two
    lower-semi winners take the other two spots. Qualified teams stop, so upper-
    semi winners (2 matches) are padded for the round they skip."""
    base_states = initialize_teams(team_ids, {tid: 999 for tid in team_ids})
    s = team_ids
    outcomes: List[Dict[str, Any]] = []

    def play(states, a, b, winner, remaining_after):
        return _play_match_deterministic(
            states, a, b, winner, remaining_rounds_after=remaining_after, prob_cache=prob_cache,
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
        )

    def rowd(key, result, teams):
        w, l, p_win_a, _b = result
        return {"key": key, "winner": w, "loser": l, "p_win_a": p_win_a, "teams": list(teams)}

    # Opening round: 4 matches from consecutive seed pairs. Losers drop, not out.
    for wo1 in (s[0], s[1]):
        sa = _clone_team_states(base_states); ro1 = play(sa, s[0], s[1], wo1, 0); mo1 = rowd("opening_1", ro1, [s[0], s[1]])
        for wo2 in (s[2], s[3]):
            sb = _clone_team_states(sa); ro2 = play(sb, s[2], s[3], wo2, 0); mo2 = rowd("opening_2", ro2, [s[2], s[3]])
            for wo3 in (s[4], s[5]):
                sc = _clone_team_states(sb); ro3 = play(sc, s[4], s[5], wo3, 0); mo3 = rowd("opening_3", ro3, [s[4], s[5]])
                for wo4 in (s[6], s[7]):
                    sd = _clone_team_states(sc); ro4 = play(sd, s[6], s[7], wo4, 0); mo4 = rowd("opening_4", ro4, [s[6], s[7]])
                    W1, L1 = mo1["winner"], mo1["loser"]
                    W2, L2 = mo2["winner"], mo2["loser"]
                    W3, L3 = mo3["winner"], mo3["loser"]
                    W4, L4 = mo4["winner"], mo4["loser"]
                    # Upper semis: winner qualifies, loser drops to lower semis.
                    for wu1 in (W1, W2):
                        se = _clone_team_states(sd); ru1 = play(se, W1, W2, wu1, 0); mu1 = rowd("upper_sf_1", ru1, [W1, W2])
                        for wu2 in (W3, W4):
                            sf = _clone_team_states(se); ru2 = play(sf, W3, W4, wu2, 0); mu2 = rowd("upper_sf_2", ru2, [W3, W4])
                            UW1, UL1 = mu1["winner"], mu1["loser"]
                            UW2, UL2 = mu2["winner"], mu2["loser"]
                            # Lower round 1: opening losers; loser eliminated (misses lower semis).
                            for wl1 in (L1, L2):
                                sg = _clone_team_states(sf); rl1 = play(sg, L1, L2, wl1, 1 + extra_rounds); ml1 = rowd("lower_r1_1", rl1, [L1, L2])
                                for wl2 in (L3, L4):
                                    sh = _clone_team_states(sg); rl2 = play(sh, L3, L4, wl2, 1 + extra_rounds); ml2 = rowd("lower_r1_2", rl2, [L3, L4])
                                    LW1, LW2 = ml1["winner"], ml2["winner"]
                                    # Lower semis, crossed: LW vs the OTHER upper-semi loser.
                                    for wls1 in (LW1, UL2):
                                        si = _clone_team_states(sh); rls1 = play(si, LW1, UL2, wls1, extra_rounds); mls1 = rowd("lower_sf_1", rls1, [LW1, UL2])
                                        for wls2 in (LW2, UL1):
                                            sj = _clone_team_states(si); rls2 = play(sj, LW2, UL1, wls2, extra_rounds); mls2 = rowd("lower_sf_2", rls2, [LW2, UL1])
                                            prob = (
                                                ro1[3] * ro2[3] * ro3[3] * ro4[3] * ru1[3] * ru2[3]
                                                * rl1[3] * rl2[3] * rls1[3] * rls2[3]
                                            )
                                            # Upper-semi winners qualify in 2 matches, skipping the
                                            # lower semis — pad for that 1 missing round.
                                            for uw in (UW1, UW2):
                                                wt = sj.get(int(uw))
                                                if not wt:
                                                    continue
                                                missing = GROUP_MATCH_BASELINE - 2
                                                for p in wt.players.values():
                                                    pad = compute_padding_components(p)
                                                    p.rating_points_total += pad["rating"] * missing
                                                    p.role_points_total += pad["role"] * missing
                                                    p.win_points_total += pad["win"] * missing
                                                    p.booster_points_total += pad["booster"] * missing
                                                    p.total_points += (pad["rating"] + pad["role"] + pad["win"] + pad["booster"]) * missing
                                            player_points: Dict[str, float] = {}
                                            player_components: Dict[str, Dict[str, float]] = {}
                                            for ts in sj.values():
                                                for pid, p in ts.players.items():
                                                    player_points[str(pid)] = float(p.total_points)
                                                    player_components[str(pid)] = {
                                                        "total": float(p.total_points),
                                                        "total_without_booster": float(
                                                            p.rating_points_total + p.win_points_total + p.role_points_total
                                                        ),
                                                        "rating": float(p.rating_points_total),
                                                        "win": float(p.win_points_total),
                                                        "role": float(p.role_points_total),
                                                        "booster": float(p.booster_points_total),
                                                    }
                                            if stage_acc is not None:
                                                _accumulate_group_stage_stats(stage_acc, float(prob), sj, [mo1, mo2, mo3, mo4, mu1, mu2, ml1, ml2, mls1, mls2])
                                            outcomes.append(
                                                {
                                                    "group": group_index,
                                                    "probability": float(prob),
                                                    "matches": [mo1, mo2, mo3, mo4, mu1, mu2, ml1, ml2, mls1, mls2],
                                                    "qualified": [UW1, UW2, mls1["winner"], mls2["winner"]],
                                                    "eliminated": [ml1["loser"], ml2["loser"], mls1["loser"], mls2["loser"]],
                                                    "players": player_points,
                                                    "player_components": player_components,
                                                    # breakdown omitted: 1024 outcomes/group would bloat the blob.
                                                    "player_breakdown": {},
                                                }
                                            )
    return outcomes


def _enumerate_group8_top3_outcomes(
    team_ids: List[int],
    group_index: int,
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    prob_cache: Dict,
    extra_rounds: int = 0,
    stage_acc: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """All 4096 exact outcomes of the Porto/Cologne 8-team double-elim group
    (top 3 qualify). Unlike the EWC variant the upper bracket plays its final:
    the winner tops the group (straight to the playoff semis), the loser still
    qualifies; the lower bracket runs to a lower final whose winner takes the
    last spot and whose loser goes home. qualified = [1st, 2nd, 3rd]."""
    base_states = initialize_teams(team_ids, {tid: 999 for tid in team_ids})
    s = team_ids
    outcomes: List[Dict[str, Any]] = []

    def play(states, a, b, winner, remaining_after):
        return _play_match_deterministic(
            states, a, b, winner, remaining_rounds_after=remaining_after, prob_cache=prob_cache,
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
        )

    def rowd(key, result, teams):
        w, l, p_win_a, _b = result
        return {"key": key, "winner": w, "loser": l, "p_win_a": p_win_a, "teams": list(teams)}

    # Opening round: 4 matches from consecutive seed pairs. Losers drop, not out.
    for wo1 in (s[0], s[1]):
        sa = _clone_team_states(base_states); ro1 = play(sa, s[0], s[1], wo1, 0); mo1 = rowd("opening_1", ro1, [s[0], s[1]])
        for wo2 in (s[2], s[3]):
            sb = _clone_team_states(sa); ro2 = play(sb, s[2], s[3], wo2, 0); mo2 = rowd("opening_2", ro2, [s[2], s[3]])
            for wo3 in (s[4], s[5]):
                sc = _clone_team_states(sb); ro3 = play(sc, s[4], s[5], wo3, 0); mo3 = rowd("opening_3", ro3, [s[4], s[5]])
                for wo4 in (s[6], s[7]):
                    sd = _clone_team_states(sc); ro4 = play(sd, s[6], s[7], wo4, 0); mo4 = rowd("opening_4", ro4, [s[6], s[7]])
                    W1, L1 = mo1["winner"], mo1["loser"]
                    W2, L2 = mo2["winner"], mo2["loser"]
                    W3, L3 = mo3["winner"], mo3["loser"]
                    W4, L4 = mo4["winner"], mo4["loser"]
                    # Upper semis: winners meet in the upper final; losers drop.
                    for wu1 in (W1, W2):
                        se = _clone_team_states(sd); ru1 = play(se, W1, W2, wu1, 0); mu1 = rowd("upper_sf_1", ru1, [W1, W2])
                        for wu2 in (W3, W4):
                            sf = _clone_team_states(se); ru2 = play(sf, W3, W4, wu2, 0); mu2 = rowd("upper_sf_2", ru2, [W3, W4])
                            UW1, UL1 = mu1["winner"], mu1["loser"]
                            UW2, UL2 = mu2["winner"], mu2["loser"]
                            # The upper-final pair's padding derives from their
                            # players' FIRST TWO games only — the upper final
                            # itself doesn't feed the pad value. Snapshot their
                            # totals now, before the final is played.
                            pad_snapshot: Dict[int, Dict[int, Dict[str, float]]] = {}
                            for pad_tid in (UW1, UW2):
                                pad_ts = sf.get(int(pad_tid))
                                if not pad_ts:
                                    continue
                                pad_snapshot[int(pad_tid)] = {
                                    pid: {
                                        "rating": float(p.rating_points_total),
                                        "win": float(p.win_points_total),
                                        "role": float(p.role_points_total),
                                    }
                                    for pid, p in pad_ts.players.items()
                                }
                            # Upper final: winner 1st (playoff semis), loser 2nd.
                            for wf in (UW1, UW2):
                                sg0 = _clone_team_states(sf); rf = play(sg0, UW1, UW2, wf, 0); mf = rowd("upper_final", rf, [UW1, UW2])
                                first, second = mf["winner"], mf["loser"]
                                # Lower round 1: opening losers; loser eliminated
                                # (misses lower semis + lower final).
                                for wl1 in (L1, L2):
                                    sg = _clone_team_states(sg0); rl1 = play(sg, L1, L2, wl1, 2 + extra_rounds); ml1 = rowd("lower_r1_1", rl1, [L1, L2])
                                    for wl2 in (L3, L4):
                                        sh = _clone_team_states(sg); rl2 = play(sh, L3, L4, wl2, 2 + extra_rounds); ml2 = rowd("lower_r1_2", rl2, [L3, L4])
                                        LW1, LW2 = ml1["winner"], ml2["winner"]
                                        # Lower semis, crossed: loser eliminated (misses lower final).
                                        for wls1 in (LW1, UL2):
                                            si = _clone_team_states(sh); rls1 = play(si, LW1, UL2, wls1, 1 + extra_rounds); mls1 = rowd("lower_sf_1", rls1, [LW1, UL2])
                                            for wls2 in (LW2, UL1):
                                                sj = _clone_team_states(si); rls2 = play(sj, LW2, UL1, wls2, 1 + extra_rounds); mls2 = rowd("lower_sf_2", rls2, [LW2, UL1])
                                                LF1, LF2 = mls1["winner"], mls2["winner"]
                                                # Lower final: winner takes 3rd, loser is out.
                                                for wlf in (LF1, LF2):
                                                    sk = _clone_team_states(sj); rlf = play(sk, LF1, LF2, wlf, extra_rounds); mlf = rowd("lower_final", rlf, [LF1, LF2])
                                                    third = mlf["winner"]
                                                    prob = (
                                                        ro1[3] * ro2[3] * ro3[3] * ro4[3] * ru1[3] * ru2[3]
                                                        * rf[3] * rl1[3] * rl2[3] * rls1[3] * rls2[3] * rlf[3]
                                                    )
                                                    # Upper-final teams finish in 3 matches; the
                                                    # lower-final pair plays 4 — pad the missing round
                                                    # with each player's per-game average over their
                                                    # first two games (no booster in padding).
                                                    for uw in (first, second):
                                                        wt = sk.get(int(uw))
                                                        snap = pad_snapshot.get(int(uw)) or {}
                                                        if not wt:
                                                            continue
                                                        missing = 4 - 3
                                                        for pid, p in wt.players.items():
                                                            base = snap.get(pid)
                                                            if base is None:
                                                                continue
                                                            pad_rating = base["rating"] / 2.0
                                                            pad_win = base["win"] / 2.0
                                                            pad_role = base["role"] / 2.0
                                                            p.rating_points_total += pad_rating * missing
                                                            p.win_points_total += pad_win * missing
                                                            p.role_points_total += pad_role * missing
                                                            p.total_points += (pad_rating + pad_win + pad_role) * missing
                                                    player_points: Dict[str, float] = {}
                                                    player_components: Dict[str, Dict[str, float]] = {}
                                                    for ts in sk.values():
                                                        for pid, p in ts.players.items():
                                                            player_points[str(pid)] = float(p.total_points)
                                                            player_components[str(pid)] = {
                                                                "total": float(p.total_points),
                                                                "total_without_booster": float(
                                                                    p.rating_points_total + p.win_points_total + p.role_points_total
                                                                ),
                                                                "rating": float(p.rating_points_total),
                                                                "win": float(p.win_points_total),
                                                                "role": float(p.role_points_total),
                                                                "booster": float(p.booster_points_total),
                                                            }
                                                    if stage_acc is not None:
                                                        _accumulate_group_stage_stats(stage_acc, float(prob), sk, [mo1, mo2, mo3, mo4, mu1, mu2, mf, ml1, ml2, mls1, mls2, mlf])
                                                    outcomes.append(
                                                        {
                                                            "group": group_index,
                                                            "probability": float(prob),
                                                            "matches": [mo1, mo2, mo3, mo4, mu1, mu2, mf, ml1, ml2, mls1, mls2, mlf],
                                                            # Ordered: 1st (playoff-semi seed), 2nd, 3rd.
                                                            "qualified": [first, second, third],
                                                            "eliminated": [ml1["loser"], ml2["loser"], mls1["loser"], mls2["loser"], mlf["loser"]],
                                                            "players": player_points,
                                                            "player_components": player_components,
                                                            "player_breakdown": {},
                                                            # the winner skips the playoff quarter-final: its
                                                            # players' sums over the group matches they played
                                                            # feed that match's padding (see _exact_combined_playoffs)
                                                            "first_games": _real_game_sums(sk, first),
                                                        }
                                                    )
    return outcomes


_PLAYOFF_BYE_PADDING_POINTS = 6.0

# Combined groups→playoff shapes whose bracket structure (how qualifiers are
# seeded into the playoff) has been verified against real HLTV event pages.
# Anything else is REJECTED and flagged for development rather than run under
# a guessed seeding: a bracket seeded the wrong way yields confident, wrong
# valuations. Key = (group_format, group_count).
_SUPPORTED_COMBINED_SHAPES: Dict[tuple, str] = {
    ("de8_top3", 2): "2 groups of 8, top 3 → 6-team bracket, group winners bye to the semis "
                     "(verified on FISSURE Playground 3 and BLAST Open Porto 2026)",
}


def combined_shape_key(group_format: str, group_count: int) -> tuple:
    return (str(group_format or "").strip().lower(), int(group_count or 0))


def combined_shape_label(group_format: str, group_count: int) -> str:
    fmt, count = combined_shape_key(group_format, group_count)
    quals = {"gsl4": 2, "de8": 4, "de8_top3": 3}.get(fmt, 0)
    return f"{count} × {fmt} groups (top {quals} each, {quals * count} qualifiers) → combined playoff"


def combined_shape_supported(group_format: str, group_count: int) -> bool:
    return combined_shape_key(group_format, group_count) in _SUPPORTED_COMBINED_SHAPES


def reject_unsupported_combined_shape(group_format: str, group_count: int, event_id: int | None = None) -> str:
    """Flag the shape for development and return the user-facing reason."""
    from backend.services import dev_flags

    fmt, count = combined_shape_key(group_format, group_count)
    label = combined_shape_label(fmt, count)
    dev_flags.flag(
        key=f"combined:{fmt}x{count}",
        kind="combined_playoff_shape",
        detail=f"{label}: playoff seeding for this shape has not been verified against an HLTV bracket, so the "
               "simulator refuses it. To support it, confirm the bracket on the event page and add the shape to "
               "_SUPPORTED_COMBINED_SHAPES.",
        event_id=event_id,
    )
    return (
        f"Combined playoffs are not supported yet for {label}. The shape has been flagged for development; "
        "run the group stage without combined playoffs meanwhile."
    )


# Group matches a qualifier has played by the time it enters the playoff, by
# qualifier rank (the enumerators' `qualified` order). One fantasy game covers
# groups and playoffs, so the playoff match numbers — and with them the booster
# slots, each booster being usable once per player — continue from here.
_GROUP_MATCHES_BY_RANK: Dict[str, List[int]] = {
    "gsl4": [2, 3],           # winners' match winner; decider winner
    "de8": [2, 2, 3, 3],      # upper-semi winners; lower-semi winners
    "de8_top3": [3, 3, 4],    # upper-final winner; upper-final loser; lower-final winner
}


def _fresh_bracket_state(base_states: Dict[int, TeamState], tid: int, prior_matches: int) -> TeamState:
    """A team state as it stands when it plays its (prior_matches + 1)-th
    playoff match: zero points, and — alive in a single-elimination bracket —
    every earlier match was a win. That is all the scorer reads (the match
    number picks the booster slot), so this pins the per-match points down."""
    ts = _clone_team_states({tid: base_states[tid]})[tid]
    ts.wins = int(prior_matches)
    ts.losses = 0
    return ts


def _combined_bracket_template(x: int, quals_per_group: int, rounds_total: int, group_format: str) -> Dict[str, Any]:
    """Bracket template shared by the exact valuation and the joint sampler:
    rounds of (left, right) feeders — ("seed", group, rank) or ("win", round,
    match) — the round each seed enters (byes enter later) and each seed's
    group matches already played (its playoff matches continue the booster
    slot numbering)."""
    byes_to_semis = quals_per_group == 3
    if byes_to_semis:
        rounds = [
            [(("seed", 0, 1), ("seed", 1, 2)), (("seed", 1, 1), ("seed", 0, 2))],  # QF1: A2 v B3, QF2: B2 v A3
            [(("seed", 0, 0), ("win", 0, 1)), (("seed", 1, 0), ("win", 0, 0))],    # SF1: A1 v QF2 W, SF2: B1 v QF1 W
            [(("win", 1, 0), ("win", 1, 1))],
        ]
        entry_round = {(0, 0): 1, (1, 0): 1}
    else:
        seeded = [(g, r) for r in range(quals_per_group) for g in range(x)]  # seed-major, like the old sim
        n = len(seeded)
        rounds = [[(("seed",) + seeded[i], ("seed",) + seeded[n - 1 - i]) for i in range(n // 2)]]
        for r in range(1, rounds_total):
            prev = len(rounds[-1])
            rounds.append([(("win", r - 1, 2 * m), ("win", r - 1, 2 * m + 1)) for m in range(prev // 2)])
        entry_round = {}
    matches_by_rank = _GROUP_MATCHES_BY_RANK.get(group_format) or []
    prior_matches = {
        (g, rank): (matches_by_rank[rank] if rank < len(matches_by_rank) else 0)
        for g in range(x)
        for rank in range(quals_per_group)
    }
    return {"rounds": rounds, "entry_round": entry_round, "prior_matches": prior_matches, "byes_to_semis": byes_to_semis}


def _exact_combined_playoffs(
    groups: List[List[int]],
    outcomes: List[Dict[str, Any]],
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    prob_cache: Dict,
    stop_teams: int = 1,
    quals_per_group: int = 2,
    progress_callback=None,
    workers: int | None = None,
    group_format: str = "de8_top3",
) -> Dict[str, Any]:
    """Exact expected playoff points for every player.

    The exact group outcomes are marginalised onto ordered qualifier tuples
    per group, then services/bracket_exact contracts the bracket: each
    sub-bracket is a winner table over the teams in its slots, each match's
    participant distribution is the contraction of its feeders with the group
    tuple distributions restricted to the slots involved, and the final is
    folded group by group — no seedings are enumerated, and a match that
    depends on a few slots is computed once. Points are linear in the matches
    played and a match's points depend only on (team, opponent, winner, each
    side's match number, rounds remaining), so the pairing weights are
    multiplied through a memoised per-pairing points table at the end.

    Bracket shapes mirror the previous simulator exactly: quals_per_group == 3
    is the 6-team byes bracket (QF1 = A2 v B3, QF2 = B2 v A3, SF1 = A1 v QF2
    winner, SF2 = B1 v QF1 winner, bye teams get full padding for the
    quarter-final they skip: +6 win plus their average rating and role over the
    matches they actually play, no booster); otherwise qualifiers are flattened seed-major
    and paired i vs N-1-i, with stop_teams ending the bracket early (which
    splits it into independent sub-brackets).
    """
    from backend.services import bracket_exact

    x = len(groups)
    stop_teams = max(1, int(stop_teams))
    byes_to_semis = quals_per_group == 3
    if byes_to_semis:
        if x != 2:
            raise ValueError("The top-3 combined playoff supports exactly 2 groups (6-team bracket)")
        if stop_teams != 1:
            raise ValueError("The top-3 combined playoff plays the full bracket (stop_teams must be 1)")
        bracket_size = 6
        rounds_total = 3
    else:
        bracket_size = quals_per_group * x
        rounds_total = max(1, int(math.log2(bracket_size)) - int(math.log2(stop_teams)))

    # 1. Ordered-qualifier tuple distribution per group (probabilities sum to 1).
    tuple_probs: List[Dict[tuple, float]] = [{} for _ in range(x)]
    for outcome in outcomes:
        g = int(outcome["group"])
        key = tuple(int(t) for t in outcome["qualified"])
        tuple_probs[g][key] = tuple_probs[g].get(key, 0.0) + float(outcome["probability"])
    specs = [bracket_exact.GroupSpec(groups[g], quals_per_group, tuple_probs[g]) for g in range(x)]

    # 2. Bracket template (feeders are ("seed", group, rank) or ("win", round, match)).
    template = _combined_bracket_template(x, quals_per_group, rounds_total, group_format)
    rounds, entry_round, prior_matches = template["rounds"], template["entry_round"], template["prior_matches"]
    roots = bracket_exact.build_tree(rounds, entry_round, prior_matches)

    # 3. Win-probability matrices between groups, from the shared cache
    #    (canonical direction, see playoff.cached_win_prob).
    def match_prob(a: int, b: int) -> float:
        return cached_win_prob(prob_cache, a, b)  # a == b only ever meets a zero-probability context

    pwin_cache: Dict[tuple, Any] = {}

    def pwin(g: int, h: int):
        key = (g, h)
        if key not in pwin_cache:
            import numpy as np

            m = np.array([[match_prob(a, b) for b in specs[h].teams] for a in specs[g].teams], dtype=np.float64)
            pwin_cache[key] = m
        return pwin_cache[key]

    weights, advance, bye_weight = bracket_exact.compute_pairing_weights(specs, roots, rounds_total, pwin)

    # 4. Per-pairing points (memoised: only a few hundred distinct pairings).
    all_team_ids = [tid for group in groups for tid in group]
    base_states = initialize_teams(all_team_ids, {tid: 999 for tid in all_team_ids})

    def pairing_points(key: tuple) -> Dict[int, tuple]:
        a, b, winner, num_a, num_b, rem = key
        states = {
            a: _fresh_bracket_state(base_states, a, num_a - 1),
            b: _fresh_bracket_state(base_states, b, num_b - 1),
        }
        _play_match_deterministic(
            states, a, b, winner, remaining_rounds_after=rem, prob_cache=prob_cache,
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
        )
        out: Dict[int, tuple] = {}
        for ts in states.values():
            for pid, p in ts.players.items():
                # 6th component: the elimination penalty inside the win total,
                # so the per-round view can show it separately.
                penalty = sum(
                    float(r.get("win_points") or 0.0)
                    for r in (p.point_breakdown or [])
                    if r.get("match_type") == "ELIMINATION"
                )
                match_row = next((r for r in (p.point_breakdown or []) if r.get("match_type") != "ELIMINATION"), None)
                out[int(pid)] = (
                    float(p.total_points), float(p.rating_points_total), float(p.win_points_total),
                    float(p.role_points_total), float(p.booster_points_total), penalty,
                    _booster_meta(match_row, p) if match_row else None,
                )
        return out

    accum: Dict[int, List[float]] = {}
    stage_ev: Dict[int, Dict[int, List[float]]] = {}
    stage_boost: Dict[int, Dict[int, Dict[int, list]]] = {}
    player_penalty: Dict[int, float] = {}
    round_wins: Dict[int, List[float]] = {}
    round_elim: Dict[int, Dict[int, List[float]]] = {}  # team -> round -> [P(out here), expected penalty]
    round_opp: Dict[int, Dict[int, Dict[int, List[float]]]] = {}  # team -> round -> opponent -> [P(play), P(play and win)]
    round_opp_pts: Dict[int, Dict[int, Dict[int, List[float]]]] = {}  # player -> round -> opponent -> [rating, win, role, booster]
    pid_team = {int(pid): tid for tid in all_team_ids for pid in base_states[tid].players}
    # Bye path (6-team bracket only): a team that skips the quarter-final plays
    # the semi as its (prior0 + 1)th match and the final as its (prior0 + 2)th,
    # numbers no quarter-final route reaches, so its pairings are identifiable.
    bye_num: Dict[int, Dict[int, int]] = {}
    group_of: Dict[int, int] = {}
    if byes_to_semis:
        for g in range(x):
            for tid in groups[g]:
                group_of[int(tid)] = g
                bye_num[int(tid)] = {r: prior_matches[(g, 0)] + r for r in range(1, rounds_total)}
    match_reach: Dict[int, Dict[int, float]] = {}  # team -> match number -> P(plays that match in the playoffs)
    pairing_table: Dict[str, Dict[str, List[float]]] = {}  # "a,b,winner,num_a,num_b,rem" -> pid -> [total, rating, win, role]
    bye_sf: Dict[int, List[float]] = {}  # team -> [P(lose the semi), P(win the semi)] on the bye path
    bye_pts: Dict[int, Dict[int, List[List[float]]]] = {}  # team -> pid -> [[semi-loss path], [final path]] component sums
    for key, w in weights.items():
        a, b, winner, na, nb, rem = key
        r = rounds_total - rem - 1
        rw = round_wins.setdefault(winner, [0.0] * rounds_total)
        rw[r] += w
        for tid, num in ((a, na), (b, nb)):
            mr = match_reach.setdefault(tid, {})
            mr[num] = mr.get(num, 0.0) + w
        bye_sides = []
        if byes_to_semis and r >= 1:
            bye_sides = [tid for tid, num in ((a, na), (b, nb)) if bye_num.get(tid, {}).get(r) == num]
            if r == 1:
                for tid in bye_sides:
                    cell = bye_sf.setdefault(tid, [0.0, 0.0])
                    cell[1 if winner == tid else 0] += w
        loser = b if winner == a else a
        for tid, opp in ((a, b), (b, a)):
            oc = round_opp.setdefault(tid, {}).setdefault(r, {}).setdefault(opp, [0.0, 0.0])
            oc[0] += w
            if winner == tid:
                oc[1] += w
        elim_recorded = False
        pairing = pairing_points(key)
        pairing_table[",".join(str(v) for v in key)] = {
            str(pid): [float(c[0]), float(c[1]), float(c[2]), float(c[3])] for pid, c in pairing.items()  # total, rating, win, role
        }
        for pid, comps in pairing.items():
            bucket = accum.setdefault(pid, [0.0, 0.0, 0.0, 0.0, 0.0])
            for i in range(5):
                bucket[i] += w * comps[i]
            penalty = comps[5]
            if bye_sides and pid_team.get(pid) in bye_sides:
                # a semi loss ends the run (4 matches played); a semi win or the final is the 5-match path
                path = 1 if (r > 1 or winner == pid_team.get(pid)) else 0
                dest = bye_pts.setdefault(pid_team[pid], {}).setdefault(pid, [[0.0] * 4, [0.0] * 4])[path]
                dest[0] += w * comps[1]
                dest[1] += w * (comps[2] - penalty)
                dest[2] += w * comps[3]
                dest[3] += w * comps[4]
            opp = b if pid_team.get(pid) == a else a
            oc = round_opp_pts.setdefault(pid, {}).setdefault(r, {}).setdefault(opp, [0.0, 0.0, 0.0, 0.0])
            oc[0] += w * comps[1]
            oc[1] += w * (comps[2] - penalty)  # match win points only; the penalty is reported per round
            oc[2] += w * comps[3]
            oc[3] += w * comps[4]
            if penalty and not elim_recorded and pid_team.get(pid) == loser:
                ecell = round_elim.setdefault(loser, {}).setdefault(r, [0.0, 0.0])
                ecell[0] += w
                ecell[1] += w * penalty
                elim_recorded = True
            cell = stage_ev.setdefault(pid, {}).setdefault(r, [0.0, 0.0, 0.0, 0.0, 0.0])
            cell[0] += w * comps[1]
            cell[1] += w * (comps[2] - penalty)
            cell[2] += w * comps[3]
            cell[3] += w * comps[4]
            cell[4] += w * (comps[0] - penalty)
            player_penalty[pid] = player_penalty.get(pid, 0.0) + w * penalty
            _add_booster_weight(stage_boost.setdefault(pid, {}).setdefault(r, {}), comps[6], w)
    # Playoff padding for the quarter-final a bye skips: +6 win, and for rating
    # and role the player's average over the matches actually played in the
    # event (group matches + semi, + final when reached) — no booster, as with
    # the group padding — i.e.
    # E[(group sum + playoff sum) / matches played] along the bye path. The group
    # sum is independent of the playoff path given the team took the bye, so the
    # conditional group sums and the per-path playoff sums combine linearly.
    group_sums: Dict[int, Dict[int, List[float]]] = {}
    rank0_prob: Dict[int, float] = {}
    if byes_to_semis:
        for outcome in outcomes:
            first = int(outcome["qualified"][0])
            p_o = float(outcome["probability"])
            rank0_prob[first] = rank0_prob.get(first, 0.0) + p_o
            for pid_s, sums in (outcome.get("first_games") or {}).items():
                cell = group_sums.setdefault(first, {}).setdefault(int(pid_s), [0.0] * 5)
                for i in range(5):
                    cell[i] += p_o * float(sums[i])
    player_playoff_pad: Dict[int, List[float]] = {}
    for tid, w_bye in bye_weight.items():
        if w_bye <= 0.0:
            continue
        p1 = rank0_prob.get(tid, 0.0)
        p_lose, p_win = bye_sf.get(tid, [0.0, 0.0])
        for pid in base_states[tid].players:
            pid = int(pid)
            gs = group_sums.get(tid, {}).get(pid) or [0.0] * 5
            g_cond = [(gs[i] / p1) if p1 > 0 else 0.0 for i in range(5)]  # E[group sums, games | took the bye]
            n_group = g_cond[4] if g_cond[4] > 0 else float(prior_matches.get((group_of.get(tid, 0), 0), 0))
            nofinal, final = bye_pts.get(tid, {}).get(pid) or ([0.0] * 4, [0.0] * 4)
            pad = [0.0, _PLAYOFF_BYE_PADDING_POINTS * w_bye, 0.0, 0.0]
            for i in (0, 2):  # rating, role; booster is never padded
                pad[i] = (
                    g_cond[i] * (p_lose / (n_group + 1.0) + p_win / (n_group + 2.0))
                    + nofinal[i] / (n_group + 1.0)
                    + final[i] / (n_group + 2.0)
                )
            bucket = accum.setdefault(pid, [0.0, 0.0, 0.0, 0.0, 0.0])
            bucket[0] += sum(pad)
            for i in range(4):
                bucket[i + 1] += pad[i]
            player_playoff_pad[pid] = pad
    if progress_callback:
        progress_callback(1, 1)

    player_ev = {
        pid: {"total": b[0], "rating": b[1], "win": b[2], "role": b[3], "booster": b[4]}
        for pid, b in accum.items()
    }
    # P(team plays a match in round r) — both results of a pairing sum to the
    # pairing's probability. Bye teams enter at the semis, so their QF reach
    # is 0 while their SF reach includes the bye.
    round_reach: Dict[int, List[float]] = {}
    for (a, b, _winner, _na, _nb, rem), w in weights.items():
        r = rounds_total - rem - 1
        for tid in (a, b):
            row = round_reach.setdefault(tid, [0.0] * rounds_total)
            row[r] += w  # weights are P(match with that result); summing both winners gives P(match)
    if byes_to_semis:
        round_labels = ["QF", "SF", "Final"]
    else:
        round_labels = []
        for r in range(rounds_total):
            teams_in_round = bracket_size >> r
            round_labels.append({2: "Final", 4: "SF", 8: "QF", 16: "R16", 32: "R32"}.get(teams_in_round, f"R{teams_in_round}"))
    return {
        "method": "exact_contraction",
        "pairings": len(weights),
        "bracket_size": bracket_size,
        "rounds": rounds_total,
        "round_labels": round_labels,
        "round_reach": {str(tid): row for tid, row in round_reach.items()},
        "round_wins": {str(tid): row for tid, row in round_wins.items()},
        # P(team plays its Nth match) in the playoffs, by match number (its
        # group matches count first) — the booster slots of the roster solve.
        "match_reach": {str(tid): {str(n): p for n, p in d.items()} for tid, d in match_reach.items()},
        # For the joint outcome sampler (ceiling / most-likely-winner over the
        # whole event): every reachable pairing's per-player points and the
        # win-probability matrix between all teams.
        "pairing_table": pairing_table,
        "win_probs": {str(a): {str(b): match_prob(a, b) for b in all_team_ids if b != a} for a in all_team_ids},
        "stop_teams": stop_teams,
        "player_ev": player_ev,
        # Per-round expected points by component (penalty excluded), the
        # elimination penalty and the playoff padding per player, for the modal.
        "player_stage_ev": {
            str(pid): {
                str(r): {"rating": c[0], "win": c[1], "role": c[2], "booster": c[3], "total": c[4]}
                for r, c in rounds_ev.items()
            }
            for pid, rounds_ev in stage_ev.items()
        },
        "player_penalty": {str(pid): v for pid, v in player_penalty.items()},
        "player_playoff_padding": {
            str(pid): {"rating": v[0], "win": v[1], "role": v[2], "booster": v[3], "total": sum(v)}
            for pid, v in player_playoff_pad.items()
        },
        "round_elim": {
            str(tid): {str(r): {"prob": c[0], "points": c[1]} for r, c in rounds.items()}
            for tid, rounds in round_elim.items()
        },
        "bye_prob": {str(tid): float(w) for tid, w in bye_weight.items()},
        "round_opp": {
            str(tid): {str(r): {str(o): {"play": c[0], "win": c[1]} for o, c in opps.items()} for r, opps in rounds.items()}
            for tid, rounds in round_opp.items()
        },
        "player_round_opp_pts": {
            str(pid): {
                str(r): {
                    str(o): {"rating": v[0], "win": v[1], "role": v[2], "booster": v[3], "total": sum(v)}
                    for o, v in opps.items()
                }
                for r, opps in rounds.items()
            }
            for pid, rounds in round_opp_pts.items()
        },
        "player_stage_boost": {
            str(pid): {str(r): _booster_list(bucket) for r, bucket in rounds.items()}
            for pid, rounds in stage_boost.items()
        },
        # P(team wins the last played round) — with stop_teams > 1 this is the
        # chance of qualifying onward rather than winning the whole bracket.
        "advance_rate": {str(tid): v for tid, v in sorted(advance.items(), key=lambda kv: -kv[1])},
    }



# Per-event snapshot of the valuation inputs (player rows, team ranks): taken
# at every bake until the event starts, frozen from then on — see
# `_event_inputs`. Keyed by fantasy event id.
_INPUT_SNAPSHOT = SingletonState("event_input_snapshot_state", keyed=True)


def event_start_at(event_id: int) -> Optional[float]:
    """Epoch seconds the event starts (from its archived HLTV page), or None."""
    from backend.data.event_db import get_event_detail
    from backend.routes import events as events_routes

    event = get_event_detail(int(event_id))
    if not event:
        return None
    try:
        detected = events_routes._detect_event_tournament_kind_cached(event)
    except Exception:  # noqa: BLE001
        return None
    start = detected.get("start_at")
    return float(start) if start else None


def event_has_started(event_id: int) -> Optional[bool]:
    """True/False from the event's start stamp; None when the start is unknown."""
    start = event_start_at(event_id)
    if start is None:
        return None
    return time.time() >= start


def _take_input_snapshot(event_id: int, team_ids: List[int]) -> Dict[str, Any]:
    """The valuation's inputs as of now: full player rows (rating, Top-X
    ratings, roles, boosters) for every roster player of the teams, priced at
    THIS event's fantasy prices (the players table's price column only holds
    whichever event was imported last), and each team's HLTV rank."""
    from backend.data.event_db import get_event_price

    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context([int(t) for t in team_ids])
    players: Dict[str, Dict[str, Any]] = {}
    for pid, row in player_rows_by_id.items():
        out = dict(row)
        try:
            price = get_event_price(int(pid), int(event_id))
        except Exception:  # noqa: BLE001
            price = None
        if price is not None:
            out["price"] = int(price)
        players[str(pid)] = out
    return {
        "players": players,
        "team_ranks": {str(tid): int(rank) for tid, rank in team_rank_by_id.items()},
    }


def _event_inputs(event_id: int, team_ids: List[int], refresh: bool = False) -> tuple:
    """(snapshot, meta) for a bake. Before the event starts (or when the start
    is unknown) a fresh snapshot is taken and stored; once it has started the
    stored snapshot is reused unchanged — so re-bakes after a nightly rating
    import cannot move a live event's values — unless `refresh` forces new
    inputs. meta = {taken_at, frozen, started, start_at}."""
    start = event_start_at(event_id)
    started = bool(start) and time.time() >= float(start)
    _INPUT_SNAPSHOT.ensure_table()  # idempotent; the table is new
    existing = _INPUT_SNAPSHOT.load(key=int(event_id))
    if existing and started and not refresh:
        meta = dict((existing.get("payload") or {}))
        meta.update({"frozen": True, "started": True, "start_at": start})
        return existing["result"], meta
    snapshot = _take_input_snapshot(event_id, team_ids)
    meta = {"event_id": int(event_id), "taken_at": time.time(), "frozen": started, "started": started, "start_at": start}
    _INPUT_SNAPSHOT.save(meta, snapshot, key=int(event_id))
    return snapshot, meta


def _lookup_context_from_snapshot(snapshot: Dict[str, Any], team_ids: List[int]) -> tuple:
    """(player_rows_by_id, team_rank_by_id) in _build_playoff_lookup_context's
    shape, from a stored snapshot (teams missing from it fall back to rank 100)."""
    player_rows_by_id = {int(pid): dict(row) for pid, row in (snapshot.get("players") or {}).items()}
    ranks = {int(t): int(r) for t, r in (snapshot.get("team_ranks") or {}).items()}
    team_rank_by_id = {int(t): ranks.get(int(t), 100) for t in team_ids}
    return player_rows_by_id, team_rank_by_id


def _snapshot_player_row(results: dict, pid: int) -> Optional[dict]:
    """The player row a stored run was valued with (its input snapshot), else
    the live players table — so the live Top 5 prices, roles and boosters
    match the stored expected points."""
    row = ((results.get("input_snapshot") or {}).get("players") or {}).get(str(int(pid)))
    if row:
        return row
    return get_player(int(pid))


def bake_event_valuations(
    event_id: int, trigger: str = "import", only_if_missing: bool = False, refresh_inputs: bool = False
) -> Dict[str, Any]:
    """Run and store the groups valuation for one fantasy event off the request
    path — once, right after the event is imported — so opening the Tournament
    tab never computes anything. Uses the detected format, the detected
    combined-playoff flag and the HLTV draw (re-fetched live once when the
    stored draw still has TBD slots). With only_if_missing the nightly run
    only fills in an event that could not be baked at import (draw not
    published yet); an existing valuation is never refreshed. Returns a short
    status dict; never raises — the scheduler records the message."""
    from backend.data.event_db import get_event_detail, get_event_groups_autofill, get_event_tournament_kind, set_event_groups_autofill
    from backend.routes import events as events_routes

    started = time.time()
    event_id = int(event_id)
    if only_if_missing and _GROUPS_STATE.load(key=event_id):
        return {"status": "exists", "event_id": event_id, "reason": "already baked at import; not refreshed"}
    event = get_event_detail(event_id)
    if not event:
        return {"status": "skipped", "event_id": event_id, "reason": "event not found"}
    try:
        detected = events_routes._detect_event_tournament_kind_cached(event)
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "event_id": event_id, "reason": f"kind detection failed: {exc}"}
    kind = get_event_tournament_kind(event_id) or detected.get("kind")
    if kind != "groups":
        return {"status": "skipped", "event_id": event_id, "reason": f"not a groups event ({kind})"}

    def _draw_ids(stored) -> List[List[int]]:
        return [[int(t or 0) for t in (g.get("team_ids") or [])] for g in ((stored or {}).get("groups") or [])]

    stored = get_event_groups_autofill(event_id)
    groups_ids = _draw_ids(stored)
    if not groups_ids or any(t <= 0 for g in groups_ids for t in g):
        try:
            fresh = autofill_event_groups(hltv_event_id=event.get("hltv_event_id"), fantasy_event_id=event_id)
            set_event_groups_autofill(event_id, fresh["group_format"], fresh["groups"])
            stored = get_event_groups_autofill(event_id)
            groups_ids = _draw_ids(stored)
        except Exception as exc:  # noqa: BLE001
            return {"status": "skipped", "event_id": event_id, "reason": f"draw unavailable: {exc}"}
        if not groups_ids or any(t <= 0 for g in groups_ids for t in g):
            return {"status": "skipped", "event_id": event_id, "reason": "draw not published yet (TBD slots)"}

    fmt = str(detected.get("group_format") or (stored or {}).get("group_format") or "gsl4")
    if fmt == "de8" and detected.get("group_variant") == "de8_top3":
        fmt = "de8_top3"
    combined = bool(detected.get("combined_playoffs"))
    if combined and not combined_shape_supported(fmt, len(groups_ids)):
        # One fantasy game scores the playoffs too, but the simulator has no
        # verified bracket for this shape: refuse (a groups-only run would
        # store misleading valuations) and leave the flag for development.
        reason = reject_unsupported_combined_shape(fmt, len(groups_ids), event_id)
        return {"status": "unsupported", "event_id": event_id, "reason": reason}
    try:
        payload = _normalize_groups_payload(
            {"groups": groups_ids, "group_format": fmt, "combined_playoffs": combined, "playoff_stop_teams": 1}
        )
    except HTTPException as exc:
        return {"status": "skipped", "event_id": event_id, "reason": f"invalid shape: {exc.detail}"}
    payload["event_id"] = event_id
    payload["baked"] = {"trigger": trigger, "at": started}
    all_team_ids = [t for g in groups_ids for t in g]
    snapshot, inputs_meta = _event_inputs(event_id, all_team_ids, refresh=refresh_inputs)
    payload["inputs"] = inputs_meta
    try:
        result = _compute_groups_result(payload, snapshot=snapshot)
        model = _finalize_joint_precompute(result, event_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "event_id": event_id, "reason": str(exc)[:300]}
    _GROUPS_STATE.save(payload, result, key=event_id)
    if model is not None:
        saved = _GROUPS_STATE.load(key=event_id)
        if saved:
            _JOINT_MODEL_CACHE.clear()
            _JOINT_MODEL_CACHE[(event_id, saved["updated_at"])] = model
    playoff = result.get("playoff") or {}
    return {
        "status": "ok",
        "event_id": event_id,
        "group_format": fmt,
        "combined_playoffs": combined,
        "pairings": playoff.get("pairings"),
        "inputs": "frozen at event start" if inputs_meta.get("frozen") else "fresh (event not started)",
        "seconds": round(time.time() - started, 1),
    }


def _assemble_stage_stats(
    group_format: str,
    acc: Dict[str, Any],
    playoff: Optional[Dict[str, Any]],
    teams_out: Dict[int, Dict[str, Any]],
    team_rank_by_id: Optional[Dict[int, int]] = None,
) -> Dict[str, Any]:
    """Per-round view for the player breakdown modal, in the Playoff tab's
    shape: for every round of the group (then of the playoff) each team's
    chance of playing it, its opponents there (chance to meet, chance to beat),
    the 'eliminated earlier' share (the -3-per-missed-round penalty, credited to
    the rounds it misses) and the chance a bye skips the first playoff round;
    each player's expected points per round by component (net of the penalty),
    per opponent, and the booster the round uses. The group padding and the
    playoff padding (the round a bye skips) stay separate."""
    rounds = _GROUP_STAGE_ROUNDS.get(group_format, [])
    stages = [
        {"key": rk, "label": lbl, "full": full, "phase": "group", "bracket": side, "opener": i == 0}
        for i, (rk, lbl, full, side, _slots) in enumerate(rounds)
    ]
    po = playoff or {}
    round_labels = po.get("round_labels") or []
    po_keys = [f"po_{r}" for r in range(len(round_labels))]
    for r, lbl in enumerate(round_labels):
        stages.append({
            "key": po_keys[r], "label": lbl, "full": _PLAYOFF_ROUND_FULL.get(lbl, lbl),
            "phase": "playoff", "bracket": "playoff", "opener": False,
        })
    all_keys = [st["key"] for st in stages]
    fold = {mk: rk for rk, _lbl, _full, _side, mks in rounds for mk in mks}
    lower_order = [rk for rk, _lbl, _full, side, _slots in rounds if side == "lower"]

    def missed_after(rk: str) -> List[str]:
        """Rounds a team knocked out in `rk` never plays (the penalty is -3 each)."""
        if rk in lower_order:
            return lower_order[lower_order.index(rk) + 1:] + po_keys
        if rk in po_keys:
            return po_keys[po_keys.index(rk) + 1:]
        return list(po_keys)

    def folded(raw: Dict[str, float]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for mk, v in raw.items():
            rk = fold.get(mk, mk)
            out[rk] = out.get(rk, 0.0) + float(v)
        return out

    ranks = team_rank_by_id or {}
    teams: Dict[str, Dict[str, Any]] = {}
    team_elim_before: Dict[int, Dict[str, List[float]]] = {}
    for tid in teams_out:
        tid_i = int(tid)
        reach = folded(acc["reach"].get(tid_i) or {})
        wins = folded(acc["wins"].get(tid_i) or {})
        elim: Dict[str, List[float]] = {}
        for mk, c in (acc["elim"].get(tid_i) or {}).items():
            e = elim.setdefault(fold.get(mk, mk), [0.0, 0.0])
            e[0] += c[0]
            e[1] += c[1]
        opps: Dict[str, Dict[int, List[float]]] = {}
        for mk, per_opp in (acc["opp"].get(tid_i) or {}).items():
            dest = opps.setdefault(fold.get(mk, mk), {})
            for o, c in per_opp.items():
                oc = dest.setdefault(int(o), [0.0, 0.0])
                oc[0] += c[0]
                oc[1] += c[1]
        rr = (po.get("round_reach") or {}).get(str(tid)) or []
        rw = (po.get("round_wins") or {}).get(str(tid)) or []
        for r, p in enumerate(rr):
            reach[po_keys[r]] = float(p)
            wins[po_keys[r]] = float(rw[r]) if r < len(rw) else 0.0
        for r, c in ((po.get("round_elim") or {}).get(str(tid)) or {}).items():
            elim[po_keys[int(r)]] = [float(c["prob"]), float(c["points"])]
        for r, per_opp in ((po.get("round_opp") or {}).get(str(tid)) or {}).items():
            dest = opps.setdefault(po_keys[int(r)], {})
            for o, c in per_opp.items():
                dest[int(o)] = [float(c["play"]), float(c["win"])]
        elim_before: Dict[str, List[float]] = {}
        for k, (p_k, pts_k) in elim.items():
            missed = missed_after(k)
            if not missed:
                continue
            share = pts_k / len(missed)
            for rk in missed:
                e = elim_before.setdefault(rk, [0.0, 0.0])
                e[0] += p_k
                e[1] += share
        team_elim_before[tid_i] = elim_before
        bye_prob = float((po.get("bye_prob") or {}).get(str(tid)) or 0.0)
        rounds_out: Dict[str, Dict[str, Any]] = {}
        for rk in all_keys:
            play = float(reach.get(rk, 0.0))
            eb = elim_before.get(rk) or [0.0, 0.0]
            rounds_out[rk] = {
                "play": play,
                "win": (wins.get(rk, 0.0) / play) if play > 0 else 0.0,
                "opponents": {
                    str(o): {"play": c[0], "win": (c[1] / c[0] if c[0] > 0 else 0.0)}
                    for o, c in (opps.get(rk) or {}).items()
                },
                "elim_before": {"prob": eb[0], "points": eb[1]},
                "bye": bye_prob if (po_keys and rk == po_keys[0]) else 0.0,
            }
        teams[str(tid)] = {
            "rounds": rounds_out,
            "rank": ranks.get(tid_i),
            "padding_prob": float(acc["pad_prob"].get(tid_i) or 0.0),
            "playoff_padding_prob": bye_prob,
            "champion": float((po.get("advance_rate") or {}).get(str(tid)) or 0.0),
        }

    pid_team: Dict[int, int] = {}
    for tid, t in teams_out.items():
        for pid in (t.get("players") or {}):
            pid_team[int(pid)] = int(tid)
    players: Dict[str, Dict[str, Any]] = {}
    keys = ("rating", "win", "role", "booster", "total")
    for pid, per_key in acc["players"].items():
        merged: Dict[str, List[float]] = {}
        for mk, v in per_key.items():
            cell = merged.setdefault(fold.get(mk, mk), [0.0, 0.0, 0.0, 0.0, 0.0])
            for i in range(5):
                cell[i] += v[i]
        row: Dict[str, Any] = {"stages": {k: dict(zip(keys, v)) for k, v in merged.items()}}
        boost_merged: Dict[str, Dict[int, list]] = {}
        for mk, bucket in (acc["boost"].get(pid) or {}).items():
            dest = boost_merged.setdefault(fold.get(mk, mk), {})
            for bid, v in bucket.items():
                cell = dest.get(bid)
                if cell is None:
                    dest[bid] = list(v)
                else:
                    cell[0] += v[0]
        for rk, bucket in boost_merged.items():
            if rk in row["stages"]:
                row["stages"][rk]["boosters"] = _booster_list(bucket)
        opp_merged: Dict[str, Dict[int, List[float]]] = {}
        for mk, per_opp in (acc["opp_pts"].get(pid) or {}).items():
            dest = opp_merged.setdefault(fold.get(mk, mk), {})
            for o, v in per_opp.items():
                cell = dest.setdefault(int(o), [0.0, 0.0, 0.0, 0.0])
                for i in range(4):
                    cell[i] += v[i]
        for rk, per_opp in opp_merged.items():
            if rk in row["stages"]:
                row["stages"][rk]["opponents"] = {
                    str(o): {"rating": v[0], "win": v[1], "role": v[2], "booster": v[3], "total": sum(v)}
                    for o, v in per_opp.items()
                }
        role_info = acc["role"].get(pid)
        if role_info:
            row["role"] = {"role_id": role_info[0], "major": role_info[1], "minor": role_info[2], "points": role_info[3]}
        pad = acc["padding"].get(pid) or [0.0, 0.0, 0.0, 0.0]
        row["padding"] = {"rating": pad[0], "win": pad[1], "role": pad[2], "booster": pad[3], "total": sum(pad)}
        row["penalty"] = float(acc["penalty"].get(pid) or 0.0)
        players[str(pid)] = row
    for pid_s, per_round in (po.get("player_stage_ev") or {}).items():
        row = players.setdefault(pid_s, {"stages": {}, "padding": {"rating": 0, "win": 0, "role": 0, "booster": 0, "total": 0}, "penalty": 0.0})
        for r, cell in per_round.items():
            rk = po_keys[int(r)]
            row["stages"][rk] = dict(cell)
            row["stages"][rk]["boosters"] = ((po.get("player_stage_boost") or {}).get(pid_s) or {}).get(str(r)) or []
            row["stages"][rk]["opponents"] = ((po.get("player_round_opp_pts") or {}).get(pid_s) or {}).get(str(r)) or {}
        row["playoff_penalty"] = float((po.get("player_penalty") or {}).get(pid_s) or 0.0)
        row["playoff_padding"] = dict(
            (po.get("player_playoff_padding") or {}).get(pid_s)
            or {"rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0, "total": 0.0}
        )
    # Net each round: the 'eliminated earlier' share sits in the win component
    # (as in the Playoff tab), so the cards add up to the total once the group
    # and playoff paddings are added.
    for pid_s, row in players.items():
        tid_i = pid_team.get(int(pid_s))
        if tid_i is None:
            continue
        eb_rounds = team_elim_before.get(tid_i) or {}
        for rk, eb in eb_rounds.items():
            cell = row["stages"].setdefault(rk, {"rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0, "total": 0.0})
            cell["win"] = float(cell.get("win") or 0.0) + eb[1]
            cell["total"] = float(cell.get("total") or 0.0) + eb[1]
    return {"stages": stages, "teams": teams, "players": players}


def _compute_groups_result(payload: dict, progress_callback=None, snapshot: Optional[Dict[str, Any]] = None) -> dict:
    groups = payload["groups"]
    gf_raw = str(payload.get("group_format") or "").strip().lower()
    group_format = gf_raw if gf_raw in ("de8", "de8_top3") else "gsl4"
    quals_per_group = {"gsl4": 2, "de8": 4, "de8_top3": 3}[group_format]
    enumerate_group = {
        "gsl4": _enumerate_group_outcomes,
        "de8": _enumerate_group8_outcomes,
        "de8_top3": _enumerate_group8_top3_outcomes,
    }[group_format]
    combined = bool(payload.get("combined_playoffs"))
    all_team_ids = [tid for group in groups for tid in group]
    if snapshot:
        player_rows_by_id, team_rank_by_id = _lookup_context_from_snapshot(snapshot, all_team_ids)
    else:
        player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(all_team_ids)
    with team_strength.rank_overrides(team_rank_by_id if snapshot else None):
        result = _compute_groups_result_inner(
            payload, progress_callback, snapshot, groups, group_format, quals_per_group, enumerate_group,
            combined, all_team_ids, player_rows_by_id, team_rank_by_id,
        )
    if snapshot:
        # The rows this run was valued with travel with the results so the live
        # Top 5 (prices, roles, boosters) matches the stored expected points.
        meta = payload.get("inputs") or {}
        result["input_snapshot"] = {
            "taken_at": meta.get("taken_at"),
            "frozen": bool(meta.get("frozen")),
            "start_at": meta.get("start_at"),
            "players": snapshot.get("players") or {},
        }
    return result


def _compute_groups_result_inner(
    payload, progress_callback, snapshot, groups, group_format, quals_per_group, enumerate_group,
    combined, all_team_ids, player_rows_by_id, team_rank_by_id,
) -> dict:
    prob_cache: Dict = {}
    outcomes: List[Dict[str, Any]] = []
    teams_out: Dict[int, Dict[str, Any]] = {}
    stage_acc = _new_stage_acc()
    total_units = len(groups) + (1 if combined else 0)
    playoff_rounds = 0
    if combined:
        stop_teams = max(1, int(payload.get("playoff_stop_teams") or 1))
        if group_format == "de8_top3":
            playoff_rounds = 3  # 6-team bracket: quarters, semis, final
        else:
            playoff_rounds = max(1, int(math.log2(quals_per_group * len(groups))) - int(math.log2(stop_teams)))
    for g_idx, group in enumerate(groups):
        group_outcomes = enumerate_group(
            group, g_idx, player_rows_by_id, team_rank_by_id, prob_cache, extra_rounds=playoff_rounds,
            stage_acc=stage_acc,
        )
        outcomes.extend(group_outcomes)
        # Exact expected player totals for this group (its probabilities sum to 1).
        accum: Dict[int, Dict[str, float]] = {}
        for outcome in group_outcomes:
            prob = float(outcome["probability"])
            for pid_raw, comps in outcome["player_components"].items():
                bucket = accum.setdefault(
                    int(pid_raw), {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0}
                )
                bucket["total"] += prob * float(comps["total"])
                bucket["rating"] += prob * float(comps["rating"])
                bucket["win"] += prob * float(comps["win"])
                bucket["role"] += prob * float(comps["role"])
                bucket["booster"] += prob * float(comps["booster"])
        for tid in group:
            team_row = get_team_by_id(int(tid)) or {}
            roster_pids = {
                int(team_row.get(key) or 0)
                for key in ("player1_id", "player2_id", "player3_id", "player4_id", "player5_id")
            }
            players_out: Dict[int, Dict[str, float]] = {}
            for pid, sums in accum.items():
                if pid not in roster_pids:
                    continue
                players_out[pid] = {
                    "total_points": sums["total"],
                    "rating_points_total": sums["rating"],
                    "win_points_total": sums["win"],
                    "role_points_total": sums["role"],
                    "booster_points_total": sums["booster"],
                    "total_points_without_booster": sums["rating"] + sums["win"] + sums["role"],
                }
            teams_out[int(tid)] = {"team_id": int(tid), "wins": 0, "losses": 0, "players": players_out}
        if progress_callback:
            progress_callback(g_idx + 1, total_units)

    playoff_summary = None
    if combined:
        playoff_summary = _exact_combined_playoffs(
            groups,
            outcomes,
            player_rows_by_id,
            team_rank_by_id,
            prob_cache,
            stop_teams=int(payload.get("playoff_stop_teams") or 1),
            quals_per_group=quals_per_group,
            group_format=group_format,
            progress_callback=(
                (lambda done, total: progress_callback(len(groups) + (1 if done >= total else 0), total_units))
                if progress_callback
                else None
            ),
        )
        # Fold expected playoff points into every player's event totals so the
        # roster optimizer values the whole event, not just the group stage.
        playoff_ev = playoff_summary["player_ev"]
        for team in teams_out.values():
            for pid, comps in (team.get("players") or {}).items():
                extra = playoff_ev.get(int(pid))
                if not extra:
                    continue
                comps["total_points"] += extra["total"]
                comps["rating_points_total"] += extra["rating"]
                comps["win_points_total"] += extra["win"]
                comps["role_points_total"] += extra["role"]
                comps["booster_points_total"] += extra["booster"]
                comps["total_points_without_booster"] += extra["rating"] + extra["win"] + extra["role"]
    # Where each team finishes in its group: P(rank r) over the exact outcomes
    # (ranks are 1-based, only qualifying places are known by rank).
    place_odds: Dict[str, Dict[str, float]] = {}
    for outcome in outcomes:
        prob = float(outcome["probability"])
        for rank, tid in enumerate(outcome.get("qualified") or [], start=1):
            row = place_odds.setdefault(str(int(tid)), {})
            row[str(rank)] = row.get(str(rank), 0.0) + prob
    # The role each player was scored with (drives the role badge in the UI).
    try:
        for tid, ts in initialize_teams(all_team_ids, {tid: 999 for tid in all_team_ids}).items():
            players_out = (teams_out.get(int(tid)) or {}).get("players") or {}
            for pid, p in ts.players.items():
                if int(pid) in players_out:
                    players_out[int(pid)]["role_id"] = p.role_id
    except Exception:
        pass

    return {
        "teams": teams_out,
        "outcomes": outcomes,
        "outcomes_count": len(outcomes),
        "groups": groups,
        "group_count": len(groups),
        "group_format": group_format,
        "quals_per_group": quals_per_group,
        "place_odds": place_odds,
        "stage_stats": _assemble_stage_stats(group_format, stage_acc, playoff_summary, teams_out, team_rank_by_id),
        "combined_playoffs": combined,
        "playoff": playoff_summary,
        "method": "exact_enumeration_per_group" + ("_plus_exact_playoffs" if combined else ""),
    }


def _run_groups_job(job_id: str, payload: dict) -> None:
    def _update(processed: int, total: int) -> None:
        with GROUPS_JOBS_LOCK:
            job = GROUPS_JOBS.get(job_id)
            if not job:
                return
            job["processed_units"] = int(processed)
            job["total_units"] = int(total)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["updated_at"] = time.time()

    with GROUPS_JOBS_LOCK:
        job = GROUPS_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = time.time()
    try:
        event_key = int(payload.get("event_id") or _state_key())
        snapshot, inputs_meta = _event_inputs(event_key, [int(t) for g in payload["groups"] for t in g])
        payload["inputs"] = inputs_meta
        result = _compute_groups_result(payload, progress_callback=_update, snapshot=snapshot)
        model = _finalize_joint_precompute(result, event_key)
        _GROUPS_STATE.save(payload, result, key=event_key)
        if model is not None:
            saved = _GROUPS_STATE.load(key=event_key)
            if saved:
                _JOINT_MODEL_CACHE.clear()
                _JOINT_MODEL_CACHE[(event_key, saved["updated_at"])] = model
        with GROUPS_JOBS_LOCK:
            job = GROUPS_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["result_ready"] = True
            job["progress"] = 1.0
            job["updated_at"] = time.time()
    except Exception as exc:
        with GROUPS_JOBS_LOCK:
            job = GROUPS_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = time.time()


def _parse_event_groups(html: str, group_format: str = "gsl4") -> List[Dict[str, Any]]:
    """Parse each group's opening matchups (seed order) from an HLTV event
    page's embedded bracket JSON. GSL groups use DoubleElimination4 (4 seeds);
    8-team groups use DoubleElimination8 (8 seeds). The opening round for both
    lives under upperRound1, listing the seeded matchups in bracket order."""
    import html as _htmlmod
    import re

    un = _htmlmod.unescape(html or "")
    # id -> ranking, captured near each team object (ranking sits after the logo).
    ranking_by_id: Dict[int, int] = {}
    for rm in re.finditer(r'"team":\{"id":(\d+),"name":"[^"]+".{0,700}?"ranking":(\d+)', un):
        ranking_by_id.setdefault(int(rm.group(1)), int(rm.group(2)))

    seed_count = 8 if str(group_format).strip().lower() == "de8" else 4
    bracket_kind = "DoubleElimination8" if seed_count == 8 else "DoubleElimination4"

    # Each team object carries a full 5-player lineup (~2k chars), so an 8-team
    # opening round spans well over 15k chars. Bound each group's segment by the
    # start of the next bracket rather than a fixed window, so the last opening
    # match (seeds 7-8) is never truncated.
    bracket_starts = [mm.start() for mm in re.finditer(r"DoubleElimination[48]\"", un)]

    groups: List[Dict[str, Any]] = []
    for m in re.finditer(bracket_kind + r'","name":"(Group[^"]+)"', un):
        name = m.group(1)
        seg_end = next((b for b in bracket_starts if b > m.end()), len(un))
        seg = un[m.end() : seg_end]
        upper_start = seg.find("upperRound1")
        end = min([x for x in (seg.find("lowerRound1", upper_start), seg.find('"final"', upper_start), seg.find("upperRound2", upper_start)) if x > 0] or [len(seg)])
        upper = seg[upper_start:end]
        seeds: List[Optional[Dict[str, Any]]] = []
        for tm in re.finditer(
            r'"team[12]":\{"type":"[^"]*\.(FixedTeam|Placeholder|TBD|Bye)[^"]*"(?:,"team":\{"id":(\d+),"name":"([^"]+)")?',
            upper,
        ):
            kind, tid, tname = tm.group(1), tm.group(2), tm.group(3)
            if kind == "FixedTeam" and tname and tid:
                seeds.append({"id": int(tid), "name": tname, "ranking": ranking_by_id.get(int(tid))})
            else:
                seeds.append(None)
            if len(seeds) == seed_count:
                break
        groups.append({"name": name, "seeds": seeds})
    return groups


def _parse_event_groups_structured(html: str, group_format: str = "gsl4") -> List[Dict[str, Any]]:
    """Same output as _parse_event_groups, but from the typed slotted-bracket
    JSON via services/event_format. Each group bracket lists its teams in
    upperRound1 slot order (verified on FISSURE Playground 3: 9z v 5star,
    MongolZ v MIBR, ...), which is exactly the seed order the simulator wants."""
    import html as _htmlmod

    from backend.services.event_format import detect_event_structure

    try:
        structure = detect_event_structure(html or "")
    except Exception:
        return []
    seed_count = 8 if str(group_format).strip().lower() == "de8" else 4
    un = _htmlmod.unescape(html or "")
    ranking_by_id: Dict[int, int] = {}
    for rm in re.finditer(r'"team":\{"id":(\d+),"name":"[^"]+".{0,700}?"ranking":(\d+)', un):
        ranking_by_id.setdefault(int(rm.group(1)), int(rm.group(2)))

    groups: List[Dict[str, Any]] = []
    for bracket in structure.get("brackets") or []:
        if bracket.get("bracket") != "double_elim" or int(bracket.get("size") or 0) != seed_count:
            continue
        variant = str(bracket.get("variant") or "")
        if variant.endswith("_full") or "_qual" in variant:
            continue  # a whole-event DE bracket, not a group
        seeds: List[Optional[Dict[str, Any]]] = []
        for tid, name in list((bracket.get("teams") or {}).items())[:seed_count]:
            try:
                hltv_id = int(tid)
            except Exception:
                seeds.append(None)
                continue
            seeds.append({"id": hltv_id, "name": str(name), "ranking": ranking_by_id.get(hltv_id)})
        groups.append({"name": str(bracket.get("name") or f"Group {len(groups) + 1}"), "seeds": seeds})
    return groups


def _detect_group_format(html: str) -> Optional[str]:
    """Infer the group format from an event page's bracket markers.

    8-team double-elim groups embed DoubleElimination8; GSL groups embed
    DoubleElimination4. Returns "de8" / "gsl4", or None when neither is present
    (e.g. a Swiss or single-elimination event that has no group brackets).
    """
    import html as _htmlmod

    # The bracket JSON is embedded HTML-escaped, so unescape before matching
    # (the "8" vs "4" marker itself is unaffected, but keeps this consistent).
    un = _htmlmod.unescape(html or "")
    if "DoubleElimination8" in un:
        return "de8"
    if "DoubleElimination4" in un:
        return "gsl4"
    return None


def _resolve_group_seed_team_id(seed: Optional[Dict[str, Any]]) -> int:
    """Map a parsed seed to this app's team id, creating the team if unseen."""
    if not seed:
        return 0
    existing = get_team_by_name(str(seed["name"]))
    if existing:
        return int(existing.get("team_id"))
    rank = int(seed.get("ranking") or 250)
    add_or_update_team(
        name=str(seed["name"]),
        hltv_rank=rank,
        hltv_points=0,
        vrs_rank=rank,
        vrs_points=0,
        win_rate=0.5,
        player_ids=[0, 0, 0, 0, 0],
        hltv_team_id=int(seed["id"]),
    )
    created = get_team_by_name(str(seed["name"])) or {}
    return int(created.get("team_id") or 0)


def _resolve_hltv_event_url(
    hltv_event_id, hltv_event_url: str, fantasy_event_id=None
) -> str:
    """Best available HLTV event URL from an explicit url/id or the stored ref."""
    from backend.data.event_db import get_active_event_id, get_event_detail

    url = str(hltv_event_url or "").strip()
    hid = hltv_event_id
    if not url:
        if not hid:
            fid = fantasy_event_id or get_active_event_id()
            event = get_event_detail(int(fid)) if fid else None
            if event:
                hid = event.get("hltv_event_id")
                url = str(event.get("hltv_event_url") or "").strip()
        if not url and hid:
            url = f"https://www.hltv.org/events/{int(hid)}/-"
    return url


def autofill_event_groups(
    hltv_event_url: str = "",
    hltv_event_id=None,
    group_format: Optional[str] = None,
    fantasy_event_id=None,
    html: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch (or reuse) the event page, detect the group format if not given,
    and return {group_format, groups} with seeds resolved to app team ids.

    Raises HTTPException on missing link / fetch failure / no group brackets so
    the interactive endpoint surfaces a clear error; the import path calls this
    inside a try/except so a Swiss event or a blocked fetch never breaks import.
    """
    from backend.services.hltv_browser import fetch_hltv_html, HLTVBrowserError

    if html is None:
        url = _resolve_hltv_event_url(hltv_event_id, hltv_event_url, fantasy_event_id)
        if not url:
            raise HTTPException(status_code=400, detail="No HLTV event link found. Import the event first or pass hltv_event_id.")
        try:
            html = fetch_hltv_html(url, wait_text=None, timeout_ms=45000)
        except HLTVBrowserError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV event page: {exc}") from exc

    fmt = str(group_format or "").strip().lower()
    if fmt not in ("gsl4", "de8"):
        fmt = _detect_group_format(html) or ""
    if fmt not in ("gsl4", "de8"):
        raise HTTPException(status_code=404, detail="No GSL or double-elim group brackets found on that event page.")

    seed_count = 8 if fmt == "de8" else 4
    # Typed slotted-bracket JSON first (the maintained path; FISSURE Playground
    # 3's page defeated the regex parser while the typed data listed all 16
    # teams), regex parser as the fallback for pages it still understands.
    parsed = _parse_event_groups_structured(html, fmt)
    if not parsed or all(s is None for g in parsed for s in (g.get("seeds") or [])):
        parsed = _parse_event_groups(html, fmt) or parsed
    if not parsed:
        raise HTTPException(status_code=404, detail="No group brackets found on that event page.")

    groups_out = []
    for group in parsed:
        seeds = list(group.get("seeds") or [])
        seeds = (seeds + [None] * seed_count)[:seed_count]
        groups_out.append(
            {
                "name": group["name"],
                "team_ids": [_resolve_group_seed_team_id(s) for s in seeds],
                "team_names": [(s["name"] if s else "TBD") for s in seeds],
            }
        )
    return {"group_format": fmt, "groups": groups_out}


def store_event_groups_autofill(
    fantasy_event_id: int, hltv_event_url: str = "", hltv_event_id=None
) -> Optional[Dict[str, Any]]:
    """Best-effort: detect the format and prefill an event's groups at import
    time, persisting the result on the event. Returns the payload or None if the
    event has no autofillable group bracket (Swiss/single-elim) or fetch failed.
    """
    from backend.data.event_db import set_event_groups_autofill

    try:
        result = autofill_event_groups(
            hltv_event_url=hltv_event_url,
            hltv_event_id=hltv_event_id,
            fantasy_event_id=fantasy_event_id,
        )
    except HTTPException:
        return None
    except Exception:
        return None
    set_event_groups_autofill(int(fantasy_event_id), result["group_format"], result["groups"])
    return result


def _parse_event_playoff_bracket(html: str) -> Dict[str, Any]:
    """Parse the first-round seeds (bracket order) of an HLTV event's main
    single-elimination playoff bracket.

    HLTV embeds it as a Bracket.SingleElimination named 'Single Elimination
    Bracket' (distinct from the single-slot '3rd Place Decider Match' bracket).
    The first round's type — Round8 / Round4 / Round2 — gives the field size
    (16 / 8: RoundN has N matches), and each slot's team1/team2 lists the
    seeded matchups in bracket order. Returns
    {bracket_size, seeds:[{id,name,ranking}|None,...]}, or bracket_size 0 with
    no seeds when the page has no such bracket (Swiss-only or not yet seeded).
    """
    import html as _htmlmod
    import re

    un = _htmlmod.unescape(html or "")
    ranking_by_id: Dict[int, int] = {}
    for rm in re.finditer(r'"team":\{"id":(\d+),"name":"[^"]+".{0,700}?"ranking":(\d+)', un):
        ranking_by_id.setdefault(int(rm.group(1)), int(rm.group(2)))

    m = re.search(r'SingleElimination","name":"Single Elimination Bracket"', un)
    if not m:
        return {"bracket_size": 0, "seeds": []}
    seg = un[m.start():]
    rt = re.search(r"Bracket\.Round\.Round(\d+)", seg)
    if not rt:
        return {"bracket_size": 0, "seeds": []}
    field_size = int(rt.group(1)) * 2
    if field_size not in (2, 4, 8, 16, 32):
        return {"bracket_size": 0, "seeds": []}

    seeds: List[Optional[Dict[str, Any]]] = []
    for tm in re.finditer(
        r'"team[12]":\{"type":"[^"]*\.(FixedTeam|Placeholder|TBD|Bye|Seed|Winner|Loser)[^"]*"'
        r'(?:,"team":\{"id":(\d+),"name":"([^"]+)")?',
        seg,
    ):
        kind, tid, tname = tm.group(1), tm.group(2), tm.group(3)
        if kind == "FixedTeam" and tid and tname:
            seeds.append({"id": int(tid), "name": tname, "ranking": ranking_by_id.get(int(tid))})
        else:
            seeds.append(None)
        if len(seeds) == field_size:
            break
    return {"bracket_size": field_size, "seeds": seeds}


def _resolve_playoff_seed_team_id(
    seed: Optional[Dict[str, Any]],
    by_hltv_id: Dict[int, int],
    by_name: Dict[str, int],
) -> int:
    """Map a parsed playoff seed to this app's team id. Playoff qualifiers are
    already event teams in the DB, so match by HLTV team id first (robust to name
    differences), then by normalised name. Unlike the groups seed resolver this
    never creates a phantom team — an unmatched seed becomes 0 (an empty slot the
    user can fill) rather than an off-event team with no lineup."""
    if not seed:
        return 0
    hid = int(seed.get("id") or 0)
    if hid and hid in by_hltv_id:
        return by_hltv_id[hid]
    name_key = _normalize_team_name(str(seed.get("name") or ""))
    return by_name.get(name_key, 0)


def _normalize_team_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _find_event_snapshot_html(hltv_event_id) -> Optional[str]:
    """Reuse the event page archived at import (page_snapshots) so autofill is
    instant when the page is already stored; returns None to fall back to a live
    fetch. Snapshot URLs carry the event slug, so match on the /events/{id}/
    prefix rather than an exact URL, and prefer the canonical event page over the
    interactive '#simulator' variant, whose embedded bracket can hold user
    predictions rather than the official draw."""
    if not hltv_event_id:
        return None
    try:
        from backend.data.page_snapshots import list_snapshot_urls, get_page_snapshot

        needle = f"/events/{int(hltv_event_id)}/"
        matches = [u for u in list_snapshot_urls() if needle in u]
        matches.sort(key=lambda u: ("#" in u or "simulator" in u.lower(), u))
        for url in matches:
            snap = get_page_snapshot(url)
            if snap and snap.get("html"):
                return snap["html"]
    except Exception:
        return None
    return None


def _parse_bracket6_from_structure(html: str):
    """The 6-team byes playoff (two hidden first-round slots) from the page's
    slotted-bracket JSON, as ordered team names in the simulator's slot
    convention [bye1, qf1a, qf1b, qf2a, qf2b, bye2]. None when the page has no
    such bracket. Bye sides are identified structurally: a semi-final side
    whose source quarter-final slot is hidden."""
    import html as _htmllib
    import json as _json

    def side_name(matchup, i):
        side = (matchup or {}).get(f"team{i}") or {}
        name = side.get("name")
        return str(name) if name else None

    for raw in re.findall(r'data-slotted-bracket-json="([^"]+)"', html or ""):
        try:
            data = _json.loads(_htmllib.unescape(raw))
        except Exception:
            continue
        if not str(data.get("type", "")).endswith("SingleElimination"):
            continue
        bracket_name = str(data.get("name") or "")
        if re.search(r"3rd|third|decider", bracket_name, re.IGNORECASE):
            continue
        rounds = data.get("rounds") or []
        if len(rounds) < 2:
            continue
        first = rounds[0].get("slots") or []
        if len(first) != 4 or sum(1 for s in first if s.get("hidden")) != 2:
            continue
        qf_by_id = {((s.get("slotId") or {}).get("id")): s for s in first}
        semis = rounds[1].get("slots") or []
        if len(semis) != 2:
            continue
        layout: list = [None] * 6
        qf_positions = [(1, 2), (3, 4)]
        ok = True
        for si, sf in enumerate(semis):
            matchup = sf.get("matchup") or {}
            bye_name = None
            qf_pair = None
            for i in (1, 2):
                entry = sf.get(f"slotEntry{i}") or {}
                src_id = (entry.get("slotId") or {}).get("id")
                qf = qf_by_id.get(src_id)
                if qf is None:
                    ok = False
                    break
                if qf.get("hidden"):
                    bye_name = (
                        side_name(matchup, i)
                        or side_name(qf.get("matchup"), 1)
                        or side_name(qf.get("matchup"), 2)
                    )
                else:
                    qm = qf.get("matchup") or {}
                    qf_pair = (side_name(qm, 1), side_name(qm, 2))
            if not ok or qf_pair is None:
                ok = False
                break
            layout[0 if si == 0 else 5] = bye_name
            a, b = qf_positions[si]
            layout[a], layout[b] = qf_pair
        if ok:
            return layout
    return None


def autofill_event_playoff(
    hltv_event_url: str = "",
    hltv_event_id=None,
    fantasy_event_id=None,
    html: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch (or reuse) the event page and return the playoff bracket seeding as
    {bracket_size, team_ids, team_names} with seeds resolved to app team ids.
    Prefers the stored page snapshot before a slow live fetch. Raises
    HTTPException on missing link / fetch failure / no playoff bracket."""
    from backend.services.hltv_browser import fetch_hltv_html, HLTVBrowserError
    from backend.data.event_db import get_active_event_id, get_event_detail
    from backend.data.team_db import get_all_teams

    if html is None:
        hid = hltv_event_id
        url = str(hltv_event_url or "").strip()
        if not hid or not url:
            fid = fantasy_event_id or get_active_event_id()
            event = get_event_detail(int(fid)) if fid else None
            if event:
                hid = hid or event.get("hltv_event_id")
                url = url or str(event.get("hltv_event_url") or "").strip()
        if not url and hid:
            url = f"https://www.hltv.org/events/{int(hid)}/-"
        # Prefer the stored snapshot, but only trust it if it actually contains a
        # seeded single-elim bracket (an import-time snapshot may predate the
        # draw); otherwise fall back to a live fetch of the canonical page.
        snap_html = _find_event_snapshot_html(hid)
        if snap_html and int(_parse_event_playoff_bracket(snap_html).get("bracket_size") or 0) in (8, 16):
            html = snap_html
        if html is None:
            if not url:
                raise HTTPException(status_code=400, detail="No HLTV event link found. Import the event first or pass hltv_event_id.")
            try:
                html = fetch_hltv_html(url, wait_text=None, timeout_ms=45000)
            except HLTVBrowserError as exc:
                raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV event page: {exc}") from exc

    # Byes bracket (Porto/Cologne playoffs): resolved from the structured JSON
    # into the 6-slot convention [bye1, qf1a, qf1b, qf2a, qf2b, bye2].
    byes_layout = _parse_bracket6_from_structure(html)
    if byes_layout is not None:
        all_teams = get_all_teams()
        by_name: Dict[str, int] = {}
        for t in all_teams:
            by_name.setdefault(_normalize_team_name(str(t.get("name") or "")), int(t.get("team_id") or 0))
        return {
            "bracket_size": 6,
            "team_ids": [by_name.get(_normalize_team_name(n or ""), 0) if n else 0 for n in byes_layout],
            "team_names": [n or "TBD" for n in byes_layout],
        }

    parsed = _parse_event_playoff_bracket(html)
    size = int(parsed.get("bracket_size") or 0)
    seeds = list(parsed.get("seeds") or [])
    if size not in (2, 4, 8, 16) or not seeds:
        raise HTTPException(status_code=404, detail="No single-elimination playoff bracket found on that event page.")
    seeds = (seeds + [None] * size)[:size]

    all_teams = get_all_teams()
    by_hltv_id: Dict[int, int] = {}
    by_name: Dict[str, int] = {}
    for t in all_teams:
        tid = int(t.get("team_id") or 0)
        hltv = t.get("hltv_team_id")
        if hltv:
            by_hltv_id[int(hltv)] = tid
        by_name.setdefault(_normalize_team_name(str(t.get("name") or "")), tid)

    return {
        "bracket_size": size,
        "team_ids": [_resolve_playoff_seed_team_id(s, by_hltv_id, by_name) for s in seeds],
        "team_names": [(s["name"] if s else "TBD") for s in seeds],
    }


@router.post("/autofill-from-hltv-event")
def autofill_groups_from_hltv_event(payload: dict | None = None):
    """Scrape the linked HLTV event page and return the group opening matchups
    as this app's team IDs (seed order). Teams not already in the DB are created
    with the ranking from the bracket, so the sim can use them immediately. The
    format is auto-detected when the caller doesn't pin one."""
    from backend.data.event_db import get_active_event_id, set_event_groups_autofill

    body = payload or {}
    result = autofill_event_groups(
        hltv_event_url=str(body.get("hltv_event_url") or "").strip(),
        hltv_event_id=body.get("hltv_event_id"),
        group_format=body.get("group_format"),
        fantasy_event_id=body.get("event_id"),
    )
    # Persist so re-opening the Groups tab keeps the prefill without re-scraping.
    fid = body.get("event_id") or get_active_event_id()
    if fid:
        set_event_groups_autofill(int(fid), result["group_format"], result["groups"])
    return {
        "status": "ok",
        "group_format": result["group_format"],
        "group_count": len(result["groups"]),
        "groups": result["groups"],
    }


@router.get("/event-autofill")
def get_stored_event_autofill(event_id: Optional[int] = None):
    """Return the group format + prefilled seeds captured at import for an event,
    so the Groups tab can populate on open with no button click or scraping."""
    from backend.data.event_db import get_active_event_id, get_event_groups_autofill

    fid = event_id or get_active_event_id()
    if not fid:
        return {"status": "empty", "group_format": None, "groups": []}
    stored = get_event_groups_autofill(int(fid))
    if not stored:
        return {"status": "empty", "group_format": None, "groups": []}
    return {"status": "ok", "event_id": int(fid), **stored}


@router.post("/placeholder-team")
def create_placeholder_team(payload: dict):
    """Create a rosterless opponent team (e.g. a non-draftable qualifier team).

    It simulates normally as an opponent while contributing no fantasy
    players, matching events where HLTV's fantasy pool only covers part of
    the field. Rank defaults to 250 — a plausible fringe-qualifier strength —
    rather than 999, which would make every placeholder a hopeless underdog.
    """
    name = str((payload or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    existing = get_team_by_name(name)
    if existing:
        return {"status": "exists", "team_id": existing.get("team_id"), "name": name}
    add_or_update_team(
        name=name,
        hltv_rank=250,
        hltv_points=0,
        vrs_rank=250,
        vrs_points=0,
        win_rate=0.5,
        player_ids=[0, 0, 0, 0, 0],
        hltv_team_id=None,
    )
    created = get_team_by_name(name) or {}
    return {"status": "created", "team_id": created.get("team_id"), "name": name}


@router.post("/start")
def start_groups_simulation(payload: dict):
    normalized = _normalize_groups_payload(payload or {})
    job_id = uuid.uuid4().hex
    with GROUPS_JOBS_LOCK:
        GROUPS_JOBS[job_id] = {
            "status": "queued",
            "error": "",
            "progress": 0.0,
            "processed_units": 0,
            "total_units": len(normalized["groups"]),
            "result_ready": False,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    threading.Thread(target=_run_groups_job, args=(job_id, normalized), daemon=True).start()
    return {"job_id": job_id}


@router.get("/job/{job_id}")
def get_groups_job(job_id: str):
    with GROUPS_JOBS_LOCK:
        job = GROUPS_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_id not found")
        return {"job_id": job_id, **{k: v for k, v in job.items() if k != "result"}}


def _slim_groups_results(results: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the Groups tab reads, minus the enumerated outcomes.

    The outcomes list is ~100% of a stored run (80 MB for two 8-team groups)
    and the tab only ever used it to derive each team's qualify odds, so
    those are folded in here as `qualify_odds` and the list itself stays
    server-side (completed-group scoring already runs on the backend).
    """
    results = results or {}
    slim = {k: v for k, v in results.items() if k != "outcomes"}
    if isinstance(slim.get("playoff"), dict):
        # the joint-outcome sampler's tables are query-time inputs, not tab data
        slim["playoff"] = {k: v for k, v in slim["playoff"].items() if k not in ("pairing_table", "win_probs")}
    odds: Dict[str, Dict[str, float]] = {}
    for outcome in results.get("outcomes") or []:
        group = str(outcome.get("group", 0))
        prob = float(outcome.get("probability") or 0.0)
        bucket = odds.setdefault(group, {})
        for tid in outcome.get("qualified") or []:
            bucket[str(tid)] = bucket.get(str(tid), 0.0) + prob
    slim["qualify_odds"] = odds
    slim["outcomes_count"] = int(results.get("outcomes_count") or len(results.get("outcomes") or []))
    return slim


@router.get("/latest")
def get_latest_groups(full: bool = False, event_id: Optional[int] = None):
    """Stored simulation for the active event (or ?event_id= for another
    event's stored run). Slim by default (no outcomes list; see
    _slim_groups_results) so the Groups tab opens instantly; ?full=1 returns
    the raw stored blob."""
    key = int(event_id) if event_id else _state_key()
    latest = _GROUPS_STATE.load(key=key)
    if not latest:
        return {"exists": False, "event_id": key}
    return {
        "exists": True,
        "event_id": key,
        "payload": latest["payload"],
        "results": latest["results"] if full else _slim_groups_results(latest["results"] or {}),
        "updated_at": latest["updated_at"],
    }


@router.post("/bake")
def bake_groups_event(event_id: Optional[int] = None, refresh_inputs: bool = False):
    """(Re)bake the stored valuation for one event — the active one by default —
    synchronously, without changing which event is active. Same job the
    scheduler runs at import; useful after a scoring change. Once the event
    has started its inputs (ratings, roles, boosters, ranks) stay frozen at
    the pre-event snapshot; refresh_inputs=1 deliberately re-reads them."""
    key = int(event_id) if event_id else _state_key()
    return bake_event_valuations(key, trigger="manual", refresh_inputs=bool(refresh_inputs))


@router.delete("/latest")
def reset_latest_groups():
    key = _state_key()
    for state in (_GROUPS_STATE, _GROUPS_BEST_STATE, _GROUPS_BEST_META):
        state.delete(key=key)
    return {"status": "ok", "event_id": key}


def _group_player_outcome_vectors(results: dict) -> Dict[int, Dict[int, List[float]]]:
    """{group_index: {player_id: [score per outcome]}} in stored outcome order.

    The per-group outcome count depends on the format (32 for GSL, 1024 for the
    8-team double-elim), so each vector is sized to the group's actual count
    rather than a fixed length.
    """
    outcomes = results.get("outcomes") or []
    per_group_count: Dict[int, int] = {}
    for outcome in outcomes:
        g = int(outcome.get("group") or 0)
        per_group_count[g] = per_group_count.get(g, 0) + 1
    vectors: Dict[int, Dict[int, List[float]]] = {}
    idxs: Dict[int, int] = {}
    for outcome in outcomes:
        g = int(outcome.get("group") or 0)
        idx = idxs.get(g, 0)
        idxs[g] = idx + 1
        by_pid = vectors.setdefault(g, {})
        n = per_group_count[g]
        for pid_raw, score in (outcome.get("players") or {}).items():
            by_pid.setdefault(int(pid_raw), [0.0] * n)[idx] = float(score)
    return vectors


def _group_outcome_probs_by_group(results: dict) -> Dict[int, List[float]]:
    """{group: [probability per outcome]} in the same per-group order as
    _group_player_outcome_vectors, so indices line up."""
    probs: Dict[int, List[float]] = {}
    for outcome in results.get("outcomes") or []:
        g = int(outcome.get("group") or 0)
        probs.setdefault(g, []).append(float(outcome.get("probability") or 0.0))
    return probs


# Most-likely-winner: enumerate the joint outcome space exactly up to this many
# combinations (single group = 1024), else Monte-Carlo sample it.
_MOST_OUTCOMES_EXACT_JOINT = 20000
_MOST_OUTCOMES_SAMPLES = 4000
# Combined events: joint outcomes (group results + playoff bracket) sampled for
# the ceiling / most-likely modes — the joint space is ~33M for two 8-team
# groups and a 6-team bracket, far too big to enumerate.
_JOINT_SAMPLES = 100000
_JOINT_MODEL_CACHE: Dict[Any, tuple] = {}  # (event, updated_at) -> model; ~64 MB each at 100k samples


def _role_points_per_match(results: dict) -> Dict[int, float]:
    """{pid: per-match points of the player's best role} from the stored stage stats."""
    out: Dict[int, float] = {}
    for pid_s, row in ((results.get("stage_stats") or {}).get("players") or {}).items():
        pts = ((row or {}).get("role") or {}).get("points")
        if pts is not None:
            out[int(pid_s)] = float(pts)
    return out


def _event_outcome_model(results: dict, cache_key: Any = None) -> tuple:
    """(vectors {0: {pid: np.ndarray}}, probs {0: np.ndarray}, group_of_player,
    decomposition) — the outcome space the ceiling and most-likely-winner modes
    score rosters over, always ONE outcome axis. A single group: its exact
    outcomes. Otherwise _JOINT_SAMPLES sampled joint outcomes — a result for
    every group match and, for combined events, every playoff match, scored
    exactly like the valuation — each with probability 1/N. The vectors hold
    each player's points with their OWN best role and boosters (the additive
    search key); the decomposition {rw, mp, mr} holds per player the rating +
    win points as scored, the matches played and the matches the role is
    scored for, so a roster can be re-scored under its own plan. Average stays
    exact. Stored runs without the decomposition inputs give decomposition
    None (per-player scoring only)."""
    import numpy as np

    if cache_key is not None and cache_key in _JOINT_MODEL_CACHE:
        return _JOINT_MODEL_CACHE[cache_key]
    groups = results.get("groups") or []
    if len(groups) == 1 and not ((results.get("playoff") or {}).get("pairing_table")):
        raw = _group_player_outcome_vectors(results)
        vectors = {0: {pid: np.asarray(v, dtype=np.float64) for pid, v in (raw.get(0) or {}).items()}}
        probs = {0: np.asarray(_group_outcome_probs_by_group(results).get(0) or [], dtype=np.float64)}
        decomp = _group_outcome_decomposition(results, 0)
        model = (vectors, probs, {pid: 0 for pid in vectors[0]}, decomp)
    else:
        model = _sample_joint_outcomes(results, _JOINT_SAMPLES)
    if cache_key is not None:
        if len(_JOINT_MODEL_CACHE) >= 2:
            _JOINT_MODEL_CACHE.pop(next(iter(_JOINT_MODEL_CACHE)))
        _JOINT_MODEL_CACHE[cache_key] = model
    return model


def _group_outcome_decomposition(results: dict, group_index: int) -> Optional[Dict[str, Dict[int, Any]]]:
    """{rw, mp, mr}: per player, over one group's stored outcomes in order —
    rating + win as scored (paddings and penalties included), matches the team
    played, matches the role is scored for (role points ÷ the best role's
    per-match points, so padded matches count)."""
    import numpy as np

    outs = [o for o in (results.get("outcomes") or []) if int(o.get("group") or 0) == int(group_index)]
    if not outs or not all(o.get("player_components") for o in outs):
        return None
    role_pm = _role_points_per_match(results)
    pid_team: Dict[int, int] = {}
    for tid, t in (results.get("teams") or {}).items():
        for pid in (t.get("players") or {}):
            pid_team[int(pid)] = int(tid)
    pids = sorted({int(pid) for o in outs for pid in (o.get("players") or {})})
    n = len(outs)
    rw = {pid: np.zeros(n) for pid in pids}
    mp = {pid: np.zeros(n, dtype=np.uint8) for pid in pids}
    mr = {pid: np.zeros(n) for pid in pids}
    for c, o in enumerate(outs):
        played: Dict[int, int] = {}
        for m in o.get("matches") or []:
            for t in m.get("teams") or []:
                played[int(t)] = played.get(int(t), 0) + 1
        comps = o.get("player_components") or {}
        for pid in pids:
            cp = comps.get(str(pid)) or {}
            rw[pid][c] = float(cp.get("rating") or 0.0) + float(cp.get("win") or 0.0)
            mp[pid][c] = played.get(pid_team.get(pid, -1), 0)
            pm = role_pm.get(pid, 0.0)
            mr[pid][c] = (float(cp.get("role") or 0.0) / pm) if abs(pm) > 1e-9 else 0.0
    return {"rw": rw, "mp": mp, "mr": mr}


def _sample_joint_outcomes(results: dict, n: int, seed: int = 20240101) -> tuple:
    """Sample n joint outcomes of a combined event and score every player in
    each: the group result (its stored per-player totals, group padding and
    penalties included), then the playoff bracket played match by match from
    the qualifiers with the stored win probabilities, each pairing scored from
    the stored table (booster slot numbering continues from the group matches,
    elimination penalties included), and the playoff padding of a bye team's
    players (+6 win plus their average rating and role over the matches played
    in that outcome). Deterministic (fixed seed)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    outcomes = results.get("outcomes") or []
    groups = [[int(t) for t in g] for g in (results.get("groups") or [])]
    x = len(groups)
    po = results.get("playoff") or {}
    fmt = str(results.get("group_format") or "gsl4")
    quals = {"gsl4": 2, "de8": 4, "de8_top3": 3}.get(fmt, 2)
    has_playoff = bool(po.get("pairing_table") and po.get("win_probs"))
    if has_playoff:
        rounds_total = int(po.get("rounds") or 1)
        template = _combined_bracket_template(x, quals, rounds_total, fmt)
        rounds, entry_round, prior_matches = template["rounds"], template["entry_round"], template["prior_matches"]
        byes = bool(template["byes_to_semis"])
    else:
        rounds_total, rounds, entry_round, prior_matches, byes = 0, [], {}, {}, False
    win_probs = {int(a): {int(b): float(p) for b, p in row.items()} for a, row in (po.get("win_probs") or {}).items()}
    table = {
        tuple(int(t) for t in k.split(",")): {int(pid): v for pid, v in row.items()}
        for k, row in (po.get("pairing_table") or {}).items()
    }
    # the decomposition needs rating + win per pairing (4-value rows) and per-outcome components
    decomposed = all(o.get("player_components") for o in outcomes) and (
        not has_playoff or all(len(v) >= 4 for row in table.values() for v in row.values())
    )
    role_pm = _role_points_per_match(results)
    pid_team: Dict[int, int] = {}
    for tid, t in (results.get("teams") or {}).items():
        for pid in (t.get("players") or {}):
            pid_team[int(pid)] = int(tid)
    by_group: Dict[int, List[dict]] = {}
    for o in outcomes:
        by_group.setdefault(int(o.get("group") or 0), []).append(o)
    all_pids = sorted({int(pid) for o in outcomes for pid in (o.get("players") or {})})
    vec = {pid: np.zeros(n, dtype=np.float64) for pid in all_pids}
    rw = {pid: np.zeros(n, dtype=np.float64) for pid in all_pids} if decomposed else {}
    mp = {pid: np.zeros(n, dtype=np.uint8) for pid in all_pids} if decomposed else {}
    mr = {pid: np.zeros(n, dtype=np.float64) for pid in all_pids} if decomposed else {}
    idx_by_group: Dict[int, Any] = {}
    for g, olist in by_group.items():
        p = np.asarray([float(o.get("probability") or 0.0) for o in olist], dtype=np.float64)
        p = p / p.sum()
        idx = rng.choice(len(olist), size=n, p=p)
        idx_by_group[g] = idx
        pids_g = sorted({int(pid) for o in olist for pid in (o.get("players") or {})})
        mat = np.asarray(
            [[float((o.get("players") or {}).get(str(pid)) or 0.0) for pid in pids_g] for o in olist],
            dtype=np.float64,
        )
        picked = mat[idx]
        for j, pid in enumerate(pids_g):
            vec[pid] += picked[:, j]
        if decomposed:
            rw_mat = np.zeros((len(olist), len(pids_g)))
            mp_mat = np.zeros((len(olist), len(pids_g)), dtype=np.uint8)
            mr_mat = np.zeros((len(olist), len(pids_g)))
            for c, o in enumerate(olist):
                played: Dict[int, int] = {}
                for m in o.get("matches") or []:
                    for t in m.get("teams") or []:
                        played[int(t)] = played.get(int(t), 0) + 1
                comps = o.get("player_components") or {}
                for j, pid in enumerate(pids_g):
                    cp = comps.get(str(pid)) or {}
                    rw_mat[c, j] = float(cp.get("rating") or 0.0) + float(cp.get("win") or 0.0)
                    mp_mat[c, j] = played.get(pid_team.get(pid, -1), 0)
                    pm = role_pm.get(pid, 0.0)
                    mr_mat[c, j] = (float(cp.get("role") or 0.0) / pm) if abs(pm) > 1e-9 else 0.0
            for j, pid in enumerate(pids_g):
                rw[pid] += rw_mat[idx, j]
                mp[pid] += mp_mat[idx, j]
                mr[pid] += mr_mat[idx, j]
    n_matches = sum(len(r) for r in rounds)
    u = rng.random((n, max(n_matches, 1)))
    missing = 0
    # Python loop over samples: ~5 matches × 10 players each — fine for 20k.
    for i in range(n):
        seed_team: Dict[tuple, int] = {}
        for g in range(x):
            q = by_group[g][idx_by_group[g][i]].get("qualified") or []
            for rank in range(quals):
                if rank < len(q):
                    seed_team[(g, rank)] = int(q[rank])
        team_seed = {t: sd for sd, t in seed_team.items()}
        winners: Dict[tuple, int] = {}
        played: Dict[int, List[float]] = {}  # pid -> [rating, role, games] on the bye path (padding basis)
        ui = 0
        for r, matches in enumerate(rounds):
            rem = rounds_total - r - 1
            for m, (left, right) in enumerate(matches):
                a = seed_team[(left[1], left[2])] if left[0] == "seed" else winners[(left[1], left[2])]
                b = seed_team[(right[1], right[2])] if right[0] == "seed" else winners[(right[1], right[2])]
                sa, sb = team_seed[a], team_seed[b]
                na = prior_matches[sa] + r - entry_round.get(sa, 0) + 1
                nb = prior_matches[sb] + r - entry_round.get(sb, 0) + 1
                w = a if u[i, ui] < win_probs[a][b] else b
                ui += 1
                winners[(r, m)] = w
                row = table.get((a, b, w, na, nb, rem))
                if row is None:
                    missing += 1
                    continue
                role_at = 3 if len(next(iter(row.values()))) >= 4 else 2
                for pid, comps in row.items():
                    vec[pid][i] += comps[0]
                    if decomposed:
                        rw[pid][i] += comps[1] + comps[2]
                        mp[pid][i] += 1
                        mr[pid][i] += 1.0
                if byes:
                    for t, sd in ((a, sa), (b, sb)):
                        if entry_round.get(sd, 0) > 0:
                            # only this team's players (the table row holds both sides)
                            for pid in _team_pids_of(results, t):
                                comps = row.get(pid)
                                if comps is None:
                                    continue
                                acc = played.setdefault(pid, [0.0, 0.0, 0.0])
                                acc[0] += comps[1]
                                acc[1] += comps[role_at]
                                acc[2] += 1.0
        if byes:
            for sd, t in seed_team.items():
                if entry_round.get(sd, 0) <= 0:
                    continue
                fg = by_group[sd[0]][idx_by_group[sd[0]][i]].get("first_games") or {}
                for pid_s, sums in fg.items():
                    pid = int(pid_s)
                    acc = played.get(pid) or [0.0, 0.0, 0.0]
                    games = float(sums[4]) + acc[2]
                    pad = _PLAYOFF_BYE_PADDING_POINTS
                    rating_avg = (float(sums[0]) + acc[0]) / games if games > 0 else 0.0
                    if games > 0:
                        pad += rating_avg + (float(sums[2]) + acc[1]) / games
                    if pid in vec:
                        vec[pid][i] += pad
                        if decomposed:
                            rw[pid][i] += _PLAYOFF_BYE_PADDING_POINTS + rating_avg
                            mr[pid][i] += 1.0
    if missing:
        import logging

        logging.getLogger(__name__).warning("joint sampler: %d pairings missing from the stored table", missing)
    decomp = {"rw": rw, "mp": mp, "mr": mr} if decomposed else None
    return {0: vec}, {0: np.full(n, 1.0 / n, dtype=np.float64)}, {pid: 0 for pid in vec}, decomp


_TEAM_PIDS_CACHE: Dict[int, Dict[int, List[int]]] = {}


def _team_pids_of(results: dict, tid: int) -> List[int]:
    key = id(results)
    by_team = _TEAM_PIDS_CACHE.get(key)
    if by_team is None:
        by_team = {int(t): [int(pid) for pid in (row.get("players") or {})] for t, row in (results.get("teams") or {}).items()}
        _TEAM_PIDS_CACHE.clear()
        _TEAM_PIDS_CACHE[key] = by_team
    return by_team.get(int(tid), [])
# Only the strongest teams by average realistically ever win an outcome; compute
# win probability for this many (the true winner is certainly among them).
_WIN_PROB_CANDIDATES = 300


def _compute_group_win_probs(
    team_pid_lists: List[List[int]],
    vectors: Dict[int, Dict[int, List[float]]],
    group_of_player: Dict[int, int],
    probs_by_group: Dict[int, List[float]],
) -> List[float]:
    """P(each team is the single highest-scoring roster) across the joint outcome
    space of all groups (groups independent → joint prob = product). Exact when
    the joint space is small (single group), Monte-Carlo otherwise. Ties split
    the probability evenly."""
    groups = sorted(vectors.keys())
    counts = {g: len(probs_by_group.get(g, [])) for g in groups}
    n_teams = len(team_pid_lists)
    win = [0.0] * n_teams
    if n_teams == 0 or not groups:
        return win
    if len(groups) == 1:
        # One outcome axis (a single group, or the sampled joint outcomes of a
        # combined event): vectorised — score every roster on every outcome,
        # the best per outcome takes its probability (ties share it).
        import numpy as np

        g = groups[0]
        gv = vectors[g]
        probs = np.asarray(probs_by_group[g], dtype=np.float64)
        n_out = len(probs)

        def chunk_scores(start: int, stop: int) -> Any:
            block = np.zeros((stop - start, n_out), dtype=np.float64)
            for t in range(start, stop):
                for pid in team_pid_lists[t]:
                    v = gv.get(int(pid))
                    if v is not None:
                        block[t - start] += np.asarray(v, dtype=np.float64)
            return block

        # Three chunked passes (candidates × outcomes would be ~320 MB at once):
        # the best score per outcome, how many rosters tie it, then each
        # roster's share of the outcomes it tops.
        step = 64  # 64 rosters × 100k outcomes × 8 B = 51 MB per chunk
        best = np.full(n_out, -np.inf)
        for start in range(0, n_teams, step):
            best = np.maximum(best, chunk_scores(start, min(n_teams, start + step)).max(axis=0))
        counts = np.zeros(n_out)
        for start in range(0, n_teams, step):
            counts += (chunk_scores(start, min(n_teams, start + step)) >= best - 1e-9).sum(axis=0)
        share = probs / np.maximum(counts, 1)
        for start in range(0, n_teams, step):
            stop = min(n_teams, start + step)
            ties = chunk_scores(start, stop) >= best - 1e-9
            for t, v in enumerate((ties * share).sum(axis=1)):
                win[start + t] = float(v)
        return win

    # Per team, the pids that fall in each group.
    team_group_pids: List[Dict[int, List[int]]] = []
    for pids in team_pid_lists:
        d: Dict[int, List[int]] = {}
        for pid in pids:
            g = group_of_player.get(int(pid))
            if g is not None:
                d.setdefault(g, []).append(int(pid))
        team_group_pids.append(d)

    def tally(idx_by_group: Dict[int, int], weight: float) -> None:
        best = None
        winners: List[int] = []
        for t in range(n_teams):
            s = 0.0
            for g, gp in team_group_pids[t].items():
                oi = idx_by_group.get(g, 0)
                gv = vectors.get(g) or {}
                for pid in gp:
                    vec = gv.get(pid)
                    if vec and oi < len(vec):
                        s += vec[oi]
            if best is None or s > best + 1e-9:
                best = s
                winners = [t]
            elif abs(s - best) <= 1e-9:
                winners.append(t)
        if winners:
            share = weight / len(winners)
            for t in winners:
                win[t] += share

    joint = 1
    for g in groups:
        joint *= max(1, counts[g])

    if joint <= _MOST_OUTCOMES_EXACT_JOINT:
        import itertools

        for combo in itertools.product(*[range(counts[g]) for g in groups]):
            w = 1.0
            for i, g in enumerate(groups):
                w *= probs_by_group[g][combo[i]]
            if w > 0:
                tally({groups[i]: combo[i] for i in range(len(groups))}, w)
    else:
        import bisect

        cum: Dict[int, List[float]] = {}
        for g in groups:
            acc = 0.0
            c = []
            for p in probs_by_group[g]:
                acc += p
                c.append(acc)
            cum[g] = c
        rng = random.Random(20240101)
        n = _MOST_OUTCOMES_SAMPLES
        for _ in range(n):
            idx_by_group = {}
            for g in groups:
                r = rng.random() * (cum[g][-1] if cum[g] else 1.0)
                idx_by_group[g] = min(bisect.bisect_left(cum[g], r), counts[g] - 1)
            tally(idx_by_group, 1.0 / n)
    return win


def _groups_players_info(results: dict, exclude: set) -> List[Dict[str, Any]]:
    players_info = []
    for tid, team_res in (results.get("teams") or {}).items():
        for pid_raw, comps in (team_res.get("players") or {}).items():
            pid = int(pid_raw)
            if pid in exclude:
                continue
            row = _snapshot_player_row(results, pid)
            if not row:
                continue
            rating_ev = float(comps.get("rating_points_total") or 0.0)
            win_ev = float(comps.get("win_points_total") or 0.0)
            role_ev = float(comps.get("role_points_total") or 0.0)
            players_info.append(
                {
                    "player_id": pid,
                    "name": row.get("name", f"Player {pid}"),
                    "team_id": int(tid),
                    "price": int(row.get("price") or 0),
                    "rating_ev": rating_ev,
                    "win_ev": win_ev,
                    "role_ev": role_ev,
                    "booster_ev": 0.0,
                    "raw_booster_ev": float(comps.get("booster_points_total") or 0.0),
                    "total_ev": rating_ev + win_ev + role_ev,
                }
            )
    return players_info


# Above this many candidate 5-player combos, prune the pool before enumerating.
_POOL_REDUCE_COMBO_THRESHOLD = 2_000_000

# The exact per-roster booster assignment (a min-cost flow) is ~0.5ms, too slow
# for every combo. We rank all combos by an admissible upper bound and run the
# exact assignment only on this many top contenders — comfortably enough that
# the true best (booster is a small slice of total EV) is always captured.
EXACT_BOOSTER_TOPK = 2000


def _reduce_player_pool(
    players_info: List[Dict[str, Any]], include: set, max_per_team: int
) -> List[Dict[str, Any]]:
    """Drop players who can never be in the optimal (or a top-K) additive-EV
    roster, so the C(N,5) enumeration stays tractable for large combined pools.

    A player p is dropped only if it is *dominated* by at least `keep_depth`
    other players that are each no more expensive AND no worse in total_ev — any
    team using p can swap in one of those (cheaper-or-equal keeps it under
    budget; better-or-equal keeps the score) while respecting the per-team cap.
    `keep_depth` is set high enough (given ≤5 players/team) that a swap that also
    satisfies the ≤max_per_team constraint always exists, so the optimum is
    provably retained. Forced-include players are always kept.
    """
    include = {int(x) for x in (include or set())}
    # With ≤5 players per team and a ≤max_per_team cap, this many dominators
    # guarantees a valid, budget- and cap-respecting replacement exists.
    keep_depth = 6 + 5 * max(1, int(max_per_team))
    kept: List[Dict[str, Any]] = []
    for p in players_info:
        if int(p["player_id"]) in include:
            kept.append(p)
            continue
        cost = int(p.get("price") or 0)
        # p's best-case value (its EV plus the most booster it could ever add)
        # must be beaten by a dominator's guaranteed floor for the drop to be
        # safe once booster is part of the objective.
        ceil = float(p.get("total_ev") or 0.0) + float(p.get("booster_ub") or 0.0)
        dominators = 0
        for q in players_info:
            if q is p:
                continue
            if int(q.get("price") or 0) <= cost and float(q.get("total_ev") or 0.0) >= ceil:
                dominators += 1
                if dominators >= keep_depth:
                    break
        if dominators < keep_depth:
            kept.append(p)
    return kept


def _group_team_reach_probs(results: dict) -> Dict[int, Dict[int, float]]:
    """Exact P(team plays its Nth match) per team, from the enumerated outcomes.
    {team_id: {match_number: probability}}. Used as booster slot probabilities —
    a booster on a player only pays off if their team actually reaches that
    match, and groups give us those reach odds exactly (no Monte Carlo)."""
    reach: Dict[int, Dict[int, float]] = {}
    for outcome in results.get("outcomes") or []:
        prob = float(outcome.get("probability") or 0.0)
        if prob <= 0:
            continue
        counts: Dict[int, int] = {}
        for m in outcome.get("matches") or []:
            for t in m.get("teams") or []:
                counts[int(t)] = counts.get(int(t), 0) + 1
        for tid, c in counts.items():
            d = reach.setdefault(int(tid), {})
            for n in range(1, c + 1):
                d[n] = d.get(n, 0.0) + prob
    return reach


def _team_match_reach_probs(results: dict) -> Dict[int, Dict[int, float]]:
    """P(team plays its Nth match) over the whole event: the group matches from
    the enumerated outcomes plus, for combined events, the playoff matches by
    match number from the exact pairings (disjoint events, so they add). The
    booster slots run through the playoffs, so the roster booster solve needs
    the full run; stored results from before `playoff.match_reach` existed fall
    back to the group matches only."""
    reach = _group_team_reach_probs(results)
    for tid_s, per_num in ((results.get("playoff") or {}).get("match_reach") or {}).items():
        d = reach.setdefault(int(tid_s), {})
        for num_s, p in per_num.items():
            d[int(num_s)] = d.get(int(num_s), 0.0) + float(p)
    return reach




def _topk_rosters_bnb(
    players_info: List[Dict[str, Any]],
    bound_scores: Dict[int, float],
    k: int,
    budget: int,
    max_per_team: int,
    include: set,
    true_score_fn=None,
    time_budget_seconds: float = 10.0,
    bound_offset: float = 0.0,
    role_scores_by_player: Optional[Dict[int, Dict[int, float]]] = None,
) -> tuple[List[Dict[str, Any]], bool]:
    """Top-k rosters by branch-and-bound without enumerating the full space.

    bound_scores must be an additive per-player upper bound on each player's
    contribution (plus bound_offset, a roster-wide constant such as the Σλ of
    the Lagrangian booster bound); true_score_fn (if given) computes the
    roster's real score, which must never exceed the additive bound.

    Returns (rosters, exact). With a near-binding budget the search can be
    slow, so it stops at time_budget_seconds and reports exact=False; results
    are then the best rosters found so far rather than provably the best.
    """
    import heapq

    include_ids = {int(pid) for pid in (include or set())}
    forced = [p for p in players_info if int(p["player_id"]) in include_ids]
    if len(forced) < len(include_ids):
        return [], True
    pool = [p for p in players_info if int(p["player_id"]) not in include_ids]
    pool.sort(key=lambda p: -float(bound_scores.get(int(p["player_id"]), 0.0)))
    n = len(pool)
    need = 5 - len(forced)
    if need < 0:
        return [], True

    scores = [float(bound_scores.get(int(p["player_id"]), 0.0)) for p in pool]
    prices = [int(p.get("price") or 0) for p in pool]
    # Exact sums of the m cheapest prices within each suffix pool[i:], so a
    # branch dies as soon as even the cheapest possible completion busts the
    # budget — crucial when the budget binds tightly.
    suffix_cheapest: List[List[int]] = [[0] * (need + 1) for _ in range(n + 1)]
    tail: List[int] = []
    for i in range(n - 1, -1, -1):
        tail.append(prices[i])
        tail.sort()
        if len(tail) > need:
            tail.pop()
        sums = [0]
        for m in range(1, need + 1):
            sums.append(sums[-1] + (tail[m - 1] if m <= len(tail) else 0))
        suffix_cheapest[i] = sums

    base_cost = sum(int(p.get("price") or 0) for p in forced)
    base_score = float(bound_offset) + sum(float(bound_scores.get(int(p["player_id"]), 0.0)) for p in forced)
    base_counts: Dict[int, int] = {}
    for p in forced:
        tid = int(p.get("team_id") or 0)
        base_counts[tid] = base_counts.get(tid, 0) + 1
        if base_counts[tid] > max_per_team:
            return [], True
    if base_cost > budget:
        return [], True

    if role_scores_by_player is None:
        role_scores_by_player = {
            int(p["player_id"]): extract_role_scores_for_player(get_player(int(p["player_id"])) or {})
            for p in players_info
        }
    players_meta = {str(p["player_id"]): p for p in players_info}

    heap: List = []  # (true_score, tiebreak, chosen_players)
    counter = 0
    nodes = 0
    deadline = time.monotonic() + max(1.0, float(time_budget_seconds))

    class _TimeUp(Exception):
        pass

    def best_m_from(i: int, m: int) -> float:
        return sum(scores[i : i + m])

    def dfs(start: int, chosen: List[Dict[str, Any]], cost: int, score: float, counts: Dict[int, int]):
        nonlocal counter, nodes
        nodes += 1
        if nodes % 4096 == 0 and time.monotonic() > deadline:
            raise _TimeUp()
        remaining = need - len(chosen)
        if remaining == 0:
            roster = forced + chosen
            pids = [int(p["player_id"]) for p in roster]
            true_score = float(true_score_fn(roster)) if true_score_fn else score
            if len(heap) >= k and true_score <= heap[0][0]:
                return
            counter += 1
            entry = (true_score, counter, roster)
            if len(heap) < k:
                heapq.heappush(heap, entry)
            else:
                heapq.heappushpop(heap, entry)
            return
        for i in range(start, n - remaining + 1):
            bound = score + best_m_from(i, remaining)
            if len(heap) >= k and bound <= heap[0][0]:
                return  # pool sorted desc: no later branch can beat the bar
            p = pool[i]
            price = prices[i]
            if cost + price + suffix_cheapest[i + 1][remaining - 1] > budget:
                continue
            tid = int(p.get("team_id") or 0)
            if counts.get(tid, 0) >= max_per_team:
                continue
            counts[tid] = counts.get(tid, 0) + 1
            chosen.append(p)
            dfs(i + 1, chosen, cost + price, score + scores[i], counts)
            chosen.pop()
            counts[tid] -= 1

    exact = True
    try:
        dfs(0, [], base_cost, base_score, dict(base_counts))
    except _TimeUp:
        exact = False

    out = []
    for true_score, _tie, roster in sorted(heap, key=lambda e: -e[0]):
        pids = [int(p["player_id"]) for p in roster]
        # The search bound gives every player their BEST role; the roster's
        # real role points come from the exact clash-free assignment (each
        # role once, weighted by the team's expected match count), the same
        # one the stored best-team run uses. Re-score each contender with it
        # so the listed roles, per-player role EV and the roster EV agree.
        _role_total, role_of, role_ev_of = _exact_role_assignment(roster, role_scores_by_player)
        roles = [str(role_of.get(pid, "-")) for pid in pids]
        bound_total = sum(float(p.get("total_ev") or 0.0) for p in roster)
        cost = sum(int(p.get("price") or 0) for p in roster)
        serialized = serialize_roster(players_meta, pids, roles, bound_total, cost)
        adjusted_total = 0.0
        for player in serialized.get("players") or []:
            pid = int(player["player_id"])
            new_role_ev = role_ev_of.get(pid)
            if new_role_ev is not None:
                old_role_ev = float(player.get("role_ev") or 0.0)
                player["role_ev"] = float(new_role_ev)
                player["total_ev"] = float(player.get("total_ev") or 0.0) - old_role_ev + float(new_role_ev)
            player["mode_score"] = float(player.get("total_ev") or 0.0)
            adjusted_total += float(player.get("total_ev") or 0.0)
        serialized["total_ev"] = float(adjusted_total)
        serialized["average_ev"] = float(adjusted_total)
        serialized["mode_metric"] = float(true_score)
        out.append(serialized)
    return out, exact


def _live_ceiling_scorer(model: tuple, players_info: List[Dict[str, Any]]):
    """(per-player bound = their best outcome, roster ceiling function) over
    the outcome model — per independent group the best outcome for the
    roster's players there, summed."""
    import numpy as np

    vectors, _probs, group_of_player = model[0], model[1], model[2]

    def true_ceiling(roster: List[Dict[str, Any]]) -> float:
        by_group: Dict[int, List[int]] = {}
        for p in roster:
            pid = int(p["player_id"])
            g = group_of_player.get(pid)
            if g is not None:
                by_group.setdefault(g, []).append(pid)
        total = 0.0
        for g, pids in by_group.items():
            vecs = [vectors[g][pid] for pid in pids if pid in vectors.get(g, {})]
            if vecs:
                total += float(np.sum(vecs, axis=0).max())
        return total

    bound_scores = {}
    for p in players_info:
        pid = int(p["player_id"])
        g = group_of_player.get(pid)
        vec = (vectors.get(g) or {}).get(pid) if g is not None else None
        bound_scores[pid] = float(np.max(vec)) if vec is not None and len(vec) else float(p.get("total_ev") or 0.0)
    return bound_scores, true_ceiling


def _dense_players(model: tuple, players_info: List[Dict[str, Any]]) -> tuple:
    """(players, pids, prices, dense team indices, score matrix players × outcomes)
    for the players present in a single-axis outcome model."""
    import numpy as np

    vectors, _probs, _group_of = model[0], model[1], model[2]
    gv = vectors[0]
    players = [p for p in players_info if int(p["player_id"]) in gv]
    pids = [int(p["player_id"]) for p in players]
    prices = np.asarray([int(p.get("price") or 0) for p in players], dtype=np.int64)
    teams_raw = [int(p.get("team_id") or 0) for p in players]
    tmap = {t: i for i, t in enumerate(sorted(set(teams_raw)))}
    team_of = np.asarray([tmap[t] for t in teams_raw], dtype=np.int64)
    M = np.stack([gv[pid] for pid in pids]) if pids else np.zeros((0, 0), dtype=np.float64)
    return players, pids, prices, team_of, M


def _dense_decomposition(model: tuple, pids: List[int]) -> Optional[tuple]:
    """(RW, MP, MR) matrices players × outcomes for these pids, or None when
    the model carries no decomposition."""
    import numpy as np

    decomp = model[3] if len(model) > 3 else None
    if not decomp or not pids or any(pid not in decomp["rw"] for pid in pids):
        return None
    RW = np.stack([decomp["rw"][pid] for pid in pids])
    MP = np.stack([decomp["mp"][pid] for pid in pids])
    MR = np.stack([decomp["mr"][pid] for pid in pids])
    return RW, MP, MR


def _plan_scored_rosters(
    keys: List[tuple], model: tuple, players_info: List[Dict[str, Any]], players_meta: Dict[str, Dict[str, Any]],
    reach_by_team, rates_by_pid, role_scores_by_pid, progress_callback=None,
) -> Optional[List[Dict[str, Any]]]:
    """Serialised rosters for the candidate keys with ceiling / most-likely
    metrics computed under each roster's own plan (exact roles + roster-wide
    boosters) over the model's outcomes; None when the model has no
    decomposition (callers then keep the per-player scoring)."""
    from backend.services.roster_plan import plan_metrics

    players, pids, _prices, _team_of, _M = _dense_players(model, players_info)
    dec = _dense_decomposition(model, pids)
    if dec is None or not keys:
        return None
    RW, MP, MR = dec
    probs = model[1][0]
    pid_idx = {pid: i for i, pid in enumerate(pids)}
    keys = [k for k in keys if all(int(pid) in pid_idx for pid in k)]
    metrics = plan_metrics(keys, players_meta, pid_idx, RW, MP, MR, probs, reach_by_team, rates_by_pid, role_scores_by_pid, progress_callback=progress_callback)
    out = []
    for key, m in zip(keys, metrics):
        r = _score_roster_exact(list(key), players_meta, reach_by_team, rates_by_pid, role_scores_by_pid)
        r["ceiling_points"] = float(m["ceiling"])
        r["ceiling_probability"] = float(m["ceiling_p"])
        r["outcome_win_probability"] = float(m["wins_prob"])
        r["outcome_wins"] = float(m["wins_count"])
        for player in r.get("players") or []:
            pid = int(player.get("player_id") or 0)
            player["ceiling_score"] = float(m["peak"].get(pid, 0.0))
            player["mode_score"] = float(m["peak"].get(pid, 0.0))
        out.append(r)
    return out


def _forced_mask(pids: List[int], options: Dict[str, Any]):
    """Boolean mask of the included (forced) players, or None when an included
    player is not in the pool (no legal roster then)."""
    import numpy as np

    idx = {pid: i for i, pid in enumerate(pids)}
    forced = np.zeros(len(pids), dtype=np.bool_)
    for pid in options.get("include") or ():
        if int(pid) not in idx:
            return None
        forced[idx[int(pid)]] = True
    return forced


def _joint_winners(model: tuple, players_info: List[Dict[str, Any]], options: Dict[str, Any]) -> Dict[tuple, int]:
    """Best legal roster in EVERY sampled outcome under the query's budget,
    team cap and include / exclude (excluded players are already out of
    players_info), tallied by roster — the compiled search does 100k outcomes
    in ~0.2 s, so the most-likely-winner shares are exact within the sample
    for any filter."""
    players, pids, prices, team_of, M = _dense_players(model, players_info)
    if len(pids) < 5:
        return {}
    forced = _forced_mask(pids, options)
    if forced is None:
        return {}
    best = rk.best_rosters_batch(M, prices, team_of, int(options["budget"]), int(options["max_per_team"]), forced=forced)
    wins: Dict[tuple, int] = {}
    for row in best:
        if row[0] < 0:
            continue
        key = tuple(sorted(int(pids[j]) for j in row))
        wins[key] = wins.get(key, 0) + 1
    return wins


def _finalize_joint_precompute(result: dict, event_key: int) -> Optional[tuple]:
    """After a combined valuation: sample the joint outcomes once (the caller
    warms the query cache with the model) and run the compiled winner search
    once so its kernels are compiled before the first query. No-op for
    groups-only runs."""
    po = result.get("playoff") or {}
    if not (po.get("pairing_table") and po.get("win_probs")):
        return None
    model = _sample_joint_outcomes(result, _JOINT_SAMPLES)
    players_info = _groups_players_info(result, set())
    if len(players_info) >= 5:
        _joint_winners(model, players_info, parse_optimizer_payload({}))
    result.pop("joint_winners", None)
    return model


_PLAN_WINNER_CANDIDATES = 1200  # outcome-topping rosters re-scored under their plans for Most Likely Winner (~4 ms each at 100k outcomes)
_CEILING_MAX_SAMPLES = 10000  # strongest sampled outcomes searched for the ceiling mode (~0.1 ms each, compiled)
_CEILING_PER_SAMPLE = 25  # best rosters kept from each searched outcome


def _rosters_by_outcome(
    model: tuple,
    players_info: List[Dict[str, Any]],
    options: Dict[str, Any],
    serialize,
    k: int,
    per_sample: int = _CEILING_PER_SAMPLE,
    max_samples: int = _CEILING_MAX_SAMPLES,
    time_budget_seconds: float = 6.0,
) -> tuple:
    """Strongest rosters of the strongest sampled joint outcomes (single-axis
    models). Outcomes are visited by an upper bound (five best adjusted scores
    s − λ·price plus λ·budget, minimised over a λ grid — valid for any λ ≥ 0,
    and far tighter than the plain five-best sum when the best rosters spend
    the whole budget); inside each the compiled search returns the exact top
    rosters for that outcome (scores are additive there). Every roster found
    gets its true ceiling (its best outcome over ALL samples). Returns
    (rosters serialised by `serialize(pids)` sorted by ceiling, certified): the
    top `certified` rosters are provably the best over the sample — no
    unvisited outcome's bound reaches their ceilings."""
    import heapq
    import numpy as np

    players, pids, prices_arr, team_of, M = _dense_players(model, players_info)
    if len(pids) < 5:
        return [], 0
    forced = _forced_mask(pids, options)
    if forced is None:
        return [], 0
    budget = int(options.get("budget") or 0)
    cap = int(options.get("max_per_team") or 5)
    prices = prices_arr.astype(np.float64)
    sample_bound = np.partition(M, -5, axis=0)[-5:].sum(axis=0)
    if budget > 0 and prices.max() > 0:
        max_ratio = float(np.max(M.max(axis=1) / np.maximum(prices, 1.0)))
        for lam in max_ratio * np.array([0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.55, 0.7, 0.85, 1.0]):
            adj = M - lam * prices[:, None]
            sample_bound = np.minimum(sample_bound, np.partition(adj, -5, axis=0)[-5:].sum(axis=0) + lam * budget)
    order = np.argsort(-sample_bound)
    # best score seen per roster over the visited outcomes: a lower bound on
    # its ceiling that becomes exact at the outcome where it peaks (visited
    # first, since outcomes are taken in bound order)
    best_seen: Dict[tuple, float] = {}
    rows_of: Dict[tuple, List[int]] = {}
    kth = -1e300
    next_bound = 0.0  # bound of the first outcome NOT visited (0 when all were)
    deadline = time.monotonic() + max(1.0, float(time_budget_seconds))
    for pos, i in enumerate(order[:max_samples]):
        if pos % 128 == 0:
            if len(best_seen) >= k:
                kth = float(np.partition(np.fromiter(best_seen.values(), dtype=np.float64), -k)[-k])
                if float(sample_bound[i]) <= kth + 1e-9:
                    next_bound = float(sample_bound[i])
                    break
            if time.monotonic() > deadline:
                next_bound = float(sample_bound[i])
                break
        for score_i, roster_idx in rk.top_rosters_one(M[:, i], prices_arr, team_of, budget, cap, per_sample, forced):
            rows = [int(j) for j in roster_idx]
            key = tuple(sorted(int(pids[j]) for j in rows))
            if score_i > best_seen.get(key, -1e300):
                best_seen[key] = float(score_i)
                rows_of[key] = rows
    else:
        next_bound = float(sample_bound[order[max_samples]]) if max_samples < len(order) else 0.0
    ranked_keys = sorted(best_seen, key=lambda kk: -best_seen[kk])[: max(k, 1)]
    # exact ceilings for the page (max over ALL samples), then the final order
    exact_ceiling = {key: float(M[rows_of[key]].sum(axis=0).max()) for key in ranked_keys}
    ranked_keys.sort(key=lambda kk: -exact_ceiling[kk])
    rosters = [serialize(list(key)) for key in ranked_keys]
    certified = sum(1 for key in ranked_keys if exact_ceiling[key] >= next_bound - 1e-9)
    return rosters, certified


def _attach_ceiling_details(team: Dict[str, Any], model: tuple) -> None:
    """ceiling_points, the probability of that outcome, and each player's score
    in it (mode_score / ceiling_score), from the roster's best outcome per group."""
    import numpy as np

    vectors, probs, group_of_player = model[0], model[1], model[2]
    pids = [int(p.get("player_id") or 0) for p in (team.get("players") or [])]
    by_group: Dict[int, List[int]] = {}
    for pid in pids:
        g = group_of_player.get(pid)
        if g is not None:
            by_group.setdefault(g, []).append(pid)
    total = 0.0
    prob = 1.0
    per_player: Dict[int, float] = {}
    for g, gp in by_group.items():
        vecs = [vectors[g][pid] for pid in gp if pid in vectors.get(g, {})]
        if not vecs:
            continue
        sums = np.sum(vecs, axis=0)
        idx = int(np.argmax(sums))
        total += float(sums[idx])
        prob *= float(probs[g][idx]) if g in probs and idx < len(probs[g]) else 1.0
        for pid in gp:
            v = vectors[g].get(pid)
            if v is not None:
                per_player[pid] = float(v[idx])
    team["ceiling_points"] = float(total)
    team["ceiling_probability"] = float(prob if by_group else 0.0)
    for player in team.get("players") or []:
        pid = int(player.get("player_id") or 0)
        if pid in per_player:
            player["ceiling_score"] = per_player[pid]
            player["mode_score"] = per_player[pid]


def _booster_prerequisites(
    results: dict, players_info: List[Dict[str, Any]], options: Dict[str, Any], reach_by_team: Optional[Dict[int, Dict[int, float]]] = None
) -> tuple:
    """Whole-event match-reach odds per team, each player's parsed booster
    trigger rates and role scores, and two per-player booster bounds set on
    each player: `booster_ub`, the plain ceiling (safe for pool pruning), and
    `booster_ub_tight`, the Lagrangian bound whose roster-wide constant is
    returned as bound_offset (bound = Σ total_ev + Σ booster_ub_tight + offset).
    Returns (reach_by_team, rates_by_pid, role_scores_by_pid, bound_offset)."""
    if reach_by_team is None:
        reach_by_team = _team_match_reach_probs(results)
    rates_by_pid: Dict[int, Dict[int, float]] = {}
    role_scores_by_pid: Dict[int, Dict[int, float]] = {}
    for p in players_info:
        pid = int(p["player_id"])
        row = _snapshot_player_row(results, pid) or {}
        rates_by_pid[pid] = parse_booster_rates(row.get("boosters_json"))
        role_scores_by_pid[pid] = extract_role_scores_for_player(row)
        p["booster_ub"] = _player_booster_ub(int(p.get("team_id", 0)), reach_by_team, rates_by_pid[pid])
    # Reference rosters for the price tuning: the strongest by the plain bound.
    loose = {int(p["player_id"]): float(p.get("total_ev") or 0.0) + float(p.get("booster_ub") or 0.0) for p in players_info}
    refs, _exact = _topk_rosters_bnb(
        players_info, loose, 20, options["budget"], options["max_per_team"], options["include"], None,
        time_budget_seconds=2.0,
    )
    meta = {str(p["player_id"]): p for p in players_info}
    ref_rosters = [[meta[str(p["player_id"])] for p in (r.get("players") or [])] for r in refs]
    tight, bound_offset = _booster_bound_lagrangian(players_info, reach_by_team, rates_by_pid, ref_rosters)
    for p in players_info:
        p["booster_ub_tight"] = float(tight.get(int(p["player_id"]), p["booster_ub"]))
    return reach_by_team, rates_by_pid, role_scores_by_pid, bound_offset


def _run_groups_best_team_job(job_id: str, payload: dict) -> None:
    def _update(processed: int, total: int) -> None:
        with GROUPS_BEST_TEAM_JOBS_LOCK:
            job = GROUPS_BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["processed_combinations"] = int(processed)
            job["total_combinations"] = int(total)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["updated_at"] = time.time()

    with GROUPS_BEST_TEAM_JOBS_LOCK:
        job = GROUPS_BEST_TEAM_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = time.time()
    try:
        state_key = _state_key()
        latest = _GROUPS_STATE.load(key=state_key)
        if not latest:
            raise HTTPException(status_code=404, detail="No stored group stage found. Run the groups simulation first.")
        results = latest["results"] or {}
        options = parse_optimizer_payload(payload or {})
        players_info = _groups_players_info(results, options["exclude"])
        if len(players_info) < 5:
            raise HTTPException(status_code=400, detail="Not enough players after exclusions")

        # Booster prerequisites: exact per-team match-reach probabilities from
        # the enumerated outcomes, each player's parsed trigger rates, and a
        # per-player booster upper bound (used for pruning + ranking before the
        # expensive exact assignment).
        reach_by_team, rates_by_pid, role_scores_by_pid, _bound_offset = _booster_prerequisites(results, players_info, options)

        # For large combined pools, prune players that can never be in an optimal
        # roster before the C(N,5) enumeration — keeps big events tractable while
        # provably retaining the best teams (booster ceiling included).
        pool_reduced_from = len(players_info)
        pool_reduced_to = pool_reduced_from
        if math.comb(len(players_info), 5) > _POOL_REDUCE_COMBO_THRESHOLD:
            players_info = _reduce_player_pool(players_info, options["include"], options["max_per_team"])
            pool_reduced_to = len(players_info)
            with GROUPS_BEST_TEAM_JOBS_LOCK:
                job2 = GROUPS_BEST_TEAM_JOBS.get(job_id)
                if job2:
                    job2["pool_reduced_from"] = pool_reduced_from
                    job2["pool_reduced_to"] = pool_reduced_to
        # Outcome model for the ceiling / most-likely modes: exact group
        # outcomes, or sampled joint outcomes (groups + playoffs) when combined.
        model = _event_outcome_model(results, (state_key, latest["updated_at"]))
        players_meta = {str(p["player_id"]): p for p in players_info}

        # Phase 1: enumerate all valid rosters, ranking each by an admissible
        # upper bound (rating+win+role + the per-player booster ceiling). Keep
        # only the top contenders in a bounded heap — the exact booster
        # assignment (a min-cost-flow) is far too slow to run on every combo.
        exact_topk = EXACT_BOOSTER_TOPK
        heap: List[tuple] = []
        counter = 0
        for roster in iter_valid_rosters(
            players_info, options["include"], options["budget"], options["max_per_team"], _update
        ):
            pids = [int(p) for p in roster["pids"]]
            ub = float(roster["total_ev"]) + sum(
                float(players_meta[str(pid)].get("booster_ub_tight", players_meta[str(pid)].get("booster_ub", 0.0)))
                for pid in pids
            )
            payload_r = {
                "pids": roster["pids"],
                "roles": roster["roles"],
                "cost": roster["cost"],
                "total_ev": roster["total_ev"],
            }
            counter += 1
            if len(heap) < exact_topk:
                heapq.heappush(heap, (ub, counter, payload_r))
            elif ub > heap[0][0]:
                heapq.heapreplace(heap, (ub, counter, payload_r))

        # Phase 2: exact booster AND exact (clash-free) role assignment for the
        # retained contenders. average_ev = rating+win + exact-role + exact-booster.
        valid_teams = []
        for _ub, _c, pr in sorted(heap, key=lambda x: -x[0]):
            pids = [int(p) for p in pr["pids"]]
            serialized = _score_roster_exact(pids, players_meta, reach_by_team, rates_by_pid, role_scores_by_pid)
            valid_teams.append(serialized)
        planned = _plan_scored_rosters(
            [tuple(sorted(int(p["player_id"]) for p in t["players"])) for t in valid_teams],
            model, players_info, players_meta, reach_by_team, rates_by_pid, role_scores_by_pid,
        )
        if planned is not None:
            valid_teams = planned
        else:
            for serialized in valid_teams:
                _attach_ceiling_details(serialized, model)
        valid_teams.sort(key=lambda team: float(team.get("average_ev") or 0.0), reverse=True)

        # Most-likely-winner: probability each roster is the single best pick
        # across the joint outcome space. Only the strongest-by-average teams can
        # realistically win, so score just the top contenders (rest stay 0).
        for team in valid_teams:
            team["outcome_win_probability"] = 0.0
        contenders = [] if planned is not None else valid_teams[:_WIN_PROB_CANDIDATES]
        if contenders:
            contender_pids = [[int(p.get("player_id") or 0) for p in (t.get("players") or [])] for t in contenders]
            win_probs = _compute_group_win_probs(contender_pids, model[0], model[2], model[1])
            for team, wp in zip(contenders, win_probs):
                team["outcome_win_probability"] = float(wp)

        result = {
            "top_teams": valid_teams[:10],
            "all_teams": valid_teams,
            "player_count": len(players_info),
            "processed_combinations": len(valid_teams),
            "total_combinations": int(job.get("total_combinations") or len(valid_teams)),
            "pool_reduced_from": pool_reduced_from,
            "pool_reduced_to": pool_reduced_to,
            "mode": "average",
        }
        _GROUPS_BEST_STATE.save(payload or {}, result, key=state_key)
        _GROUPS_BEST_META.save(
            payload or {},
            {
                "mode": "average",
                "player_count": len(players_info),
                "total_teams": len(valid_teams),
                "processed_combinations": result["processed_combinations"],
                "total_combinations": result["total_combinations"],
                "pool_reduced_from": pool_reduced_from,
                "pool_reduced_to": pool_reduced_to,
            },
            key=state_key,
        )
        result_slim = {k: v for k, v in result.items() if k != "all_teams"}
        with GROUPS_BEST_TEAM_JOBS_LOCK:
            job = GROUPS_BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["result"] = result_slim
            job["progress"] = 1.0
            job["updated_at"] = time.time()
    except Exception as exc:
        with GROUPS_BEST_TEAM_JOBS_LOCK:
            job = GROUPS_BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = time.time()


def _live_pool_size(results: dict) -> int:
    return sum(len(t.get("players") or {}) for t in (results.get("teams") or {}).values())


def _is_live_pool(results: dict) -> bool:
    return _live_pool_size(results) > LIVE_OPTIMIZER_PLAYER_THRESHOLD


@router.post("/best-team/start")
def start_groups_best_team(payload: dict | None = None):
    body = payload or {}
    latest = _GROUPS_STATE.load(key=_state_key())
    if not latest:
        raise HTTPException(status_code=404, detail="No stored group stage found. Run the groups simulation first.")
    if _is_live_pool(latest["results"] or {}):
        raise HTTPException(
            status_code=400,
            detail="This event is large enough that rosters are optimized live per query; no precompute is needed.",
        )
    with GROUPS_BEST_TEAM_JOBS_LOCK:
        for existing_id, existing in GROUPS_BEST_TEAM_JOBS.items():
            if existing.get("status") in {"queued", "running"}:
                return {"job_id": existing_id, "reused": True}
        job_id = uuid.uuid4().hex
        GROUPS_BEST_TEAM_JOBS[job_id] = {
            "status": "queued",
            "error": "",
            "progress": 0.0,
            "processed_combinations": 0,
            "total_combinations": 0,
            "result": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    threading.Thread(target=_run_groups_best_team_job, args=(job_id, body), daemon=True).start()
    return {"job_id": job_id}


@router.get("/best-team/job/{job_id}")
def get_groups_best_team_job(job_id: str):
    with GROUPS_BEST_TEAM_JOBS_LOCK:
        job = GROUPS_BEST_TEAM_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_id not found")
        out = dict(job)
    return {
        "job_id": job_id,
        "status": out.get("status", "queued"),
        "error": out.get("error", ""),
        "progress": out.get("progress", 0.0),
        "processed_combinations": out.get("processed_combinations", 0),
        "total_combinations": out.get("total_combinations", 0),
        "result": out.get("result"),
    }


@router.get("/best-team/latest")
def get_latest_groups_best_team():
    key = _state_key()
    latest_sim = _GROUPS_STATE.load(key=key)
    if latest_sim and _is_live_pool(latest_sim["results"] or {}):
        return {"exists": True, "live": True, "updated_at": latest_sim["updated_at"]}
    meta = _GROUPS_BEST_META.load(key=key)
    if not meta:
        return {"exists": False}
    summary = meta["result"] or {}
    return {
        "exists": True,
        "live": False,
        "payload": meta["payload"],
        "total_teams": summary.get("total_teams"),
        "pool_reduced_from": summary.get("pool_reduced_from"),
        "pool_reduced_to": summary.get("pool_reduced_to"),
        "updated_at": meta["updated_at"],
    }


def _live_groups_query(results: dict, body: dict, mode: str, cache_key: Any = None) -> Dict[str, Any]:
    options = parse_optimizer_payload(body)
    players_info = _groups_players_info(results, options["exclude"])
    if len(players_info) < 5:
        raise HTTPException(status_code=400, detail="Not enough players after exclusions")
    page = int(body.get("page") or 0)
    page_size = int(body.get("page_size") or 200)
    k = min(LIVE_OPTIMIZER_MAX_K, max(210, (page + 1) * page_size + 10))
    reach_by_team, rates_by_pid, role_scores_by_pid, bound_offset = _booster_prerequisites(results, players_info, options)
    players_meta = {str(p["player_id"]): p for p in players_info}
    # Admissible per-player bound for the average: rating+win+best role plus the
    # Lagrangian booster bound (with bound_offset the roster-wide constant); the
    # exact roster score never exceeds it.
    bound_scores = {
        int(p["player_id"]): float(p.get("total_ev") or 0.0) + float(p.get("booster_ub_tight") or 0.0)
        for p in players_info
    }

    def exact_score(roster: List[Dict[str, Any]]) -> float:
        # rating+win + exact clash-free roles + exact roster-wide boosters
        booster = optimize_group_boosters_for_roster(roster, reach_by_team, rates_by_pid)["total_expected_booster_points"]
        role_total, _role_of, _role_ev_of = _exact_role_assignment(roster, role_scores_by_pid)
        rating_win = sum(float(p.get("rating_ev", 0.0)) + float(p.get("win_ev", 0.0)) for p in roster)
        return rating_win + role_total + float(booster)

    def rescore(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            _score_roster_exact(
                [int(p["player_id"]) for p in (c.get("players") or [])],
                players_meta, reach_by_team, rates_by_pid, role_scores_by_pid,
            )
            for c in cands
        ]

    exact_candidates = 0
    certified_top = None  # how many of the top rosters are provably in order (ceiling mode)
    if mode == "single_outcome":
        model = _event_outcome_model(results, cache_key)
        if len(model[0]) == 1:
            # Sampled joint outcomes (or a single group): search the strongest
            # outcomes one by one — a per-player "best sample" bound across 20k
            # outcomes is far too loose for the plain branch-and-bound.
            cand_keys, certified_top = _rosters_by_outcome(model, players_info, options, lambda pids: tuple(pids), k)
            planned = _plan_scored_rosters(cand_keys, model, players_info, players_meta, reach_by_team, rates_by_pid, role_scores_by_pid)
            if planned is not None:
                # every candidate under its own plan: the ceiling is the best
                # outcome with the plan's roles and boosters, not the players' own
                planned.sort(key=lambda r: -float(r.get("ceiling_points") or 0.0))
                rosters = planned
                exact = True
            else:
                rosters = [_score_roster_exact(list(key), players_meta, reach_by_team, rates_by_pid, role_scores_by_pid) for key in cand_keys]
                for team in rosters:
                    _attach_ceiling_details(team, model)
                exact = certified_top >= min(k, len(rosters))
        else:
            ceiling_bounds, true_fn = _live_ceiling_scorer(model, players_info)
            rosters, exact = _topk_rosters_bnb(
                players_info, ceiling_bounds, k, options["budget"], options["max_per_team"], options["include"], true_fn,
                role_scores_by_player=role_scores_by_pid,
            )
            for team in rosters:
                _attach_ceiling_details(team, model)
        exact_candidates = len(rosters)
    elif mode == "most_outcomes":
        model = _event_outcome_model(results, cache_key)
        if len(model[0]) == 1:
            # One outcome axis (sampled joint outcomes, or a single group): the
            # compiled search finds the best legal roster in EVERY outcome under
            # the query's own budget, team cap and include / exclude, so the win
            # shares are exact within the sample and cover every roster.
            wins = _joint_winners(model, players_info, options)
            n_samples = float(len(model[1][0])) or 1.0
            ranked_keys = [key_r for key_r, _w in sorted(wins.items(), key=lambda kv: -kv[1])]
            # candidates: the rosters that top outcomes with their players' own
            # best roles and boosters (the strongest 2,000), plus the strongest by
            # plan average — then every candidate is re-scored under its plan
            cand_keys = ranked_keys[:_PLAN_WINNER_CANDIDATES]
            avg_cands, _ = _topk_rosters_bnb(
                players_info, bound_scores, _WIN_PROB_CANDIDATES, options["budget"], options["max_per_team"], options["include"],
                exact_score, bound_offset=bound_offset, role_scores_by_player=role_scores_by_pid,
            )
            have = set(cand_keys)
            for c in avg_cands:
                key_c = tuple(sorted(int(p["player_id"]) for p in c["players"]))
                if key_c not in have:
                    have.add(key_c)
                    cand_keys.append(key_c)
            planned = _plan_scored_rosters(cand_keys, model, players_info, players_meta, reach_by_team, rates_by_pid, role_scores_by_pid)
            if planned is not None:
                planned.sort(key=lambda r: (-float(r.get("outcome_win_probability") or 0.0), -float(r.get("average_ev") or 0.0)))
                rosters = planned
            else:
                rosters = []
                for key_r in ranked_keys:
                    r = _score_roster_exact(list(key_r), players_meta, reach_by_team, rates_by_pid, role_scores_by_pid)
                    r["outcome_win_probability"] = float(wins[key_r]) / n_samples
                    rosters.append(r)
            exact = True
            exact_candidates = len(rosters)
            certified_top = len(rosters)
        else:
            # Several independent exact groups: rank the strongest-by-bound
            # teams by their chance of being the single best pick; their
            # displayed average carries the exact booster + role solve.
            cand_k = max(k, _WIN_PROB_CANDIDATES)
            cands, exact = _topk_rosters_bnb(
                players_info, bound_scores, cand_k, options["budget"], options["max_per_team"], options["include"],
                exact_score, bound_offset=bound_offset,
            )
            rosters = rescore(cands)
            exact_candidates = len(rosters)
            team_pids = [[int(p.get("player_id") or 0) for p in (r.get("players") or [])] for r in rosters]
            win_probs = _compute_group_win_probs(team_pids, model[0], model[2], model[1])
            for r, wp in zip(rosters, win_probs):
                r["outcome_win_probability"] = float(wp)
    else:
        # Average: the search scores every roster it cannot prune with the
        # exact role + booster assignments (the Lagrangian bound keeps that
        # set small), so the top k is the true top k unless the time budget
        # runs out (exact=False, best found so far).
        cands, exact = _topk_rosters_bnb(
            players_info, bound_scores, k, options["budget"], options["max_per_team"], options["include"],
            exact_score, bound_offset=bound_offset,
        )
        rosters = rescore(cands)
        rosters.sort(key=lambda r: -float(r.get("average_ev") or 0.0))
        exact_candidates = len(rosters)
    if certified_top is None:
        certified_top = len(rosters) if exact else 0
    search = str(body.get("search") or "")
    filtered = _filter_saved_combo_teams(rosters, set(), set(), search)
    sorted_teams = _sort_saved_combo_teams(filtered, mode, str(body.get("sort") or "ev_desc"))
    return {
        "exists": True,
        "live": True,
        "exact": exact,
        "exact_candidates": exact_candidates,
        "certified_top": certified_top,
        "mode": mode,
        "total_teams": len(rosters),
        "filtered_count": len(sorted_teams),
        "top_teams": sorted_teams[:10],
        "page_teams": _page_items(sorted_teams, page, page_size),
        "page": max(0, page),
        "page_size": max(1, min(500, page_size)),
    }


@router.post("/best-team/query")
def query_groups_best_team(payload: dict | None = None):
    body = payload or {}
    mode = str(body.get("mode") or "average").strip().lower()
    if mode not in {"average", "single_outcome", "most_outcomes"}:
        mode = "average"
    # body.event_id queries another event's stored run (tests / tools); the
    # app itself always asks for the active event.
    key = int(body.get("event_id") or 0) or _state_key()
    latest_sim = _GROUPS_STATE.load(key=key)
    if latest_sim and _is_live_pool(latest_sim["results"] or {}):
        # The live optimizer costs ~0.5 s per query; identical queries against
        # the same stored run (tab re-opens, paging back) come from a small cache.
        cache_key = (key, latest_sim["updated_at"], mode, json.dumps(body, sort_keys=True, default=str))
        with _LIVE_QUERY_CACHE_LOCK:
            cached = _LIVE_QUERY_CACHE.get(cache_key)
        if cached is not None:
            return cached
        result = _live_groups_query(latest_sim["results"] or {}, body, mode, cache_key=(key, latest_sim["updated_at"]))
        result["updated_at"] = latest_sim["updated_at"]
        with _LIVE_QUERY_CACHE_LOCK:
            if len(_LIVE_QUERY_CACHE) >= 32:
                _LIVE_QUERY_CACHE.pop(next(iter(_LIVE_QUERY_CACHE)))
            _LIVE_QUERY_CACHE[cache_key] = result
        return result
    latest = _GROUPS_BEST_STATE.load(key=key)
    if not latest:
        raise HTTPException(status_code=404, detail="No stored combinations found. Run Combinations first.")
    options = parse_optimizer_payload(body)
    teams = list((latest["result"] or {}).get("all_teams") or [])
    filtered = _filter_saved_combo_teams(teams, options["include"], options["exclude"], str(body.get("search") or ""))
    sorted_teams = _sort_saved_combo_teams(filtered, mode, str(body.get("sort") or "ev_desc"))
    page = int(body.get("page") or 0)
    page_size = int(body.get("page_size") or 200)
    return {
        "exists": True,
        "live": False,
        "mode": mode,
        "updated_at": latest["updated_at"],
        "total_teams": len(teams),
        "filtered_count": len(sorted_teams),
        "top_teams": sorted_teams[:10],
        "page_teams": _page_items(sorted_teams, page, page_size),
        "page": max(0, page),
        "page_size": max(1, min(500, page_size)),
    }


def _find_completed_group_outcomes(results: dict, picks_by_group: List[List[int]]) -> List[Dict[str, Any]]:
    outcomes = results.get("outcomes") or []
    match_count = 10 if str(results.get("group_format") or "") == "de8" else 5
    selected: List[Dict[str, Any]] = []
    for g_idx, winners in enumerate(picks_by_group):
        want = [int(w) for w in winners]
        if len(want) != match_count or any(w <= 0 for w in want):
            raise HTTPException(status_code=400, detail=f"Group {g_idx + 1}: pick all {match_count} match winners first")
        match = None
        for outcome in outcomes:
            group_val = outcome.get("group")
            if group_val is None or int(group_val) != g_idx:
                continue
            got = [int(m.get("winner") or 0) for m in (outcome.get("matches") or [])]
            if got == want:
                match = outcome
                break
        if not match:
            raise HTTPException(status_code=404, detail=f"Group {g_idx + 1}: no stored outcome matches those winners")
        selected.append(match)
    return selected


@router.post("/best-team/completed-query")
def query_groups_best_team_completed(payload: dict | None = None):
    body = payload or {}
    key = _state_key()
    latest = _GROUPS_STATE.load(key=key)
    if not latest:
        raise HTTPException(status_code=404, detail="No stored group stage found.")
    results = latest["results"] or {}
    live_pool = _is_live_pool(results)
    latest_combos = None if live_pool else _GROUPS_BEST_STATE.load(key=key)
    if not live_pool and not latest_combos:
        raise HTTPException(status_code=404, detail="No stored combinations found. Run Combinations first.")
    picks_by_group = body.get("group_winners") or []
    selected = _find_completed_group_outcomes(results, picks_by_group)
    scores_by_pid: Dict[int, float] = {}
    components_by_pid: Dict[int, dict] = {}
    probability = 1.0
    for outcome in selected:
        probability *= float(outcome.get("probability") or 0.0)
        for pid_raw, score in (outcome.get("players") or {}).items():
            scores_by_pid[int(pid_raw)] = float(score)
        for pid_raw, comps in (outcome.get("player_components") or {}).items():
            components_by_pid[int(pid_raw)] = dict(comps or {})
    options = parse_optimizer_payload(body)
    player_values = []
    for pid, score in scores_by_pid.items():
        row = get_player(pid) or {}
        comps = components_by_pid.get(pid) or {}
        player_values.append(
            {
                "player_id": pid,
                "name": row.get("name", f"Player {pid}"),
                "team_id": 0,
                "price": int(row.get("price") or 0),
                "points": float(score),
                "rating": float(comps.get("rating", 0.0) or 0.0),
                "win": float(comps.get("win", 0.0) or 0.0),
                "role": float(comps.get("role", 0.0) or 0.0),
                "booster": float(comps.get("booster", 0.0) or 0.0),
                "components_available": bool(comps),
            }
        )
    page = int(body.get("page") or 0)
    page_size = int(body.get("page_size") or 200)
    if live_pool:
        # Optimize directly against the realized scores; exact, since the
        # objective is additive per player.
        players_info = _groups_players_info(results, options["exclude"])
        bound_scores = {int(p["player_id"]): float(scores_by_pid.get(int(p["player_id"]), 0.0)) for p in players_info}
        k = min(LIVE_OPTIMIZER_MAX_K, max(210, (page + 1) * page_size + 10))
        rosters, _exact = _topk_rosters_bnb(
            players_info, bound_scores, k, options["budget"], options["max_per_team"], options["include"], None
        )
        scored = []
        for team in rosters:
            players = []
            total = 0.0
            for player in team.get("players") or []:
                pid = int(player.get("player_id") or 0)
                score = float(scores_by_pid.get(pid, 0.0))
                total += score
                players.append({**player, "mode_score": score, "total_ev": score})
            scored.append({**team, "players": players, "total_ev": total, "bracket_score": total})
        scored = _filter_saved_combo_teams(scored, set(), set(), str(body.get("search") or ""))
        scored.sort(key=lambda team: float(team.get("bracket_score") or 0.0), reverse=True)
        combos_total = len(rosters)
        combos_updated_at = latest["updated_at"]
    else:
        teams = list((latest_combos["result"] or {}).get("all_teams") or [])
        filtered = _filter_saved_combo_teams(teams, options["include"], options["exclude"], str(body.get("search") or ""))
        scored = []
        for team in filtered:
            players = []
            total = 0.0
            for player in team.get("players") or []:
                pid = int(player.get("player_id") or 0)
                score = float(scores_by_pid.get(pid, 0.0))
                total += score
                players.append({**player, "mode_score": score, "total_ev": score})
            scored.append({**team, "players": players, "total_ev": total, "bracket_score": total})
        scored.sort(key=lambda team: float(team.get("bracket_score") or 0.0), reverse=True)
        combos_total = len(teams)
        combos_updated_at = latest_combos["updated_at"]
    return {
        "exists": True,
        "live": live_pool,
        "mode": "completed_groups",
        "updated_at": combos_updated_at,
        "outcome_probability": probability,
        "outcomes_count": int(results.get("outcomes_count") or 0),
        "player_values": sorted(player_values, key=lambda row: float(row.get("points") or 0.0), reverse=True),
        "total_teams": combos_total,
        "filtered_count": len(scored),
        "top_teams": scored[:10],
        "page_teams": _page_items(scored, page, page_size),
        "page": max(0, page),
        "page_size": max(1, min(500, page_size)),
    }
