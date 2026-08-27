"""REST surface for the nightly data-ingestion scheduler."""

from fastapi import APIRouter

from backend.data import schedule_db
from backend.data.schedule_db import ensure_schedule_schema  # re-exported for main's initializers
from backend.services.scheduler import scheduler

router = APIRouter()


@router.get("/status")
def get_schedule_status() -> dict:
    return scheduler.status()


@router.get("/runs")
def get_schedule_runs(limit: int = 50) -> dict:
    return {"runs": schedule_db.list_runs(limit)}


@router.post("/config")
def update_config(payload: dict | None = None) -> dict:
    schedule_db.update_schedule_config(payload or {})
    return scheduler.status()


@router.post("/run-now")
def run_now(payload: dict | None = None) -> dict:
    task = str((payload or {}).get("task") or "all").strip().lower()
    return scheduler.run_now(task)
