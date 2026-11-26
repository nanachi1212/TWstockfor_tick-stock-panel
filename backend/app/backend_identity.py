"""Local desktop/background backend identity and exclusive listener ownership."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
from pathlib import Path

from apscheduler.schedulers.base import STATE_RUNNING

LOOPBACK = "127.0.0.1"
BACKEND_PORT = 3018
API_TITLE = "Nanachi 的台股監控看板"
DATA_CHECK_HEADER = "X-TickStock-Data-Check"


def data_root_fingerprint(data_dir: Path) -> str:
    canonical = os.path.normcase(str(data_dir.resolve()))
    return hashlib.sha256(os.fsencode(canonical)).hexdigest()


def after_close_ready(state) -> bool:
    scheduler = getattr(state, "scheduler", None)
    return bool(
        getattr(scheduler, "state", None) == STATE_RUNNING
        and getattr(state, "startup_catchup_ready", False)
    )


def reserve_backend_listener(port: int) -> socket.socket:
    """Own the port before executing any potentially writing app lifespan hooks."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LOOPBACK, port))
        listener.listen()
        return listener
    except BaseException:
        listener.close()
        raise


def _read_local_json(port: int, path: str, headers: dict[str, str]) -> dict | None:
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=2)
    try:
        conn.request("GET", path, headers=headers)
        with conn.getresponse() as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read(8 * 1024 * 1024))
            return payload if isinstance(payload, dict) else None
    except (http.client.HTTPException, OSError, ValueError):
        return None
    finally:
        conn.close()


def project_backend_ready(
    port: int, data_dir: Path, version: str, *, require_scheduler: bool = False
) -> bool:
    health = _read_local_json(
        port, "/health", {DATA_CHECK_HEADER: data_root_fingerprint(data_dir)}
    )
    if (
        not health
        or health.get("status") != "ok"
        or health.get("version") != version
        or health.get("same_data_root") is not True
        or (require_scheduler and health.get("after_close_ready") is not True)
    ):
        return False
    schema = _read_local_json(port, "/openapi.json", {})
    info = schema.get("info") if schema else None
    return bool(
        isinstance(info, dict)
        and info.get("title") == API_TITLE
        and info.get("version") == version
    )
