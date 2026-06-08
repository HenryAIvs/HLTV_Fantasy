from typing import Any, Dict, List, Tuple
import math
import re
import json
from datetime import date
import logging

from fastapi import APIRouter, HTTPException

from backend.routes.admin import _import_money_draft_data
from backend.hltv_rankings import (
    get_recent_hltv_results,
    get_hltv_match_details,
    get_all_hltv_rankings_on_or_before_date,
    get_all_vrs_rankings_on_or_before_date,
    HLTVRankingError,
)
from backend.data.event_db import (
    clear_hltv_results,
    count_hltv_results,
    dedupe_hltv_results_by_match_id,
    get_cached_hltv_rankings_on_or_before_date,
    get_cached_vrs_rankings_on_or_before_date,
    get_active_event_id,
    get_event_detail,
    get_hltv_result_by_url,
    list_hltv_results,
    set_hltv_result_maps,
    list_events,
    set_active_event,
    upsert_hltv_rankings_snapshot,
    upsert_vrs_rankings_snapshot,
    update_hltv_results_points,
    upsert_hltv_results,
)
from backend.data.team_db import get_all_teams
from backend.services.hltv_browser import HLTVBrowserError, fetch_hltv_json

router = APIRouter()
logger = logging.getLogger(__name__)


def _norm_team_name(name: str) -> str:
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _fit_logistic_1d(xs: List[float], ys: List[int]) -> Dict[str, float]:
    if len(xs) < 10 or len(xs) != len(ys):
        raise ValueError("Not enough data points to fit model.")

    mean_x = sum(xs) / len(xs)
    var_x = sum((x - mean_x) ** 2 for x in xs) / max(1, len(xs) - 1)
    std_x = math.sqrt(var_x) if var_x > 1e-12 else 1.0
    zs = [(x - mean_x) / std_x for x in xs]

    a = 0.0
    b = 0.0
    l2 = 1e-4
    lr = 0.03
    n = float(len(zs))
    for _ in range(2500):
        ga = 0.0
        gb = 0.0
        for z, y in zip(zs, ys):
            p = _sigmoid(a + b * z)
            e = p - float(y)
            ga += e
            gb += e * z
        ga = ga / n + l2 * a
        gb = gb / n + l2 * b
        a -= lr * ga
        b -= lr * gb

    return {"a": a, "b": b, "mean_x": mean_x, "std_x": std_x}


def _predict_logistic_1d(model: Dict[str, float], x: float) -> float:
    z = (x - float(model["mean_x"])) / float(model["std_x"] if model["std_x"] != 0 else 1.0)
    return _sigmoid(float(model["a"]) + float(model["b"]) * z)


def _fit_logistic_2d(x1s: List[float], x2s: List[float], ys: List[int]) -> Dict[str, float]:
    if len(x1s) < 20 or len(x1s) != len(x2s) or len(x1s) != len(ys):
        raise ValueError("Not enough data points to fit 2-feature model.")

    n = float(len(x1s))
    m1 = sum(x1s) / len(x1s)
    m2 = sum(x2s) / len(x2s)
    v1 = sum((x - m1) ** 2 for x in x1s) / max(1, len(x1s) - 1)
    v2 = sum((x - m2) ** 2 for x in x2s) / max(1, len(x2s) - 1)
    s1 = math.sqrt(v1) if v1 > 1e-12 else 1.0
    s2 = math.sqrt(v2) if v2 > 1e-12 else 1.0
    z1s = [(x - m1) / s1 for x in x1s]
    z2s = [(x - m2) / s2 for x in x2s]

    a = 0.0
    b1 = 0.0
    b2 = 0.0
    l2 = 1e-4
    lr = 0.02
    for _ in range(3000):
        ga = 0.0
        gb1 = 0.0
        gb2 = 0.0
        for z1, z2, y in zip(z1s, z2s, ys):
            p = _sigmoid(a + b1 * z1 + b2 * z2)
            e = p - float(y)
            ga += e
            gb1 += e * z1
            gb2 += e * z2
        ga = ga / n + l2 * a
        gb1 = gb1 / n + l2 * b1
        gb2 = gb2 / n + l2 * b2
        a -= lr * ga
        b1 -= lr * gb1
        b2 -= lr * gb2

    return {
        "a": a,
        "b_hltv": b1,
        "b_vrs": b2,
        "mean_hltv": m1,
        "std_hltv": s1,
        "mean_vrs": m2,
        "std_vrs": s2,
    }


