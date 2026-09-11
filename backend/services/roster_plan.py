"""A roster's plan and how it scores.

Two layers shared by every Top 5 (groups, playoff, bounty, swiss):

* choosing the plan for a five-player roster — a clash-free role assignment
  (each player a distinct role) and a roster-wide booster assignment (each of
  the 18 boosters used at most once, one per player per match slot), both
  maximising expected points (`plan_for_roster`); and
* scoring the roster in a given outcome under that fixed plan
  (`plan_outcome_scores`): rating and win points as scored in the outcome,
  the assigned role's per-match points for every match the player is scored
  for, and the assigned boosters' points for the match slots actually played.

The functions above `plan_for_roster` were moved here from groups.py unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.swiss_booster_assignment import (
    BOOSTER_NAMES,
    BOOSTER_POINT_VALUE,
    _max_weight_assignment,
)
from backend.services.team_optimizer import serialize_roster


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


def _booster_slot_weights(pid: int, tid: int, reach_by_team, rates_by_pid) -> tuple:
    """(slot probabilities in match order, {booster_id: trigger rate}) for one player."""
    slots = [float(p) for _n, p in sorted((reach_by_team.get(int(tid)) or {}).items()) if p > 0]
    return slots, (rates_by_pid.get(int(pid)) or {})


def _player_booster_sub(slots: List[float], rates: Dict[int, float], lam: List[float]) -> tuple:
    """One player's Lagrangian sub-problem: assign boosters to their match
    slots (one per slot, each booster once) maximising Σ (slot × rate × value −
    λ_booster), negative reduced weights clipped to 0 (booster unused).
    Returns (value, {booster_id used})."""
    if not slots:
        return 0.0, set()
    weights = [
        [max(0.0, sp * float(rates.get(b, 0.0)) * BOOSTER_POINT_VALUE - lam[b]) for sp in slots]
        for b in range(18)
    ]
    value = 0.0
    used = set()
    for b, _c, w in _max_weight_assignment(weights):
        if w > 1e-9:
            value += float(w)
            used.add(int(b))
    return value, used


def _booster_bound_lagrangian(
    players_info: List[Dict[str, Any]],
    reach_by_team: Dict[int, Dict[int, float]],
    rates_by_pid: Dict[int, Dict[int, float]],
    reference_rosters: List[List[Dict[str, Any]]],
    iters: int = 40,
) -> tuple:
    """Additive booster bound that stays close to the exact roster solve.

    With booster prices λ ≥ 0 shared by every roster, for ANY roster
        exact booster ≤ Σ_players sub_p(λ) + Σ_b λ_b
    (each booster is used at most once, so the prices paid are at most Σλ;
    each player's part of any feasible assignment is feasible for sub_p). The
    plain per-player ceiling is the λ = 0 case and over-counts by ~40 points on
    a strong roster because all five players claim the same best boosters; the
    prices are tuned by subgradient descent to minimise the bound over the
    reference rosters (the strongest by the plain bound), where it becomes
    nearly tight. Returns ({pid: sub_p(λ)}, Σλ)."""
    slot_info = {
        int(p["player_id"]): _booster_slot_weights(int(p["player_id"]), int(p.get("team_id", 0)), reach_by_team, rates_by_pid)
        for p in players_info
    }
    lam = [0.0] * 18
    refs = [[int(p["player_id"]) for p in roster] for roster in reference_rosters if roster]
    if refs:
        step = 1.0
        for it in range(int(iters)):
            counts = [0.0] * 18
            for pids in refs:
                for pid in pids:
                    slots, rates = slot_info[pid]
                    _v, used = _player_booster_sub(slots, rates, lam)
                    for b in used:
                        counts[b] += 1.0 / len(refs)
            # d bound / d λ_b = 1 − (players using b): raise over-used prices, lower unused ones
            lam = [max(0.0, lam[b] - step * (1.0 - counts[b])) for b in range(18)]
            step *= 0.93
    sub = {}
    for pid, (slots, rates) in slot_info.items():
        sub[pid], _used = _player_booster_sub(slots, rates, lam)
    return sub, float(sum(lam))


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


def _score_roster_exact(
    pids: List[int],
    players_meta: Dict[str, Dict[str, Any]],
    reach_by_team: Dict[int, Dict[int, float]],
    rates_by_pid: Dict[int, Dict[int, float]],
    role_scores_by_pid: Dict[int, Dict[int, float]],
) -> Dict[str, Any]:
    """Serialised roster with the exact clash-free role assignment and the
    exact roster-wide booster assignment (each booster type once across the
    roster, one per player per match slot): average_ev = rating+win + exact
    role + exact booster, with per-player role_ev / booster_ev / total_ev
    patched to the assigned role and boosters so the player rows sum to it."""
    roster_players = [players_meta[str(pid)] for pid in pids]
    booster_result = optimize_group_boosters_for_roster(roster_players, reach_by_team, rates_by_pid)
    role_total, role_of, role_ev_of = _exact_role_assignment(roster_players, role_scores_by_pid)
    rating_win = sum(float(p.get("rating_ev", 0.0)) + float(p.get("win_ev", 0.0)) for p in roster_players)
    avg_ev = rating_win + role_total + float(booster_result["total_expected_booster_points"])
    cost = sum(int(p.get("price") or 0) for p in roster_players)
    serialized = serialize_roster(
        players_meta, pids, [str(role_of.get(pid, "-")) for pid in pids], avg_ev, cost,
        booster_assignments=booster_result["assignments"],
    )
    for player in serialized.get("players") or []:
        pid = int(player["player_id"])
        new_role_ev = role_ev_of.get(pid)
        if new_role_ev is not None:
            old_role_ev = float(player.get("role_ev") or 0.0)
            player["role_ev"] = float(new_role_ev)
            player["total_ev"] = float(player.get("total_ev") or 0.0) - old_role_ev + float(new_role_ev)
        player["mode_score"] = float(player.get("total_ev") or 0.0)
    serialized["average_ev"] = float(avg_ev)
    serialized["mode_metric"] = float(avg_ev)
    return serialized


def plan_for_roster(
    players: List[Dict[str, Any]],
    reach_by_team: Dict[int, Dict[int, float]],
    rates_by_pid: Dict[int, Dict[int, float]],
    role_scores_by_pid: Dict[int, Dict[int, float]],
) -> Dict[str, Any]:
    """The roster's plan: exact clash-free roles and the roster-wide booster
    assignment, both chosen to maximise expected points. Returns role_of
    {pid: role index}, role_pm {pid: assigned role's per-match points},
    role_ev_of {pid: expected role points}, role_total, boosters (the
    assignment rows), slot_rates {pid: {match number: trigger rate}} and
    booster_total."""
    booster = optimize_group_boosters_for_roster(players, reach_by_team, rates_by_pid)
    role_total, role_of, role_ev_of = _exact_role_assignment(players, role_scores_by_pid)
    role_pm: Dict[int, float] = {}
    for p in players:
        pid = int(p["player_id"])
        rs = role_scores_by_pid.get(pid) or {}
        r = role_of.get(pid)
        role_pm[pid] = float(rs.get(r, 0.0)) if r is not None else 0.0
    slot_rates: Dict[int, Dict[int, float]] = {}
    for a in booster.get("assignments") or []:
        pid = int(a["player_id"])
        rate = float((rates_by_pid.get(pid) or {}).get(int(a["booster_id"]), 0.0))
        slot_rates.setdefault(pid, {})[int(a["match_number"])] = rate
    return {
        "role_of": role_of,
        "role_ev_of": role_ev_of,
        "role_pm": role_pm,
        "role_total": float(role_total),
        "boosters": booster.get("assignments") or [],
        "booster_total": float(booster.get("total_expected_booster_points") or 0.0),
        "slot_rates": slot_rates,
    }


def booster_prefix_table(slot_rates: Dict[int, float], max_slot: int):
    """R[m] = booster points of the plan's slots 1..m (5 × trigger rate each),
    so a player who plays m matches in an outcome scores R[m]."""
    import numpy as np

    table = np.zeros(max(int(max_slot), 0) + 1, dtype=np.float64)
    for k in range(1, len(table)):
        table[k] = table[k - 1] + BOOSTER_POINT_VALUE * float(slot_rates.get(k, 0.0))
    return table


def plan_outcome_scores(plan: Dict[str, Any], pids: List[int], rows: List[int], RW, MP, MR):
    """The roster's score in every outcome under its plan (vector over
    outcomes): RW rows (rating + win as scored, penalties and paddings
    included) + assigned role per-match points × MR (matches the role is
    scored for) + the assigned boosters' points for the MP matches played."""
    import numpy as np

    S = RW[rows].sum(axis=0).astype(np.float64)
    max_slot = int(MP.max()) if MP.size else 0
    for pid, j in zip(pids, rows):
        rpm = float(plan["role_pm"].get(int(pid), 0.0))
        if rpm:
            S += rpm * MR[j]
        sr = plan["slot_rates"].get(int(pid))
        if sr:
            S += booster_prefix_table(sr, max_slot)[MP[j]]
    return S


def match_reach_from_played(pids: List[int], team_of: Dict[int, int], MP, probs) -> Dict[int, Dict[int, float]]:
    """{team: {match number: P(team plays that match)}} from the per-outcome
    matches-played table (any one player of the team stands for the team)."""
    import numpy as np

    reach: Dict[int, Dict[int, float]] = {}
    seen: set = set()
    for j, pid in enumerate(pids):
        tid = int(team_of.get(int(pid), 0))
        if tid in seen:
            continue
        seen.add(tid)
        row = MP[j]
        d: Dict[int, float] = {}
        for n in range(1, int(row.max()) + 1 if row.size else 1):
            p = float(probs[row >= n].sum())
            if p > 0:
                d[n] = p
        reach[tid] = d
    return reach


def plan_metrics(
    keys: List[tuple],
    players_meta: Dict[str, Dict[str, Any]],
    pid_idx: Dict[int, int],
    RW,
    MP,
    MR,
    probs,
    reach_by_team: Dict[int, Dict[int, float]],
    rates_by_pid: Dict[int, Dict[int, float]],
    role_scores_by_pid: Dict[int, Dict[int, float]],
    chunk: int = 16,
    progress_callback=None,
) -> List[Dict[str, Any]]:
    """For each candidate roster (a tuple of pids): its plan, expected points
    under the plan, its ceiling (best outcome), that outcome's probability and
    index, each player's score in it, and the share of outcomes the roster
    tops among the candidates. Vectorised in chunks over the outcome tables."""
    import numpy as np

    C = len(keys)
    N = RW.shape[1]
    max_slot = int(MP.max()) if MP.size else 0
    rows = np.asarray([[pid_idx[int(pid)] for pid in key] for key in keys], dtype=np.int64).reshape(C, 5)
    plans: List[Dict[str, Any]] = []
    role_pm = np.zeros((C, 5))
    tables = np.zeros((C, 5, max_slot + 1))
    for i, key in enumerate(keys):
        plan = plan_for_roster([players_meta[str(pid)] for pid in key], reach_by_team, rates_by_pid, role_scores_by_pid)
        plans.append(plan)
        for p, pid in enumerate(key):
            role_pm[i, p] = float(plan["role_pm"].get(int(pid), 0.0))
            sr = plan["slot_rates"].get(int(pid))
            if sr:
                tables[i, p] = booster_prefix_table(sr, max_slot)
    best_val = np.full(N, -np.inf)
    best_i = np.full(N, -1, dtype=np.int64)
    avg = np.empty(C)
    ceil = np.empty(C)
    ceil_p = np.empty(C)
    argmax = np.empty(C, dtype=np.int64)
    for st in range(0, C, chunk):
        R = rows[st:st + chunk]
        S = RW[R].sum(axis=1)
        S += (role_pm[st:st + chunk, :, None] * MR[R]).sum(axis=1)
        S += np.take_along_axis(tables[st:st + chunk], MP[R].astype(np.int64), axis=2).sum(axis=1)
        avg[st:st + chunk] = S @ probs
        mx = S.max(axis=1)
        ceil[st:st + chunk] = mx
        argmax[st:st + chunk] = S.argmax(axis=1)
        ceil_p[st:st + chunk] = ((S >= mx[:, None] - 1e-9) * probs).sum(axis=1)
        cb = S.max(axis=0)
        ca = S.argmax(axis=0)
        better = cb > best_val + 1e-9
        best_val[better] = cb[better]
        best_i[better] = st + ca[better]
        if progress_callback:
            progress_callback(min(C, st + chunk), C)
    wins_prob = np.zeros(C)
    wins_count = np.zeros(C)
    for c in range(N):
        i = best_i[c]
        if i >= 0:
            wins_prob[i] += probs[c]
            wins_count[i] += 1
    out = []
    for i, key in enumerate(keys):
        o = int(argmax[i])
        peak = {}
        for p, pid in enumerate(key):
            j = rows[i, p]
            peak[int(pid)] = float(RW[j, o]) + role_pm[i, p] * float(MR[j, o]) + float(tables[i, p, int(MP[j, o])])
        out.append({
            "plan": plans[i],
            "avg": float(avg[i]),
            "ceiling": float(ceil[i]),
            "ceiling_p": float(ceil_p[i]),
            "argmax": o,
            "peak": peak,
            "wins_prob": float(wins_prob[i]),
            "wins_count": float(wins_count[i]),
        })
    return out
