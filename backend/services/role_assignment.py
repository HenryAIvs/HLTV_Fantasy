# role_assignment.py

import json
from typing import Dict, List, Tuple, Optional

from backend.services.rating_curve import _safe_float


def _compute_role_score(major: float, minor: float) -> float:
    """
    Compute expected role points for a role given its major/minor trigger rates.

    Using your earlier formula:
      role_score_per_match = 5 * major + 2 * minor - 2 * (1 - major - minor)
    """
    if major is None or minor is None:
        return 0.0
    return 5.0 * major + 2.0 * minor - 2.0 * (1.0 - major - minor)


def extract_role_scores_for_player(player_row: Dict) -> Dict[int, float]:
    """
    From a player DB row (with roles_json), build a mapping:
       our_role_index -> role_score

    IMPORTANT: roles_json is assumed to be keyed by our internal role indices "0".."11",
    which is exactly what your triggerRates importer writes.

    So if roles_json looks like:
      {
        "0": {"major": 0.68, "minor": 0.29},
        "1": {...},
        ...
      }

    then we treat the keys 0..11 as our role IDs.
    """
    out: Dict[int, float] = {}
    roles_json_str = player_row.get("roles_json")
    if not roles_json_str:
        return out
    try:
        roles_obj = json.loads(roles_json_str)
    except Exception:
        return out

    for rid_str, data in roles_obj.items():
        if not isinstance(data, dict):
            continue
        try:
            our_role = int(rid_str)
        except ValueError:
            continue
        major = _safe_float(data.get("major"))
        minor = _safe_float(data.get("minor"))
        if major is None or minor is None:
            continue
        score = _compute_role_score(major, minor)
        out[our_role] = score

    return out


def best_role_assignment_for_team(
    player_ids: List[int],
    role_scores_by_player: Dict[int, Dict[int, float]],
) -> Tuple[Optional[Dict[int, int]], float]:
    """
    Greedy, clash-avoiding role assignment for a single team (typically 5 players).

    For the given player_ids:
      - role_scores_by_player[pid] gives {role_id -> score} for that player.
      - For a team of size n, we only consider each player's TOP n roles.
        (It's logically impossible to ever need a player's 6th-best role in a 5-player team.)
      - Start with everyone on their best role.
      - While there is a clash (role used by >1 player):
          - For that clashing role, consider moving each affected player
            to their next-best unused role from their top-n list.
          - Pick the move with the best delta in total score (least loss / biggest gain).
          - Apply it.
        If at any point no player in a clash can move to a free role,
        we return (None, 0.0) = no clash-free assignment.

    Returns:
      (assignment, total_role_score)
    where:
      - assignment is {player_id: role_index} (our role indices 0..11),
      - total_role_score is sum of the scores for the assigned roles.
    """

    # Build (pid, scores) rows from precomputed dict
    player_rows: List[Tuple[int, Dict[int, float]]] = []
    for pid in player_ids:
        scores = role_scores_by_player.get(pid, {})
        player_rows.append((pid, scores))

    n = len(player_rows)
    if n == 0:
        return None, 0.0

    # Build per-player role preference list: sorted by score, truncated to top n roles
    preferences: List[List[Tuple[int, float]]] = []
    for pid, scores in player_rows:
        items = sorted(
            [(rid, s) for rid, s in scores.items()],
            key=lambda kv: kv[1],
            reverse=True,
        )
        if not items:
            # player has no valid roles at all
            return None, 0.0
        # logical cap: only top n roles for a team of size n
        items = items[:n]
        preferences.append(items)

    # Initial assignment: everyone gets their best role
    assignment: Dict[int, int] = {}
    role_to_players: Dict[int, List[int]] = {}

    for i, (pid, _scores) in enumerate(player_rows):
        best_role_id, _best_score = preferences[i][0]
        assignment[pid] = best_role_id
        role_to_players.setdefault(best_role_id, []).append(pid)

    # Helper to compute current total score
    def current_total_score() -> float:
        total = 0.0
        for i, (pid, _scores) in enumerate(player_rows):
            r_id = assignment.get(pid)
            if r_id is None:
                continue
            for role_id, score in preferences[i]:
                if role_id == r_id:
                    total += score
                    break
        return total

    # Greedy conflict resolution
    while True:
        # Find all clashing roles (used by > 1 player)
        clashes = [r for r, plist in role_to_players.items() if len(plist) > 1]

        if not clashes:
            # No clashes, we're done
            break

        best_move = None  # (pid, old_role, new_role)
        best_move_delta = float("-inf")

        # For each clashing role, and for each player assigned to it,
        # consider moving that player to their next-best unused role.
        for role_id in clashes:
            players_on_role = role_to_players.get(role_id, [])
            for pid in players_on_role:
                # find this player's index in player_rows / preferences
                try:
                    i = next(idx for idx, (p, _) in enumerate(player_rows) if p == pid)
                except StopIteration:
                    continue
                prefs = preferences[i]

                # current score for this player's assigned role
                current_role = assignment[pid]
                current_score = 0.0
                for rid, s in prefs:
                    if rid == current_role:
                        current_score = s
                        break

                # look for next-best alternative role that is not already used by *another* player
                for alt_role, alt_score in prefs:
                    if alt_role == current_role:
                        continue
                    if alt_role in role_to_players and any(p != pid for p in role_to_players[alt_role]):
                        continue

                    delta = alt_score - current_score
                    if delta > best_move_delta:
                        best_move_delta = delta
                        best_move = (pid, current_role, alt_role)
                    # only consider the first valid alternative (their next-best free role)
                    break

        if best_move is None:
            # No possible move to resolve clashes -> no clash-free assignment
            return None, 0.0

        # Apply the best move found
        pid, old_role, new_role = best_move

        # update assignment
        assignment[pid] = new_role

        # update role_to_players
        role_to_players[old_role] = [p for p in role_to_players[old_role] if p != pid]
        if not role_to_players[old_role]:
            del role_to_players[old_role]

        role_to_players.setdefault(new_role, []).append(pid)

    # At this point there are no clashes
    total_score = current_total_score()
    return assignment, total_score