def _predict_logistic_2d(model: Dict[str, float], x1: float, x2: float) -> float:
    z1 = (x1 - float(model["mean_hltv"])) / float(model["std_hltv"] if model["std_hltv"] != 0 else 1.0)
    z2 = (x2 - float(model["mean_vrs"])) / float(model["std_vrs"] if model["std_vrs"] != 0 else 1.0)
    return _sigmoid(float(model["a"]) + float(model["b_hltv"]) * z1 + float(model["b_vrs"]) * z2)


def _build_current_points_maps() -> Tuple[Dict[str, int], Dict[str, int]]:
    teams = get_all_teams()
    hltv_map: Dict[str, int] = {}
    vrs_map: Dict[str, int] = {}
    for t in teams:
        key = _norm_team_name(t.get("name") or "")
        if not key:
            continue
        hp = t.get("hltv_points")
        vp = t.get("vrs_points")
        if hp is not None:
            hltv_map[key] = int(hp)
        if vp is not None:
            vrs_map[key] = int(vp)
    return hltv_map, vrs_map


@router.get("/")
def list_all_events():
    return {
        "active_event_id": get_active_event_id(),
        "events": list_events(),
    }


@router.get("/active")
def get_active_event():
    active_id = get_active_event_id()
    if active_id is None:
        return {"active_event_id": None, "event": None}
    event = get_event_detail(active_id)
    return {"active_event_id": active_id, "event": event}


@router.get("/hltv-recent-results")
def fetch_hltv_recent_results(limit: int = 100):
    try:
        return get_recent_hltv_results(limit=limit)
    except HLTVRankingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/hltv-results/import")
def import_hltv_results(payload: Dict[str, Any] | None = None):
    body = payload or {}
    pages = int(body.get("pages", 1))
    start_offset = int(body.get("start_offset", 0))
    page_stride = int(body.get("page_stride", 100))
    per_page_limit = int(body.get("per_page_limit", 100))

    pages = max(1, min(30, pages))
    start_offset = max(0, start_offset)
    page_stride = max(1, page_stride)
    per_page_limit = max(1, min(100, per_page_limit))
    offsets = [start_offset + i * page_stride for i in range(pages)]

    try:
        snapshot = get_recent_hltv_results(limit=pages * per_page_limit, offsets=offsets)
    except HLTVRankingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    rows = snapshot.get("results") or []
    # Persist map-level details at import time so UI cards can be DB-only.
    for r in rows:
        url = str(r.get("match_url") or "").strip()
        if not url:
            continue
        try:
            md = get_hltv_match_details(url)
            maps = md.get("maps") or []
            if maps:
                r["maps_json"] = json.dumps(maps)
        except Exception:
            continue
    stored = upsert_hltv_results(rows)
    deduped_removed = dedupe_hltv_results_by_match_id()
    enrich = enrich_hltv_results_historical_points(
        {
            "limit": max(1, count_hltv_results()),
            "max_days_back": 7,
            "refresh_all": False,
            "auto_backfill_dates": True,
        }
    )
    return {
        "status": "ok",
        "requested_pages": pages,
        "offsets": offsets,
        "fetched": len(rows),
        "inserted": int(stored.get("inserted", 0)),
        "updated": int(stored.get("updated", 0)),
        "deduped_removed": int(deduped_removed),
        "enriched_matches": int((enrich or {}).get("matches_updated", 0)),
    }


@router.get("/hltv-results")
def get_hltv_results(limit: int = 100, offset: int = 0):
    rows = list_hltv_results(limit=limit, offset=offset)
    return {
        "count": len(rows),
        "limit": int(limit),
        "offset": int(offset),
        "results": rows,
    }


