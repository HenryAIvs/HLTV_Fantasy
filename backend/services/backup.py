"""Nightly database backup.

Consistent copies of both SQLite files (the main database and the archived
HLTV pages), taken with SQLite's online backup API so they are safe while the
backend is running, gzip-compressed into a folder that OneDrive syncs, keeping
the last N days. Runs as the last step of the scheduler's nightly batch and
on demand (POST /schedule/run-now {"task": "backup"}).

Two sources with different policies (the config can override the numbers):

* fantasy_players.db: every night, gzip-compressed (2 GB -> ~180 MB), keep the
  last `keep_days` copies (default 7).
* page_snapshots.db (archived HLTV pages): once a week, stored as-is (its
  pages are already compressed inside the file; gzip gained nothing), keep
  `snapshots_keep` copies (default 1). Losing a week of archived pages costs
  a re-scrape of recent pages, not the valuations.

Configuration: .runtime/backup.json
    {"dir": "C:/Users/you/OneDrive/CS Fantasy Backups", "keep_days": 7,
     "snapshots_every_days": 7, "snapshots_keep": 1}
or HLTV_BACKUP_DIR / HLTV_BACKUP_KEEP_DAYS in the environment. Without a
directory the task records a warning and does nothing.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.data.db import DB_PATH, ROOT_DIR
from backend.data.page_snapshots import SNAPSHOT_DB_PATH

logger = logging.getLogger(__name__)

DEFAULT_KEEP_DAYS = 7
_CONFIG_PATH = Path(ROOT_DIR) / ".runtime" / "backup.json"
_TMP_DIR = Path(ROOT_DIR) / ".runtime" / "backup-tmp"

# name -> source file. The name is the prefix of the backup files.
SOURCES: Dict[str, Path] = {
    "fantasy_players": Path(DB_PATH),
    "page_snapshots": Path(SNAPSHOT_DB_PATH),
}


def _policies(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-source: how often (days between copies), how many to keep, gzip?"""
    return {
        "fantasy_players": {"every_days": 1, "keep": int(cfg["keep_days"]), "compress": True},
        "page_snapshots": {
            "every_days": int(cfg.get("snapshots_every_days") or 7),
            "keep": int(cfg.get("snapshots_keep") or 1),
            "compress": False,
        },
    }


def backup_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {"dir": None, "keep_days": DEFAULT_KEEP_DAYS, "snapshots_every_days": 7, "snapshots_keep": 1}
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        if raw.get("dir"):
            cfg["dir"] = str(raw["dir"])
        for key in ("keep_days", "snapshots_every_days", "snapshots_keep"):
            if raw.get(key):
                cfg[key] = max(1, int(raw[key]))
    except Exception:  # noqa: BLE001 - missing or malformed file = not configured
        pass
    if os.getenv("HLTV_BACKUP_DIR"):
        cfg["dir"] = os.getenv("HLTV_BACKUP_DIR")
    if os.getenv("HLTV_BACKUP_KEEP_DAYS"):
        try:
            cfg["keep_days"] = int(os.getenv("HLTV_BACKUP_KEEP_DAYS") or DEFAULT_KEEP_DAYS)
        except ValueError:
            pass
    cfg["keep_days"] = max(1, int(cfg["keep_days"]))
    return cfg


def _fmt_mb(n: int) -> str:
    return f"{n / 1048576:.0f} MB"


