from dataclasses import dataclass, field
from typing import Any, Dict, Set, List


@dataclass
class PlayerState:
    player_id: int
    rating: float
    major_pct: float        # role major trigger (0-1)
    minor_pct: float        # role minor trigger (0-1)
    boosters: List[float]   # e.g. top 5 booster trigger values
    role_id: int | None = None
    booster_ids: List[int] = field(default_factory=list)
    booster_rates: Dict[int, float] = field(default_factory=dict)
    # Per-slot edge over the field-average trigger rate (drives slot order).
    booster_edges: List[float] = field(default_factory=list)

    # Aggregated fantasy points over the whole Swiss sim:
    total_points: float = 0.0

    # Components:
    rating_points_total: float = 0.0
    win_points_total: float = 0.0
    role_points_total: float = 0.0
    booster_points_total: float = 0.0
    point_breakdown: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TeamState:
    team_id: int
    vrs_rank: int                    # lower = better seed
    players: Dict[int, PlayerState]  # player_id -> PlayerState

    wins: int = 0
    losses: int = 0
    opponents_played: Set[int] = field(default_factory=set)

    def record_win(self, opponent_id: int):
        self.wins += 1
        self.opponents_played.add(opponent_id)

    def record_loss(self, opponent_id: int):
        self.losses += 1
        self.opponents_played.add(opponent_id)

    @property
    def eliminated(self) -> bool:
        return self.losses >= 3

    @property
    def qualified(self) -> bool:
        return self.wins >= 3

    @property
    def matches_played(self) -> int:
        return self.wins + self.losses
