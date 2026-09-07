"""Exact single-elimination playoff expectations over uncertain qualifiers.

Inputs: the groups (each a list of team ids plus a probability for every
ordered qualifier tuple), a bracket template (which (group, rank) slot feeds
which match; a slot entering a later round is a bye) and win probabilities.

Output: for every match and every pair of teams, the probability that they
meet there as (left, right) — the "pairing weights" the scorer needs — plus
each team's chance of winning a root match and the bye-slot marginals.

Nothing is enumerated per seeding. Every sub-bracket gets a winner table: a
numpy array with one axis per slot in it (the axis runs over that slot's
group's teams) and a last axis saying which slot's team wins. For a match,
the right feeder's table is folded group by group with the group tuple
distributions restricted to the slots involved (a matrix product per group),
which turns it into a table over the LEFT feeder's slots; a pairing weight is
then one batched matrix-vector product against the left table. Groups with
slots on both sides are exactly what couple the sides, and the fold sums
over their tuples, so the final of a 16-team bracket fed by four groups of
eight costs a few matrix products instead of ~10^13 seedings, and a match
that only depends on a few slots is computed once.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

_LETTERS = string.ascii_letters
# Largest winner table (elements) we are willing to hold for one sub-bracket:
# 8 slots of 8 teams × 8 winners = 134M doubles ≈ 1 GB (the 16-team, four
# groups of eight case). Beyond that the shape needs a different algorithm.
MAX_TABLE_ELEMS = 2 ** 27 + 1


class GroupSpec:
    """One group: its teams (slot axes index into this list) and the joint
    distribution over ordered qualifier tuples as a dense array."""

    def __init__(self, teams: Sequence[int], quals: int, tuple_probs: Dict[tuple, float]):
        self.teams = [int(t) for t in teams]
        self.n = len(self.teams)
        self.quals = int(quals)
        index = {tid: i for i, tid in enumerate(self.teams)}
        P = np.zeros((self.n,) * self.quals, dtype=np.float64)
        for tup, p in tuple_probs.items():
            P[tuple(index[int(t)] for t in tup)] += float(p)
        self.P = P

    def marginal(self, ranks: Sequence[int]) -> np.ndarray:
        """P summed over every rank not listed, axes ordered as `ranks`."""
        keep = list(ranks)
        drop = tuple(r for r in range(self.quals) if r not in keep)
        M = self.P.sum(axis=drop) if drop else self.P
        ascending = sorted(keep)
        perm = [ascending.index(r) for r in keep]
        return np.transpose(M, perm) if perm != list(range(len(keep))) else M


@dataclass(frozen=True)
class Slot:
    group: int
    rank: int
    entry_round: int = 0  # round index this seed enters (byes enter later)


@dataclass
class Match:
    left: Any  # Slot | Match
    right: Any
    round: int


def build_tree(rounds: List[List[tuple]], entry_round: Dict[tuple, int]) -> List[Match]:
    """Template → match nodes. Feeders are ("seed", g, rank) or ("win", r, m).
    Returns the root matches (the last round's matches)."""
    nodes: List[List[Match]] = []
    for r, matches in enumerate(rounds):
        row: List[Match] = []
        for fa, fb in matches:
            feeders = []
            for f in (fa, fb):
                if f[0] == "seed":
                    feeders.append(Slot(int(f[1]), int(f[2]), int(entry_round.get((f[1], f[2]), r))))
                else:
                    feeders.append(nodes[f[1]][f[2]])
            row.append(Match(feeders[0], feeders[1], r))
        nodes.append(row)
    return nodes[-1]


def _move_axis_front(arr: np.ndarray, axis: int) -> np.ndarray:
    return np.moveaxis(arr, axis, 0) if axis != 0 else arr


def _pair_weights(
    specs: List[GroupSpec],
    left_slots: List[Slot],
    FL: np.ndarray,
    right_slots: List[Slot],
    FR: np.ndarray,
) -> Dict[Tuple[int, int], np.ndarray]:
    """W[(xi, yi)][i, j] = P(team i of left slot xi meets team j of right slot yi
    in this match). Right table folded group by group into left-slot axes."""
    groups_present: List[int] = []
    for s in left_slots + right_slots:
        if s.group not in groups_present:
            groups_present.append(s.group)
    gL = {g: [s for s in left_slots if s.group == g] for g in groups_present}
    gR = {g: [s for s in right_slots if s.group == g] for g in groups_present}
    # Marginal per group over the ranks that appear in this match, axes = its
    # left slots then its right slots.
    marg = {g: specs[g].marginal([s.rank for s in gL[g]] + [s.rank for s in gR[g]]) for g in groups_present}

    # Groups with slots only on the left never enter the fold: their marginal
    # simply weights the left table over those slots' axes.
    left_only = [g for g in groups_present if not gR[g]]
    if left_only:
        lo_slots = [s for s in left_slots if s.group in left_only]
        lt = {s: _LETTERS[i] for i, s in enumerate(lo_slots)}
        subs = ["".join(lt[s] for s in gL[g]) for g in left_only]
        weight_small = np.einsum(",".join(subs) + "->" + "".join(lt[s] for s in lo_slots), *[marg[g] for g in left_only], optimize="optimal")
        lo_set = set(lo_slots)
        shape = [specs[s.group].n if s in lo_set else 1 for s in left_slots]
        FLw = FL * weight_small.reshape(shape)[..., None]
    else:
        FLw = FL

    out: Dict[Tuple[int, int], np.ndarray] = {}
    for yi, y in enumerate(right_slots):
        T = FR[..., yi]
        T_slots: List[Slot] = list(right_slots)
        for g in groups_present:
            if not gR[g]:
                continue
            M = marg[g]  # axes: gL[g] + gR[g]
            m_slots = gL[g] + gR[g]
            r_slots_g = [s for s in gR[g] if s != y]
            if y in gR[g]:
                # Keep y's axis (shared between T and M): a batched contraction.
                lt = {s: _LETTERS[i] for i, s in enumerate(T_slots)}
                extra = {}
                for s in m_slots:
                    if s not in lt:
                        extra[s] = _LETTERS[len(lt) + len(extra)]
                sub_T = "".join(lt[s] for s in T_slots)
                sub_M = "".join(lt.get(s) or extra[s] for s in m_slots)
                keep = [s for s in T_slots if s not in r_slots_g] + gL[g]
                sub_out = "".join(lt.get(s) or extra[s] for s in keep)
                T = np.einsum(f"{sub_T},{sub_M}->{sub_out}", T, M, optimize="optimal")
                T_slots = keep
            else:
                t_axes = [T_slots.index(s) for s in gR[g]]
                m_axes = [m_slots.index(s) for s in gR[g]]
                T = np.tensordot(T, M, axes=(t_axes, m_axes))
                T_slots = [s for s in T_slots if s not in gR[g]] + gL[g]
        # T now spans the left slots of every shared group, plus y. Left-only
        # groups were folded into FLw instead, so broadcast T over their axes,
        # then align everything to (left order..., y).
        for s in left_slots:
            if s not in T_slots:
                T = T[None, ...]
                T_slots = [s] + T_slots
        target = list(left_slots) + [y]
        if set(T_slots) != set(target):
            raise RuntimeError(f"fold left unexpected axes {T_slots} (wanted {target})")
        T = np.transpose(T, [T_slots.index(s) for s in target])
        T = np.broadcast_to(T, [specs[s.group].n for s in target])
        n_y = specs[y.group].n
        for xi, x in enumerate(left_slots):
            A = _move_axis_front(FLw[..., xi], xi)  # (n_x, rest...)
            B = _move_axis_front(T, xi)             # (n_x, rest..., n_y)
            n_x = A.shape[0]
            W = np.einsum("am,ami->ai", A.reshape(n_x, -1), B.reshape(n_x, -1, n_y))
            out[(xi, yi)] = W
    return out


def compute_pairing_weights(
    specs: List[GroupSpec],
    roots: List[Match],
    rounds_total: int,
    pwin: Callable[[int, int], np.ndarray],
    max_table_elems: int = MAX_TABLE_ELEMS,
    progress: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict[tuple, float], Dict[int, float], Dict[int, float]]:
    """Returns (weights, advance, bye_weight):
    weights[(a, b, winner, match_num_a, match_num_b, rounds_remaining)] = P(that
    match happens with that result); advance[team] = P(team wins a root match);
    bye_weight[team] = P(team occupies a bye slot).
    pwin(g, h) -> array (n_g, n_h): P(team i of group g beats team j of h)."""
    weights: Dict[tuple, float] = {}
    advance: Dict[int, float] = {}
    bye_weight: Dict[int, float] = {}

    def add_w(key: tuple, value: float) -> None:
        if value > 0.0:
            weights[key] = weights.get(key, 0.0) + value

    def evaluate(node: Any, is_root: bool) -> Tuple[List[Slot], Optional[np.ndarray]]:
        if isinstance(node, Slot):
            if node.entry_round > 0:
                m = specs[node.group].marginal([node.rank])
                for i, tid in enumerate(specs[node.group].teams):
                    if m[i] > 0:
                        bye_weight[tid] = bye_weight.get(tid, 0.0) + float(m[i])
            return [node], np.ones((specs[node.group].n, 1), dtype=np.float64)

        left_slots, FL = evaluate(node.left, False)
        right_slots, FR = evaluate(node.right, False)
        slots = left_slots + right_slots
        r = node.round
        rem = rounds_total - r - 1
        if progress:
            progress(f"round {r + 1}: match over {len(slots)} slots")

        pair_w = _pair_weights(specs, left_slots, FL, right_slots, FR)
        for (xi, yi), W in pair_w.items():
            x, y = left_slots[xi], right_slots[yi]
            pm = pwin(x.group, y.group)
            num_x = r - x.entry_round + 1
            num_y = r - y.entry_round + 1
            for i, j in np.argwhere(W > 0.0):
                w = float(W[i, j])
                a = specs[x.group].teams[i]
                b = specs[y.group].teams[j]
                p = float(pm[i, j])
                add_w((a, b, a, num_x, num_y, rem), w * p)
                add_w((a, b, b, num_x, num_y, rem), w * (1.0 - p))
                if is_root:
                    advance[a] = advance.get(a, 0.0) + w * p
                    advance[b] = advance.get(b, 0.0) + w * (1.0 - p)
        if is_root:
            return slots, None

        # Winner table for this sub-bracket (the parent contracts it).
        shape = [specs[s.group].n for s in slots]
        elems = int(np.prod(shape)) * len(slots)
        if elems > max_table_elems:
            raise ValueError(
                f"Exact playoff table too large for this bracket shape ({elems:,} elements for a "
                f"{len(slots)}-slot sub-bracket); this shape needs a different algorithm."
            )
        letter = {s: _LETTERS[i] for i, s in enumerate(slots)}
        sub_L = "".join(letter[s] for s in left_slots)
        sub_R = "".join(letter[s] for s in right_slots)
        all_sub = "".join(letter[s] for s in slots)
        F = np.zeros(shape + [len(slots)], dtype=np.float64)
        for xi, x in enumerate(left_slots):
            for yi, y in enumerate(right_slots):
                pm = pwin(x.group, y.group)
                base = f"{sub_L},{sub_R},{letter[x]}{letter[y]}->{all_sub}"
                F[..., xi] += np.einsum(base, FL[..., xi], FR[..., yi], pm, optimize="optimal")
                F[..., len(left_slots) + yi] += np.einsum(base, FL[..., xi], FR[..., yi], 1.0 - pm, optimize="optimal")
        return slots, F

    for root in roots:
        evaluate(root, True)
    return weights, advance, bye_weight
