"""Nightly data-ingestion scheduler.

A single daemon thread wakes periodically and, once per day at the configured
local time, runs the three ingestion jobs in sequence — team rankings, new
matches, then player Top-X ratings. They run sequentially because they all
serialize on the one shared HLTV browser anyway. Manual "run now" triggers reuse
the same code path. State + history live in schedule_db.

No external scheduler dependency: this is plain threading, started from the
FastAPI lifespan so it only runs while the (always-on) backend is up.
"""

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.data import schedule_db

logger = logging.getLogger(__name__)

_TICK_SECONDS = 30


class DataScheduler:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._run_lock = threading.Lock()  # only one ingestion batch at a time
        self._state_lock = threading.Lock()
        self._state: Dict[str, Any] = {
            "running": False,
            "current_task": None,
            "trigger": None,
            "started_at": None,
            "processed": 0,
            "total": 0,
            "message": "",
        }

    # ---- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_resurrect = 0.0
        self._keep_awake = False
        try:
            schedule_db.fail_orphan_runs()
        except Exception:
            logger.exception("Could not clean orphaned schedule runs")
        self._thread = threading.Thread(target=self._loop, name="data-scheduler", daemon=True)
        self._thread.start()
        logger.info("Data scheduler started")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(_TICK_SECONDS):
            try:
                self._maybe_run_scheduled()
            except Exception:
                logger.exception("Scheduler tick failed")
            try:
                self._maybe_resurrect_jobs()
            except Exception:
                logger.exception("Job resurrection tick failed")
            try:
                self._update_keep_awake()
            except Exception:
                logger.exception("Keep-awake tick failed")

    # ---- overnight autonomy --------------------------------------------------
    # Long scraping jobs get marked "paused" whenever the backend dies mid-run
    # (reboot, restart-backend.ps1, crash). Nothing used to resume them, so an
    # unattended machine made no progress for days. The scheduler thread now
    # resurrects interruption-paused jobs itself — a pause the USER requested
    # (pause_requested=True) is respected and left alone.
    def _job_families(self):
        from backend.routes import admin, events, players, teams

        return [
            ("trigger rates", admin._get_latest_trigger_job, admin.resume_trigger_backfill_job),
            ("veto backfill", events._get_latest_veto_job, events.resume_veto_backfill_job),
            ("map scoreboards", events._get_latest_map_sb_job, events.resume_map_scoreboards_job),
            ("historical map-stats", events._get_latest_historical_job, events.resume_historical_map_stats_job),
            ("results import", events.get_latest_hltv_results_import_job, events.resume_hltv_results_import_job),
            ("topx batch", lambda: players._get_latest_topx_batch_job(include_completed=False),
             players.resume_fetch_top_ratings_batch_job),
            ("rankings refresh", teams._get_latest_rankings_job, teams.resume_rankings_refresh_job),
            ("map-stats import", lambda: teams._get_latest_map_stats_job(include_completed=False),
             teams.resume_map_stats_import_job),
        ]

    def _maybe_resurrect_jobs(self) -> None:
        now = time.time()
        if now - self._last_resurrect < 120:
            return
        self._last_resurrect = now
        for name, getter, resume in self._job_families():
            try:
                job = getter() or {}
                if not isinstance(job, dict) or job.get("exists") is False:
                    continue
                if str(job.get("status") or "") == "paused" and not job.get("pause_requested"):
                    resume(str(job.get("job_id")))
                    logger.info("Auto-resumed interrupted %s job %s", name, job.get("job_id"))
            except Exception:
                logger.info("Could not auto-resume %s job", name, exc_info=True)

    def _any_work_active(self) -> bool:
        with self._state_lock:
            if self._state.get("running"):
                return True
        active = {"queued", "running", "pausing", "canceling"}
        for _name, getter, _resume in self._job_families():
            try:
                job = getter() or {}
                if isinstance(job, dict) and str(job.get("status") or "") in active:
                    return True
            except Exception:
                continue
        return False

    def _update_keep_awake(self) -> None:
        """Block system sleep while any scrape job or scheduled batch runs —
        an overnight run is pointless if Windows dozes off 20 minutes in.
        SetThreadExecutionState is per-thread, and this scheduler thread lives
        for the whole process, so the flag persists until released here."""
        busy = self._any_work_active()
        if busy == self._keep_awake:
            return
        try:
            import ctypes

            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if busy else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self._keep_awake = busy
            logger.info("Keep-awake %s (jobs %s)", "engaged" if busy else "released", "active" if busy else "idle")
        except Exception:
            logger.exception("SetThreadExecutionState failed")

    def _maybe_run_scheduled(self) -> None:
        cfg = schedule_db.get_schedule_config()
        if not cfg.get("enabled"):
            return
        now = datetime.now()
        hh, mm = (cfg.get("run_time") or "00:00").split(":")
        scheduled = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if now < scheduled:
            return
        # Already fired since today's slot? (survives restarts via the DB.)
        if schedule_db.last_scheduled_run_ts() >= scheduled.timestamp():
            return
        if self._run_lock.locked():
            return  # a manual run is in progress; retry next tick
        self._run_batch(self._enabled_tasks(cfg), trigger="scheduled", cfg=cfg)

    # ---- running -------------------------------------------------------------
    @staticmethod
    def _enabled_tasks(cfg: Dict[str, Any]) -> List[str]:
        tasks = []
        if cfg.get("do_events"):
            tasks.append("events")
        if cfg.get("do_rankings"):
            tasks.append("rankings")
        if cfg.get("do_matches"):
            tasks.append("matches")
        if cfg.get("do_ratings"):
            tasks.append("ratings")
        return tasks

    def run_now(self, task: str = "all") -> Dict[str, Any]:
        """Trigger a run in a background thread; returns immediately."""
        if self._run_lock.locked():
            return {"status": "busy", "detail": "An ingestion run is already in progress."}
        cfg = schedule_db.get_schedule_config()
        if task == "all":
            tasks = self._enabled_tasks(cfg) or list(schedule_db.TASK_KEYS)
        elif task in schedule_db.TASK_KEYS:
            tasks = [task]
        else:
            return {"status": "error", "detail": f"Unknown task '{task}'."}
        threading.Thread(
            target=self._run_batch, args=(tasks, "manual", cfg), name="scheduler-manual", daemon=True
        ).start()
        return {"status": "started", "tasks": tasks}

    def _run_batch(self, tasks: List[str], trigger: str, cfg: Dict[str, Any]) -> None:
        if not tasks:
            return
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            for task in tasks:
                self._run_task(task, trigger, cfg)
        finally:
            self._run_lock.release()
            self._set_state(running=False, current_task=None, trigger=None, processed=0, total=0)

    def _run_task(self, task: str, trigger: str, cfg: Dict[str, Any]) -> None:
        run_id = schedule_db.start_run(task, trigger)
        self._set_state(running=True, current_task=task, trigger=trigger, started_at=time.time(),
                        processed=0, total=0, message=f"Running {task}...")
        try:
            if task == "events":
                msg = self._task_events()
            elif task == "rankings":
                msg = self._task_rankings()
            elif task == "matches":
                msg = self._task_matches(cfg)
            elif task == "ratings":
                msg = self._task_ratings()
            else:
                raise ValueError(f"Unknown task '{task}'")
            schedule_db.finish_run(run_id, "success", msg)
            logger.info("Scheduler task %s (%s) succeeded: %s", task, trigger, msg)
        except Exception as exc:  # noqa: BLE001 — record and continue to next task
            schedule_db.finish_run(run_id, "error", str(exc))
            logger.exception("Scheduler task %s (%s) failed", task, trigger)

    # ---- individual tasks (lazy imports avoid import cycles) -----------------
    def _task_events(self) -> str:
        from backend.routes import admin, events

        result = events.discover_and_import_new_fantasy_events() or {}
        # Booster/role data rides along: when active-event players are missing
        # trigger rates, start the backfill (it auto-provisions the fantasy
        # team for events with no captured endpoint yet).
        try:
            if result.get("imported"):
                # A new event refreshes EVERY priced player's rates — data
                # drifts between events, so coverage alone isn't enough.
                target = max(result["imported"])
                started = admin.start_trigger_backfill({"event_id": target, "refresh_all": True})
                result["trigger_backfill"] = f"refresh-all started for event {target}"
                result["trigger_job"] = started.get("job_id")
            else:
                _prices, missing, _cov = admin._missing_trigger_players(None)
                if missing:
                    started = admin.start_trigger_backfill({})
                    result["trigger_backfill"] = f"started for active event ({len(missing)} players missing)"
                    result["trigger_job"] = started.get("job_id")
                else:
                    result["trigger_backfill"] = "coverage complete"
        except Exception as exc:  # noqa: BLE001 — report, don't fail the import
            result["trigger_backfill"] = f"not started: {exc}"
        return _short(result)

    def _task_rankings(self) -> str:
        from backend.routes import teams

        result = teams.refresh_all_rankings_for_all_teams_today() or {}
        return _short(result)

    def _task_matches(self, cfg: Dict[str, Any]) -> str:
        from backend.routes import events

        lookback = int(cfg.get("matches_lookback_days") or 3)
        until = (date.today() - timedelta(days=lookback)).isoformat()
        result = events.import_hltv_results({"import_mode": "until_date", "until_date": until}) or {}
        return _short(result)

    def _task_ratings(self) -> str:
        from backend.data.player_db import get_all_players, get_active_topx_window
        from backend.routes import players

        try:
            months = int(get_active_topx_window() or 3)
        except Exception:
            months = 3
        ids = [int(p["player_id"]) for p in get_all_players() if p.get("player_id")]
        items = [{"player_id": pid} for pid in ids]

        # _run_top_ratings_batch reports (row, processed, total, ok, failed).
        def _progress(row, processed, total, ok, failed) -> None:
            self._set_state(processed=int(processed), total=int(total),
                            message=f"Ratings {processed}/{total} ({months}mo, {failed} failed)")

        result = players._run_top_ratings_batch(items, 1, progress_callback=_progress, months=months) or {}
        return f"{len(ids)} players, {months}mo: {_short(result)}"

    # ---- status --------------------------------------------------------------
    def _set_state(self, **kwargs: Any) -> None:
        with self._state_lock:
            self._state.update(kwargs)

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            state = dict(self._state)
        cfg = schedule_db.get_schedule_config()
        now = datetime.now()
        hh, mm = (cfg.get("run_time") or "00:00").split(":")
        scheduled_today = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        next_run = scheduled_today if now < scheduled_today else scheduled_today + timedelta(days=1)
        return {
            "config": cfg,
            "state": state,
            "next_run_at": next_run.timestamp(),
            "last_success_by_task": schedule_db.last_success_by_task(),
        }


scheduler = DataScheduler()


def _short(result: Any, limit: int = 400) -> str:
    try:
        text = ", ".join(f"{k}={result[k]}" for k in list(result)[:8]) if isinstance(result, dict) else str(result)
    except Exception:
        text = str(result)
    return text[:limit]
