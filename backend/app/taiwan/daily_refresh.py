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
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
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


def _is_potential_trading_day(d: date, calendar: TaiwanTradingCalendar) -> bool:
    """Check if date could be a trading day (confirmed True, or unverified None).

    Confirmed non-trading days (weekends, statutory holidays) return False.
    """
    status = calendar.is_trading_day(d)
    return status is not False


def _iter_candidate_dates(start: date, end: date, calendar: TaiwanTradingCalendar):
    """Yield candidate dates between start and end, skipping confirmed non-trading days."""
    cur = start
    while cur <= end:
        if _is_potential_trading_day(cur, calendar):
            yield cur
        cur += timedelta(days=1)


def _coalesce_dates_to_ranges(dates: list[date]) -> list[tuple[date, date]]:
    """Coalesce sorted candidate dates into contiguous start..end ranges.

    If two candidate trading dates have only confirmed non-trading days (e.g. weekend)
    between them, they are merged into a single range to avoid multiple small HTTP requests.
    """
    if not dates:
        return []
    sorted_dates = sorted(dates)
    ranges: list[tuple[date, date]] = []
    r_start = sorted_dates[0]
    r_end = sorted_dates[0]

    for d in sorted_dates[1:]:
        # If gap is <= 3 days (e.g. Friday to Monday is 3 days), merge into one range
        if (d - r_end).days <= 3:
            r_end = d
        else:
            ranges.append((r_start, r_end))
            r_start = d
            r_end = d
    ranges.append((r_start, r_end))
    return ranges


def _determine_fetch_ranges_for_symbol(
    existing_dates: set[date],
    start: date,
    end: date,
    calendar: TaiwanTradingCalendar,
) -> list[tuple[date, date]]:
    """Determine the minimal ranges to fetch for a given symbol.

    - If no existing data in storage: bootstrap fetch requested [start, end].
    - If symbol has existing data:
      Find candidate trading dates missing in [start, end] and coalesce.
    """
    if not existing_dates:
        # First historical bootstrap: fetch whole requested range once
        # Check if there are any candidate trading days in [start, end]
        candidates = list(_iter_candidate_dates(start, end, calendar))
        if not candidates:
            return []
        return [(start, end)]

    # Missing candidate trading dates
    missing_dates = [
        d for d in _iter_candidate_dates(start, end, calendar)
        if d not in existing_dates
    ]
    return _coalesce_dates_to_ranges(missing_dates)


class TaiwanDailyRefreshService:
    """Refresh Taiwan daily data into TaiwanDailyStore.

    - Universe from TaiwanSecurityMaster (is_supported=True, stock/etf, TWSE/TPEX)
    - Trading-day-aware: weekends and known holidays do not cause re-fetches
    - Incremental: only fetches missing trading-date ranges, not whole history
    - Bounded concurrency (default 5) to respect provider rate limits
    - Resume-capable: already-persisted symbols/dates are skipped
    """

    def __init__(
        self,
        store: TaiwanDailyStore | None = None,
        provider: TaiwanHybridProvider | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        start_date: str = DEFAULT_START_DATE,
    ) -> None:
        self._store = store or TaiwanDailyStore()
        self._provider = provider or TaiwanHybridProvider()
        self._calendar = calendar or TaiwanTradingCalendar()
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

        # Read existing dates in [start, end] for requested symbols
        existing_by_symbol: dict[str, set[date]] = {s: set() for s in symbols}
        all_rows = self._store.read_range(symbols, start, end)
        if not all_rows.is_empty():
            for row in all_rows.iter_rows(named=True):
                existing_by_symbol[row["symbol"]].add(row["date"])

        # Determine fetch jobs (symbol, fetch_start, fetch_end)
        jobs: list[tuple[str, date, date]] = []
        for sym in symbols:
            ranges = _determine_fetch_ranges_for_symbol(
                existing_by_symbol[sym], start, end, self._calendar
            )
            for r_start, r_end in ranges:
                jobs.append((sym, r_start, r_end))

        if not jobs:
            return {"message": "all dates up to date", "symbols_fetched": 0, "rows_written": 0, "jobs_executed": 0}

        stats: dict[str, Any] = {
            "symbols_total": len(symbols),
            "symbols_fetched": 0,
            "jobs_executed": 0,
            "rows_written": 0,
            "failed": [],
            "per_symbol": {},
        }

        with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
            futures = {
                executor.submit(self._fetch_and_store, sym, j_start, j_end): (sym, j_start, j_end)
                for sym, j_start, j_end in jobs
            }
            symbols_seen = set()
            for future in as_completed(futures):
                sym, j_start, j_end = futures[future]
                try:
                    result = future.result()
                    symbols_seen.add(sym)
                    stats["jobs_executed"] += 1
                    stats["rows_written"] += result.get("rows", 0)
                    if sym not in stats["per_symbol"]:
                        stats["per_symbol"][sym] = {
                            "symbol": sym,
                            "rows": 0,
                            "ranges": [],
                            "status": "ok",
                        }
                    stats["per_symbol"][sym]["rows"] += result.get("rows", 0)
                    stats["per_symbol"][sym]["ranges"].append({
                        "start": j_start.isoformat(),
                        "end": j_end.isoformat(),
                        "rows": result.get("rows", 0),
                    })
                except Exception as exc:
                    logger.warning("Refresh failed for %s (%s..%s): %s", sym, j_start, j_end, exc)
                    if sym not in stats["failed"]:
                        stats["failed"].append(sym)
                    stats["per_symbol"][sym] = {"error": str(exc)}

            stats["symbols_fetched"] = len(symbols_seen)

        return stats

    def _fetch_and_store(self, symbol: str, start: date, end: date) -> dict[str, Any]:
        """Fetch a single symbol's daily data for [start, end] and store it atomically."""
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
