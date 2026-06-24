import itertools
import math
from typing import Callable, Iterable

from backend.data.player_db import get_player
from backend.services.role_assignment import best_role_assignment_for_team, extract_role_scores_for_player


ProgressCallback = Callable[[int, int], None]


def parse_optimizer_payload(payload: dict | None) -> dict:
    body = payload or {}
    return {
        "budget": int(body.get("budget", 1_000_000)),
        "max_per_team": int(body.get("max_per_team", 2)),
        "include": {int(pid) for pid in (body.get("include_player_ids") or [])},
        "exclude": {int(pid) for pid in (body.get("exclude_player_ids") or [])},
    }


def serialize_roster_player(player: dict, role_name: str, booster_ev: float | None = None) -> dict:
    pid = int(player["player_id"])
    player_booster_ev = float(player.get("booster_ev", 0.0) if booster_ev is None else booster_ev)
    return {
        "player_id": pid,
        "name": player.get("name", f"Player {pid}"),
        "team_id": int(player.get("team_id", 0)),
        "price": int(player.get("price", 0)),
        "rating_ev": float(player.get("rating_ev", 0.0)),
        "win_ev": float(player.get("win_ev", 0.0)),
        "role_ev": float(player.get("role_ev", 0.0)),
        "booster_ev": player_booster_ev,
        "total_ev": float(player.get("rating_ev", 0.0)) + float(player.get("win_ev", 0.0)) + float(player.get("role_ev", 0.0)) + player_booster_ev,
        "role_name": str(role_name),
        "components_available": bool(player.get("components_available", True)),
    }


def serialize_roster(
    players_by_id: dict[str, dict],
    pids: Iterable[int],
    roles: Iterable[str],
    total_ev: float,
    cost: int,
    booster_assignments: list[dict] | None = None,
) -> dict:
    per_player_booster = {}
    for assignment in booster_assignments or []:
        pid = int(assignment.get("player_id", 0))
        per_player_booster[pid] = per_player_booster.get(pid, 0.0) + float(assignment.get("expected_points", 0.0))
    players = []
    for pid, role_name in zip(pids, roles):
        meta = players_by_id.get(str(pid), {})
        players.append(serialize_roster_player(meta, str(role_name), per_player_booster.get(int(pid))))
    return {
        "total_ev": float(total_ev),
        "cost": int(cost),
        "players": players,
        "booster_assignments": booster_assignments or [],
        "booster_ev": sum(float(a.get("expected_points", 0.0)) for a in (booster_assignments or [])),
    }


def iter_valid_rosters(
    players_info: list[dict],
    include: set[int],
    budget: int,
    max_per_team: int,
    progress_callback: ProgressCallback | None = None,
):
    if len(players_info) < 5:
        return

    players_sorted = sorted(players_info, key=lambda p: -float(p.get("total_ev", 0.0)))
    total_combinations = math.comb(len(players_sorted), 5)
    processed = 0
    if progress_callback:
        progress_callback(0, total_combinations)

    role_scores_by_player = {
        int(player["player_id"]): extract_role_scores_for_player(get_player(int(player["player_id"])) or {})
        for player in players_sorted
    }

    for combo in itertools.combinations(players_sorted, 5):
        processed += 1
        if progress_callback and (processed % 1000 == 0 or processed == total_combinations):
            progress_callback(processed, total_combinations)

        combo_ids = [int(player["player_id"]) for player in combo]
        if include and not include.issubset(set(combo_ids)):
            continue

        total_cost = sum(int(player.get("price", 0)) for player in combo)
        if total_cost > budget:
            continue

        team_counts = {}
        for player in combo:
            team_id = int(player.get("team_id", 0))
            team_counts[team_id] = team_counts.get(team_id, 0) + 1
            if team_counts[team_id] > max_per_team:
                break
        else:
            assignment, _ = best_role_assignment_for_team(combo_ids, role_scores_by_player)
            if assignment is None:
                continue
            role_names = [str(assignment.get(pid, "-")) for pid in combo_ids]
            yield {
                "players": combo,
                "pids": combo_ids,
                "roles": role_names,
                "cost": int(total_cost),
                "total_ev": float(sum(float(player.get("total_ev", 0.0)) for player in combo)),
                "processed": int(total_combinations),
                "total_combinations": int(total_combinations),
            }


def optimize_rosters(
    players_info: list[dict],
    include: set[int],
    budget: int,
    max_per_team: int,
    progress_callback: ProgressCallback | None = None,
    include_error_suffix: str = "",
) -> dict:
    if len(players_info) < 5:
        return {"error": "Not enough players after exclusions"}

    available_ids = {int(player["player_id"]) for player in players_info}
    missing_includes = [pid for pid in include if pid not in available_ids]
    if missing_includes:
        suffix = f" {include_error_suffix}" if include_error_suffix else ""
        return {"error": f"Included players not available{suffix}: {missing_includes}"}

    players_meta = {str(player["player_id"]): player for player in players_info}
    total_combinations = math.comb(len(players_info), 5)
    processed = 0
    valid_teams = []
    for roster in iter_valid_rosters(players_info, include, budget, max_per_team, progress_callback):
        processed = int(roster["processed"])
        valid_teams.append(
            serialize_roster(players_meta, roster["pids"], roster["roles"], roster["total_ev"], roster["cost"])
        )

    valid_teams.sort(key=lambda team: team["total_ev"], reverse=True)
    return {
        "top_teams": valid_teams[:10],
        "all_teams": valid_teams,
        "player_count": len(players_info),
        "processed_combinations": int(processed or total_combinations),
        "total_combinations": int(total_combinations),
    }
