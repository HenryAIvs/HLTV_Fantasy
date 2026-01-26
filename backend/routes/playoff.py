import itertools
import random
from typing import List, Dict

from fastapi import APIRouter, HTTPException

from player_db import get_player
from role_assignment import best_role_assignment_for_team, extract_role_scores_for_player
from swiss_stage.team_initialization import initialize_teams
from swiss_stage.swiss_models import TeamState
from match_engine import simulate_match_outcome, apply_fantasy_points_for_team

router = APIRouter()


def _apply_elimination_penalty(team: TeamState, remaining_rounds: int) -> None:
    """
    Apply -3 points per remaining round to all players (win component/total).
    """
    if remaining_rounds <= 0:
        return
    penalty = -3.0 * remaining_rounds
    for p in team.players.values():
        p.win_points_total += penalty
        p.total_points += penalty


def _serialize_team(ts: TeamState) -> dict:
    return {
        "team_id": ts.team_id,
        "wins": ts.wins,
        "losses": ts.losses,
        "players": {
            pid: {
                "total_points": p.total_points,
                "rating_points_total": p.rating_points_total,
                "win_points_total": p.win_points_total,
                "role_points_total": p.role_points_total,
                "booster_points_total": p.booster_points_total,
            }
            for pid, p in ts.players.items()
        },
    }


def _simulate_bracket(team_slots: List[int], vrs_ranks: Dict[int, int], rng=None):
    team_states: Dict[int, TeamState] = initialize_teams(team_slots, vrs_ranks)
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

        apply_fantasy_points_for_team(A, B.team_id, result.win_probability, did_win_a, match_num_a, "bo3")
        apply_fantasy_points_for_team(B, A.team_id, 1.0 - result.win_probability, did_win_b, match_num_b, "bo3")

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


def _average_player_totals(team_slots: List[int], vrs_ranks: Dict[int, int], n_sims: int) -> Dict[int, Dict]:
    """
    Monte Carlo over the playoff bracket, returning expected fantasy components per player.
    """
    player_sums: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    for _ in range(n_sims):
        team_states, _ = _simulate_bracket(team_slots, vrs_ranks)
        for tid, ts in team_states.items():
            for pid, p in ts.players.items():
                bucket = player_sums[tid].setdefault(
                    pid,
                    {"total": 0.0, "rating": 0.0, "win": 0.0, "role": 0.0, "booster": 0.0},
                )
                bucket["total"] += p.total_points
                bucket["rating"] += p.rating_points_total
                bucket["win"] += p.win_points_total
                bucket["role"] += p.role_points_total
                bucket["booster"] += p.booster_points_total

    results: Dict[int, Dict] = {}
    for tid in team_slots:
        players_out: Dict[int, Dict[str, float]] = {}
        for pid, sums in player_sums[tid].items():
            players_out[pid] = {
                "total_points": sums["total"] / float(n_sims),
                "rating_points_total": sums["rating"] / float(n_sims),
                "win_points_total": sums["win"] / float(n_sims),
                "role_points_total": sums["role"] / float(n_sims),
                "booster_points_total": sums["booster"] / float(n_sims),
            }
        results[tid] = {
            "team_id": tid,
            "wins": 0,
            "losses": 0,
            "players": players_out,
        }
    return results


@router.post("/run")
def run_playoff(payload: dict):
    """
    Simulate a single-elimination playoff bracket (BO3 all matches).

    Expects:
      - team_slots: list of 8 team_ids in bracket order:
          [QF1_A, QF1_B, QF2_A, QF2_B, QF3_A, QF3_B, QF4_A, QF4_B]
    """
    slots: List[int] = payload.get("team_slots") or []
    if len(slots) != 8:
        raise HTTPException(status_code=400, detail="team_slots must contain 8 team IDs")

    n_sims = int(payload.get("n_sims", 1))
    if n_sims < 1:
        raise HTTPException(status_code=400, detail="n_sims must be >= 1")

    # Initialize teams; vrs_ranks not relevant here (use default 999)
    vrs_ranks = {tid: 999 for tid in slots}

    if n_sims == 1:
        team_states, match_results = _simulate_bracket(slots, vrs_ranks)
        return {
            "bracket": match_results,
            "teams": {tid: _serialize_team(ts) for tid, ts in team_states.items()},
        }

    # Monte Carlo: run many times and average player components. Return a sample bracket for display.
    team_states, sample_bracket = _simulate_bracket(slots, vrs_ranks)
    averaged_players = _average_player_totals(slots, vrs_ranks, n_sims)
    return {
        "bracket": sample_bracket,
        "teams": averaged_players,
        "n_sims": n_sims,
    }


