"""Taiwan Daily Refresh Service (Phase 6B-Data Foundation).

Drives incremental batch refresh from TaiwanHybridProvider into
TaiwanDailyStore.  All provider calls are bounded-concurrency,
resume-capable, and never attempt a full 2335-symbol sweep in one shot.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.universe import get_security_master

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 5
DEFAULT_START_DATE = "2024-01-01"


def _get_universe() -> list[str]:
    """Return canonical symbols supported for screening."""
    master = get_security_master()
    rows = master.to_provider_dataframe(asset_type="stock")
    etf_rows = master.to_provider_dataframe(asset_type="etf")
    combined = pl.concat([rows, etf_rows], how="diagonal_relaxed")
    if combined.is_empty():
        return []
    return sorted(combined["symbol"].unique().to_list())


def _need_dates(store: TaiwanDailyStore, symbols: list[str], start: date, end: date) -> dict[str, list[date]]:
    """For each symbol, return the dates that are missing from storage."""
    existing: dict[str, set[date]] = {}
    for sym in symbols:
        existing[sym] = set()
    dates_present = store.available_dates()
    if not dates_present:
        return {sym: [d for d in _iter_dates(start, end)] for sym in symbols}

    # Efficient: read all partitions once and index by (symbol, date).
    all_rows = store.read_range(symbols, start, end)
    if not all_rows.is_empty():
        for row in all_rows.iter_rows(named=True):
            existing[row["symbol"]].add(row["date"])

    needed: dict[str, list[date]] = {}
    for sym in symbols:
        needed[sym] = [d for d in _iter_dates(start, end) if d not in existing[sym]]
    return needed


def _iter_dates(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


class TaiwanDailyRefreshService:
    """Refresh Taiwan daily data into TaiwanDailyStore.

    - Universe from TaiwanSecurityMaster (is_supported=True, stock/etf, TWSE/TPEX)
    - Incremental: only fetches dates missing in store
    - Bounded concurrency (default 5) to respect provider rate limits
    - Resume-capable: already-persisted symbols/dates are skipped
    """

    def __init__(
        self,
        store: TaiwanDailyStore | None = None,
        provider: TaiwanHybridProvider | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        start_date: str = DEFAULT_START_DATE,
    ) -> None:
        self._store = store or TaiwanDailyStore()
        self._provider = provider or TaiwanHybridProvider()
        self._concurrency = concurrency
        self._start_date = date.fromisoformat(start_date)

    def refresh_symbols(
        self,
        symbols: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        """Refresh *symbols* between *start* and *end* into the store.

        Args:
            symbols: Canonical symbols to refresh.  If *None*, uses
                     the TaiwanSecurityMaster universe.
            start:  Earliest date to ensure coverage for.
            end:    Latest date to ensure coverage for.

        Returns:
            Dict with counts and per-symbol stats.
        """
        if symbols is None:
            symbols = _get_universe()
        if not symbols:
            return {"error": "no symbols in universe"}

        end = end or date.today()
        start = start or self._start_date
        if start > end:
            return {"error": "start > end"}

        # Determine missing dates per symbol.
        needed = _need_dates(self._store, symbols, start, end)

        # Flatten (symbol, date) pairs, but only fetch per symbol once
        # per contiguous date range.  The provider returns a DataFrame
        # spanning start..end for a given symbol, so we fetch once per
        # symbol that has ANY missing date.
        symbols_to_fetch = [s for s in symbols if needed[s]]
        if not symbols_to_fetch:
            return {"message": "all dates up to date", "symbols_fetched": 0, "rows_written": 0}

        stats: dict[str, Any] = {
            "symbols_total": len(symbols),
            "symbols_fetched": 0,
            "rows_written": 0,
            "failed": [],
            "per_symbol": {},
        }

        with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
            futures = {
                executor.submit(self._fetch_and_store, sym, start, end): sym
                for sym in symbols_to_fetch
            }
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    result = future.result()
                    stats["symbols_fetched"] += 1
                    stats["rows_written"] += result.get("rows", 0)
                    stats["per_symbol"][sym] = result
                except Exception as exc:
                    logger.warning("Refresh failed for %s: %s", sym, exc)
                    stats["failed"].append(sym)
                    stats["per_symbol"][sym] = {"error": str(exc)}

        return stats

    def _fetch_and_store(self, symbol: str, start: date, end: date) -> dict[str, Any]:
        """Fetch a single symbol's daily data and store it atomically."""
        df = self._provider.get_daily([symbol], start_time=start, end_time=end)
        if df.is_empty():
            return {"symbol": symbol, "rows": 0, "status": "empty"}

        rows = df.height
        written = self._store.write_batch(df)
        return {"symbol": symbol, "rows": rows, "written": written, "status": "ok"}

    def refresh_today(self) -> dict[str, Any]:
        """Refresh all universe symbols for today only."""
        return self.refresh_symbols(start=date.today(), end=date.today())

    def refresh_range(self, start: date, end: date) -> dict[str, Any]:
        """Refresh all universe symbols for a date range."""
        return self.refresh_symbols(start=start, end=end)

    def fetch_sample(self, symbols: list[str], start: date, end: date) -> pl.DataFrame:
        """Fetch a small sample of symbols for validation (bypasses store)."""
        if not symbols:
            return pl.DataFrame()
        return self._provider.get_daily(symbols, start_time=start, end_time=end)
