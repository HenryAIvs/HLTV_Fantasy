# swiss_stage/pairing.py

from typing import List, Tuple, Dict
from swiss_stage.swiss_models import TeamState


def generate_pairings(pool: List[TeamState]) -> List[Tuple[TeamState, TeamState]]:
    """
    Generate pairings for a Swiss pool.

    Behaviour:
      - In the very first round (all teams 0-0):
          use 'seeded' first-round pairings:
              sort by vrs_rank ascending, then pair
                [0] vs [N/2], [1] vs [N/2+1], etc.

      - In all subsequent rounds:
          use a backtracking matcher that:
            * sorts teams by vrs_rank ascending
            * attempts to pair without *any* rematches (based on opponents_played)
            * prefers 'top vs bottom' style (1 vs N, 2 vs N-1, etc.)
            * if avoiding rematches is impossible, falls back to
              the seeded 'top vs bottom' pairings.

    This should fix cases where the old avoid_rematches logic stopped
    one rematch but allowed another.
    """

    # sort by vrs_rank: lower is better
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

    # For non-first rounds, try to find a full matching with no rematches.

    # Build a quick lookup: team_id -> TeamState, although we already have objects
    teams = pool_sorted[:]  # copy

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

        # pick the highest-seeded remaining team
        t0 = remaining[0]
        rest = remaining[1:]

        # candidate partners, in preferred order:
        #   try bottom seed first (top vs bottom style), then upwards
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

    # If we couldn't avoid rematches entirely, fall back to seeded top vs bottom:
    # sort by vrs_rank and pair [0] vs [N-1], [1] vs [N-2], ...
    fallback_pairs: List[Tuple[TeamState, TeamState]] = []
    for i in range(n // 2):
        A = pool_sorted[i]
        B = pool_sorted[n - 1 - i]
        fallback_pairs.append((A, B))

    return fallback_pairs