def simulate_playoff_fantasy(team_slots: List[int], n_sims: int, return_runs: bool = False):
    if n_sims <= 0:
        raise HTTPException(status_code=400, detail="n_sims must be positive")
    vrs_ranks = {tid: 999 for tid in team_slots}
    player_sums: Dict[int, Dict[int, Dict[str, float]]] = {tid: {} for tid in team_slots}
    run_points: List[Dict[int, float]] = []

    for _ in range(n_sims):
        team_states, _ = _simulate_bracket(team_slots, vrs_ranks)
        run_totals: Dict[int, float] = {}
        for tid, ts in team_states.items():
            for pid, p in ts.players.items():
                bucket = player_sums[tid].setdefault(
                    pid,
                    {
                        "total": 0.0,
                        "rating": 0.0,
                        "win": 0.0,
                        "role": 0.0,
                        "booster": 0.0,
                    },
                )
                bucket["total"] += p.total_points
                bucket["rating"] += p.rating_points_total
                bucket["win"] += p.win_points_total
                bucket["role"] += p.role_points_total
                bucket["booster"] += p.booster_points_total
                run_totals[pid] = p.total_points
        run_points.append(run_totals)

    results: Dict[int, Dict] = {}
    for tid in team_slots:
        players_out: Dict[int, Dict[str, float]] = {}
        for pid, sums in player_sums[tid].items():
            players_out[pid] = {
                "total": sums["total"] / float(n_sims),
                "rating": sums["rating"] / float(n_sims),
                "win": sums["win"] / float(n_sims),
                "role": sums["role"] / float(n_sims),
                "booster": sums["booster"] / float(n_sims),
            }
        results[tid] = {"players": players_out}

    if return_runs:
        return results, run_points
    return results


@router.post("/best-team")
def best_team_playoff(payload: dict):
    """
    Optimise a fantasy roster of 5 for the playoff bracket (Monte Carlo over the bracket).
    """
    slots: List[int] = payload.get("team_slots") or []
    if len(slots) != 8:
        raise HTTPException(status_code=400, detail="team_slots must contain 8 team IDs")

    n_sims = int(payload.get("n_sims", 200))
    budget = int(payload.get("budget", 1_000_000))
    max_per_team = int(payload.get("max_per_team", 2))
    include = set(payload.get("include_player_ids") or [])
    exclude = set(payload.get("exclude_player_ids") or [])

    sim_results, run_points = simulate_playoff_fantasy(slots, n_sims, return_runs=True)

    players_info = []
    for tid, team_res in sim_results.items():
        for pid_key, comps in team_res.get("players", {}).items():
            pid = int(pid_key)
            if pid in exclude:
                continue
            row = get_player(pid)
            if not row:
                continue
            name = row.get("name", f"Player {pid}")
            price = row.get("price", 0)
            roles_json = row.get("roles_json", "")
            rating_ev = float(comps.get("rating_points_total", comps.get("rating", 0.0)))
            win_ev = float(comps.get("win_points_total", comps.get("win", 0.0)))
            role_ev = float(comps.get("role_points_total", comps.get("role", 0.0)))
            booster_ev = float(comps.get("booster_points_total", comps.get("booster", 0.0)))
            total_ev = float(comps.get("total_points", comps.get("total", 0.0)))

            players_info.append(
                {
                    "player_id": pid,
                    "name": name,
                    "team_id": tid,
                    "price": price,
                    "rating_ev": rating_ev,
                    "win_ev": win_ev,
                    "role_ev": role_ev,
                    "booster_ev": booster_ev,
                    "total_ev": total_ev,
                    "roles_json": roles_json,
                }
            )

    if len(players_info) < 5:
        return {"error": "Not enough players after exclusions"}

    # Ensure included players exist within the available set
    available_ids = {p["player_id"] for p in players_info}
    missing_includes = [pid for pid in include if pid not in available_ids]
    if missing_includes:
        return {"error": f"Included players not available in bracket teams: {missing_includes}"}

    valid_teams = []
    players_info_sorted = sorted(players_info, key=lambda x: -x["total_ev"])

    for combo in itertools.combinations(players_info_sorted, 5):
        if include and not include.issubset({p["player_id"] for p in combo}):
            continue
        total_cost = sum(p["price"] for p in combo)
        if total_cost > budget:
            continue
        counts = {}
        valid = True
        for p in combo:
            counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
            if counts[p["team_id"]] > max_per_team:
                valid = False
                break
        if not valid:
            continue

        # Bracket-aware expected total: sum player points per simulation, then average
        combo_pids = [p["player_id"] for p in combo]
        agg = 0.0
        for run in run_points:
            agg += sum(run.get(pid, 0.0) for pid in combo_pids)
        total_ev = agg / float(n_sims)
        valid_teams.append(
            {
                "total_ev": total_ev,
                "cost": total_cost,
                "players": combo,
            }
        )

    valid_teams.sort(key=lambda x: x["total_ev"], reverse=True)

    def serialize_team(entry):
        return {
            "total_ev": entry["total_ev"],
            "cost": entry["cost"],
            "players": [
                {
                    "player_id": p["player_id"],
                    "name": p["name"],
                    "team_id": p["team_id"],
                    "price": p["price"],
                    "rating_ev": p["rating_ev"],
                    "win_ev": p["win_ev"],
                    "role_ev": p["role_ev"],
                    "booster_ev": p["booster_ev"],
                    "total_ev": p["total_ev"],
                    "role_name": p.get("best_role", "-"),
                }
                for p in entry["players"]
            ],
        }

    all_serialized = [serialize_team(t) for t in valid_teams]
    top_teams = all_serialized[:10]

    return {"top_teams": top_teams, "all_teams": all_serialized, "player_count": len(players_info)}
