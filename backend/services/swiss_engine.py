"""The Swiss stage as a group format of the groups engine ("swiss").

A Swiss stage cannot be enumerated (2^40 match results for 16 teams), so
its outcome space is a seeded Monte Carlo sample of whole stages: the
pairing rules of swiss_stage/pairing.py (seeded first round, then Buchholz
order with no rematches), the detected Bo mode, and the win model's series
odds between every pair of teams. Each sample is kept as the PATH of every
team — opponent, result and series type per round — not as points: points
are a function of the path, scored by the same deterministic match scorer
the groups and playoff engines use, over a memoised table of the distinct
(team, opponent, result, match number, series) pairings. From the paths the
engine derives, with numpy over all samples at once:

  * every player's expected points and the model the Top 5 modes score
    rosters over (vectors + decomposition, as _sample_joint_outcomes gives),
  * the per-round Point Sources (stage stats) in the groups modal contract,
  * the same, conditioned on any subset of samples (the outcomes a roster
    wins / its best one), plus the team's runs through the stage.

Samples are reproducible from the stored run alone (seed, team order, win
matrices), so a roster's winning sample indexes stay valid across restarts.
"""
from __future__ import annotations

import os
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

ROUNDS = 5
TARGET = 3  # wins to qualify / losses to go out
SWISS_SAMPLES = 30000
SWISS_SEED = 20240101
_CHUNK = 2500  # fixed chunking: the sample stream is the same whatever the worker count
RECORDS = ("3-0", "3-1", "3-2", "2-3", "1-3", "0-3")
ROUND_LABELS = [f"R{r + 1}" for r in range(ROUNDS)]
BO_MODES = {"all", "none", "elim_qual"}
_WIN_PENALTY = -3.0
_PAD_WIN = 6.0


@dataclass
class SwissSamples:
    """Every sampled stage as paths: arrays (n, T, ROUNDS) indexed by sample,
    team position in the seed order and round."""

    opp: Any  # int16: opponent's team position, -1 when the team did not play that round
    won: Any  # int8: 1 won, 0 lost, -1 not played
    bo: Any  # int8: 1 or 3
    played: Any  # int8 (n, T)
    wins: Any  # int8 (n, T)

    @property
    def n(self) -> int:
        return int(self.opp.shape[0])


def match_bo(wins: int, losses: int, bo3_mode: str) -> int:
    if bo3_mode == "all":
        return 3
    if bo3_mode == "none":
        return 1
    return 3 if (wins == 2 or losses == 2) else 1


def _sample_chunk(args: tuple):
    """One worker's chunk of stages, from its own seed."""
    import numpy as np

    from backend.swiss_stage.pairing import generate_pairings
    from backend.swiss_stage.swiss_models import TeamState

    team_ids, seeds, p_bo1, p_bo3, bo3_mode, n, seed = args
    T = len(team_ids)
    pos = {tid: i for i, tid in enumerate(team_ids)}
    rng = random.Random(seed)
    opp = np.full((n, T, ROUNDS), -1, dtype=np.int16)
    won = np.full((n, T, ROUNDS), -1, dtype=np.int8)
    bo = np.zeros((n, T, ROUNDS), dtype=np.int8)
    played = np.zeros((n, T), dtype=np.int8)
    wins = np.zeros((n, T), dtype=np.int8)
    probs = {1: p_bo1, 3: p_bo3}
    for s in range(n):
        states = {tid: TeamState(team_id=tid, vrs_rank=int(seeds[tid]), players={}) for tid in team_ids}
        for r in range(ROUNDS):
            pools: Dict[tuple, list] = {}
            for t in states.values():
                if t.wins >= TARGET or t.losses >= TARGET:
                    continue
                pools.setdefault((t.wins, t.losses), []).append(t)
            if not pools:
                break
            for key in sorted(pools):
                for a, b in generate_pairings(pools[key], states):
                    series = match_bo(a.wins, a.losses, bo3_mode)
                    ia, ib = pos[a.team_id], pos[b.team_id]
                    a_won = rng.random() < probs[series][ia][ib]
                    if a_won:
                        a.record_win(b.team_id)
                        b.record_loss(a.team_id)
                    else:
                        b.record_win(a.team_id)
                        a.record_loss(b.team_id)
                    opp[s, ia, r] = ib
                    opp[s, ib, r] = ia
                    won[s, ia, r] = 1 if a_won else 0
                    won[s, ib, r] = 0 if a_won else 1
                    bo[s, ia, r] = series
                    bo[s, ib, r] = series
        for tid, t in states.items():
            played[s, pos[tid]] = t.wins + t.losses
            wins[s, pos[tid]] = t.wins
    return opp, won, bo, played, wins


