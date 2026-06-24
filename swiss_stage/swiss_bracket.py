# swiss_stage/swiss_bracket.py

from typing import Dict, List

from swiss_stage.swiss_models import TeamState, PlayerState
from swiss_stage.swiss_round import run_round
from swiss_stage.fantasy_scoring import (
    compute_padding_components,
    compute_elimination_penalty_components,
)


def simulate_single_swiss_run(
    team_ids: List[int],
    vrs_ranks: Dict[int, int],
    bo3_mode: str,
    initialize_teams,
    slot_counts: Dict[int, Dict[str, int]] | None = None,
) -> Dict[int, TeamState]:
    """
    Runs one full Swiss tournament and returns final TeamState objects.

    initialize_teams(team_ids, vrs_ranks) must return:
      { team_id: TeamState }
    """

    # Create initial team states
    team_states: Dict[int, TeamState] = initialize_teams(team_ids, vrs_ranks)

    # Run rounds until all teams are done
    while True:
        active = [
            t for t in team_states.values()
            if not t.qualified and not t.eliminated
        ]
        if not active:
            break

        if slot_counts is not None:
            for t in active:
                record = f"{t.wins}-{t.losses}"
                slot_counts.setdefault(t.team_id, {})
                slot_counts[t.team_id][record] = slot_counts[t.team_id].get(record, 0) + 1

        run_round(team_states, bo3_mode)

    # Apply padding / penalties for teams that didn't play 5 matches
    for t in team_states.values():
        played = t.matches_played
        missing = max(0, 5 - played)

        if missing <= 0:
            continue

        if t.qualified:
            # Qualified early: padding for each missing match
            for p in t.players.values():
                comps = compute_padding_components(p)
                # apply once per missing match
                p.rating_points_total += comps["rating"] * missing
                p.role_points_total += comps["role"] * missing
                p.win_points_total += comps["win"] * missing
                p.booster_points_total += comps["booster"] * missing
                p.total_points += (
                    comps["rating"] + comps["role"] + comps["win"] + comps["booster"]
                ) * missing

        elif t.eliminated:
            # Eliminated early: penalty for all missing matches
            for p in t.players.values():
                comps = compute_elimination_penalty_components(missing)
                p.rating_points_total += comps["rating"]
                p.role_points_total += comps["role"]
                p.win_points_total += comps["win"]
                p.booster_points_total += comps["booster"]
                p.total_points += (
                    comps["rating"] + comps["role"] + comps["win"] + comps["booster"]
                )

    return team_states