@router.delete("/hltv-results")
def reset_hltv_results():
    deleted = clear_hltv_results()
    return {
        "status": "ok",
        "deleted": int(deleted),
    }


@router.post("/hltv-results/enrich-historical-points")
def enrich_hltv_results_historical_points(payload: Dict[str, Any] | None = None):
    body = payload or {}
    max_days_back = max(0, min(30, int(body.get("max_days_back", 7))))
    refresh_all = bool(body.get("refresh_all", False))
    auto_backfill_dates = bool(body.get("auto_backfill_dates", True))
    limit = int(body.get("limit", 0))
    if limit <= 0:
        limit = max(1, count_hltv_results())

    rows = list_hltv_results(limit=limit, offset=0)
    if not rows:
        raise HTTPException(status_code=400, detail="No stored HLTV results to enrich.")

    with_date = sum(1 for r in rows if str(r.get("match_date") or "").strip())
    logger.info(
        "HLTV historical enrich start: scanned=%d with_date=%d missing_date=%d max_days_back=%d refresh_all=%s",
        len(rows),
        with_date,
        max(0, len(rows) - with_date),
        max_days_back,
        refresh_all,
    )

    # Backfill match_date by re-fetching the known source offsets and upserting.
    if auto_backfill_dates and with_date == 0 and rows:
        offsets = sorted({int(r.get("source_offset") or 0) for r in rows})[:30]
        try:
            logger.info("HLTV historical enrich: attempting date backfill from live results offsets=%s", offsets)
            snapshot = get_recent_hltv_results(limit=len(offsets) * 100, offsets=offsets)
            fetched_rows = snapshot.get("results") or []
            stored = upsert_hltv_results(fetched_rows)
            logger.info(
                "HLTV historical enrich: date backfill completed fetched=%d inserted=%d updated=%d",
                len(fetched_rows),
                int(stored.get("inserted", 0)),
                int(stored.get("updated", 0)),
            )
            rows = list_hltv_results(limit=limit, offset=0)
            with_date = sum(1 for r in rows if str(r.get("match_date") or "").strip())
            logger.info(
                "HLTV historical enrich: post-backfill with_date=%d missing_date=%d",
                with_date,
                max(0, len(rows) - with_date),
            )
        except Exception as exc:
            logger.warning("HLTV historical enrich: date backfill attempt failed: %s", exc)

    target_rows = []
    for r in rows:
        if not str(r.get("match_date") or "").strip():
            continue
        has_any = any(
            r.get(k) is not None
            for k in ("hltv_points_1", "hltv_points_2", "vrs_points_1", "vrs_points_2")
        )
        if refresh_all or not has_any:
            target_rows.append(r)

    if not target_rows:
        sample_missing = [str(r.get("match_url") or "") for r in rows if not str(r.get("match_date") or "").strip()][:5]
        if sample_missing:
            logger.warning("HLTV historical enrich: no target rows. sample missing-date matches=%s", sample_missing)
        return {
            "status": "ok",
            "matches_scanned": len(rows),
            "matches_targeted": 0,
            "matches_updated": 0,
            "dates_requested": 0,
            "dates_hltv_ok": 0,
            "dates_vrs_ok": 0,
        }

    unique_dates = sorted({str(r.get("match_date")) for r in target_rows if r.get("match_date")})
    hltv_cache: Dict[str, Dict[str, Any]] = {}
    vrs_cache: Dict[str, Dict[str, Any]] = {}
    dates_hltv_ok = 0
    dates_vrs_ok = 0
    dates_hltv_cache = 0
    dates_vrs_cache = 0
    dates_hltv_live = 0
    dates_vrs_live = 0

    for ds in unique_dates:
        cached_h = get_cached_hltv_rankings_on_or_before_date(ds, max_days_back=max_days_back)
        if cached_h and (cached_h.get("rankings_by_team") or {}):
            hltv_cache[ds] = cached_h
            dates_hltv_ok += 1
            dates_hltv_cache += 1
            logger.info(
                "HLTV historical enrich date cache hit: requested=%s effective=%s teams=%d",
                ds,
                cached_h.get("effective_date"),
                len(cached_h.get("rankings_by_team") or {}),
            )
        else:
            logger.info("HLTV historical enrich date cache miss: requested=%s", ds)
        try:
            d = date.fromisoformat(ds)
        except Exception:
            continue
        if ds not in hltv_cache:
            try:
                snap = get_all_hltv_rankings_on_or_before_date(d, max_days_back=max_days_back)
                hltv_cache[ds] = snap
                dates_hltv_ok += 1
                dates_hltv_live += 1
                upsert_hltv_rankings_snapshot(
                    str(snap.get("effective_date") or ds),
                    snap.get("rankings_by_team") or {},
                    str(snap.get("url") or ""),
                )
                logger.info(
                    "HLTV historical enrich date ok (live): requested=%s effective=%s teams=%d",
                    ds,
                    snap.get("effective_date"),
                    len(snap.get("rankings_by_team") or {}),
                )
            except Exception:
                hltv_cache[ds] = {}
                logger.warning("HLTV historical enrich date failed (HLTV): requested=%s", ds)

        cached_v = get_cached_vrs_rankings_on_or_before_date(ds, max_days_back=max_days_back)
        if cached_v and (cached_v.get("rankings_by_team") or {}):
            vrs_cache[ds] = cached_v
            dates_vrs_ok += 1
            dates_vrs_cache += 1
            logger.info(
                "HLTV historical enrich date cache hit (VRS): requested=%s effective=%s teams=%d",
                ds,
                cached_v.get("effective_date"),
                len(cached_v.get("rankings_by_team") or {}),
            )
        else:
            logger.info("HLTV historical enrich date cache miss (VRS): requested=%s", ds)
            try:
                snap = get_all_vrs_rankings_on_or_before_date(d, max_days_back=max_days_back)
                vrs_cache[ds] = snap
                dates_vrs_ok += 1
                dates_vrs_live += 1
                upsert_vrs_rankings_snapshot(
                    str(snap.get("effective_date") or ds),
                    snap.get("rankings_by_team") or {},
                    str(snap.get("url") or ""),
                )
                logger.info(
                    "HLTV historical enrich date ok (VRS live): requested=%s effective=%s teams=%d",
                    ds,
                    snap.get("effective_date"),
                    len(snap.get("rankings_by_team") or {}),
                )
            except Exception:
                vrs_cache[ds] = {}
                logger.warning("HLTV historical enrich date failed (VRS): requested=%s", ds)

    updates: List[Dict[str, Any]] = []
    for r in target_rows:
        ds = str(r.get("match_date") or "")
        t1 = str(r.get("team1") or "")
        t2 = str(r.get("team2") or "")
        if not ds or not t1 or not t2:
            continue
        hk1 = _norm_team_name(t1)
        hk2 = _norm_team_name(t2)
        hltv_snap = hltv_cache.get(ds) or {}
        vrs_snap = vrs_cache.get(ds) or {}
        hltv_map = (hltv_snap.get("rankings_by_team") or {}) if isinstance(hltv_snap, dict) else {}
        vrs_map = (vrs_snap.get("rankings_by_team") or {}) if isinstance(vrs_snap, dict) else {}
        h1 = (hltv_map.get(hk1) or {}).get("points")
        h2 = (hltv_map.get(hk2) or {}).get("points")
        hr1 = (hltv_map.get(hk1) or {}).get("hltv_rating")
        hr2 = (hltv_map.get(hk2) or {}).get("hltv_rating")
        v1 = (vrs_map.get(hk1) or {}).get("points")
        v2 = (vrs_map.get(hk2) or {}).get("points")
        vr1 = (vrs_map.get(hk1) or {}).get("vrs_rank")
        vr2 = (vrs_map.get(hk2) or {}).get("vrs_rank")
        if h1 is None and h2 is None and v1 is None and v2 is None:
            continue
        updates.append(
            {
                "match_url": r.get("match_url"),
                "hltv_points_1": int(h1) if h1 is not None else None,
                "hltv_points_2": int(h2) if h2 is not None else None,
                "hltv_rank_1": int(hr1) if hr1 is not None else None,
                "hltv_rank_2": int(hr2) if hr2 is not None else None,
                "vrs_points_1": int(v1) if v1 is not None else None,
                "vrs_points_2": int(v2) if v2 is not None else None,
                "vrs_rank_1": int(vr1) if vr1 is not None else None,
                "vrs_rank_2": int(vr2) if vr2 is not None else None,
                "hltv_effective_date": hltv_snap.get("effective_date") if isinstance(hltv_snap, dict) else None,
                "vrs_effective_date": vrs_snap.get("effective_date") if isinstance(vrs_snap, dict) else None,
            }
        )

    matches_updated = update_hltv_results_points(updates)
    logger.info(
        "HLTV historical enrich complete: targeted=%d updates_prepared=%d matches_updated=%d dates=%d hltv_ok=%d(cache=%d live=%d) vrs_ok=%d(cache=%d live=%d)",
        len(target_rows),
        len(updates),
        int(matches_updated),
        len(unique_dates),
        dates_hltv_ok,
        dates_hltv_cache,
        dates_hltv_live,
        dates_vrs_ok,
        dates_vrs_cache,
        dates_vrs_live,
    )
    return {
        "status": "ok",
        "matches_scanned": len(rows),
        "matches_targeted": len(target_rows),
        "matches_updated": int(matches_updated),
        "dates_requested": len(unique_dates),
        "dates_hltv_ok": dates_hltv_ok,
        "dates_vrs_ok": dates_vrs_ok,
        "dates_hltv_cache": dates_hltv_cache,
        "dates_hltv_live": dates_hltv_live,
        "dates_vrs_cache": dates_vrs_cache,
        "dates_vrs_live": dates_vrs_live,
    }


