import json
import math
from collections import deque
from typing import Any


BOOSTER_POINT_VALUE = 5.0
DEFAULT_EXPECTED_MAPS = 2.4

BOOSTER_NAMES = {
    0: "Best Pistol Round",
    1: "Bottom of scoreboard",
    2: "Clutch",
    3: "Top of scoreboard",
    4: "Avenger",
    5: "Bait",
    6: "Rambo",
    7: "Flash",
    8: "Mister consistent",
    9: "Kobe",
    10: "Saver",
    11: "Assist",
    12: "Aim bot",
    13: "Quad",
    14: "Carry",
    15: "Cannon fodder",
    16: "Farmer",
    17: "Hellcase",
}

SWISS_SLOTS = [
    {"match_number": 1, "record": "0-0", "match_format": "BO1"},
    {"match_number": 2, "record": "1-0", "match_format": "BO1"},
    {"match_number": 2, "record": "0-1", "match_format": "BO1"},
    {"match_number": 3, "record": "2-0", "match_format": "BO3"},
    {"match_number": 3, "record": "1-1", "match_format": "BO1"},
    {"match_number": 3, "record": "0-2", "match_format": "BO3"},
    {"match_number": 4, "record": "2-1", "match_format": "BO3"},
    {"match_number": 4, "record": "1-2", "match_format": "BO3"},
    {"match_number": 5, "record": "2-2", "match_format": "BO3"},
]


def normalize_probability(value: Any) -> float:
    try:
        p = float(value)
    except Exception:
        return 0.0
    if not math.isfinite(p):
        return 0.0
    if p > 1.0:
        p = p / 100.0
    return max(0.0, min(1.0, p))


def parse_booster_rates(raw: Any) -> dict[int, float]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return {}
    rates = {}
    for idx in range(18):
        rates[idx] = normalize_probability(raw.get(str(idx), raw.get(idx, 0.0)))
    return rates


def adjusted_trigger_probability(p_bo3: float, match_format: str, expected_maps: float = DEFAULT_EXPECTED_MAPS) -> float:
    p = normalize_probability(p_bo3)
    if str(match_format).upper() != "BO1":
        return p
    maps = max(0.01, float(expected_maps or DEFAULT_EXPECTED_MAPS))
    return 1.0 - ((1.0 - p) ** (1.0 / maps))


def fallback_slot_probabilities(team_result: dict) -> dict[str, float]:
    final_probs = {rec: normalize_probability(team_result.get(rec, 0.0)) for rec in ("3-0", "3-1", "3-2", "2-3", "1-3", "0-3")}
    paths = {
        "3-0": ["WWW"],
        "3-1": ["WWLW", "WLWW", "LWWW"],
        "3-2": ["WWLLW", "WLWLW", "WLLWW", "LWWLW", "LWLWW", "LLWWW"],
        "2-3": ["WWLLL", "WLWLL", "WLLWL", "LWWLL", "LWLWL", "LLWWL"],
        "1-3": ["WLLL", "LWLL", "LLWL"],
        "0-3": ["LLL"],
    }
    out = {slot["record"]: 0.0 for slot in SWISS_SLOTS}
    for final_record, seqs in paths.items():
        prob = final_probs.get(final_record, 0.0)
        if prob <= 0.0 or not seqs:
            continue
        path_weight = prob / float(len(seqs))
        for seq in seqs:
            wins = 0
            losses = 0
            for match_number in range(1, len(seq) + 1):
                record = f"{wins}-{losses}"
                if record in out:
                    out[record] += path_weight
                if seq[match_number - 1] == "W":
                    wins += 1
                else:
                    losses += 1
    return out


def slot_probabilities_for_team(team_result: dict) -> dict[str, float]:
    raw = team_result.get("slot_probs")
    if isinstance(raw, dict):
        parsed = {slot["record"]: normalize_probability(raw.get(slot["record"], 0.0)) for slot in SWISS_SLOTS}
        if any(v > 0.0 for v in parsed.values()):
            return parsed
    return fallback_slot_probabilities(team_result)


class _Edge:
    __slots__ = ("to", "rev", "cap", "cost")

    def __init__(self, to: int, rev: int, cap: int, cost: float):
        self.to = to
        self.rev = rev
        self.cap = cap
        self.cost = cost


def _add_edge(graph: list[list[_Edge]], fr: int, to: int, cap: int, cost: float) -> None:
    graph[fr].append(_Edge(to, len(graph[to]), cap, cost))
    graph[to].append(_Edge(fr, len(graph[fr]) - 1, 0, -cost))


