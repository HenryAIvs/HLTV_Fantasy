import itertools

from fastapi import APIRouter, HTTPException
from player_db import get_player
from swiss_stage.fantasy_montecarlo import simulate_swiss_fantasy
from role_assignment import best_role_assignment_for_team, extract_role_scores_for_player

router = APIRouter()


@router.post("/")
def find_best_team(payload: dict):
    """
    Returns the top 10 teams of 5 based on simulation output and booster EV approximation.

    Expects the same payload as /simulate, plus:
      - budget (int, optional; default 1_000_000)
      - max_per_team (int, optional; default 2)
      - exclude_player_ids (list[int], optional)
    """
    required = ["team_ids", "vrs_ranks", "bo3_mode", "n_sims"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    team_ids = payload["team_ids"]
    if len(team_ids) < 2 or len(team_ids) % 2 != 0:
        raise HTTPException(status_code=400, detail="team_ids must contain an even number of teams (Swiss requires pairs).")

    budget = int(payload.get("budget", 1_000_000))
    max_per_team = int(payload.get("max_per_team", 2))
    exclude = set(payload.get("exclude_player_ids") or [])

    # Run simulation first
    results = simulate_swiss_fantasy(
        team_ids=team_ids,
        vrs_ranks=payload["vrs_ranks"],
        bo3_mode=payload["bo3_mode"],
        n_sims=int(payload["n_sims"]),
    )

    # Compute expected games per team
    def expected_games(team_result):
        p30 = team_result.get("3-0", 0.0)
        p31 = team_result.get("3-1", 0.0)
        p32 = team_result.get("3-2", 0.0)
        p23 = team_result.get("2-3", 0.0)
        p13 = team_result.get("1-3", 0.0)
        p03 = team_result.get("0-3", 0.0)
        p3 = p30 + p03
        p4 = p31 + p13
        p5 = p32 + p23
        return 3.0 * p3 + 4.0 * p4 + 5.0 * p5

    players_info = []
    expected_games_by_team = {tid: expected_games(res) for tid, res in results.items()}

    for tid, team_res in results.items():
        EG = expected_games_by_team.get(tid, 0.0)
        players_data = team_res.get("players", {})
        for pid_key, comps in players_data.items():
            pid = int(pid_key)
            if pid in exclude:
                continue
            row = get_player(pid)
            if not row:
                continue
            name = row.get("name", f"Player {pid}")
            price = row.get("price", 0)
            boosters_json = row.get("boosters_json", "")
            roles_json = row.get("roles_json", "")
            rating_ev = float(comps.get("rating", 0.0))
            win_ev = float(comps.get("win", 0.0))
            role_ev = float(comps.get("role", 0.0))

            # Approx booster EV = average triggerRate * 5 * expected games
            booster_ev = 0.0
            try:
                import json

                obj = json.loads(boosters_json) if boosters_json else {}
                if isinstance(obj, dict) and obj:
                    rates = []
                    for v in obj.values():
                        try:
                            rates.append(float(v))
                        except Exception:
                            continue
                    if rates:
                        booster_ev = 5.0 * (sum(rates) / len(rates)) * max(0.0, EG)
            except Exception:
                booster_ev = 0.0

            total_ev = rating_ev + win_ev + role_ev + booster_ev
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

    role_scores_by_player = {}
    best_role_score_by_player = {}
    for p in players_info:
        pid = p["player_id"]
        scores = extract_role_scores_for_player(get_player(pid) or {})
        role_scores_by_player[pid] = scores
        best_role_score_by_player[pid] = max(scores.values()) if scores else 0.0
        p["base_no_role_ev"] = p["total_ev"] - p["role_ev"]

    valid_teams = []
    players_info_sorted = sorted(players_info, key=lambda x: -x["total_ev"])

    for combo in itertools.combinations(players_info_sorted, 5):
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

        base_sum = sum(p["base_no_role_ev"] for p in combo)
        upper_role_sum = sum(best_role_score_by_player[p["player_id"]] for p in combo)
        upper_bound = base_sum + upper_role_sum

        assignment, role_score = best_role_assignment_for_team(
            [p["player_id"] for p in combo], role_scores_by_player
        )
        if assignment is None:
            continue

        total_ev = sum(p["total_ev"] for p in combo)
        valid_teams.append(
            {
                "total_ev": total_ev,
                "cost": total_cost,
                "players": combo,
                "assignment": assignment,
                "role_score": role_score,
            }
        )

    valid_teams.sort(key=lambda x: x["total_ev"], reverse=True)
    top_teams = valid_teams[:10]

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
                    "role_name": str(entry["assignment"].get(p["player_id"], "-")),
                }
                for p in entry["players"]
            ],
        }

    return {"top_teams": [serialize_team(t) for t in top_teams], "player_count": len(players_info)}
