"""Taiwan Daily Refresh Service (Phase 6B-Data Foundation).

Drives incremental batch refresh from TaiwanHybridProvider into
TaiwanDailyStore.  All provider calls are bounded-concurrency,
resume-capable, and never attempt a full 2335-symbol sweep in one shot.
"""
from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

import polars as pl

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import ObservedUniverseStore, is_potential_market_session
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.providers.snapshot_provider import (
    OfficialDailySnapshotAdapter,
    OfficialSnapshotUnpublished,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_today
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
        snapshot_adapter: OfficialDailySnapshotAdapter | None = None,
        evidence_store: ObservedUniverseStore | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        start_date: str = DEFAULT_START_DATE,
    ) -> None:
        self._store = store or TaiwanDailyStore()
        self._provider = provider or TaiwanHybridProvider()
        self._calendar = calendar or TaiwanTradingCalendar()
        self._evidence_store = evidence_store or ObservedUniverseStore()
        self._snapshot_adapter = snapshot_adapter
        self._concurrency = concurrency
        self._start_date = date.fromisoformat(start_date)

    def refresh_dates(
        self,
        start_date: date,
        end_date: date,
        force: bool = False,
    ) -> dict[str, Any]:
        """Fetch and persist market-wide daily snapshots for trading days in [start_date, end_date].

        Uses OfficialDailySnapshotAdapter:
          - Exactly 1 HTTP request for TWSE MI_INDEX
          - Exactly 1 HTTP request for TPEx dailyQuotes
        per trading day (2 HTTP requests total per date instead of ~2,335).
        """
        # A partition made by a single-symbol fetch is not a complete market day.
        available = (
            set(self._store.available_dates()) - set(self._store.partial_dates()) if not force else set()
        )
        stats: dict[str, Any] = {
            "dates_requested": 0,
            "dates_fetched": 0,
            "dates_skipped": 0,
            "total_rows_written": 0,
            "failed_dates": [],
        }

        cur = start_date
        while cur <= end_date:
            if not is_potential_market_session(cur, self._calendar, self._evidence_store):
                cur += timedelta(days=1)
                continue

            stats["dates_requested"] += 1

            if cur in available:
                stats["dates_skipped"] += 1
                cur += timedelta(days=1)
                continue

            try:
                # A cold adapter can fetch and persist the security master. Construct it
                # only when fetching a missing session, never during freshness inspection.
                if self._snapshot_adapter is None:
                    self._snapshot_adapter = OfficialDailySnapshotAdapter()
                df = self._snapshot_adapter.fetch_date(cur)
                if not df.is_empty():
                    written = self._store.write_batch(df, partition_date=cur)
                    stats["total_rows_written"] += written
                    stats["dates_fetched"] += 1
                else:
                    # A market-wide empty response is not a successful skip:
                    # no date partition was persisted, so the gap must remain
                    # visible and retryable on the next scheduled run.
                    stats["failed_dates"].append({
                        "date": str(cur), "error": "empty_official_daily_snapshot", "status": "unpublished",
                    })
            except OfficialSnapshotUnpublished as exc:
                # The exchange has no snapshot for this date yet; callers decide whether that
                # is expected (today right after the close) or a gap.
                stats["failed_dates"].append({"date": str(cur), "error": str(exc), "status": "unpublished"})
            except Exception as exc:
                logger.warning("Daily snapshot refresh failed for %s: %s", cur, exc)
                stats["failed_dates"].append({"date": str(cur), "error": str(exc)})

            cur += timedelta(days=1)

        return stats

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

        end = end or taipei_today()
        start = start or self._start_date
        if start > end:
            return {"error": "start > end"}

        # Read existing dates in [start, end] for requested symbols
        existing_by_symbol: dict[str, set[date]] = {s: set() for s in symbols}
        all_rows = self._store.read_range(symbols, start, end)
        if not all_rows.is_empty():
            for row in all_rows.iter_rows(named=True):
                existing_by_symbol[row["symbol"]].add(row["date"])

        sessions = []
        day = start
        while day <= end:
            if is_potential_market_session(day, self._calendar, self._evidence_store):
                sessions.append(day)
            day += timedelta(days=1)

        # Keep the exact expected sessions even when one HTTP request spans existing rows or closures.
        jobs = []
        for sym, existing in existing_by_symbol.items():
            missing = [day for day in sessions if day not in existing]
            if not missing:
                continue
            ranges = _coalesce_dates_to_ranges(missing) if existing else [(start, end)]
            for first, last in ranges:
                expected = missing[bisect_left(missing, first):bisect_right(missing, last)]
                jobs.append((sym, first, last, expected))

        if not jobs:
            return {"message": "all dates up to date", "symbols_fetched": 0, "rows_written": 0, "jobs_executed": 0}

        stats: dict[str, Any] = {
            "symbols_total": len(symbols),
            "symbols_fetched": 0,
            "jobs_executed": 0,
            "rows_written": 0,
            "failed": [],
            "pending_dates": [],
            "per_symbol": {},
        }

        with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
            futures = {
                executor.submit(self._fetch_and_store, sym, first, last): (sym, first, last, expected)
                for sym, first, last, expected in jobs
            }
            symbols_seen = set()
            fetched_days = set()
            for future in as_completed(futures):
                sym, j_start, j_end, expected = futures[future]
                item = stats["per_symbol"].setdefault(sym, {"symbol": sym, "rows": 0, "ranges": []})
                interval = {"start": j_start.isoformat(), "end": j_end.isoformat(), "rows": 0}
                stats["jobs_executed"] += 1
                try:
                    result = future.result()
                    rows = result["rows"]
                    interval["rows"] = rows
                    item["rows"] += rows
                    stats["rows_written"] += result.get("written", 0)
                    returned = set(result["dates"])
                    fetched_days.update(returned)
                    missing_days = [day for day in expected if day not in returned]
                    interval["missing"] = len(missing_days)
                    stats["pending_dates"].extend({"symbol": sym, "date": day.isoformat(), "reason": "no daily rows for symbol"}
                                                  for day in missing_days)
                    if rows:
                        symbols_seen.add(sym)
                except Exception as exc:
                    logger.warning("Refresh failed for %s (%s..%s): %s", sym, j_start, j_end, exc)
                    if sym not in stats["failed"]:
                        stats["failed"].append(sym)
                    item["error"] = interval["error"] = str(exc)
                item["ranges"].append(interval)

        for item in stats["per_symbol"].values():
            if item["rows"]:
                item["status"] = "ok" if all(r["rows"] and not r["missing"] for r in item["ranges"]) else "partial"
            else:
                item["status"] = "failed" if "error" in item else "empty"
        stats["symbols_fetched"] = len(symbols_seen)
        stats["dates_fetched"] = len(fetched_days)

        return stats

    def _fetch_and_store(self, symbol: str, start: date, end: date) -> dict[str, Any]:
        """Fetch a single symbol's daily data for [start, end] and store it atomically."""
        df = self._provider.get_daily([symbol], start_time=start, end_time=end)
        if df.is_empty():
            return {"symbol": symbol, "rows": 0, "dates": [], "status": "empty"}

        df = df.filter(
            (pl.col("symbol") == symbol) & pl.col("date").cast(pl.Date).is_between(start, end)
        )
        written = self._store.write_batch(df, partial=True)
        return {"symbol": symbol, "rows": df.height, "written": written,
                "dates": df["date"].cast(pl.Date).unique().to_list(), "status": "ok" if df.height else "empty"}
