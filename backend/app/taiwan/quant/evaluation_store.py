"""Append-only provenance ledger for formal Primary OOS evaluations."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.taiwan.quant.data_health import DataHealth
from app.taiwan.quant.live_contract import canonical_json


class PrimaryOosRunStore:
    """Keep immutable run events and successful artifacts outside Git."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from app.taiwan.data_root import taiwan_data_root

            path = taiwan_data_root() / "quant" / "primary_oos_runs.sqlite3"
        self.path = Path(path)

    @staticmethod
    def progress_snapshot(progress: Mapping[str, int]) -> dict[str, int]:
        return {
            name: progress[name]
            for name in ("completed_jobs", "pending_jobs", "failed_jobs", "unique_first_seen_dates")
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('started','succeeded','failed')),
                    recorded_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS run_events_identity
                    ON run_events(identity_key, event_type, event_id);
                CREATE INDEX IF NOT EXISTS run_events_order
                    ON run_events(event_id);
                CREATE TRIGGER IF NOT EXISTS immutable_run_events_update
                    BEFORE UPDATE ON run_events BEGIN
                    SELECT RAISE(ABORT, 'immutable Primary OOS run event'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_run_events_delete
                    BEFORE DELETE ON run_events BEGIN
                    SELECT RAISE(ABORT, 'immutable Primary OOS run event'); END;
            """)
            yield db
        finally:
            db.close()

    @staticmethod
    def _context_matches(
        payload: Mapping[str, Any], *, health: DataHealth,
        progress: Mapping[str, int], spec_hash: str,
    ) -> bool:
        context = payload.get("context")
        return (
            isinstance(context, dict)
            and context.get("health") == health.describe()
            and context.get("a2b") == PrimaryOosRunStore.progress_snapshot(progress)
            and context.get("spec_hash") == spec_hash
        )

    def begin(
        self, *, run_id: str, identity_key: str, recorded_at: str,
        context: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Append a start event, or return an identical successful artifact."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT run_id, payload_json FROM run_events "
                "WHERE identity_key=? AND event_type='succeeded' ORDER BY event_id DESC LIMIT 1",
                (identity_key,),
            ).fetchone()
            if previous is not None:
                payload = json.loads(previous["payload_json"])
                artifact = payload.get("artifact")
                if not isinstance(artifact, dict):
                    raise RuntimeError("successful Primary OOS event has no artifact")
                return {
                    "run_id": previous["run_id"],
                    "artifact": artifact,
                    "reused": True,
                }
            db.execute(
                "INSERT INTO run_events(run_id,identity_key,event_type,recorded_at,payload_json) "
                "VALUES(?,?,?,?,?)",
                (run_id, identity_key, "started", recorded_at,
                 canonical_json({"context": dict(context)})),
            )
            db.commit()
        return None

    def succeed(
        self, *, run_id: str, identity_key: str, recorded_at: str,
        context: Mapping[str, Any], artifact: Mapping[str, Any],
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO run_events(run_id,identity_key,event_type,recorded_at,payload_json) "
                "VALUES(?,?,?,?,?)",
                (run_id, identity_key, "succeeded", recorded_at,
                 canonical_json({"context": dict(context), "artifact": dict(artifact)})),
            )
            db.commit()

    def fail(
        self, *, run_id: str, identity_key: str, recorded_at: str,
        context: Mapping[str, Any], error_code: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO run_events(run_id,identity_key,event_type,recorded_at,payload_json) "
                "VALUES(?,?,?,?,?)",
                (run_id, identity_key, "failed", recorded_at,
                 canonical_json({"context": dict(context), "error_code": error_code})),
            )
            db.commit()

    def _read_events(self) -> list[sqlite3.Row]:
        if not self.path.is_file():
            return []
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            return db.execute(
                "SELECT run_id,identity_key,event_type,recorded_at,payload_json "
                "FROM run_events ORDER BY event_id DESC"
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            if db is not None:
                db.close()

    def latest_success(
        self, *, health: DataHealth, progress: Mapping[str, int], spec_hash: str,
    ) -> dict[str, Any] | None:
        for event in self._read_events():
            if event["event_type"] != "succeeded":
                continue
            payload = json.loads(event["payload_json"])
            if self._context_matches(payload, health=health, progress=progress,
                                     spec_hash=spec_hash):
                artifact = payload.get("artifact")
                if isinstance(artifact, dict):
                    return {"run_id": event["run_id"], **artifact}
        return None

    def latest_state(
        self, *, health: DataHealth, progress: Mapping[str, int], spec_hash: str,
    ) -> dict[str, Any] | None:
        for event in self._read_events():
            payload = json.loads(event["payload_json"])
            if self._context_matches(payload, health=health, progress=progress,
                                     spec_hash=spec_hash):
                return {
                    "run_id": event["run_id"], "event_type": event["event_type"],
                    "recorded_at": event["recorded_at"],
                    "error_code": payload.get("error_code"),
                }
        return None
