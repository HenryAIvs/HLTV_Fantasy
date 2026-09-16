"""One lifecycle for every tournament format: the event behaviour contract.

Every fantasy event, whatever its format, is handled the same way:

1. Imported: baked straight away. Inputs come from the event page (group draw
   or playoff bracket), every outcome is enumerated, the roster combinations
   are stored and the Top 5 caches warmed. If the draw or bracket is not
   published yet the event is "pending" and the nightly run retries it.
2. Until its first match: refreshed by the nightly run with that night's
   ratings, rankings, roles and boosters.
3. From its first match: frozen. Nothing automatic touches it again.
4. Stored per event. Another event's run never replaces it.
5. The operator's Run buttons are manual overrides, allowed at any time.
6. The public app reads exactly the stored run and can view any imported event.

Formats plug in through FORMATS below. A format without an automatic baker is
reported as "manual" (never silently skipped) until it is brought in.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple


def _groups_hooks() -> Dict[str, Any]:
    from backend.routes import groups

    return {"bake": groups.bake_event_valuations, "exists": lambda eid: groups._GROUPS_STATE.exists(eid)}


def _playoff_hooks() -> Dict[str, Any]:
    from backend.routes import playoff

    return {"bake": playoff.bake_event_playoff, "exists": lambda eid: playoff._STATE_SETS["main"]["playoff"].exists(eid)}


def _swiss_hooks() -> Dict[str, Any]:
    from backend.routes import simulation

    # Not automated yet: run from the operator app (Swiss tab).
    return {"bake": None, "exists": lambda eid: simulation._SIM_STATE.exists(eid)}


def _bounty_hooks() -> Dict[str, Any]:
    from backend.routes import playoff

    return {"bake": None, "exists": lambda eid: playoff._STATE_SETS["bounty"]["playoff"].exists(eid)}


# kind (as detected by events._detect_event_tournament_kind_cached, or the
# operator's override) -> hooks factory. Factories import lazily: the routers
# import this module's neighbours at load time.
FORMATS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "groups": _groups_hooks,
    "playoff": _playoff_hooks,
    "double_elim": _playoff_hooks,
    "swiss": _swiss_hooks,
    "bounty": _bounty_hooks,
}


def detect_kind(event_id: int) -> Tuple[Optional[str], Dict[str, Any]]:
    """(kind, detection dict) for an event; kind honours the operator override."""
    from backend.data.event_db import get_event_detail, get_event_tournament_kind
    from backend.routes import events as events_routes

    event = get_event_detail(int(event_id))
    if not event:
        return None, {}
    try:
        detected = events_routes._detect_event_tournament_kind_cached(event) or {}
    except Exception:  # noqa: BLE001
        detected = {}
    return (get_event_tournament_kind(int(event_id)) or detected.get("kind")), detected


def _hooks_for(kind: Optional[str]) -> Optional[Dict[str, Any]]:
    factory = FORMATS.get(str(kind or ""))
    return factory() if factory else None


def has_stored_run(event_id: int) -> bool:
    kind, _ = detect_kind(event_id)
    hooks = _hooks_for(kind)
    return bool(hooks and hooks["exists"](int(event_id)))


def has_started(event_id: int) -> Optional[bool]:
    """True/False from the event page's start stamp; None when unknown."""
    _, detected = detect_kind(event_id)
    start = detected.get("start_at")
    if not start:
        return None
    return time.time() >= float(start)


def bake_event(event_id: int, trigger: str = "import", only_if_missing: bool = False, refresh_inputs: bool = False) -> Dict[str, Any]:
    """Run the event's format baker. Never raises; returns a status dict the
    scheduler records ("ok", "exists", "skipped", "pending", "manual",
    "unsupported", "error")."""
    event_id = int(event_id)
    kind, _ = detect_kind(event_id)
    hooks = _hooks_for(kind)
    if not hooks:
        return {"status": "unsupported", "event_id": event_id, "kind": kind, "reason": f"no pipeline for format {kind!r}"}
    if hooks["bake"] is None:
        return {
            "status": "manual",
            "event_id": event_id,
            "kind": kind,
            "reason": f"{kind} events are not automated yet; run them from the operator app",
        }
    try:
        return hooks["bake"](event_id, trigger=trigger, only_if_missing=only_if_missing, refresh_inputs=refresh_inputs)
    except Exception as exc:  # noqa: BLE001 - the scheduler must always get a status
        return {"status": "error", "event_id": event_id, "kind": kind, "reason": str(exc)[:300]}


def event_status(event_id: int) -> Dict[str, Any]:
    """Where an event sits in the lifecycle, for the Events tab and tooling."""
    event_id = int(event_id)
    kind, detected = detect_kind(event_id)
    hooks = _hooks_for(kind)
    baked = bool(hooks and hooks["exists"](event_id))
    start_at = detected.get("start_at")
    started: Optional[bool] = (time.time() >= float(start_at)) if start_at else None
    automated = bool(hooks and hooks["bake"])
    if not hooks:
        status, label = "unsupported", "Format not supported"
    elif not automated:
        status = "manual"
        label = "Published (run manually)" if baked else "Not automated: run from the operator app"
    elif baked and started:
        status, label = "frozen", "Published, frozen (event started)"
    elif baked:
        status, label = "baked", "Published, refreshes nightly until the first match"
    elif started:
        status, label = "missing", "Not published (event already started)"
    else:
        status, label = "pending", "Not published yet, retried nightly"
    return {
        "kind": kind,
        "automated": automated,
        "baked": baked,
        "started": started,
        "start_at": float(start_at) if start_at else None,
        "status": status,
        "label": label,
        "published": bool(baked),
    }
