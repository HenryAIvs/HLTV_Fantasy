import heapq
import itertools
import json
import math
import os
import random
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Dict

from fastapi import APIRouter, HTTPException

from backend.data.db import connect as _connect
from backend.data.player_db import get_player
from backend.data.singleton_state import SingletonState
from backend.data.team_db import get_team_by_id
from backend.services.team_optimizer import iter_valid_rosters, optimize_rosters, parse_optimizer_payload, serialize_roster
from backend.swiss_stage.fantasy_scoring import compute_elimination_penalty_components
from backend.swiss_stage.team_initialization import initialize_teams
from backend.swiss_stage.swiss_models import TeamState, PlayerState
from backend.services.match_engine import simulate_match_outcome, apply_fantasy_points_for_team, calculate_win_probability, BOOSTER_NAMES

router = APIRouter()
PLAYOFF_JOBS = {}
PLAYOFF_JOBS_LOCK = threading.Lock()
PLAYOFF_BEST_TEAM_JOBS = {}
PLAYOFF_BEST_TEAM_JOBS_LOCK = threading.Lock()
PLAYOFF_COMPLETED_BRACKET_JOBS = {}
PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK = threading.Lock()


# Two independent copies of the playoff pipeline state: the regular playoff
# bracket and the Bounty Event playoffs share every endpoint, distinguished by
# a `variant` field ("main"/"bounty") in payloads or query params.
_STATE_SETS = {
    "main": {
        "playoff": SingletonState("playoff_simulation_state", result_column="results_json", result_key="results"),
        "completed": SingletonState("playoff_completed_bracket_state"),
        "best": SingletonState("playoff_best_team_state"),
        # Small summary saved alongside the combos blob so metadata endpoints
        # never have to materialize the (multi-hundred-MB) result_json.
        "meta": SingletonState("playoff_best_team_meta"),
    },
    "bounty": {
        "playoff": SingletonState("bounty_playoff_simulation_state", result_column="results_json", result_key="results"),
        "completed": SingletonState("bounty_completed_bracket_state"),
        "best": SingletonState("bounty_best_team_state"),
        "meta": SingletonState("bounty_best_team_meta"),
    },
}


def _variant(value) -> str:
    return "bounty" if str(value or "").strip().lower() == "bounty" else "main"


def _states(variant) -> dict:
    return _STATE_SETS[_variant(variant)]


def ensure_playoff_schema() -> None:
    for states in _STATE_SETS.values():
        for state in states.values():
            state.ensure_table()


def save_latest_playoff(payload: dict, results: dict, variant: str = "main") -> None:
    _states(variant)["playoff"].save(payload, results)


def load_latest_playoff(variant: str = "main") -> dict | None:
    return _states(variant)["playoff"].load()


def save_latest_completed_bracket(payload: dict, result: dict, variant: str = "main") -> None:
    _states(variant)["completed"].save(payload, result)


def load_latest_completed_bracket(variant: str = "main") -> dict | None:
    return _states(variant)["completed"].load()


def _best_team_meta_summary(result: dict) -> dict:
    return {
        "mode": result.get("mode"),
        "player_count": result.get("player_count"),
        "total_teams": len(result.get("all_teams") or []),
        "processed_combinations": result.get("processed_combinations"),
        "total_combinations": result.get("total_combinations"),
        # True when the pool was too large to store every roster and only the
        # strongest candidates were kept (16-team two-phase).
        "approximate": bool(result.get("approximate")),
        "candidate_count": result.get("candidate_count"),
    }


def save_latest_playoff_best_team(payload: dict, result: dict, variant: str = "main") -> None:
    _states(variant)["best"].save(payload, result)
    _states(variant)["meta"].save(payload, _best_team_meta_summary(result))


def load_latest_playoff_best_team(variant: str = "main") -> dict | None:
    return _states(variant)["best"].load()


def _saved_combo_metric(team: dict, mode: str) -> float:
    if mode == "single_outcome":
        return float(team.get("ceiling_points") or 0.0)
    if mode == "most_outcomes":
        # Rank by chance this roster ends up the winner; blobs saved before the
        # probability field existed fall back to the raw outcome-win count.
        prob = team.get("outcome_win_probability")
        if prob is not None:
            return float(prob or 0.0)
        return float(team.get("outcome_wins") or 0.0)
    return float(team.get("average_ev", team.get("total_ev", 0.0)) or 0.0)


def _filter_saved_combo_teams(teams: list[dict], include: set[int], exclude: set[int], search: str = "") -> list[dict]:
    q = str(search or "").strip().lower()
    filtered = []
    for team in teams or []:
        players = team.get("players") or []
        pids = {int(p.get("player_id") or 0) for p in players}
        if include and not include.issubset(pids):
            continue
        if exclude and pids.intersection(exclude):
            continue
        if q:
            if q not in str(team.get("cost", "")).lower() and q not in str(team.get("total_ev", "")).lower():
                matched = False
                for player in players:
                    haystack = " ".join(
                        [
                            str(player.get("name") or ""),
                            str(player.get("player_id") or ""),
                            str(player.get("team_id") or ""),
                            str(player.get("role_name") or ""),
                        ]
                    ).lower()
                    if q in haystack:
                        matched = True
                        break
                if not matched:
                    continue
        filtered.append(team)
    return filtered


def _sort_saved_combo_teams(teams: list[dict], mode: str, sort_key: str = "ev_desc") -> list[dict]:
    def metric(team: dict) -> float:
        return _saved_combo_metric(team, mode)

    if sort_key == "cost_asc":
        return sorted(teams, key=lambda team: int(team.get("cost") or 0))
    if sort_key == "cost_desc":
        return sorted(teams, key=lambda team: int(team.get("cost") or 0), reverse=True)
    if sort_key == "cpp_asc":
        return sorted(teams, key=lambda team: metric(team) / float(team.get("cost") or 1))
    if sort_key == "cpp_desc":
        return sorted(teams, key=lambda team: metric(team) / float(team.get("cost") or 1), reverse=True)
    if sort_key == "ev_asc":
        return sorted(teams, key=metric)
    return sorted(teams, key=metric, reverse=True)


def _page_items(items: list[dict], page: int, page_size: int) -> list[dict]:
    safe_page = max(0, int(page or 0))
    safe_size = max(1, min(500, int(page_size or 200)))
    start = safe_page * safe_size
    return items[start : start + safe_size]


def _apply_elimination_penalty(team: TeamState, remaining_rounds: int) -> None:
    """
    Apply -3 points per remaining round to all players (win component/total).
    """
    if remaining_rounds <= 0:
        return
    penalty = compute_elimination_penalty_components(remaining_rounds)["win"]
    for p in team.players.values():
        p.win_points_total += penalty
        p.total_points += penalty
        p.point_breakdown.append(
            {
                "match_number": None,
                "match_type": "ELIMINATION",
                "opponent_team_id": None,
                "opponent_rank": None,
                "did_win": False,
                "win_probability": 0.0,
                "rating_used": None,
                "rating_points": 0.0,
                "win_points": float(penalty),
                "role_id": p.role_id,
                "role_major_pct": float(p.major_pct),
                "role_minor_pct": float(p.minor_pct),
                "role_points": 0.0,
                "booster_slot": None,
                "booster_id": None,
                "booster_name": None,
                "booster_trigger_rate": 0.0,
                "booster_points": 0.0,
                "total_points": float(penalty),
                "note": f"Elimination penalty for {remaining_rounds} unplayed round(s)",
            }
        )


def _booster_slots_for(ps: PlayerState | None) -> list[dict]:
    """The player's booster assignment per match slot: ranked by edge over the
    field-average trigger rate, best edge on the team's 1st match. Points still
    pay 5 x the player's raw rate."""
    if ps is None:
        return []
    slots = []
    for i, rate in enumerate(ps.boosters or []):
        bid_raw = ps.booster_ids[i] if i < len(ps.booster_ids or []) else -1
        bid = int(bid_raw) if bid_raw is not None and int(bid_raw) >= 0 else None
        edges = ps.booster_edges or []
        slots.append(
            {
                "slot": i + 1,
                "booster_id": bid,
                "booster_name": BOOSTER_NAMES.get(bid) if bid is not None else None,
                "booster_rate": float(rate or 0.0),
                "edge": float(edges[i]) if i < len(edges) else 0.0,
            }
        )
    return slots


def _build_playoff_lookup_context(team_slots: List[int]) -> tuple[Dict[int, dict], Dict[int, int]]:
    conn = _connect()
    try:
        team_rows = conn.execute(
            """
            SELECT team_id, hltv_rank, player1_id, player2_id, player3_id, player4_id, player5_id
            FROM teams
            WHERE team_id IN ({})
            """.format(",".join("?" for _ in team_slots)),
            tuple(int(t) for t in team_slots),
        ).fetchall()

        team_rank_by_id: Dict[int, int] = {}
        player_ids = set()
        for r in team_rows:
            tid = int(r["team_id"])
            rank = r["hltv_rank"]
            team_rank_by_id[tid] = int(rank) if rank is not None else 100
            for col in ("player1_id", "player2_id", "player3_id", "player4_id", "player5_id"):
                pid = r[col]
                if pid:
                    player_ids.add(int(pid))

        player_rows_by_id: Dict[int, dict] = {}
        if player_ids:
            p_rows = conn.execute(
                """
                SELECT * FROM players
                WHERE player_id IN ({})
                """.format(",".join("?" for _ in player_ids)),
                tuple(player_ids),
            ).fetchall()
            player_rows_by_id = {int(r["player_id"]): dict(r) for r in p_rows}

        return player_rows_by_id, team_rank_by_id
    finally:
        conn.close()


def _simulate_bracket(team_slots: List[int], vrs_ranks: Dict[int, int], rng=None):
    team_states: Dict[int, TeamState] = initialize_teams(team_slots, vrs_ranks)
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(team_slots)
    match_results = {"quarters": [], "semis": [], "final": []}

    def ts(tid: int) -> TeamState:
        if tid not in team_states:
            raise HTTPException(status_code=400, detail=f"Unknown team_id {tid}")
        return team_states[tid]

    def play_match(a_id: int, b_id: int, remaining_rounds_after: int):
        A, B = ts(a_id), ts(b_id)
        match_num_a = A.matches_played + 1
        match_num_b = B.matches_played + 1
        rand_fn = (rng or random).random if rng is not None else None
        result = simulate_match_outcome(A, B, "bo3", rng=rand_fn)
        if result.winner_id == A.team_id:
            winner, loser = A, B
            did_win_a, did_win_b = True, False
        else:
            winner, loser = B, A
            did_win_a, did_win_b = False, True

        winner.record_win(loser.team_id)
        loser.record_loss(winner.team_id)

        apply_fantasy_points_for_team(
            A, B.team_id, result.win_probability, did_win_a, match_num_a, "bo3",
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
        )
        apply_fantasy_points_for_team(
            B, A.team_id, 1.0 - result.win_probability, did_win_b, match_num_b, "bo3",
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
        )

        _apply_elimination_penalty(loser, remaining_rounds_after)
        return winner.team_id, loser.team_id, result.win_probability

    quarters = [
        (team_slots[0], team_slots[1]),
        (team_slots[2], team_slots[3]),
        (team_slots[4], team_slots[5]),
        (team_slots[6], team_slots[7]),
    ]

    semi_ids = []
    for a, b in quarters:
        w, l, p = play_match(a, b, remaining_rounds_after=2)
        semi_ids.append(w)
        match_results["quarters"].append({"winner": w, "loser": l, "p_win_a": p, "teams": [a, b]})

    final_ids = []
    for i in range(0, len(semi_ids), 2):
        a, b = semi_ids[i], semi_ids[i + 1]
        w, l, p = play_match(a, b, remaining_rounds_after=1)
        final_ids.append(w)
        match_results["semis"].append({"winner": w, "loser": l, "p_win_a": p, "teams": [a, b]})

    champ, runner, p = play_match(final_ids[0], final_ids[1], remaining_rounds_after=0)
    match_results["final"].append({"winner": champ, "loser": runner, "p_win_a": p, "teams": [final_ids[0], final_ids[1]]})

    return team_states, match_results


def _clone_team_states(team_states: Dict[int, TeamState]) -> Dict[int, TeamState]:
    cloned: Dict[int, TeamState] = {}
    for tid, ts in team_states.items():
        cloned_players: Dict[int, PlayerState] = {}
        for pid, p in ts.players.items():
            cloned_players[pid] = PlayerState(
                player_id=p.player_id,
                rating=p.rating,
                major_pct=p.major_pct,
                minor_pct=p.minor_pct,
                boosters=list(p.boosters),
                role_id=p.role_id,
                booster_ids=list(p.booster_ids),
                booster_rates=dict(p.booster_rates),
                total_points=p.total_points,
                rating_points_total=p.rating_points_total,
                win_points_total=p.win_points_total,
                role_points_total=p.role_points_total,
                booster_points_total=p.booster_points_total,
                point_breakdown=[dict(row) for row in p.point_breakdown],
            )
        cloned[tid] = TeamState(
            team_id=ts.team_id,
            vrs_rank=ts.vrs_rank,
            players=cloned_players,
            wins=ts.wins,
            losses=ts.losses,
            opponents_played=set(ts.opponents_played),
        )
    return cloned


def _play_match_deterministic(
    team_states: Dict[int, TeamState],
    a_id: int,
    b_id: int,
    winner_id: int,
    remaining_rounds_after: int,
    prob_cache: Dict[tuple[int, int], float] | None = None,
    player_rows_by_id: Dict[int, dict] | None = None,
    team_rank_by_id: Dict[int, int] | None = None,
) -> tuple[int, int, float, float]:
    A = team_states[a_id]
    B = team_states[b_id]
    match_num_a = A.matches_played + 1
    match_num_b = B.matches_played + 1
    key = (a_id, b_id)
    if prob_cache is not None and key in prob_cache:
        prob_a = prob_cache[key]
    else:
        prob_a = calculate_win_probability(a_id, b_id, "bo3")
        if prob_cache is not None:
            prob_cache[key] = prob_a
            prob_cache[(b_id, a_id)] = 1.0 - prob_a

    if winner_id == a_id:
        winner, loser = A, B
        did_win_a, did_win_b = True, False
        branch_prob = prob_a
    elif winner_id == b_id:
        winner, loser = B, A
        did_win_a, did_win_b = False, True
        branch_prob = 1.0 - prob_a
    else:
        raise ValueError(f"winner_id {winner_id} is not in match ({a_id}, {b_id})")

    winner.record_win(loser.team_id)
    loser.record_loss(winner.team_id)

    apply_fantasy_points_for_team(
        A, B.team_id, prob_a, did_win_a, match_num_a, "bo3",
        player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
    )
    apply_fantasy_points_for_team(
        B, A.team_id, 1.0 - prob_a, did_win_b, match_num_b, "bo3",
        player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
    )
    _apply_elimination_penalty(loser, remaining_rounds_after)

    return winner.team_id, loser.team_id, prob_a, branch_prob


