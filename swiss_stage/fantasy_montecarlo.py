# swiss_stage/fantasy_montecarlo.py

from typing import Dict, List, Tuple
import os
from concurrent.futures import ProcessPoolExecutor

from swiss_stage.swiss_bracket import simulate_single_swiss_run


def _run_chunk(args: Tuple[List[int], Dict[int, int], str, int]) -> Tuple[
    Dict[int, Dict[str, int]],
    Dict[int, Dict[int, Dict[str, float]]]
]:
    """
    Worker function for a chunk of simulations.

    Args:
        args = (team_ids, vrs_ranks, bo3_mode, n_sims_chunk)

    Returns:
        (record_counts_chunk, player_sums_chunk)
        where:
          record_counts_chunk[team_id][record] = count
          player_sums_chunk[team_id][player_id][comp] = sum over sims
    """
    team_ids, vrs_ranks, bo3_mode, n_sims_chunk = args

    # To avoid passing non-picklable callables, import initialize_teams here
    from swiss_stage.team_initialization import initialize_teams  # local import in worker

    # Per-team record counts for this chunk
    record_counts: Dict[int, Dict[str, int]] = {
        tid: {"3-0": 0, "3-1": 0, "3-2": 0, "2-3": 0, "1-3": 0, "0-3": 0}
        for tid in team_ids
    }

    # Per-team per-player component sums for this chunk
    player_sums: Dict[int, Dict[int, Dict[str, float]]] = {
        tid: {} for tid in team_ids
    }

    for _ in range(n_sims_chunk):
        final_states = simulate_single_swiss_run(
            team_ids=team_ids,
            vrs_ranks=vrs_ranks,
            bo3_mode=bo3_mode,
            initialize_teams=initialize_teams,
        )

        for tid, t in final_states.items():
            rec = f"{t.wins}-{t.losses}"
            if rec in record_counts[tid]:
                record_counts[tid][rec] += 1

            # accumulate player points
            for pid, p in t.players.items():
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

    return record_counts, player_sums


def simulate_swiss_fantasy(
    team_ids: List[int],
    vrs_ranks: Dict[int, int],
    bo3_mode: str,
    n_sims: int,
    n_workers: int = None,
) -> Dict[int, Dict]:
    """
    Parallel Swiss Monte Carlo + fantasy scoring.

    Returns:
    {
      team_id: {
        "3-0": p,
        "3-1": p,
        "3-2": p,
        "2-3": p,
        "1-3": p,
        "0-3": p,
        "players": {
          player_id: {
            "total":   expected_total_points,
            "rating":  expected_rating_points,
            "win":     expected_win_points,
            "role":    expected_role_points,
            "booster": expected_booster_points,
          }
        }
      }
    }
    """

    if n_sims <= 0:
        raise ValueError("n_sims must be positive")

    if n_workers is None:
        # use up to #cores, but not more than n_sims
        n_workers = min(os.cpu_count() or 1, n_sims)
    if n_workers < 1:
        n_workers = 1

    # Split n_sims into chunks per worker
    base = n_sims // n_workers
    rem = n_sims % n_workers
    chunks = [base + (1 if i < rem else 0) for i in range(n_workers)]
    chunks = [c for c in chunks if c > 0]

    args_list = [(team_ids, vrs_ranks, bo3_mode, c) for c in chunks]

    # Aggregate structures
    total_record_counts: Dict[int, Dict[str, int]] = {
        tid: {"3-0": 0, "3-1": 0, "3-2": 0, "2-3": 0, "1-3": 0, "0-3": 0}
        for tid in team_ids
    }
    total_player_sums: Dict[int, Dict[int, Dict[str, float]]] = {
        tid: {} for tid in team_ids
    }

    # Run chunks in parallel
    if len(args_list) == 1:
        # single-worker fallback
        rc, ps = _run_chunk(args_list[0])
        results = [(rc, ps)]
    else:
        with ProcessPoolExecutor(max_workers=len(args_list)) as ex:
            results = list(ex.map(_run_chunk, args_list))

    # Merge partial results
    for record_counts_chunk, player_sums_chunk in results:
        # records
        for tid, rec_map in record_counts_chunk.items():
            for rec, cnt in rec_map.items():
                total_record_counts[tid][rec] += cnt

        # players
        for tid, players_map in player_sums_chunk.items():
            for pid, comps in players_map.items():
                bucket = total_player_sums[tid].setdefault(
                    pid,
                    {
                        "total": 0.0,
                        "rating": 0.0,
                        "win": 0.0,
                        "role": 0.0,
                        "booster": 0.0,
                    },
                )
                bucket["total"] += comps["total"]
                bucket["rating"] += comps["rating"]
                bucket["win"] += comps["win"]
                bucket["role"] += comps["role"]
                bucket["booster"] += comps["booster"]

    # Convert counts to probabilities and sums to averages
    results_out: Dict[int, Dict] = {}

    for tid in team_ids:
        team_result: Dict[str, object] = {}

        # record probabilities
        for rec, cnt in total_record_counts[tid].items():
            team_result[rec] = cnt / float(n_sims)

        # expected fantasy points per player
        players_out: Dict[int, Dict[str, float]] = {}
        for pid, sums in total_player_sums[tid].items():
            players_out[pid] = {
                "total": sums["total"] / float(n_sims),
                "rating": sums["rating"] / float(n_sims),
                "win": sums["win"] / float(n_sims),
                "role": sums["role"] / float(n_sims),
                "booster": sums["booster"] / float(n_sims),
            }

        team_result["players"] = players_out
        results_out[tid] = team_result

    return results_out
