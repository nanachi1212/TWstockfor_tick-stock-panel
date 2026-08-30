"""Unit tests for TaiwanDailyRefreshService (Phase 6B-Data Foundation Final Hardening).

Covers:
  - test_refresh_does_not_refetch_weekend
  - test_refresh_fetches_only_new_forward_range
  - test_refresh_second_identical_run_makes_zero_provider_calls
  - test_refresh_bootstrap_fetches_requested_range_once
  - test_refresh_preserves_existing_rows
  - test_refresh_no_duplicate_after_incremental_update
  - test_refresh_skips_known_holidays

All tests are offline with mock provider and explicit call tracking.
"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.realtime.calendar import TaiwanTradingCalendar


class MockCallTrackingProvider:
    """Mock provider recording all call arguments."""

    def __init__(self, data_generator=None):
        self.calls: list[dict] = []
        self._data_generator = data_generator

    def get_daily(self, symbols: list[str], start_time=None, end_time=None, **kwargs) -> pl.DataFrame:
        self.calls.append({
            "symbols": list(symbols),
            "start": start_time,
            "end": end_time,
        })
        if self._data_generator:
            return self._data_generator(symbols, start_time, end_time)

        # Default synthetic response: generate rows for weekdays in [start_time, end_time]
        rows = []
        cur = start_time
        while cur <= end_time:
            if cur.weekday() < 5:  # Monday to Friday
                for sym in symbols:
                    rows.append({
                        "symbol": sym,
                        "date": cur.isoformat(),
                        "open": 100.0,
                        "high": 105.0,
                        "low": 95.0,
                        "close": 102.0,
                        "volume": 10000.0,
                        "amount": 1020000.0,
                        "quote_ts": None,
                    })
            cur += timedelta(days=1)
        return pl.DataFrame(rows) if rows else pl.DataFrame()


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as d:
        yield TaiwanDailyStore(data_dir=Path(d) / "daily")


class TestTaiwanDailyRefreshIncremental:
    """Trading-day-aware incremental refresh tests."""

    def test_refresh_does_not_refetch_weekend(self, tmp_store):
        """If store has Friday data, refreshing up to Sunday must NOT call provider for weekend."""
        # Pre-seed Friday 2026-08-28
        df_fri = pl.DataFrame({
            "symbol": ["2330.TWSE"],
            "date": [date(2026, 8, 28)],
            "open": [100.0], "high": [105.0], "low": [95.0], "close": [102.0],
            "volume": [10000.0], "amount": [1020000.0], "quote_ts": [None],
        })
        tmp_store.write_batch(df_fri)

        provider = MockCallTrackingProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider)

        # Friday to Sunday (2026-08-28 to 2026-08-30)
        res = service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 28), end=date(2026, 8, 30))

        assert len(provider.calls) == 0, f"Expected 0 calls for weekend, got: {provider.calls}"
        assert res.get("symbols_fetched") == 0
        assert res.get("rows_written") == 0

    def test_refresh_fetches_only_new_forward_range(self, tmp_store):
        """When store has up to 8/27 and 8/28 is requested, only 8/28 must be fetched."""
        # Pre-seed 2026-08-24 to 2026-08-27 (Mon-Thu)
        rows = []
        for d in [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)]:
            rows.append({
                "symbol": "2330.TWSE",
                "date": d,
                "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0,
                "volume": 10000.0, "amount": 1020000.0, "quote_ts": None,
            })
        tmp_store.write_batch(pl.DataFrame(rows))

        provider = MockCallTrackingProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider)

        # Refresh for full week: 2026-08-24 to 2026-08-28
        res = service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 28))

        assert len(provider.calls) == 1
        call = provider.calls[0]
        # Crucial check: start and end must be 2026-08-28, NOT 2026-08-24!
        assert call["start"] == date(2026, 8, 28)
        assert call["end"] == date(2026, 8, 28)
        assert res.get("symbols_fetched") == 1
        assert res.get("rows_written") == 1

        # Total rows in store should now be 5
        all_rows = tmp_store.read_all()
        assert all_rows.height == 5

    def test_refresh_second_identical_run_makes_zero_provider_calls(self, tmp_store):
        """Running identical refresh back-to-back must result in 0 provider calls on 2nd run."""
        provider = MockCallTrackingProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider)

        # 1st run: bootstrap Mon-Fri
        res1 = service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 28))
        assert len(provider.calls) == 1
        assert res1.get("rows_written") == 5

        # 2nd run: identical request
        res2 = service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 28))
        assert len(provider.calls) == 1  # Total calls remains 1 (0 new calls!)
        assert res2.get("symbols_fetched") == 0
        assert res2.get("rows_written") == 0
        assert res2.get("jobs_executed") == 0

    def test_refresh_bootstrap_fetches_requested_range_once(self, tmp_store):
        """First time symbol is fetched, it makes exactly 1 provider call for requested range."""
        provider = MockCallTrackingProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider)

        res = service.refresh_symbols(
            ["2330.TWSE", "0050.TWSE"],
            start=date(2026, 8, 24),
            end=date(2026, 8, 28),
        )

        assert len(provider.calls) == 2  # 1 per symbol
        call_symbols = sorted([c["symbols"][0] for c in provider.calls])
        assert call_symbols == ["0050.TWSE", "2330.TWSE"]
        for c in provider.calls:
            assert c["start"] == date(2026, 8, 24)
            assert c["end"] == date(2026, 8, 28)

        assert res.get("symbols_fetched") == 2
        assert res.get("rows_written") == 10  # 5 rows per symbol

    def test_refresh_preserves_existing_rows(self, tmp_store):
        """Incremental refresh must preserve previously persisted rows and their values."""
        # Write day 1
        df1 = pl.DataFrame({
            "symbol": ["2330.TWSE"],
            "date": [date(2026, 8, 24)],
            "open": [999.0], "high": [1005.0], "low": [990.0], "close": [1000.0],
            "volume": [12345.0], "amount": [12345000.0], "quote_ts": [None],
        })
        tmp_store.write_batch(df1)

        provider = MockCallTrackingProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider)

        # Refresh extending to 8/25
        service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 25))

        row_824 = tmp_store.read_range(["2330.TWSE"], date(2026, 8, 24), date(2026, 8, 24))
        assert row_824.height == 1
        assert row_824["open"][0] == 999.0
        assert row_824["close"][0] == 1000.0
        assert row_824["volume"][0] == 12345.0

    def test_refresh_no_duplicate_after_incremental_update(self, tmp_store):
        """Repeated forward refresh runs must produce zero duplicates."""
        provider = MockCallTrackingProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider)

        # Step 1: Mon-Wed
        service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 26))
        # Step 2: Mon-Thu
        service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 27))
        # Step 3: Mon-Fri
        service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 28))

        all_rows = tmp_store.read_all()
        assert all_rows.height == 5
        unique_rows = all_rows.unique(subset=["symbol", "date"]).height
        assert unique_rows == 5

    def test_refresh_skips_known_holidays(self, tmp_store):
        """TaiwanTradingCalendar confirmed holidays are not counted as missing trading days."""
        cal = TaiwanTradingCalendar(known_holidays={date(2026, 8, 28)})  # Mark Friday as holiday

        # Seed Mon-Thu
        rows = []
        for d in [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)]:
            rows.append({
                "symbol": "2330.TWSE", "date": d,
                "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0,
                "volume": 10000.0, "amount": 1020000.0, "quote_ts": None,
            })
        tmp_store.write_batch(pl.DataFrame(rows))

        provider = MockCallTrackingProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider, calendar=cal)

        # Refresh for Mon-Fri; Friday is a holiday, so 0 calls needed
        res = service.refresh_symbols(["2330.TWSE"], start=date(2026, 8, 24), end=date(2026, 8, 28))
        assert len(provider.calls) == 0
        assert res.get("symbols_fetched") == 0

    def test_empty_symbol_does_not_mark_market_holiday(self, tmp_store):
        """Empty response for a single symbol MUST NOT mark the date as a market-wide holiday."""
        cal = TaiwanTradingCalendar()
        target_date = date(2026, 8, 26)  # Wednesday (unverified trading day)
        assert target_date not in cal.known_holidays

        class EmptyOneSymbolProvider:
            def __init__(self):
                self.calls = []

            def get_daily(self, symbols: list[str], start_time=None, end_time=None, **kwargs):
                self.calls.append(symbols)
                # 2330.TWSE returns empty (e.g. suspension, delisted, provider gap)
                if "2330.TWSE" in symbols:
                    return pl.DataFrame()
                # 0050.TWSE returns valid trading data
                return pl.DataFrame([{
                    "symbol": "0050.TWSE",
                    "date": target_date.isoformat(),
                    "open": 160.0, "high": 162.0, "low": 159.0, "close": 161.0,
                    "volume": 50000.0, "amount": 8050000.0, "quote_ts": None,
                }])

        provider = EmptyOneSymbolProvider()
        service = TaiwanDailyRefreshService(store=tmp_store, provider=provider, calendar=cal)

        # Refresh 2330.TWSE which returns empty
        res = service.refresh_symbols(["2330.TWSE"], start=target_date, end=target_date)
        assert res.get("rows_written") == 0

        # CRITICAL ASSERTION: The calendar must NOT have recorded target_date as a holiday!
        assert target_date not in cal.known_holidays, f"{target_date} was incorrectly marked as holiday!"

        # Another symbol (0050.TWSE) on the same date can still be fetched and stored normally
        res2 = service.refresh_symbols(["0050.TWSE"], start=target_date, end=target_date)
        assert res2.get("rows_written") == 1
        assert res2.get("symbols_fetched") == 1

        stored = tmp_store.read_all(["0050.TWSE"])
        assert stored.height == 1
        assert stored["date"][0] == target_date
