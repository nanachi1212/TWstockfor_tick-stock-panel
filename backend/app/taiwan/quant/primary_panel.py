"""Materialize the historical Primary PIT factor panel from the A2a/A2b stores.

This is only the missing entry to the existing pipeline. Every factor is
computed by ``panel.build_factor_panel`` (per-session PIT adjustment, no other
scorer), rows are admitted later by ``panel_training_matrix``, and partitions
are published by ``FactorPanelStore`` (immutable, atomic). Nothing here defines
a factor, a universe or a threshold.

Symbols are computed in independent batches (the panel is quadratic in a
symbol's history and its coverage table has ~35 exception rows per point), then
re-cut by session so each published partition holds the whole cross-section.
"""
from __future__ import annotations

import json
import multiprocessing
import shutil
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import polars as pl

from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
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


def _build_batch(args: tuple[pl.DataFrame, tuple[CorporateActionEvent, ...], str, Path]) -> str:
    history, events, factor_version, out_dir = args
    panel = build_factor_panel(
        history, events=events, factor_version=factor_version,
        policy_version=PRIMARY_OOS_SPEC.policy_version, universe_tier=PRIMARY_VERIFIED,
    )
    key = history["symbol"][0].replace(".", "_")
    panel.values.write_parquet(out_dir / f"values_{key}.parquet")
    if not panel.coverage.is_empty():
        panel.coverage.write_parquet(out_dir / f"coverage_{key}.parquet")
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
    """Build and publish the panel once the shared Primary gate is ready."""
    if preflight.readiness.status.value != "ready":
        raise PrimaryOosNotReadyError(preflight.readiness)
    store = store or FactorPanelStore()
    worker = worker or TaiwanHistoricalBackfillWorker()
    universe, _identity = _primary_universe(worker.census_store, worker.classification_store)
    members = universe.filter(
        (pl.col("instrument_type_status") == "verified") & (pl.col("instrument_type") == "stock")
        & pl.col("observed_on_market")
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

    # The batch results are hours of compute: keep them until every partition is
    # published, so an interrupted or failed publish resumes without recomputing.
    work = store.root.parent / "primary_panel_build"
    identity = {"rows": history.height, "symbols": len(symbols), "last_date": last.isoformat(),
                "events": len(events), "factor_version": PRIMARY_OOS_SPEC.factor_version}
    done = work / "_batches_complete.json"
    if not (done.is_file() and json.loads(done.read_text(encoding="utf-8")) == identity):
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        jobs = [(history.filter(pl.col("symbol").is_in(batch)), events,
                 PRIMARY_OOS_SPEC.factor_version, work)
                for batch in _batches(symbols, batch_size)]
        # Polars is not fork-safe: a forked child can deadlock on its thread pool.
        with ProcessPoolExecutor(max_workers=workers,
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            list(pool.map(_build_batch, jobs))
        done.write_text(json.dumps(identity), encoding="utf-8")
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
