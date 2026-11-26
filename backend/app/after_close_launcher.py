"""Start or reuse the scheduled backend for an explicitly selected existing store."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from app import __version__
from app.backend_identity import (
    BACKEND_PORT,
    LOOPBACK,
    project_backend_ready,
    reserve_backend_listener,
)


def existing_data_root(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("Use an absolute path to the existing application data directory.")
    root = path.resolve(strict=True)
    # These are created by DataStore on first launch. Do not initialize a new store here.
    if not (root / "user_data").is_dir() or not (root / "kline_daily").is_dir():
        raise ValueError("Data directory is not an initialized TickStock store.")
    return root


def start_or_reuse(data_dir: Path, *, port: int = BACKEND_PORT, timeout: float = 15.0) -> int:
    try:
        listener = reserve_backend_listener(port)
    except OSError as exc:
        deadline = time.monotonic() + timeout
        while True:
            if project_backend_ready(port, data_dir, __version__, require_scheduler=True):
                print("Reusing ready TickStock backend for the selected data root.", flush=True)
                return 0
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Port {port} unavailable; could not verify a ready TickStock scheduler "
                    "at the selected data root and version. No process was started or stopped."
                ) from exc
            time.sleep(0.25)

    with listener:
        # Must be set before app.config or any storage code is imported.
        os.environ["DATA_DIR"] = str(data_dir)
        import uvicorn

        from app.main import app

        app.state.require_after_close_scheduler = True
        server = uvicorn.Server(uvicorn.Config(app, host=LOOPBACK, port=port))
        print(f"Starting TickStock backend on {LOOPBACK}:{port}.", flush=True)
        server.run(sockets=[listener])
        return 0 if server.started else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        return start_or_reuse(existing_data_root(args.data_dir))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"After-close launcher failed: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
