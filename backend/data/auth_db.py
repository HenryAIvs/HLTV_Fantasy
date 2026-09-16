"""Accounts and sessions for the distributed app.

users        one row per Google account (keyed by Google's stable `sub`).
sessions     bearer tokens handed to the app, stored hashed; 90-day expiry,
             revocable (sign out).
auth_pending the sign-in handshake: the app parks a random state + a
             challenge, the browser flow fills in the session token, the app
             collects it once with the matching verifier.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any, Dict, Optional

from backend.data.db import connect

SESSION_DAYS = 90
PENDING_SECONDS = 10 * 60
_TOUCH_INTERVAL = 5 * 60  # update last_seen_at at most this often


def ensure_auth_schema() -> None:
    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                google_sub    TEXT NOT NULL UNIQUE,
                email         TEXT,
                name          TEXT,
                picture       TEXT,
                created_at    REAL NOT NULL,
                last_login_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash    TEXT PRIMARY KEY,
                user_id       INTEGER NOT NULL,
                created_at    REAL NOT NULL,
                last_seen_at  REAL NOT NULL,
                expires_at    REAL NOT NULL,
                revoked       INTEGER NOT NULL DEFAULT 0,
                client        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS auth_pending (
                state         TEXT PRIMARY KEY,
                challenge     TEXT NOT NULL,
                created_at    REAL NOT NULL,
                token         TEXT,
                user_id       INTEGER
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---- users -------------------------------------------------------------------
def upsert_user(google_sub: str, email: Optional[str], name: Optional[str], picture: Optional[str]) -> Dict[str, Any]:
    now = time.time()
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO users (google_sub, email, name, picture, created_at, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(google_sub) DO UPDATE SET
                email = excluded.email, name = excluded.name, picture = excluded.picture,
                last_login_at = excluded.last_login_at
            """,
            (google_sub, email, name, picture, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def public_user(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The fields the app may see."""
    if not row:
        return None
    return {"id": int(row["id"]), "email": row.get("email"), "name": row.get("name"), "picture": row.get("picture")}


# ---- sessions ----------------------------------------------------------------
def create_session(user_id: int, client: Optional[str] = None) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, revoked, client) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (_hash(token), int(user_id), now, now, now + SESSION_DAYS * 86400, client),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def user_id_for_token(token: Optional[str]) -> Optional[int]:
    """The user behind a bearer token, or None when missing, unknown, revoked
    or expired. Touches last_seen_at occasionally."""
    if not token or len(token) < 20 or len(token) > 200:
        return None
    h = _hash(token)
    now = time.time()
    conn = connect()
    try:
        row = conn.execute(
            "SELECT user_id, last_seen_at, expires_at, revoked FROM sessions WHERE token_hash = ?", (h,)
        ).fetchone()
        if not row or int(row["revoked"]) or float(row["expires_at"]) < now:
            return None
        if now - float(row["last_seen_at"]) > _TOUCH_INTERVAL:
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now, h))
            conn.commit()
        return int(row["user_id"])
    finally:
        conn.close()


def revoke_session(token: Optional[str]) -> bool:
    if not token:
        return False
    conn = connect()
    try:
        cur = conn.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (_hash(token),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---- sign-in handshake -------------------------------------------------------
def create_pending(state: str, challenge: str) -> None:
    now = time.time()
    conn = connect()
    try:
        conn.execute("DELETE FROM auth_pending WHERE created_at < ?", (now - PENDING_SECONDS,))
        conn.execute(
            "INSERT OR REPLACE INTO auth_pending (state, challenge, created_at, token, user_id) VALUES (?, ?, ?, NULL, NULL)",
            (state, challenge, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_pending(state: str) -> Optional[Dict[str, Any]]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM auth_pending WHERE state = ?", (state,)).fetchone()
        if not row:
            return None
        if time.time() - float(row["created_at"]) > PENDING_SECONDS:
            conn.execute("DELETE FROM auth_pending WHERE state = ?", (state,))
            conn.commit()
            return None
        return dict(row)
    finally:
        conn.close()


def complete_pending(state: str, token: str, user_id: int) -> bool:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE auth_pending SET token = ?, user_id = ? WHERE state = ? AND token IS NULL", (token, int(user_id), state)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def take_pending(state: str) -> Optional[Dict[str, Any]]:
    """Return and delete a completed handshake (the token is handed over once)."""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM auth_pending WHERE state = ? AND token IS NOT NULL", (state,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM auth_pending WHERE state = ?", (state,))
        conn.commit()
        return dict(row)
    finally:
        conn.close()