@router.get("/hltv-results/winrate-model-current-points")
def get_winrate_model_current_points(limit: int = 1000, fallback_current: int = 1):
    """
    Fit winrate models from stored HLTV results using CURRENT team points:
      - HLTV points model
      - VRS points model
    """
    lim_in = int(limit)
    if lim_in <= 0:
        lim = max(1, count_hltv_results())
    else:
        lim = max(50, min(lim_in, 100000))
    rows = list_hltv_results(limit=lim, offset=0)
    if not rows:
        raise HTTPException(status_code=400, detail="No stored HLTV results. Import results first.")

    use_current_fallback = int(fallback_current) != 0
    hltv_points_by_team, vrs_points_by_team = _build_current_points_maps()

    fit_hltv_x: List[float] = []
    fit_hltv_y: List[int] = []
    fit_vrs_x: List[float] = []
    fit_vrs_y: List[int] = []
    fit_combo_hx: List[float] = []
    fit_combo_vx: List[float] = []
    fit_combo_y: List[int] = []
    match_rows: List[Dict[str, Any]] = []
    hist_hltv_count = 0
    hist_vrs_count = 0
    hist_both_count = 0
    fallback_hltv_count = 0
    fallback_vrs_count = 0

    for i, r in enumerate(rows):
        t1 = str(r.get("team1") or "").strip()
        t2 = str(r.get("team2") or "").strip()
        winner = str(r.get("winner") or "").strip()
        if not t1 or not t2:
            continue
        if winner not in (t1, t2):
            continue
        y = 1 if winner == t1 else 0

        k1 = _norm_team_name(t1)
        k2 = _norm_team_name(t2)
        hp1 = r.get("hltv_points_1")
        hp2 = r.get("hltv_points_2")
        vp1 = r.get("vrs_points_1")
        vp2 = r.get("vrs_points_2")
        had_hist_hltv = (hp1 is not None and hp2 is not None)
        had_hist_vrs = (vp1 is not None and vp2 is not None)
        if had_hist_hltv:
            hist_hltv_count += 1
        if had_hist_vrs:
            hist_vrs_count += 1
        if had_hist_hltv and had_hist_vrs:
            hist_both_count += 1

        if use_current_fallback:
            if hp1 is None:
                hp1 = hltv_points_by_team.get(k1)
            if hp2 is None:
                hp2 = hltv_points_by_team.get(k2)
            if vp1 is None:
                vp1 = vrs_points_by_team.get(k1)
            if vp2 is None:
                vp2 = vrs_points_by_team.get(k2)
        if (not had_hist_hltv) and (hp1 is not None and hp2 is not None):
            fallback_hltv_count += 1
        if (not had_hist_vrs) and (vp1 is not None and vp2 is not None):
            fallback_vrs_count += 1

        hx = None
        vx = None
        if hp1 is not None and hp2 is not None:
            hx = float(hp1 - hp2)
            fit_hltv_x.append(hx)
            fit_hltv_y.append(y)
        if vp1 is not None and vp2 is not None:
            vx = float(vp1 - vp2)
            fit_vrs_x.append(vx)
            fit_vrs_y.append(y)
        if hx is not None and vx is not None:
            fit_combo_hx.append(hx)
            fit_combo_vx.append(vx)
            fit_combo_y.append(y)

        match_rows.append(
            {
                "idx": i + 1,
                "team1": t1,
                "team2": t2,
                "winner": winner,
                "actual_team1_win": y,
                "match_date": r.get("match_date"),
                "hltv_points_1": hp1,
                "hltv_points_2": hp2,
                "vrs_points_1": vp1,
                "vrs_points_2": vp2,
                "hltv_effective_date": r.get("hltv_effective_date"),
                "vrs_effective_date": r.get("vrs_effective_date"),
                "hltv_points_source": ("historical" if had_hist_hltv else ("current_fallback" if (hp1 is not None and hp2 is not None) else "missing")),
                "vrs_points_source": ("historical" if had_hist_vrs else ("current_fallback" if (vp1 is not None and vp2 is not None) else "missing")),
                "hltv_x": hx,
                "vrs_x": vx,
                "event": r.get("event"),
                "match_url": r.get("match_url"),
            }
        )

    if len(fit_hltv_x) < 20 and len(fit_vrs_x) < 20:
        raise HTTPException(status_code=400, detail="Not enough matched teams with current points to fit models.")

    hltv_model = _fit_logistic_1d(fit_hltv_x, fit_hltv_y) if len(fit_hltv_x) >= 20 else None
    vrs_model = _fit_logistic_1d(fit_vrs_x, fit_vrs_y) if len(fit_vrs_x) >= 20 else None
    combo_model = _fit_logistic_2d(fit_combo_hx, fit_combo_vx, fit_combo_y) if len(fit_combo_hx) >= 20 else None

    hltv_brier_sum = 0.0
    hltv_n = 0
    vrs_brier_sum = 0.0
    vrs_n = 0
    combo_brier_sum = 0.0
    combo_n = 0
    for row in match_rows:
        if hltv_model and row["hltv_x"] is not None:
            p = _predict_logistic_1d(hltv_model, float(row["hltv_x"]))
            row["pred_hltv_team1_win"] = p
            hltv_brier_sum += (p - float(row["actual_team1_win"])) ** 2
            hltv_n += 1
        else:
            row["pred_hltv_team1_win"] = None

        if vrs_model and row["vrs_x"] is not None:
            p = _predict_logistic_1d(vrs_model, float(row["vrs_x"]))
            row["pred_vrs_team1_win"] = p
            vrs_brier_sum += (p - float(row["actual_team1_win"])) ** 2
            vrs_n += 1
        else:
            row["pred_vrs_team1_win"] = None

        if combo_model and row["hltv_x"] is not None and row["vrs_x"] is not None:
            p = _predict_logistic_2d(combo_model, float(row["hltv_x"]), float(row["vrs_x"]))
            row["pred_combo_team1_win"] = p
            combo_brier_sum += (p - float(row["actual_team1_win"])) ** 2
            combo_n += 1
        else:
            row["pred_combo_team1_win"] = None

    return {
        "status": "ok",
        "matches_considered": len(match_rows),
        "coverage": {
            "matches": len(match_rows),
            "historical_hltv": hist_hltv_count,
            "historical_vrs": hist_vrs_count,
            "historical_both": hist_both_count,
            "fallback_hltv": fallback_hltv_count,
            "fallback_vrs": fallback_vrs_count,
            "missing_hltv_after_fallback": max(0, len(match_rows) - (hist_hltv_count + fallback_hltv_count)),
            "missing_vrs_after_fallback": max(0, len(match_rows) - (hist_vrs_count + fallback_vrs_count)),
            "fallback_enabled": bool(use_current_fallback),
        },
        "hltv_model": {
            "available": bool(hltv_model),
            "samples": len(fit_hltv_x),
            "params": hltv_model,
            "brier": (hltv_brier_sum / hltv_n) if hltv_n > 0 else None,
        },
        "vrs_model": {
            "available": bool(vrs_model),
            "samples": len(fit_vrs_x),
            "params": vrs_model,
            "brier": (vrs_brier_sum / vrs_n) if vrs_n > 0 else None,
        },
        "combo_model": {
            "available": bool(combo_model),
            "samples": len(fit_combo_hx),
            "params": combo_model,
            "brier": (combo_brier_sum / combo_n) if combo_n > 0 else None,
        },
        "rows": match_rows,
    }


