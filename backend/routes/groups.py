"""Double-elimination (GSL) group stage: X groups of 4, two qualify, two out.

Each group plays five BO3s — two opening matches, winners' match, elimination
match, decider — giving exactly 32 outcomes per group. Groups are independent,
so player expectations are exact per group and roster metrics that decompose
per group (average EV, ceiling) stay exact for any number of groups.
"""

import heapq
import math
import random
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend.data.db import connect as _connect
from backend.data.player_db import get_player
from backend.data.singleton_state import SingletonState
from backend.routes.playoff import (
    _build_playoff_lookup_context,
    _clone_team_states,
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
from backend.swiss_stage.fantasy_scoring import compute_padding_components
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

_GROUPS_STATE = SingletonState("groups_simulation_state", result_column="results_json", result_key="results")
_GROUPS_BEST_STATE = SingletonState("groups_best_team_state")
_GROUPS_BEST_META = SingletonState("groups_best_team_meta")

GROUP_MATCH_KEYS = ["opening_1", "opening_2", "winners", "elimination", "decider"]
GROUP_MATCH_LABELS = {
    "opening_1": "Opening 1",
    "opening_2": "Opening 2",
    "winners": "Winners' match",
    "elimination": "Elimination match",
    "decider": "Decider",
}


def ensure_groups_schema() -> None:
    for state in (_GROUPS_STATE, _GROUPS_BEST_STATE, _GROUPS_BEST_META):
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
    n_sims = int(payload.get("n_playoff_sims") or 2000)
    return {
        "groups": groups,
        "group_format": group_format,
        "combined_playoffs": combined,
        "n_playoff_sims": max(200, min(20000, n_sims)),
        "playoff_stop_teams": stop_teams,
    }


def _enumerate_group_outcomes(
    team_ids: List[int],
    group_index: int,
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    prob_cache: Dict,
    extra_rounds: int = 0,
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
                                                        }
                                                    )
    return outcomes


