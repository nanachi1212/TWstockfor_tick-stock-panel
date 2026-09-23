"""Append-only live events, physically separate from factor/OOS persistence.

SQLite transactions serialize concurrent writers across processes. Signals and
model activations cannot be updated or deleted even through SQL. Outcomes use a
different database and append observations; conflicting results remain visible.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.quant.live_contract import (
    HORIZONS,
    LIVE_CONTRACT,
    EvidenceReader,
    LiveModel,
    LiveSignalBatch,
    canonical_hash,
    canonical_json,
    latest_completed_session,
)
from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now


class LiveConflictError(ValueError):
    """An immutable identity already has different content; audit was retained."""


class LiveLedger:
    def __init__(self, root: Path | None = None, *,
                 clock: Callable[[], datetime] = taipei_now,
                 evidence: EvidenceReader | None = None) -> None:
        if root is None:
            from app.taiwan.data_root import taiwan_data_root

            root = taiwan_data_root() / "live_quant"
        self.root = Path(root)
        self.clock = clock
        self.evidence = evidence

    @contextmanager
    def _connect(self, outcomes: bool = False):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / ("outcomes.sqlite3" if outcomes else "signals.sqlite3"),
                             timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        if outcomes:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observations (
                    model_key TEXT NOT NULL, session TEXT NOT NULL, symbol TEXT NOT NULL,
                    horizon INTEGER NOT NULL CHECK(horizon IN (1,5,20)),
                    digest TEXT NOT NULL, payload TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    PRIMARY KEY(model_key,session,symbol,horizon,digest));
            """)
            tables = ("observations",)
        else:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS models (
                    model_key TEXT PRIMARY KEY, definition TEXT NOT NULL,
                    first_live_session TEXT NOT NULL, activated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    model_key TEXT NOT NULL REFERENCES models(model_key), session TEXT NOT NULL,
                    digest TEXT NOT NULL, snapshot TEXT NOT NULL, frozen_at TEXT NOT NULL,
                    PRIMARY KEY(model_key,session));
                CREATE TABLE IF NOT EXISTS conflicts (
                    model_key TEXT NOT NULL, session TEXT NOT NULL, digest TEXT NOT NULL,
                    payload TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    PRIMARY KEY(model_key,session,digest));
                CREATE TABLE IF NOT EXISTS operations (
                    recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            tables = ("models", "runs", "conflicts", "operations")
        for table in tables:
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action}
                    BEFORE {action} ON {table} BEGIN
                    SELECT RAISE(ABORT, 'immutable live event'); END""")
        try:
            with db:
                yield db
        finally:
            db.close()

    def current_session(self):
        if self.evidence is None:
            raise ValueError("writer requires verified session evidence")
        before = self.clock()
        session = latest_completed_session(before, self.evidence)
        after = self.clock()

        if self._boundary(before) != self._boundary(after):
            raise ValueError("publication_boundary_changed_retry")
        return session

    @staticmethod
    def _boundary(stamp):
        local = stamp.astimezone(TAIPEI_TZ)
        return local.date() - timedelta(days=int(local.hour < 16))

    def activate(self, model: LiveModel) -> dict[str, Any]:
        definition = canonical_json(model.describe())
        # Resolve remote evidence before taking a write lock. Recheck identity
        # after acquiring the transaction so concurrent activations stay atomic.
        with self._connect() as db:
            existing = db.execute("SELECT * FROM models WHERE model_key=?", (model.key,)).fetchone()
            if existing:
                if existing["definition"] != definition:
                    raise LiveConflictError("model definition changed without new version")
                return self._model(existing)
        boundary = self._boundary(self.clock())
        session = self.current_session()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM models WHERE model_key=?", (model.key,)).fetchone()
            if existing:
                if existing["definition"] != definition:
                    raise LiveConflictError("model definition changed without new version")
                return self._model(existing)
            if self._boundary(self.clock()) != boundary:
                raise ValueError("publication_boundary_changed_retry")
            db.execute("INSERT INTO models VALUES (?,?,?,?)",
                       (model.key, definition, session.isoformat(), self.clock().isoformat()))
            return self._model(db.execute("SELECT * FROM models WHERE model_key=?", (model.key,)).fetchone())

    @staticmethod
    def _model(row) -> dict[str, Any]:
        return {**json.loads(row["definition"]), "first_live_session": row["first_live_session"],
                "activated_at": row["activated_at"]}

    def models(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [self._model(row) for row in db.execute("SELECT * FROM models ORDER BY activated_at")]

    def freeze(self, batch: LiveSignalBatch) -> dict[str, Any]:
        if not isinstance(batch, LiveSignalBatch):
            raise TypeError("only a LiveSignalBatch can enter the live writer")
        session = batch.session.isoformat()
        snapshot = json.loads(canonical_json(batch.snapshot))
        if snapshot.get("contract") != LIVE_CONTRACT or snapshot.get("signal_session") != session:
            raise ValueError("live universe/session contract required")
        cutoff = datetime.fromisoformat(snapshot["data_cutoff"])
        if (cutoff.utcoffset() is None or not market_close(batch.session) <= cutoff <= self.clock()
                or snapshot.get("model") != json.loads(canonical_json(batch.model.describe()))
                or snapshot.get("usage_scope") != "experimental_live"
                or snapshot.get("validation_state") != "unvalidated"):
            raise ValueError("live snapshot provenance mismatch")
        if {"origin", "fold_index", "forward_return", "oos_prediction"} & set(snapshot):
            raise ValueError("OOS predictions cannot enter live storage")
        eligible = {row["symbol"] for row in snapshot["eligible_universe"]}
        feature_symbols = {row["symbol"] for row in snapshot["features"]}
        signal_symbols = [row["symbol"] for row in snapshot["signals"]]
        if (not set(signal_symbols) <= eligible & feature_symbols
                or len(signal_symbols) != len(set(signal_symbols))
                or len(signal_symbols) > batch.model.top_n
                or canonical_hash(snapshot["features"]) != snapshot.get("feature_snapshot_hash")):
            raise ValueError("live signal/feature identity mismatch")
        if any(row.get("universe_contract") != LIVE_CONTRACT
               for row in snapshot["eligible_universe"]):
            raise ValueError("historical universe rejected by live writer")
        # No origin flag, OOS row importer, or caller-supplied activation date.
        boundary = self._boundary(self.clock())
        if batch.session != self.current_session():
            raise ValueError("live freeze only accepts latest completed session")
        payload = canonical_json(snapshot)
        digest = canonical_hash(snapshot)
        conflict = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._boundary(self.clock()) != boundary:
                raise ValueError("publication_boundary_changed_retry")
            model = db.execute("SELECT * FROM models WHERE model_key=?", (batch.model.key,)).fetchone()
            if model is None:
                raise ValueError("activate live model before freeze")
            if model["definition"] != canonical_json(batch.model.describe()):
                raise LiveConflictError("model definition changed")
            if session < model["first_live_session"]:
                raise ValueError("signal before first live session")
            prior = db.execute("SELECT digest FROM runs WHERE model_key=? AND session=?",
                               (batch.model.key, session)).fetchone()
            if prior:
                if prior["digest"] == digest:
                    return {"status": "noop", "session": session, "snapshot_hash": digest}
                db.execute("INSERT OR IGNORE INTO conflicts VALUES (?,?,?,?,?)",
                           (batch.model.key, session, digest, payload, self.clock().isoformat()))
                conflict = True
            else:
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                           (batch.model.key, session, digest, payload, self.clock().isoformat()))
        if conflict:
            raise LiveConflictError("signal conflict recorded; original signal unchanged")
        return {"status": "frozen", "session": session, "snapshot_hash": digest,
                "signal_count": len(snapshot["signals"])}

    def read_run(self, key: str, session: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE model_key=? AND session=?", (key, session)).fetchone()
            if row is None:
                return None
            conflicts = db.execute("SELECT digest,recorded_at FROM conflicts WHERE model_key=? AND session=?",
                                   (key, session)).fetchall()
            return {"model_key": key, "session": session, "snapshot_hash": row["digest"],
                    "frozen_at": row["frozen_at"], "snapshot": json.loads(row["snapshot"]),
                    "audit_status": "conflict" if conflicts else "ok",
                    "conflicts": [dict(x) for x in conflicts]}

    def runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT model_key,session FROM runs ORDER BY session DESC LIMIT ?",
                              (min(max(limit, 1), 1000),)).fetchall()
        return [self.read_run(row["model_key"], row["session"]) for row in rows]

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT model_key,session,digest AS snapshot_hash,frozen_at,
                    json_array_length(snapshot,'$.signals') AS signal_count
                   FROM runs ORDER BY session DESC LIMIT ?""", (min(max(limit, 1), 100),))]

    def record_operation(self, result: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO operations VALUES (?,?)",
                       (self.clock().isoformat(), canonical_json(result)))

    def outcome_runs(self) -> list[dict[str, Any]]:
        """Small projection; maturation must not deserialize years of features."""
        with self._connect() as db:
            return [{"model_key": row["model_key"], "session": row["session"],
                     "signals": json.loads(row["signals"])} for row in db.execute(
                         """SELECT model_key,session,json_extract(snapshot,'$.signals') AS signals
                            FROM runs ORDER BY session""")]

    def latest_operation(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations ORDER BY rowid DESC LIMIT 1").fetchone()
            return {**json.loads(row["payload"]), "recorded_at": row["recorded_at"]} if row else None

    def observe_outcome(self, key: str, session: str, symbol: str, horizon: int,
                        outcome: dict[str, Any]) -> str:
        with self._connect() as db:
            row = db.execute("""SELECT json_extract(snapshot,'$.signals') FROM runs
                                WHERE model_key=? AND session=?""", (key, session)).fetchone()
        if horizon not in HORIZONS or row is None or symbol not in {
                signal["symbol"] for signal in json.loads(row[0])}:
            raise ValueError("outcome must reference a frozen live signal/horizon")
        if outcome.get("status") not in ("pending", "data_insufficient", "verified"):
            raise ValueError("invalid outcome status")
        if outcome["status"] == "verified":
            value = outcome.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("verified outcome requires a finite return")
            latest = self.current_session()
            cursor = date.fromisoformat(session)
            count = 0
            while cursor < latest and count < horizon:
                cursor += timedelta(days=1)
                evidence = self.evidence(cursor, symbol.split(".")[1])
                if evidence.status == "unresolved":
                    raise ValueError("outcome session evidence unresolved")
                count += evidence.status == "trading"
            if count != horizon or str(outcome.get("end_session")) != cursor.isoformat():
                raise ValueError("outcome horizon not mature or end session mismatch")
        elif outcome.get("value") is not None:
            raise ValueError("unavailable outcome cannot contain a return")
        payload = canonical_json(outcome)
        digest = canonical_hash(outcome)
        identity = (key, session, symbol, horizon)
        with self._connect(outcomes=True) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT payload,digest FROM observations
                WHERE model_key=? AND session=? AND symbol=? AND horizon=?""", identity).fetchall()
            if any(row["digest"] == digest for row in rows):
                return ("conflict" if sum(json.loads(row["payload"])["status"] == "verified"
                                          for row in rows) > 1 else "noop")
            prior_verified = [json.loads(row["payload"]) for row in rows
                              if json.loads(row["payload"])["status"] == "verified"]
            db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)",
                       (*identity, digest, payload, self.clock().isoformat()))
        if prior_verified and outcome["status"] == "verified" and any(
                row != json.loads(payload) for row in prior_verified):
            raise LiveConflictError("outcome conflict recorded; original result unchanged")
        return "appended"

    def outcomes(self, key: str, session: str) -> list[dict[str, Any]]:
        run = self.read_run(key, session)
        if run is None:
            return []
        return self.signal_outcomes(key, session, run["snapshot"]["signals"])

    def signal_outcomes(self, key: str, session: str, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._connect(outcomes=True) as db:
            rows = db.execute("""SELECT * FROM observations WHERE model_key=? AND session=?
                ORDER BY rowid""", (key, session)).fetchall()
        result = []
        for signal in signals:
            for horizon in HORIZONS:
                matches = [json.loads(row["payload"]) for row in rows
                           if row["symbol"] == signal["symbol"] and row["horizon"] == horizon]
                verified = [row for row in matches if row["status"] == "verified"]
                value = verified[0] if verified else matches[-1] if matches else {
                    "status": "pending", "value": None, "reason": "horizon_not_mature"}
                result.append({"symbol": signal["symbol"], "horizon": horizon, **value,
                               "status": "conflict" if len(verified) > 1 else value["status"],
                               "observations": matches})
        return result
