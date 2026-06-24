# swiss_stage/pairing.py

from typing import List, Tuple, Dict, Mapping
from swiss_stage.swiss_models import TeamState


def buchholz_score(team: TeamState, all_teams: Mapping[int, TeamState] | None = None) -> int:
    """
    Sum of current match wins earned by opponents this team has already faced.
    """
    lookup = all_teams or {}
    total = 0
    for opponent_id in team.opponents_played:
        opponent = lookup.get(int(opponent_id))
        if opponent is not None:
            total += int(opponent.wins)
    return total


def _pairing_strength_key(team: TeamState, all_teams: Mapping[int, TeamState] | None = None) -> Tuple[int, int, int]:
    # Higher Buchholz is stronger. Lower VRS rank is stronger. Team id keeps ordering stable.
    return (-buchholz_score(team, all_teams), int(team.vrs_rank), int(team.team_id))


def generate_pairings(
    pool: List[TeamState],
    all_teams: Mapping[int, TeamState] | None = None,
) -> List[Tuple[TeamState, TeamState]]:
    """
    Generate pairings for a Swiss pool.

    Behaviour:
      - In the very first round (all teams 0-0):
          use 'seeded' first-round pairings:
              sort by vrs_rank ascending, then pair
                [0] vs [N/2], [1] vs [N/2+1], etc.

      - In all subsequent rounds:
          use a backtracking matcher that:
            * sorts teams by Buchholz descending, then vrs_rank ascending
            * attempts to pair without *any* rematches (based on opponents_played)
            * prefers strongest vs weakest inside the same record pool
            * if avoiding rematches is impossible, falls back to
              strongest vs weakest pairings.

    This should fix cases where the old avoid_rematches logic stopped
    one rematch but allowed another.
    """

    # Initial seed order: lower VRS rank is better
    pool_sorted = sorted(pool, key=lambda t: t.vrs_rank)
    n = len(pool_sorted)
    if n % 2 != 0:
        # caller should handle odd-pool warning; pairing here assumes even
        raise ValueError("Pool size must be even for pairing.")

    # Detect "first round": everyone is 0-0
    is_first_round = all(t.wins == 0 and t.losses == 0 for t in pool_sorted)

    if is_first_round:
        # First round: classic Swiss seeding: 1 vs 1+N/2, 2 vs 2+N/2, ...
        half = n // 2
        base_pairs = []
        for i in range(half):
            A = pool_sorted[i]
            B = pool_sorted[i + half]
            base_pairs.append((A, B))
        return base_pairs

    # For non-first rounds, try to find a full Buchholz matching with no rematches.

    teams = sorted(pool, key=lambda t: _pairing_strength_key(t, all_teams))

    # Backtracking search for pairings with no rematches
    best_pairs: List[Tuple[TeamState, TeamState]] = []

    def backtrack(remaining: List[TeamState], acc: List[Tuple[TeamState, TeamState]]) -> bool:
        """
        Try to build a full set of pairings with no rematches.
        Returns True if successful and fills best_pairs.
        """
        nonlocal best_pairs

        if not remaining:
            best_pairs = acc[:]
            return True

        # pick the strongest remaining team by Buchholz, then seed
        t0 = remaining[0]
        rest = remaining[1:]

        # candidate partners, in preferred order:
        #   try weakest remaining first (strongest vs weakest), then upwards
        indices = list(range(len(rest) - 1, -1, -1))  # from last to first
        for idx in indices:
            t1 = rest[idx]
            # skip if they've already played
            if t1.team_id in t0.opponents_played:
                continue

            # build new remaining list without t0 and t1
            new_remaining = rest[:idx] + rest[idx + 1:]
            if backtrack(new_remaining, acc + [(t0, t1)]):
                return True

        # no valid partner for t0 without rematch
        return False

    if backtrack(teams, []):
        # Found a rematch-free pairing set
        return best_pairs

    # If we couldn't avoid rematches entirely, fall back to Buchholz strongest vs weakest.
    fallback_pairs: List[Tuple[TeamState, TeamState]] = []
    for i in range(n // 2):
        A = teams[i]
        B = teams[n - 1 - i]
        fallback_pairs.append((A, B))

    return fallback_pairs