def _exact_weighted_player_totals(
    team_slots: List[int],
    vrs_ranks: Dict[int, int],
    has_third_place_decider: bool = False,
    progress_callback=None,
    quarters_override: List[tuple] | None = None,
    sf_pairs_resolver=None,
) -> tuple[Dict[int, Dict], Dict, int, List[Dict]]:
    base_states = initialize_teams(team_slots, vrs_ranks)
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(team_slots)
    accum: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    total_prob = 0.0
    processed = 0
    best_prob = -1.0
    best_bracket = {"quarters": [], "semis": [], "final": []}
    outcomes: List[Dict] = []

    quarters = [tuple(pair) for pair in quarters_override] if quarters_override else [
        (team_slots[0], team_slots[1]),
        (team_slots[2], team_slots[3]),
        (team_slots[4], team_slots[5]),
        (team_slots[6], team_slots[7]),
    ]
    total_outcomes = 256 if has_third_place_decider else 128
    prob_cache: Dict[tuple[int, int], float] = {}

    if progress_callback:
        progress_callback(0, total_outcomes)

    def capture_outcome(path_prob: float, states: Dict[int, TeamState], bracket: Dict) -> None:
        player_points: Dict[str, float] = {}
        player_components: Dict[str, Dict[str, float]] = {}
        player_breakdown: Dict[str, List[dict]] = {}
        for ts in states.values():
            for pid, p in ts.players.items():
                player_points[str(pid)] = float(p.total_points)
                player_components[str(pid)] = {
                    "total": float(p.total_points),
                    "total_without_booster": float(p.rating_points_total + p.win_points_total + p.role_points_total),
                    "rating": float(p.rating_points_total),
                    "win": float(p.win_points_total),
                    "role": float(p.role_points_total),
                    "booster": float(p.booster_points_total),
                }
                player_breakdown[str(pid)] = [dict(row) for row in p.point_breakdown]
        outcomes.append(
            {
                "probability": float(path_prob),
                "bracket": bracket,
                "players": player_points,
                "player_components": player_components,
                "player_breakdown": player_breakdown,
            }
        )

    def recurse_qf(idx: int, states: Dict[int, TeamState], prob: float, qf_winners: List[int], qf_matches: List[dict]):
        nonlocal total_prob, processed, best_prob, best_bracket
        if idx == 4:
            if sf_pairs_resolver:
                (sf1_a, sf1_b), (sf2_a, sf2_b) = sf_pairs_resolver(qf_winners)
            else:
                sf1_a, sf1_b = qf_winners[0], qf_winners[1]
                sf2_a, sf2_b = qf_winners[2], qf_winners[3]
            # Semifinal 1
            for semi1_winner in (sf1_a, sf1_b):
                s1_states = _clone_team_states(states)
                s1_winner, s1_loser, s1_p_win_a, s1_branch_p = _play_match_deterministic(
                    s1_states, sf1_a, sf1_b, semi1_winner, remaining_rounds_after=1, prob_cache=prob_cache,
                    player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
                )
                semi1_match = {
                    "winner": s1_winner,
                    "loser": s1_loser,
                    "p_win_a": s1_p_win_a,
                    "teams": [sf1_a, sf1_b],
                }

                # Semifinal 2
                for semi2_winner in (sf2_a, sf2_b):
                    s2_states = _clone_team_states(s1_states)
                    s2_winner, s2_loser, s2_p_win_a, s2_branch_p = _play_match_deterministic(
                        s2_states, sf2_a, sf2_b, semi2_winner, remaining_rounds_after=1, prob_cache=prob_cache,
                        player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
                    )
                    semi2_match = {
                        "winner": s2_winner,
                        "loser": s2_loser,
                        "p_win_a": s2_p_win_a,
                        "teams": [sf2_a, sf2_b],
                    }

                    # Final (and optional third-place decider)
                    for final_winner in (s1_winner, s2_winner):
                        f_states = _clone_team_states(s2_states)
                        f_winner, f_loser, f_p_win_a, f_branch_p = _play_match_deterministic(
                            f_states, s1_winner, s2_winner, final_winner, remaining_rounds_after=0, prob_cache=prob_cache,
                            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
                        )

                        if has_third_place_decider:
                            for third_winner in (s1_loser, s2_loser):
                                t_states = _clone_team_states(f_states)
                                t_winner, t_loser, t_p_win_a, t_branch_p = _play_match_deterministic(
                                    t_states, s1_loser, s2_loser, third_winner, remaining_rounds_after=0, prob_cache=prob_cache,
                                    player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
                                )
                                path_prob = prob * s1_branch_p * s2_branch_p * f_branch_p * t_branch_p
                                total_prob += path_prob
                                processed += 1

                                if path_prob > best_prob:
                                    best_prob = path_prob
                                    best_bracket = {
                                        "quarters": list(qf_matches),
                                        "semis": [semi1_match, semi2_match],
                                        "final": [
                                            {
                                                "winner": f_winner,
                                                "loser": f_loser,
                                                "p_win_a": f_p_win_a,
                                                "teams": [s1_winner, s2_winner],
                                            }
                                        ],
                                        "third_place": [
                                            {
                                                "winner": t_winner,
                                                "loser": t_loser,
                                                "p_win_a": t_p_win_a,
                                                "teams": [s1_loser, s2_loser],
                                            }
                                        ],
                                    }
                                outcome_bracket = {
                                    "quarters": list(qf_matches),
                                    "semis": [semi1_match, semi2_match],
                                    "final": [
                                        {
                                            "winner": f_winner,
                                            "loser": f_loser,
                                            "p_win_a": f_p_win_a,
                                            "teams": [s1_winner, s2_winner],
                                        }
                                    ],
                                    "third_place": [
                                        {
                                            "winner": t_winner,
                                            "loser": t_loser,
                                            "p_win_a": t_p_win_a,
                                            "teams": [s1_loser, s2_loser],
                                        }
                                    ],
                                }
                                capture_outcome(path_prob, t_states, outcome_bracket)

                                for tid, ts in t_states.items():
                                    for pid, p in ts.players.items():
                                        bucket = accum[tid].setdefault(
                                            pid,
                                            {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0},
                                        )
                                        bucket["total"] += path_prob * p.total_points
                                        bucket["rating"] += path_prob * p.rating_points_total
                                        bucket["win"] += path_prob * p.win_points_total
                                        bucket["role"] += path_prob * p.role_points_total
                                        bucket["booster"] += path_prob * p.booster_points_total

                                if progress_callback:
                                    progress_callback(processed, total_outcomes)
                        else:
                            path_prob = prob * s1_branch_p * s2_branch_p * f_branch_p
                            total_prob += path_prob
                            processed += 1

                            if path_prob > best_prob:
                                best_prob = path_prob
                                best_bracket = {
                                    "quarters": list(qf_matches),
                                    "semis": [semi1_match, semi2_match],
                                    "final": [
                                        {
                                            "winner": f_winner,
                                            "loser": f_loser,
                                            "p_win_a": f_p_win_a,
                                            "teams": [s1_winner, s2_winner],
                                        }
                                    ],
                                }
                            outcome_bracket = {
                                "quarters": list(qf_matches),
                                "semis": [semi1_match, semi2_match],
                                "final": [
                                    {
                                        "winner": f_winner,
                                        "loser": f_loser,
                                        "p_win_a": f_p_win_a,
                                        "teams": [s1_winner, s2_winner],
                                    }
                                ],
                            }
                            capture_outcome(path_prob, f_states, outcome_bracket)

                            for tid, ts in f_states.items():
                                for pid, p in ts.players.items():
                                    bucket = accum[tid].setdefault(
                                        pid,
                                        {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0},
                                    )
                                    bucket["total"] += path_prob * p.total_points
                                    bucket["rating"] += path_prob * p.rating_points_total
                                    bucket["win"] += path_prob * p.win_points_total
                                    bucket["role"] += path_prob * p.role_points_total
                                    bucket["booster"] += path_prob * p.booster_points_total

                            if progress_callback:
                                progress_callback(processed, total_outcomes)
            return

        a_id, b_id = quarters[idx]
        for winner_id in (a_id, b_id):
            branch_states = _clone_team_states(states)
            winner, loser, p_win_a, branch_prob = _play_match_deterministic(
                branch_states, a_id, b_id, winner_id, remaining_rounds_after=2, prob_cache=prob_cache,
                player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
            )
            recurse_qf(
                idx + 1,
                branch_states,
                prob * branch_prob,
                qf_winners + [winner],
                qf_matches + [{"winner": winner, "loser": loser, "p_win_a": p_win_a, "teams": [a_id, b_id]}],
            )

    recurse_qf(0, base_states, 1.0, [], [])

    denom = total_prob if total_prob > 0 else 1.0
    results: Dict[int, Dict] = {}
    for tid in team_slots:
        players_out: Dict[int, Dict[str, float]] = {}
        state_players = base_states[tid].players
        for pid, sums in accum[tid].items():
            ps = state_players.get(pid)
            players_out[pid] = {
                "total_points": sums["total"] / denom,
                "rating_points_total": sums["rating"] / denom,
                "win_points_total": sums["win"] / denom,
                "role_points_total": sums["role"] / denom,
                "booster_points_total": sums["booster"] / denom,
                "total_points_without_booster": (sums["rating"] + sums["win"] + sums["role"]) / denom,
                "role_id": ps.role_id if ps else None,
                "booster_slots": _booster_slots_for(ps),
            }
        results[tid] = {
            "team_id": tid,
            "wins": 0,
            "losses": 0,
            "players": players_out,
        }

    return results, best_bracket, total_outcomes, outcomes


def _exact_bracket6_player_totals(
    team_slots: List[int],
    vrs_ranks: Dict[int, int],
    progress_callback=None,
) -> tuple[Dict[int, Dict], Dict, int, List[Dict]]:
    """Exact enumeration of the 6-team byes bracket (Porto/Cologne playoffs).

    Slot convention: [SEMI BYE 1, QF1a, QF1b, QF2a, QF2b, SEMI BYE 2] — the
    byes are the group winners, entering directly at the semi-finals (bye 1
    meets the QF1 winner, bye 2 the QF2 winner). 32 outcomes."""
    bye1, q1a, q1b, q2a, q2b, bye2 = (int(t) for t in team_slots)
    base_states = initialize_teams(team_slots, vrs_ranks)
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(team_slots)
    accum: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    prob_cache: Dict[tuple[int, int], float] = {}
    outcomes: List[Dict] = []
    best_prob = -1.0
    best_bracket: Dict = {"quarters": [], "semis": [], "final": []}
    total_outcomes = 32
    processed = 0
    total_prob = 0.0

    def play(states, a, b, winner, remaining_after):
        return _play_match_deterministic(
            states, a, b, winner, remaining_rounds_after=remaining_after, prob_cache=prob_cache,
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
        )

    def mk(w, l, p, teams):
        return {"winner": w, "loser": l, "p_win_a": p, "teams": list(teams)}

    for w1 in (q1a, q1b):
        s1 = _clone_team_states(base_states)
        q1w, q1l, q1p, q1bp = play(s1, q1a, q1b, w1, 2)
        m_q1 = mk(q1w, q1l, q1p, [q1a, q1b])
        for w2 in (q2a, q2b):
            s2 = _clone_team_states(s1)
            q2w, q2l, q2p, q2bp = play(s2, q2a, q2b, w2, 2)
            m_q2 = mk(q2w, q2l, q2p, [q2a, q2b])
            for w3 in (bye1, q1w):
                s3 = _clone_team_states(s2)
                sf1w, sf1l, sf1p, sf1bp = play(s3, bye1, q1w, w3, 1)
                m_s1 = mk(sf1w, sf1l, sf1p, [bye1, q1w])
                for w4 in (bye2, q2w):
                    s4 = _clone_team_states(s3)
                    sf2w, sf2l, sf2p, sf2bp = play(s4, bye2, q2w, w4, 1)
                    m_s2 = mk(sf2w, sf2l, sf2p, [bye2, q2w])
                    for w5 in (sf1w, sf2w):
                        s5 = _clone_team_states(s4)
                        fw, fl, fp, fbp = play(s5, sf1w, sf2w, w5, 0)
                        m_f = mk(fw, fl, fp, [sf1w, sf2w])
                        # Bye teams get 6 padding points per player for the
                        # quarter-final they skip.
                        for bye_tid in (bye1, bye2):
                            bye_ts = s5.get(bye_tid)
                            if not bye_ts:
                                continue
                            for p in bye_ts.players.values():
                                p.win_points_total += 6.0
                                p.total_points += 6.0
                        path_prob = q1bp * q2bp * sf1bp * sf2bp * fbp
                        total_prob += path_prob
                        processed += 1
                        bracket = {"quarters": [m_q1, m_q2], "semis": [m_s1, m_s2], "final": [m_f]}
                        if path_prob > best_prob:
                            best_prob = path_prob
                            best_bracket = bracket
                        player_points: Dict[str, float] = {}
                        player_components: Dict[str, Dict[str, float]] = {}
                        player_breakdown: Dict[str, List[dict]] = {}
                        for ts in s5.values():
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
                                player_breakdown[str(pid)] = [dict(row) for row in p.point_breakdown]
                        outcomes.append(
                            {
                                "probability": float(path_prob),
                                "bracket": bracket,
                                "players": player_points,
                                "player_components": player_components,
                                "player_breakdown": player_breakdown,
                            }
                        )
                        for tid, ts in s5.items():
                            for pid, p in ts.players.items():
                                bucket = accum[tid].setdefault(
                                    pid,
                                    {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0},
                                )
                                bucket["total"] += path_prob * p.total_points
                                bucket["rating"] += path_prob * p.rating_points_total
                                bucket["win"] += path_prob * p.win_points_total
                                bucket["role"] += path_prob * p.role_points_total
                                bucket["booster"] += path_prob * p.booster_points_total
                        if progress_callback:
                            progress_callback(processed, total_outcomes)

    denom = total_prob if total_prob > 0 else 1.0
    results: Dict[int, Dict] = {}
    for tid in team_slots:
        players_out: Dict[int, Dict[str, float]] = {}
        state_players = base_states[tid].players
        for pid, sums in accum[tid].items():
            ps = state_players.get(pid)
            players_out[pid] = {
                "total_points": sums["total"] / denom,
                "rating_points_total": sums["rating"] / denom,
                "win_points_total": sums["win"] / denom,
                "role_points_total": sums["role"] / denom,
                "booster_points_total": sums["booster"] / denom,
                "total_points_without_booster": (sums["rating"] + sums["win"] + sums["role"]) / denom,
                "role_id": ps.role_id if ps else None,
                "booster_slots": _booster_slots_for(ps),
            }
        results[tid] = {"team_id": tid, "wins": 0, "losses": 0, "players": players_out}

    return results, best_bracket, total_outcomes, outcomes


