"""A player's runs through an event over a set of outcomes.

A run is the path the player's team takes — which stages it plays and
whether it wins each — plus the paddings credited and the elimination
penalty taken. Over a conditioned outcome set (the outcomes a roster wins,
its single best one) the team's possible runs collapse to a handful, so
listing them with their share is the clear view: the shares sum to one and
the share-weighted totals are the player's expected points.

Outcomes with the same path are one run; within it each match keeps the
distribution of opponents met there (the most common one fronts the chip)
and the player's points averaged over them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PARTS = ("rating", "win", "role", "booster")
KEYS = (*PARTS, "total")


def run_totals(matches: List[dict], pads: List[dict], penalty: float) -> Dict[str, float]:
    totals = {k: 0.0 for k in PARTS}
    for m in matches:
        for k in PARTS:
            totals[k] += float(m.get(k) or 0.0)
    for p in pads:
        for k in PARTS:
            totals[k] += float(p.get(k) or 0.0)
    totals["win"] += float(penalty or 0.0)
    totals["total"] = sum(totals[k] for k in PARTS)
    return totals


def add_run(run_map: Dict[tuple, Dict[str, Any]], matches: List[dict], pads: List[dict], penalty: float, weight: float) -> None:
    """Tally one outcome's run (its matches with opponent, result and points;
    its paddings; its penalty) with its weight."""
    if weight <= 0.0:
        return
    sig = (tuple((str(m.get("stage")), bool(m.get("won"))) for m in matches), tuple(str(p.get("stage")) for p in pads))
    run = run_map.get(sig)
    if run is None:
        run = run_map[sig] = {
            "weight": 0.0,
            "matches": [
                {
                    "stage": m.get("stage"), "label": m.get("label"), "full": m.get("full"), "slot": m.get("slot"),
                    "won": bool(m.get("won")), "opps": {}, "boosters": {}, "sums": {k: 0.0 for k in KEYS},
                }
                for m in matches
            ],
            "pads": [{"stage": p.get("stage"), "label": p.get("label"), "sums": {k: 0.0 for k in KEYS}} for p in pads],
            "penalty_sum": 0.0,
            "sums": {k: 0.0 for k in KEYS},
        }
    run["weight"] += weight
    for rm, m in zip(run["matches"], matches):
        opp = int(m.get("opponent_team_id") or 0)
        rm["opps"][opp] = rm["opps"].get(opp, 0.0) + weight
        bname = m.get("booster_name")
        if bname:
            rm["boosters"][bname] = rm["boosters"].get(bname, 0.0) + weight
        for k in KEYS:
            rm["sums"][k] += weight * float(m.get(k) or 0.0)
    for rp, p in zip(run["pads"], pads):
        for k in KEYS:
            rp["sums"][k] += weight * float(p.get(k) or 0.0)
    run["penalty_sum"] += weight * float(penalty or 0.0)
    for k, v in run_totals(matches, pads, penalty).items():
        run["sums"][k] += weight * v


def finish_runs(run_map: Dict[tuple, Dict[str, Any]], mass: float, cap: int = 24) -> Dict[str, Any]:
    """The runs most common first, each with its share of `mass`, its matches
    (opponent distribution, weight-averaged points), paddings, penalty and
    totals; beyond `cap` rows the rest is one aggregate."""
    rows: List[Dict[str, Any]] = []
    for run in run_map.values():
        w = float(run["weight"])
        if w <= 0.0:
            continue
        matches = []
        for rm in run["matches"]:
            opps = sorted(rm["opps"].items(), key=lambda kv: -kv[1])
            boosters = sorted(rm["boosters"].items(), key=lambda kv: -kv[1])
            matches.append({
                "stage": rm["stage"], "label": rm["label"], "full": rm["full"], "slot": rm["slot"], "won": rm["won"],
                "opponent_team_id": opps[0][0] if opps else 0,
                "opponent_share": (opps[0][1] / w) if opps else 0.0,
                "opponents": [{"team_id": o, "share": ow / w} for o, ow in opps[:4]],
                "booster_name": boosters[0][0] if boosters else None,
                **{k: v / w for k, v in rm["sums"].items()},
            })
        pads = [{"stage": rp["stage"], "label": rp["label"], **{k: v / w for k, v in rp["sums"].items()}} for rp in run["pads"]]
        rows.append({
            "share": w / mass if mass > 0 else 0.0,
            "matches": matches,
            "pads": pads,
            "penalty": run["penalty_sum"] / w,
            "totals": {k: v / w for k, v in run["sums"].items()},
        })
    rows.sort(key=lambda r: -r["share"])
    head, tail = rows[:cap], rows[cap:]
    rest: Optional[Dict[str, Any]] = None
    if tail:
        ws = sum(r["share"] for r in tail)
        rest = {
            "count": len(tail),
            "share": ws,
            "totals": {k: (sum(r["share"] * r["totals"][k] for r in tail) / ws if ws > 0 else 0.0) for k in KEYS},
        }
    return {"runs": head, "rest": rest}
