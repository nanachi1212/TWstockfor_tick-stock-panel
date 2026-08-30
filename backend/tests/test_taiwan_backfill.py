"""Unit tests for TaiwanBackfillService (Phase 6B Data Foundation Backfill).

Tests:
  - Batching across symbols
  - Checkpoint progress reporting callback
  - Resumption (already-persisted symbols/ranges skipped)
  - Partially complete symbols only fetch missing ranges
  - Failure tracking (failed symbols recorded explicitly)
  - Routine refresh skipping weekends
  - No live internet calls (fully offline with mock provider)
"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.backfill_service import TaiwanBackfillService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.realtime.calendar import TaiwanTradingCalendar


class MockTrackingProvider:
    def __init__(self, fail_symbols: set[str] | None = None):
        self.calls: list[dict] = []
        self.fail_symbols = fail_symbols or set()

    def get_daily(self, symbols: list[str], start_time=None, end_time=None, **kwargs) -> pl.DataFrame:
        self.calls.append({
            "symbols": list(symbols),
            "start": start_time,
            "end": end_time,
        })
        for s in symbols:
            if s in self.fail_symbols:
                raise RuntimeError(f"Simulated network failure for {s}")

        rows = []
        cur = start_time
        while cur <= end_time:
            if cur.weekday() < 5:  # Monday to Friday
                for sym in symbols:
                    rows.append({
                        "symbol": sym,
                        "date": cur.isoformat(),
                        "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0,
                        "volume": 10000.0, "amount": 1020000.0, "quote_ts": None,
                    })
            cur += timedelta(days=1)
        return pl.DataFrame(rows) if rows else pl.DataFrame()


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as d:
        yield TaiwanDailyStore(data_dir=Path(d) / "daily")


class TestTaiwanBackfillService:
    def test_backfill_batches_and_progress_callback(self, tmp_store):
        provider = MockTrackingProvider()
        service = TaiwanBackfillService(store=tmp_store, provider=provider, concurrency=2)

        test_symbols = [f"SYM{i:02d}.TWSE" for i in range(10)]
        progress_reports = []

        stats = service.backfill(
            symbols=test_symbols,
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 28),
            batch_size=4,
            progress_callback=lambda p: progress_reports.append(p),
        )

        assert stats["total_symbols"] == 10
        assert stats["processed_symbols"] == 10
        assert stats["symbols_fetched"] == 10
        assert stats["symbols_failed"] == 0
        assert stats["batches_total"] == 3  # 4 + 4 + 2
        assert stats["batches_completed"] == 3
        assert len(progress_reports) == 3
        assert progress_reports[-1]["pct_complete"] == 100.0

    def test_backfill_resume_skips_already_persisted_symbols(self, tmp_store):
        provider = MockTrackingProvider()
        service = TaiwanBackfillService(store=tmp_store, provider=provider)

        test_symbols = ["2330.TWSE", "0050.TWSE", "8069.TPEX"]

        # Run 1: Full range Mon-Fri
        stats1 = service.backfill(
            symbols=test_symbols,
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 28),
        )
        assert stats1["symbols_fetched"] == 3
        assert stats1["symbols_skipped_up_to_date"] == 0
        assert len(provider.calls) == 3

        # Run 2: Identical run should skip all 3 symbols with 0 provider calls
        provider.calls.clear()
        stats2 = service.backfill(
            symbols=test_symbols,
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 28),
        )
        assert stats2["symbols_fetched"] == 0
        assert stats2["symbols_skipped_up_to_date"] == 3
        assert len(provider.calls) == 0

    def test_backfill_partially_complete_symbol_fetches_only_missing(self, tmp_store):
        # Pre-seed 2330.TWSE for Mon-Wed (8/24..8/26)
        rows = [
            {"symbol": "2330.TWSE", "date": d, "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 10000.0, "amount": 1000000.0, "quote_ts": None}
            for d in [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)]
        ]
        tmp_store.write_batch(pl.DataFrame(rows))

        provider = MockTrackingProvider()
        service = TaiwanBackfillService(store=tmp_store, provider=provider)

        # Backfill Mon-Fri (8/24..8/28)
        stats = service.backfill(
            symbols=["2330.TWSE"],
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 28),
        )

        assert len(provider.calls) == 1
        call = provider.calls[0]
        # Must only request Thursday to Friday (8/27..8/28)
        assert call["start"] == date(2026, 8, 27)
        assert call["end"] == date(2026, 8, 28)

        # Verify store has exactly 5 unique rows (zero duplicates)
        all_rows = tmp_store.read_all()
        assert all_rows.height == 5
        assert all_rows.unique(subset=["symbol", "date"]).height == 5

    def test_backfill_failure_tracking_and_retry(self, tmp_store):
        # FAIL_SYM triggers a provider exception
        provider = MockTrackingProvider(fail_symbols={"FAIL_SYM.TWSE"})
        service = TaiwanBackfillService(store=tmp_store, provider=provider)

        test_symbols = ["OK_SYM.TWSE", "FAIL_SYM.TWSE"]

        # Run 1: 1 success, 1 failure
        stats1 = service.backfill(
            symbols=test_symbols,
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 28),
        )
        assert stats1["symbols_failed"] == 1
        assert "FAIL_SYM.TWSE" in stats1["failed_symbols"]
        assert stats1["per_symbol_status"]["FAIL_SYM.TWSE"]["status"] == "failed"
        assert stats1["per_symbol_status"]["OK_SYM.TWSE"]["status"] == "success"

        # Now fix provider (clear failure) and run again
        provider.fail_symbols.clear()
        provider.calls.clear()

        stats2 = service.backfill(
            symbols=test_symbols,
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 28),
        )
        # OK_SYM should be skipped, FAIL_SYM should be fetched and succeed
        assert stats2["symbols_skipped_up_to_date"] == 1
        assert stats2["symbols_fetched"] == 1
        assert stats2["symbols_failed"] == 0
        assert stats2["per_symbol_status"]["FAIL_SYM.TWSE"]["status"] == "success"

    def test_routine_refresh_skips_weekends(self, tmp_store):
        cal = TaiwanTradingCalendar()
        service = TaiwanBackfillService(store=tmp_store, calendar=cal)

        saturday = date(2026, 8, 29)
        res = service.refresh_daily_routine(as_of_date=saturday)
        assert res["status"] == "skipped"
        assert "confirmed non-trading day" in res["reason"]
