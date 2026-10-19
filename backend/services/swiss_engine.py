"""The Swiss stage's sampler for the path engine (services/paths_engine).

A Swiss stage cannot be enumerated (2^33 result combinations for 16 teams,
and the pairings depend on earlier results), so its outcome space is a
seeded Monte Carlo sample of whole stages: the pairing rules of
swiss_stage/pairing.py (seeded first round, then Buchholz order with no
rematches), the detected Bo mode, and the win model's series odds between
every pair of teams. Each sample is kept as the PATH of every team —
opponent, result and series type per round — and services/stage_formats
turns the samples into the engine's paths. Samples are reproducible from
the stored run alone (seed, team order, win matrices), so a roster's winning
sample indexes stay valid across restarts.
"""
from __future__ import annotations

import os
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List

ROUNDS = 5
TARGET = 3  # wins to qualify / losses to go out
SWISS_SAMPLES = 100000
SWISS_SEED = 20240101
_CHUNK = 2500  # fixed chunking: the sample stream is the same whatever the worker count
RECORDS = ("3-0", "3-1", "3-2", "2-3", "1-3", "0-3")
BO_MODES = {"all", "none", "elim_qual"}


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


def series_for_mode(bo3_mode: str) -> List[int]:
    return [3] if bo3_mode == "all" else [1] if bo3_mode == "none" else [1, 3]


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


def records(samples: SwissSamples, team_ids: List[int]) -> Dict[str, Dict[str, float]]:
    """P(each final record) per team over the samples."""
    import numpy as np

    out: Dict[str, Dict[str, float]] = {}
    for t, tid in enumerate(team_ids):
        w, p = samples.wins[:, t].astype(np.int64), samples.played[:, t].astype(np.int64)
        losses = p - w
        out[str(tid)] = {rec: float(np.mean((w == int(rec[0])) & (losses == int(rec[2])))) for rec in RECORDS}
    return out
