"""Shared SQLite connection helpers for the fantasy database."""

import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = str(ROOT_DIR / "fantasy_players.db")


def connect() -> sqlite3.Connection:
    """Return a connection with dict-like row access.

    WAL mode lets status polls read while background jobs write; the busy
    timeout keeps brief write bursts from surfacing as 'database is locked'.
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn
