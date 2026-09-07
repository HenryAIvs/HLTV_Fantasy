"""Formats and shapes the app has met but does not model yet.

Rather than guess (a bracket seeded the wrong way produces confident, wrong
valuations), unsupported shapes are rejected and recorded here so they show
up in the Scheduling tab and the run history as work to do. Stored in
.runtime/dev-flags.json, keyed by a short shape key.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
FLAGS_FILE = ROOT_DIR / ".runtime" / "dev-flags.json"
_LOCK = threading.Lock()


def _load() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(FLAGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(flags: Dict[str, Dict[str, Any]]) -> None:
    FLAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FLAGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(flags, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, FLAGS_FILE)


def flag(key: str, kind: str, detail: str, event_id: int | None = None) -> Dict[str, Any]:
    """Record (or bump) a development flag. Idempotent per key."""
    now = time.time()
    with _LOCK:
        flags = _load()
        entry = flags.get(key) or {"key": key, "kind": kind, "first_seen": now, "count": 0, "event_ids": []}
        entry["detail"] = detail
        entry["last_seen"] = now
        entry["count"] = int(entry.get("count") or 0) + 1
        if event_id and int(event_id) not in entry["event_ids"]:
            entry["event_ids"] = sorted(set(entry["event_ids"]) | {int(event_id)})
        flags[key] = entry
        try:
            _save(flags)
        except Exception:
            logger.exception("Could not persist dev flags")
    logger.warning("Flagged for development [%s] %s: %s", kind, key, detail)
    return entry


def list_flags() -> List[Dict[str, Any]]:
    with _LOCK:
        flags = _load()
    return sorted(flags.values(), key=lambda f: -float(f.get("last_seen") or 0))


def clear_flag(key: str) -> bool:
    with _LOCK:
        flags = _load()
        if key not in flags:
            return False
        flags.pop(key)
        _save(flags)
    return True
