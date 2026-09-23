"""Background Data Lane runtime for the Taiwan historical backfill.

One scheduled run does a bounded amount of work and exits 0.  It is safe to run
every night, safe to interrupt, and safe to start twice (the second instance
detects the lock and leaves).

Pieces
------
``WorkerLock``     single-instance filesystem lock with staleness expiry.
``WorkerState``    crash-safe JSON checkpoint: attempt counts, provider
                   failures/retries, ``last_success_at``.
``run_once``       one scheduled run: census within the session budget, then
                   classification within the request budget.
``status``         machine-readable progress for a future Data Health panel.

Everything it writes lives under the Taiwan data root, never in Git.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.taiwan.historical_classification import (
    REQUESTS_PER_DATE,
    HistoricalClassificationStore,
    TwseHistoricalClassifier,
    classification_queue,
)
from app.taiwan.observed_universe import (
    ObservedUniverseCensus,
    ObservedUniverseStore,
    candidate_sessions,
    first_observed_dates,
)
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

logger = logging.getLogger(__name__)

CENSUS_START = date(2015, 1, 1)

DEFAULT_SESSION_BUDGET = 300
DEFAULT_CLASSIFICATION_BUDGET = 1200

#: A budget of 0 means *unlimited* (LongRun): keep going until the work is done
#: or the operator interrupts.  Every other guarantee is unchanged — the rate
#: limiter, bounded retry, single-instance lock, atomic writes and checkpoint
#: all apply exactly as in a budgeted run.  Unlimited never raises provider rpm.
UNLIMITED_BUDGET = 0

#: In a LongRun the checkpoint must not wait for the end of the run.
_STATE_FLUSH_EVERY = 50

#: A date that keeps failing is parked rather than retried forever.
MAX_ATTEMPTS = 5

#: A lock older than this is assumed to belong to a crashed run.
LOCK_MAX_AGE = timedelta(hours=26)


class WorkerBusyError(RuntimeError):
    """Another worker instance holds the lock."""


class WorkerLock:
    """Single-instance lock. ``O_EXCL`` create, with staleness expiry."""

    def __init__(self, path: Path, max_age: timedelta = LOCK_MAX_AGE) -> None:
        self.path = Path(path)
        self.max_age = max_age
        self._held = False

    def _stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age > self.max_age.total_seconds()

    def acquire(self, *, force: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if (force or self._stale()) and self.path.exists():
            logger.warning("removing %s lock at %s",
                           "forced" if force else "stale", self.path)
            self.path.unlink(missing_ok=True)
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            holder = ""
            with contextlib.suppress(OSError):
                holder = self.path.read_text(encoding="utf-8").strip()
            raise WorkerBusyError(
                f"another taiwan backfill worker holds {self.path} ({holder}); "
                "exiting without doing work"
            ) from exc
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(),
                       "started_at": datetime.now(TAIPEI).isoformat()}, stream)
        self._held = True

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self) -> WorkerLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class WorkerState:
    """Crash-safe JSON checkpoint written atomically after every run."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "attempts": {},          # "TWSE:2015-01-05" -> {count, last_error, last_attempt}
            "providers": {},         # exchange -> {failures, retries}
            "last_success_at": None,
            "last_run_at": None,
            "runs": 0,
        }
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                logger.warning("unreadable worker state at %s; starting fresh", self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        os.close(handle)
        try:
            Path(temporary).write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8")
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    # ── attempt accounting ─────────────────────────────────────

    @staticmethod
    def _key(scope: str, day: date) -> str:
        return f"{scope}:{day.isoformat()}"

    def attempts(self, scope: str, day: date) -> int:
        return int(self.data["attempts"].get(self._key(scope, day), {}).get("count", 0))

    def exhausted(self, scope: str, day: date) -> bool:
        return self.attempts(scope, day) >= MAX_ATTEMPTS

    def record_failure(self, scope: str, day: date, error: str) -> None:
        key = self._key(scope, day)
        entry = self.data["attempts"].setdefault(key, {"count": 0})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_error"] = error[:500]
        entry["last_attempt"] = datetime.now(TAIPEI).isoformat()
        provider = self.data["providers"].setdefault(scope, {"failures": 0, "retries": 0})
        provider["failures"] += 1
        if entry["count"] > 1:
            provider["retries"] += 1

    def record_success(self, scope: str, day: date) -> None:
        self.data["attempts"].pop(self._key(scope, day), None)
        self.data["last_success_at"] = datetime.now(TAIPEI).isoformat()

    def parked(self, scope: str) -> list[str]:
        prefix = f"{scope}:"
        return sorted(
            key.removeprefix(prefix)
            for key, entry in self.data["attempts"].items()
            if key.startswith(prefix) and int(entry.get("count", 0)) >= MAX_ATTEMPTS
        )


class StopSignal:
    """Ctrl+C once → finish the current unit of work, then stop cleanly."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._previous: Any = None

    def __enter__(self) -> StopSignal:
        def handler(signum: int, frame: object) -> None:
            del signum, frame
            if self._event.is_set():
                raise KeyboardInterrupt
            logger.warning("stop requested; finishing current item then exiting")
            self._event.set()
        try:
            self._previous = signal.signal(signal.SIGINT, handler)
        except ValueError:
            self._previous = None  # not on the main thread (tests)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._previous is not None:
            with contextlib.suppress(ValueError):
                signal.signal(signal.SIGINT, self._previous)

    def __call__(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()


class TaiwanHistoricalBackfillWorker:
    """Bounded, resumable nightly worker for A2a census + A2b classification."""

    def __init__(
        self,
        data_dir: Path | None = None,
        census_store: ObservedUniverseStore | None = None,
        classification_store: HistoricalClassificationStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        census: ObservedUniverseCensus | None = None,
        classifier: TwseHistoricalClassifier | None = None,
    ) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root()
        self.data_dir = Path(data_dir)
        self.census_store = census_store or ObservedUniverseStore(
            self.data_dir / "observed_universe")
        self.classification_store = classification_store or HistoricalClassificationStore(
            self.data_dir / "historical_classification")
        self.calendar = calendar or TaiwanTradingCalendar()
        self.state = WorkerState(self.data_dir / "backfill_worker_state.json")
        self.lock = WorkerLock(self.data_dir / "backfill_worker.lock")
        self._census = census
        self._classifier = classifier
        self._state_lock = threading.Lock()

    # ── census ─────────────────────────────────────────────────

    def _pending_sessions(self, exchange: str, start: date, end: date) -> list[date]:
        done = self.census_store.completed_dates(exchange)
        return [
            day for day in candidate_sessions(start, end, self.calendar)
            if day not in done and not self.state.exhausted(f"census:{exchange}", day)
        ]

    def run_census(self, start: date, end: date, *, session_budget: int,
                   should_stop: Any = None) -> dict[str, Any]:
        """Fetch up to *session_budget* sessions per exchange (2 requests each)."""
        census = self._census or ObservedUniverseCensus(
            store=self.census_store, calendar=self.calendar)
        owns = self._census is None

        def one_exchange(exchange: str) -> dict[str, Any]:
            scope = f"census:{exchange}"
            fetch = census.fetch_twse if exchange == "TWSE" else census.fetch_tpex
            stats = {"exchange": exchange, "sessions": 0, "empty": 0,
                     "rows": 0, "failed": [], "stopped_early": False}
            pending = self._pending_sessions(exchange, start, end)
            if session_budget > UNLIMITED_BUDGET:
                pending = pending[:session_budget]
            for index, day in enumerate(pending, start=1):
                if should_stop is not None and should_stop():
                    stats["stopped_early"] = True
                    break
                try:
                    rows = fetch(day)
                except Exception as exc:
                    with self._state_lock:
                        self.state.record_failure(scope, day, f"{type(exc).__name__}: {exc}")
                    stats["failed"].append(day.isoformat())
                    continue
                written = self.census_store.write(exchange, day, rows)
                with self._state_lock:
                    self.state.record_success(scope, day)
                stats["rows"] += written
                if written:
                    stats["sessions"] += 1
                else:
                    stats["empty"] += 1
                if index % _STATE_FLUSH_EVERY == 0:
                    with self._state_lock:
                        self.state.save()
            return stats

        try:
            # Independent throttle buckets, so the two exchanges overlap
            # instead of serialising and doubling the wall clock.
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {ex: pool.submit(one_exchange, ex) for ex in ("TWSE", "TPEX")}
                results = {ex: future.result() for ex, future in futures.items()}
        finally:
            if owns:
                census.close()
        return results

    # ── classification ─────────────────────────────────────────

    def run_classification(self, *, request_budget: int,
                           should_stop: Any = None) -> dict[str, Any]:
        queue = classification_queue(self.census_store, self.classification_store)
        queue = [d for d in queue if not self.state.exhausted("classify:TWSE", d)]
        unlimited = request_budget <= UNLIMITED_BUDGET
        if not queue or (not unlimited and request_budget < REQUESTS_PER_DATE):
            return {"queue_depth": len(queue), "requests_used": 0, "dates_done": 0,
                    "rows": 0, "failed_dates": [], "stopped_early": False}
        classifier = self._classifier or TwseHistoricalClassifier(
            store=self.classification_store)
        owns = self._classifier is None
        try:
            stats = classifier.run(queue, request_budget=request_budget,
                                   should_stop=should_stop)
        finally:
            if owns:
                classifier.close()
        for failure in stats["failed_dates"]:
            self.state.record_failure("classify:TWSE", date.fromisoformat(failure["date"]),
                                      failure["error"])
        stats["queue_depth"] = len(queue)
        return stats

    # ── one scheduled run ──────────────────────────────────────

    def run_once(
        self,
        *,
        start: date = CENSUS_START,
        end: date | None = None,
        session_budget: int = DEFAULT_SESSION_BUDGET,
        classification_budget: int = DEFAULT_CLASSIFICATION_BUDGET,
        force_unlock: bool = False,
        skip_census: bool = False,
        skip_classification: bool = False,
    ) -> dict[str, Any]:
        end = end or datetime.now(TAIPEI).date()
        self.lock.acquire(force=force_unlock)
        try:
            with StopSignal() as should_stop:
                started = time.monotonic()
                census = {} if skip_census else self.run_census(
                    start, end, session_budget=session_budget, should_stop=should_stop)
                classification = (
                    {"queue_depth": 0, "requests_used": 0, "dates_done": 0, "rows": 0,
                     "failed_dates": [], "stopped_early": False}
                    if skip_classification
                    else self.run_classification(
                        request_budget=classification_budget, should_stop=should_stop)
                )
                self.state.data["last_run_at"] = datetime.now(TAIPEI).isoformat()
                self.state.data["runs"] = int(self.state.data.get("runs", 0)) + 1
                self.state.save()
                return {
                    "census": census,
                    "classification": classification,
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "stopped_early": bool(should_stop()),
                }
        finally:
            self.lock.release()

    # ── status ─────────────────────────────────────────────────

    def status(self, *, start: date = CENSUS_START, end: date | None = None) -> dict[str, Any]:
        """Machine-readable progress snapshot. Reads only local state."""
        end = end or datetime.now(TAIPEI).date()
        candidates = list(candidate_sessions(start, end, self.calendar))
        total = len(candidates)

        census: dict[str, Any] = {}
        for exchange in ("TWSE", "TPEX"):
            done = self.census_store.completed_dates(exchange)
            census[exchange] = {
                "completed_sessions": len(done),
                "total_sessions": total,
                "percent": round(len(done) / total * 100, 2) if total else 0.0,
                "earliest_completed": min(done).isoformat() if done else None,
                "latest_completed": max(done).isoformat() if done else None,
                "parked_dates": self.state.parked(f"census:{exchange}"),
            }

        first_seen = first_observed_dates(self.census_store, "TWSE")
        wanted = sorted(set(first_seen.values()))
        completed_jobs = self.classification_store.completed_dates()
        parked = set(self.state.parked("classify:TWSE"))
        pending = [d for d in wanted if d not in completed_jobs and d.isoformat() not in parked]

        remaining_sessions = max(
            total - min(census["TWSE"]["completed_sessions"],
                        census["TPEX"]["completed_sessions"]),
            0,
        )
        return {
            "generated_at": datetime.now(TAIPEI).isoformat(),
            "census": census,
            "classification": {
                "twse_codes_observed": len(first_seen),
                "unique_first_seen_dates": len(wanted),
                "completed_jobs": len(completed_jobs & set(wanted)),
                "pending_jobs": len(pending),
                "failed_jobs": len(parked),
                "requests_per_job": REQUESTS_PER_DATE,
                "exact_request_count_remaining": len(pending) * REQUESTS_PER_DATE,
            },
            "providers": self.state.data.get("providers", {}),
            "last_success_at": self.state.data.get("last_success_at"),
            "last_run_at": self.state.data.get("last_run_at"),
            "runs": self.state.data.get("runs", 0),
            "estimated_remaining_runs": {
                "census": _ceil_div(remaining_sessions, DEFAULT_SESSION_BUDGET),
                "classification": _ceil_div(
                    len(pending) * REQUESTS_PER_DATE, DEFAULT_CLASSIFICATION_BUDGET),
            },
        }


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator) if denominator > 0 else 0