def _snapshot_to(src: Path, tmp_db: Path) -> None:
    """One consistent copy of a live SQLite file via the online backup API."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    try:
        dest = sqlite3.connect(str(tmp_db))
        try:
            # pages=-1 copies in one step under a single read lock, so a
            # writer cannot make the copy restart part-way through.
            source.backup(dest, pages=-1)
        finally:
            dest.close()
    finally:
        source.close()


def _compress(tmp_db: Path, target: Path) -> None:
    part = target.with_suffix(target.suffix + ".part")
    with open(tmp_db, "rb") as fin, gzip.open(part, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, 8 * 1024 * 1024)
    os.replace(part, target)


def _existing(dest: Path, name: str) -> List[Path]:
    """This source's backups, newest first (compressed or not)."""
    files = list(dest.glob(f"{name}.*.db.gz")) + list(dest.glob(f"{name}.*.db"))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def prune(dest: Path, policies: Dict[str, Dict[str, Any]]) -> List[str]:
    """Keep the newest `keep` copies of each source, delete the rest and any
    abandoned .part files. Returns the names removed."""
    removed: List[str] = []
    for name in SOURCES:
        keep = max(1, int(policies[name]["keep"]))
        for old in _existing(dest, name)[keep:]:
            try:
                old.unlink()
                removed.append(old.name)
            except OSError as exc:
                logger.warning("Could not delete old backup %s: %s", old, exc)
    for part in dest.glob("*.part"):
        try:
            part.unlink()
            removed.append(part.name)
        except OSError:
            pass
    return removed


def run_backup(dest_dir: Optional[str] = None, keep_days: Optional[int] = None, progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Back up every source into dest_dir and prune. Never raises for a
    missing configuration; other failures propagate so the scheduler records
    an error (and the heartbeat reports a failed night)."""
    cfg = backup_config()
    dest_dir = dest_dir or cfg["dir"]
    if keep_days:
        cfg["keep_days"] = int(keep_days)
    policies = _policies(cfg)
    if not dest_dir:
        return {"status": "skipped", "reason": "no backup directory configured (.runtime/backup.json or HLTV_BACKUP_DIR)"}
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    stamp = date.today().isoformat()
    files: List[Dict[str, Any]] = []
    for name, src in SOURCES.items():
        policy = policies[name]
        if not src.exists():
            files.append({"name": name, "status": "missing"})
            continue
        newest = _existing(dest, name)
        if newest and (time.time() - newest[0].stat().st_mtime) < (policy["every_days"] - 0.5) * 86400:
            due = datetime.fromtimestamp(newest[0].stat().st_mtime) + timedelta(days=policy["every_days"])
            files.append({"name": name, "status": f"not due until {due.date().isoformat()}"})
            continue
        t0 = time.time()
        if progress:
            progress(f"Backing up {name}...")
        tmp_db = _TMP_DIR / f"{name}.{stamp}.db"
        target = dest / (f"{name}.{stamp}.db.gz" if policy["compress"] else f"{name}.{stamp}.db")
        try:
            _snapshot_to(src, tmp_db)
            copied = time.time()
            if policy["compress"]:
                _compress(tmp_db, target)
            else:
                part = target.with_suffix(target.suffix + ".part")
                shutil.copyfile(tmp_db, part)
                os.replace(part, target)
        finally:
            try:
                tmp_db.unlink()
            except OSError:
                pass
        files.append(
            {
                "name": name,
                "file": target.name,
                "source_bytes": src.stat().st_size,
                "backup_bytes": target.stat().st_size,
                "copy_seconds": round(copied - t0, 1),
                "seconds": round(time.time() - t0, 1),
            }
        )
    removed = prune(dest, policies)
    total_kept = sum(p.stat().st_size for name in SOURCES for p in _existing(dest, name))
    summary = "; ".join(
        f"{f['name']}: {_fmt_mb(f['source_bytes'])} -> {_fmt_mb(f['backup_bytes'])} in {f['seconds']}s" if f.get("file") else f"{f['name']}: {f['status']}"
        for f in files
    )
    return {
        "status": "ok",
        "dir": str(dest),
        "date": stamp,
        "files": files,
        "removed": removed,
        "kept_bytes": total_kept,
        "policies": policies,
        "seconds": round(time.time() - started, 1),
        "summary": f"{summary}; folder {_fmt_mb(total_kept)}" + (f"; pruned {len(removed)}" if removed else ""),
    }


def latest_backups(dest_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest backup per source, for status displays."""
    cfg = backup_config()
    dest_dir = dest_dir or cfg["dir"]
    if not dest_dir or not Path(dest_dir).exists():
        return []
    out = []
    for name in SOURCES:
        files = _existing(Path(dest_dir), name)
        if files:
            st = files[0].stat()
            out.append({"name": name, "file": files[0].name, "bytes": st.st_size, "at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")})
    return out
