from backend.swiss_stage.swiss_models import TeamState
from backend.swiss_stage.player_loading import load_team_players

def initialize_teams(team_ids, vrs_ranks):
    """
    Creates {team_id: TeamState}
    """

    teams = {}

    for tid in team_ids:
        players = load_team_players(tid)
        teams[tid] = TeamState(
            team_id=tid,
            vrs_rank=vrs_ranks.get(tid, 999),
            players=players,
        )

    return teams
