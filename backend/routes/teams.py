from datetime import date
import logging

from fastapi import APIRouter, HTTPException

from backend.hltv_rankings import (
    HLTVRankingError,
    RankingPageParseError,
    TeamNotRankedError,
    get_latest_hltv_rankings,
    get_latest_vrs_rankings,
    get_team_hltv_rating_and_points_on_date,
    get_team_vrs_rank_and_points_on_date,
)
from backend.data.team_db import (
    get_all_teams,
    get_team_by_id,
    get_team_by_name,
    add_or_update_team,
    delete_team,
)


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
def list_teams():
    return get_all_teams()


@router.get("/hltv-ranking/by-date")
def fetch_hltv_ranking_by_date(team_name: str, on_date: str):
    """
    Fetch HLTV ranking position ("hltv_rating") and points for a team on a specific date.
    `on_date` must be YYYY-MM-DD.
    """
    if not team_name or not team_name.strip():
        raise HTTPException(status_code=400, detail="team_name is required")

    try:
        parsed_date = date.fromisoformat(on_date)
    except Exception:
        raise HTTPException(status_code=400, detail="on_date must be in YYYY-MM-DD format")

    try:
        return get_team_hltv_rating_and_points_on_date(team_name, parsed_date)
    except TeamNotRankedError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RankingPageParseError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except HLTVRankingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/refresh-hltv-today")
def refresh_hltv_for_all_teams_today():
    """
    Refresh all teams' HLTV rank and HLTV points for today's date.
    """
    today = date.today()
    teams = get_all_teams()
    teams_by_name = {(t.get("name") or "").strip().lower(): t for t in teams if (t.get("name") or "").strip()}
    logger.info("HLTV refresh requested: date=%s existing_teams=%d", today.isoformat(), len(teams))

    results = []
    updated = 0
    failed = 0
    snapshot = None

    try:
        snapshot = get_latest_hltv_rankings()
        logger.info(
            "HLTV latest snapshot selected: requested=%s effective=%s",
            snapshot.get("requested_date"),
            snapshot.get("effective_date"),
        )
    except HLTVRankingError as exc:
        logger.error("HLTV refresh snapshot fetch failed: %s", exc)
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": len([t for t in teams if (t.get("name") or "").strip()]),
            "results": [
                {
                    "team_id": t.get("team_id"),
                    "team_name": (t.get("name") or "").strip(),
                    "status": "error",
                    "detail": str(exc),
                }
                for t in teams
                if (t.get("name") or "").strip()
            ],
        }

    rankings = snapshot.get("rankings_by_team") or {}
    effective_date = snapshot.get("effective_date")
    requested_date = snapshot.get("requested_date")
    days_back = int(snapshot.get("days_back") or 0)

    if not rankings:
        logger.warning("HLTV refresh snapshot contains no teams")
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": 0,
            "inserted": 0,
            "results": [],
        }

    inserted = 0
    for _, data in rankings.items():
        name = (data.get("team_name") or "").strip()
        if not name:
            continue
        try:
            existing = teams_by_name.get(name.lower()) or {}
            before_exists = bool(existing)
            add_or_update_team(
                name=name,
                hltv_rank=int(data.get("hltv_rating") or 999),
                hltv_points=int(data.get("points") or 0),
                vrs_rank=int(existing.get("vrs_rank") or 999),
                vrs_points=int(existing.get("vrs_points") or 0),
                win_rate=float(existing.get("win_rate") or 0.5),
                player_ids=[
                    int(existing.get("player1_id") or 0),
                    int(existing.get("player2_id") or 0),
                    int(existing.get("player3_id") or 0),
                    int(existing.get("player4_id") or 0),
                    int(existing.get("player5_id") or 0),
                ],
            )
            updated += 1
            if not before_exists:
                inserted += 1
            results.append(
                {
                    "team_id": existing.get("team_id"),
                    "team_name": name,
                    "status": "ok",
                    "hltv_rank": int(data.get("hltv_rating") or 999),
                    "hltv_points": int(data.get("points") or 0),
                    "requested_date": requested_date,
                    "effective_date": effective_date,
                    "days_back": days_back,
                    "inserted": not before_exists,
                }
            )
        except Exception as exc:
            logger.error("HLTV refresh error: team='%s' detail=%s", name, exc)
            results.append({"team_name": name, "status": "error", "detail": str(exc)})
            failed += 1

    logger.info("HLTV refresh complete: updated=%d inserted=%d failed=%d", updated, inserted, failed)
    return {
        "status": "ok",
        "date": today.isoformat(),
        "updated": updated,
        "inserted": inserted,
        "failed": failed,
        "results": results,
    }


