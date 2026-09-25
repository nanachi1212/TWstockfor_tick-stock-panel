"""Materialize the historical Primary PIT factor panel from the A2a/A2b stores.

This is only the missing entry to the existing pipeline. Every factor is
computed by ``panel.build_factor_panel`` (per-session PIT adjustment, no other
scorer), rows are admitted later by ``panel_training_matrix``, and partitions
are published by ``FactorPanelStore`` (immutable, atomic). Nothing here defines
a factor, a universe or a threshold.

Symbols are computed in independent batches (the panel is quadratic in a
symbol's history and its coverage table has ~35 exception rows per point), then
re-cut by session so each published partition holds the whole cross-section.

A session without an official price bar is not a feature row, and
``build_factor_panel`` counts bars, not sessions. So each symbol's history is cut at
every missing exchange session and every contiguous run goes through
``build_factor_panel`` on its own: rolling windows, recursive (EWM) indicators and
streaks all restart and warm up again after a gap instead of bridging it. Nothing is
filled or masked after the fact.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import shutil
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker, WorkerLock
from app.taiwan.corporate_actions import CorporateActionEvent
from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
from app.taiwan.quant.panel import FactorPanel, build_factor_panel
from app.taiwan.quant.primary_oos_runner import (
    PrimaryOosInputError,
    PrimaryOosNotReadyError,
    PrimaryOosPreflight,
    _action_snapshot,
    _primary_universe,
    usable_price_bar,
)
from app.taiwan.quant.storage import FactorPanelStore
from app.taiwan.quant_eligibility import PRIMARY_VERIFIED

_OHLCV = ("open", "high", "low", "close", "volume", "amount")
_DATES_PER_PUBLISH = 25


def split_at_session_gaps(history: pl.DataFrame, sessions: Sequence[date]) -> list[pl.DataFrame]:
    """One frame per contiguous run of exchange sessions of each symbol."""
    index = {day: position for position, day in enumerate(sessions)}
    numbered = history.sort(["symbol", "date"]).with_columns(
        pl.col("date").replace_strict(index, return_dtype=pl.Int64).alias("_session"))
    numbered = numbered.with_columns(
        (pl.col("_session").diff().over("symbol").fill_null(1) != 1).cum_sum().over("symbol")
        .alias("_run"))
    return [part.drop("_session", "_run")
            for part in numbered.partition_by(["symbol", "_run"], maintain_order=True)]


_COVERAGE_SCHEMA = {
    "symbol": pl.String, "date": pl.Date, "factor": pl.String, "status": pl.String,
    "as_of": pl.String, "available_at": pl.String, "reason": pl.String, "source": pl.String,
}


def _code_fingerprint() -> str:
    """The algorithm files a batch depends on; a code change invalidates resumed batches."""
    here = Path(__file__).resolve()
    taiwan = here.parents[1]
    digest = hashlib.sha256()
    for path in (here, taiwan / "quant" / "panel.py", taiwan / "quant" / "evaluation_spec.py",
                 taiwan / "adjust.py", taiwan / "technical_indicators.py",
                 taiwan / "corporate_actions.py"):
        digest.update(path.read_bytes().replace(bytes([13, 10]), bytes([10])))
    return digest.hexdigest()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_parquet(frame: pl.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(".tmp")
    frame.write_parquet(temporary)
    os.replace(temporary, path)


def _build_batch(
    args: tuple[pl.DataFrame, tuple[CorporateActionEvent, ...], str, Path, list[date]],
) -> str:
    history, events, factor_version, out_dir, sessions = args
    key = history["symbol"][0].replace(".", "_")
    if (out_dir / f"done_{key}").exists():
        return key
    parts = [
        build_factor_panel(
            run, events=events, factor_version=factor_version,
            policy_version=PRIMARY_OOS_SPEC.policy_version, universe_tier=PRIMARY_VERIFIED,
        )
        for run in split_at_session_gaps(history, sessions)
    ]
    values = pl.concat([part.values for part in parts]).sort(["date", "symbol"])
    coverages = [part.coverage.select([pl.col(name).cast(kind) for name, kind in _COVERAGE_SCHEMA.items()])
                 for part in parts if not part.coverage.is_empty()]
    coverage = pl.concat(coverages) if coverages else pl.DataFrame(schema=_COVERAGE_SCHEMA)
    _write_parquet(values, out_dir / f"values_{key}.parquet")
    if not coverage.is_empty():
        _write_parquet(coverage, out_dir / f"coverage_{key}.parquet")
    (out_dir / f"done_{key}").write_text("ok", encoding="utf-8")
    return key


def _save_with_retry(store: FactorPanelStore, panel: FactorPanel, attempts: int = 5) -> None:
    """Publishing is idempotent (identical partitions are skipped); on Windows a
    scanner or indexer can briefly hold a freshly written directory."""
    for attempt in range(1, attempts + 1):
        try:
            store.save(panel)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(2.0 * attempt)


def _batches(symbols: Sequence[str], size: int) -> list[list[str]]:
    return [list(symbols[index:index + size]) for index in range(0, len(symbols), size)]


def build_primary_factor_panel(
    preflight: PrimaryOosPreflight,
    *,
    events: tuple[CorporateActionEvent, ...] | None = None,
    store: FactorPanelStore | None = None,
    worker: TaiwanHistoricalBackfillWorker | None = None,
    workers: int = 6,
    batch_size: int = 20,
) -> dict[str, int]:
    """Build and publish the panel once the shared Primary gate is ready.

    The shared worker lock is held from the input snapshot to the last published
    partition, so a backfill or evidence run cannot change the stores midway.
    """
    if preflight.readiness.status.value != "ready":
        raise PrimaryOosNotReadyError(preflight.readiness)
    store = store or FactorPanelStore()
    worker = worker or TaiwanHistoricalBackfillWorker()
    # One build at a time per workspace: the batch directory is shared state.
    workspace_lock = WorkerLock(store.root.parent / "primary_panel_build.lock")
    workspace_lock.acquire()
    try:
        return _build_guarded(worker, store, events, workers, batch_size)
    finally:
        workspace_lock.release()


def _build_guarded(
    worker: TaiwanHistoricalBackfillWorker, store: FactorPanelStore,
    events: tuple[CorporateActionEvent, ...] | None, workers: int, batch_size: int,
) -> dict[str, int]:
    # Snapshot under the lock, compute without it (hours), then revalidate the same
    # snapshot under the lock right before publishing: a store that changed in
    # between makes the build fail instead of publishing a mixed generation.
    worker.lock.acquire()
    try:
        snapshot = _snapshot(worker, events)
    finally:
        worker.lock.release()
    _compute_batches(snapshot, store, workers, batch_size)
    worker.lock.acquire()
    try:
        if _snapshot(worker, snapshot["events"])["identity_inputs"] != snapshot["identity_inputs"]:
            raise PrimaryOosInputError(
                "census or classification changed while the panel was computed; nothing was published")
        return _publish(snapshot, store)
    finally:
        worker.lock.release()


def _snapshot(
    worker: TaiwanHistoricalBackfillWorker, events: tuple[CorporateActionEvent, ...] | None,
) -> dict[str, Any]:
    universe, _identity = _primary_universe(worker.census_store, worker.classification_store)
    members = universe.filter(
        (pl.col("instrument_type_status") == "verified") & (pl.col("instrument_type") == "stock")
        & pl.col("price_bar_available")
    )
    if members.is_empty():
        raise PrimaryOosInputError("no verified historical Primary stock rows to materialize")
    symbols = sorted(members["market_symbol"].unique().to_list())

    raw = worker.census_store.read("TWSE").select(
        pl.concat_str([pl.col("raw_code"), pl.lit(".TWSE")]).alias("symbol"),
        "date", *_OHLCV,
    ).filter(usable_price_bar())
    history = raw.join(members.select(pl.col("market_symbol").alias("symbol")).unique(),
                       on="symbol", how="semi").sort(["symbol", "date"])
    if history.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise PrimaryOosInputError("A2a raw Primary daily prices are duplicated")
    first, last = history["date"].min(), history["date"].max()
    if events is None:
        events, coverage = _action_snapshot(first, last, required_symbols=frozenset(symbols))
        if not coverage.covers(first, last):
            raise PrimaryOosInputError("corporate-action source coverage is incomplete")
    sessions = sorted(worker.census_store.session_dates("TWSE"))
    inputs = {
        "history": _digest(str(int(history.hash_rows(seed=1).sum()))
                           + str(int(history.hash_rows(seed=2).sum()))),
        "events": _digest("".join(sorted(event.content_hash for event in events))),
        "sessions": _digest(",".join(day.isoformat() for day in sessions)),
        "universe": _digest(str(int(universe.hash_rows(seed=1).sum()))),
    }
    unresolved = sorted(
        day for day, status in worker.census_store.partition_statuses("TWSE").items()
        if status == "empty_unknown" and first <= day <= last)
    if unresolved:
        # An official session without rows would make neighbouring bars look
        # consecutive; recover it (or prove it a holiday) before publishing.
        raise PrimaryOosInputError(
            f"unresolved empty sessions inside the panel span: {[d.isoformat() for d in unresolved[:5]]}")

    return {"history": history, "symbols": symbols, "events": events, "sessions": sessions,
            "last": last, "identity_inputs": inputs}


def _compute_batches(
    snapshot: dict[str, Any], store: FactorPanelStore, workers: int, batch_size: int,
) -> None:
    history, symbols = snapshot["history"], snapshot["symbols"]
    events, sessions = snapshot["events"], snapshot["sessions"]
    # The batch results are hours of compute: keep them until every partition is
    # published, so an interrupted or failed publish resumes without recomputing.
    work = store.root.parent / "primary_panel_build"
    identity = {
        "rows": history.height, "symbols": len(symbols), "last_date": snapshot["last"].isoformat(),
        "factor_version": PRIMARY_OOS_SPEC.factor_version, "batch_size": batch_size,
        # Content digests: a same-sized correction must not reuse stale batches.
        **snapshot["identity_inputs"], "code": _code_fingerprint(),
        "spec": PRIMARY_OOS_SPEC.fingerprint,
    }
    marker = work / "_identity.json"
    if not (marker.is_file() and json.loads(marker.read_text(encoding="utf-8")) == identity):
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        marker.write_text(json.dumps(identity), encoding="utf-8")
    jobs = [(history.filter(pl.col("symbol").is_in(batch)), events,
             PRIMARY_OOS_SPEC.factor_version, work, sessions)
            for batch in _batches(symbols, batch_size)]
    # Polars is not fork-safe: a forked child can deadlock on its thread pool.
    with ProcessPoolExecutor(max_workers=workers,
                             mp_context=multiprocessing.get_context("spawn")) as pool:
        list(pool.map(_build_batch, jobs))


def _publish(snapshot: dict[str, Any], store: FactorPanelStore) -> dict[str, int]:
    history, symbols = snapshot["history"], snapshot["symbols"]
    work = store.root.parent / "primary_panel_build"
    values = pl.scan_parquet(work / "values_*.parquet")
    coverage = (pl.scan_parquet(work / "coverage_*.parquet")
                if any(work.glob("coverage_*.parquet")) else None)
    unverified = values.filter(
        (pl.col("adjustment_status") != "verified") | (pl.col("usage_scope") != "pit_feature")
    ).select(pl.col("symbol").unique()).collect()["symbol"].to_list()
    if unverified:
        raise PrimaryOosInputError(
            f"PIT factor rows with unverified adjustment for {sorted(unverified)[:10]}; "
            "nothing was published")
    days = values.select("date").unique().sort("date").collect()["date"].to_list()
    published = 0
    for index in range(0, len(days), _DATES_PER_PUBLISH):
        chunk: list[date] = days[index:index + _DATES_PER_PUBLISH]
        value_frame = values.filter(pl.col("date").is_in(chunk)).collect().sort(["date", "symbol"])
        coverage_frame = (coverage.filter(pl.col("date").is_in(chunk)).collect()
                          .sort(["date", "symbol", "factor"])
                          if coverage is not None else pl.DataFrame())
        _save_with_retry(store, FactorPanel(
            value_frame, coverage_frame, PRIMARY_OOS_SPEC.factor_version,
            PRIMARY_OOS_SPEC.policy_version, PRIMARY_VERIFIED,
        ))
        published += len(chunk)
    shutil.rmtree(work, ignore_errors=True)
    return {"symbols": len(symbols), "sessions": published, "rows": history.height}
