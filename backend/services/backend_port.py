"""Free-port selection and the memorized port file.

The backend no longer assumes port 8000. On launch it:

1. reads the port it used last time from ``.runtime/backend-port.json``
   (falling back to 8000 the very first time),
2. tries to bind that port; if it is free the backend keeps using it,
3. otherwise checks whether the occupant is *another copy of this backend*
   (``GET /health`` answers with our app id) — in which case this launch is a
   duplicate and exits quietly, leaving the file pointing at the live one —
4. or, if a foreign program owns the port, scans 8000-8099 for the first free
   port instead,
5. and finally memorizes whatever port it bound so the next launch, the
   autostart watchdog (scripts/backend-port.ps1) and the Electron launcher
   (electron/main.js) all find it in the same file.

Binding happens *here* and the bound socket is handed to uvicorn, so the port
written to the file is always the port actually being served.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
PORT_FILE = ROOT_DIR / ".runtime" / "backend-port.json"

APP_ID = "hltv-fantasy"
HOST = "127.0.0.1"
DEFAULT_PORT = 8000
SCAN_PORTS = range(8000, 8100)
# A sibling backend that has bound its port but is still running lifespan
# startup (schema init, curve fit) cannot answer /health yet; give it this long
# before concluding the port belongs to some other program.
SIBLING_PROBE_SECONDS = 10.0

logger = logging.getLogger(__name__)


def read_port_info() -> dict[str, Any] | None:
    """Return the memorized port record, or None if missing/unreadable."""
    try:
        data = json.loads(PORT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        data["port"] = int(data.get("port"))
    except Exception:
        return None
    return data


def write_port_info(port: int, pid: int | None, *, adopted: bool = False) -> dict[str, Any]:
    """Memorize the port (atomically) so every launcher agrees on it."""
    info = {
        "app": APP_ID,
        "host": HOST,
        "port": int(port),
        "pid": int(pid) if pid else None,
        "url": f"http://{HOST}:{int(port)}",
        "adopted": bool(adopted),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PORT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info, indent=2), encoding="utf-8")
    os.replace(tmp, PORT_FILE)
    return info


def try_bind(port: int) -> socket.socket | None:
    """Bind HOST:port exclusively; return the bound socket or None if taken."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Never SO_REUSEADDR here: on Windows it would let us bind a port that is
    # already in use, which is exactly what this check must detect.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind((HOST, int(port)))
        sock.listen(128)
    except OSError:
        sock.close()
        return None
    return sock


FOREIGN = "foreign"


def _probe(port: int, timeout: float = 1.5) -> dict[str, Any] | str | None:
    """GET /health on the port.

    Returns our health JSON when the occupant is one of our backends, the
    FOREIGN marker when *something* answered but it is not ours (another
    program, or a pre-identity build of this backend), and None when nothing
    answered at all (connection refused / no reply yet).
    """
    try:
        with urllib.request.urlopen(f"http://{HOST}:{int(port)}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return FOREIGN
    except Exception:
        return None
    if isinstance(data, dict) and data.get("app") == APP_ID:
        return data
    return FOREIGN


def probe_backend(port: int, timeout: float = 1.5) -> dict[str, Any] | None:
    """GET /health on the port; return its JSON only if it is one of ours."""
    found = _probe(port, timeout)
    return found if isinstance(found, dict) else None


def _wait_for_sibling(port: int) -> dict[str, Any] | None:
    """Give a sibling that is still booting time to answer; a definite
    non-matching answer means a foreign occupant and returns immediately."""
    deadline = time.monotonic() + SIBLING_PROBE_SECONDS
    while True:
        found = _probe(port)
        if isinstance(found, dict):
            return found
        if found == FOREIGN or time.monotonic() >= deadline:
            return None
        time.sleep(1.0)


def select_port() -> tuple[socket.socket, int]:
    """Pick the port to serve on and return (bound socket, port).

    Raises SystemExit(0) when a healthy copy of this backend already owns the
    memorized port (duplicate launch), and SystemExit(1) when no port can be
    bound at all.
    """
    explicit = os.getenv("HLTV_BACKEND_PORT", "").strip()
    if explicit:
        port = int(explicit)
        sock = try_bind(port)
        if sock is None:
            raise SystemExit(f"HLTV_BACKEND_PORT={port} is already in use; free it or unset the variable.")
        return sock, port

    remembered = read_port_info()
    preferred = int(remembered["port"]) if remembered else DEFAULT_PORT
    if remembered:
        logger.info("Memorized backend port %s (from %s)", preferred, PORT_FILE)
    else:
        logger.info("No memorized backend port yet; starting from %s", DEFAULT_PORT)

    candidates = [preferred] + [p for p in SCAN_PORTS if p != preferred]
    for port in candidates:
        sock = try_bind(port)
        if sock is not None:
            if port != preferred:
                logger.warning("Port %s is busy; selected free port %s instead and memorizing it", preferred, port)
            return sock, port

        sibling = _wait_for_sibling(port)
        if sibling:
            # Keep the file pointing at the instance that is actually alive so
            # the watchdog and Electron connect to it instead of relaunching.
            write_port_info(port, sibling.get("pid"), adopted=True)
            logger.warning(
                "Another HLTV Fantasy backend is already running on port %s (pid %s); this launch exits.",
                port,
                sibling.get("pid"),
            )
            raise SystemExit(0)
        logger.info("Port %s is owned by another program; trying the next free port", port)

    raise SystemExit(f"No free backend port found in {SCAN_PORTS.start}-{SCAN_PORTS.stop - 1}.")
