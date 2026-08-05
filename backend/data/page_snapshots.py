"""Compressed archive of every scraped HLTV page, one snapshot per URL.

Lives in its own SQLite file (page_snapshots.db) so the main database stays
lean; rows are only read by explicit re-parse passes, never by normal app
traffic. Match pages are static once played, so refetches simply overwrite.
"""

import gzip
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.data.db import DB_PATH

logger = logging.getLogger(__name__)

SNAPSHOT_DB_PATH = Path(DB_PATH).parent / "page_snapshots.db"

_schema_lock = threading.Lock()
_schema_ready = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SNAPSHOT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_snapshot_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_snapshots (
                    url TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT '',
                    fetched_at REAL NOT NULL,
                    raw_bytes INTEGER NOT NULL DEFAULT 0,
                    html_gz BLOB NOT NULL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_page_snapshots_kind ON page_snapshots(kind)")
            conn.commit()
        finally:
            conn.close()
        _schema_ready = True


def _infer_kind(url: str) -> str:
    u = str(url or "").lower()
    if "/matches/" in u:
        return "match"
    if "/stats/teams/" in u:
        return "team_stats"
    if "/results" in u:
        return "results"
    if "/ranking" in u:
        return "ranking"
    return "other"


def save_page_snapshot(url: str, html: str, kind: str | None = None) -> None:
    """Best-effort archive; must never break the scrape that produced the page."""
    try:
        u = str(url or "").strip()
        if not u or not html:
            return
        ensure_snapshot_schema()
        payload = gzip.compress(html.encode("utf-8", errors="replace"), compresslevel=6)
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO page_snapshots (url, kind, fetched_at, raw_bytes, html_gz)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    kind = excluded.kind,
                    fetched_at = excluded.fetched_at,
                    raw_bytes = excluded.raw_bytes,
                    html_gz = excluded.html_gz
                """,
                (u, kind if kind is not None else _infer_kind(u), time.time(), len(html), payload),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Failed to save page snapshot for %s: %s", url, exc)


def get_page_snapshot(url: str) -> Optional[Dict[str, Any]]:
    ensure_snapshot_schema()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM page_snapshots WHERE url = ?", (str(url or "").strip(),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "url": row["url"],
        "kind": row["kind"],
        "fetched_at": float(row["fetched_at"]),
        "raw_bytes": int(row["raw_bytes"]),
        "html": gzip.decompress(row["html_gz"]).decode("utf-8", errors="replace"),
    }


def list_snapshot_urls(kind: str | None = None) -> List[str]:
    ensure_snapshot_schema()
    conn = _connect()
    try:
        if kind:
            rows = conn.execute("SELECT url FROM page_snapshots WHERE kind = ? ORDER BY fetched_at", (str(kind),)).fetchall()
        else:
            rows = conn.execute("SELECT url FROM page_snapshots ORDER BY fetched_at").fetchall()
        return [str(r["url"]) for r in rows]
    finally:
        conn.close()


def snapshot_summary() -> Dict[str, Any]:
    ensure_snapshot_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS n, SUM(LENGTH(html_gz)) AS stored, SUM(raw_bytes) AS raw FROM page_snapshots GROUP BY kind"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n, SUM(LENGTH(html_gz)) AS stored FROM page_snapshots").fetchone()
    finally:
        conn.close()
    return {
        "total_pages": int(total["n"] or 0),
        "stored_mb": round(float(total["stored"] or 0) / 1048576.0, 1),
        "by_kind": [
            {
                "kind": r["kind"],
                "pages": int(r["n"] or 0),
                "stored_mb": round(float(r["stored"] or 0) / 1048576.0, 1),
                "raw_mb": round(float(r["raw"] or 0) / 1048576.0, 1),
            }
            for r in rows
        ],
    }
