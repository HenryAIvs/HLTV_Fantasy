"""The roster a valuation plays a team with.

A fantasy game lists each team as HLTV fields it for THAT event, stand-ins
included, and that list is stored with the event (event_teams). The teams
table holds the organisation's current lineup instead, refreshed nightly from
the HLTV team page, which is the wrong roster whenever a team plays an event
with a substitute. Valuations therefore read rosters here: the event being
baked (set by the pipeline), else the active event; a team outside that event
keeps its teams-table lineup.
"""
from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional

_EVENT: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("event_rosters_event", default=None)
_LOCK = threading.Lock()
_CACHE: Dict[int, tuple[float, Dict[int, List[int]]]] = {}
_TTL_SECONDS = 60.0


@contextmanager
def for_event(event_id: Optional[int]) -> Iterator[None]:
    """Value teams with this event's rosters inside the block."""
    token = _EVENT.set(int(event_id) if event_id else None)
    try:
        yield
    finally:
        _EVENT.reset(token)


def current_event_id() -> Optional[int]:
    explicit = _EVENT.get()
    if explicit:
        return int(explicit)
    from backend.data.event_db import get_active_event_id

    try:
        active = get_active_event_id()
    except Exception:  # noqa: BLE001
        return None
    return int(active) if active else None


def invalidate() -> None:
    with _LOCK:
        _CACHE.clear()


def rosters(event_id: Optional[int] = None) -> Dict[int, List[int]]:
    """{team_id: [player ids]} as the event lists its teams."""
    eid = int(event_id) if event_id else current_event_id()
    if not eid:
        return {}
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(eid)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    from backend.data.event_db import get_event_detail
    from backend.data.team_db import get_team_by_name

    out: Dict[int, List[int]] = {}
    try:
        event = get_event_detail(eid) or {}
        for team in event.get("teams") or []:
            name = str(team.get("team_name") or "").strip()
            pids = [int(p) for p in (team.get("player_ids") or []) if p]
            if not name or not pids:
                continue
            row = get_team_by_name(name)
            if row and row.get("team_id") is not None:
                out[int(row["team_id"])] = pids
    except Exception:  # noqa: BLE001
        out = {}
    with _LOCK:
        _CACHE[eid] = (now, out)
    return out


def roster_for(team_id: int, event_id: Optional[int] = None) -> Optional[List[int]]:
    """The event's roster for the team. A team the fantasy game does not list
    at all (in the draw, but none of its players priced) gets an empty roster:
    its matches still shape everyone else's odds, but nothing of it can be
    picked. None when no event is in scope or the event lists no teams."""
    listed = rosters(event_id)
    if not listed:
        return None
    return listed.get(int(team_id), [])