# --- General N-team single-elimination bracket (Monte-Carlo for large sizes) ---

_ROUND_NAME_BY_TEAMS = {2: "final", 4: "semis", 8: "quarters", 16: "round_of_16", 32: "round_of_32"}
# Fields with more than this many exact outcomes (2^(teams-1)) are sampled.
# 8 teams = 128 outcomes (exact); 16 teams = 32768 (Monte-Carlo).
_BRACKET_EXACT_OUTCOME_LIMIT = 1024
_BRACKET_MC_SIMS = 5000
# Bounds for a user-supplied Monte-Carlo sample count (large fields only).
_BRACKET_MC_SIMS_MIN = 500
_BRACKET_MC_SIMS_MAX = 200000
# Valid single-elim field sizes the UI/back-end accept.
_ALLOWED_BRACKET_SIZES = (2, 4, 6, 8, 16)


def _clamp_mc_sims(value) -> int | None:
    """Clamp a requested Monte-Carlo sample count to a sane range, or None to use
    the default. More sims = smoother EVs but longer runs (~5000 ≈ 18s)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return max(_BRACKET_MC_SIMS_MIN, min(_BRACKET_MC_SIMS_MAX, n))


def _round_name_for(teams_in_round: int) -> str:
    return _ROUND_NAME_BY_TEAMS.get(int(teams_in_round), f"round_of_{int(teams_in_round)}")


def _reset_states_inplace(states: Dict[int, TeamState]) -> None:
    """Zero the accumulated points and records so the same state objects can be
    reused across Monte-Carlo sims (far cheaper than deep-cloning per sim)."""
    for ts in states.values():
        ts.wins = 0
        ts.losses = 0
        ts.opponents_played = set()
        for p in ts.players.values():
            p.total_points = 0.0
            p.rating_points_total = 0.0
            p.win_points_total = 0.0
            p.role_points_total = 0.0
            p.booster_points_total = 0.0
            p.point_breakdown = []


def _simulate_bracket_n(
    team_slots: List[int],
    team_states: Dict[int, TeamState],
    player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int],
    has_third_place_decider: bool,
    rng,
) -> tuple[Dict[int, TeamState], Dict[str, List[dict]]]:
    """One random single-elim bracket for any power-of-two field. Round names
    generalise by size (round_of_16 → quarters → semis → final). The two
    semi-final losers play the optional third-place decider. The caller resets
    and reuses the same state objects across sims (a DB reload or deep clone per
    sim is far too slow)."""
    n = len(team_slots)
    total_rounds = int(round(math.log2(n)))
    match_results: Dict[str, List[dict]] = {}
    rand_fn = (rng or random).random

    def play(a_id: int, b_id: int, remaining_after: int):
        A, B = team_states[a_id], team_states[b_id]
        mna, mnb = A.matches_played + 1, B.matches_played + 1
        result = simulate_match_outcome(A, B, "bo3", rng=rand_fn)
        if result.winner_id == A.team_id:
            winner, loser, dwa, dwb = A, B, True, False
        else:
            winner, loser, dwa, dwb = B, A, False, True
        winner.record_win(loser.team_id)
        loser.record_loss(winner.team_id)
        apply_fantasy_points_for_team(A, B.team_id, result.win_probability, dwa, mna, "bo3", player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id)
        apply_fantasy_points_for_team(B, A.team_id, 1.0 - result.win_probability, dwb, mnb, "bo3", player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id)
        _apply_elimination_penalty(loser, remaining_after)
        return winner.team_id, loser.team_id, result.win_probability

    current = list(team_slots)
    round_idx = 0
    semi_losers: List[int] = []
    while len(current) > 1:
        teams_in_round = len(current)
        rname = _round_name_for(teams_in_round)
        remaining_after = total_rounds - round_idx - 1
        winners, matches = [], []
        for i in range(0, len(current), 2):
            a, b = current[i], current[i + 1]
            w, l, p = play(a, b, remaining_after)
            winners.append(w)
            matches.append({"winner": w, "loser": l, "p_win_a": p, "teams": [a, b]})
            if teams_in_round == 4:
                semi_losers.append(l)
        match_results[rname] = matches
        current = winners
        round_idx += 1

    if has_third_place_decider and len(semi_losers) == 2:
        w, l, p = play(semi_losers[0], semi_losers[1], 0)
        match_results["third_place"] = [{"winner": w, "loser": l, "p_win_a": p, "teams": list(semi_losers)}]
    return team_states, match_results


# Below this many sims the process-pool startup costs more than it saves.
_MC_PARALLEL_MIN_SIMS = 2000


def _mc_chunk_worker(
    team_slots: List[int],
    vrs_ranks: Dict[int, int],
    has_third_place_decider: bool,
    n_sims: int,
    seed: int,
    store_outcomes: bool,
    total_sims: int,
) -> tuple[Dict[int, Dict[int, Dict[str, float]]], List[Dict]]:
    """One Monte-Carlo chunk, run in a worker process: n_sims random brackets,
    returning the raw accumulator sums plus (optionally) the sampled outcomes.
    Spawn-safe — builds its own states and DB lookup context."""
    rng = random.Random(seed)
    states = initialize_teams(team_slots, vrs_ranks)
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(team_slots)
    accum: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    outcomes: List[Dict] = []
    for _ in range(n_sims):
        _reset_states_inplace(states)
        _, mr = _simulate_bracket_n(
            team_slots, states, player_rows_by_id, team_rank_by_id, has_third_place_decider, rng=rng
        )
        player_points: Dict[str, float] = {}
        player_components: Dict[str, Dict[str, float]] = {}
        for ts in states.values():
            for pid, p in ts.players.items():
                b = accum[ts.team_id].setdefault(pid, {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0})
                b["total"] += p.total_points
                b["rating"] += p.rating_points_total
                b["win"] += p.win_points_total
                b["role"] += p.role_points_total
                b["booster"] += p.booster_points_total
                if store_outcomes:
                    player_points[str(pid)] = float(p.total_points)
                    player_components[str(pid)] = {
                        "total": float(p.total_points),
                        "total_without_booster": float(p.rating_points_total + p.win_points_total + p.role_points_total),
                        "rating": float(p.rating_points_total),
                        "win": float(p.win_points_total),
                        "role": float(p.role_points_total),
                        "booster": float(p.booster_points_total),
                    }
        if store_outcomes:
            outcomes.append({
                "probability": 1.0 / total_sims,
                "bracket": mr,
                "players": player_points,
                "player_components": player_components,
                "player_breakdown": {},
            })
    return accum, outcomes


def _monte_carlo_parallel(
    team_slots: List[int],
    vrs_ranks: Dict[int, int],
    has_third_place_decider: bool,
    n_sims: int,
    store_outcomes: bool,
    progress_callback,
    workers: int,
) -> tuple[Dict[int, Dict], Dict, int, List[Dict]]:
    """Fan the Monte-Carlo sims out over a process pool (Python threads can't
    speed up this CPU-bound loop) and merge the chunk accumulators."""
    base = n_sims // workers
    sizes = [base + (1 if i < n_sims % workers else 0) for i in range(workers)]
    sizes = [s for s in sizes if s > 0]
    seed_root = random.randrange(2**31)
    accum: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    outcomes: List[Dict] = []
    if progress_callback:
        progress_callback(0, n_sims)
    done = 0
    with ProcessPoolExecutor(max_workers=len(sizes)) as ex:
        fut_size = {
            ex.submit(
                _mc_chunk_worker,
                team_slots, vrs_ranks, has_third_place_decider,
                size, seed_root + i, store_outcomes, n_sims,
            ): size
            for i, size in enumerate(sizes)
        }
        for fut in as_completed(fut_size):
            chunk_accum, chunk_outcomes = fut.result()
            for tid, players in chunk_accum.items():
                dst = accum.setdefault(tid, {})
                for pid, sums in players.items():
                    b = dst.setdefault(pid, {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0})
                    for k in b:
                        b[k] += sums[k]
            outcomes.extend(chunk_outcomes)
            done += fut_size[fut]
            if progress_callback:
                progress_callback(done, n_sims)

    denom = float(n_sims) if n_sims > 0 else 1.0
    base_states = initialize_teams(team_slots, vrs_ranks)
    results: Dict[int, Dict] = {}
    for tid in team_slots:
        players_out: Dict[int, Dict[str, float]] = {}
        state_players = base_states[tid].players
        for pid, sums in accum[tid].items():
            ps = state_players.get(pid)
            players_out[pid] = {
                "total_points": sums["total"] / denom,
                "rating_points_total": sums["rating"] / denom,
                "win_points_total": sums["win"] / denom,
                "role_points_total": sums["role"] / denom,
                "booster_points_total": sums["booster"] / denom,
                "total_points_without_booster": (sums["rating"] + sums["win"] + sums["role"]) / denom,
                "role_id": ps.role_id if ps else None,
                "booster_slots": _booster_slots_for(ps),
            }
        results[tid] = {"team_id": tid, "wins": 0, "losses": 0, "players": players_out}

    best_bracket = outcomes[0]["bracket"] if outcomes else {}
    return results, best_bracket, n_sims, outcomes


def _monte_carlo_bracket_totals(
    team_slots: List[int],
    vrs_ranks: Dict[int, int],
    has_third_place_decider: bool = False,
    n_sims: int = _BRACKET_MC_SIMS,
    store_outcomes: bool = True,
    progress_callback=None,
) -> tuple[Dict[int, Dict], Dict, int, List[Dict]]:
    """Monte-Carlo player EVs for a large bracket, matching the exact function's
    return contract. Each sampled bracket is one 'outcome' with probability
    1/n_sims (used by the ceiling / most-likely modes); average EV is the sample
    mean. Per-match breakdowns are dropped to keep the sample set light. Large
    sample counts fan out over a process pool."""
    workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    if n_sims >= _MC_PARALLEL_MIN_SIMS and workers > 1:
        return _monte_carlo_parallel(
            team_slots, vrs_ranks, has_third_place_decider, n_sims, store_outcomes, progress_callback, workers
        )
    accum: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    outcomes: List[Dict] = []
    # Build player states + lookup context ONCE; reset & reuse them per sim.
    states = initialize_teams(team_slots, vrs_ranks)
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(team_slots)
    if progress_callback:
        progress_callback(0, n_sims)
    for s in range(n_sims):
        _reset_states_inplace(states)
        _, mr = _simulate_bracket_n(
            team_slots, states, player_rows_by_id, team_rank_by_id, has_third_place_decider, rng=random
        )
        player_points: Dict[str, float] = {}
        player_components: Dict[str, Dict[str, float]] = {}
        for ts in states.values():
            for pid, p in ts.players.items():
                b = accum[ts.team_id].setdefault(pid, {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0})
                b["total"] += p.total_points
                b["rating"] += p.rating_points_total
                b["win"] += p.win_points_total
                b["role"] += p.role_points_total
                b["booster"] += p.booster_points_total
                if store_outcomes:
                    player_points[str(pid)] = float(p.total_points)
                    player_components[str(pid)] = {
                        "total": float(p.total_points),
                        "total_without_booster": float(p.rating_points_total + p.win_points_total + p.role_points_total),
                        "rating": float(p.rating_points_total),
                        "win": float(p.win_points_total),
                        "role": float(p.role_points_total),
                        "booster": float(p.booster_points_total),
                    }
        if store_outcomes:
            outcomes.append({
                "probability": 1.0 / n_sims,
                "bracket": mr,
                "players": player_points,
                "player_components": player_components,
                "player_breakdown": {},
            })
        if progress_callback and (s + 1) % 500 == 0:
            progress_callback(s + 1, n_sims)

    denom = float(n_sims) if n_sims > 0 else 1.0
    results: Dict[int, Dict] = {}
    for tid in team_slots:
        players_out: Dict[int, Dict[str, float]] = {}
        state_players = states[tid].players
        for pid, sums in accum[tid].items():
            ps = state_players.get(pid)
            players_out[pid] = {
                "total_points": sums["total"] / denom,
                "rating_points_total": sums["rating"] / denom,
                "win_points_total": sums["win"] / denom,
                "role_points_total": sums["role"] / denom,
                "booster_points_total": sums["booster"] / denom,
                "total_points_without_booster": (sums["rating"] + sums["win"] + sums["role"]) / denom,
                "role_id": ps.role_id if ps else None,
                "booster_slots": _booster_slots_for(ps),
            }
        results[tid] = {"team_id": tid, "wins": 0, "losses": 0, "players": players_out}

    best_bracket = outcomes[0]["bracket"] if outcomes else {}
    return results, best_bracket, n_sims, outcomes


def _bracket_player_totals(
    team_slots: List[int],
    vrs_ranks: Dict[int, int],
    has_third_place_decider: bool = False,
    store_outcomes: bool = True,
    progress_callback=None,
    quarters_override: List[tuple] | None = None,
    sf_pairs_resolver=None,
    mc_sims: int | None = None,
) -> tuple[Dict[int, Dict], Dict, int, List[Dict]]:
    """Route a bracket to exact enumeration (small fields + the 8-team bounty
    variant) or Monte-Carlo (large fields like 16-team). `mc_sims` overrides the
    Monte-Carlo sample count (ignored by the exact path, which is deterministic)."""
    n = len(team_slots)
    if n == 6:
        # Byes bracket (group winners straight to the semis) — its own exact
        # path; the generic enumerator assumes a full power-of-two field.
        results, best, count, outcomes = _exact_bracket6_player_totals(
            team_slots, vrs_ranks, progress_callback=progress_callback
        )
        return results, best, count, outcomes
    exact_outcomes = 2 ** max(0, n - 1)
    if quarters_override or sf_pairs_resolver or exact_outcomes <= _BRACKET_EXACT_OUTCOME_LIMIT:
        return _exact_weighted_player_totals(
            team_slots, vrs_ranks,
            has_third_place_decider=has_third_place_decider,
            progress_callback=progress_callback,
            quarters_override=quarters_override,
            sf_pairs_resolver=sf_pairs_resolver,
        )
    return _monte_carlo_bracket_totals(
        team_slots, vrs_ranks,
        has_third_place_decider=has_third_place_decider,
        n_sims=int(mc_sims) if mc_sims else _BRACKET_MC_SIMS,
        store_outcomes=store_outcomes,
        progress_callback=progress_callback,
    )


def _normalize_playoff_payload(payload: dict) -> dict:
    slots: List[int] = payload.get("team_slots") or []
    if len(slots) not in _ALLOWED_BRACKET_SIZES:
        raise HTTPException(status_code=400, detail="team_slots must contain 2, 4, 6, 8, or 16 team IDs")
    has_third_place_decider = bool(payload.get("has_third_place_decider", False))
    if len(slots) == 6:
        has_third_place_decider = False  # the byes bracket has no decider
    normalized = {
        "team_slots": [int(x) for x in slots],
        "has_third_place_decider": has_third_place_decider,
    }
    mc_sims = _clamp_mc_sims(payload.get("mc_sims"))
    if mc_sims:
        normalized["mc_sims"] = mc_sims
    if _variant(payload.get("variant")) == "bounty":
        qf_pairs_raw = payload.get("qf_pairs") or []
        qf_pairs = [[int(a), int(b)] for a, b in qf_pairs_raw] if len(qf_pairs_raw) == 4 else []
        used = [tid for pair in qf_pairs for tid in pair]
        if sorted(used) != sorted(normalized["team_slots"]):
            raise HTTPException(status_code=400, detail="qf_pairs must pair all 8 teams exactly once (finish the draft first)")
        normalized["variant"] = "bounty"
        normalized["qf_pairs"] = qf_pairs
        normalized["sf_picks"] = dict(payload.get("sf_picks") or {})
        normalized["has_third_place_decider"] = False
    return normalized


def _bounty_sf_pairs_resolver(team_slots: List[int], sf_picks: dict):
    """SF pairings for a set of QF winners in the Bounty re-draft format.

    `sf_picks` maps a scenario key (the 4 surviving team ids, sorted ascending,
    joined with '-') to explicit SF pairs [[a, b], [c, d]]. Scenarios without a
    stored pick fall back to the default: the highest-seeded bottom-half
    survivor drafts the weakest (lowest-seeded) top-half survivor.
    """
    seed_index = {int(tid): idx for idx, tid in enumerate(team_slots)}

    def resolve(qf_winners: List[int]):
        winners = [int(t) for t in qf_winners]
        key = "-".join(str(t) for t in sorted(winners))
        picked = sf_picks.get(key)
        if picked and len(picked) == 2:
            pairs = [[int(a), int(b)] for a, b in picked]
            if sorted(t for pair in pairs for t in pair) == sorted(winners):
                return [(pairs[0][0], pairs[0][1]), (pairs[1][0], pairs[1][1])]
        surv = sorted(winners, key=lambda t: seed_index.get(t, 99))
        return [(surv[2], surv[1]), (surv[3], surv[0])]

    return resolve


_STAGE_MAIN_ORDER = ["round_of_32", "round_of_16", "quarters", "semis", "final"]


def _stage_stats_from_outcomes(outcomes: List[Dict], results: Dict[int, Dict]) -> Dict:
    """Per-round decomposition of the enumerated outcomes.

    Returns each team's probability of appearing in (and winning) every round,
    and each player's probability-weighted expected points per round split by
    component (rating/win/role/booster; total includes booster, so each round
    row is self-consistent and the rounds sum to the player's full total).
    Elimination penalties are split across the rounds the team missed; the
    byes' +6 pad (applied without a breakdown row) lands on the round they
    skipped. Player rows are empty when the outcomes carry no per-match
    breakdowns (Monte-Carlo fields)."""
    pid_team: Dict[str, int] = {}
    for tid, tdata in results.items():
        for pid in (tdata.get("players") or {}):
            pid_team[str(pid)] = int(tid)
    team_ids = [int(t) for t in results.keys()]

    main_present = [s for s in _STAGE_MAIN_ORDER if any((o.get("bracket") or {}).get(s) for o in outcomes)]
    has_third = any((o.get("bracket") or {}).get("third_place") for o in outcomes)
    stages = main_present + (["third_place"] if has_third else [])
    if not stages:
        return {"stages": [], "teams": {}, "players": {}}

    total_p = 0.0
    reach = {tid: {s: 0.0 for s in stages} for tid in team_ids}
    champion = {tid: 0.0 for tid in team_ids}
    player_stage: Dict[str, Dict[str, Dict[str, float]]] = {}
    # pid -> stage -> opponent tid -> weighted sums (for conditional matchup EVs)
    player_opps: Dict[str, Dict[str, Dict[int, Dict[str, float]]]] = {}
    # pid -> stage -> booster used that round (slot = team's Nth match, so it is
    # the same booster in every outcome that reaches the round)
    player_boost_meta: Dict[str, Dict[str, dict]] = {}

    for o in outcomes:
        p = float(o.get("probability") or 0.0)
        if p <= 0:
            continue
        total_p += p
        bracket = o.get("bracket") or {}
        stage_teams: Dict[str, set] = {}
        for s in stages:
            in_stage: set = set()
            for m in bracket.get(s) or []:
                in_stage.update(int(t) for t in m.get("teams") or [])
            stage_teams[s] = in_stage
            for tid in in_stage:
                if tid in reach:
                    reach[tid][s] += p
        final_matches = bracket.get("final") or []
        if final_matches:
            w = int(final_matches[0].get("winner") or 0)
            if w in champion:
                champion[w] += p

        breakdown = o.get("player_breakdown") or {}
        if not breakdown:
            continue
        components = o.get("player_components") or {}
        # Ordered rounds each team actually played this outcome (third-place
        # sorts last, matching play order).
        team_played: Dict[int, List[str]] = {}
        for s in stages:
            for tid in stage_teams[s]:
                team_played.setdefault(tid, []).append(s)

        for pid_str, rows in breakdown.items():
            tid = pid_team.get(pid_str)
            if tid is None:
                continue
            played = team_played.get(tid, [])
            main_played = [s for s in played if s != "third_place"]
            bucket = player_stage.setdefault(
                pid_str,
                {s: {"rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0, "total": 0.0} for s in stages},
            )

            def add(stage: str, rating: float, win: float, role: float, booster: float) -> None:
                cell = bucket[stage]
                cell["rating"] += p * rating
                cell["win"] += p * win
                cell["role"] += p * role
                cell["booster"] += p * booster
                cell["total"] += p * (rating + win + role + booster)

            stage_iter = iter(played)
            row_sums = {"rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0}
            for row in rows:
                rr = float(row.get("rating_points") or 0.0)
                rw = float(row.get("win_points") or 0.0)
                ro = float(row.get("role_points") or 0.0)
                rb = float(row.get("booster_points") or 0.0)
                row_sums["rating"] += rr
                row_sums["win"] += rw
                row_sums["role"] += ro
                row_sums["booster"] += rb
                if str(row.get("match_type") or "").upper() == "ELIMINATION":
                    # The penalty covers every main-line round after the team's
                    # last played one — spread it evenly across those rounds.
                    last = main_played[-1] if main_played else None
                    after = [
                        s
                        for s in main_present
                        if (last is None or main_present.index(s) > main_present.index(last))
                        and tid not in stage_teams[s]
                    ]
                    targets = after or (main_played[-1:] or main_present[-1:])
                    n = len(targets)
                    for s in targets:
                        add(s, rr / n, rw / n, ro / n, rb / n)
                else:
                    s = next(stage_iter, None)
                    if s is None:
                        s = played[-1] if played else main_present[0]
                    add(s, rr, rw, ro, rb)
                    opp_raw = row.get("opponent_team_id")
                    if opp_raw is not None:
                        om = (
                            player_opps.setdefault(pid_str, {})
                            .setdefault(s, {})
                            .setdefault(
                                int(opp_raw),
                                {
                                    "prob": 0.0,
                                    "rating": 0.0,
                                    "win": 0.0,
                                    "role": 0.0,
                                    "booster": 0.0,
                                    "total": 0.0,
                                    "win_prob": 0.0,
                                    "rank": row.get("opponent_rank"),
                                },
                            )
                        )
                        om["prob"] += p
                        om["rating"] += p * rr
                        om["win"] += p * rw
                        om["role"] += p * ro
                        om["booster"] += p * rb
                        om["total"] += p * (rr + rw + ro + rb)
                        if row.get("did_win"):
                            om["win_prob"] += p
                    bm = player_boost_meta.setdefault(pid_str, {})
                    if s not in bm and row.get("booster_id") is not None:
                        bm[s] = {
                            "booster_id": row.get("booster_id"),
                            "booster_name": row.get("booster_name"),
                            "booster_rate": float(row.get("booster_trigger_rate") or 0.0),
                        }
            # Points applied without a breakdown row (the byes' +6 pad) belong
            # to the round(s) the team skipped before its first match.
            comp = components.get(pid_str) or {}
            residuals = {
                "rating": float(comp.get("rating") or 0.0) - row_sums["rating"],
                "win": float(comp.get("win") or 0.0) - row_sums["win"],
                "role": float(comp.get("role") or 0.0) - row_sums["role"],
                "booster": float(comp.get("booster") or 0.0) - row_sums["booster"],
            }
            if any(abs(v) > 1e-9 for v in residuals.values()):
                first = main_played[0] if main_played else None
                before = [
                    s
                    for s in main_present
                    if first is not None
                    and main_present.index(s) < main_present.index(first)
                    and tid not in stage_teams[s]
                ]
                targets = before or (main_played[:1] or main_present[:1])
                n = len(targets)
                for s in targets:
                    add(s, residuals["rating"] / n, residuals["win"] / n, residuals["role"] / n, residuals["booster"] / n)

    denom = total_p if total_p > 0 else 1.0
    return {
        "stages": stages,
        "teams": {
            str(tid): {
                "reach": {s: reach[tid][s] / denom for s in stages},
                "champion": champion[tid] / denom,
            }
            for tid in team_ids
        },
        "players": {
            pid: {
                s: _finish_stage_cell(
                    cell,
                    denom,
                    (player_boost_meta.get(pid) or {}).get(s),
                    (player_opps.get(pid) or {}).get(s),
                )
                for s, cell in svals.items()
            }
            for pid, svals in player_stage.items()
        },
    }


def _finish_stage_cell(cell: Dict[str, float], denom: float, boost_meta: dict | None, opps: dict | None) -> dict:
    """Normalize one player-round cell and attach the round's booster plus the
    per-opponent matchup rows: `prob` is the unconditional chance of that
    matchup; the point values are conditional (expected in that match IF it
    happens), so a weak opponent showing a bigger number answers 'does he do
    better if a bad team makes it here'."""
    out: dict = {k: v / denom for k, v in cell.items()}
    if boost_meta:
        out.update(boost_meta)
    opp_list = []
    for otid, om in (opps or {}).items():
        q = float(om.get("prob") or 0.0)
        if q <= 0:
            continue
        opp_list.append(
            {
                "team_id": int(otid),
                "rank": om.get("rank"),
                "prob": q / denom,
                "win_chance": om["win_prob"] / q,
                "total": om["total"] / q,
                "rating": om["rating"] / q,
                "win": om["win"] / q,
                "role": om["role"] / q,
                "booster": om["booster"] / q,
            }
        )
    opp_list.sort(key=lambda x: -x["prob"])
    if opp_list:
        out["opponents"] = opp_list
    return out


def _compute_playoff_result(payload: dict, progress_callback=None) -> dict:
    slots = payload["team_slots"]
    has_third_place_decider = bool(payload.get("has_third_place_decider", False))

    # vrs_ranks not relevant here (use default 999)
    vrs_ranks = {tid: 999 for tid in slots}

    quarters_override = None
    sf_pairs_resolver = None
    if _variant(payload.get("variant")) == "bounty":
        quarters_override = [tuple(pair) for pair in payload.get("qf_pairs") or []]
        sf_pairs_resolver = _bounty_sf_pairs_resolver(slots, payload.get("sf_picks") or {})

    exact_players, best_bracket, outcomes_count, outcomes = _bracket_player_totals(
        slots,
        vrs_ranks,
        has_third_place_decider=has_third_place_decider,
        progress_callback=progress_callback,
        quarters_override=quarters_override,
        sf_pairs_resolver=sf_pairs_resolver,
        mc_sims=_clamp_mc_sims(payload.get("mc_sims")),
    )
    return {
        "bracket": best_bracket,
        "teams": exact_players,
        "method": "exact_enumeration",
        "outcomes_count": outcomes_count,
        "outcomes": outcomes,
        "stage_stats": _stage_stats_from_outcomes(outcomes, exact_players),
        "has_third_place_decider": has_third_place_decider,
    }


@router.post("/autofill-from-hltv-event")
def autofill_playoff_from_hltv_event(payload: dict | None = None):
    """Scrape (or reuse the stored snapshot of) the linked HLTV event page and
    return the single-elimination playoff bracket seeding as this app's team IDs
    in bracket order, plus the field size (16/8/4). The frontend fills the seed
    slots and bracket-size selector from it."""
    # Lazy import: groups.py imports from this module at load time, so importing
    # it at module top here would be a circular import.
    from backend.routes.groups import autofill_event_playoff

    body = payload or {}
    result = autofill_event_playoff(
        hltv_event_url=str(body.get("hltv_event_url") or "").strip(),
        hltv_event_id=body.get("hltv_event_id"),
        fantasy_event_id=body.get("fantasy_event_id") or body.get("event_id"),
    )
    return {"status": "ok", **result}


@router.post("/run")
def run_playoff(payload: dict):
    """
    Simulate a single-elimination playoff bracket (BO3 all matches).

    Expects:
      - team_slots: list of 8 team_ids in bracket order:
          [QF1_A, QF1_B, QF2_A, QF2_B, QF3_A, QF3_B, QF4_A, QF4_B]
    """
    normalized = _normalize_playoff_payload(payload)
    response = _compute_playoff_result(normalized)
    save_latest_playoff(normalized, response, normalized.get("variant"))
    return response


def _run_playoff_job(job_id: str, payload: dict) -> None:
    def _update_progress(processed: int, total: int) -> None:
        with PLAYOFF_JOBS_LOCK:
            job = PLAYOFF_JOBS.get(job_id)
            if not job:
                return
            job["processed_sims"] = int(processed)
            job["total_sims"] = int(total)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["updated_at"] = time.time()

    with PLAYOFF_JOBS_LOCK:
        job = PLAYOFF_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = time.time()

    try:
        result = _compute_playoff_result(payload, progress_callback=_update_progress)
        save_latest_playoff(payload, result, payload.get("variant"))
        with PLAYOFF_JOBS_LOCK:
            job = PLAYOFF_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["result"] = result
            total = int(job.get("total_sims", 128))
            job["processed_sims"] = total
            job["total_sims"] = total
            job["progress"] = 1.0
            job["updated_at"] = time.time()
    except Exception as exc:
        with PLAYOFF_JOBS_LOCK:
            job = PLAYOFF_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = time.time()


@router.post("/start")
def start_playoff(payload: dict):
    normalized = _normalize_playoff_payload(payload)
    n_slots = len(normalized.get("team_slots") or [])
    if 2 ** max(0, n_slots - 1) <= _BRACKET_EXACT_OUTCOME_LIMIT:
        total_outcomes = (2 ** max(0, n_slots - 1)) * (2 if normalized.get("has_third_place_decider") else 1)
    else:
        # Monte-Carlo sample count for large fields (user-configurable).
        total_outcomes = int(normalized.get("mc_sims") or _BRACKET_MC_SIMS)
    job_id = str(int(time.time() * 1000000))
    with PLAYOFF_JOBS_LOCK:
        PLAYOFF_JOBS[job_id] = {
            "status": "queued",
            "error": "",
            "progress": 0.0,
            "processed_sims": 0,
            "total_sims": int(total_outcomes),
            "result": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    worker = threading.Thread(target=_run_playoff_job, args=(job_id, normalized), daemon=True)
    worker.start()
    return {"job_id": job_id}


@router.get("/job/{job_id}")
def get_playoff_job(job_id: str):
    with PLAYOFF_JOBS_LOCK:
        job = PLAYOFF_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_id not found")
        out = dict(job)
    return {
        "job_id": job_id,
        "status": out.get("status", "queued"),
        "error": out.get("error", ""),
        "progress": out.get("progress", 0.0),
        "processed_sims": out.get("processed_sims", 0),
        "total_sims": out.get("total_sims", 0),
        "result": out.get("result"),
    }


def simulate_playoff_fantasy(team_slots: List[int], n_sims: int = 1, return_runs: bool = False):
    if n_sims <= 0:
        raise HTTPException(status_code=400, detail="n_sims must be positive")
    vrs_ranks = {tid: 999 for tid in team_slots}
    exact_results, _, _, _ = _bracket_player_totals(team_slots, vrs_ranks, has_third_place_decider=False, store_outcomes=False)

    results: Dict[int, Dict] = {}
    for tid in team_slots:
        players_out: Dict[int, Dict[str, float]] = {}
        for pid, comps in (exact_results.get(tid, {}).get("players", {}) or {}).items():
            players_out[pid] = {
                "total": float(comps.get("total_points", 0.0)),
                "rating": float(comps.get("rating_points_total", 0.0)),
                "win": float(comps.get("win_points_total", 0.0)),
                "role": float(comps.get("role_points_total", 0.0)),
                "booster": float(comps.get("booster_points_total", 0.0)),
            }
        results[tid] = {"players": players_out}

    if return_runs:
        return results, []
    return results


def _build_players_info_from_sim_results(sim_results: Dict, exclude: set[int]):
    players_info = []
    for tid, team_res in (sim_results or {}).items():
        tid_int = int(tid)
        for pid_key, comps in (team_res.get("players", {}) or {}).items():
            pid = int(pid_key)
            if pid in exclude:
                continue
            row = get_player(pid)
            if not row:
                continue
            rating_ev = float(comps.get("rating_points_total", comps.get("rating", 0.0)))
            win_ev = float(comps.get("win_points_total", comps.get("win", 0.0)))
            role_ev = float(comps.get("role_points_total", comps.get("role", 0.0)))
            players_info.append(
                {
                    "player_id": pid,
                    "name": row.get("name", f"Player {pid}"),
                    "team_id": tid_int,
                    "price": int(row.get("price", 0)),
                    "rating_ev": rating_ev,
                    "win_ev": win_ev,
                    "role_ev": role_ev,
                    "booster_ev": 0.0,
                    "raw_booster_ev": float(comps.get("booster_points_total", comps.get("booster", 0.0))),
                    "total_ev": rating_ev + win_ev + role_ev,
                }
            )
    return players_info


def _optimize_playoff_teams(
    players_info: list[dict],
    include: set[int],
    budget: int,
    max_per_team: int,
    progress_callback=None,
):
    return optimize_rosters(
        players_info,
        include,
        budget,
        max_per_team,
        progress_callback=progress_callback,
        include_error_suffix="in bracket teams",
    )


# A pool with more rosters than this triggers the two-phase optimiser instead
# of the exhaustive per-outcome scan. 8-team = C(40,5) = 658k (stays exact);
# 16-team = C(80,5) = 24M (would be billions of roster×outcome scores).
_PLAYOFF_EXACT_ROSTER_LIMIT = 2_000_000
# Candidates deep-scored (ceiling / most-likely) in phase 2 of the two-phase run.
_PLAYOFF_TWO_PHASE_TOPK = 12000


def _two_phase_scan_chunk(
    price_arr: list,
    team_arr: list,
    val_arr: list,
    include_idx: tuple,
    budget: int,
    max_per_team: int,
    topk: int,
    i0_list: list,
    n: int,
) -> list:
    """Phase-1 roster scan for a slice of first-player indexes, run in a worker
    process. Returns the slice's local top-K (val, counter, combo, cost) heap
    entries; the parent merges slices into the global top-K."""
    heap: list = []
    counter = 0
    heappush, heapreplace = heapq.heappush, heapq.heapreplace
    for i0 in i0_list:
        p0, t0, v0 = price_arr[i0], team_arr[i0], val_arr[i0]
        for i1, i2, i3, i4 in itertools.combinations(range(i0 + 1, n), 4):
            cost = p0 + price_arr[i1] + price_arr[i2] + price_arr[i3] + price_arr[i4]
            if cost > budget:
                continue
            over = False
            tc: Dict[int, int] = {}
            for t in (t0, team_arr[i1], team_arr[i2], team_arr[i3], team_arr[i4]):
                v = tc.get(t, 0) + 1
                if v > max_per_team:
                    over = True
                    break
                tc[t] = v
            if over:
                continue
            c = (i0, i1, i2, i3, i4)
            if include_idx and not all(ii in c for ii in include_idx):
                continue
            val = v0 + val_arr[i1] + val_arr[i2] + val_arr[i3] + val_arr[i4]
            if len(heap) < topk:
                heappush(heap, (val, counter, c, cost))
                counter += 1
            elif val > heap[0][0]:
                heapreplace(heap, (val, counter, c, cost))
                counter += 1
    return list(heap)


def _two_phase_score_chunk(
    top_slice: list,
    offset: int,
    pid_arr: list,
    ev_arr: list,
    out_arr: dict,
    players_meta: dict,
    role_scores_by_player: dict,
    probs: list,
    mode: str,
    N: int,
) -> tuple:
    """Phase-2 per-outcome scoring for a slice of candidate rosters, run in a
    worker process. Returns the slice's serialized teams (player-level ceiling
    fields filled by the parent, which holds the outcome tables), each
    candidate's ceiling outcome index, and the slice-local best-per-outcome
    tracking (with GLOBAL candidate positions) for the parent to merge."""
    from backend.services.role_assignment import best_role_assignment_for_team

    best_scores = [-1e18] * N
    best_pos: List[List[int]] = [[] for _ in range(N)]
    teams_out: list = []
    argmaxes: list = []
    for local_pos, (val, _, c, cost) in enumerate(top_slice):
        pos = offset + local_pos
        a0, a1, a2, a3, a4 = out_arr[c[0]], out_arr[c[1]], out_arr[c[2]], out_arr[c[3]], out_arr[c[4]]
        max_s = -1e18
        cnt = 0
        argmax = 0
        for o in range(N):
            s = a0[o] + a1[o] + a2[o] + a3[o] + a4[o]
            if s > max_s + 1e-9:
                max_s = s
                cnt = 1
                argmax = o
            elif s >= max_s - 1e-9:
                cnt += 1
            bs = best_scores[o]
            if s > bs + 1e-9:
                best_scores[o] = s
                best_pos[o] = [pos]
            elif s >= bs - 1e-9:
                best_pos[o].append(pos)
        pids = [pid_arr[i] for i in c]
        assignment, _ = best_role_assignment_for_team(pids, role_scores_by_player)
        roles = [str((assignment or {}).get(pid, "-")) for pid in pids]
        ev_no_booster = ev_arr[c[0]] + ev_arr[c[1]] + ev_arr[c[2]] + ev_arr[c[3]] + ev_arr[c[4]]
        serialized = serialize_roster(players_meta, pids, roles, ev_no_booster, cost)
        serialized["average_ev"] = float(val)
        serialized["ceiling_points"] = float(max_s if N else val)
        serialized["ceiling_probability"] = float(cnt) * (probs[argmax] if N else 0.0)
        serialized["outcome_wins"] = 0.0
        serialized["outcome_win_probability"] = 0.0
        serialized["mode"] = mode
        teams_out.append(serialized)
        argmaxes.append(argmax)
    return teams_out, argmaxes, best_scores, best_pos


def _optimize_playoff_teams_two_phase(
    players_info: list[dict],
    outcomes: List[Dict],
    include: set[int],
    budget: int,
    max_per_team: int,
    mode: str,
    progress_callback=None,
    topk: int = _PLAYOFF_TWO_PHASE_TOPK,
):
    """Scalable optimiser for very large pools (e.g. 16-team playoffs: ~80
    players → C(80,5) = 24M rosters, which the exhaustive per-outcome scan would
    score against thousands of outcomes = hundreds of billions of ops).

    Phase 1 ranks EVERY valid roster by its exact additive expected score — the
    sum of each player's expected points including their raw booster, which
    equals the probability-weighted roster total over the outcome table — and
    keeps the top `topk` in a bounded heap. Phase 2 computes the per-outcome
    metrics (ceiling, most-likely-winner) only on those candidates. So Average
    Value is exact top-K, while ceiling / most-likely are exact among the
    strongest candidates (their true best is virtually always a strong-average
    roster). Smaller fields keep the exact enumerator for full precision.
    """
    from backend.services.role_assignment import best_role_assignment_for_team, extract_role_scores_for_player

    include = {int(x) for x in (include or set())}
    if len(players_info) < 5:
        return {"error": "Not enough players after exclusions"}
    players_meta = {str(p["player_id"]): p for p in players_info}
    n = len(players_info)
    total_combinations = math.comb(n, 5)

    # Sort by full per-player value (expected points incl. raw booster) so the
    # heap warms up with strong rosters quickly.
    players_sorted = sorted(
        players_info,
        key=lambda p: -(float(p.get("total_ev", 0.0)) + float(p.get("raw_booster_ev", 0.0))),
    )
    pid_arr = [int(p["player_id"]) for p in players_sorted]
    price_arr = [int(p.get("price") or 0) for p in players_sorted]
    team_arr = [int(p.get("team_id") or 0) for p in players_sorted]
    ev_arr = [float(p.get("total_ev") or 0.0) for p in players_sorted]  # excl. booster (roster total_ev)
    val_arr = [ev_arr[i] + float(players_sorted[i].get("raw_booster_ev") or 0.0) for i in range(n)]  # E[total_points]
    idx_by_pid = {pid_arr[i]: i for i in range(n)}
    include_idx = [idx_by_pid[p] for p in include if p in idx_by_pid]
    if len(include_idx) != len(include):
        missing = [p for p in include if p not in idx_by_pid]
        return {"error": f"Included players not available in bracket teams: {missing}"}
    include_idx = tuple(include_idx)

    # ---- Phase 1: exact top-K rosters by additive expected score. ----
    # The scan is pure arithmetic over C(n,5) index tuples, so it fans out over
    # a process pool: each worker sweeps a balanced slice of first-player
    # indexes and returns its local top-K, which merge exactly into the global
    # top-K (Python threads would gain nothing on this CPU-bound loop).
    workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    heap: list = []  # (value, counter, combo_idx_tuple, cost)
    if workers > 1 and n > 5:
        chunk_weights = [math.comb(n - 1 - i0, 4) for i0 in range(n - 4)]
        target = sum(chunk_weights) / workers
        slices: list[list[int]] = [[]]
        acc = 0.0
        for i0, w in enumerate(chunk_weights):
            if acc >= target and len(slices) < workers:
                slices.append([])
                acc = 0.0
            slices[-1].append(i0)
            acc += w
        slices = [s for s in slices if s]
        if progress_callback:
            progress_callback(0, total_combinations)
        done_weight = 0
        with ProcessPoolExecutor(max_workers=len(slices)) as ex:
            fut_weight = {
                ex.submit(
                    _two_phase_scan_chunk,
                    price_arr, team_arr, val_arr, include_idx,
                    budget, max_per_team, topk, s, n,
                ): sum(chunk_weights[i0] for i0 in s)
                for s in slices
            }
            merge_counter = 0
            heappush, heapreplace = heapq.heappush, heapq.heapreplace
            for fut in as_completed(fut_weight):
                for val, _, c, cost in fut.result():
                    if len(heap) < topk:
                        heappush(heap, (val, merge_counter, c, cost))
                        merge_counter += 1
                    elif val > heap[0][0]:
                        heapreplace(heap, (val, merge_counter, c, cost))
                        merge_counter += 1
                done_weight += fut_weight[fut]
                if progress_callback:
                    progress_callback(done_weight, total_combinations)
    else:
        counter = 0
        processed = 0
        heappush, heapreplace = heapq.heappush, heapq.heapreplace
        for c in itertools.combinations(range(n), 5):
            processed += 1
            if progress_callback and (processed & 0x3FFFF) == 0:  # ~every 262k
                progress_callback(processed, total_combinations)
            i0, i1, i2, i3, i4 = c
            cost = price_arr[i0] + price_arr[i1] + price_arr[i2] + price_arr[i3] + price_arr[i4]
            if cost > budget:
                continue
            over = False
            tc: Dict[int, int] = {}
            for t in (team_arr[i0], team_arr[i1], team_arr[i2], team_arr[i3], team_arr[i4]):
                v = tc.get(t, 0) + 1
                if v > max_per_team:
                    over = True
                    break
                tc[t] = v
            if over:
                continue
            if include_idx and not all(ii in c for ii in include_idx):
                continue
            val = val_arr[i0] + val_arr[i1] + val_arr[i2] + val_arr[i3] + val_arr[i4]
            if len(heap) < topk:
                heappush(heap, (val, counter, c, cost))
                counter += 1
            elif val > heap[0][0]:
                heapreplace(heap, (val, counter, c, cost))
                counter += 1
    if progress_callback:
        progress_callback(total_combinations, total_combinations)

    # ---- Phase 2: exact per-outcome metrics on the candidate set. ----
    top = list(heap)
    N = len(outcomes)
    probs = [float(o.get("probability") or 0.0) for o in outcomes]
    outcome_players = [o.get("players") or {} for o in outcomes]
    used = set()
    for _, _, c, _ in top:
        used.update(c)
    # Per-candidate-player score across outcomes (string pid keys as stored).
    out_arr: Dict[int, list] = {i: [float(op.get(str(pid_arr[i]), 0.0)) for op in outcome_players] for i in used}
    role_scores_by_player = {pid_arr[i]: extract_role_scores_for_player(get_player(pid_arr[i]) or {}) for i in used}

    best_scores = [-1e18] * N
    best_pos: List[List[int]] = [[] for _ in range(N)]
    teams_out: list = []
    if workers > 1 and len(top) * max(1, N) >= 2_000_000:
        # Fan the candidate scoring out over the pool and merge the
        # best-per-outcome tracking (tie-tolerant) in the parent.
        chunk = math.ceil(len(top) / workers)
        slices2 = [(i, top[i : i + chunk]) for i in range(0, len(top), chunk)]
        teams_by_pos: list = [None] * len(top)
        argmax_by_pos: list = [0] * len(top)
        with ProcessPoolExecutor(max_workers=len(slices2)) as ex:
            futs = [
                ex.submit(
                    _two_phase_score_chunk,
                    t_slice, offset, pid_arr, ev_arr, out_arr,
                    players_meta, role_scores_by_player, probs, mode, N,
                )
                for offset, t_slice in slices2
            ]
            offsets = {futs[i]: slices2[i][0] for i in range(len(futs))}
            for fut in as_completed(futs):
                s_teams, s_argmax, s_best_scores, s_best_pos = fut.result()
                off = offsets[fut]
                for j, team in enumerate(s_teams):
                    teams_by_pos[off + j] = team
                    argmax_by_pos[off + j] = s_argmax[j]
                for o in range(N):
                    s = s_best_scores[o]
                    if s > best_scores[o] + 1e-9:
                        best_scores[o] = s
                        best_pos[o] = list(s_best_pos[o])
                    elif s >= best_scores[o] - 1e-9:
                        best_pos[o].extend(s_best_pos[o])
        teams_out = teams_by_pos
        # Player-level ceiling fields need the outcome tables, which stay in
        # the parent instead of being shipped to every worker.
        for pos, serialized in enumerate(teams_out):
            ceiling_scores = outcome_players[argmax_by_pos[pos]] if N else {}
            for player in serialized.get("players") or []:
                pid = int(player.get("player_id") or 0)
                if mode == "single_outcome":
                    player["mode_score"] = float(ceiling_scores.get(str(pid), 0.0))
                else:
                    player["mode_score"] = float(player.get("total_ev") or 0.0)
                player["ceiling_score"] = float(ceiling_scores.get(str(pid), 0.0))
    else:
        for pos, (val, _, c, cost) in enumerate(top):
            a0, a1, a2, a3, a4 = out_arr[c[0]], out_arr[c[1]], out_arr[c[2]], out_arr[c[3]], out_arr[c[4]]
            max_s = -1e18
            cnt = 0
            argmax = 0
            for o in range(N):
                s = a0[o] + a1[o] + a2[o] + a3[o] + a4[o]
                if s > max_s + 1e-9:
                    max_s = s
                    cnt = 1
                    argmax = o
                elif s >= max_s - 1e-9:
                    cnt += 1
                bs = best_scores[o]
                if s > bs + 1e-9:
                    best_scores[o] = s
                    best_pos[o] = [pos]
                elif s >= bs - 1e-9:
                    best_pos[o].append(pos)
            pids = [pid_arr[i] for i in c]
            assignment, _ = best_role_assignment_for_team(pids, role_scores_by_player)
            roles = [str((assignment or {}).get(pid, "-")) for pid in pids]
            ev_no_booster = ev_arr[c[0]] + ev_arr[c[1]] + ev_arr[c[2]] + ev_arr[c[3]] + ev_arr[c[4]]
            serialized = serialize_roster(players_meta, pids, roles, ev_no_booster, cost)
            ceiling_scores = outcome_players[argmax] if N else {}
            for player in serialized.get("players") or []:
                pid = int(player.get("player_id") or 0)
                if mode == "single_outcome":
                    player["mode_score"] = float(ceiling_scores.get(str(pid), 0.0))
                else:
                    player["mode_score"] = float(player.get("total_ev") or 0.0)
                player["ceiling_score"] = float(ceiling_scores.get(str(pid), 0.0))
            serialized["average_ev"] = float(val)
            serialized["ceiling_points"] = float(max_s if N else val)
            serialized["ceiling_probability"] = float(cnt) * (probs[argmax] if N else 0.0)
            serialized["outcome_wins"] = 0.0
            serialized["outcome_win_probability"] = 0.0
            serialized["mode"] = mode
            teams_out.append(serialized)

    for o, winners in enumerate(best_pos):
        if not winners:
            continue
        share = 1.0 / float(len(winners))
        pshare = probs[o] * share
        for pos in winners:
            teams_out[pos]["outcome_wins"] += share
            teams_out[pos]["outcome_win_probability"] += pshare
            teams_out[pos].setdefault("winning_outcome_indexes", []).append(o)

    if mode == "single_outcome":
        teams_out.sort(key=lambda t: (float(t.get("ceiling_points", 0.0)), float(t.get("average_ev", 0.0))), reverse=True)
    elif mode == "most_outcomes":
        teams_out.sort(
            key=lambda t: (
                float(t.get("outcome_win_probability", 0.0)),
                float(t.get("outcome_wins", 0.0)),
                float(t.get("average_ev", 0.0)),
            ),
            reverse=True,
        )
    else:
        teams_out.sort(key=lambda t: float(t.get("average_ev", 0.0)), reverse=True)

    return {
        "top_teams": teams_out[:10],
        "all_teams": teams_out,
        "player_count": n,
        "processed_combinations": int(total_combinations),
        "total_combinations": int(total_combinations),
        "candidate_count": len(teams_out),
        "approximate": True,
        "mode": mode,
    }


def _optimize_playoff_teams_by_outcomes(
    players_info: list[dict],
    outcomes: List[Dict],
    include: set[int],
    budget: int,
    max_per_team: int,
    mode: str,
    progress_callback=None,
):
    # Very large pools (16-team playoffs) are intractable to enumerate exactly
    # against every outcome, so fall back to the two-phase candidate optimiser.
    if len(players_info) >= 5 and math.comb(len(players_info), 5) > _PLAYOFF_EXACT_ROSTER_LIMIT:
        return _optimize_playoff_teams_two_phase(
            players_info, outcomes or [], include, budget, max_per_team, mode, progress_callback
        )
    if mode == "average":
        return _optimize_playoff_teams(players_info, include, budget, max_per_team, progress_callback=progress_callback)
    if len(players_info) < 5:
        return {"error": "Not enough players after exclusions"}
    if not outcomes:
        return {"error": "No playoff outcome table found. Re-run Playoff Bracket first."}

    available_ids = {int(player["player_id"]) for player in players_info}
    missing_includes = [pid for pid in include if pid not in available_ids]
    if missing_includes:
        return {"error": f"Included players not available in bracket teams: {missing_includes}"}

    players_meta = {str(player["player_id"]): player for player in players_info}
    outcome_player_scores = [
        {int(pid): float(score) for pid, score in (outcome.get("players") or {}).items()}
        for outcome in outcomes
    ]
    best_scores = [-1e18 for _ in outcome_player_scores]
    best_roster_indexes: List[List[int]] = [[] for _ in outcome_player_scores]
    valid_teams = []

    for roster_idx, roster in enumerate(iter_valid_rosters(players_info, include, budget, max_per_team, progress_callback)):
        pids = [int(pid) for pid in roster["pids"]]
        scores = [sum(outcome_scores.get(pid, 0.0) for pid in pids) for outcome_scores in outcome_player_scores]
        max_score = max(scores) if scores else 0.0
        ceiling_idx = scores.index(max_score) if scores else -1
        expected_score = sum(float(outcomes[idx].get("probability") or 0.0) * score for idx, score in enumerate(scores))
        ceiling_probability = sum(
            float(outcomes[idx].get("probability") or 0.0)
            for idx, score in enumerate(scores)
            if abs(score - max_score) <= 1e-9
        )
        for idx, score in enumerate(scores):
            if score > best_scores[idx] + 1e-9:
                best_scores[idx] = score
                best_roster_indexes[idx] = [roster_idx]
            elif abs(score - best_scores[idx]) <= 1e-9:
                best_roster_indexes[idx].append(roster_idx)

        serialized = serialize_roster(players_meta, pids, roster["roles"], roster["total_ev"], roster["cost"])
        ceiling_scores = outcome_player_scores[ceiling_idx] if ceiling_idx >= 0 else {}
        if mode == "single_outcome" and ceiling_idx >= 0:
            for player in serialized.get("players") or []:
                player["mode_score"] = float(ceiling_scores.get(int(player.get("player_id") or 0), 0.0))
        else:
            for player in serialized.get("players") or []:
                player["mode_score"] = float(player.get("total_ev") or 0.0)
        for player in serialized.get("players") or []:
            player["ceiling_score"] = float(ceiling_scores.get(int(player.get("player_id") or 0), 0.0))
        serialized["average_ev"] = float(expected_score)
        serialized["ceiling_points"] = float(max_score)
        serialized["ceiling_probability"] = float(ceiling_probability)
        serialized["outcome_wins"] = 0
        serialized["outcome_win_probability"] = 0.0
        serialized["mode"] = mode
        valid_teams.append(serialized)

    for outcome_idx, winners in enumerate(best_roster_indexes):
        if not winners:
            continue
        share = 1.0 / float(len(winners))
        probability_share = float(outcomes[outcome_idx].get("probability") or 0.0) * share
        for roster_idx in winners:
            valid_teams[roster_idx]["outcome_wins"] += share
            valid_teams[roster_idx]["outcome_win_probability"] += probability_share
            valid_teams[roster_idx].setdefault("winning_outcome_indexes", []).append(outcome_idx)

    if mode == "single_outcome":
        valid_teams.sort(key=lambda team: (float(team.get("ceiling_points", 0.0)), float(team.get("average_ev", 0.0))), reverse=True)
    elif mode == "most_outcomes":
        valid_teams.sort(
            key=lambda team: (
                float(team.get("outcome_win_probability", 0.0)),
                float(team.get("outcome_wins", 0.0)),
                float(team.get("average_ev", 0.0)),
            ),
            reverse=True,
        )
    else:
        valid_teams.sort(key=lambda team: float(team.get("total_ev", 0.0)), reverse=True)

    total_combinations = math.comb(len(players_info), 5)
    return {
        "top_teams": valid_teams[:10],
        "all_teams": valid_teams,
        "player_count": len(players_info),
        "processed_combinations": int(total_combinations),
        "total_combinations": int(total_combinations),
        "mode": mode,
    }


def _run_playoff_best_team_job(job_id: str, payload: dict | None = None) -> None:
    def _update_progress(processed: int, total: int) -> None:
        with PLAYOFF_BEST_TEAM_JOBS_LOCK:
            job = PLAYOFF_BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["processed_combinations"] = int(processed)
            job["total_combinations"] = int(total)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["updated_at"] = time.time()

    with PLAYOFF_BEST_TEAM_JOBS_LOCK:
        job = PLAYOFF_BEST_TEAM_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = time.time()

    try:
        body = payload or {}
        latest = load_latest_playoff(body.get("variant"))
        if not latest:
            raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")

        options = parse_optimizer_payload(body)
        budget = options["budget"]
        max_per_team = options["max_per_team"]
        include = options["include"]
        exclude = options["exclude"]
        mode = str(body.get("mode") or "average").strip().lower()
        if mode not in {"average", "single_outcome", "most_outcomes"}:
            mode = "average"

        latest_results = latest.get("results", {}) or {}
        sim_results = latest_results.get("teams", {}) or {}
        players_info = _build_players_info_from_sim_results(sim_results, exclude)
        result = _optimize_playoff_teams_by_outcomes(
            players_info,
            list(latest_results.get("outcomes") or []),
            include,
            budget,
            max_per_team,
            mode,
            progress_callback=_update_progress,
        )
        with PLAYOFF_BEST_TEAM_JOBS_LOCK:
            job = PLAYOFF_BEST_TEAM_JOBS.get(job_id)
            if job:
                job["phase"] = "saving"
                job["updated_at"] = time.time()
        save_latest_playoff_best_team(body, result, body.get("variant"))

        with PLAYOFF_BEST_TEAM_JOBS_LOCK:
            job = PLAYOFF_BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["result"] = result
            total = int(job.get("total_combinations", 0))
            done = int(job.get("processed_combinations", 0))
            if done <= 0 and total <= 0:
                done = int(result.get("processed_combinations", 0))
                total = int(result.get("total_combinations", 0))
            job["processed_combinations"] = done
            job["total_combinations"] = total
            job["progress"] = 0.0 if total <= 0 else min(1.0, float(done) / float(total))
            job["updated_at"] = time.time()
    except Exception as exc:
        with PLAYOFF_BEST_TEAM_JOBS_LOCK:
            job = PLAYOFF_BEST_TEAM_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = time.time()


@router.post("/best-team")
def best_team_playoff(payload: dict):
    """
    Optimise a fantasy roster of 5 for the playoff bracket (Monte Carlo over the bracket).
    """
    slots: List[int] = payload.get("team_slots") or []
    if len(slots) not in _ALLOWED_BRACKET_SIZES:
        raise HTTPException(status_code=400, detail="team_slots must contain 2, 4, 8, or 16 team IDs")

    options = parse_optimizer_payload(payload)
    budget = options["budget"]
    max_per_team = options["max_per_team"]
    include = options["include"]
    exclude = options["exclude"]
    mode = str(payload.get("mode") or "average").strip().lower()
    if mode not in {"average", "single_outcome", "most_outcomes"}:
        mode = "average"

    if mode == "average":
        sim_results = simulate_playoff_fantasy(slots, return_runs=False)
        players_info = _build_players_info_from_sim_results(sim_results, exclude)
        return _optimize_playoff_teams_by_outcomes(players_info, [], include, budget, max_per_team, mode)
    exact_players, _best_bracket, _outcomes_count, outcomes = _bracket_player_totals(
        slots,
        {tid: 999 for tid in slots},
        has_third_place_decider=bool(payload.get("has_third_place_decider", False)),
    )
    players_info = _build_players_info_from_sim_results(exact_players, exclude)
    return _optimize_playoff_teams_by_outcomes(players_info, outcomes, include, budget, max_per_team, mode)


@router.post("/best-team/from-latest")
def best_team_playoff_from_latest(payload: dict | None = None):
    body = payload or {}
    latest = load_latest_playoff(body.get("variant"))
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")
    options = parse_optimizer_payload(body)
    budget = options["budget"]
    max_per_team = options["max_per_team"]
    include = options["include"]
    exclude = options["exclude"]
    mode = str(body.get("mode") or "average").strip().lower()
    if mode not in {"average", "single_outcome", "most_outcomes"}:
        mode = "average"

    latest_results = latest.get("results", {}) or {}
    sim_results = latest_results.get("teams", {}) or {}
    players_info = _build_players_info_from_sim_results(sim_results, exclude)
    return _optimize_playoff_teams_by_outcomes(
        players_info,
        list(latest_results.get("outcomes") or []),
        include,
        budget,
        max_per_team,
        mode,
    )


def _completed_bracket_round_winners(payload: dict, team_slots: List[int]) -> tuple[List[List[int]], int]:
    """Normalise the completed-bracket picks into per-round winner lists in
    bracket order (round 0 first, final last). Accepts either a general
    ``round_winners`` field (list-of-lists, used by every field size) or the
    legacy 8-team ``qf_winners``/``sf_winners``/``final_winner`` fields.
    Returns ``(round_winners, third_place_winner)``."""
    n = len(team_slots)
    total_rounds = int(round(math.log2(n)))
    third_place_winner = int(payload.get("third_place_winner") or 0)

    raw_rounds = payload.get("round_winners")
    if raw_rounds:
        rounds = [[int(x) for x in (r or [])] for r in raw_rounds]
    else:
        rounds = []
        for key in ("r16_winners", "qf_winners", "sf_winners"):
            vals = payload.get(key)
            if vals:
                rounds.append([int(x) for x in vals])
        final_winner = int(payload.get("final_winner") or 0)
        if final_winner > 0:
            rounds.append([final_winner])

    if len(rounds) != total_rounds:
        raise HTTPException(
            status_code=400,
            detail=f"Completed bracket needs winners for all {total_rounds} rounds; got {len(rounds)}.",
        )
    expected = n // 2
    for idx, round_win in enumerate(rounds):
        if len(round_win) != expected:
            raise HTTPException(
                status_code=400,
                detail=f"Round {idx + 1} needs {expected} winner(s); got {len(round_win)}.",
            )
        expected //= 2
    return rounds, third_place_winner


def _deterministic_completed_outcome(
    team_slots: List[int],
    vrs_ranks: Dict[int, int],
    round_winners: List[List[int]],
    has_third_place_decider: bool,
    third_place_winner: int,
) -> Dict:
    """Play the single bracket implied by the user's picks and return one outcome
    dict in the same shape as a stored exact/Monte-Carlo outcome. Because it
    computes the picked bracket directly (rather than looking it up in the
    sampled outcome table), the Completed Bracket picker works for any field
    size — essential for Monte-Carlo 16-team runs, where the exact bracket is
    almost never among the samples. For 8-team fields this reuses the same
    ``_play_match_deterministic`` path as the exact enumerator, so the scores are
    byte-identical to the old lookup."""
    states = initialize_teams(team_slots, vrs_ranks)
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(team_slots)
    prob_cache: Dict[tuple[int, int], float] = {}
    n = len(team_slots)
    total_rounds = int(round(math.log2(n)))

    match_results: Dict[str, List[dict]] = {}
    path_prob = 1.0
    current = list(team_slots)
    semi_losers: List[int] = []
    for round_idx in range(total_rounds):
        teams_in_round = len(current)
        rname = _round_name_for(teams_in_round)
        remaining_after = total_rounds - round_idx - 1
        winners_this_round = round_winners[round_idx]
        winners, matches = [], []
        for i in range(0, len(current), 2):
            a, b = current[i], current[i + 1]
            picked = int(winners_this_round[i // 2])
            if picked not in (a, b):
                raise HTTPException(
                    status_code=400,
                    detail=f"{rname}: winner {picked} is not in match ({a} vs {b}).",
                )
            w, l, p_win_a, branch_p = _play_match_deterministic(
                states, a, b, picked, remaining_rounds_after=remaining_after,
                prob_cache=prob_cache, player_rows_by_id=player_rows_by_id,
                team_rank_by_id=team_rank_by_id,
            )
            path_prob *= branch_p
            winners.append(w)
            matches.append({"winner": w, "loser": l, "p_win_a": p_win_a, "teams": [a, b]})
            if teams_in_round == 4:
                semi_losers.append(l)
        match_results[rname] = matches
        current = winners

    if has_third_place_decider and len(semi_losers) == 2:
        if third_place_winner not in semi_losers:
            raise HTTPException(
                status_code=400,
                detail="Third-place winner must be one of the two semi-final losers.",
            )
        w, l, p_win_a, branch_p = _play_match_deterministic(
            states, semi_losers[0], semi_losers[1], third_place_winner,
            remaining_rounds_after=0, prob_cache=prob_cache,
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id,
        )
        path_prob *= branch_p
        match_results["third_place"] = [
            {"winner": w, "loser": l, "p_win_a": p_win_a, "teams": list(semi_losers)}
        ]

    player_points: Dict[str, float] = {}
    player_components: Dict[str, Dict[str, float]] = {}
    player_breakdown: Dict[str, List[dict]] = {}
    for ts in states.values():
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
            player_breakdown[str(pid)] = [dict(row) for row in p.point_breakdown]

    return {
        "probability": float(path_prob),
        "bracket": match_results,
        "players": player_points,
        "player_components": player_components,
        "player_breakdown": player_breakdown,
    }


def _outcome_matches_completed_bracket(outcome: Dict, picks: Dict[str, object]) -> bool:
    bracket = outcome.get("bracket") or {}
    qf_winners = [int(x) for x in (picks.get("qf_winners") or [])]
    sf_winners = [int(x) for x in (picks.get("sf_winners") or [])]
    final_winner = int(picks.get("final_winner") or 0)
    third_place_winner = int(picks.get("third_place_winner") or 0)
    quarters = bracket.get("quarters") or []
    semis = bracket.get("semis") or []
    finals = bracket.get("final") or []
    third_place = bracket.get("third_place") or []
    if len(qf_winners) != 4 or len(sf_winners) != 2 or final_winner <= 0:
        return False
    if len(quarters) < 4 or len(semis) < 2 or not finals:
        return False
    if [int(row.get("winner") or 0) for row in quarters[:4]] != qf_winners:
        return False
    if [int(row.get("winner") or 0) for row in semis[:2]] != sf_winners:
        return False
    if int(finals[0].get("winner") or 0) != final_winner:
        return False
    if third_place:
        return third_place_winner > 0 and int(third_place[0].get("winner") or 0) == third_place_winner
    return third_place_winner <= 0


def _selected_completed_outcome_from_latest(payload: dict | None = None) -> tuple[dict, dict]:
    latest = load_latest_playoff((payload or {}).get("variant"))
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")
    body = payload or {}

    # Bounty brackets use a bespoke QF pairing + SF re-draft, so the picked
    # bracket is looked up among the stored exact outcomes (which were generated
    # with that pairing). Every other variant computes the picked bracket
    # deterministically — that works for any field size and, crucially, for the
    # Monte-Carlo 16-team runs whose sampled table almost never contains the
    # exact bracket the user selected.
    if _variant(body.get("variant")) == "bounty":
        latest_results = latest.get("results", {}) or {}
        outcomes = list(latest_results.get("outcomes") or [])
        if not outcomes:
            raise HTTPException(status_code=400, detail="Stored playoff run has no exact outcome table.")
        for outcome in outcomes:
            if _outcome_matches_completed_bracket(outcome, body):
                return latest, outcome
        raise HTTPException(status_code=404, detail="No stored outcome matches that completed bracket.")

    latest_payload = latest.get("payload", {}) or {}
    team_slots = [int(x) for x in (latest_payload.get("team_slots") or [])]
    if not team_slots:
        raise HTTPException(status_code=400, detail="Stored playoff run is missing its team slots.")
    has_third_place = bool(latest_payload.get("has_third_place_decider", False))
    round_winners, third_place_winner = _completed_bracket_round_winners(body, team_slots)
    vrs_ranks = {tid: 999 for tid in team_slots}
    outcome = _deterministic_completed_outcome(
        team_slots, vrs_ranks, round_winners, has_third_place, third_place_winner
    )
    return latest, outcome


def _player_team_map_from_latest(latest_results: Dict, latest_payload: Dict) -> Dict[int, int]:
    pid_to_team_id: Dict[int, int] = {}
    for tid_raw, team_data in (latest_results.get("teams") or {}).items():
        try:
            tid = int(tid_raw)
        except Exception:
            continue
        for pid_raw in ((team_data or {}).get("players") or {}).keys():
            try:
                pid_to_team_id[int(pid_raw)] = tid
            except Exception:
                continue

    for tid_raw in latest_payload.get("team_slots") or []:
        try:
            tid = int(tid_raw)
        except Exception:
            continue
        team = get_team_by_id(tid) or {}
        for key in ("player1_id", "player2_id", "player3_id", "player4_id", "player5_id"):
            try:
                pid = int(team.get(key) or 0)
            except Exception:
                continue
            if pid > 0:
                pid_to_team_id.setdefault(pid, tid)
    return pid_to_team_id


def _compute_completed_bracket_from_latest(payload: dict | None = None, progress_callback=None) -> dict:
    body = payload or {}
    latest, selected = _selected_completed_outcome_from_latest(body)
    latest_results = latest.get("results", {}) or {}
    latest_payload = latest.get("payload", {}) or {}
    outcomes = list(latest_results.get("outcomes") or [])

    options = parse_optimizer_payload(body)
    components_by_pid = {
        int(pid): dict(comps or {})
        for pid, comps in ((selected.get("player_components") or {}).items())
    }
    breakdown_by_pid = {
        int(pid): list(rows or [])
        for pid, rows in ((selected.get("player_breakdown") or {}).items())
    }
    scores_by_pid = {
        int(pid): float(
            (components_by_pid.get(int(pid)) or {}).get(
                "total_without_booster",
                float((components_by_pid.get(int(pid)) or {}).get("rating", 0.0) or 0.0)
                + float((components_by_pid.get(int(pid)) or {}).get("win", 0.0) or 0.0)
                + float((components_by_pid.get(int(pid)) or {}).get("role", 0.0) or 0.0),
            )
        )
        for pid in (selected.get("players") or {}).keys()
    }
    pid_to_team_id = _player_team_map_from_latest(latest_results, latest_payload)
    players_info = []
    player_values = []
    for pid, score in scores_by_pid.items():
        if pid in options["exclude"]:
            continue
        row = get_player(pid)
        if not row:
            continue
        comps = components_by_pid.get(pid) or {}
        components_available = bool(comps)
        rating_score = float(comps.get("rating", score if not comps else 0.0) or 0.0)
        win_score = float(comps.get("win", 0.0) or 0.0)
        role_score = float(comps.get("role", 0.0) or 0.0)
        booster_score = float(comps.get("booster", 0.0) or 0.0)
        price = int(row.get("price") or 0)
        team_id = int(pid_to_team_id.get(pid) or 0)
        players_info.append(
            {
                "player_id": pid,
                "name": row.get("name", f"Player {pid}"),
                "team_id": team_id,
                "price": price,
                "rating_ev": rating_score,
                "win_ev": win_score,
                "role_ev": role_score,
                "booster_ev": booster_score,
                "raw_booster_ev": booster_score,
                "total_ev": float(score),
                "components_available": components_available,
                "point_breakdown": breakdown_by_pid.get(pid, []),
            }
        )
        player_values.append(
            {
                "player_id": pid,
                "name": row.get("name", f"Player {pid}"),
                "team_id": team_id,
                "price": price,
                "points": float(score),
                "rating": rating_score,
                "win": win_score,
                "role": role_score,
                "booster": booster_score,
                "raw_booster": booster_score,
                "components_available": components_available,
                "point_breakdown": breakdown_by_pid.get(pid, []),
            }
        )

    if not players_info:
        raise HTTPException(status_code=400, detail="Selected bracket outcome has no available players.")
    if not any(int(player.get("team_id") or 0) > 0 for player in players_info):
        raise HTTPException(status_code=400, detail="Could not map selected bracket players back to their teams.")

    result = optimize_rosters(
        players_info,
        options["include"],
        options["budget"],
        options["max_per_team"],
        progress_callback=progress_callback,
        include_error_suffix="in selected bracket outcome",
    )
    for team in result.get("all_teams") or result.get("top_teams") or []:
        for player in team.get("players") or []:
            player["mode_score"] = float(player.get("total_ev") or 0.0)
    # The completed-bracket UI only consumes top_teams/player_values; keeping
    # every roster would persist and return a second multi-hundred-MB blob.
    result.pop("all_teams", None)
    result["bracket_probability"] = float(selected.get("probability") or 0.0)
    result["bracket"] = selected.get("bracket") or {}
    result["outcomes_count"] = int(latest_results.get("outcomes_count") or len(outcomes))
    result["player_values"] = sorted(player_values, key=lambda row: float(row.get("points") or 0.0), reverse=True)
    result["mode"] = "completed_bracket"
    return result


def _run_completed_bracket_job(job_id: str, payload: dict | None = None) -> None:
    def _update_progress(processed: int, total: int) -> None:
        with PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK:
            job = PLAYOFF_COMPLETED_BRACKET_JOBS.get(job_id)
            if not job:
                return
            job["processed_combinations"] = int(processed)
            job["total_combinations"] = int(total)
            job["progress"] = 0.0 if total <= 0 else float(processed) / float(total)
            job["updated_at"] = time.time()

    with PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK:
        job = PLAYOFF_COMPLETED_BRACKET_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = time.time()

    try:
        result = _compute_completed_bracket_from_latest(payload or {}, progress_callback=_update_progress)
        save_latest_completed_bracket(payload or {}, result, (payload or {}).get("variant"))
        with PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK:
            job = PLAYOFF_COMPLETED_BRACKET_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            job["phase"] = "completed"
            job["result"] = result
            total = int(job.get("total_combinations", 0))
            done = int(job.get("processed_combinations", 0))
            if done <= 0 and total <= 0:
                done = int(result.get("processed_combinations", 0))
                total = int(result.get("total_combinations", 0))
            job["processed_combinations"] = done
            job["total_combinations"] = total
            job["progress"] = 0.0 if total <= 0 else min(1.0, float(done) / float(total))
            job["updated_at"] = time.time()
    except Exception as exc:
        with PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK:
            job = PLAYOFF_COMPLETED_BRACKET_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = time.time()


@router.post("/best-team/bracket-from-latest")
def best_team_for_completed_bracket_from_latest(payload: dict | None = None):
    result = _compute_completed_bracket_from_latest(payload or {})
    save_latest_completed_bracket(payload or {}, result, (payload or {}).get("variant"))
    return result


@router.post("/best-team/bracket-from-latest/start")
def start_completed_bracket_from_latest(payload: dict | None = None):
    body = payload or {}
    latest = load_latest_playoff(body.get("variant"))
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")

    with PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK:
        for existing_id, existing_job in PLAYOFF_COMPLETED_BRACKET_JOBS.items():
            if _variant((existing_job.get("payload") or {}).get("variant")) != _variant(body.get("variant")):
                continue
            if existing_job.get("status") in {"queued", "running"}:
                return {"job_id": existing_id, "reused": True}

        job_id = uuid.uuid4().hex
        PLAYOFF_COMPLETED_BRACKET_JOBS[job_id] = {
            "status": "queued",
            "error": "",
            "progress": 0.0,
            "processed_combinations": 0,
            "total_combinations": 0,
            "result": None,
            "payload": body,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    worker = threading.Thread(target=_run_completed_bracket_job, args=(job_id, body), daemon=True)
    worker.start()
    return {"job_id": job_id}


@router.get("/best-team/bracket-from-latest/job/{job_id}")
def get_completed_bracket_job(job_id: str):
    with PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK:
        job = PLAYOFF_COMPLETED_BRACKET_JOBS.get(job_id)
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
        "result_ready": out.get("result") is not None,
        "result": out.get("result"),
    }


@router.get("/best-team/bracket-from-latest/latest")
def get_latest_completed_bracket(variant: str = "main"):
    latest = load_latest_completed_bracket(variant)
    if not latest:
        return {"exists": False}
    # Rows saved before all_teams was dropped carry every roster; serializing
    # that here breaks the response, and no caller uses it.
    result = dict(latest["result"] or {})
    result.pop("all_teams", None)
    return {
        "exists": True,
        "payload": latest["payload"],
        "result": result,
        "updated_at": latest["updated_at"],
    }


@router.post("/best-team/from-latest/start")
def start_best_team_playoff_from_latest(payload: dict | None = None):
    body = payload or {}
    latest = load_latest_playoff(body.get("variant"))
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")

    with PLAYOFF_BEST_TEAM_JOBS_LOCK:
        for existing_id, existing_job in PLAYOFF_BEST_TEAM_JOBS.items():
            if _variant((existing_job.get("payload") or {}).get("variant")) != _variant(body.get("variant")):
                continue
            if existing_job.get("status") in {"queued", "running"}:
                return {"job_id": existing_id, "reused": True}

        job_id = uuid.uuid4().hex
        PLAYOFF_BEST_TEAM_JOBS[job_id] = {
            "status": "queued",
            "error": "",
            "progress": 0.0,
            "processed_combinations": 0,
            "total_combinations": 0,
            "result": None,
            "payload": body,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    worker = threading.Thread(target=_run_playoff_best_team_job, args=(job_id, body), daemon=True)
    worker.start()
    return {"job_id": job_id}


@router.get("/best-team/job/{job_id}")
def get_best_team_playoff_job(job_id: str):
    with PLAYOFF_BEST_TEAM_JOBS_LOCK:
        job = PLAYOFF_BEST_TEAM_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_id not found")
        out = dict(job)
    return {
        "job_id": job_id,
        "status": out.get("status", "queued"),
        "error": out.get("error", ""),
        "phase": out.get("phase", out.get("status", "queued")),
        "progress": out.get("progress", 0.0),
        "processed_combinations": out.get("processed_combinations", 0),
        "total_combinations": out.get("total_combinations", 0),
        "result_ready": out.get("result") is not None,
    }


@router.get("/best-team/from-latest/latest")
def get_latest_best_team_playoff_from_latest(variant: str = "main"):
    states = _states(variant)
    meta = states["meta"].load()
    if meta:
        summary = meta["result"] or {}
        return {
            "exists": True,
            "payload": meta["payload"],
            "mode": summary.get("mode"),
            "player_count": summary.get("player_count"),
            "total_teams": summary.get("total_teams"),
            "processed_combinations": summary.get("processed_combinations"),
            "total_combinations": summary.get("total_combinations"),
            "approximate": bool(summary.get("approximate")),
            "candidate_count": summary.get("candidate_count"),
            "updated_at": meta["updated_at"],
        }

    # Legacy row saved before the meta table existed. Summarize it with
    # SQLite's JSON functions (C-side parse, no giant Python objects) and
    # persist the summary so this path only ever runs once.
    best_table = states["best"].table
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT payload_json, updated_at FROM {best_table} WHERE singleton_id = 1"
        ).fetchone()
        if not row:
            return {"exists": False}
        payload = json.loads(row["payload_json"])
        updated_at = float(row["updated_at"])
        summary = {
            "mode": None,
            "player_count": None,
            "total_teams": None,
            "processed_combinations": None,
            "total_combinations": None,
        }
        try:
            extracted = conn.execute(
                f"""
                SELECT json_extract(result_json, '$.mode') AS mode,
                       json_extract(result_json, '$.player_count') AS player_count,
                       json_array_length(result_json, '$.all_teams') AS total_teams,
                       json_extract(result_json, '$.processed_combinations') AS processed_combinations,
                       json_extract(result_json, '$.total_combinations') AS total_combinations
                FROM {best_table} WHERE singleton_id = 1
                """
            ).fetchone()
            if extracted:
                summary = {key: extracted[key] for key in summary}
        except Exception:
            pass  # blob too large to summarize here; exists/payload still useful
    finally:
        conn.close()
    states["meta"].save(payload, summary)
    return {
        "exists": True,
        "payload": payload,
        "mode": summary.get("mode"),
        "player_count": summary.get("player_count"),
        "total_teams": summary.get("total_teams"),
        "processed_combinations": summary.get("processed_combinations"),
        "total_combinations": summary.get("total_combinations"),
        "updated_at": updated_at,
    }


@router.post("/best-team/from-latest/query")
def query_latest_best_team_playoff(payload: dict | None = None):
    body = payload or {}
    latest = load_latest_playoff_best_team(body.get("variant"))
    if not latest:
        raise HTTPException(status_code=404, detail="No stored team combinations found. Run Combinations first.")
    options = parse_optimizer_payload(body)
    mode = str(body.get("mode") or "average").strip().lower()
    if mode not in {"average", "single_outcome", "most_outcomes"}:
        mode = "average"
    result = latest["result"] or {}
    teams = list(result.get("all_teams") or [])
    filtered = _filter_saved_combo_teams(
        teams,
        options["include"],
        options["exclude"],
        str(body.get("search") or ""),
    )
    sorted_teams = _sort_saved_combo_teams(filtered, mode, str(body.get("sort") or "ev_desc"))
    page = int(body.get("page") or 0)
    page_size = int(body.get("page_size") or 200)
    return {
        "exists": True,
        "mode": mode,
        "updated_at": latest["updated_at"],
        "total_teams": len(teams),
        "filtered_count": len(sorted_teams),
        "top_teams": sorted_teams[:10],
        "page_teams": _page_items(sorted_teams, page, page_size),
        "page": max(0, page),
        "page_size": max(1, min(500, page_size)),
        "approximate": bool(result.get("approximate")),
        "total_combinations": result.get("total_combinations"),
    }


@router.post("/best-team/from-latest/completed-query")
def query_latest_best_team_for_completed_bracket(payload: dict | None = None):
    body = payload or {}
    latest_combos = load_latest_playoff_best_team(body.get("variant"))
    if not latest_combos:
        raise HTTPException(status_code=404, detail="No stored team combinations found. Run Combinations first.")
    latest_playoff, selected = _selected_completed_outcome_from_latest(body)
    latest_results = latest_playoff.get("results", {}) or {}
    options = parse_optimizer_payload(body)
    components_by_pid = {
        int(pid): dict(comps or {})
        for pid, comps in ((selected.get("player_components") or {}).items())
    }
    breakdown_by_pid = {
        int(pid): list(rows or [])
        for pid, rows in ((selected.get("player_breakdown") or {}).items())
    }
    scores_by_pid = {
        int(pid): (
            float((components_by_pid.get(int(pid)) or {}).get("total_without_booster", 0.0) or 0.0)
            if (components_by_pid.get(int(pid)) or {}).get("total_without_booster") is not None
            else (
                float((components_by_pid.get(int(pid)) or {}).get("rating", 0.0) or 0.0)
                + float((components_by_pid.get(int(pid)) or {}).get("win", 0.0) or 0.0)
                + float((components_by_pid.get(int(pid)) or {}).get("role", 0.0) or 0.0)
            )
        )
        for pid in (selected.get("players") or {}).keys()
    }
    pid_to_team_id = _player_team_map_from_latest(latest_results, latest_playoff.get("payload", {}) or {})

    player_values = []
    for pid, score in scores_by_pid.items():
        row = get_player(pid) or {}
        comps = components_by_pid.get(pid) or {}
        player_values.append(
            {
                "player_id": pid,
                "name": row.get("name", f"Player {pid}"),
                "team_id": int(pid_to_team_id.get(pid) or 0),
                "price": int(row.get("price") or 0),
                "points": float(score),
                "rating": float(comps.get("rating", 0.0) or 0.0),
                "win": float(comps.get("win", 0.0) or 0.0),
                "role": float(comps.get("role", 0.0) or 0.0),
                "booster": float(comps.get("booster", 0.0) or 0.0),
                "raw_booster": float(comps.get("booster", 0.0) or 0.0),
                "components_available": bool(comps),
                "point_breakdown": breakdown_by_pid.get(pid, []),
            }
        )

    teams = list((latest_combos.get("result") or {}).get("all_teams") or [])
    filtered = _filter_saved_combo_teams(teams, options["include"], options["exclude"], str(body.get("search") or ""))
    scored = []
    for team in filtered:
        players = []
        total = 0.0
        for player in team.get("players") or []:
            pid = int(player.get("player_id") or 0)
            score = float(scores_by_pid.get(pid, 0.0))
            comps = components_by_pid.get(pid) or {}
            player_out = {**player, "mode_score": score, "total_ev": score}
            if comps:
                player_out.update(
                    {
                        "rating_ev": float(comps.get("rating", 0.0) or 0.0),
                        "win_ev": float(comps.get("win", 0.0) or 0.0),
                        "role_ev": float(comps.get("role", 0.0) or 0.0),
                        "booster_ev": float(comps.get("booster", 0.0) or 0.0),
                        "raw_booster_ev": float(comps.get("booster", 0.0) or 0.0),
                        "rating": float(comps.get("rating", 0.0) or 0.0),
                        "win": float(comps.get("win", 0.0) or 0.0),
                        "role": float(comps.get("role", 0.0) or 0.0),
                        "booster": float(comps.get("booster", 0.0) or 0.0),
                        "raw_booster": float(comps.get("booster", 0.0) or 0.0),
                        "components_available": True,
                        "point_breakdown": breakdown_by_pid.get(pid, []),
                    }
                )
            else:
                player_out["components_available"] = False
            total += score
            players.append(player_out)
        scored.append({**team, "players": players, "total_ev": total, "bracket_score": total})
    scored.sort(key=lambda team: float(team.get("bracket_score") or 0.0), reverse=True)
    page = int(body.get("page") or 0)
    page_size = int(body.get("page_size") or 200)
    return {
        "exists": True,
        "mode": "completed_bracket",
        "updated_at": latest_combos["updated_at"],
        "bracket_probability": float(selected.get("probability") or 0.0),
        "bracket": selected.get("bracket") or {},
        "outcomes_count": int(latest_results.get("outcomes_count") or len(latest_results.get("outcomes") or [])),
        "player_values": sorted(player_values, key=lambda row: float(row.get("points") or 0.0), reverse=True),
        "total_teams": len(teams),
        "filtered_count": len(scored),
        "top_teams": scored[:10],
        "page_teams": _page_items(scored, page, page_size),
        "page": max(0, page),
        "page_size": max(1, min(500, page_size)),
    }


@router.get("/latest")
def get_latest_playoff(variant: str = "main"):
    latest = load_latest_playoff(variant)
    if not latest:
        return {"exists": False}
    return {
        "exists": True,
        "payload": latest["payload"],
        "results": latest["results"],
        "updated_at": latest["updated_at"],
    }


@router.delete("/latest")
def reset_latest_playoff(variant: str = "main"):
    states = _states(variant)
    conn = _connect()
    try:
        for state in states.values():
            conn.execute(f"DELETE FROM {state.table} WHERE singleton_id = 1")
        conn.commit()
    finally:
        conn.close()
    for state in states.values():
        state.invalidate()
    return {"status": "ok"}
