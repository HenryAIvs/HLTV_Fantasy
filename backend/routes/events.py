import math
import random
import re
import json
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
import logging

from fastapi import APIRouter, HTTPException
from dateutil.relativedelta import relativedelta

from backend.routes.players import _INTERRUPTION_BOILERPLATE
from backend.routes.admin import _extract_hltv_event_ref_from_fantasy, _import_money_draft_data
from backend.services.hltv_rankings import (
    get_recent_hltv_results,
    get_hltv_match_details,
    get_hltv_team_map_stats_for_range,
    get_all_hltv_rankings_on_or_before_date,
    get_all_vrs_rankings_on_or_before_date,
    HLTVRankingError,
)
from backend.data.db import connect as event_db_connect
from backend.data.event_db import (
    get_historical_team_map_stats_keys,
    clear_hltv_results,
    count_hltv_results,
    dedupe_hltv_results_by_match_id,
    get_cached_hltv_rankings_on_or_before_date,
    get_cached_vrs_rankings_on_or_before_date,
    get_active_event_id,
    get_event_detail,
    get_hltv_result_by_url,
    get_imported_match_ids,
    get_historical_team_map_stats,
    list_hltv_results,
    set_hltv_result_maps,
    list_events,
    set_active_event,
    upsert_historical_team_map_stats,
    upsert_hltv_rankings_snapshot,
    upsert_vrs_rankings_snapshot,
    update_hltv_results_points,
    upsert_hltv_results,
)
from backend.data.team_db import get_all_teams
from backend.services.hltv_browser import HLTVBrowserError, fetch_hltv_json

router = APIRouter()
logger = logging.getLogger(__name__)
HLTV_RESULTS_IMPORT_JOBS: Dict[str, Dict[str, Any]] = {}
HLTV_RESULTS_IMPORT_JOBS_LOCK = threading.Lock()
HLTV_RESULTS_IMPORT_JOB_DIR = Path(__file__).resolve().parents[2] / ".runtime" / "hltv_results_import_jobs"


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


ROUND_SHARE_FEATURES: Tuple[Tuple[str, str], ...] = (
    ("hltv_gap", "hltv_gap"),
    ("hltv_level", "hltv_level"),
    ("hltv_gap_level", "hltv_gap_level"),
    ("vrs_gap", "vrs_gap"),
    ("vrs_level", "vrs_level"),
    ("vrs_gap_level", "vrs_gap_level"),
    ("map_win_gap", "map_win_gap"),
    ("pick_gap", "pick_gap"),
    ("ban_gap", "ban_gap"),
    ("played_pct_gap", "played_pct_gap"),
    ("map_stats_available", "map_stats_available"),
)


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


def _predict_logistic_2d(
    model: Dict[str, float],
    x1: float,
    x2: float,
    x3: float | None = None,
    x4: float | None = None,
    map_win_a: float = 0.0,
    map_win_b: float = 0.0,
    pick_a: float = 0.0,
    pick_b: float = 0.0,
    ban_a: float = 0.0,
    ban_b: float = 0.0,
    played_pct_a: float = 0.0,
    played_pct_b: float = 0.0,
) -> float:
    if "b_hltv_gap" in model or "b_map_win_gap" in model or "b_pick_gap" in model or "b_ban_gap" in model:
        hltv_a = float(x1)
        hltv_b = float(x2)
        vrs_a = float(x3 if x3 is not None else 0.0)
        vrs_b = float(x4 if x4 is not None else 0.0)
        values = {
            "hltv_gap": hltv_b - hltv_a,
            "hltv_level": (hltv_a + hltv_b) / 2.0,
            "hltv_gap_level": (hltv_b - hltv_a) * ((hltv_a + hltv_b) / 2.0),
            "vrs_gap": vrs_b - vrs_a,
            "vrs_level": (vrs_a + vrs_b) / 2.0,
            "vrs_gap_level": (vrs_b - vrs_a) * ((vrs_a + vrs_b) / 2.0),
            "map_win_gap": float(map_win_a) - float(map_win_b),
            "pick_gap": float(pick_a) - float(pick_b),
            "ban_gap": float(ban_a) - float(ban_b),
            "played_pct_gap": float(played_pct_a) - float(played_pct_b),
            "map_stats_available": 1.0
            if any(
                abs(float(v)) > 1e-12
                for v in (map_win_a, map_win_b, pick_a, pick_b, ban_a, ban_b, played_pct_a, played_pct_b)
            )
            else 0.0,
        }
        z = float(model["a"])
        for name, _sample_key in ROUND_SHARE_FEATURES:
            mean = float(model.get(f"mean_{name}", 0.0))
            std = float(model.get(f"std_{name}", 1.0)) or 1.0
            z += float(model.get(f"b_{name}", 0.0)) * ((values[name] - mean) / std)
        return _sigmoid(z)

    z1 = (x1 - float(model["mean_hltv"])) / float(model["std_hltv"] if model["std_hltv"] != 0 else 1.0)
    z2 = (x2 - float(model["mean_vrs"])) / float(model["std_vrs"] if model["std_vrs"] != 0 else 1.0)
    return _sigmoid(float(model["a"]) + float(model["b_hltv"]) * z1 + float(model["b_vrs"]) * z2)


def _fit_round_share_logistic_2d(samples: List[Dict[str, float]], include_map_stats: bool = True) -> Dict[str, float]:
    if len(samples) < 20:
        raise ValueError("Not enough round-share samples.")
    feature_defs = ROUND_SHARE_FEATURES if include_map_stats else ROUND_SHARE_FEATURES[:6]
    ys = [float(s["round_share"]) for s in samples]
    ws = [max(1.0, float(s.get("weight") or 1.0)) for s in samples]
    total_w = max(1.0, sum(ws))
    raw_features: Dict[str, List[float]] = {
        name: [float(sample.get(sample_key) or 0.0) for sample in samples]
        for name, sample_key in feature_defs
    }
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    z_features: Dict[str, List[float]] = {}
    for name, values in raw_features.items():
        mean = sum(x * w for x, w in zip(values, ws)) / total_w
        var = sum(w * (x - mean) ** 2 for x, w in zip(values, ws)) / total_w
        std = math.sqrt(var) if var > 1e-12 else 1.0
        means[name] = mean
        stds[name] = std
        z_features[name] = [(x - mean) / std for x in values]

    a = 0.0
    coefs: Dict[str, float] = {name: 0.0 for name, _sample_key in feature_defs}
    l2 = 1e-4
    lr = 0.03
    for _ in range(3500):
        ga = 0.0
        gradients: Dict[str, float] = {name: 0.0 for name, _sample_key in feature_defs}
        for idx, (y, w) in enumerate(zip(ys, ws)):
            z = a
            for name, _sample_key in feature_defs:
                z += coefs[name] * z_features[name][idx]
            p = _sigmoid(z)
            e = (p - y) * w
            ga += e
            for name, _sample_key in feature_defs:
                gradients[name] += e * z_features[name][idx]
        ga = ga / total_w + l2 * a
        a -= lr * ga
        for name, _sample_key in feature_defs:
            gradients[name] = gradients[name] / total_w + l2 * coefs[name]
            coefs[name] -= lr * gradients[name]

    model = {
        "a": a,
        "samples": len(samples),
        "rounds": int(round(total_w)),
        "feature_set": "with_map_data" if include_map_stats else "rank_only",
    }
    for name, _sample_key in feature_defs:
        model[f"b_{name}"] = coefs[name]
        model[f"mean_{name}"] = means[name]
        model[f"std_{name}"] = stds[name]
    return model


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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hltv_results_import_job_path(job_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "", str(job_id or ""))
    if not safe:
        safe = "job"
    return HLTV_RESULTS_IMPORT_JOB_DIR / f"{safe}.json"


def _persist_hltv_results_import_job(job_id: str, job: Dict[str, Any] | None = None) -> None:
    try:
        HLTV_RESULTS_IMPORT_JOB_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = dict(job or HLTV_RESULTS_IMPORT_JOBS.get(job_id) or {})
        if not snapshot:
            return
        snapshot.pop("thread", None)
        tmp = _hltv_results_import_job_path(job_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=True), encoding="utf-8")
        tmp.replace(_hltv_results_import_job_path(job_id))
    except Exception as exc:
        logger.warning("Failed to persist HLTV results import job %s: %s", job_id, exc)