def sample_paths(
    team_ids: List[int], seeds: Dict[int, int], p_bo1: List[List[float]], p_bo3: List[List[float]], bo3_mode: str,
    n: int, seed: int,
) -> SwissSamples:
    """n stages, chunked in a fixed way and seeded per chunk, in parallel."""
    import numpy as np

    chunks = [_CHUNK] * (n // _CHUNK) + ([n % _CHUNK] if n % _CHUNK else [])
    args = [(list(team_ids), dict(seeds), p_bo1, p_bo3, bo3_mode, c, int(seed) + i) for i, c in enumerate(chunks)]
    workers = min(len(args), os.cpu_count() or 1, 61)
    if len(args) == 1 or workers <= 1:
        parts = [_sample_chunk(a) for a in args]
    else:
        try:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                parts = list(ex.map(_sample_chunk, args))
        except Exception:  # noqa: BLE001 - no process pool here (frozen / sandboxed): same stream, serially
            parts = [_sample_chunk(a) for a in args]
    return SwissSamples(
        opp=np.concatenate([p[0] for p in parts]),
        won=np.concatenate([p[1] for p in parts]),
        bo=np.concatenate([p[2] for p in parts]),
        played=np.concatenate([p[3] for p in parts]),
        wins=np.concatenate([p[4] for p in parts]),
    )


def win_matrices(team_ids: List[int], bo3_mode: str, prob_cache: Dict) -> Dict[str, List[List[float]]]:
    """P(row beats column) per series type the mode uses, through the shared
    canonical-direction cache (the match scorer reads the same)."""
    from backend.routes.playoff import cached_win_prob

    out: Dict[str, List[List[float]]] = {}
    for series in ((3,) if bo3_mode == "all" else (1,) if bo3_mode == "none" else (1, 3)):
        out[f"bo{series}"] = [
            [cached_win_prob(prob_cache, a, b, f"bo{series}") if a != b else 0.5 for b in team_ids] for a in team_ids
        ]
    return out


# ----------------------------------------------------------------- scoring


@dataclass
class SwissPaths:
    """The scored sample space: the paths, their pairing index and the
    per-pairing points of every player."""

    samples: SwissSamples
    team_ids: List[int]
    pair_id: Any  # int32 (n, T, ROUNDS): index into the pairing table, -1 when not played
    match_no: Any  # int8 (n, T, ROUNDS): the team's match number that round (1-based), 0 when not played
    keys: List[tuple]  # pairing index -> (team pos, opponent pos, won, match number, series)
    pids: List[int]  # dense player order
    pid_pos: Dict[int, int]
    team_pos_of: Dict[int, int]  # pid -> team position
    tbl: Any  # float64 (K, P, 4): rating, win, role, booster of each player in each pairing (0 off-team)
    tbl_bid: Any  # int32 (K, P): the player's booster in that pairing, -1 none
    tbl_rate: Any  # float64 (K, P)
    booster_names: Dict[int, str]
    pad_rating: Any  # float64 (P,): one padded match's rating points (base rating)
    pad_role: Any  # float64 (P,): one padded match's role points (own role)
    role_of: Dict[int, dict]  # pid -> {role_id, major, minor, points}
    probs_by_series: Dict[int, Any]  # 1/3 -> np matrix


def _pairing_index(samples: SwissSamples, T: int) -> tuple:
    import numpy as np

    mask = samples.opp >= 0
    match_no = np.cumsum(mask, axis=2).astype(np.int8) * mask  # (n, T, R)
    t_idx = np.arange(T, dtype=np.int64)[None, :, None]
    packed = (((t_idx * T + samples.opp.astype(np.int64)) * 2 + samples.won.astype(np.int64)) * (ROUNDS + 1)
              + match_no.astype(np.int64)) * 2 + (samples.bo.astype(np.int64) == 3)
    flat = packed[mask]
    uniq, inv = np.unique(flat, return_inverse=True)
    pair_id = np.full(samples.opp.shape, -1, dtype=np.int32)
    pair_id[mask] = inv.astype(np.int32)
    keys = []
    for u in uniq.tolist():
        series = 3 if (u & 1) else 1
        u >>= 1
        m = u % (ROUNDS + 1)
        u //= (ROUNDS + 1)
        w = u % 2
        u //= 2
        o = u % T
        t = u // T
        keys.append((int(t), int(o), int(w), int(m), series))
    return pair_id, match_no, keys


def score_paths(
    samples: SwissSamples, team_ids: List[int], base_states: Dict[int, Any], player_rows_by_id: Dict[int, dict],
    team_rank_by_id: Dict[int, int], prob_cache: Dict, probs: Dict[str, List[List[float]]],
) -> SwissPaths:
    """Score every distinct pairing the samples contain for the players of
    the team involved (the match scorer on fresh states at that match
    number), and the per-player padding constants."""
    import numpy as np

    from backend.routes.playoff import _fresh_bracket_state, _play_match_deterministic
    from backend.swiss_stage.fantasy_scoring import compute_padding_components, compute_role_points

    T = len(team_ids)
    pair_id, match_no, keys = _pairing_index(samples, T)
    pids: List[int] = []
    team_pos_of: Dict[int, int] = {}
    for t, tid in enumerate(team_ids):
        for pid in base_states[tid].players:
            pids.append(int(pid))
            team_pos_of[int(pid)] = t
    pid_pos = {pid: i for i, pid in enumerate(pids)}
    K, P = len(keys), len(pids)
    tbl = np.zeros((K, P, 4), dtype=np.float64)
    tbl_bid = np.full((K, P), -1, dtype=np.int32)
    tbl_rate = np.zeros((K, P), dtype=np.float64)
    booster_names: Dict[int, str] = {}
    for k, (t, o, w, m, series) in enumerate(keys):
        a, b = team_ids[t], team_ids[o]
        states = {a: _fresh_bracket_state(base_states, a, m - 1), b: _fresh_bracket_state(base_states, b, 0)}
        _play_match_deterministic(
            states, a, b, a if w else b, remaining_rounds_after=0, prob_cache=prob_cache,
            player_rows_by_id=player_rows_by_id, team_rank_by_id=team_rank_by_id, match_type=f"bo{series}",
        )
        for pid, p in states[a].players.items():
            real = next((r for r in p.point_breakdown if r.get("match_number") is not None), None)
            if real is None:
                continue
            j = pid_pos[int(pid)]
            tbl[k, j] = (
                float(real.get("rating_points") or 0.0), float(real.get("win_points") or 0.0),
                float(real.get("role_points") or 0.0), float(real.get("booster_points") or 0.0),
            )
            bid = real.get("booster_id")
            if bid is not None:
                tbl_bid[k, j] = int(bid)
                tbl_rate[k, j] = float(real.get("booster_trigger_rate") or 0.0)
                booster_names.setdefault(int(bid), str(real.get("booster_name") or f"Booster {bid}"))
    pad_rating = np.zeros(P, dtype=np.float64)
    pad_role = np.zeros(P, dtype=np.float64)
    role_of: Dict[int, dict] = {}
    for tid in team_ids:
        for pid, p in base_states[tid].players.items():
            j = pid_pos[int(pid)]
            comps = compute_padding_components(p)
            pad_rating[j] = float(comps["rating"])
            pad_role[j] = float(comps["role"])
            role_of[int(pid)] = {
                "role_id": p.role_id, "major": float(p.major_pct), "minor": float(p.minor_pct),
                "points": float(compute_role_points(p)),
            }
    probs_by_series = {int(k[2:]): np.asarray(v, dtype=np.float64) for k, v in probs.items()}
    return SwissPaths(
        samples=samples, team_ids=list(team_ids), pair_id=pair_id, match_no=match_no, keys=keys, pids=pids,
        pid_pos=pid_pos, team_pos_of=team_pos_of, tbl=tbl, tbl_bid=tbl_bid, tbl_rate=tbl_rate,
        booster_names=booster_names, pad_rating=pad_rating, pad_role=pad_role, role_of=role_of,
        probs_by_series=probs_by_series,
    )


def _player_matrix(paths: SwissPaths, pid: int, sel=None) -> Dict[str, Any]:
    """Per-sample arrays for one player over the selected samples: per-round
    components (n, R) with their own role and boosters, the played mask, and
    the padding / penalty match counts."""
    import numpy as np

    j = paths.pid_pos[pid]
    t = paths.team_pos_of[pid]
    s = paths.samples
    ids = paths.pair_id[:, t, :] if sel is None else paths.pair_id[sel, t, :]
    mask = ids >= 0
    safe = np.where(mask, ids, 0)
    comps = paths.tbl[safe, j, :] * mask[:, :, None]  # (m, R, 4)
    played = s.played[:, t] if sel is None else s.played[sel, t]
    wins = s.wins[:, t] if sel is None else s.wins[sel, t]
    missing = (ROUNDS - played.astype(np.int64)).clip(min=0)
    qualified = wins >= TARGET
    return {
        "ids": ids, "safe": safe, "mask": mask, "comps": comps,
        "opp": s.opp[:, t, :] if sel is None else s.opp[sel, t, :],
        "won": s.won[:, t, :] if sel is None else s.won[sel, t, :],
        "match_no": paths.match_no[:, t, :] if sel is None else paths.match_no[sel, t, :],
        "played": played, "wins": wins, "missing": missing, "qualified": qualified,
        "pad_matches": missing * qualified, "pen_matches": missing * (~qualified),
    }


def player_totals(paths: SwissPaths, pid: int) -> Dict[str, Any]:
    """The player's per-sample points with their own role and boosters:
    components (n, 4) summed over the rounds plus padding and penalty, and
    the decomposition the roster planner needs."""
    import numpy as np

    j = paths.pid_pos[pid]
    m = _player_matrix(paths, pid)
    comps = m["comps"].sum(axis=1)  # (n, 4)
    pad = m["pad_matches"].astype(np.float64)
    pen = m["pen_matches"].astype(np.float64)
    comps[:, 0] += pad * paths.pad_rating[j]
    comps[:, 1] += pad * _PAD_WIN + pen * _WIN_PENALTY
    comps[:, 2] += pad * paths.pad_role[j]
    return {
        "comps": comps,
        "total": comps.sum(axis=1),
        "rw": comps[:, 0] + comps[:, 1],
        "rt": comps[:, 0].copy(),
        "mp": m["played"].astype(np.uint8),
        "mr": m["played"].astype(np.float64) + pad,
    }


def build_model(paths: SwissPaths) -> tuple:
    """(vectors, probs, group_of_player, decomposition): the single-axis
    outcome model of the groups Top 5 (every sample 1/n), with the paths
    attached for the conditional views."""
    import numpy as np

    n = paths.samples.n
    vec: Dict[int, Any] = {}
    rw: Dict[int, Any] = {}
    rt: Dict[int, Any] = {}
    mp: Dict[int, Any] = {}
    mr: Dict[int, Any] = {}
    means: Dict[int, Dict[str, float]] = {}
    for pid in paths.pids:
        tot = player_totals(paths, pid)
        vec[pid] = tot["total"]
        rw[pid] = tot["rw"]
        rt[pid] = tot["rt"]
        mp[pid] = tot["mp"]
        mr[pid] = tot["mr"]
        c = tot["comps"].mean(axis=0)
        means[pid] = {"rating": float(c[0]), "win": float(c[1]), "role": float(c[2]), "booster": float(c[3]), "total": float(c.sum())}
    decomp = {"rw": rw, "rt": rt, "mp": mp, "mr": mr, "paths": paths, "means": means}
    return {0: vec}, {0: np.full(n, 1.0 / n, dtype=np.float64)}, {pid: 0 for pid in vec}, decomp


def team_summary(paths: SwissPaths) -> Dict[str, Any]:
    """Records, qualify odds and match-reach odds per team over every sample."""
    import numpy as np

    s = paths.samples
    n = float(s.n)
    records: Dict[str, Dict[str, float]] = {}
    qualify: Dict[str, float] = {}
    reach: Dict[str, Dict[str, float]] = {}
    for t, tid in enumerate(paths.team_ids):
        w, p = s.wins[:, t].astype(np.int64), s.played[:, t].astype(np.int64)
        losses = p - w
        records[str(tid)] = {rec: float(np.mean((w == int(rec[0])) & (losses == int(rec[2])))) for rec in RECORDS}
        qualify[str(tid)] = float(np.mean(w >= TARGET))
        reach[str(tid)] = {str(k): float(np.mean(p >= k)) for k in range(1, ROUNDS + 1)}
    return {"records": records, "qualify": qualify, "match_reach": reach}


# -------------------------------------------------------------- per-round view


def _booster_rows(bucket: Dict[int, list]) -> List[Dict[str, Any]]:
    from backend.routes.groups import _booster_list

    return _booster_list(bucket)


def round_summary(paths: SwissPaths, pid: int, sel, w, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One player's per-round view over the selected samples (weights w, sum
    1), under the roster's plan when given, in the keys _assemble_stage_stats
    reads for a playoff summary: the team's reach / wins / eliminations /
    opponents per round and the player's components, boosters, opponents,
    penalty and padding."""
    import numpy as np

    j = paths.pid_pos[pid]
    t = paths.team_pos_of[pid]
    tid = paths.team_ids[t]
    T = len(paths.team_ids)
    m = _player_matrix(paths, pid, sel)
    w = np.asarray(w, dtype=np.float64)
    comps = m["comps"].copy()  # (m, R, 4)
    mask = m["mask"]
    role_pm = (plan or {}).get("role_pm")
    by_slot = (plan or {}).get("by_slot")
    if role_pm is not None:
        comps[:, :, 2] = mask * float(role_pm)
    if by_slot is not None:
        slot_pts = np.zeros(ROUNDS + 2, dtype=np.float64)
        for slot, b in by_slot.items():
            if 0 <= int(slot) <= ROUNDS + 1:
                slot_pts[int(slot)] = float(b[3])
        comps[:, :, 3] = slot_pts[m["match_no"].astype(np.int64)] * mask
    wm = w[:, None] * mask  # (m, R)
    round_reach = wm.sum(axis=0)
    round_wins = (w[:, None] * (m["won"] == 1)).sum(axis=0)
    stage_ev = (comps * wm[:, :, None]).sum(axis=0)  # (R, 4)
    opp = m["opp"].astype(np.int64)
    round_opp: Dict[str, Dict[str, Dict[str, float]]] = {}
    opp_pts: Dict[str, Dict[str, Dict[str, float]]] = {}
    boost: Dict[str, List[Dict[str, Any]]] = {}
    for r in range(ROUNDS):
        mk = mask[:, r]
        if not mk.any():
            continue
        o = opp[mk, r]
        wr = w[mk]
        play = np.bincount(o, weights=wr, minlength=T)
        win = np.bincount(o, weights=wr * (m["won"][mk, r] == 1), minlength=T)
        round_opp[str(r)] = {
            str(paths.team_ids[k]): {"play": float(play[k]), "win": float(win[k])} for k in np.nonzero(play > 0)[0]
        }
        per = [np.bincount(o, weights=wr * comps[mk, r, c], minlength=T) for c in range(4)]
        opp_pts[str(r)] = {
            str(paths.team_ids[k]): {
                "rating": float(per[0][k]), "win": float(per[1][k]), "role": float(per[2][k]), "booster": float(per[3][k]),
                "total": float(per[0][k] + per[1][k] + per[2][k] + per[3][k]),
            }
            for k in np.nonzero(play > 0)[0]
        }
        bucket: Dict[int, list] = {}
        if by_slot is not None:
            slots = m["match_no"][mk, r].astype(np.int64)
            for slot in np.unique(slots):
                b = by_slot.get(int(slot))
                if b is None:
                    continue
                weight = float(wr[slots == slot].sum())
                cell = bucket.get(b[0])
                if cell is None:
                    bucket[b[0]] = [weight, b[1], b[2], int(slot), 0.0]
                else:
                    cell[0] += weight
        else:
            bids = paths.tbl_bid[m["safe"][mk, r], j]
            rates = paths.tbl_rate[m["safe"][mk, r], j]
            slots = m["match_no"][mk, r].astype(np.int64)
            for bid in np.unique(bids[bids >= 0]):
                pick = bids == bid
                bucket[int(bid)] = [
                    float(wr[pick].sum()), paths.booster_names.get(int(bid), f"Booster {bid}"),
                    float(rates[pick][0]), int(slots[pick][0]), 0.0,
                ]
        boost[str(r)] = _booster_rows(bucket)
    pen = m["pen_matches"].astype(np.float64)
    pad = m["pad_matches"].astype(np.float64)
    elim_round = m["played"].astype(np.int64) - 1
    round_elim: Dict[str, Dict[str, float]] = {}
    for r in range(ROUNDS):
        pick = (pen > 0) & (elim_round == r)
        if pick.any():
            round_elim[str(r)] = {"prob": float(w[pick].sum()), "points": float((w * pen * _WIN_PENALTY)[pick].sum())}
    pad_role = float(role_pm) if role_pm is not None else float(paths.pad_role[j])
    pad_sum = [
        float((w * pad).sum() * paths.pad_rating[j]), float((w * pad).sum() * _PAD_WIN), float((w * pad).sum() * pad_role), 0.0,
    ]
    bye_prob = float(w[pad > 0].sum())
    return {
        "round_labels": list(ROUND_LABELS),
        "third_place": False,
        "round_reach": {str(tid): [float(v) for v in round_reach]},
        "round_wins": {str(tid): [float(v) for v in round_wins]},
        "round_elim": {str(tid): round_elim},
        "round_opp": {str(tid): round_opp},
        "bye_prob": {str(tid): bye_prob},
        "advance_rate": {str(tid): float(w[m["qualified"]].sum())},
        "player_stage_ev": {
            str(pid): {
                str(r): {
                    "rating": float(stage_ev[r, 0]), "win": float(stage_ev[r, 1]), "role": float(stage_ev[r, 2]),
                    "booster": float(stage_ev[r, 3]), "total": float(stage_ev[r].sum()),
                }
                for r in range(ROUNDS)
                if round_reach[r] > 0
            }
        },
        "player_penalty": {str(pid): float((w * pen * _WIN_PENALTY).sum())},
        "player_playoff_padding": (
            {str(pid): {"rating": pad_sum[0], "win": pad_sum[1], "role": pad_sum[2], "booster": pad_sum[3], "total": sum(pad_sum)}}
            if bye_prob > 0
            else {}
        ),
        "player_round_opp_pts": {str(pid): opp_pts},
        "player_stage_boost": {str(pid): boost},
    }


def merge_summaries(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Several players' round summaries (one team row each) into one playoff-
    shaped summary for _assemble_stage_stats."""
    out: Dict[str, Any] = {"round_labels": list(ROUND_LABELS), "third_place": False}
    for key in (
        "round_reach", "round_wins", "round_elim", "round_opp", "bye_prob", "advance_rate", "player_stage_ev",
        "player_penalty", "player_playoff_padding", "player_round_opp_pts", "player_stage_boost",
    ):
        merged: Dict[str, Any] = {}
        for part in parts:
            merged.update(part.get(key) or {})
        out[key] = merged
    return out


def stage_stats(paths: SwissPaths, teams_out: Dict[Any, Any], team_rank_by_id: Dict[int, int]) -> Dict[str, Any]:
    """The stored run's Point Sources over every sample, for every player."""
    import numpy as np

    from backend.routes.groups import _assemble_stage_stats, _new_stage_acc

    n = paths.samples.n
    w = np.full(n, 1.0 / n)
    sel = np.arange(n)
    parts = [round_summary(paths, pid, sel, w) for pid in paths.pids]
    stats = _assemble_stage_stats("swiss", _new_stage_acc(), merge_summaries(parts), teams_out, team_rank_by_id)
    return finish_stage_stats(stats, paths)


def finish_stage_stats(stats: Dict[str, Any], paths: SwissPaths, role_info: Optional[Dict[int, dict]] = None) -> Dict[str, Any]:
    """Tag the rounds as the Swiss stage section and attach each player's role."""
    for st in stats.get("stages") or []:
        st["section"] = "playoff"
        st["section_title"] = "Swiss Stage"
    for pid_s, row in (stats.get("players") or {}).items():
        info = (role_info or {}).get(int(pid_s)) or paths.role_of.get(int(pid_s))
        if info:
            row["role"] = dict(info)
    return stats


# ------------------------------------------------------------------- runs


def runs(paths: SwissPaths, pid: int, sel, w, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The team's runs through the stage over the selected samples, grouped
    by results path, in player_runs' output (see services/player_runs.py)."""
    import numpy as np

    from backend.services import player_runs

    j = paths.pid_pos[pid]
    t = paths.team_pos_of[pid]
    T = len(paths.team_ids)
    m = _player_matrix(paths, pid, sel)
    w = np.asarray(w, dtype=np.float64)
    comps = m["comps"].copy()
    mask = m["mask"]
    role_pm = (plan or {}).get("role_pm")
    by_slot = (plan or {}).get("by_slot")
    if role_pm is not None:
        comps[:, :, 2] = mask * float(role_pm)
    if by_slot is not None:
        slot_pts = np.zeros(ROUNDS + 2, dtype=np.float64)
        for slot, b in by_slot.items():
            if 0 <= int(slot) <= ROUNDS + 1:
                slot_pts[int(slot)] = float(b[3])
        comps[:, :, 3] = slot_pts[m["match_no"].astype(np.int64)] * mask
    code = ((m["won"].astype(np.int64) + 1) * (3 ** np.arange(ROUNDS, dtype=np.int64))[None, :]).sum(axis=1)
    pen = m["pen_matches"].astype(np.float64) * _WIN_PENALTY
    pad = m["pad_matches"].astype(np.float64)
    pad_role = float(role_pm) if role_pm is not None else float(paths.pad_role[j])
    run_map: Dict[tuple, Dict[str, Any]] = {}
    for c in np.unique(code):
        g = code == c
        wg = w[g]
        weight = float(wg.sum())
        if weight <= 0:
            continue
        matches = []
        for r in range(ROUNDS):
            if not mask[g, r].any():
                break
            o = m["opp"][g, r].astype(np.int64)
            opps = np.bincount(o, weights=wg, minlength=T)
            sums = (comps[g, r, :] * wg[:, None]).sum(axis=0)
            boosters: Dict[str, float] = {}
            if by_slot is not None:
                slot = int(m["match_no"][g, r][0])
                b = by_slot.get(slot)
                if b is not None:
                    boosters[b[1]] = weight
            else:
                bids = paths.tbl_bid[m["safe"][g, r], j]
                for bid in np.unique(bids[bids >= 0]):
                    boosters[paths.booster_names.get(int(bid), f"Booster {bid}")] = float(wg[bids == bid].sum())
            matches.append({
                "stage": f"po_{r}", "label": ROUND_LABELS[r], "full": f"Round {r + 1}", "slot": int(m["match_no"][g, r][0]),
                "won": bool(m["won"][g, r][0] == 1),
                "opps": {paths.team_ids[k]: float(opps[k]) for k in np.nonzero(opps > 0)[0]},
                "boosters": boosters,
                "sums": {"rating": float(sums[0]), "win": float(sums[1]), "role": float(sums[2]), "booster": float(sums[3]), "total": float(sums.sum())},
            })
        pads = []
        pad_n = float((wg * pad[g]).sum())
        if pad_n > 0:
            p_sums = {"rating": pad_n * float(paths.pad_rating[j]), "win": pad_n * _PAD_WIN, "role": pad_n * pad_role, "booster": 0.0}
            p_sums["total"] = sum(p_sums.values())
            pads.append({"stage": "po_padding", "label": "Padding", "sums": p_sums})
        penalty_sum = float((wg * pen[g]).sum())
        totals = {k: sum(mm["sums"][k] for mm in matches) + sum(pp["sums"][k] for pp in pads) for k in ("rating", "win", "role", "booster")}
        totals["win"] += penalty_sum
        totals["total"] = sum(totals.values())
        sig = (tuple((mm["stage"], mm["won"]) for mm in matches), tuple(pp["stage"] for pp in pads))
        run_map[sig] = {"weight": weight, "matches": matches, "pads": pads, "penalty_sum": penalty_sum, "sums": totals}
    return player_runs.finish_runs(run_map, float(w.sum()) or 1.0)


def sample_probability(paths: SwissPaths, i: int) -> float:
    """The exact probability of one sampled stage: every match's result odds."""
    s = paths.samples
    prob = 1.0
    T = len(paths.team_ids)
    for t in range(T):
        for r in range(ROUNDS):
            o = int(s.opp[i, t, r])
            if o < 0 or o < t:
                continue  # each match once, from the lower position
            p = float(paths.probs_by_series[int(s.bo[i, t, r])][t, o])
            prob *= p if int(s.won[i, t, r]) == 1 else (1.0 - p)
    return prob
