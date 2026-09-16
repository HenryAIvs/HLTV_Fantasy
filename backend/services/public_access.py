"""Public / operator split for a hosted backend.

The distributed app talks to a backend running on the operator's machine. Two
kinds of caller reach it:

* the operator — requests from this machine (loopback, not through the
  tunnel) or carrying the admin token (`X-Admin-Token`, see admin_token()) —
  who may do everything: imports, scheduler, runs, restarts;
* everyone else — treated as public — who only reaches the read-only surface
  the app needs: stored runs, valuations, Top 5 queries, player and team data,
  images. Anything that scrapes HLTV or rewrites stored state answers 403.

The public app always sends `X-Public-Client: 1`, so it is handled as public
even when it runs on the operator's own machine. Public query endpoints are
rate limited per client and capped in concurrency, since each one costs a
second or two of every core.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_RUNTIME_DIR = Path(__file__).resolve().parents[2] / ".runtime"
_TOKEN_FILE = _RUNTIME_DIR / "admin-token.txt"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip")

# (method, path pattern) the public app may call. Paths are matched whole,
# without the query string.
PUBLIC_ROUTES: List[Tuple[str, str]] = [
    ("GET", r"/health"),
    ("GET", r"/public/config"),
    ("GET", r"/players/?"),
    ("GET", r"/players/\d+"),
    ("GET", r"/players/\d+/rating-curve"),
    ("GET", r"/players/average-rating-curve"),
    ("GET", r"/players/topx-window"),
    ("GET", r"/teams/?"),
    ("GET", r"/teams/\d+"),
    ("GET", r"/teams/map-stats-import/latest"),
    ("GET", r"/events/?"),
    ("GET", r"/events/active"),
    ("GET", r"/events/\d+"),
    ("GET", r"/events/\d+/kind"),
    ("GET", r"/events/hltv-results/stored"),
    ("GET", r"/assets/.*"),
    ("GET", r"/playoff/latest"),
    ("GET", r"/playoff/best-team/from-latest/latest"),
    ("GET", r"/playoff/best-team/bracket-from-latest/latest"),
    ("POST", r"/playoff/best-team/from-latest/query"),
    ("POST", r"/playoff/best-team/from-latest/completed-query"),
    ("POST", r"/playoff/outcome-player-detail"),
    ("POST", r"/playoff/winning-player-detail"),
    ("GET", r"/groups/latest"),
    ("GET", r"/groups/best-team/latest"),
    ("GET", r"/groups/event-autofill"),
    ("POST", r"/groups/best-team/query"),
    ("POST", r"/groups/best-team/completed-query"),
    ("GET", r"/simulate/latest"),
    ("GET", r"/best-team/latest"),
    ("POST", r"/best-team/query"),
]
_PUBLIC_COMPILED = [(m, re.compile(f"^{p}$")) for m, p in PUBLIC_ROUTES]

# Endpoints that compute on request: per-client rate limit + global concurrency cap.
_HEAVY = re.compile(r"^/(playoff/best-team/from-latest/(query|completed-query)|playoff/(outcome|winning)-player-detail|groups/best-team/(query|completed-query)|best-team/query)$")
_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = int(os.getenv("HLTV_PUBLIC_RATE_LIMIT", "60"))
_MAX_CONCURRENT = int(os.getenv("HLTV_PUBLIC_MAX_CONCURRENT", "3"))

_rate_lock = threading.Lock()
_rate_hits: Dict[str, List[float]] = {}
_inflight = threading.BoundedSemaphore(_MAX_CONCURRENT)


def admin_token() -> str:
    """The operator token: HLTV_ADMIN_TOKEN, else .runtime/admin-token.txt
    (generated once). Send it as X-Admin-Token to use the operator surface
    from another machine."""
    env = (os.getenv("HLTV_ADMIN_TOKEN") or "").strip()
    if env:
        return env
    try:
        stored = _TOKEN_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    except Exception:
        pass
    token = secrets.token_urlsafe(32)
    try:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(token, encoding="utf-8")
    except Exception:
        pass
    return token


def client_key(request) -> str:
    for header in _PROXY_HEADERS:
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_operator(request) -> bool:
    token = request.headers.get("x-admin-token")
    if token and secrets.compare_digest(token, admin_token()):
        return True
    if request.headers.get("x-public-client"):
        return False
    if any(request.headers.get(h) for h in _PROXY_HEADERS):
        return False  # came through the tunnel / a proxy
    host = request.client.host if request.client else ""
    return host in _LOCAL_HOSTS


def is_public_route(method: str, path: str) -> bool:
    return any(m == method and rx.match(path) for m, rx in _PUBLIC_COMPILED)


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        hits = [t for t in _rate_hits.get(key, []) if now - t < _RATE_WINDOW_SECONDS]
        if len(hits) >= _RATE_LIMIT:
            _rate_hits[key] = hits
            return True
        hits.append(now)
        _rate_hits[key] = hits
        if len(_rate_hits) > 5000:  # forget idle clients
            for k in [k for k, v in _rate_hits.items() if not v or now - v[-1] > _RATE_WINDOW_SECONDS]:
                _rate_hits.pop(k, None)
    return False


class PublicAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "OPTIONS" or is_operator(request):
            return await call_next(request)
        path = request.url.path
        if not is_public_route(request.method, path):
            return JSONResponse({"detail": "This action is only available to the server operator."}, status_code=403)
        if _HEAVY.match(path):
            if _rate_limited(client_key(request)):
                return JSONResponse({"detail": "Too many requests. Please wait a moment."}, status_code=429)
            if not _inflight.acquire(blocking=False):
                return JSONResponse({"detail": "The server is busy. Please try again in a few seconds."}, status_code=503)
            try:
                return await call_next(request)
            finally:
                _inflight.release()
        return await call_next(request)
