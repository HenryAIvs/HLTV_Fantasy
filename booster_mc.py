# booster_mc.py

import math
import random
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ProcessPoolExecutor

from swiss_stage.swiss_models import TeamState
from swiss_stage.pairing import generate_pairings
from swiss_stage.swiss_round import get_match_type
from team_strength import get_team_winrate


# -------------------------------------------------------------------
# 1. Booster trigger probabilities from HLTV triggerRate
# -------------------------------------------------------------------

def bo_probs_from_rate(r: float) -> tuple[float, float]:
    """
    Given HLTV triggerRate r for a booster (0..1) for a Bo3 series where:
        - 58% of Bo3s end in 2 maps
        - 42% of Bo3s go to 3 maps

    We assume:
        - per-map trigger probability = q
        - series no-trigger probability:
              P(no trig) = 0.58 * (1 - q)^2 + 0.42 * (1 - q)^3
        - series triggerRate r = 1 - P(no trig)

    We solve for q in [0,1] and then define:
        p_bo3 = r
        p_bo1 = q
    """
    # Clamp r for safety
    r = max(0.0, min(1.0, float(r)))
    if r <= 0.0:
        return 0.0, 0.0
    if r >= 1.0:
        return 1.0, 1.0  # always triggers

    target_no = 1.0 - r  # target P(no trig in series)

    p2 = 0.58
    p3 = 0.42

    # f(q) = P_no(q) - target_no; we want f(q) = 0
    def f(q: float) -> float:
        x = 1.0 - q
        return p2 * (x ** 2) + p3 * (x ** 3) - target_no

    # Bisection in [0,1]
    lo, hi = 0.0, 1.0
    for _ in range(40):  # 40 iterations is plenty
        mid = 0.5 * (lo + hi)
        if f(mid) == 0.0:
            q = mid
            break
        # f(q) is decreasing in q, so check sign
        if f(lo) * f(mid) > 0:
            lo = mid
        else:
            hi = mid
    else:
        q = 0.5 * (lo + hi)

    p_bo1 = max(0.0, min(1.0, q))
    p_bo3 = r
    return p_bo1, p_bo3


# -------------------------------------------------------------------
# 2. Simulate Swiss path (no boosters), record fantasy events
# -------------------------------------------------------------------

def simulate_swiss_path_events(
    team_states_init: Dict[int, TeamState],
    vrs_ranks: Dict[int, int],
    fantasy_players: List[int],
    player_team: Dict[int, int],
) -> List[Tuple[int, int, str]]:
    """
    Simulate a single Swiss path from the given state WITHOUT boosters,
    recording all (round_idx, player_id, match_type) events for the given
    fantasy players.

    Returns:
      events: list of (round_idx, player_id, match_type_str)
    """
    # Deep-copy team states (only Swiss state needed, not player lists)
    team_states: Dict[int, TeamState] = {}
    for tid, ts in team_states_init.items():
        new_ts = TeamState(
            team_id=ts.team_id,
            vrs_rank=ts.vrs_rank,
            players={},  # not used here
        )
        new_ts.wins = ts.wins
        new_ts.losses = ts.losses
        new_ts.opponents_played = set(ts.opponents_played)
        team_states[tid] = new_ts

    events: List[Tuple[int, int, str]] = []
    round_idx = 0

    while True:
        # Build pools of "in play" teams
        pools: Dict[Tuple[int, int], List[TeamState]] = {}
        in_play = []
        for t in team_states.values():
            if t.wins < 3 and t.losses < 3:
                key = (t.wins, t.losses)
                pools.setdefault(key, []).append(t)
                in_play.append(t)

        if not in_play:
            break  # Swiss done

        # Generate pairings across all pools
        round_pairings: List[Tuple[TeamState, TeamState, str]] = []

        for _, pool in pools.items():
            if len(pool) < 2:
                continue
            pool_sorted = sorted(pool, key=lambda tt: tt.vrs_rank)
            pairings = generate_pairings(pool_sorted)
            for A, B in pairings:
                mtype = get_match_type(A, "elim_qual")
                round_pairings.append((A, B, mtype))

        if not round_pairings:
            break

        # Record events for fantasy players
        for A, B, mtype in round_pairings:
            for pid in fantasy_players:
                tid = player_team.get(pid)
                if tid == A.team_id or tid == B.team_id:
                    events.append((round_idx, pid, mtype))

        # Resolve matches
        for A, B, mtype in round_pairings:
            pA = get_team_winrate(A.team_id, B.team_id, mtype)
            if random.random() < pA:
                winner, loser = A, B
            else:
                winner, loser = B, A
            winner.wins += 1
            loser.losses += 1
            winner.opponents_played.add(loser.team_id)
            loser.opponents_played.add(winner.team_id)

        round_idx += 1

    return events


# -------------------------------------------------------------------
# 3. Global greedy booster allocation for one path
# -------------------------------------------------------------------

