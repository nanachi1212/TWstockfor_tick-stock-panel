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
``build_factor_panel`` counts bars, not sessions. The only addition to its output
is therefore a mask: a rolling factor whose lookback window (its published
``min_history`` bars) is not made of consecutive exchange sessions is set to
unavailable, with a coverage exception. Nothing is filled or recomputed.
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

import polars as pl

from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
from app.taiwan.corporate_actions import CorporateActionEvent
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
from app.taiwan.quant.panel import (
    RELATIVE,
    TECHNICAL,
    FactorPanel,
    build_factor_panel,
)
from app.taiwan.quant.primary_oos_runner import (
    PrimaryOosInputError,
    PrimaryOosNotReadyError,
    PrimaryOosPreflight,
    _action_snapshot,
    _primary_universe,
    usable_price_bar,
)
from app.taiwan.quant.storage import FactorPanelStore, _min_history
from app.taiwan.quant_eligibility import PRIMARY_VERIFIED

_OHLCV = ("open", "high", "low", "close", "volume", "amount")
_DATES_PER_PUBLISH = 25
#: Factors that read a rolling window of the symbol's own price/volume bars.
_WINDOWED = tuple(name for name in (*TECHNICAL, "relative_volume", "adv20_twd", *RELATIVE)
                  if _min_history(name) > 1)
_COVERAGE_SCHEMA = {
    "symbol": pl.String, "date": pl.Date, "factor": pl.String, "status": pl.String,
    "as_of": pl.String, "available_at": pl.String, "reason": pl.String, "source": pl.String,
}


def _streak_crosses_gap(frame: pl.DataFrame) -> pl.Series:
    """True where the |macd_hist_streak| most recent bars are not consecutive sessions."""
    flags = [False] * frame.height
    position = 0
    for _symbol, rows in frame.group_by("symbol", maintain_order=True):
        sessions = rows["_session"].to_list()
        for offset, streak in enumerate(rows["macd_hist_streak"].to_list()):
            if streak is None or streak == 0:
                continue
            span = int(abs(streak)) - 1
            if span > offset or sessions[offset] - sessions[offset - span] != span:
                flags[position + offset] = True
        position += rows.height
    return pl.Series("_streak_gap", flags, dtype=pl.Boolean)


def mask_missing_session_windows(
    values: pl.DataFrame, sessions: Sequence[date],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Unavailable where the factor's bar window skips an exchange session.

    Returns the masked values and the coverage exceptions for every value that
    was available before the mask. ``sessions`` is the exchange's session list.
    """
    index = {day: position for position, day in enumerate(sessions)}
    frame = values.sort(["symbol", "date"]).with_columns(
        pl.col("date").replace_strict(index, return_dtype=pl.Int64).alias("_session"))
    masks = {
        name: ((pl.col("_session") - pl.col("_session").shift(_min_history(name) - 1)
                .over("symbol")) != _min_history(name) - 1)
        for name in _WINDOWED
    }
    # macd_hist_streak counts back until the histogram changes sign, so its span is
    # the value itself, not a fixed window: it must not reach across a session gap.
    frame = frame.with_columns(_streak_crosses_gap(frame).alias("_streak_gap"))
    masks["macd_hist_streak"] = masks["macd_hist_streak"] | pl.col("_streak_gap")
    flagged = frame.select(
        "symbol", "date",
        *[(pl.col(name).is_not_null() & masks[name]).alias(name) for name in _WINDOWED],
    )
    closes = {day: market_close(day).isoformat() for day in frame["date"].unique().to_list()}
    exceptions = []
    for name in _WINDOWED:
        rows = flagged.filter(pl.col(name)).select("symbol", "date")
        if rows.height:
            exceptions.append(rows.with_columns(
                pl.lit(name).alias("factor"), pl.lit("data_insufficient").alias("status"),
                pl.col("date").replace_strict(closes, return_dtype=pl.String).alias("as_of"),
                pl.lit(None, dtype=pl.String).alias("available_at"),
                pl.lit("missing_session_in_window").alias("reason"),
                pl.lit("pit_adjusted_daily").alias("source"),
            ).select(list(_COVERAGE_SCHEMA)))
    masked = frame.with_columns(
        [pl.when(masks[name]).then(None).otherwise(pl.col(name)).alias(name) for name in _WINDOWED]
    ).drop("_session", "_streak_gap").sort(["date", "symbol"])
    return masked, (pl.concat(exceptions) if exceptions else pl.DataFrame(schema=_COVERAGE_SCHEMA))


def _code_fingerprint() -> str:
    """The algorithm files a batch depends on; a code change invalidates resumed batches."""
    here = Path(__file__).resolve()
    taiwan = here.parents[1]
    digest = hashlib.sha256()
    for path in (here, taiwan / "quant" / "panel.py", taiwan / "adjust.py",
                 taiwan / "technical_indicators.py", taiwan / "corporate_actions.py"):
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
    panel = build_factor_panel(
        history, events=events, factor_version=factor_version,
        policy_version=PRIMARY_OOS_SPEC.policy_version, universe_tier=PRIMARY_VERIFIED,
    )
    values, masked = mask_missing_session_windows(panel.values, sessions)
    coverage = (pl.concat([panel.coverage.select(
                    [pl.col(name).cast(kind) for name, kind in _COVERAGE_SCHEMA.items()]), masked])
                if not panel.coverage.is_empty() else masked)
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
    worker.lock.acquire()
    try:
        return _build_locked(worker, store, events, workers, batch_size)
    finally:
        worker.lock.release()


def _build_locked(
    worker: TaiwanHistoricalBackfillWorker, store: FactorPanelStore,
    events: tuple[CorporateActionEvent, ...] | None, workers: int, batch_size: int,
) -> dict[str, int]:
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
    unresolved = sorted(
        day for day, status in worker.census_store.partition_statuses("TWSE").items()
        if status == "empty_unknown" and first <= day <= last)
    if unresolved:
        # An official session without rows would make neighbouring bars look
        # consecutive; recover it (or prove it a holiday) before publishing.
        raise PrimaryOosInputError(
            f"unresolved empty sessions inside the panel span: {[d.isoformat() for d in unresolved[:5]]}")

    # The batch results are hours of compute: keep them until every partition is
    # published, so an interrupted or failed publish resumes without recomputing.
    work = store.root.parent / "primary_panel_build"
    identity = {
        "rows": history.height, "symbols": len(symbols), "last_date": last.isoformat(),
        "factor_version": PRIMARY_OOS_SPEC.factor_version, "batch_size": batch_size,
        # Content digests: a same-sized correction must not reuse stale batches.
        "history": _digest(str(int(history.hash_rows(seed=1).sum()))
                           + str(int(history.hash_rows(seed=2).sum()))),
        "events": _digest("".join(sorted(event.content_hash for event in events))),
        "sessions": _digest(",".join(day.isoformat() for day in sessions)),
        "code": _code_fingerprint(),
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