def _max_weight_assignment(weights: list[list[float]]) -> list[tuple[int, int, float]]:
    if not weights or not weights[0]:
        return []
    rows = len(weights)
    cols = len(weights[0])
    source = 0
    row_start = 1
    col_start = row_start + rows
    sink = col_start + cols
    graph = [[] for _ in range(sink + 1)]

    for r in range(rows):
        _add_edge(graph, source, row_start + r, 1, 0.0)
        for c in range(cols):
            _add_edge(graph, row_start + r, col_start + c, 1, -float(weights[r][c]))
    for c in range(cols):
        _add_edge(graph, col_start + c, sink, 1, 0.0)

    flow = 0
    while flow < rows:
        dist = [math.inf] * len(graph)
        in_queue = [False] * len(graph)
        prev_node = [-1] * len(graph)
        prev_edge = [-1] * len(graph)
        dist[source] = 0.0
        q = deque([source])
        in_queue[source] = True
        while q:
            v = q.popleft()
            in_queue[v] = False
            for i, edge in enumerate(graph[v]):
                if edge.cap <= 0:
                    continue
                nd = dist[v] + edge.cost
                if nd + 1e-12 < dist[edge.to]:
                    dist[edge.to] = nd
                    prev_node[edge.to] = v
                    prev_edge[edge.to] = i
                    if not in_queue[edge.to]:
                        q.append(edge.to)
                        in_queue[edge.to] = True
        if prev_node[sink] < 0:
            break
        v = sink
        while v != source:
            edge = graph[prev_node[v]][prev_edge[v]]
            edge.cap -= 1
            graph[v][edge.rev].cap += 1
            v = prev_node[v]
        flow += 1

    assignments = []
    for r in range(rows):
        node = row_start + r
        for edge in graph[node]:
            if col_start <= edge.to < col_start + cols and edge.cap == 0:
                c = edge.to - col_start
                assignments.append((r, c, float(weights[r][c])))
                break
    return assignments


def optimize_swiss_boosters_for_roster(
    players: list[dict],
    team_results_by_id: dict[int, dict],
    expected_maps: float = DEFAULT_EXPECTED_MAPS,
) -> dict:
    columns = []
    for player in players:
        pid = int(player["player_id"])
        team_id = int(player.get("team_id", 0))
        slot_probs = slot_probabilities_for_team(team_results_by_id.get(team_id, {}))
        for slot in SWISS_SLOTS:
            columns.append(
                {
                    "player_id": pid,
                    "player_name": player.get("name", f"Player {pid}"),
                    "match_number": int(slot["match_number"]),
                    "record": str(slot["record"]),
                    "match_format": str(slot["match_format"]),
                    "slot_probability": float(slot_probs.get(str(slot["record"]), 0.0)),
                }
            )

    player_rates = {int(p["player_id"]): parse_booster_rates(p.get("boosters_json")) for p in players}
    weights = []
    for booster_id in range(18):
        row = []
        for column in columns:
            p_bo3 = player_rates.get(int(column["player_id"]), {}).get(booster_id, 0.0)
            trigger = adjusted_trigger_probability(p_bo3, column["match_format"], expected_maps)
            row.append(float(column["slot_probability"]) * trigger * BOOSTER_POINT_VALUE)
        weights.append(row)

    assignments = []
    per_player = {int(p["player_id"]): 0.0 for p in players}
    for booster_id, column_idx, ev in _max_weight_assignment(weights):
        column = columns[column_idx]
        p_bo3 = player_rates.get(int(column["player_id"]), {}).get(booster_id, 0.0)
        trigger = adjusted_trigger_probability(p_bo3, column["match_format"], expected_maps)
        expected_points = float(ev)
        pid = int(column["player_id"])
        per_player[pid] = per_player.get(pid, 0.0) + expected_points
        assignments.append(
            {
                "booster_id": int(booster_id),
                "booster": BOOSTER_NAMES.get(int(booster_id), f"Booster {booster_id}"),
                "player_id": pid,
                "player": column["player_name"],
                "match_number": int(column["match_number"]),
                "record": column["record"],
                "match_format": column["match_format"],
                "slot_probability": float(column["slot_probability"]),
                "adjusted_trigger_probability": float(trigger),
                "expected_points": expected_points,
            }
        )

    total = sum(a["expected_points"] for a in assignments)
    assignments.sort(key=lambda a: a["expected_points"], reverse=True)
    return {
        "total_expected_booster_points": float(total),
        "average_expected_booster_points_per_player": float(total / max(1, len(players))),
        "per_player": per_player,
        "assignments": assignments,
    }
