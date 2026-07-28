import json
import math
import random
import threading
import time
import uuid
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
from backend.services.match_engine import simulate_match_outcome, apply_fantasy_points_for_team, calculate_win_probability

router = APIRouter()
PLAYOFF_JOBS = {}
PLAYOFF_JOBS_LOCK = threading.Lock()
PLAYOFF_BEST_TEAM_JOBS = {}
PLAYOFF_BEST_TEAM_JOBS_LOCK = threading.Lock()
PLAYOFF_COMPLETED_BRACKET_JOBS = {}
PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK = threading.Lock()


_PLAYOFF_STATE = SingletonState("playoff_simulation_state", result_column="results_json", result_key="results")
_COMPLETED_BRACKET_STATE = SingletonState("playoff_completed_bracket_state")
_BEST_TEAM_STATE = SingletonState("playoff_best_team_state")
# Small summary saved alongside the combos blob so metadata endpoints never
# have to materialize the (multi-hundred-MB) result_json.
_BEST_TEAM_META = SingletonState("playoff_best_team_meta")


def ensure_playoff_schema() -> None:
    for state in (_PLAYOFF_STATE, _COMPLETED_BRACKET_STATE, _BEST_TEAM_STATE, _BEST_TEAM_META):
        state.ensure_table()


def save_latest_playoff(payload: dict, results: dict) -> None:
    _PLAYOFF_STATE.save(payload, results)


def load_latest_playoff() -> dict | None:
    return _PLAYOFF_STATE.load()


def save_latest_completed_bracket(payload: dict, result: dict) -> None:
    _COMPLETED_BRACKET_STATE.save(payload, result)


def load_latest_completed_bracket() -> dict | None:
    return _COMPLETED_BRACKET_STATE.load()


def _best_team_meta_summary(result: dict) -> dict:
    return {
        "mode": result.get("mode"),
        "player_count": result.get("player_count"),
        "total_teams": len(result.get("all_teams") or []),
        "processed_combinations": result.get("processed_combinations"),
        "total_combinations": result.get("total_combinations"),
    }


def save_latest_playoff_best_team(payload: dict, result: dict) -> None:
    _BEST_TEAM_STATE.save(payload, result)
    _BEST_TEAM_META.save(payload, _best_team_meta_summary(result))


def load_latest_playoff_best_team() -> dict | None:
    return _BEST_TEAM_STATE.load()