def _global_greedy_round0_evs_for_path(
    events: List[Tuple[int, int, str]],
    fantasy_players: List[int],
    player_booster_triggers: Dict[int, Dict[int, float]],
    booster_ids: List[int],
) -> Dict[Tuple[int, int], float]:
    """
    Given all events in a Swiss path and per-player booster trigger rates,
    perform a global greedy allocation of boosters across all rounds and
    all fantasy players, and return the EV contributed in round 0 only.

    booster_ids: list of booster IDs that are available at the start
                 of this path (e.g., remaining boosters).

    Returns:
      ev_round0: mapping (player_id, booster_id) -> EV contribution
                 from this path for events in round_idx == 0.
    """
    edges: List[Tuple[float, int, int]] = []
    for e_idx, (r_idx, pid, mtype) in enumerate(events):
        if pid not in fantasy_players:
            continue
        trig_map = player_booster_triggers.get(pid, {})
        for bid in booster_ids:
            r = trig_map.get(bid, 0.0)
            if r <= 0.0:
                continue
            p_bo1, p_bo3 = bo_probs_from_rate(r)
            p = p_bo3 if mtype.lower() == "bo3" else p_bo1
            ev = 5.0 * p
            if ev > 0.0:
                edges.append((ev, e_idx, bid))

    edges.sort(key=lambda x: x[0], reverse=True)

    used_boosters = set()
    used_events = set()
    ev_round0: Dict[Tuple[int, int], float] = {}

    for ev, e_idx, bid in edges:
        if bid in used_boosters:
            continue
        if e_idx in used_events:
            continue

        used_boosters.add(bid)
        used_events.add(e_idx)

        r_idx, pid, _ = events[e_idx]
        if r_idx == 0:
            key = (pid, bid)
            ev_round0[key] = ev_round0.get(key, 0.0) + ev

    return ev_round0


# -------------------------------------------------------------------
# 4. Worker: simulate many paths, accumulate EVs
# -------------------------------------------------------------------

def _run_paths_chunk(
    n_paths: int,
    team_states_init: Dict[int, TeamState],
    vrs_ranks: Dict[int, int],
    fantasy_players: List[int],
    player_team: Dict[int, int],
    player_booster_triggers: Dict[int, Dict[int, float]],
    booster_ids: List[int],
    log_prefix: str = "",
) -> Dict[Tuple[int, int], float]:
    """
    Run n_paths Swiss path simulations and accumulate EV for round0 edges:
      (player_id, booster_id) -> sum EV over paths in this chunk.
    """
    sums: Dict[Tuple[int, int], float] = {}

    for i in range(n_paths):
        events = simulate_swiss_path_events(
            team_states_init,
            vrs_ranks,
            fantasy_players,
            player_team,
        )
        ev_round0 = _global_greedy_round0_evs_for_path(
            events,
            fantasy_players,
            player_booster_triggers,
            booster_ids,
        )

        for key, val in ev_round0.items():
            sums[key] = sums.get(key, 0.0) + val

        # logging every ~5% of this chunk
        if n_paths >= 20 and (i + 1) % max(1, n_paths // 20) == 0:
            pct = (i + 1) / n_paths * 100.0
            print(f"{log_prefix} paths: {i+1}/{n_paths} ({pct:.1f}%)")

    return sums


def _run_paths_chunk_wrapper(args):
    return _run_paths_chunk(*args)


# -------------------------------------------------------------------
# 5. Public API: estimate round0 EVs for (player, booster)
# -------------------------------------------------------------------

def mc_estimate_round0_evs(
    n_paths: int,
    team_states_init: Dict[int, TeamState],
    vrs_ranks: Dict[int, int],
    fantasy_players: List[int],
    player_team: Dict[int, int],
    player_booster_triggers: Dict[int, Dict[int, float]],
    n_workers: int = 4,
    available_boosters: Optional[List[int]] = None,
) -> Dict[Tuple[int, int], float]:
    """
    Run Monte Carlo and estimate expected EV in the *next round* (round_idx=0)
    for each (player_id, booster_id) pair, under globally optimal shared-pool
    booster allocation across all rounds, starting from the given set of
    available boosters.

    Inputs:
      - n_paths: number of Swiss paths to simulate.
      - team_states_init: current Swiss state (wins/losses/opponents_played).
      - vrs_ranks: team_id -> vrs_rank.
      - fantasy_players: list of your selected player_ids (up to 5).
      - player_team: player_id -> team_id.
      - player_booster_triggers: pid -> {booster_id -> triggerRate r (0..1)}.
      - available_boosters: list of booster IDs still available at the start
                            (e.g. after you tick off used ones). If None:
                            defaults to [0..17].

    Returns:
      avg_evs: mapping (player_id, booster_id) -> average EV contribution
               in round0 across paths.
    """
    if n_paths <= 0:
        return {}

    n_workers = max(1, n_workers)
    if available_boosters is None:
        booster_ids = list(range(18))
    else:
        booster_ids = list(available_boosters)

    # Chunk paths across workers
    paths_per_worker = [n_paths // n_workers] * n_workers
    for i in range(n_paths % n_workers):
        paths_per_worker[i] += 1
    paths_per_worker = [p for p in paths_per_worker if p > 0]

    args_list = []
    for wi, n_p in enumerate(paths_per_worker):
        log_prefix = f"[BoosterMC worker={wi}]"
        args_list.append(
            (n_p, team_states_init, vrs_ranks, fantasy_players,
             player_team, player_booster_triggers, booster_ids, log_prefix)
        )

    # Single worker shortcut
    if len(args_list) == 1:
        sums = _run_paths_chunk(*args_list[0])
        return {k: v / float(n_paths) for k, v in sums.items()}

    # Multiprocessing
    with ProcessPoolExecutor(max_workers=len(args_list)) as ex:
        chunk_results = list(ex.map(_run_paths_chunk_wrapper, args_list))

    # Aggregate across chunks
    total_sums: Dict[Tuple[int, int], float] = {}
    for sums in chunk_results:
        for key, val in sums.items():
            total_sums[key] = total_sums.get(key, 0.0) + val

    avg_evs = {k: v / float(n_paths) for k, v in total_sums.items()}
    return avg_evs