@router.get("/{event_id}")
def fetch_event(event_id: int):
    event = get_event_detail(int(event_id))
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/activate")
def activate_event(payload: Dict[str, Any]):
    event_id_raw = payload.get("event_id")
    if event_id_raw is None:
        raise HTTPException(status_code=400, detail="event_id is required")
    if not str(event_id_raw).isdigit():
        raise HTTPException(status_code=400, detail="event_id must be numeric")
    event_id = int(event_id_raw)

    event = get_event_detail(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    set_active_event(event_id)
    return {"status": "ok", "active_event_id": event_id}


@router.get("/hltv-results/match-details")
def get_hltv_result_match_details(match_url: str):
    url = str(match_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="match_url is required")

    stored = get_hltv_result_by_url(url)
    if stored is None:
        # Fallback to canonical match-id lookup if client passes variant URL.
        m = re.search(r"/matches/(\d+)/", url)
        if m:
            mid = int(m.group(1))
            rows = list_hltv_results(limit=500, offset=0)
            stored = next((r for r in rows if int(r.get("match_id") or 0) == mid), None)

    maps = []
    try:
        details = get_hltv_match_details(url)
        maps = details.get("maps") or []
        if maps and stored and stored.get("match_url"):
            set_hltv_result_maps(str(stored.get("match_url")), maps)
    except HLTVRankingError:
        maps = []
    except Exception:
        maps = []

    if not maps and isinstance(stored, dict) and stored.get("maps_json"):
        try:
            import json
            parsed = json.loads(str(stored.get("maps_json")))
            if isinstance(parsed, list):
                maps = parsed
        except Exception:
            maps = []

    return {
        "match_url": url,
        "stored": stored,
        "maps": maps,
    }


@router.post("/import-hltv-event")
def import_hltv_event(payload: Dict[str, Any]):
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id.isdigit():
        raise HTTPException(status_code=400, detail="event_id must be numeric")

    url = f"https://www.hltv.org/fantasy/{event_id}/leagues/create/json"
    try:
        data = fetch_hltv_json(url)
    except HLTVBrowserError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV data with SeleniumBase UC: {exc}")

    money = data.get("moneyDraftData", {})
    if not money:
        raise HTTPException(status_code=400, detail="moneyDraftData missing in response")

    counts = _import_money_draft_data(money, event_id=int(event_id))
    return {"status": "ok", **counts, "event_id": int(event_id), "active_event_id": int(event_id)}