def _simulate_combined_playoffs(
    groups: List[List[int]],
    outcomes: List[Dict[str, Any]],
    n_sims: int,
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    prob_cache: Dict,
    stop_teams: int = 1,
    quals_per_group: int = 2,
    progress_callback=None,
) -> Dict[str, Any]:
    """Monte Carlo playoff stage: sample each group's exact outcome, seed the
    qualifiers into a single-elimination bracket (standard 1-vs-N seeding that
    spreads same-group teams apart), and average per-player playoff points.

    stop_teams ends the bracket early: with 32 teams and stop_teams=4, the
    quarter-finals are the last matches played and the 4 winners qualify
    without playing on (e.g. a qualifier feeding a main event).
    """
    x = len(groups)
    stop_teams = max(1, int(stop_teams))
    byes_to_semis = quals_per_group == 3
    if byes_to_semis:
        # Porto/Cologne 6-team bracket: group winners bye to the semis.
        if x != 2:
            raise ValueError("The top-3 combined playoff supports exactly 2 groups (6-team bracket)")
        if stop_teams != 1:
            raise ValueError("The top-3 combined playoff plays the full bracket (stop_teams must be 1)")
        bracket_size = 6
        rounds_total = 3
    else:
        bracket_size = quals_per_group * x
        rounds_total = max(1, int(math.log2(bracket_size)) - int(math.log2(stop_teams)))
    rng = random.Random(1234567)

    # Per-group sampling tables over the 32 exact outcomes.
    per_group: List[List[Dict[str, Any]]] = [[] for _ in range(x)]
    for outcome in outcomes:
        per_group[int(outcome["group"])].append(outcome)
    cumulative: List[List[float]] = []
    for g in range(x):
        acc = 0.0
        cums = []
        for outcome in per_group[g]:
            acc += float(outcome["probability"])
            cums.append(acc)
        cumulative.append(cums)

    all_team_ids = [tid for group in groups for tid in group]
    base_states = initialize_teams(all_team_ids, {tid: 999 for tid in all_team_ids})

    accum: Dict[int, Dict[str, float]] = {}
    champion_counts: Dict[int, int] = {}

    def sample_group(g: int) -> Dict[str, Any]:
        draw = rng.random() * (cumulative[g][-1] if cumulative[g] else 1.0)
        idx = 0
        cums = cumulative[g]
        lo, hi = 0, len(cums) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cums[mid] < draw:
                lo = mid + 1
            else:
                hi = mid
        idx = lo
        return per_group[g][idx]

    def match_prob(a: int, b: int) -> float:
        key = (a, b)
        if key in prob_cache:
            return prob_cache[key]
        from backend.services.match_engine import calculate_win_probability

        p = calculate_win_probability(a, b, "bo3")
        prob_cache[key] = p
        prob_cache[(b, a)] = 1.0 - p
        return p

    for sim in range(n_sims):
        sampled = [sample_group(g) for g in range(x)]
        if byes_to_semis:
            # qualified is ordered [1st, 2nd, 3rd]; 1sts skip the quarters.
            qa = [int(t) for t in sampled[0]["qualified"]]
            qb = [int(t) for t in sampled[1]["qualified"]]
            playoff_team_ids = qa + qb
            states = _clone_team_states({tid: base_states[tid] for tid in playoff_team_ids})

            def play_po(a, b, remaining_after):
                p = match_prob(a, b)
                winner = a if rng.random() < p else b
                _play_match_deterministic(
                    states, a, b, winner, remaining_rounds_after=remaining_after, prob_cache=prob_cache,
                    player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
                )
                return winner

            wq1 = play_po(qa[1], qb[2], 2)  # QF1: A-2nd vs B-3rd
            wq2 = play_po(qb[1], qa[2], 2)  # QF2: B-2nd vs A-3rd
            ws1 = play_po(qa[0], wq2, 1)    # SF1: A-1st vs QF2 winner
            ws2 = play_po(qb[0], wq1, 1)    # SF2: B-1st vs QF1 winner
            final_winners = [play_po(ws1, ws2, 0)]
        else:
            # Flatten qualifiers seed-major (all group winners, then all seed-2, ...)
            # then standard i vs (N-1-i) seeding, which puts same-group teams on
            # opposite ends of the bracket.
            seeded: List[int] = []
            for seed_idx in range(quals_per_group):
                for g in range(x):
                    q = sampled[g]["qualified"]
                    if seed_idx < len(q):
                        seeded.append(int(q[seed_idx]))
            n_seeded = len(seeded)
            pairs: List[tuple] = [(seeded[i], seeded[n_seeded - 1 - i]) for i in range(n_seeded // 2)]
            playoff_team_ids = [tid for pair in pairs for tid in pair]
            states = _clone_team_states({tid: base_states[tid] for tid in playoff_team_ids})
            current = pairs
            final_winners = []
            for round_idx in range(rounds_total):
                remaining_after = rounds_total - round_idx - 1
                winners: List[int] = []
                for a, b in current:
                    p = match_prob(a, b)
                    winner = a if rng.random() < p else b
                    _play_match_deterministic(
                        states, a, b, winner, remaining_rounds_after=remaining_after, prob_cache=prob_cache,
                        player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
                    )
                    winners.append(winner)
                final_winners = winners
                if len(winners) > 1:
                    current = [(winners[i], winners[i + 1]) for i in range(0, len(winners), 2)]
        for tid in final_winners:
            champion_counts[tid] = champion_counts.get(tid, 0) + 1
        for ts in states.values():
            for pid, p in ts.players.items():
                bucket = accum.setdefault(
                    int(pid), {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0}
                )
                bucket["total"] += float(p.total_points)
                bucket["rating"] += float(p.rating_points_total)
                bucket["win"] += float(p.win_points_total)
                bucket["role"] += float(p.role_points_total)
                bucket["booster"] += float(p.booster_points_total)
        if progress_callback and (sim + 1) % 100 == 0:
            progress_callback(sim + 1)

    playoff_ev = {
        pid: {key: value / float(n_sims) for key, value in sums.items()} for pid, sums in accum.items()
    }
    return {
        "n_sims": int(n_sims),
        "bracket_size": bracket_size,
        "rounds": rounds_total,
        "stop_teams": stop_teams,
        "player_ev": playoff_ev,
        # P(team wins the last played round) — with stop_teams > 1 this is the
        # chance of qualifying onward rather than winning the whole bracket.
        "advance_rate": {
            str(tid): count / float(n_sims) for tid, count in sorted(champion_counts.items(), key=lambda kv: -kv[1])
        },
    }


def _compute_groups_result(payload: dict, progress_callback=None) -> dict:
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
    n_playoff_sims = int(payload.get("n_playoff_sims") or 2000)
    all_team_ids = [tid for group in groups for tid in group]
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(all_team_ids)
    prob_cache: Dict = {}
    outcomes: List[Dict[str, Any]] = []
    teams_out: Dict[int, Dict[str, Any]] = {}
    total_units = len(groups) + (n_playoff_sims if combined else 0)
    playoff_rounds = 0
    if combined:
        stop_teams = max(1, int(payload.get("playoff_stop_teams") or 1))
        if group_format == "de8_top3":
            playoff_rounds = 3  # 6-team bracket: quarters, semis, final
        else:
            playoff_rounds = max(1, int(math.log2(quals_per_group * len(groups))) - int(math.log2(stop_teams)))
    for g_idx, group in enumerate(groups):
        group_outcomes = enumerate_group(
            group, g_idx, player_rows_by_id, team_rank_by_id, prob_cache, extra_rounds=playoff_rounds
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
        playoff_summary = _simulate_combined_playoffs(
            groups,
            outcomes,
            n_playoff_sims,
            player_rows_by_id,
            team_rank_by_id,
            prob_cache,
            stop_teams=int(payload.get("playoff_stop_teams") or 1),
            quals_per_group=quals_per_group,
            progress_callback=(
                (lambda sims_done: progress_callback(len(groups) + sims_done, total_units))
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

    return {
        "teams": teams_out,
        "outcomes": outcomes,
        "outcomes_count": len(outcomes),
        "groups": groups,
        "group_count": len(groups),
        "group_format": group_format,
        "quals_per_group": quals_per_group,
        "combined_playoffs": combined,
        "playoff": playoff_summary,
        "method": "exact_enumeration_per_group" + ("_plus_mc_playoffs" if combined else ""),
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
        result = _compute_groups_result(payload, progress_callback=_update)
        _GROUPS_STATE.save(payload, result)
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
    parsed = _parse_event_groups(html, fmt)
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
    (16 / 8 / 4: RoundN has N matches), and each slot's team1/team2 lists the
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
        if snap_html and int(_parse_event_playoff_bracket(snap_html).get("bracket_size") or 0) in (2, 4, 8, 16):
            html = snap_html
        if html is None:
            if not url:
                raise HTTPException(status_code=400, detail="No HLTV event link found. Import the event first or pass hltv_event_id.")
            try:
                html = fetch_hltv_html(url, wait_text=None, timeout_ms=45000)
            except HLTVBrowserError as exc:
                raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV event page: {exc}") from exc

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


@router.get("/latest")
def get_latest_groups():
    latest = _GROUPS_STATE.load()
    if not latest:
        return {"exists": False}
    return {
        "exists": True,
        "payload": latest["payload"],
        "results": latest["results"],
        "updated_at": latest["updated_at"],
    }


@router.delete("/latest")
def reset_latest_groups():
    conn = _connect()
    try:
        for state in (_GROUPS_STATE, _GROUPS_BEST_STATE, _GROUPS_BEST_META):
            conn.execute(f"DELETE FROM {state.table} WHERE singleton_id = 1")
        conn.commit()
    finally:
        conn.close()
    for state in (_GROUPS_STATE, _GROUPS_BEST_STATE, _GROUPS_BEST_META):
        state.invalidate()
    return {"status": "ok"}


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
            row = get_player(pid)
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


def _player_booster_ub(team_id: int, reach_by_team: Dict[int, Dict[int, float]], rates: Dict[int, float]) -> float:
    """Upper bound on a single player's booster EV: pair their most-reached
    match slots with their highest trigger rates (a player can hold at most one
    booster per match slot). Ignores that boosters are shared across the roster,
    so summing this over 5 players over-counts — exactly what makes it a safe
    admissible bound for pruning / ranking before the exact assignment."""
    reach_probs = sorted((reach_by_team.get(int(team_id)) or {}).values(), reverse=True)
    if not reach_probs:
        return 0.0
    triggers = sorted((rates or {}).values(), reverse=True)
    ub = 0.0
    for i, prob in enumerate(reach_probs):
        trig = triggers[i] if i < len(triggers) else 0.0
        ub += float(prob) * float(trig) * BOOSTER_POINT_VALUE
    return ub


def optimize_group_boosters_for_roster(
    players: List[Dict[str, Any]],
    reach_by_team: Dict[int, Dict[int, float]],
    rates_by_pid: Dict[int, Dict[int, float]],
) -> Dict[str, Any]:
    """Assign the 18 booster types across a roster's (player, match-slot) columns
    to maximise total expected booster points (max-weight bipartite matching via
    min-cost flow). Group matches are all scored as BO3 (matching the outcome
    enumeration), so trigger rates need no BO1 adjustment.
    """
    columns: List[Dict[str, Any]] = []
    for player in players:
        pid = int(player["player_id"])
        tid = int(player.get("team_id", 0))
        for n, prob in sorted((reach_by_team.get(tid) or {}).items()):
            if prob > 0:
                columns.append(
                    {
                        "player_id": pid,
                        "player_name": player.get("name", f"Player {pid}"),
                        "match_number": int(n),
                        "slot_probability": float(prob),
                    }
                )
    if not columns:
        return {"assignments": [], "total_expected_booster_points": 0.0}

    weights: List[List[float]] = []
    for booster_id in range(18):
        row = []
        for col in columns:
            trig = float((rates_by_pid.get(col["player_id"]) or {}).get(booster_id, 0.0))
            row.append(col["slot_probability"] * trig * BOOSTER_POINT_VALUE)
        weights.append(row)

    assignments: List[Dict[str, Any]] = []
    total = 0.0
    for booster_id, col_idx, ev in _max_weight_assignment(weights):
        if ev <= 1e-9:
            continue
        col = columns[col_idx]
        assignments.append(
            {
                "booster_id": int(booster_id),
                "booster": BOOSTER_NAMES.get(int(booster_id), f"Booster {booster_id}"),
                "player_id": int(col["player_id"]),
                "player": col["player_name"],
                "match_number": int(col["match_number"]),
                "slot_probability": float(col["slot_probability"]),
                "expected_points": float(ev),
            }
        )
        total += float(ev)
    return {"assignments": assignments, "total_expected_booster_points": total}


_NUM_ROLES = 12
_ROLE_UNAVAILABLE = -1e9  # weight for a role the player has no trigger data for


def _exact_role_assignment(
    players: List[Dict[str, Any]], role_scores_by_pid: Dict[int, Dict[int, float]]
) -> tuple:
    """Optimal clash-free role assignment: each player takes a DISTINCT role,
    maximising total expected role points (max-weight matching via min-cost
    flow). Per-role EV = the player's role_ev (which is best-role points, i.e.
    best_per_match_score × padding-inclusive match count) scaled by that role's
    per-match score relative to their best — role points scale linearly with the
    per-match score over the same match count. Returns
    (total_role_ev, {pid: role_index}, {pid: assigned_role_ev}).

    This replaces the old 'everyone on their best role' sum (which ignored
    clashes and overstated role); when two players share a best role the optimum
    moves one to their next-best free role, reducing the total.
    """
    weights: List[List[float]] = []
    for p in players:
        pid = int(p["player_id"])
        rs = role_scores_by_pid.get(pid) or {}
        role_ev = float(p.get("role_ev") or 0.0)
        best = max(rs.values()) if rs else 0.0
        if abs(best) <= 1e-9:
            # No usable role data — neutral everywhere so it never blocks others.
            weights.append([0.0] * _NUM_ROLES)
            continue
        row = [_ROLE_UNAVAILABLE] * _NUM_ROLES
        for r in range(_NUM_ROLES):
            if r in rs:
                row[r] = role_ev * (float(rs[r]) / best)
        weights.append(row)

    total = 0.0
    role_of: Dict[int, int] = {}
    role_ev_of: Dict[int, float] = {}
    for row_idx, col_idx, w in _max_weight_assignment(weights):
        pid = int(players[row_idx]["player_id"])
        role_of[pid] = int(col_idx)
        contrib = float(w) if w > _ROLE_UNAVAILABLE / 2 else 0.0
        role_ev_of[pid] = contrib
        total += contrib
    return total, role_of, role_ev_of


def _topk_rosters_bnb(
    players_info: List[Dict[str, Any]],
    bound_scores: Dict[int, float],
    k: int,
    budget: int,
    max_per_team: int,
    include: set,
    true_score_fn=None,
    time_budget_seconds: float = 10.0,
) -> tuple[List[Dict[str, Any]], bool]:
    """Top-k rosters by branch-and-bound without enumerating the full space.

    bound_scores must be an additive per-player upper bound on each player's
    contribution; true_score_fn (if given) computes the roster's real score,
    which must never exceed the additive bound (e.g. per-group ceiling).

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
    base_score = sum(float(bound_scores.get(int(p["player_id"]), 0.0)) for p in forced)
    base_counts: Dict[int, int] = {}
    for p in forced:
        tid = int(p.get("team_id") or 0)
        base_counts[tid] = base_counts.get(tid, 0) + 1
        if base_counts[tid] > max_per_team:
            return [], True
    if base_cost > budget:
        return [], True

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
        assignment, _ = best_role_assignment_for_team(pids, role_scores_by_player)
        roles = [str((assignment or {}).get(pid, "-")) for pid in pids]
        total_ev = sum(float(p.get("total_ev") or 0.0) for p in roster)
        cost = sum(int(p.get("price") or 0) for p in roster)
        serialized = serialize_roster(players_meta, pids, roles, total_ev, cost)
        serialized["average_ev"] = float(total_ev)
        serialized["mode_metric"] = float(true_score)
        for player in serialized.get("players") or []:
            player["mode_score"] = float(player.get("total_ev") or 0.0)
        out.append(serialized)
    return out, exact


def _live_ceiling_scorer(results: dict, players_info: List[Dict[str, Any]]):
    vectors = _group_player_outcome_vectors(results)
    group_of_player: Dict[int, int] = {}
    for g, by_pid in vectors.items():
        for pid in by_pid:
            group_of_player[pid] = g

    def true_ceiling(roster: List[Dict[str, Any]]) -> float:
        by_group: Dict[int, List[int]] = {}
        for p in roster:
            pid = int(p["player_id"])
            g = group_of_player.get(pid)
            if g is not None:
                by_group.setdefault(g, []).append(pid)
        total = 0.0
        for g, pids in by_group.items():
            group_vecs = vectors.get(g) or {}
            n = len(next(iter(group_vecs.values()))) if group_vecs else 0
            sums = [0.0] * n
            for pid in pids:
                vec = group_vecs.get(pid)
                if not vec:
                    continue
                for i, v in enumerate(vec):
                    sums[i] += v
            total += max(sums) if sums else 0.0
        return total

    bound_scores = {}
    for p in players_info:
        pid = int(p["player_id"])
        g = group_of_player.get(pid)
        vec = (vectors.get(g) or {}).get(pid) if g is not None else None
        bound_scores[pid] = max(vec) if vec else float(p.get("total_ev") or 0.0)
    return bound_scores, true_ceiling


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
        latest = _GROUPS_STATE.load()
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
        reach_by_team = _group_team_reach_probs(results)
        rates_by_pid: Dict[int, Dict[int, float]] = {}
        role_scores_by_pid: Dict[int, Dict[int, float]] = {}
        for p in players_info:
            pid = int(p["player_id"])
            row = get_player(pid) or {}
            rates_by_pid[pid] = parse_booster_rates(row.get("boosters_json"))
            role_scores_by_pid[pid] = extract_role_scores_for_player(row)
            p["booster_ub"] = _player_booster_ub(int(p.get("team_id", 0)), reach_by_team, rates_by_pid[pid])

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
        vectors = _group_player_outcome_vectors(results)
        group_of_player: Dict[int, int] = {}
        for g, by_pid in vectors.items():
            for pid in by_pid:
                group_of_player[pid] = g
        players_meta = {str(p["player_id"]): p for p in players_info}

        def _roster_ceiling(pids: List[int]) -> float:
            # Groups are independent → best joint outcome is the best outcome
            # per group summed.
            ceiling = 0.0
            by_group: Dict[int, List[int]] = {}
            for pid in pids:
                g = group_of_player.get(pid)
                if g is not None:
                    by_group.setdefault(g, []).append(pid)
            for g, group_pids in by_group.items():
                group_vecs = vectors.get(g) or {}
                n = len(next(iter(group_vecs.values()))) if group_vecs else 0
                totals = [0.0] * n
                for pid in group_pids:
                    vec = group_vecs.get(pid)
                    if not vec:
                        continue
                    for i, v in enumerate(vec):
                        totals[i] += v
                ceiling += max(totals) if totals else 0.0
            return ceiling

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
                float(players_meta[str(pid)].get("booster_ub", 0.0)) for pid in pids
            )
            payload_r = {
                "pids": roster["pids"],
                "roles": roster["roles"],
                "cost": roster["cost"],
                "total_ev": roster["total_ev"],
                "ceiling": _roster_ceiling(pids),
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
            roster_players = [players_meta[str(pid)] for pid in pids]
            booster_result = optimize_group_boosters_for_roster(roster_players, reach_by_team, rates_by_pid)
            role_total, role_of, role_ev_of = _exact_role_assignment(roster_players, role_scores_by_pid)
            rating_win = sum(
                float(players_meta[str(pid)].get("rating_ev", 0.0)) + float(players_meta[str(pid)].get("win_ev", 0.0))
                for pid in pids
            )
            avg_ev = rating_win + role_total + float(booster_result["total_expected_booster_points"])
            role_names = [str(role_of.get(pid, "-")) for pid in pids]
            serialized = serialize_roster(
                players_meta, pr["pids"], role_names, avg_ev, pr["cost"],
                booster_assignments=booster_result["assignments"],
            )
            # Patch per-player role_ev to the exact-assigned role so the roster's
            # player rows sum to the team's average_ev (no clash overcount).
            for player in serialized.get("players") or []:
                pid = int(player["player_id"])
                new_role_ev = role_ev_of.get(pid)
                if new_role_ev is not None:
                    old_role_ev = float(player.get("role_ev") or 0.0)
                    player["role_ev"] = float(new_role_ev)
                    player["total_ev"] = float(player.get("total_ev") or 0.0) - old_role_ev + float(new_role_ev)
                player["mode_score"] = float(player.get("total_ev") or 0.0)
            serialized["average_ev"] = avg_ev
            serialized["ceiling_points"] = float(pr["ceiling"])
            valid_teams.append(serialized)
        valid_teams.sort(key=lambda team: float(team.get("average_ev") or 0.0), reverse=True)

        # Most-likely-winner: probability each roster is the single best pick
        # across the joint outcome space. Only the strongest-by-average teams can
        # realistically win, so score just the top contenders (rest stay 0).
        for team in valid_teams:
            team["outcome_win_probability"] = 0.0
        contenders = valid_teams[:_WIN_PROB_CANDIDATES]
        if contenders:
            probs_by_group = _group_outcome_probs_by_group(results)
            contender_pids = [[int(p.get("player_id") or 0) for p in (t.get("players") or [])] for t in contenders]
            win_probs = _compute_group_win_probs(contender_pids, vectors, group_of_player, probs_by_group)
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
        _GROUPS_BEST_STATE.save(payload or {}, result)
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
    latest = _GROUPS_STATE.load()
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
    latest_sim = _GROUPS_STATE.load()
    if latest_sim and _is_live_pool(latest_sim["results"] or {}):
        return {"exists": True, "live": True, "updated_at": latest_sim["updated_at"]}
    meta = _GROUPS_BEST_META.load()
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


def _live_groups_query(results: dict, body: dict, mode: str) -> Dict[str, Any]:
    options = parse_optimizer_payload(body)
    players_info = _groups_players_info(results, options["exclude"])
    if len(players_info) < 5:
        raise HTTPException(status_code=400, detail="Not enough players after exclusions")
    page = int(body.get("page") or 0)
    page_size = int(body.get("page_size") or 200)
    k = min(LIVE_OPTIMIZER_MAX_K, max(210, (page + 1) * page_size + 10))
    if mode == "single_outcome":
        bound_scores, true_fn = _live_ceiling_scorer(results, players_info)
        rosters, exact = _topk_rosters_bnb(
            players_info, bound_scores, k, options["budget"], options["max_per_team"], options["include"], true_fn
        )
        for team in rosters:
            team["ceiling_points"] = float(team.get("mode_metric") or 0.0)
    elif mode == "most_outcomes":
        # Rank the strongest-by-average teams by their probability of being the
        # single best pick across the joint outcome space.
        bound_scores = {int(p["player_id"]): float(p.get("total_ev") or 0.0) for p in players_info}
        cand_k = max(k, _WIN_PROB_CANDIDATES)
        rosters, exact = _topk_rosters_bnb(
            players_info, bound_scores, cand_k, options["budget"], options["max_per_team"], options["include"], None
        )
        vectors = _group_player_outcome_vectors(results)
        group_of_player = {pid: g for g, by_pid in vectors.items() for pid in by_pid}
        probs_by_group = _group_outcome_probs_by_group(results)
        team_pids = [[int(p.get("player_id") or 0) for p in (r.get("players") or [])] for r in rosters]
        win_probs = _compute_group_win_probs(team_pids, vectors, group_of_player, probs_by_group)
        for r, wp in zip(rosters, win_probs):
            r["outcome_win_probability"] = float(wp)
    else:
        bound_scores = {int(p["player_id"]): float(p.get("total_ev") or 0.0) for p in players_info}
        rosters, exact = _topk_rosters_bnb(
            players_info, bound_scores, k, options["budget"], options["max_per_team"], options["include"], None
        )
    search = str(body.get("search") or "")
    filtered = _filter_saved_combo_teams(rosters, set(), set(), search)
    sorted_teams = _sort_saved_combo_teams(filtered, mode, str(body.get("sort") or "ev_desc"))
    return {
        "exists": True,
        "live": True,
        "exact": exact,
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
    latest_sim = _GROUPS_STATE.load()
    if latest_sim and _is_live_pool(latest_sim["results"] or {}):
        result = _live_groups_query(latest_sim["results"] or {}, body, mode)
        result["updated_at"] = latest_sim["updated_at"]
        return result
    latest = _GROUPS_BEST_STATE.load()
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
    latest = _GROUPS_STATE.load()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored group stage found.")
    results = latest["results"] or {}
    live_pool = _is_live_pool(results)
    latest_combos = None if live_pool else _GROUPS_BEST_STATE.load()
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
