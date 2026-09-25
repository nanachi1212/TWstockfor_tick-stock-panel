"""Background Data Lane runtime for the Taiwan historical backfill.

One scheduled run does a bounded amount of work and exits 0.  It is safe to run
every night, safe to interrupt, and safe to start twice (the second instance
detects the lock and leaves).

Pieces
------
``WorkerLock``     single-instance lock with verified dead-owner reclamation.
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
from uuid import uuid4

import polars as pl
import psutil

from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.historical_classification import (
    REQUESTS_PER_DATE,
    HistoricalClassificationStore,
    TwseHistoricalClassifier,
    classification_queue,
)
from app.taiwan.observed_universe import (
    CensusSchemaError,
    ObservedUniverseCensus,
    ObservedUniverseStore,
    candidate_sessions,
    census_coverage,
    first_observed_dates,
    session_candidates,
)
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now

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

#: Old locks are reclaimable only when the recorded process is confirmed dead.
LOCK_MAX_AGE = timedelta(hours=26)


class WorkerBusyError(RuntimeError):
    """Another worker instance holds the lock."""


class WorkerLock:
    """O_EXCL owner file; process identity, not age, proves owner death."""

    def __init__(self, path: Path, max_age: timedelta = LOCK_MAX_AGE) -> None:
        self.path = Path(path)
        self.max_age = max_age
        self._held = False
        self._token = uuid4().hex

    @contextlib.contextmanager
    def _mutation_guard(self, *, wait: bool = False):
        # A persistent, OS-locked sidecar serializes reclaim/create/release.
        # Do not unlink the sidecar: that would create separate locking inodes.
        with self.path.with_suffix(self.path.suffix + ".guard").open("a+b") as guard:
            guard.seek(0, os.SEEK_END)
            if guard.tell() == 0:
                guard.write(b"0")
                guard.flush()
            guard.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(guard.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except OSError as exc:
                raise WorkerBusyError("worker lock transition in progress") from exc
            try:
                yield
            finally:
                guard.seek(0)
                if os.name == "nt":
                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(guard.fileno(), fcntl.LOCK_UN)

    def _owner_alive(self) -> bool | None:
        try:
            owner = json.loads(self.path.read_text(encoding="utf-8"))
            pid = owner["pid"]
            if not isinstance(pid, int) or pid <= 0:
                return None
            process = psutil.Process(pid)
            created = owner.get("process_created_at")
            if created is not None and process.create_time() != created:
                return False  # PID reused by a different process.
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except (OSError, ValueError, KeyError, TypeError, psutil.AccessDenied):
            return None  # Unknown/legacy corrupt evidence is never presumed dead.

    def owner_status(self) -> str:
        """Read current lock evidence without reclaiming or mutating the lock."""
        if not self.path.exists():
            return "idle"
        alive = self._owner_alive()
        return "running" if alive is True else "stale" if alive is False else "unknown"

    def _stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age > self.max_age.total_seconds()

    def acquire(self, *, force: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._mutation_guard():
            if self.path.exists():
                alive = self._owner_alive()
                if alive is True or (not force and (alive is None or not self._stale())):
                    raise WorkerBusyError(f"another or unverified worker holds {self.path}")
                self.path.unlink()
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "token": self._token,
                           "process_created_at": psutil.Process().create_time(),
                           "started_at": datetime.now(TAIPEI).isoformat()}, stream)
            self._held = True

    def release(self) -> None:
        if self._held:
            try:
                with self._mutation_guard(wait=True), contextlib.suppress(FileNotFoundError):
                    owner = json.loads(self.path.read_text(encoding="utf-8"))
                    if owner.get("token") == self._token:
                        self.path.unlink()
            finally:
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

    def record_failure(self, scope: str, day: date, error: str, *,
                       reason: str = "provider_error") -> None:
        key = self._key(scope, day)
        entry = self.data["attempts"].setdefault(key, {"count": 0})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_error"] = error[:500]
        entry["reason"] = reason
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

    def _pending_sessions(
        self, exchange: str, start: date, end: date, *, retry_empty: bool = False,
    ) -> list[date]:
        done = self.census_store.completed_dates(exchange)
        retryable_empty = (
            {day for day, status in self.census_store.partition_statuses(exchange).items()
             if status == "empty_unknown"}
            if retry_empty else set()
        )
        return [
            day for day in candidate_sessions(start, end, self.calendar)
            if (day not in done or day in retryable_empty)
            and not self.state.exhausted(f"census:{exchange}", day)
        ]

    def run_census(self, start: date, end: date, *, session_budget: int,
                   should_stop: Any = None,
                   retry_empty: bool = False) -> dict[str, Any]:
        """Fetch up to *session_budget* sessions per exchange (2 requests each)."""
        census = self._census or ObservedUniverseCensus(
            store=self.census_store, calendar=self.calendar)
        owns = self._census is None

        def one_exchange(exchange: str) -> dict[str, Any]:
            scope = f"census:{exchange}"
            fetch = census.fetch_twse if exchange == "TWSE" else census.fetch_tpex
            stats = {"exchange": exchange, "sessions": 0, "empty": 0,
                     "rows": 0, "failed": [], "stopped_early": False}
            # A verified calendar is independent evidence.  Never promote an
            # ordinary empty provider response to a confirmed closure.
            for day in sorted(self.calendar.known_holidays):
                if not (start <= day <= end) or day.weekday() >= 5:
                    continue
                if self.census_store.has(exchange, day):
                    status = self.census_store.partition_status(exchange, day)
                    if status == "observed":
                        logger.warning("%s %s: calendar closure conflicts with observations",
                                       exchange, day)
                        continue
                    if status == "confirmed_non_trading":
                        continue
                self.census_store.write(
                    exchange, day, [], confirmed_non_trading_source="calendar:known_holidays")
                stats["empty"] += 1
            pending = self._pending_sessions(
                exchange, start, end, retry_empty=retry_empty)
            if session_budget > UNLIMITED_BUDGET:
                pending = pending[:session_budget]
            stats["retried_empty"] = sum(
                self.census_store.partition_status(exchange, day) == "empty_unknown"
                for day in pending if self.census_store.has(exchange, day)
            )
            for index, day in enumerate(pending, start=1):
                if should_stop is not None and should_stop():
                    stats["stopped_early"] = True
                    break
                try:
                    rows = fetch(day)
                except Exception as exc:
                    with self._state_lock:
                        self.state.record_failure(
                            scope, day, f"{type(exc).__name__}: {exc}",
                            reason="schema_mismatch" if isinstance(exc, CensusSchemaError) else "provider_error")
                    stats["failed"].append(day.isoformat())
                    continue
                rechecked_empty = (
                    not rows and self.census_store.has(exchange, day)
                    and self.census_store.partition_status(exchange, day) == "empty_unknown"
                )
                written = self.census_store.write(
                    exchange, day, rows, empty_response_rechecked=rechecked_empty)
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
        for completed in stats.get("completed_dates", []):
            self.state.record_success("classify:TWSE", date.fromisoformat(completed))
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
        retry_empty: bool = False,
    ) -> dict[str, Any]:
        latest = resolve_target_latest_trading_date(self.calendar, as_of_dt=taipei_now())
        if end is not None and end > latest:
            raise ValueError(f"census end {end} exceeds latest publication session {latest}")
        end = end or latest
        self.lock.acquire(force=force_unlock)
        try:
            with StopSignal() as should_stop:
                started = time.monotonic()
                census = {} if skip_census else self.run_census(
                    start, end, session_budget=session_budget, should_stop=should_stop,
                    retry_empty=retry_empty)
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

    def verify_trading_days(
        self, *, start: date = CENSUS_START, end: date | None = None,
        force_unlock: bool = False, apply: bool = True,
    ) -> dict[str, Any]:
        """Settle unexplained-empty census days with official month tables."""
        from app.taiwan.trading_day_evidence import verify_empty_days

        latest = resolve_target_latest_trading_date(self.calendar, as_of_dt=taipei_now())
        end = min(end or latest, latest)
        self.lock.acquire(force=force_unlock)
        try:
            census = self._census or ObservedUniverseCensus(
                store=self.census_store, calendar=self.calendar)
            try:
                fetchers = {"TWSE": census.fetch_twse, "TPEX": census.fetch_tpex}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = {
                        exchange: pool.submit(
                            verify_empty_days, self.census_store, exchange,
                            start=start, end=end, census_rows=fetchers[exchange],
                            apply=apply)
                        for exchange in ("TWSE", "TPEX")
                    }
                    return {exchange: future.result() for exchange, future in futures.items()}
            finally:
                if self._census is None:
                    census.close()
        finally:
            self.lock.release()

    def resolve_instrument_types(
        self, *, refresh_evidence: bool = False, force_unlock: bool = False,
    ) -> dict[str, Any]:
        """Settle industry-only TWSE codes from official security registries."""
        from app.taiwan.instrument_evidence import (
            InstrumentEvidenceStore,
            resolve_industry_only_codes,
        )
        from app.taiwan.instrument_evidence import (
            refresh_evidence as download_evidence,
        )

        evidence = InstrumentEvidenceStore(self.data_dir / "instrument_evidence")
        self.lock.acquire(force=force_unlock)
        try:
            counts = (download_evidence(evidence, today=taipei_now().date())
                      if refresh_evidence else {})
            observations = self.census_store.read("TWSE")
            span = observations.group_by("raw_code").agg(
                pl.col("date").min().alias("first"), pl.col("date").max().alias("last"))
            first_seen = dict(zip(span["raw_code"], span["first"], strict=True))
            last_seen = dict(zip(span["raw_code"], span["last"], strict=True))
            result = resolve_industry_only_codes(
                self.classification_store, first_seen, last_seen, evidence)
            return {"evidence_downloaded": counts, **result}
        finally:
            self.lock.release()

    # ── status ─────────────────────────────────────────────────

    def status(self, *, start: date = CENSUS_START, end: date | None = None) -> dict[str, Any]:
        """Machine-readable progress snapshot. Reads only local state.

        ``completed_sessions``, ``percent``, ``earliest_completed`` and
        ``latest_completed`` are deprecated processing-progress aliases.
        Quant readiness uses the exchange-specific trading coverage fields.
        """
        # Match the worker's publication cutoff. A weekday before 16:00 Taipei
        # is not yet eligible, so it must not inflate the unresolved count.
        end = end or resolve_target_latest_trading_date(
            self.calendar, as_of_dt=taipei_now())
        total = len(set(candidate_sessions(start, end)))

        census: dict[str, Any] = {}
        for exchange in ("TWSE", "TPEX"):
            candidates = session_candidates(self.census_store, exchange, start, end)
            statuses = {day: state for day, state in
                        self.census_store.partition_statuses(exchange).items()
                        if day in candidates}
            coverage = census_coverage(
                self.census_store, exchange, candidates, partition_statuses=statuses,
                calendar=self.calendar)
            processed = set(statuses)
            sessions = {day for day, state in statuses.items() if state == "observed"}
            processed_percent = round(coverage.processed_ratio * 100, 2)
            failures = {
                date.fromisoformat(key.rsplit(":", 1)[1]): value
                for key, value in self.state.data["attempts"].items()
                if key.startswith(f"census:{exchange}:")
                and date.fromisoformat(key.rsplit(":", 1)[1]) in candidates
            }
            unresolved_by_reason: dict[str, dict[str, Any]] = {}
            confirmed_dates = self.census_store.confirmed_non_trading_dates(exchange)
            confirmed_dates.update(
                day for day in candidates
                if day not in statuses
                and self.calendar.day_evidence(day, exchange).status == "non_trading")
            for day in sorted(candidates - sessions - confirmed_dates):
                evidence = self.census_store.day_evidence(
                    exchange, day, calendar=self.calendar, failure=failures.get(day))
                entry = unresolved_by_reason.setdefault(
                    evidence.reason, {"count": 0, "dates": []})
                entry["count"] += 1
                entry["dates"].append(day.isoformat())
            census[exchange] = {
                **coverage.describe(),
                "processed_percent": processed_percent,
                "earliest_processed": min(processed).isoformat() if processed else None,
                "latest_processed": max(processed).isoformat() if processed else None,
                "earliest_trading_session": min(sessions).isoformat() if sessions else None,
                "latest_trading_session": max(sessions).isoformat() if sessions else None,
                "unresolved_by_reason": unresolved_by_reason,
                "parked_dates": self.state.parked(f"census:{exchange}"),
                "failed_date_evidence": [
                    self.census_store.day_evidence(
                        exchange, date.fromisoformat(key.split(":")[-1]),
                        calendar=self.calendar, failure=entry).describe()
                    for key, entry in sorted(self.state.data["attempts"].items())
                    if key.startswith(f"census:{exchange}:")
                    and date.fromisoformat(key.split(":")[-1]) in candidates
                ],
                # Deprecated aliases for existing CLI consumers.
                "completed_sessions": coverage.processed_dates,
                "percent": processed_percent,
                "earliest_completed": min(processed).isoformat() if processed else None,
                "latest_completed": max(processed).isoformat() if processed else None,
                "trading_sessions": coverage.observed_trading_sessions,
                "total_sessions": total,
                "percent_processed": processed_percent,
                "percent_trading_sessions": round(coverage.trading_coverage_ratio * 100, 2),
            }

        first_seen = first_observed_dates(self.census_store, "TWSE")
        wanted = sorted(set(first_seen.values()))
        completed_jobs = self.classification_store.completed_dates()
        upgrades = {d for d in completed_jobs & set(wanted)
                    if self.classification_store.needs_upgrade(d)}
        parked = set(self.state.parked("classify:TWSE"))
        pending = [d for d in wanted
                   if (d not in completed_jobs or d in upgrades)
                   and d.isoformat() not in parked]

        remaining_sessions = max(
            total - min(census["TWSE"]["processed_dates"],
                        census["TPEX"]["processed_dates"]),
            0,
        )
        return {
            "generated_at": datetime.now(TAIPEI).isoformat(),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "census": census,
            "classification": {
                "twse_codes_observed": len(first_seen),
                "unique_first_seen_dates": len(wanted),
                "completed_jobs": len((completed_jobs & set(wanted)) - upgrades),
                "jobs_needing_contract_upgrade": len(upgrades),
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