@router.get("/vrs-ranking/by-date")
def fetch_vrs_ranking_by_date(team_name: str, on_date: str):
    if not team_name or not team_name.strip():
        raise HTTPException(status_code=400, detail="team_name is required")

    try:
        parsed_date = date.fromisoformat(on_date)
    except Exception:
        raise HTTPException(status_code=400, detail="on_date must be in YYYY-MM-DD format")

    try:
        return get_team_vrs_rank_and_points_on_date(team_name, parsed_date)
    except TeamNotRankedError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RankingPageParseError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except HLTVRankingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/refresh-vrs-today")
def refresh_vrs_for_all_teams_today():
    today = date.today()
    teams = get_all_teams()
    teams_by_name = {(t.get("name") or "").strip().lower(): t for t in teams if (t.get("name") or "").strip()}
    logger.info("VRS refresh requested: date=%s existing_teams=%d", today.isoformat(), len(teams))

    results = []
    updated = 0
    failed = 0

    try:
        snapshot = get_latest_vrs_rankings()
        logger.info(
            "VRS latest snapshot selected: requested=%s effective=%s",
            snapshot.get("requested_date"),
            snapshot.get("effective_date"),
        )
    except HLTVRankingError as exc:
        logger.error("VRS refresh snapshot fetch failed: %s", exc)
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": len([t for t in teams if (t.get("name") or "").strip()]),
            "results": [
                {
                    "team_id": t.get("team_id"),
                    "team_name": (t.get("name") or "").strip(),
                    "status": "error",
                    "detail": str(exc),
                }
                for t in teams
                if (t.get("name") or "").strip()
            ],
        }

    rankings = snapshot.get("rankings_by_team") or {}
    effective_date = snapshot.get("effective_date")
    requested_date = snapshot.get("requested_date")
    days_back = int(snapshot.get("days_back") or 0)

    if not rankings:
        logger.warning("VRS refresh snapshot contains no teams")
        return {
            "status": "ok",
            "date": today.isoformat(),
            "updated": 0,
            "failed": 0,
            "inserted": 0,
            "results": [],
        }

    inserted = 0
    for _, data in rankings.items():
        name = (data.get("team_name") or "").strip()
        if not name:
            continue
        try:
            existing = teams_by_name.get(name.lower()) or {}
            before_exists = bool(existing)
            add_or_update_team(
                name=name,
                hltv_rank=int(existing.get("hltv_rank") or 999),
                hltv_points=int(existing.get("hltv_points") or 0),
                vrs_rank=int(data.get("vrs_rank") or 999),
                vrs_points=int(data.get("points") or 0),
                win_rate=float(existing.get("win_rate") or 0.5),
                player_ids=[
                    int(existing.get("player1_id") or 0),
                    int(existing.get("player2_id") or 0),
                    int(existing.get("player3_id") or 0),
                    int(existing.get("player4_id") or 0),
                    int(existing.get("player5_id") or 0),
                ],
            )
            updated += 1
            if not before_exists:
                inserted += 1
            results.append(
                {
                    "team_id": existing.get("team_id"),
                    "team_name": name,
                    "status": "ok",
                    "vrs_rank": int(data.get("vrs_rank") or 999),
                    "vrs_points": int(data.get("points") or 0),
                    "requested_date": requested_date,
                    "effective_date": effective_date,
                    "days_back": days_back,
                    "inserted": not before_exists,
                }
            )
        except Exception as exc:
            logger.error("VRS refresh error: team='%s' detail=%s", name, exc)
            results.append({"team_name": name, "status": "error", "detail": str(exc)})
            failed += 1

    logger.info("VRS refresh complete: updated=%d inserted=%d failed=%d", updated, inserted, failed)
    return {
        "status": "ok",
        "date": today.isoformat(),
        "updated": updated,
        "inserted": inserted,
        "failed": failed,
        "results": results,
    }


@router.post("/refresh-rankings-today")
def refresh_all_rankings_for_all_teams_today():
    """
    Run HLTV + VRS refresh in one call.
    """
    hltv = refresh_hltv_for_all_teams_today()
    vrs = refresh_vrs_for_all_teams_today()
    return {
        "status": "ok",
        "date": date.today().isoformat(),
        "hltv": hltv,
        "vrs": vrs,
        "updated_total": int(hltv.get("updated", 0)) + int(vrs.get("updated", 0)),
        "inserted_total": int(hltv.get("inserted", 0)) + int(vrs.get("inserted", 0)),
        "failed_total": int(hltv.get("failed", 0)) + int(vrs.get("failed", 0)),
    }


@router.get("/{team_id}")
def fetch_team(team_id: int):
    team = get_team_by_id(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.get("/by-name/{name}")
def fetch_team_by_name(name: str):
    team = get_team_by_name(name)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.post("/")
def upsert_team(payload: dict):
    """
    Requires: name, hltv_rank, vrs_rank, win_rate, player_ids (len==5).
    """
    required = ["name", "hltv_rank", "vrs_rank", "win_rate", "player_ids"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
    add_or_update_team(**payload)
    return {"status": "ok"}


@router.delete("/{team_id}")
def remove_team(team_id: int):
    delete_team(team_id)
    return {"status": "ok"}
