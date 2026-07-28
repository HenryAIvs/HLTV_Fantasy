from typing import Dict, List, Tuple

from fastapi import APIRouter, HTTPException
from backend.swiss_stage.team_initialization import initialize_teams
from backend.swiss_stage.pairing import buchholz_score, generate_pairings
from backend.swiss_stage.swiss_bracket import simulate_single_swiss_run
from backend.swiss_stage.swiss_models import TeamState


router = APIRouter()


def _normalize_vrs_map(vrs_ranks: dict) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for k, v in (vrs_ranks or {}).items():
        try:
            out[int(k)] = int(v)
        except Exception:
            continue
    return out


def _build_manual_team_states(team_ids: List[int], vrs_ranks: Dict[int, int]) -> Dict[int, TeamState]:
    team_states: Dict[int, TeamState] = {}
    for tid in team_ids:
        team_states[int(tid)] = TeamState(
            team_id=int(tid),
            vrs_rank=int(vrs_ranks.get(int(tid), 999)),
            players={},
        )
    return team_states


def _serialize_manual_team_states(team_states: Dict[int, TeamState]) -> List[Dict]:
    items: List[Dict] = []
    for tid in sorted(team_states.keys()):
        t = team_states[tid]
        items.append(
            {
                "team_id": t.team_id,
                "vrs_rank": t.vrs_rank,
                "wins": t.wins,
                "losses": t.losses,
                "opponents_played": sorted(list(t.opponents_played)),
            }
        )
    return items


def _deserialize_manual_team_states(serialized: List[Dict]) -> Dict[int, TeamState]:
    team_states: Dict[int, TeamState] = {}
    for item in serialized:
        tid = int(item["team_id"])
        t = TeamState(
            team_id=tid,
            vrs_rank=int(item.get("vrs_rank", 999)),
            players={},
            wins=int(item.get("wins", 0)),
            losses=int(item.get("losses", 0)),
            opponents_played=set(int(x) for x in (item.get("opponents_played") or [])),
        )
        team_states[tid] = t
    return team_states


def _build_manual_round_view(team_states: Dict[int, TeamState]) -> Tuple[bool, int, List[Dict], List[Dict]]:
    active = [t for t in team_states.values() if not t.qualified and not t.eliminated]
    done = len(active) == 0
    round_no = (max((t.matches_played for t in team_states.values()), default=0) + 1) if not done else max(
        (t.matches_played for t in team_states.values()), default=0
    )

    pools: Dict[Tuple[int, int], List[TeamState]] = {}
    for t in active:
        pools.setdefault((t.wins, t.losses), []).append(t)

    pool_items: List[Dict] = []
    for key in sorted(pools.keys(), key=lambda x: (x[0], x[1]), reverse=True):
        pool = pools[key]
        pairings = generate_pairings(pool, team_states)
        matches = []
        for idx, (a, b) in enumerate(pairings):
            matches.append(
                {
                    "match_id": f"{key[0]}-{key[1]}:{idx}",
                    "team_a_id": a.team_id,
                    "team_b_id": b.team_id,
                    "record": f"{key[0]}-{key[1]}",
                    "team_a_buchholz": buchholz_score(a, team_states),
                    "team_b_buchholz": buchholz_score(b, team_states),
                }
            )
        pool_items.append({"record": f"{key[0]}-{key[1]}", "matches": matches})

    standings = [
        {
            "team_id": t.team_id,
            "wins": t.wins,
            "losses": t.losses,
            "qualified": t.qualified,
            "eliminated": t.eliminated,
            "vrs_rank": t.vrs_rank,
            "buchholz": buchholz_score(t, team_states),
        }
        for t in sorted(team_states.values(), key=lambda x: (-x.wins, x.losses, -buchholz_score(x, team_states), x.vrs_rank))
    ]
    return done, round_no, pool_items, standings


@router.post("/swiss-run")
def swiss_run(payload: dict):
    """
    Run a single Swiss bracket and return the final TeamState objects.
    Expects:
      - team_ids: list[int]
      - vrs_ranks: dict[int, int]
      - bo3_mode: str
    """
    required = ["team_ids", "vrs_ranks", "bo3_mode"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    team_ids = payload["team_ids"]
    vrs_ranks = payload["vrs_ranks"]
    bo3_mode = "elim_qual"

    team_states = simulate_single_swiss_run(
        team_ids=team_ids,
        vrs_ranks=vrs_ranks,
        bo3_mode=bo3_mode,
        initialize_teams=initialize_teams,
    )

    # Serialize dataclasses to dicts for JSON response
    serialized = {}
    for tid, ts in team_states.items():
        serialized[tid] = {
            "team_id": ts.team_id,
            "wins": ts.wins,
            "losses": ts.losses,
            "qualified": ts.qualified,
            "eliminated": ts.eliminated,
            "players": {
                pid: {
                    "total_points": p.total_points,
                    "rating_points_total": p.rating_points_total,
                    "win_points_total": p.win_points_total,
                    "role_points_total": p.role_points_total,
                    "booster_points_total": p.booster_points_total,
                }
                for pid, p in ts.players.items()
            },
        }
    return serialized


@router.post("/swiss-manual/init")
def swiss_manual_init(payload: dict):
    required = ["team_ids", "vrs_ranks"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    team_ids = [int(x) for x in payload.get("team_ids", [])]
    if len(team_ids) < 2 or len(team_ids) % 2 != 0:
        raise HTTPException(status_code=400, detail="team_ids must contain an even number of teams (>=2).")

    vrs_ranks = _normalize_vrs_map(payload.get("vrs_ranks", {}))
    team_states = _build_manual_team_states(team_ids, vrs_ranks)
    done, round_no, pools, standings = _build_manual_round_view(team_states)
    return {
        "done": done,
        "round": round_no,
        "pools": pools,
        "standings": standings,
        "team_states": _serialize_manual_team_states(team_states),
    }


@router.post("/swiss-manual/apply-round")
def swiss_manual_apply_round(payload: dict):
    required = ["team_states", "results"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    team_states = _deserialize_manual_team_states(payload.get("team_states", []))
    results = payload.get("results") or []
    if not results:
        raise HTTPException(status_code=400, detail="results is required and cannot be empty.")

    for r in results:
        a_id = int(r.get("team_a_id"))
        b_id = int(r.get("team_b_id"))
        w_id = int(r.get("winner_id"))
        if a_id not in team_states or b_id not in team_states:
            raise HTTPException(status_code=400, detail=f"Unknown team in result: {a_id} vs {b_id}")
        if w_id not in (a_id, b_id):
            raise HTTPException(status_code=400, detail=f"winner_id must be one of {a_id}, {b_id}")

        a = team_states[a_id]
        b = team_states[b_id]
        if a.qualified or a.eliminated or b.qualified or b.eliminated:
            raise HTTPException(status_code=400, detail=f"Cannot apply result for completed team: {a_id} vs {b_id}")

        if w_id == a_id:
            a.record_win(b_id)
            b.record_loss(a_id)
        else:
            b.record_win(a_id)
            a.record_loss(b_id)

    done, round_no, pools, standings = _build_manual_round_view(team_states)
    return {
        "done": done,
        "round": round_no,
        "pools": pools,
        "standings": standings,
        "team_states": _serialize_manual_team_states(team_states),
    }