def _saved_combo_metric(team: dict, mode: str) -> float:
    if mode == "single_outcome":
        return float(team.get("ceiling_points") or 0.0)
    if mode == "most_outcomes":
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
) -> tuple[Dict[int, Dict], Dict, int, List[Dict]]:
    base_states = initialize_teams(team_slots, vrs_ranks)
    player_rows_by_id, team_rank_by_id = _build_playoff_lookup_context(team_slots)
    accum: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    total_prob = 0.0
    processed = 0
    best_prob = -1.0
    best_bracket = {"quarters": [], "semis": [], "final": []}
    outcomes: List[Dict] = []

    quarters = [
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
            # Semifinal 1
            for semi1_winner in (qf_winners[0], qf_winners[1]):
                s1_states = _clone_team_states(states)
                s1_winner, s1_loser, s1_p_win_a, s1_branch_p = _play_match_deterministic(
                    s1_states, qf_winners[0], qf_winners[1], semi1_winner, remaining_rounds_after=1, prob_cache=prob_cache,
                    player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
                )
                semi1_match = {
                    "winner": s1_winner,
                    "loser": s1_loser,
                    "p_win_a": s1_p_win_a,
                    "teams": [qf_winners[0], qf_winners[1]],
                }

                # Semifinal 2
                for semi2_winner in (qf_winners[2], qf_winners[3]):
                    s2_states = _clone_team_states(s1_states)
                    s2_winner, s2_loser, s2_p_win_a, s2_branch_p = _play_match_deterministic(
                        s2_states, qf_winners[2], qf_winners[3], semi2_winner, remaining_rounds_after=1, prob_cache=prob_cache,
                        player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id
                    )
                    semi2_match = {
                        "winner": s2_winner,
                        "loser": s2_loser,
                        "p_win_a": s2_p_win_a,
                        "teams": [qf_winners[2], qf_winners[3]],
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
        for pid, sums in accum[tid].items():
            players_out[pid] = {
                "total_points": sums["total"] / denom,
                "rating_points_total": sums["rating"] / denom,
                "win_points_total": sums["win"] / denom,
                "role_points_total": sums["role"] / denom,
                "booster_points_total": sums["booster"] / denom,
                "total_points_without_booster": (sums["rating"] + sums["win"] + sums["role"]) / denom,
            }
        results[tid] = {
            "team_id": tid,
            "wins": 0,
            "losses": 0,
            "players": players_out,
        }

    return results, best_bracket, total_outcomes, outcomes


def _normalize_playoff_payload(payload: dict) -> dict:
    slots: List[int] = payload.get("team_slots") or []
    if len(slots) != 8:
        raise HTTPException(status_code=400, detail="team_slots must contain 8 team IDs")
    has_third_place_decider = bool(payload.get("has_third_place_decider", False))
    return {
        "team_slots": [int(x) for x in slots],
        "has_third_place_decider": has_third_place_decider,
    }


def _compute_playoff_result(payload: dict, progress_callback=None) -> dict:
    slots = payload["team_slots"]
    has_third_place_decider = bool(payload.get("has_third_place_decider", False))

    # vrs_ranks not relevant here (use default 999)
    vrs_ranks = {tid: 999 for tid in slots}

    exact_players, best_bracket, outcomes_count, outcomes = _exact_weighted_player_totals(
        slots,
        vrs_ranks,
        has_third_place_decider=has_third_place_decider,
        progress_callback=progress_callback,
    )
    return {
        "bracket": best_bracket,
        "teams": exact_players,
        "method": "exact_enumeration",
        "outcomes_count": outcomes_count,
        "outcomes": outcomes,
        "has_third_place_decider": has_third_place_decider,
    }


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
    save_latest_playoff(normalized, response)
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
        save_latest_playoff(payload, result)
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
    total_outcomes = 256 if normalized.get("has_third_place_decider") else 128
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
    exact_results, _, _, _ = _exact_weighted_player_totals(team_slots, vrs_ranks, has_third_place_decider=False)

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


def _optimize_playoff_teams_by_outcomes(
    players_info: list[dict],
    outcomes: List[Dict],
    include: set[int],
    budget: int,
    max_per_team: int,
    mode: str,
    progress_callback=None,
):
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

    if mode == "single_outcome":
        valid_teams.sort(key=lambda team: (float(team.get("ceiling_points", 0.0)), float(team.get("average_ev", 0.0))), reverse=True)
    elif mode == "most_outcomes":
        valid_teams.sort(
            key=lambda team: (
                float(team.get("outcome_wins", 0.0)),
                float(team.get("outcome_win_probability", 0.0)),
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
        latest = load_latest_playoff()
        if not latest:
            raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")

        body = payload or {}
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
        save_latest_playoff_best_team(body, result)

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
    if len(slots) != 8:
        raise HTTPException(status_code=400, detail="team_slots must contain 8 team IDs")

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
    exact_players, _best_bracket, _outcomes_count, outcomes = _exact_weighted_player_totals(
        slots,
        {tid: 999 for tid in slots},
        has_third_place_decider=bool(payload.get("has_third_place_decider", False)),
    )
    players_info = _build_players_info_from_sim_results(exact_players, exclude)
    return _optimize_playoff_teams_by_outcomes(players_info, outcomes, include, budget, max_per_team, mode)


@router.post("/best-team/from-latest")
def best_team_playoff_from_latest(payload: dict | None = None):
    latest = load_latest_playoff()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")

    body = payload or {}
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
    latest = load_latest_playoff()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")
    latest_results = latest.get("results", {}) or {}
    outcomes = list(latest_results.get("outcomes") or [])
    if not outcomes:
        raise HTTPException(status_code=400, detail="Stored playoff run has no exact outcome table.")
    body = payload or {}
    for outcome in outcomes:
        if _outcome_matches_completed_bracket(outcome, body):
            return latest, outcome
    raise HTTPException(status_code=404, detail="No stored outcome matches that completed bracket.")


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
        save_latest_completed_bracket(payload or {}, result)
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
    save_latest_completed_bracket(payload or {}, result)
    return result


@router.post("/best-team/bracket-from-latest/start")
def start_completed_bracket_from_latest(payload: dict | None = None):
    latest = load_latest_playoff()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")

    body = payload or {}
    with PLAYOFF_COMPLETED_BRACKET_JOBS_LOCK:
        for existing_id, existing_job in PLAYOFF_COMPLETED_BRACKET_JOBS.items():
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
def get_latest_completed_bracket():
    latest = load_latest_completed_bracket()
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
    latest = load_latest_playoff()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored playoff simulation found. Run Playoff Bracket first.")

    body = payload or {}
    with PLAYOFF_BEST_TEAM_JOBS_LOCK:
        for existing_id, existing_job in PLAYOFF_BEST_TEAM_JOBS.items():
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
def get_latest_best_team_playoff_from_latest():
    meta = _BEST_TEAM_META.load()
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
            "updated_at": meta["updated_at"],
        }

    # Legacy row saved before the meta table existed. Summarize it with
    # SQLite's JSON functions (C-side parse, no giant Python objects) and
    # persist the summary so this path only ever runs once.
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload_json, updated_at FROM playoff_best_team_state WHERE singleton_id = 1"
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
                """
                SELECT json_extract(result_json, '$.mode') AS mode,
                       json_extract(result_json, '$.player_count') AS player_count,
                       json_array_length(result_json, '$.all_teams') AS total_teams,
                       json_extract(result_json, '$.processed_combinations') AS processed_combinations,
                       json_extract(result_json, '$.total_combinations') AS total_combinations
                FROM playoff_best_team_state WHERE singleton_id = 1
                """
            ).fetchone()
            if extracted:
                summary = {key: extracted[key] for key in summary}
        except Exception:
            pass  # blob too large to summarize here; exists/payload still useful
    finally:
        conn.close()
    _BEST_TEAM_META.save(payload, summary)
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
    latest = load_latest_playoff_best_team()
    if not latest:
        raise HTTPException(status_code=404, detail="No stored team combinations found. Run Combinations first.")
    body = payload or {}
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
    }


@router.post("/best-team/from-latest/completed-query")
def query_latest_best_team_for_completed_bracket(payload: dict | None = None):
    latest_combos = load_latest_playoff_best_team()
    if not latest_combos:
        raise HTTPException(status_code=404, detail="No stored team combinations found. Run Combinations first.")
    body = payload or {}
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
def get_latest_playoff():
    latest = load_latest_playoff()
    if not latest:
        return {"exists": False}
    return {
        "exists": True,
        "payload": latest["payload"],
        "results": latest["results"],
        "updated_at": latest["updated_at"],
    }


@router.delete("/latest")
def reset_latest_playoff():
    conn = _connect()
    try:
        conn.execute("DELETE FROM playoff_simulation_state WHERE singleton_id = 1")
        conn.execute("DELETE FROM playoff_best_team_state WHERE singleton_id = 1")
        conn.execute("DELETE FROM playoff_best_team_meta WHERE singleton_id = 1")
        conn.execute("DELETE FROM playoff_completed_bracket_state WHERE singleton_id = 1")
        conn.commit()
    finally:
        conn.close()
    for state in (_PLAYOFF_STATE, _BEST_TEAM_STATE, _BEST_TEAM_META, _COMPLETED_BRACKET_STATE):
        state.invalidate()
    return {"status": "ok"}