def _load_hltv_results_import_job(job_id: str) -> Dict[str, Any] | None:
    path = _hltv_results_import_job_path(job_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("Failed to load HLTV results import job %s: %s", job_id, exc)
    return None


def _latest_hltv_results_import_job() -> Dict[str, Any] | None:
    try:
        files = sorted(HLTV_RESULTS_IMPORT_JOB_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return None
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _hydrate_hltv_results_import_job(job_id: str) -> Dict[str, Any] | None:
    with HLTV_RESULTS_IMPORT_JOBS_LOCK:
        existing = HLTV_RESULTS_IMPORT_JOBS.get(job_id)
        if existing:
            return dict(existing)
    loaded = _load_hltv_results_import_job(job_id)
    if not loaded:
        return None
    loaded.pop("thread", None)
    status = str(loaded.get("status") or "")
    if status in {"queued", "running", "pausing"}:
        loaded["status"] = "queued"
        loaded["phase"] = loaded.get("phase") or "queued"
        loaded["current"] = "Restarting after app restart"
        loaded["pause_requested"] = False
        loaded["updated_at"] = _utc_now_iso()
    with HLTV_RESULTS_IMPORT_JOBS_LOCK:
        HLTV_RESULTS_IMPORT_JOBS[job_id] = loaded
    _persist_hltv_results_import_job(job_id, loaded)
    return dict(loaded)


def _normalize_hltv_results_import_payload(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    body = payload or {}
    start_offset = max(0, int(body.get("start_offset", 0)))
    page_stride = max(1, int(body.get("page_stride", 100)))
    per_page_limit = max(1, min(100, int(body.get("per_page_limit", 100))))
    import_mode = str(body.get("import_mode") or body.get("mode") or "").strip().lower()
    until_date = str(body.get("until_date") or "").strip()
    if until_date:
        try:
            until_date = date.fromisoformat(until_date).isoformat()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="until_date must be YYYY-MM-DD") from exc
    if import_mode not in {"max_pages", "until_date"}:
        import_mode = "until_date" if until_date else "max_pages"
    if import_mode == "until_date" and not until_date:
        raise HTTPException(status_code=400, detail="until_date is required in until-date import mode")
    pages = max(1, int(body.get("pages", 1)))
    try:
        max_hltv_rank = max(0, min(500, int(str(body.get("max_hltv_rank") or "0").strip() or "0")))
    except Exception:
        max_hltv_rank = 0
    rank_filter_mode = str(body.get("rank_filter_mode") or "both").strip().lower()
    if rank_filter_mode not in {"both", "either"}:
        rank_filter_mode = "both"
    offsets = [start_offset + i * page_stride for i in range(pages)] if import_mode == "max_pages" else []
    return {
        "pages": pages,
        "start_offset": start_offset,
        "page_stride": page_stride,
        "per_page_limit": per_page_limit,
        "max_hltv_rank": max_hltv_rank,
        "rank_filter_mode": rank_filter_mode,
        "import_mode": import_mode,
        "until_date": until_date,
        "offsets": offsets,
    }


def _attach_hltv_ranks_to_result_rows(
    rows: List[Dict[str, Any]],
    max_days_back: int = 7,
    progress: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, int]:
    unique_dates = sorted({str(r.get("match_date")) for r in rows if r.get("match_date")})
    hltv_cache: Dict[str, Dict[str, Any]] = {}
    dates_ok = 0
    dates_total = len(unique_dates)
    for idx, ds in enumerate(unique_dates):
        if progress:
            progress(
                {
                    "phase": "filtering_by_rank",
                    "current": f"Checking ranking date {idx + 1} / {dates_total}: {ds}",
                    "ranking_dates_done": idx,
                    "ranking_dates_total": dates_total,
                }
            )
        cached = get_cached_hltv_rankings_on_or_before_date(ds, max_days_back=max_days_back)
        if cached and (cached.get("rankings_by_team") or {}):
            hltv_cache[ds] = cached
            dates_ok += 1
        else:
            try:
                snap = get_all_hltv_rankings_on_or_before_date(date.fromisoformat(ds), max_days_back=max_days_back)
                hltv_cache[ds] = snap
                dates_ok += 1
                upsert_hltv_rankings_snapshot(
                    str(snap.get("effective_date") or ds),
                    snap.get("rankings_by_team") or {},
                    str(snap.get("url") or ""),
                )
            except Exception:
                hltv_cache[ds] = {}
        if progress:
            progress(
                {
                    "phase": "filtering_by_rank",
                    "current": f"Checked ranking date {idx + 1} / {dates_total}: {ds}",
                    "ranking_dates_done": idx + 1,
                    "ranking_dates_total": dates_total,
                }
            )

    ranked_rows = 0
    for row in rows:
        ds = str(row.get("match_date") or "")
        if not ds:
            continue
        snap = hltv_cache.get(ds) or {}
        hltv_map = (snap.get("rankings_by_team") or {}) if isinstance(snap, dict) else {}
        team1 = str(row.get("team1") or "")
        team2 = str(row.get("team2") or "")
        r1 = (hltv_map.get(_norm_team_name(team1)) or {}).get("hltv_rating")
        r2 = (hltv_map.get(_norm_team_name(team2)) or {}).get("hltv_rating")
        p1 = (hltv_map.get(_norm_team_name(team1)) or {}).get("points")
        p2 = (hltv_map.get(_norm_team_name(team2)) or {}).get("points")
        if r1 is not None:
            row["hltv_rank_1"] = int(r1)
        if r2 is not None:
            row["hltv_rank_2"] = int(r2)
        if p1 is not None:
            row["hltv_points_1"] = int(p1)
        if p2 is not None:
            row["hltv_points_2"] = int(p2)
        if r1 is not None or r2 is not None:
            row["hltv_effective_date"] = snap.get("effective_date") if isinstance(snap, dict) else None
            ranked_rows += 1
    return {"dates_requested": len(unique_dates), "dates_ok": dates_ok, "ranked_rows": ranked_rows}


def _row_passes_hltv_rank_filter(row: Dict[str, Any], max_rank: int, mode: str) -> bool:
    if max_rank <= 0:
        return True
    ranks = []
    for key in ("hltv_rank_1", "hltv_rank_2"):
        try:
            value = int(row.get(key))
            if value > 0:
                ranks.append(value)
        except Exception:
            pass
    if mode == "either":
        return any(rank <= max_rank for rank in ranks)
    return len(ranks) == 2 and all(rank <= max_rank for rank in ranks)


def _row_is_on_or_after(row: Dict[str, Any], cutoff: date) -> bool:
    raw = str(row.get("match_date") or "").strip()
    if not raw:
        return True
    try:
        return date.fromisoformat(raw) >= cutoff
    except Exception:
        return True


def _import_hltv_results_work(
    payload: Dict[str, Any] | None,
    progress: Callable[[Dict[str, Any]], None] | None = None,
    job_id: str | None = None,
) -> Dict[str, Any]:
    normalized = _normalize_hltv_results_import_payload(payload)
    pages = int(normalized["pages"])
    per_page_limit = int(normalized["per_page_limit"])
    offsets = list(normalized["offsets"])
    until_date = str(normalized.get("until_date") or "")
    until_date_obj = date.fromisoformat(until_date) if until_date else None
    import_mode = str(normalized.get("import_mode") or ("until_date" if until_date_obj else "max_pages"))
    max_hltv_rank = int(normalized.get("max_hltv_rank") or 0)
    rank_filter_mode = str(normalized.get("rank_filter_mode") or "both")

    job_state = _hydrate_hltv_results_import_job(job_id) if job_id else None
    rows: List[Dict[str, Any]] = list((job_state or {}).get("rows") or [])
    fetched_count = int((job_state or {}).get("fetched_count") or len(rows))
    rank_filtered_out = 0
    date_filtered_out = int((job_state or {}).get("date_filtered_out") or 0)
    rank_filter_meta = (job_state or {}).get("rank_filter_meta") or {"dates_requested": 0, "dates_ok": 0, "ranked_rows": 0}
    processed_units = int((job_state or {}).get("processed_units") or 0)
    total_units = int((job_state or {}).get("total_units") or (0 if import_mode == "until_date" else max(1, pages)))
    page_index = int((job_state or {}).get("page_index") or 0)
    offset = int((job_state or {}).get("next_offset") or normalized["start_offset"])
    fetch_complete = bool((job_state or {}).get("fetch_complete"))
    detail_index = int((job_state or {}).get("detail_index") or 0)
    detail_complete = bool((job_state or {}).get("detail_complete"))

    def pause_requested() -> bool:
        if not job_id:
            return False
        with HLTV_RESULTS_IMPORT_JOBS_LOCK:
            job = HLTV_RESULTS_IMPORT_JOBS.get(job_id) or {}
            return bool(job.get("pause_requested"))

    def cancel_requested() -> bool:
        if not job_id:
            return False
        with HLTV_RESULTS_IMPORT_JOBS_LOCK:
            job = HLTV_RESULTS_IMPORT_JOBS.get(job_id) or {}
            return bool(job.get("cancel_requested"))

    def stop_if_requested(where: str):
        """Return a terminal status dict when cancel/pause was requested, else None."""
        if cancel_requested():
            checkpoint(status="canceled", phase="canceled", current=f"Canceled {where}")
            return {"status": "canceled"}
        if pause_requested():
            checkpoint(status="paused", phase="paused", current=f"Paused {where}")
            return {"status": "paused"}
        return None

    def checkpoint(**updates: Any) -> None:
        if not job_id:
            return
        snapshot_updates = {
            "payload": normalized,
            "rows": rows,
            "fetched_count": fetched_count,
            "processed_units": processed_units,
            "total_units": total_units,
            "page_index": page_index,
            "next_offset": offset,
            "fetch_complete": fetch_complete,
            "detail_index": detail_index,
            "detail_complete": detail_complete,
            "date_filtered_out": date_filtered_out,
            "rank_filter_meta": rank_filter_meta,
        }
        snapshot_updates.update(updates)
        _update_hltv_results_import_job(job_id, **snapshot_updates)

    def report(**updates: Any) -> None:
        if progress is None:
            return
        progress(
            {
                "processed_units": processed_units,
                "total_units": total_units,
                "fetched": fetched_count or len(rows),
                "kept": len(rows),
                "rank_filtered_out": rank_filtered_out,
                "import_mode": import_mode,
                **updates,
            }
        )

    stopped = stop_if_requested("before starting")
    if stopped:
        return stopped

    if not fetch_complete:
        report(phase="fetching_results")
    while not fetch_complete:
        stopped = stop_if_requested(f"before page {page_index + 1}")
        if stopped:
            return stopped
        page_index += 1
        try:
            snapshot = get_recent_hltv_results(limit=per_page_limit, offsets=[offset])
        except HLTVRankingError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        page_rows = snapshot.get("results") or []
        rows.extend(page_rows)
        fetched_count += len(page_rows)
        processed_units += 1
        page_dates = []
        for row in page_rows:
            try:
                page_dates.append(date.fromisoformat(str(row.get("match_date") or "")))
            except Exception:
                pass
        report(
            phase="fetching_results",
            current=(
                f"Page {page_index} until {until_date}"
                if import_mode == "until_date"
                else f"Page {page_index} / {pages}"
            ),
            page_index=page_index,
            pages=pages if import_mode == "max_pages" else None,
        )
        if import_mode == "until_date":
            if not page_rows:
                fetch_complete = True
                break
            if page_dates and min(page_dates) <= until_date_obj:
                fetch_complete = True
                break
            offset += int(normalized["page_stride"])
            checkpoint(status="running", phase="fetching_results")
            continue
        if page_index >= pages:
            fetch_complete = True
            break
        offset += int(normalized["page_stride"])
        checkpoint(status="running", phase="fetching_results")

    checkpoint(status="running", phase="fetching_results")

    if not detail_complete and detail_index <= 0 and until_date_obj is not None and not (job_state or {}).get("date_filter_applied"):
        before_date_filter = len(rows)
        rows = [r for r in rows if _row_is_on_or_after(r, until_date_obj)]
        date_filtered_out = max(0, before_date_filter - len(rows))
        checkpoint(date_filter_applied=True)

    if not detail_complete and detail_index <= 0 and rows and not (job_state or {}).get("existing_filter_applied"):
        # Skip matches that were already fully imported (map details stored).
        imported_ids = get_imported_match_ids()
        before_existing_filter = len(rows)
        rows = [
            r
            for r in rows
            if not (r.get("match_id") is not None and int(r.get("match_id") or 0) in imported_ids)
        ]
        skipped_existing = max(0, before_existing_filter - len(rows))
        checkpoint(existing_filter_applied=True, skipped_existing=skipped_existing)
        report(
            phase="fetching_results",
            current=f"Skipped {skipped_existing} already-imported matches",
            skipped_existing=skipped_existing,
        )
    else:
        skipped_existing = int((job_state or {}).get("skipped_existing") or 0)

    if not detail_complete and detail_index <= 0 and max_hltv_rank > 0 and rows and not (job_state or {}).get("rank_filter_applied"):
        stopped = stop_if_requested("before rank filtering")
        if stopped:
            return stopped
        rank_filter_dates_total = len({str(r.get("match_date")) for r in rows if r.get("match_date")})
        total_units = max(
            1,
            processed_units
            + rank_filter_dates_total
            + max(0, len(rows) - detail_index)
            + 3,
        )
        checkpoint(total_units=total_units, phase="filtering_by_rank")
        report(
            phase="filtering_by_rank",
            current=f"Checking HLTV rank dates 0 / {rank_filter_dates_total}",
            total_units=total_units,
            ranking_dates_done=0,
            ranking_dates_total=rank_filter_dates_total,
        )

        def rank_filter_progress(updates: Dict[str, Any]) -> None:
            nonlocal processed_units
            done = int(updates.get("ranking_dates_done") or 0)
            processed = processed_units + done
            report(processed_units=processed, total_units=total_units, **updates)
            checkpoint(
                status="running",
                processed_units=processed,
                total_units=total_units,
                **updates,
            )

        rank_filter_meta = _attach_hltv_ranks_to_result_rows(rows, progress=rank_filter_progress)
        processed_units += rank_filter_dates_total
        before_filter = len(rows)
        rows = [r for r in rows if _row_passes_hltv_rank_filter(r, max_hltv_rank, rank_filter_mode)]
        rank_filtered_out = max(0, before_filter - len(rows))
        checkpoint(
            rank_filter_applied=True,
            rank_filtered_out=rank_filtered_out,
            processed_units=processed_units,
            current=f"Checked HLTV rank dates {rank_filter_dates_total} / {rank_filter_dates_total}",
        )

    total_units = max(1, processed_units + max(0, len(rows) - detail_index) + 3)
    checkpoint(total_units=total_units, phase="fetching_match_details")
    report(phase="fetching_match_details", detail_total=len(rows), detail_done=detail_index)
    for idx in range(detail_index, len(rows)):
        stopped = stop_if_requested(f"at match {idx + 1} / {len(rows)}")
        if stopped:
            return stopped
        r = rows[idx]
        url = str(r.get("match_url") or "").strip()
        if url:
            try:
                stored = get_hltv_result_by_url(url)
                if stored and stored.get("maps_json"):
                    r["maps_json"] = str(stored.get("maps_json") or "[]")
                    if stored.get("veto_json"):
                        r["veto_json"] = str(stored.get("veto_json"))
                    if stored.get("player_stats_json"):
                        r["player_stats_json"] = str(stored.get("player_stats_json"))
                else:
                    md = get_hltv_match_details(url)
                    maps = md.get("maps") or []
                    if maps:
                        r["maps_json"] = json.dumps(maps)
                    veto = md.get("veto") or []
                    if veto:
                        r["veto_json"] = json.dumps(veto)
                    player_stats = md.get("player_stats") or []
                    if player_stats:
                        r["player_stats_json"] = json.dumps(player_stats)
            except Exception:
                pass
        processed_units += 1
        detail_index = idx + 1
        report(
            phase="fetching_match_details",
            current=f"Match {detail_index} / {len(rows)}",
            detail_done=detail_index,
            detail_total=len(rows),
        )
        checkpoint(status="running", phase="fetching_match_details", current=f"Match {detail_index} / {len(rows)}")

    detail_complete = True
    checkpoint(detail_complete=True, phase="storing_results", current="Saving matches")
    report(phase="storing_results", current="Saving matches")
    stored = upsert_hltv_results(rows)
    processed_units += 1
    checkpoint(phase="deduping_results", current="Removing duplicates")
    report(phase="deduping_results", current="Removing duplicates")
    deduped_removed = dedupe_hltv_results_by_match_id()
    processed_units += 1
    checkpoint(phase="enriching_points", current="Enriching historical points")
    report(phase="enriching_points", current="Enriching historical points")
    enrich_limit = max(1, count_hltv_results())
    enrich_rows = list_hltv_results(limit=enrich_limit, offset=0)
    enrich_target_rows = [
        r
        for r in enrich_rows
        if str(r.get("match_date") or "").strip()
        and not any(
            r.get(k) is not None
            for k in ("hltv_points_1", "hltv_points_2", "vrs_points_1", "vrs_points_2")
        )
    ]
    enrich_ranking_dates_total = len({str(r.get("match_date")) for r in enrich_target_rows if r.get("match_date")}) * 2
    total_units = max(1, processed_units + max(1, enrich_ranking_dates_total))
    checkpoint(
        phase="enriching_points",
        current=f"Checking ranking dates 0 / {enrich_ranking_dates_total}",
        total_units=total_units,
        ranking_dates_done=0,
        ranking_dates_total=enrich_ranking_dates_total,
    )
    report(
        phase="enriching_points",
        current=f"Checking ranking dates 0 / {enrich_ranking_dates_total}",
        total_units=total_units,
        ranking_dates_done=0,
        ranking_dates_total=enrich_ranking_dates_total,
    )

    def enrich_progress(updates: Dict[str, Any]) -> None:
        done = int(updates.get("ranking_dates_done") or 0)
        processed = processed_units + done
        report(processed_units=processed, total_units=total_units, **updates)
        checkpoint(
            status="running",
            processed_units=processed,
            total_units=total_units,
            **updates,
        )

    enrich = _enrich_hltv_results_historical_points(
        {
            "limit": enrich_limit,
            "max_days_back": 7,
            "refresh_all": False,
            "auto_backfill_dates": True,
        },
        progress=enrich_progress,
    )
    processed_units += max(1, enrich_ranking_dates_total)

    result = {
        "status": "ok",
        "requested_pages": pages,
        "fetched_pages": page_index,
        "offsets": offsets,
        "fetched": int(fetched_count),
        "kept": len(rows),
        "rank_filtered_out": int(rank_filtered_out),
        "date_filtered_out": int(date_filtered_out),
        "skipped_existing": int(skipped_existing),
        "max_hltv_rank": max_hltv_rank,
        "rank_filter_mode": rank_filter_mode,
        "import_mode": import_mode,
        "until_date": until_date,
        "rank_filter_meta": rank_filter_meta,
        "inserted": int(stored.get("inserted", 0)),
        "updated": int(stored.get("updated", 0)),
        "deduped_removed": int(deduped_removed),
        "enriched_matches": int((enrich or {}).get("matches_updated", 0)),
    }
    report(phase="completed", current="Completed", result=result)
    return result


def _update_hltv_results_import_job(job_id: str, **updates: Any) -> None:
    snapshot: Dict[str, Any] | None = None
    with HLTV_RESULTS_IMPORT_JOBS_LOCK:
        job = HLTV_RESULTS_IMPORT_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = _utc_now_iso()
        snapshot = dict(job)
    _persist_hltv_results_import_job(job_id, snapshot)


def _run_hltv_results_import_job(job_id: str, payload: Dict[str, Any] | None) -> None:
    _update_hltv_results_import_job(job_id, status="running", phase="fetching_results", pause_requested=False)
    try:
        result = _import_hltv_results_work(payload, lambda updates: _update_hltv_results_import_job(job_id, **updates), job_id=job_id)
        if (result or {}).get("status") == "paused":
            _update_hltv_results_import_job(job_id, status="paused", phase="paused", current="Paused", pause_requested=False)
            return
        if (result or {}).get("status") == "canceled":
            _update_hltv_results_import_job(
                job_id,
                status="canceled",
                phase="canceled",
                current="Canceled",
                pause_requested=False,
                cancel_requested=False,
                finished_at=_utc_now_iso(),
            )
            return
        with HLTV_RESULTS_IMPORT_JOBS_LOCK:
            total_units = int((HLTV_RESULTS_IMPORT_JOBS.get(job_id) or {}).get("total_units") or 1)
        _update_hltv_results_import_job(
            job_id,
            status="completed",
            phase="completed",
            processed_units=total_units,
            result=result,
            error="",
            finished_at=_utc_now_iso(),
        )
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _update_hltv_results_import_job(
            job_id,
            status="failed",
            phase="failed",
            error=str(detail or "HLTV results import failed."),
            finished_at=_utc_now_iso(),
        )


def _start_hltv_results_import_thread(job_id: str) -> bool:
    with HLTV_RESULTS_IMPORT_JOBS_LOCK:
        job = HLTV_RESULTS_IMPORT_JOBS.get(job_id)
        if not job:
            return False
        status = str(job.get("status") or "")
        if status == "running":
            return True
        payload = dict(job.get("payload") or {})
        job["status"] = "queued"
        job["pause_requested"] = False
        job["updated_at"] = _utc_now_iso()
        snapshot = dict(job)
    _persist_hltv_results_import_job(job_id, snapshot)
    worker = threading.Thread(target=_run_hltv_results_import_job, args=(job_id, payload), daemon=True)
    worker.start()
    return True


@router.post("/hltv-results/import")
def import_hltv_results(payload: Dict[str, Any] | None = None):
    return _import_hltv_results_work(payload)


@router.post("/hltv-results/import/start")
def start_hltv_results_import(payload: Dict[str, Any] | None = None):
    with HLTV_RESULTS_IMPORT_JOBS_LOCK:
        for existing_id, existing_job in HLTV_RESULTS_IMPORT_JOBS.items():
            if existing_job.get("status") in {"queued", "running", "pausing", "canceling"}:
                return {"job_id": existing_id, "status": existing_job.get("status"), "reused": True}
        job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        normalized = _normalize_hltv_results_import_payload(payload)
        HLTV_RESULTS_IMPORT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "phase": "queued",
            "processed_units": 0,
            "total_units": 0 if normalized.get("import_mode") == "until_date" else int(normalized.get("pages", 1)),
            "fetched": 0,
            "error": "",
            "result": None,
            "rows": [],
            "page_index": 0,
            "next_offset": int(normalized.get("start_offset") or 0),
            "fetch_complete": False,
            "detail_index": 0,
            "detail_complete": False,
            "pause_requested": False,
            "cancel_requested": False,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "payload": normalized,
        }
        snapshot = dict(HLTV_RESULTS_IMPORT_JOBS[job_id])

    _persist_hltv_results_import_job(job_id, snapshot)
    _start_hltv_results_import_thread(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/hltv-results/import/job/{job_id}")
def get_hltv_results_import_job(job_id: str):
    data = _hydrate_hltv_results_import_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job_id not found")
    if data.get("status") == "queued":
        _start_hltv_results_import_thread(job_id)
        data = _hydrate_hltv_results_import_job(job_id) or data
    processed = int(data.get("processed_units") or 0)
    total = int(data.get("total_units") or 0)
    data["progress"] = min(1.0, max(0.0, processed / total)) if total > 0 else 0.0
    data.pop("rows", None)
    return data


@router.get("/hltv-results/import/latest")
def get_latest_hltv_results_import_job():
    with HLTV_RESULTS_IMPORT_JOBS_LOCK:
        active = sorted(
            [dict(job) for job in HLTV_RESULTS_IMPORT_JOBS.values()],
            key=lambda job: str(job.get("updated_at") or ""),
            reverse=True,
        )
    data = active[0] if active else _latest_hltv_results_import_job()
    if not data:
        return {"exists": False}
    job_id = str(data.get("job_id") or "")
    hydrated = _hydrate_hltv_results_import_job(job_id) if job_id else data
    if hydrated and hydrated.get("status") == "queued":
        _start_hltv_results_import_thread(job_id)
        hydrated = _hydrate_hltv_results_import_job(job_id) or hydrated
    out = dict(hydrated or data)
    processed = int(out.get("processed_units") or 0)
    total = int(out.get("total_units") or 0)
    out["exists"] = True
    out["progress"] = min(1.0, max(0.0, processed / total)) if total > 0 else 0.0
    out.pop("rows", None)
    return out


@router.post("/hltv-results/import/job/{job_id}/pause")
def pause_hltv_results_import_job(job_id: str):
    data = _hydrate_hltv_results_import_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job_id not found")
    status = str(data.get("status") or "")
    if status in {"completed", "failed", "canceled", "paused"}:
        return get_hltv_results_import_job(job_id)
    _update_hltv_results_import_job(job_id, status="pausing", pause_requested=True, current="Pausing after current request")
    return get_hltv_results_import_job(job_id)


@router.post("/hltv-results/import/job/{job_id}/resume")
def resume_hltv_results_import_job(job_id: str):
    data = _hydrate_hltv_results_import_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job_id not found")
    status = str(data.get("status") or "")
    if status in {"completed", "canceled", "running"}:
        return get_hltv_results_import_job(job_id)
    _update_hltv_results_import_job(job_id, status="queued", pause_requested=False, cancel_requested=False, current="Resuming")
    _start_hltv_results_import_thread(job_id)
    return get_hltv_results_import_job(job_id)


@router.post("/hltv-results/import/job/{job_id}/cancel")
def cancel_hltv_results_import_job(job_id: str):
    data = _hydrate_hltv_results_import_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job_id not found")
    status = str(data.get("status") or "")
    if status in {"completed", "failed", "canceled"}:
        return get_hltv_results_import_job(job_id)
    if status in {"queued", "paused"}:
        # No worker is mid-request; cancel immediately.
        _update_hltv_results_import_job(
            job_id,
            status="canceled",
            phase="canceled",
            current="Canceled",
            pause_requested=False,
            cancel_requested=False,
            finished_at=_utc_now_iso(),
        )
        return get_hltv_results_import_job(job_id)
    _update_hltv_results_import_job(
        job_id, status="canceling", cancel_requested=True, current="Canceling after current request"
    )
    return get_hltv_results_import_job(job_id)


@router.get("/hltv-results")
def get_hltv_results(limit: int = 100, offset: int = 0):
    rows = list_hltv_results(limit=limit, offset=offset)
    return {
        "count": len(rows),
        "total": int(count_hltv_results()),
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
    return _enrich_hltv_results_historical_points(payload)


def _enrich_hltv_results_historical_points(
    payload: Dict[str, Any] | None = None,
    progress: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, Any]:
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
    ranking_dates_total = len(unique_dates) * 2
    if progress:
        progress(
            {
                "phase": "enriching_points",
                "current": f"Checking ranking dates 0 / {ranking_dates_total}",
                "ranking_dates_done": 0,
                "ranking_dates_total": ranking_dates_total,
            }
        )
    hltv_cache: Dict[str, Dict[str, Any]] = {}
    vrs_cache: Dict[str, Dict[str, Any]] = {}
    dates_hltv_ok = 0
    dates_vrs_ok = 0
    dates_hltv_cache = 0
    dates_vrs_cache = 0
    dates_hltv_live = 0
    dates_vrs_live = 0

    ranking_dates_done = 0
    for ds in unique_dates:
        if progress:
            progress(
                {
                    "phase": "enriching_points",
                    "current": f"Checking HLTV ranking date {ranking_dates_done + 1} / {ranking_dates_total}: {ds}",
                    "ranking_dates_done": ranking_dates_done,
                    "ranking_dates_total": ranking_dates_total,
                }
            )
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

        ranking_dates_done += 1
        if progress:
            progress(
                {
                    "phase": "enriching_points",
                    "current": f"Checked HLTV ranking date {ranking_dates_done} / {ranking_dates_total}: {ds}",
                    "ranking_dates_done": ranking_dates_done,
                    "ranking_dates_total": ranking_dates_total,
                }
            )

        if progress:
            progress(
                {
                    "phase": "enriching_points",
                    "current": f"Checking VRS ranking date {ranking_dates_done + 1} / {ranking_dates_total}: {ds}",
                    "ranking_dates_done": ranking_dates_done,
                    "ranking_dates_total": ranking_dates_total,
                }
            )
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

        ranking_dates_done += 1
        if progress:
            progress(
                {
                    "phase": "enriching_points",
                    "current": f"Checked VRS ranking date {ranking_dates_done} / {ranking_dates_total}: {ds}",
                    "ranking_dates_done": ranking_dates_done,
                    "ranking_dates_total": ranking_dates_total,
                }
            )

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


MAP_POOL = ["Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Dust2", "Cache"]


def _canonical_map_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    aliases = {
        "dust 2": "Dust2",
        "dust2": "Dust2",
        "de_dust2": "Dust2",
    }
    key = raw.lower().replace("_", " ")
    if key in aliases:
        return aliases[key]
    for name in MAP_POOL:
        if key == name.lower():
            return name
    return raw[:32]


def _parse_stored_maps(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        map_name = _canonical_map_name(item.get("map"))
        try:
            s1 = int(item.get("score1"))
            s2 = int(item.get("score2"))
        except Exception:
            continue
        if not map_name or s1 < 0 or s2 < 0:
            continue
        outcome = 1 if s1 > s2 else -1 if s1 < s2 else 0
        if s1 >= 12 and s2 >= 12:
            s1, s2 = 12, 12
        out.append({"map": map_name, "score1": s1, "score2": s2, "outcome": outcome})
    return out


def _parse_team_map_stats(value: Any) -> Dict[str, Dict[str, float]]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    if not isinstance(parsed, list):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        map_name = _canonical_map_name(item.get("map"))
        if not map_name:
            continue
        pick_rate = _to_float_or_none(item.get("pick_rate"))
        ban_rate = _to_float_or_none(item.get("ban_rate"))
        win_rate = _to_float_or_none(item.get("win_rate"))
        played = _to_float_or_none(item.get("played"))
        out[map_name] = {
            "pick_rate": max(0.0, min(1.0, pick_rate if pick_rate is not None else 0.0)),
            "ban_rate": max(0.0, min(1.0, ban_rate if ban_rate is not None else 0.0)),
            "win_rate": max(0.0, min(1.0, win_rate if win_rate is not None else 0.0)),
            "played": max(0.0, played if played is not None else 0.0),
        }
    return out


def _build_team_map_stats_by_key(teams: List[Dict[str, Any]] | None = None) -> Dict[str, Dict[str, Dict[str, float]]]:
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for team in teams if teams is not None else get_all_teams():
        key = _norm_team_name(team.get("name") or "")
        if key:
            out[key] = _parse_team_map_stats(team.get("map_stats_json"))
    return out


def _historical_map_stats_window(match_date: Any) -> tuple[str, str] | None:
    try:
        played_on = date.fromisoformat(str(match_date or "")[:10])
    except Exception:
        return None
    end_date = played_on - relativedelta(days=1)
    start_date = end_date - relativedelta(months=6)
    return start_date.isoformat(), end_date.isoformat()


def _required_historical_windows(
    rows: List[Dict[str, Any]],
) -> tuple[Dict[tuple[str, str, str], Dict[str, Any]], set[str]]:
    """All (team, six-month window) pairs the given matches need pre-match map stats for."""
    teams_by_key = {_norm_team_name(team.get("name") or ""): team for team in get_all_teams()}
    required: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    missing_team_keys: set[str] = set()
    for row in rows:
        window = _historical_map_stats_window(row.get("match_date"))
        if not window:
            continue
        start_date, end_date = window
        for team_name in (row.get("team1"), row.get("team2")):
            key = _norm_team_name(team_name or "")
            if not key:
                continue
            team = teams_by_key.get(key)
            if not team or not team.get("hltv_team_id"):
                missing_team_keys.add(key)
                continue
            required[(key, start_date, end_date)] = {
                "key": key,
                "team_name": team.get("name") or team_name or key,
                "hltv_team_id": int(team.get("hltv_team_id")),
                "start_date": start_date,
                "end_date": end_date,
            }
    return required, missing_team_keys


def _build_historical_team_map_stats_by_window(
    rows: List[Dict[str, Any]],
    *,
    fetch_missing: bool = False,
) -> tuple[Dict[tuple[str, str, str], Dict[str, Dict[str, float]]], Dict[str, Any]]:
    required, missing_team_keys = _required_historical_windows(rows)

    out: Dict[tuple[str, str, str], Dict[str, Dict[str, float]]] = {}
    fetched = 0
    failed = 0
    cache_hits = 0
    cache_misses = 0
    for cache_key, item in required.items():
        stored = get_historical_team_map_stats(item["key"], item["start_date"], item["end_date"])
        if stored:
            cache_hits += 1
            out[cache_key] = _parse_team_map_stats(stored.get("maps_json"))
            continue
        cache_misses += 1
        if not fetch_missing:
            continue
        try:
            stats = get_hltv_team_map_stats_for_range(
                int(item["hltv_team_id"]),
                str(item["team_name"]),
                start_date=str(item["start_date"]),
                end_date=str(item["end_date"]),
            )
            maps_json = json.dumps(stats.get("maps") or [])
            upsert_historical_team_map_stats(
                normalized_name=str(item["key"]),
                team_name=str(item["team_name"]),
                hltv_team_id=int(item["hltv_team_id"]),
                start_date=str(item["start_date"]),
                end_date=str(item["end_date"]),
                maps_json=maps_json,
                source_url=str(stats.get("url") or ""),
            )
            out[cache_key] = _parse_team_map_stats(maps_json)
            fetched += 1
        except Exception as exc:
            logger.warning(
                "Historical map stats fetch failed: team=%s start=%s end=%s error=%s",
                item["team_name"],
                item["start_date"],
                item["end_date"],
                exc,
            )
            failed += 1

    return out, {
        "required_windows": len(required),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "fetched": fetched,
        "failed": failed,
        "unmapped_team_keys": len(missing_team_keys),
        "unmapped_team_examples": sorted(missing_team_keys)[:20],
        "fetch_missing": bool(fetch_missing),
    }


# --- Historical map-stats backfill as a pausable/cancelable background job ----
# Fills the permanent historical_team_map_stats table (one HLTV scrape per
# missing team/six-month-window pair) so the Model Lab can train on full data.

HISTORICAL_STATS_JOBS: Dict[str, Dict[str, Any]] = {}
HISTORICAL_STATS_WORKERS: Dict[str, threading.Thread] = {}
HISTORICAL_STATS_JOBS_LOCK = threading.Lock()


def ensure_historical_stats_job_schema() -> None:
    conn = event_db_connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_map_stats_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                progress REAL NOT NULL DEFAULT 0,
                processed_items INTEGER NOT NULL DEFAULT 0,
                total_items INTEGER NOT NULL DEFAULT 0,
                ok INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                current_item TEXT NOT NULL DEFAULT '',
                pause_requested INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_historical_stats_jobs_updated ON historical_map_stats_jobs(updated_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def _historical_job_from_row(row: Any) -> Dict[str, Any]:
    out = dict(row)
    out["pause_requested"] = bool(out.get("pause_requested"))
    out["cancel_requested"] = bool(out.get("cancel_requested"))
    out["progress"] = float(out.get("progress") or 0.0)
    return out


def _get_stored_historical_job(job_id: str) -> Dict[str, Any] | None:
    conn = event_db_connect()
    try:
        row = conn.execute("SELECT * FROM historical_map_stats_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _historical_job_from_row(row) if row else None
    finally:
        conn.close()


def _save_historical_job(job: Dict[str, Any]) -> None:
    now = time.time()
    job["updated_at"] = now
    conn = event_db_connect()
    try:
        conn.execute(
            """
            INSERT INTO historical_map_stats_jobs (
                job_id, status, error, last_error, progress, processed_items, total_items,
                ok, failed, current_item, pause_requested, cancel_requested,
                created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                error = excluded.error,
                last_error = excluded.last_error,
                progress = excluded.progress,
                processed_items = excluded.processed_items,
                total_items = excluded.total_items,
                ok = excluded.ok,
                failed = excluded.failed,
                current_item = excluded.current_item,
                pause_requested = excluded.pause_requested,
                cancel_requested = excluded.cancel_requested,
                updated_at = excluded.updated_at,
                started_at = COALESCE(excluded.started_at, historical_map_stats_jobs.started_at),
                finished_at = excluded.finished_at
            """,
            (
                str(job["job_id"]),
                str(job.get("status") or "queued"),
                str(job.get("error") or ""),
                str(job.get("last_error") or ""),
                float(job.get("progress") or 0.0),
                int(job.get("processed_items") or 0),
                int(job.get("total_items") or 0),
                int(job.get("ok") or 0),
                int(job.get("failed") or 0),
                str(job.get("current_item") or ""),
                1 if job.get("pause_requested") else 0,
                1 if job.get("cancel_requested") else 0,
                float(job.get("created_at") or now),
                now,
                job.get("started_at"),
                job.get("finished_at"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _publish_historical_job(job: Dict[str, Any]) -> None:
    with HISTORICAL_STATS_JOBS_LOCK:
        HISTORICAL_STATS_JOBS[str(job["job_id"])] = dict(job)
    _save_historical_job(job)


def _historical_worker_is_active(job_id: str) -> bool:
    with HISTORICAL_STATS_JOBS_LOCK:
        worker = HISTORICAL_STATS_WORKERS.get(job_id)
        return bool(worker and worker.is_alive())


def _historical_job_response(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": str(job["job_id"]),
        "status": job.get("status", "queued"),
        "error": job.get("error", ""),
        "last_error": job.get("last_error", ""),
        "progress": job.get("progress", 0.0),
        "processed_items": job.get("processed_items", 0),
        "total_items": job.get("total_items", 0),
        "ok": job.get("ok", 0),
        "failed": job.get("failed", 0),
        "current_item": job.get("current_item", ""),
        "pause_requested": bool(job.get("pause_requested")),
        "cancel_requested": bool(job.get("cancel_requested")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


def _get_historical_job_for_response(job_id: str) -> Dict[str, Any]:
    with HISTORICAL_STATS_JOBS_LOCK:
        cached = HISTORICAL_STATS_JOBS.get(job_id)
        job = dict(cached) if cached else None
    if not job:
        job = _get_stored_historical_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found")
    if job.get("status") in {"queued", "running", "pausing", "canceling"} and not _historical_worker_is_active(job_id):
        job["status"] = "canceled" if job.get("cancel_requested") else "paused"
        job["last_error"] = job.get("last_error") or "Job was interrupted before completion. Resume to continue."
        job["error"] = ""
        job["pause_requested"] = False
        job["cancel_requested"] = False
        _publish_historical_job(job)
    return job


def _historical_missing_items() -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Current missing (team, window) pairs plus coverage counts, straight from the DB."""
    rows = list_hltv_results(limit=max(1, count_hltv_results()), offset=0)
    required, unmapped = _required_historical_windows(rows)
    stored_keys = get_historical_team_map_stats_keys()
    missing: List[Dict[str, Any]] = []
    cached = 0
    for cache_key, item in required.items():
        if cache_key in stored_keys:
            cached += 1
        else:
            missing.append(item)
    missing.sort(key=lambda it: (it["end_date"], it["key"]), reverse=True)
    coverage = {
        "required_windows": len(required),
        "cached_windows": cached,
        "missing_windows": len(missing),
        "unmapped_team_keys": len(unmapped),
        "unmapped_team_examples": sorted(unmapped)[:20],
    }
    return missing, coverage


def _run_historical_stats_job(job_id: str) -> None:
    job = _get_stored_historical_job(job_id)
    if not job:
        return

    def stop_requested() -> str:
        with HISTORICAL_STATS_JOBS_LOCK:
            live = HISTORICAL_STATS_JOBS.get(job_id) or {}
        if live.get("cancel_requested"):
            return "canceled"
        if live.get("pause_requested"):
            return "paused"
        return ""

    job["status"] = "running"
    job["pause_requested"] = False
    job["cancel_requested"] = False
    job["error"] = ""
    if job.get("last_error") in _INTERRUPTION_BOILERPLATE:
        job["last_error"] = ""
    job["started_at"] = job.get("started_at") or time.time()
    job["finished_at"] = None
    job["current_item"] = "Scanning stored matches for missing windows"
    _publish_historical_job(job)

    try:
        # Recomputed fresh on every (re)start, so already-fetched windows are
        # skipped automatically and resume always continues where it left off.
        missing, _coverage = _historical_missing_items()
        total = len(missing)
        processed = 0
        ok = int(job.get("ok") or 0)
        failed = int(job.get("failed") or 0)
        job["total_items"] = total
        job["processed_items"] = 0
        job["progress"] = 0.0 if total else 1.0
        _publish_historical_job(job)

        for item in missing:
            stopped = stop_requested()
            if stopped:
                job["status"] = stopped
                job["pause_requested"] = False
                job["cancel_requested"] = False
                job["last_error"] = "Canceled" if stopped == "canceled" else "Paused"
                job["finished_at"] = time.time()
                _publish_historical_job(job)
                return
            label = f"{item['team_name']} ({item['start_date']} to {item['end_date']})"
            job["current_item"] = label
            try:
                stats = get_hltv_team_map_stats_for_range(
                    int(item["hltv_team_id"]),
                    str(item["team_name"]),
                    start_date=str(item["start_date"]),
                    end_date=str(item["end_date"]),
                )
                upsert_historical_team_map_stats(
                    normalized_name=str(item["key"]),
                    team_name=str(item["team_name"]),
                    hltv_team_id=int(item["hltv_team_id"]),
                    start_date=str(item["start_date"]),
                    end_date=str(item["end_date"]),
                    maps_json=json.dumps(stats.get("maps") or []),
                    source_url=str(stats.get("url") or ""),
                )
                ok += 1
            except Exception as exc:
                failed += 1
                job["last_error"] = f"{label}: {exc}"
            processed += 1
            job["processed_items"] = processed
            job["ok"] = ok
            job["failed"] = failed
            job["progress"] = processed / float(total) if total else 1.0
            # Merge live pause/cancel requests before publishing, so this
            # snapshot does not erase a request made while we were fetching.
            with HISTORICAL_STATS_JOBS_LOCK:
                live = HISTORICAL_STATS_JOBS.get(job_id) or {}
            job["pause_requested"] = bool(live.get("pause_requested"))
            job["cancel_requested"] = bool(live.get("cancel_requested"))
            if job["cancel_requested"]:
                job["status"] = "canceling"
            elif job["pause_requested"]:
                job["status"] = "pausing"
            else:
                job["status"] = "running"
            _publish_historical_job(job)

        job["status"] = "completed"
        job["current_item"] = ""
        job["finished_at"] = time.time()
        _publish_historical_job(job)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        job["finished_at"] = time.time()
        _publish_historical_job(job)
    finally:
        with HISTORICAL_STATS_JOBS_LOCK:
            HISTORICAL_STATS_WORKERS.pop(job_id, None)


def _start_historical_stats_worker(job_id: str) -> None:
    with HISTORICAL_STATS_JOBS_LOCK:
        worker = HISTORICAL_STATS_WORKERS.get(job_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(target=_run_historical_stats_job, args=(job_id,), daemon=True)
        HISTORICAL_STATS_WORKERS[job_id] = worker
        worker.start()


def _get_latest_historical_job() -> Dict[str, Any] | None:
    conn = event_db_connect()
    try:
        row = conn.execute("SELECT job_id FROM historical_map_stats_jobs ORDER BY updated_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return _get_historical_job_for_response(str(row["job_id"])) if row else None


@router.get("/hltv-results/historical-map-stats/coverage")
def get_historical_map_stats_coverage():
    _missing, coverage = _historical_missing_items()
    return {"status": "ok", **coverage}


@router.post("/hltv-results/historical-map-stats/start")
def start_historical_map_stats_job():
    latest = _get_latest_historical_job()
    if latest and latest.get("status") in {"queued", "running", "pausing", "canceling"}:
        return {"job_id": latest["job_id"], "status": latest.get("status"), "reused": True}
    now = time.time()
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    job = {
        "job_id": job_id,
        "status": "queued",
        "error": "",
        "last_error": "",
        "progress": 0.0,
        "processed_items": 0,
        "total_items": 0,
        "ok": 0,
        "failed": 0,
        "current_item": "",
        "pause_requested": False,
        "cancel_requested": False,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    _publish_historical_job(job)
    _start_historical_stats_worker(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/hltv-results/historical-map-stats/latest")
def get_latest_historical_map_stats_job():
    job = _get_latest_historical_job()
    if not job:
        return {"exists": False}
    return {"exists": True, **_historical_job_response(job)}


@router.get("/hltv-results/historical-map-stats/job/{job_id}")
def get_historical_map_stats_job(job_id: str):
    return _historical_job_response(_get_historical_job_for_response(job_id))


@router.post("/hltv-results/historical-map-stats/job/{job_id}/pause")
def pause_historical_map_stats_job(job_id: str):
    job = _get_historical_job_for_response(job_id)
    if job.get("status") in {"completed", "canceled", "failed", "paused"}:
        return _historical_job_response(job)
    job["pause_requested"] = True
    job["status"] = "pausing" if _historical_worker_is_active(job_id) else "paused"
    _publish_historical_job(job)
    return _historical_job_response(job)


@router.post("/hltv-results/historical-map-stats/job/{job_id}/cancel")
def cancel_historical_map_stats_job(job_id: str):
    job = _get_historical_job_for_response(job_id)
    if job.get("status") in {"completed", "canceled", "failed"}:
        return _historical_job_response(job)
    job["cancel_requested"] = True
    job["status"] = "canceling" if _historical_worker_is_active(job_id) else "canceled"
    if job["status"] == "canceled":
        job["cancel_requested"] = False
        job["finished_at"] = time.time()
    _publish_historical_job(job)
    return _historical_job_response(job)


@router.post("/hltv-results/historical-map-stats/job/{job_id}/resume")
def resume_historical_map_stats_job(job_id: str):
    job = _get_historical_job_for_response(job_id)
    if job.get("status") in {"completed", "canceled"}:
        return _historical_job_response(job)
    if job.get("status") == "running" and _historical_worker_is_active(job_id):
        return _historical_job_response(job)
    job["status"] = "queued"
    job["pause_requested"] = False
    job["cancel_requested"] = False
    job["error"] = ""
    if job.get("last_error") in _INTERRUPTION_BOILERPLATE:
        job["last_error"] = ""
    job["finished_at"] = None
    _publish_historical_job(job)
    _start_historical_stats_worker(job_id)
    return _historical_job_response(_get_historical_job_for_response(job_id))


def _map_stat_features(
    team_a_stats: Dict[str, Dict[str, float]] | None,
    team_b_stats: Dict[str, Dict[str, float]] | None,
    map_name: str,
) -> Dict[str, float]:
    a_stats = (team_a_stats or {}).get(map_name) or {}
    b_stats = (team_b_stats or {}).get(map_name) or {}
    if not a_stats or not b_stats:
        return {
            "map_win_a": 0.0,
            "map_win_b": 0.0,
            "map_win_gap": 0.0,
            "pick_a": 0.0,
            "pick_b": 0.0,
            "pick_gap": 0.0,
            "ban_a": 0.0,
            "ban_b": 0.0,
            "ban_gap": 0.0,
            "played_pct_a": 0.0,
            "played_pct_b": 0.0,
            "played_pct_gap": 0.0,
            "map_stats_available": 0.0,
        }
    a_total_played = sum(max(0.0, float(row.get("played") or 0.0)) for row in (team_a_stats or {}).values())
    b_total_played = sum(max(0.0, float(row.get("played") or 0.0)) for row in (team_b_stats or {}).values())
    a_played_pct = max(0.0, float(a_stats.get("played") or 0.0)) / a_total_played if a_total_played > 0 else 0.0
    b_played_pct = max(0.0, float(b_stats.get("played") or 0.0)) / b_total_played if b_total_played > 0 else 0.0
    a_win = float(a_stats.get("win_rate") or 0.0)
    b_win = float(b_stats.get("win_rate") or 0.0)
    a_pick = float(a_stats.get("pick_rate") or 0.0)
    b_pick = float(b_stats.get("pick_rate") or 0.0)
    a_ban = float(a_stats.get("ban_rate") or 0.0)
    b_ban = float(b_stats.get("ban_rate") or 0.0)
    return {
        "map_win_a": a_win,
        "map_win_b": b_win,
        "map_win_gap": a_win - b_win,
        "pick_a": a_pick,
        "pick_b": b_pick,
        "pick_gap": a_pick - b_pick,
        "ban_a": a_ban,
        "ban_b": b_ban,
        "ban_gap": a_ban - b_ban,
        "played_pct_a": a_played_pct,
        "played_pct_b": b_played_pct,
        "played_pct_gap": a_played_pct - b_played_pct,
        "map_stats_available": 1.0,
    }


def _to_float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _round_win_to_map_win_probability(p_round: float) -> float:
    p = min(0.999, max(0.001, float(p_round)))
    q = 1.0 - p
    win_before_ot = sum(math.comb(12 + k, k) * (p ** 13) * (q ** k) for k in range(12))
    ot = math.comb(24, 12) * (p ** 12) * (q ** 12)
    return min(0.999, max(0.001, win_before_ot + 0.5 * ot))


def _series_probability_from_map_probability(p_map: float, best_of: int) -> float:
    games = max(1, int(best_of))
    if games % 2 == 0:
        games += 1
    need = games // 2 + 1
    p = min(0.999, max(0.001, float(p_map)))
    total = 0.0
    for wins in range(need, games + 1):
        total += math.comb(games, wins) * (p ** wins) * ((1.0 - p) ** (games - wins))
    return min(0.999, max(0.001, total))


def _iter_ranked_map_samples(
    rows: List[Dict[str, Any]],
    team_map_stats_by_key: Dict[str, Dict[str, Dict[str, float]]] | None = None,
    *,
    historical_map_stats_by_window: Dict[tuple[str, str, str], Dict[str, Dict[str, float]]] | None = None,
    require_map_stats: bool = False,
) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for r in rows:
        t1 = str(r.get("team1") or "").strip()
        t2 = str(r.get("team2") or "").strip()
        maps = _parse_stored_maps(r.get("maps_json"))
        h1 = _to_float_or_none(r.get("hltv_rank_1"))
        h2 = _to_float_or_none(r.get("hltv_rank_2"))
        v1 = _to_float_or_none(r.get("vrs_rank_1"))
        v2 = _to_float_or_none(r.get("vrs_rank_2"))
        if not t1 or not t2 or not maps or h1 is None or h2 is None or v1 is None or v2 is None:
            continue
        k1 = _norm_team_name(t1)
        k2 = _norm_team_name(t2)
        window = _historical_map_stats_window(r.get("match_date")) if historical_map_stats_by_window is not None else None
        if window:
            start_date, end_date = window
            team1_stats = (historical_map_stats_by_window or {}).get((k1, start_date, end_date))
            team2_stats = (historical_map_stats_by_window or {}).get((k2, start_date, end_date))
        else:
            team1_stats = (team_map_stats_by_key or {}).get(k1)
            team2_stats = (team_map_stats_by_key or {}).get(k2)
        for m in maps:
            map_name = _canonical_map_name(m.get("map"))
            if map_name not in MAP_POOL:
                continue
            try:
                s1 = int(m["score1"])
                s2 = int(m["score2"])
            except Exception:
                continue
            total_rounds = s1 + s2
            if total_rounds <= 0:
                continue
            outcome = int(m.get("outcome") or (1 if s1 > s2 else -1 if s1 < s2 else 0))
            map_stat_features = _map_stat_features(
                team1_stats,
                team2_stats,
                map_name,
            )
            if require_map_stats and float(map_stat_features.get("map_stats_available") or 0.0) <= 0.0:
                continue
            samples.append(
                {
                    "match_url": r.get("match_url"),
                    "match_date": r.get("match_date"),
                    "team1": t1,
                    "team2": t2,
                    "map": map_name,
                    "score1": s1,
                    "score2": s2,
                    "outcome": outcome,
                    "total_rounds": total_rounds,
                    "hltv_a": float(h1),
                    "hltv_b": float(h2),
                    "vrs_a": float(v1),
                    "vrs_b": float(v2),
                    "hltv_gap": float(h2 - h1),
                    "hltv_level": float((h1 + h2) / 2.0),
                    "hltv_gap_level": float((h2 - h1) * ((h1 + h2) / 2.0)),
                    "vrs_gap": float(v2 - v1),
                    "vrs_level": float((v1 + v2) / 2.0),
                    "vrs_gap_level": float((v2 - v1) * ((v1 + v2) / 2.0)),
                    "round_share": float(s1 / total_rounds),
                    "weight": float(total_rounds),
                    "hltv_rank_1": h1,
                    "hltv_rank_2": h2,
                    "vrs_rank_1": v1,
                    "vrs_rank_2": v2,
                    "map_win_gap": float(map_stat_features.get("map_win_gap") or 0.0),
                    "pick_gap": float(map_stat_features.get("pick_gap") or 0.0),
                    "ban_gap": float(map_stat_features.get("ban_gap") or 0.0),
                    "played_pct_gap": float(map_stat_features.get("played_pct_gap") or 0.0),
                    "map_stats_available": float(map_stat_features.get("map_stats_available") or 0.0),
                }
            )
    return samples


def _fit_map_model_set(samples: List[Dict[str, Any]], include_map_stats: bool = True) -> Dict[str, Dict[str, Any]]:
    symmetric: List[Dict[str, float]] = []
    by_map: Dict[str, List[Dict[str, float]]] = {m: [] for m in MAP_POOL}
    for sample in samples:
        row_a = {
            "hltv_gap": float(sample["hltv_gap"]),
            "hltv_level": float(sample["hltv_level"]),
            "hltv_gap_level": float(sample["hltv_gap_level"]),
            "vrs_gap": float(sample["vrs_gap"]),
            "vrs_level": float(sample["vrs_level"]),
            "vrs_gap_level": float(sample["vrs_gap_level"]),
            "map_win_gap": float(sample.get("map_win_gap") or 0.0),
            "pick_gap": float(sample.get("pick_gap") or 0.0),
            "ban_gap": float(sample.get("ban_gap") or 0.0),
            "played_pct_gap": float(sample.get("played_pct_gap") or 0.0),
            "map_stats_available": float(sample.get("map_stats_available") or 0.0),
            "round_share": float(sample["round_share"]),
            "weight": float(sample["weight"]),
        }
        row_b = {
            "hltv_gap": -float(sample["hltv_gap"]),
            "hltv_level": float(sample["hltv_level"]),
            "hltv_gap_level": -float(sample["hltv_gap_level"]),
            "vrs_gap": -float(sample["vrs_gap"]),
            "vrs_level": float(sample["vrs_level"]),
            "vrs_gap_level": -float(sample["vrs_gap_level"]),
            "map_win_gap": -float(sample.get("map_win_gap") or 0.0),
            "pick_gap": -float(sample.get("pick_gap") or 0.0),
            "ban_gap": -float(sample.get("ban_gap") or 0.0),
            "played_pct_gap": -float(sample.get("played_pct_gap") or 0.0),
            "map_stats_available": float(sample.get("map_stats_available") or 0.0),
            "round_share": 1.0 - float(sample["round_share"]),
            "weight": float(sample["weight"]),
        }
        symmetric.extend([row_a, row_b])
        by_map.setdefault(str(sample["map"]), []).extend([row_a, row_b])
    if len(symmetric) < 20:
        raise HTTPException(status_code=400, detail="Not enough training maps with stored scores and HLTV/VRS ranks.")
    global_model = {
        **_fit_round_share_logistic_2d(symmetric, include_map_stats=include_map_stats),
        "scope": "global",
        "samples": len(symmetric),
    }
    models: Dict[str, Dict[str, Any]] = {"__global__": global_model}
    for map_name, rows in by_map.items():
        if len(rows) >= 20:
            try:
                models[map_name] = {
                    **_fit_round_share_logistic_2d(rows, include_map_stats=include_map_stats),
                    "scope": "map",
                    "samples": len(rows),
                }
                continue
            except Exception:
                pass
        models[map_name] = {**global_model, "scope": "global_fallback", "samples": len(rows)}
    return models


def _approx_scoreline_from_round_probability(p_round_team1: float) -> tuple[int, int]:
    p = min(0.999, max(0.001, float(p_round_team1)))
    if p >= 0.5:
        loser_raw = 13.0 * (1.0 - p) / p
        if loser_raw >= 11.5:
            return 12, 12
        loser = max(0, min(11, round(loser_raw)))
        return 13, int(loser)
    winner_p = 1.0 - p
    loser_raw = 13.0 * p / winner_p
    if loser_raw >= 11.5:
        return 12, 12
    loser = max(0, min(11, round(loser_raw)))
    return int(loser), 13


def _scoreline_distribution_from_round_probability(p_round_team1: float) -> List[Dict[str, Any]]:
    p = min(0.999, max(0.001, float(p_round_team1)))
    q = 1.0 - p
    rows: List[Dict[str, Any]] = []
    for loser in range(12):
        rows.append(
            {
                "score": f"13-{loser}",
                "team1_score": 13,
                "team2_score": loser,
                "probability": math.comb(12 + loser, loser) * (p ** 13) * (q ** loser),
            }
        )
    rows.append(
        {
            "score": "12-12",
            "team1_score": 12,
            "team2_score": 12,
            "probability": math.comb(24, 12) * (p ** 12) * (q ** 12),
        }
    )
    for loser in range(11, -1, -1):
        rows.append(
            {
                "score": f"{loser}-13",
                "team1_score": loser,
                "team2_score": 13,
                "probability": math.comb(12 + loser, loser) * (q ** 13) * (p ** loser),
            }
        )
    return rows


def _round_share_feature_values(sample: Dict[str, Any]) -> Dict[str, float]:
    return {name: float(sample.get(sample_key) or 0.0) for name, sample_key in ROUND_SHARE_FEATURES}


def _round_share_feature_breakdown(model: Dict[str, Any], sample: Dict[str, Any]) -> Dict[str, Any]:
    values = _round_share_feature_values(sample)
    rows = []
    logit = float(model.get("a") or 0.0)
    for name, _sample_key in ROUND_SHARE_FEATURES:
        coef_key = f"b_{name}"
        if coef_key not in model:
            continue
        raw = float(values.get(name) or 0.0)
        mean = float(model.get(f"mean_{name}", 0.0))
        std = float(model.get(f"std_{name}", 1.0)) or 1.0
        standardized = (raw - mean) / std
        coef = float(model.get(coef_key) or 0.0)
        contribution = coef * standardized
        logit += contribution
        rows.append(
            {
                "feature": name,
                "value": raw,
                "mean": mean,
                "std": std,
                "standardized": standardized,
                "coefficient": coef,
                "contribution": contribution,
            }
        )
    return {
        "intercept": float(model.get("a") or 0.0),
        "logit": logit,
        "features": rows,
    }


def _predict_from_round_share_features(model: Dict[str, Any], values: Dict[str, float]) -> float:
    z = float(model.get("a") or 0.0)
    for name, _sample_key in ROUND_SHARE_FEATURES:
        coef_key = f"b_{name}"
        if coef_key not in model:
            continue
        mean = float(model.get(f"mean_{name}", 0.0))
        std = float(model.get(f"std_{name}", 1.0)) or 1.0
        raw = float(values.get(name, mean))
        z += float(model.get(coef_key) or 0.0) * ((raw - mean) / std)
    return _sigmoid(z)


def _rank_level_band(level: float) -> Dict[str, Any]:
    if level <= 5:
        return {"key": "level_0_5", "label": "Level <= 5", "min": None, "max": 5}
    if level <= 10:
        return {"key": "level_5_10", "label": "Level 5-10", "min": 5, "max": 10}
    if level <= 20:
        return {"key": "level_10_20", "label": "Level 10-20", "min": 10, "max": 20}
    if level <= 40:
        return {"key": "level_20_40", "label": "Level 20-40", "min": 20, "max": 40}
    return {"key": "level_40_plus", "label": "Level > 40", "min": 40, "max": None}


def _build_rank_effect_curve(model: Dict[str, Any], samples: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    band_order = ["level_0_5", "level_5_10", "level_10_20", "level_20_40", "level_40_plus"]
    band_meta = {
        "level_0_5": {"key": "level_0_5", "label": "Level <= 5", "min": None, "max": 5},
        "level_5_10": {"key": "level_5_10", "label": "Level 5-10", "min": 5, "max": 10},
        "level_10_20": {"key": "level_10_20", "label": "Level 10-20", "min": 10, "max": 20},
        "level_20_40": {"key": "level_20_40", "label": "Level 20-40", "min": 20, "max": 40},
        "level_40_plus": {"key": "level_40_plus", "label": "Level > 40", "min": 40, "max": None},
    }
    grouped: Dict[str, Dict[int, Dict[str, float]]] = {key: {} for key in band_order}
    for idx, sample in enumerate(samples or []):
        try:
            hltv_gap = float(sample["hltv_gap"])
            vrs_gap = float(sample["vrs_gap"])
            hltv_level = float(sample["hltv_level"])
            vrs_level = float(sample["vrs_level"])
            score1 = int(sample["score1"])
            score2 = int(sample["score2"])
        except Exception:
            continue
        gap = (hltv_gap + vrs_gap) / 2.0
        level = (hltv_level + vrs_level) / 2.0
        if level <= 0:
            continue
        outcome = int(sample.get("outcome") or (1 if score1 > score2 else -1 if score1 < score2 else 0))
        actual = 1.0 if outcome > 0 else 0.0 if outcome < 0 else 0.5
        predicted = _round_win_to_map_win_probability(_predict_from_round_share_features(model, _round_share_feature_values(sample)))
        band = _rank_level_band(level)
        gap_bucket = int(math.floor(gap / 5.0) * 5)
        bucket = grouped[band["key"]].setdefault(
            gap_bucket,
            {"n": 0.0, "predicted_sum": 0.0, "actual_sum": 0.0, "gap_sum": 0.0, "level_sum": 0.0},
        )
        bucket["n"] += 1.0
        bucket["predicted_sum"] += predicted
        bucket["actual_sum"] += actual
        bucket["gap_sum"] += gap
        bucket["level_sum"] += level
    level_bands = []
    for key in band_order:
        rows = []
        for gap_bucket, values in sorted(grouped[key].items()):
            n = max(1.0, float(values["n"]))
            rows.append(
                {
                    "gap": gap_bucket + 2.5,
                    "gap_min": gap_bucket,
                    "gap_max": gap_bucket + 5,
                    "n": int(values["n"]),
                    "predicted_winrate": float(values["predicted_sum"]) / n,
                    "actual_winrate": float(values["actual_sum"]) / n,
                    "avg_gap": float(values["gap_sum"]) / n,
                    "avg_level": float(values["level_sum"]) / n,
                }
            )
        level_bands.append({**band_meta[key], "rows": rows, "n": sum(int(row["n"]) for row in rows)})
    return {
        "level_bands": level_bands,
        "description": "Each chart groups real training maps into a matchup-level band, buckets average HLTV/VRS rank gap into 5-rank bins, then plots the average predicted map winrate for Team A in each gap bin. The actual winrate for that same bin is included as a comparison line.",
    }


def _evaluate_map_model_set(
    models: Dict[str, Dict[str, Any]],
    test_samples: List[Dict[str, Any]],
    *,
    include_rows: bool = False,
) -> Dict[str, Any]:
    eval_rows = []
    abs_score_error = 0.0
    abs_round_share_error = 0.0
    winner_correct = 0
    brier_sum = 0.0
    by_map: Dict[str, Dict[str, Any]] = {}
    for sample in test_samples:
        map_name = str(sample["map"])
        model = models.get(map_name) or models["__global__"]
        p_round = _predict_from_round_share_features(model, _round_share_feature_values(sample))
        p_map = _round_win_to_map_win_probability(p_round)
        pred_s1, pred_s2 = _approx_scoreline_from_round_probability(p_round)
        actual_s1 = int(sample["score1"])
        actual_s2 = int(sample["score2"])
        actual_outcome = int(sample.get("outcome") or (1 if actual_s1 > actual_s2 else -1 if actual_s1 < actual_s2 else 0))
        pred_outcome = 1 if p_map >= 0.5 else -1
        actual_map_target = 1.0 if actual_outcome > 0 else 0.0 if actual_outcome < 0 else 0.5
        score_error = abs(pred_s1 - actual_s1) + abs(pred_s2 - actual_s2)
        round_share_error = abs(p_round - float(sample["round_share"]))
        abs_score_error += score_error
        abs_round_share_error += round_share_error
        winner_correct += 1 if pred_outcome == actual_outcome else 0
        brier_sum += (p_map - actual_map_target) ** 2
        bucket = by_map.setdefault(
            map_name,
            {"map": map_name, "n": 0, "score_error": 0.0, "winner_correct": 0},
        )
        bucket["n"] += 1
        bucket["score_error"] += score_error
        bucket["winner_correct"] += 1 if pred_outcome == actual_outcome else 0
        if include_rows and len(eval_rows) < 200:
            eval_rows.append(
                {
                    "match_date": sample.get("match_date"),
                    "match_url": sample.get("match_url"),
                    "team1": sample.get("team1"),
                    "team2": sample.get("team2"),
                    "map": map_name,
                    "actual_score": f"{actual_s1}-{actual_s2}",
                    "predicted_score": f"{pred_s1}-{pred_s2}",
                    "predicted_winner": sample.get("team1") if pred_outcome > 0 else sample.get("team2"),
                    "actual_winner": sample.get("team1") if actual_outcome > 0 else sample.get("team2") if actual_outcome < 0 else None,
                    "team1_round_win_probability": p_round,
                    "team1_map_win_probability": p_map,
                    "feature_breakdown": _round_share_feature_breakdown(model, sample),
                    "score_distribution": _scoreline_distribution_from_round_probability(p_round),
                    "score_error": score_error,
                    "model_scope": model.get("scope"),
                }
            )

    n = max(1, len(test_samples))
    map_metrics = []
    for row in by_map.values():
        count = max(1, int(row["n"]))
        model = models.get(str(row["map"])) or {}
        map_metrics.append(
            {
                "map": row["map"],
                "n": count,
                "score_mae": float(row["score_error"]) / count,
                "winner_accuracy": float(row["winner_correct"]) / count,
                "model_scope": model.get("scope"),
                "training_samples": int(model.get("samples") or 0),
            }
        )
    map_metrics.sort(key=lambda r: r["n"], reverse=True)
    return {
        "metrics": {
            "n_maps": len(test_samples),
            "score_mae": abs_score_error / n,
            "round_share_mae": abs_round_share_error / n,
            "winner_accuracy": winner_correct / n,
            "brier": brier_sum / n,
        },
        "maps": map_metrics,
        "rows": eval_rows,
    }


def _map_model_input_summary(samples: List[Dict[str, Any]], *, candidate_maps: int | None = None) -> Dict[str, Any]:
    total = len(samples)
    with_stats = sum(1 for sample in samples if float(sample.get("map_stats_available") or 0.0) > 0.0)
    candidates = int(candidate_maps if candidate_maps is not None else total)
    return {
        "maps": total,
        "candidate_maps": candidates,
        "with_map_stats": with_stats,
        "excluded_missing_map_stats": max(0, candidates - total),
        "missing_map_stats": max(0, total - with_stats),
        "map_stats_coverage": (total / candidates) if candidates else 0.0,
    }


@router.get("/hltv-results/map-model-lab")
def get_map_model_lab(
    train_limit: int = 3000,
    train_offset: int = 500,
    test_limit: int = 500,
    test_offset: int = 0,
    random_split: bool = False,
    random_seed: int | None = None,
    fetch_missing_map_stats: bool = False,
):
    db_matches = int(count_hltv_results())
    train_limit = max(0, min(100000, int(train_limit)))
    test_limit = max(0, min(100000, int(test_limit)))
    train_offset = max(0, int(train_offset or 0))
    test_offset = max(0, int(test_offset or 0))
    seed_used = int(random_seed if random_seed is not None else random.randrange(1, 2_147_483_647))
    if random_split and db_matches > 0:
        total_needed = min(db_matches, train_limit + test_limit)
        all_rows = list_hltv_results(limit=db_matches, offset=0)
        rng = random.Random(seed_used)
        rng.shuffle(all_rows)
        test_count = min(test_limit, total_needed)
        train_count = min(train_limit, max(0, total_needed - test_count))
        test_rows = all_rows[:test_count]
        train_rows = all_rows[test_count : test_count + train_count]
        train_offset = 0
        test_offset = 0
        train_limit = len(train_rows)
        test_limit = len(test_rows)
    else:
        random_split = False
        seed_used = 0
        if db_matches > 0:
            test_offset = 0
            train_offset = min(test_limit, db_matches)
            train_limit = min(train_limit, max(0, db_matches - train_offset))
            test_limit = min(test_limit, max(0, db_matches - test_offset))
        train_rows = list_hltv_results(limit=train_limit, offset=train_offset)
        test_rows = list_hltv_results(limit=test_limit, offset=test_offset)
    historical_map_stats_by_window, historical_cache_summary = _build_historical_team_map_stats_by_window(
        [*train_rows, *test_rows],
        fetch_missing=bool(fetch_missing_map_stats),
    )
    train_candidate_samples = _iter_ranked_map_samples(train_rows)
    test_candidate_samples = _iter_ranked_map_samples(test_rows)
    train_samples = _iter_ranked_map_samples(
        train_rows,
        historical_map_stats_by_window=historical_map_stats_by_window,
        require_map_stats=True,
    )
    test_samples = _iter_ranked_map_samples(
        test_rows,
        historical_map_stats_by_window=historical_map_stats_by_window,
        require_map_stats=True,
    )
    models_with_map_data = _fit_map_model_set(train_samples, include_map_stats=True)
    models_rank_only = _fit_map_model_set(train_samples, include_map_stats=False)
    if not test_samples:
        raise HTTPException(
            status_code=400,
            detail="No evaluation maps have historical six-month map stats for both teams. Fetch missing historical map stats, or fix team identity mappings.",
        )

    with_map_data = _evaluate_map_model_set(models_with_map_data, test_samples, include_rows=True)
    rank_only = _evaluate_map_model_set(models_rank_only, test_samples)
    return {
        "status": "ok",
        "method": "Trains two round-share logistic formulas on the same historical slice after excluding maps without cached six-month pre-match map stats for both teams. The rank-only baseline uses HLTV/VRS rank gaps, matchup rank level, and gap-by-level interactions. The map-data model also uses directional historical map-stat deltas for Team A minus Team B: win-rate, pick-rate, ban-rate, and map-played share. Both are evaluated on the same covered holdout slice.",
        "db_matches": db_matches,
        "split": {
            "mode": "random" if random_split else "ordered",
            "random": bool(random_split),
            "random_seed": seed_used if random_split else None,
        },
        "map_pool": MAP_POOL,
        "train": {
            "limit": train_limit,
            "offset": train_offset,
            "matches_loaded": len(train_rows),
            "map_samples": len(train_samples),
        },
        "test": {
            "limit": test_limit,
            "offset": test_offset,
            "matches_loaded": len(test_rows),
            "map_samples": len(test_samples),
        },
        "formula": models_with_map_data["__global__"],
        "rank_only_formula": models_rank_only["__global__"],
        "rank_effect_curve": _build_rank_effect_curve(models_with_map_data["__global__"], train_samples),
        "rank_only_effect_curve": _build_rank_effect_curve(models_rank_only["__global__"], train_samples),
        "metrics": with_map_data["metrics"],
        "rank_only_metrics": rank_only["metrics"],
        "comparison": {
            "with_map_data": with_map_data["metrics"],
            "rank_only": rank_only["metrics"],
        },
        "input_summary": {
            "train": _map_model_input_summary(train_samples, candidate_maps=len(train_candidate_samples)),
            "test": _map_model_input_summary(test_samples, candidate_maps=len(test_candidate_samples)),
            "map_feature_shape": "directional_deltas",
            "historical_map_stats": historical_cache_summary,
        },
        "maps": with_map_data["maps"],
        "rank_only_maps": rank_only["maps"],
        "rows": with_map_data["rows"],
    }
    teams = get_all_teams()
    team_map_stats_by_key = _build_team_map_stats_by_key(teams)
    by_id = {int(t.get("team_id")): t for t in teams if t.get("team_id") is not None}
    team_a = by_id.get(int(team_a_id))
    team_b = by_id.get(int(team_b_id))
    if not team_a or not team_b:
        raise HTTPException(status_code=404, detail="Both teams must exist in the team DB.")
    if int(team_a_id) == int(team_b_id):
        raise HTTPException(status_code=400, detail="Choose two different teams.")

    lim = max(100, min(100000, int(limit or 5000)))
    rows = list_hltv_results(limit=lim, offset=0)
    samples_by_map: Dict[str, List[Dict[str, float]]] = {m: [] for m in MAP_POOL}
    global_samples: List[Dict[str, float]] = []
    team_map_counts: Dict[str, Dict[str, int]] = {}
    team_total_maps: Dict[str, int] = {}
    global_map_counts: Dict[str, int] = {m: 0 for m in MAP_POOL}
    global_total_maps = 0
    parsed_match_count = 0

    for r in rows:
        t1 = str(r.get("team1") or "").strip()
        t2 = str(r.get("team2") or "").strip()
        if not t1 or not t2:
            continue
        maps = _parse_stored_maps(r.get("maps_json"))
        if not maps:
            continue
        h1 = _to_float_or_none(r.get("hltv_rank_1"))
        h2 = _to_float_or_none(r.get("hltv_rank_2"))
        v1 = _to_float_or_none(r.get("vrs_rank_1"))
        v2 = _to_float_or_none(r.get("vrs_rank_2"))
        if h1 is None or h2 is None or v1 is None or v2 is None:
            continue
        parsed_match_count += 1
        k1 = _norm_team_name(t1)
        k2 = _norm_team_name(t2)
        team_map_counts.setdefault(k1, {})
        team_map_counts.setdefault(k2, {})
        for m in maps:
            map_name = _canonical_map_name(m.get("map"))
            if map_name not in MAP_POOL:
                continue
            s1 = int(m["score1"])
            s2 = int(m["score2"])
            total_rounds = s1 + s2
            if total_rounds <= 0:
                continue
            global_map_counts[map_name] = global_map_counts.get(map_name, 0) + 1
            global_total_maps += 1
            team_total_maps[k1] = team_total_maps.get(k1, 0) + 1
            team_total_maps[k2] = team_total_maps.get(k2, 0) + 1
            team_map_counts[k1][map_name] = team_map_counts[k1].get(map_name, 0) + 1
            team_map_counts[k2][map_name] = team_map_counts[k2].get(map_name, 0) + 1
            map_stat_features = _map_stat_features(
                team_map_stats_by_key.get(k1),
                team_map_stats_by_key.get(k2),
                map_name,
            )

            sample_a = {
                "hltv_a": float(h1),
                "hltv_b": float(h2),
                "vrs_a": float(v1),
                "vrs_b": float(v2),
                "hltv_gap": float(h2 - h1),
                "hltv_level": float((h1 + h2) / 2.0),
                "hltv_gap_level": float((h2 - h1) * ((h1 + h2) / 2.0)),
                "vrs_gap": float(v2 - v1),
                "vrs_level": float((v1 + v2) / 2.0),
                "vrs_gap_level": float((v2 - v1) * ((v1 + v2) / 2.0)),
                "map_win_gap": float(map_stat_features["map_win_gap"]),
                "pick_gap": float(map_stat_features["pick_gap"]),
                "ban_gap": float(map_stat_features["ban_gap"]),
                "played_pct_gap": float(map_stat_features["played_pct_gap"]),
                "map_stats_available": float(map_stat_features["map_stats_available"]),
                "round_share": float(s1 / total_rounds),
                "weight": float(total_rounds),
            }
            sample_b = {
                "hltv_a": float(h2),
                "hltv_b": float(h1),
                "vrs_a": float(v2),
                "vrs_b": float(v1),
                "hltv_gap": float(h1 - h2),
                "hltv_level": float((h1 + h2) / 2.0),
                "hltv_gap_level": float((h1 - h2) * ((h1 + h2) / 2.0)),
                "vrs_gap": float(v1 - v2),
                "vrs_level": float((v1 + v2) / 2.0),
                "vrs_gap_level": float((v1 - v2) * ((v1 + v2) / 2.0)),
                "map_win_gap": -float(map_stat_features["map_win_gap"]),
                "pick_gap": -float(map_stat_features["pick_gap"]),
                "ban_gap": -float(map_stat_features["ban_gap"]),
                "played_pct_gap": -float(map_stat_features["played_pct_gap"]),
                "map_stats_available": float(map_stat_features["map_stats_available"]),
                "round_share": float(s2 / total_rounds),
                "weight": float(total_rounds),
            }
            samples_by_map.setdefault(map_name, []).extend([sample_a, sample_b])
            global_samples.extend([sample_a, sample_b])

    if len(global_samples) < 20:
        raise HTTPException(
            status_code=400,
            detail="Not enough stored map results with HLTV/VRS ranks. Import results, match maps, and ranking enrichment first.",
        )

    global_model = _fit_round_share_logistic_2d(global_samples)
    models_by_map: Dict[str, Dict[str, Any]] = {}
    for map_name, samples in samples_by_map.items():
        if len(samples) >= 20:
            try:
                models_by_map[map_name] = {**_fit_round_share_logistic_2d(samples), "scope": "map"}
                continue
            except Exception:
                pass
        models_by_map[map_name] = {**global_model, "scope": "global_fallback", "samples": len(samples)}

    hltv_a = _to_float_or_none(team_a.get("hltv_rank"))
    hltv_b = _to_float_or_none(team_b.get("hltv_rank"))
    vrs_a = _to_float_or_none(team_a.get("vrs_rank"))
    vrs_b = _to_float_or_none(team_b.get("vrs_rank"))
    if hltv_a is None or hltv_b is None or vrs_a is None or vrs_b is None:
        raise HTTPException(status_code=400, detail="Both teams need current HLTV and VRS ranks.")
    team_a_key = _norm_team_name(team_a.get("name") or "")
    team_b_key = _norm_team_name(team_b.get("name") or "")
    team_a_hltv_map_stats = _parse_team_map_stats(team_a.get("map_stats_json"))
    team_b_hltv_map_stats = _parse_team_map_stats(team_b.get("map_stats_json"))
    has_hltv_map_stats = bool(team_a_hltv_map_stats) and bool(team_b_hltv_map_stats)

    def play_rate(team_key: str, map_name: str) -> float:
        team_total = int(team_total_maps.get(team_key) or 0)
        if team_total > 0:
            return float(team_map_counts.get(team_key, {}).get(map_name, 0) / team_total)
        if global_total_maps > 0:
            return float(global_map_counts.get(map_name, 0) / global_total_maps)
        return 1.0 / len(MAP_POOL)

    raw_map_rows = []
    total_weight = 0.0
    for map_name in MAP_POOL:
        model = models_by_map[map_name]
        a_stats = team_a_hltv_map_stats.get(map_name) or {}
        b_stats = team_b_hltv_map_stats.get(map_name) or {}
        map_stat_features = _map_stat_features(team_a_hltv_map_stats, team_b_hltv_map_stats, map_name)
        p_round_a = _predict_logistic_2d(
            model,
            hltv_a,
            hltv_b,
            vrs_a,
            vrs_b,
            float(map_stat_features["map_win_a"]),
            float(map_stat_features["map_win_b"]),
            float(map_stat_features["pick_a"]),
            float(map_stat_features["pick_b"]),
            float(map_stat_features["ban_a"]),
            float(map_stat_features["ban_b"]),
            float(map_stat_features["played_pct_a"]),
            float(map_stat_features["played_pct_b"]),
        )
        p_map_a = _round_win_to_map_win_probability(p_round_a)
        if a_stats and b_stats:
            a_rate = float(a_stats.get("pick_rate") or 0.0)
            b_rate = float(b_stats.get("pick_rate") or 0.0)
            a_ban = float(a_stats.get("ban_rate") or 0.0)
            b_ban = float(b_stats.get("ban_rate") or 0.0)
            pick_pressure = 0.02 + a_rate + b_rate
            veto_survival = math.sqrt(max(0.000001, (1.0 - a_ban) * (1.0 - b_ban)))
            appearance_weight = max(0.000001, pick_pressure * veto_survival)
            map_pool_source = "hltv_pick_ban"
        else:
            a_rate = play_rate(team_a_key, map_name)
            b_rate = play_rate(team_b_key, map_name)
            a_ban = 1.0 - a_rate
            b_ban = 1.0 - b_rate
            appearance_weight = max(0.000001, ((a_rate + b_rate) / 2.0) * math.sqrt(max(0.000001, a_rate * b_rate)))
            map_pool_source = "played_map_fallback"
        total_weight += appearance_weight
        raw_map_rows.append(
            {
                "map": map_name,
                "model_scope": model.get("scope", "map"),
                "training_samples": int(model.get("samples") or 0),
                "training_rounds": int(model.get("rounds") or 0),
                "team_a_play_rate": a_rate,
                "team_b_play_rate": b_rate,
                "team_a_pick_rate": a_rate,
                "team_b_pick_rate": b_rate,
                "team_a_ban_rate": a_ban,
                "team_b_ban_rate": b_ban,
                "team_a_ban_proxy": a_ban,
                "team_b_ban_proxy": b_ban,
                "appearance_weight": appearance_weight,
                "map_pool_source": map_pool_source,
                "team_a_round_win_probability": p_round_a,
                "team_b_round_win_probability": 1.0 - p_round_a,
                "team_a_map_win_probability": p_map_a,
                "team_b_map_win_probability": 1.0 - p_map_a,
            }
        )

    if total_weight <= 0:
        total_weight = float(len(raw_map_rows))
        for row in raw_map_rows:
            row["appearance_weight"] = 1.0

    avg_map_win = 0.0
    map_rows = []
    for row in raw_map_rows:
        row["map_probability"] = float(row["appearance_weight"] / total_weight)
        avg_map_win += float(row["map_probability"]) * float(row["team_a_map_win_probability"])
        map_rows.append(row)
    map_rows.sort(key=lambda r: r["map_probability"], reverse=True)

    series_a = _series_probability_from_map_probability(avg_map_win, best_of)
    return {
        "status": "ok",
        "limit": lim,
        "matches_loaded": len(rows),
        "matches_with_ranked_maps": parsed_match_count,
        "map_samples": len(global_samples),
        "best_of": max(1, int(best_of)),
        "method": "Round-share logistic regression by map using HLTV/VRS rank gaps, matchup rank level, gap-by-level interactions, and separate Team A and Team B imported map win-rate, pick-rate, ban-rate, and map-played percentage values where available. Map-pool probabilities use HLTV team map pick/ban rates when imported, with stored played-map rates as fallback.",
        "map_pool": MAP_POOL,
        "map_pool_source": "hltv_pick_ban" if has_hltv_map_stats else "played_map_fallback",
        "team_a": {
            "team_id": int(team_a_id),
            "name": team_a.get("name"),
            "hltv_rank": hltv_a,
            "vrs_rank": vrs_a,
            "map_stats_imported_at": team_a.get("map_stats_imported_at"),
            "map_stats_source_url": team_a.get("map_stats_source_url"),
            "series_win_probability": series_a,
            "average_map_win_probability": avg_map_win,
        },
        "team_b": {
            "team_id": int(team_b_id),
            "name": team_b.get("name"),
            "hltv_rank": hltv_b,
            "vrs_rank": vrs_b,
            "map_stats_imported_at": team_b.get("map_stats_imported_at"),
            "map_stats_source_url": team_b.get("map_stats_source_url"),
            "series_win_probability": 1.0 - series_a,
            "average_map_win_probability": 1.0 - avg_map_win,
        },
        "global_model": global_model,
        "maps": map_rows,
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
    live_detail_failed = False
    try:
        details = get_hltv_match_details(url)
        maps = details.get("maps") or []
        if maps and stored and stored.get("match_url"):
            set_hltv_result_maps(str(stored.get("match_url")), maps)
    except HLTVRankingError:
        live_detail_failed = True
        maps = []
    except Exception:
        live_detail_failed = True
        maps = []

    if live_detail_failed and not maps and isinstance(stored, dict) and stored.get("maps_json"):
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

    ref = _extract_hltv_event_ref_from_fantasy(int(event_id), data)
    counts = _import_money_draft_data(
        money,
        event_id=int(event_id),
        hltv_event_id=ref.get("hltv_event_id"),
        hltv_event_url=ref.get("hltv_event_url"),
    )
    return {"status": "ok", **counts, "event_id": int(event_id), "active_event_id": int(event_id), **ref}
