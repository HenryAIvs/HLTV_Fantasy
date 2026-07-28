"""One-row tables that persist the latest (payload, result) JSON pair for a feature."""

import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from backend.data.db import connect


class SingletonState:
    """A saved payload/result pair kept in a single-row table.

    `iso_timestamps` keeps the legacy per-table updated_at format: ISO-8601 TEXT
    for the swiss simulation table, epoch REAL for the playoff tables.

    Loads are cached in-process because parsing these blobs per request stalls
    the backend. Caching is size-capped: blobs above _CACHE_MAX_RAW_BYTES parse
    per request instead of staying resident (a 755 MB combos blob expands to
    several GB of Python objects and causes MemoryError if retained). This
    process is the only writer, so save()/invalidate() keep the cache coherent.
    Callers must treat loaded values as read-only.
    """

    _CACHE_MAX_RAW_BYTES = 100 * 1024 * 1024

    def __init__(
        self,
        table: str,
        *,
        result_column: str = "result_json",
        result_key: str = "result",
        iso_timestamps: bool = False,
    ):
        self.table = table
        self.result_column = result_column
        self.result_key = result_key
        self.iso_timestamps = iso_timestamps
        self._cache_lock = threading.Lock()
        self._cache_valid = False
        self._cache_value: Optional[dict] = None
        # Serializes oversized (uncached) loads: parsing one of these blobs can
        # transiently need several GB, and two concurrent parses OOM the backend.
        self._load_lock = threading.Lock()

    def invalidate(self) -> None:
        with self._cache_lock:
            self._cache_valid = False
            self._cache_value = None

    def _now(self):
        if self.iso_timestamps:
            return datetime.now(timezone.utc).isoformat()
        return float(time.time())

    def ensure_table(self) -> None:
        conn = connect()
        try:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                    payload_json TEXT NOT NULL,
                    {self.result_column} TEXT NOT NULL,
                    updated_at {"TEXT" if self.iso_timestamps else "REAL"} NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def save(self, payload: dict, result: dict) -> None:
        stamp = self._now()
        payload_text = json.dumps(payload)
        result_text = json.dumps(result)
        raw_size = len(payload_text) + len(result_text)
        conn = connect()
        try:
            conn.execute(
                f"""
                INSERT INTO {self.table} (singleton_id, payload_json, {self.result_column}, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    {self.result_column} = excluded.{self.result_column},
                    updated_at = excluded.updated_at
                """,
                (payload_text, result_text, stamp),
            )
            conn.commit()
        finally:
            conn.close()
        with self._cache_lock:
            if raw_size <= self._CACHE_MAX_RAW_BYTES:
                self._cache_value = {
                    "payload": payload,
                    self.result_key: result,
                    "updated_at": stamp if self.iso_timestamps else float(stamp),
                }
                self._cache_valid = True
            else:
                self._cache_value = None
                self._cache_valid = False

    def load(self) -> Optional[dict]:
        with self._cache_lock:
            if self._cache_valid:
                return self._cache_value
        with self._load_lock:
            # Another request may have populated the cache while we waited.
            with self._cache_lock:
                if self._cache_valid:
                    return self._cache_value
            conn = connect()
            try:
                row = conn.execute(
                    f"SELECT payload_json, {self.result_column}, updated_at FROM {self.table} WHERE singleton_id = 1"
                ).fetchone()
            finally:
                conn.close()
            if not row:
                with self._cache_lock:
                    self._cache_value = None
                    self._cache_valid = True
                return None
            payload_text = row["payload_json"]
            result_text = row[self.result_column]
            raw_size = len(payload_text or "") + len(result_text or "")
            updated_at = row["updated_at"] if self.iso_timestamps else float(row["updated_at"])
            del row
            payload = json.loads(payload_text)
            del payload_text
            result = json.loads(result_text)
            del result_text
            value = {
                "payload": payload,
                self.result_key: result,
                "updated_at": updated_at,
            }
            if raw_size <= self._CACHE_MAX_RAW_BYTES:
                with self._cache_lock:
                    self._cache_value = value
                    self._cache_valid = True
            return value
