"""Double-elimination (GSL) group stage: X groups of 4, two qualify, two out.

Each group plays five BO3s — two opening matches, winners' match, elimination
match, decider — giving exactly 32 outcomes per group. Groups are independent,
so player expectations are exact per group and roster metrics that decompose
per group (average EV, ceiling) stay exact for any number of groups.
"""

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
        raise HTTPException(status_code=400, detail="groups must be a non-empty list of 4-team lists")
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
        if len(ids) != 4 or any(t <= 0 for t in ids):
            raise HTTPException(status_code=400, detail="Each group needs exactly 4 team IDs")
        if seen.intersection(ids):
            raise HTTPException(status_code=400, detail="A team appears in more than one group")
        seen.update(ids)
        groups.append(ids)
    combined = bool(payload.get("combined_playoffs"))
    stop_teams = int(payload.get("playoff_stop_teams") or 1)
    if combined:
        bracket_size = 2 * len(groups)
        if bracket_size & (bracket_size - 1) != 0:
            raise HTTPException(
                status_code=400,
                detail="Combined playoffs need the qualifier count to be a power of two (1, 2, 4, 8, or 16 groups).",
            )
        if stop_teams < 1 or stop_teams >= bracket_size or stop_teams & (stop_teams - 1) != 0:
            raise HTTPException(
                status_code=400,
                detail="playoff_stop_teams must be a power of two smaller than the bracket size (1 = play out the full bracket).",
            )
    n_sims = int(payload.get("n_playoff_sims") or 2000)
    return {
        "groups": groups,
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


def _simulate_combined_playoffs(
    groups: List[List[int]],
    outcomes: List[Dict[str, Any]],
    n_sims: int,
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    prob_cache: Dict,
    stop_teams: int = 1,
    progress_callback=None,
) -> Dict[str, Any]:
    """Monte Carlo playoff stage: sample each group's exact outcome, feed the
    qualifiers into a single-elimination bracket (group N winner vs group N+1
    runner-up and vice versa), and average per-player playoff fantasy points.

    stop_teams ends the bracket early: with 32 teams and stop_teams=4, the
    quarter-finals are the last matches played and the 4 winners qualify
    without playing on (e.g. a qualifier feeding a main event).
    """
    x = len(groups)
    bracket_size = 2 * x
    stop_teams = max(1, int(stop_teams))
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
        pairs: List[tuple] = []
        if x == 1:
            q = sampled[0]["qualified"]
            pairs = [(int(q[0]), int(q[1]))]
        else:
            for g in range(0, x, 2):
                q_a = sampled[g]["qualified"]
                q_b = sampled[g + 1]["qualified"]
                pairs.append((int(q_a[0]), int(q_b[1])))
                pairs.append((int(q_b[0]), int(q_a[1])))
        playoff_team_ids = [tid for pair in pairs for tid in pair]
        states = _clone_team_states({tid: base_states[tid] for tid in playoff_team_ids})
        current = pairs
        final_winners: List[int] = []
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
        playoff_rounds = max(1, int(math.log2(2 * len(groups))) - int(math.log2(stop_teams)))
    for g_idx, group in enumerate(groups):
        group_outcomes = _enumerate_group_outcomes(
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


def _parse_event_groups(html: str) -> List[Dict[str, Any]]:
    """Parse each DoubleElimination4 group's two opening matchups (seed order)
    from an HLTV event page's embedded bracket JSON."""
    import html as _htmlmod
    import re

    un = _htmlmod.unescape(html or "")
    # id -> ranking, captured near each team object (ranking sits after the logo).
    ranking_by_id: Dict[int, int] = {}
    for rm in re.finditer(r'"team":\{"id":(\d+),"name":"[^"]+".{0,700}?"ranking":(\d+)', un):
        ranking_by_id.setdefault(int(rm.group(1)), int(rm.group(2)))

    groups: List[Dict[str, Any]] = []
    for m in re.finditer(r'DoubleElimination4","name":"(Group[^"]+)"', un):
        name = m.group(1)
        seg = un[m.end() : m.end() + 6000]
        upper_start = seg.find("upperRound1")
        end = min([x for x in (seg.find("lowerRound1", upper_start), seg.find('"final"', upper_start)) if x > 0] or [len(seg)])
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
            if len(seeds) == 4:
                break
        groups.append({"name": name, "seeds": seeds})
    return groups


@router.post("/autofill-from-hltv-event")
def autofill_groups_from_hltv_event(payload: dict | None = None):
    """Scrape the linked HLTV event page and return the group opening matchups
    as this app's team IDs (seed order). Teams not already in the DB are created
    with the ranking from the bracket, so the sim can use them immediately."""
    from backend.data.event_db import get_active_event_id, get_event_detail
    from backend.services.hltv_browser import fetch_hltv_html, HLTVBrowserError

    body = payload or {}
    hltv_event_id = body.get("hltv_event_id")
    hltv_event_url = str(body.get("hltv_event_url") or "").strip()
    if not hltv_event_url:
        if not hltv_event_id:
            fantasy_id = body.get("event_id") or get_active_event_id()
            event = get_event_detail(int(fantasy_id)) if fantasy_id else None
            if event:
                hltv_event_id = event.get("hltv_event_id")
                hltv_event_url = str(event.get("hltv_event_url") or "").strip()
        if not hltv_event_url and hltv_event_id:
            hltv_event_url = f"https://www.hltv.org/events/{int(hltv_event_id)}/-"
    if not hltv_event_url:
        raise HTTPException(status_code=400, detail="No HLTV event link found. Import the event first or pass hltv_event_id.")

    try:
        html = fetch_hltv_html(hltv_event_url, wait_text=None, timeout_ms=45000)
    except HLTVBrowserError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV event page: {exc}") from exc

    parsed = _parse_event_groups(html)
    if not parsed:
        raise HTTPException(status_code=404, detail="No group brackets found on that event page.")

    def resolve(seed: Optional[Dict[str, Any]]) -> int:
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

    groups_out = []
    for group in parsed:
        seeds = list(group.get("seeds") or [])
        seeds = (seeds + [None, None, None, None])[:4]
        groups_out.append(
            {
                "name": group["name"],
                "team_ids": [resolve(s) for s in seeds],
                "team_names": [(s["name"] if s else "TBD") for s in seeds],
            }
        )
    return {"status": "ok", "group_count": len(groups_out), "groups": groups_out}


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
    """{group_index: {player_id: [score per outcome]}} in stored outcome order."""
    vectors: Dict[int, Dict[int, List[float]]] = {}
    counts: Dict[int, int] = {}
    for outcome in results.get("outcomes") or []:
        g = int(outcome.get("group") or 0)
        idx = counts.get(g, 0)
        counts[g] = idx + 1
        by_pid = vectors.setdefault(g, {})
        for pid_raw, score in (outcome.get("players") or {}).items():
            by_pid.setdefault(int(pid_raw), [0.0] * 32)[idx] = float(score)
    return vectors


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
            sums = [0.0] * 32
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
        vectors = _group_player_outcome_vectors(results)
        group_of_player: Dict[int, int] = {}
        for g, by_pid in vectors.items():
            for pid in by_pid:
                group_of_player[pid] = g
        players_meta = {str(p["player_id"]): p for p in players_info}
        valid_teams = []
        for roster in iter_valid_rosters(players_info, options["include"], options["budget"], options["max_per_team"], _update):
            pids = [int(p) for p in roster["pids"]]
            serialized = serialize_roster(players_meta, roster["pids"], roster["roles"], roster["total_ev"], roster["cost"])
            # Ceiling: groups are independent, so the best joint outcome is the
            # best outcome per group summed.
            ceiling = 0.0
            by_group: Dict[int, List[int]] = {}
            for pid in pids:
                g = group_of_player.get(pid)
                if g is not None:
                    by_group.setdefault(g, []).append(pid)
            for g, group_pids in by_group.items():
                group_vecs = vectors.get(g) or {}
                totals = [0.0] * 32
                for pid in group_pids:
                    vec = group_vecs.get(pid)
                    if not vec:
                        continue
                    for i, v in enumerate(vec):
                        totals[i] += v
                ceiling += max(totals) if totals else 0.0
            serialized["average_ev"] = float(roster["total_ev"])
            serialized["ceiling_points"] = float(ceiling)
            for player in serialized.get("players") or []:
                player["mode_score"] = float(player.get("total_ev") or 0.0)
            valid_teams.append(serialized)
        valid_teams.sort(key=lambda team: float(team.get("average_ev") or 0.0), reverse=True)
        result = {
            "top_teams": valid_teams[:10],
            "all_teams": valid_teams,
            "player_count": len(players_info),
            "processed_combinations": len(valid_teams),
            "total_combinations": int(job.get("total_combinations") or len(valid_teams)),
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
    if mode not in {"average", "single_outcome"}:
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
    selected: List[Dict[str, Any]] = []
    for g_idx, winners in enumerate(picks_by_group):
        want = [int(w) for w in winners]
        if len(want) != 5 or any(w <= 0 for w in want):
            raise HTTPException(status_code=400, detail=f"Group {g_idx + 1}: pick all 5 match winners first")
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
