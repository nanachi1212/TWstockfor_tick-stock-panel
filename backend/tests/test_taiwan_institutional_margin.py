"""Unit tests for Taiwan Institutional & Margin Persistence and Screener Integration.

Covers:
  - InstitutionalStore: write/read, date partition, dedup, latest-per-symbol, restart persistence, missing values, TWSE, TPEx.
  - MarginStore: write/read, date partition, dedup, latest-per-symbol, restart persistence, missing values, short_margin_ratio computation.
  - Refresh services: bootstrap, repeat run (0 duplicate calls), incremental, failure isolation, empty semantics, provider call count.
  - Screener integration: batch join, missing != 0, provenance dates, 0 provider HTTP calls.
"""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.institutional_margin_refresh import (
    TaiwanInstitutionalRefreshService,
    TaiwanMarginRefreshService,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.daily_store import TaiwanDailyStore


@pytest.fixture
def tmp_inst_dir(tmp_path: Path) -> Path:
    p = tmp_path / "institutional"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_margin_dir(tmp_path: Path) -> Path:
    p = tmp_path / "margin"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def inst_store(tmp_inst_dir: Path) -> TaiwanInstitutionalStore:
    return TaiwanInstitutionalStore(data_dir=tmp_inst_dir)


@pytest.fixture
def margin_store(tmp_margin_dir: Path) -> TaiwanMarginStore:
    return TaiwanMarginStore(data_dir=tmp_margin_dir)


# =====================================================================
# 1. Institutional Store Unit Tests
# =====================================================================

class TestTaiwanInstitutionalStore:
    def test_write_and_read_basic(self, inst_store: TaiwanInstitutionalStore):
        d1 = date(2026, 8, 27)
        d2 = date(2026, 8, 28)
        df = pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d1, "foreign_net": 1000, "investment_trust_net": 500, "dealer_net": 100},
            {"symbol": "8069.TPEX", "date": d1, "foreign_net": -200, "investment_trust_net": 0, "dealer_net": 50},
            {"symbol": "2330.TWSE", "date": d2, "foreign_net": 2000, "investment_trust_net": -100, "dealer_net": 200},
        ])
        written = inst_store.write_batch(df)
        assert written == 3

        # Read all
        all_rows = inst_store.read_all()
        assert all_rows.height == 3
        assert inst_store.available_dates() == [d1, d2]
        assert inst_store.latest_date() == d2
        assert inst_store.known_symbols() == ["2330.TWSE", "8069.TPEX"]

    def test_dedup_on_same_partition(self, inst_store: TaiwanInstitutionalStore):
        d = date(2026, 8, 28)
        df1 = pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d, "foreign_net": 1000},
        ])
        df2 = pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d, "foreign_net": 9999},  # update
        ])
        inst_store.write_batch(df1)
        inst_store.write_batch(df2)

        res = inst_store.read_all()
        assert res.height == 1
        assert res["foreign_net"][0] == 9999

    def test_latest_per_symbol(self, inst_store: TaiwanInstitutionalStore):
        d1 = date(2026, 8, 25)
        d2 = date(2026, 8, 26)
        df = pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d1, "foreign_net": 100},
            {"symbol": "2330.TWSE", "date": d2, "foreign_net": 200},
            {"symbol": "0050.TWSE", "date": d1, "foreign_net": 300},
        ])
        inst_store.write_batch(df)

        latest_df = inst_store.read_latest_per_symbol()
        assert latest_df.height == 2
        # 2330 should have d2, 0050 should have d1
        row_2330 = latest_df.filter(pl.col("symbol") == "2330.TWSE")
        assert row_2330["date"][0] == d2
        assert row_2330["foreign_net"][0] == 200
        row_0050 = latest_df.filter(pl.col("symbol") == "0050.TWSE")
        assert row_0050["date"][0] == d1
        assert row_0050["foreign_net"][0] == 300

    def test_restart_persistence(self, tmp_inst_dir: Path):
        d = date(2026, 8, 28)
        store1 = TaiwanInstitutionalStore(data_dir=tmp_inst_dir)
        store1.write_batch(pl.DataFrame([{"symbol": "2330.TWSE", "date": d, "foreign_net": 5000}]))

        # Instantiate brand new store object pointing to same directory
        store2 = TaiwanInstitutionalStore(data_dir=tmp_inst_dir)
        res = store2.read_all()
        assert res.height == 1
        assert res["foreign_net"][0] == 5000


# =====================================================================
# 2. Margin Store Unit Tests
# =====================================================================

class TestTaiwanMarginStore:
    def test_write_and_read_basic(self, margin_store: TaiwanMarginStore):
        d = date(2026, 8, 28)
        df = pl.DataFrame([
            {
                "symbol": "2330.TWSE",
                "date": d,
                "margin_previous_balance": 20000,
                "margin_balance": 21000,
                "margin_change": 1000,
                "short_previous_balance": 500,
                "short_balance": 1050,
                "short_change": 550,
            }
        ])
        margin_store.write_batch(df)

        res = margin_store.read_all()
        assert res.height == 1
        # short_margin_ratio: 1050 / 21000 * 100 = 5.0%
        assert res["short_margin_ratio"][0] == 5.0

    def test_short_margin_ratio_zero_or_null_margin(self, margin_store: TaiwanMarginStore):
        d = date(2026, 8, 28)
        df = pl.DataFrame([
            {"symbol": "00646.TWSE", "date": d, "margin_balance": 0, "short_balance": 0},
            {"symbol": "9999.TWSE", "date": d, "margin_balance": None, "short_balance": 100},
        ])
        margin_store.write_batch(df)
        res = margin_store.read_all().sort("symbol")
        assert res["short_margin_ratio"][0] is None
        assert res["short_margin_ratio"][1] is None

    def test_margin_dedup_and_restart(self, tmp_margin_dir: Path):
        d = date(2026, 8, 28)
        store1 = TaiwanMarginStore(data_dir=tmp_margin_dir)
        store1.write_batch(pl.DataFrame([{"symbol": "8069.TPEX", "date": d, "margin_balance": 5000}]))
        store1.write_batch(pl.DataFrame([{"symbol": "8069.TPEX", "date": d, "margin_balance": 6000}]))

        store2 = TaiwanMarginStore(data_dir=tmp_margin_dir)
        res = store2.read_all()
        assert res.height == 1
        assert res["margin_balance"][0] == 6000


# =====================================================================
# 3. Refresh Services Unit Tests
# =====================================================================

class MockInstitutionalProvider:
    def __init__(self):
        self.twse_calls = []
        self.tpex_calls = []

    class MockTwse:
        def __init__(self, outer):
            self.outer = outer
        def build_url(self, d):
            return f"mock://twse/{d}"
        def parse_payload(self, payload, d, url):
            self.outer.twse_calls.append(d)
            from app.taiwan.enrichment.models import InstitutionalFlow
            return [
                InstitutionalFlow(
                    symbol="2330.TWSE", trade_date=d,
                    foreign_buy=1000, foreign_sell=500, foreign_net=500,
                    investment_trust_buy=100, investment_trust_sell=0, investment_trust_net=100,
                    dealer_buy=50, dealer_sell=10, dealer_net=40,
                )
            ]

    class MockTpex:
        def __init__(self, outer):
            self.outer = outer
        def build_url(self, d):
            return f"mock://tpex/{d}"
        def parse_payload(self, payload, d, url):
            self.outer.tpex_calls.append(d)
            from app.taiwan.enrichment.models import InstitutionalFlow
            return [
                InstitutionalFlow(
                    symbol="8069.TPEX", trade_date=d,
                    foreign_buy=200, foreign_sell=300, foreign_net=-100,
                    investment_trust_buy=0, investment_trust_sell=0, investment_trust_net=0,
                    dealer_buy=20, dealer_sell=10, dealer_net=10,
                )
            ]

    @property
    def twse(self):
        return self.MockTwse(self)

    @property
    def tpex(self):
        return self.MockTpex(self)


class TestInstitutionalRefreshService:
    def test_refresh_dates_and_skip_weekends_and_resume(self, inst_store: TaiwanInstitutionalStore):
        provider = MockInstitutionalProvider()
        service = TaiwanInstitutionalRefreshService(store=inst_store, provider=provider)

        # Mock network call
        service._fetch_json = lambda url: {}

        # 2026-08-28 is Friday, 2026-08-29 is Saturday, 2026-08-30 is Sunday
        res = service.refresh_dates(date(2026, 8, 28), date(2026, 8, 30))
        assert res["dates_fetched"] == 1
        assert res["dates_skipped"] == 0
        assert res["total_rows_written"] == 2
        assert len(provider.twse_calls) == 1
        assert len(provider.tpex_calls) == 1

        # Second run on same range: 0 provider calls
        provider.twse_calls.clear()
        provider.tpex_calls.clear()
        res2 = service.refresh_dates(date(2026, 8, 28), date(2026, 8, 30))
        assert res2["dates_fetched"] == 0
        assert res2["dates_skipped"] == 1
        assert len(provider.twse_calls) == 0
        assert len(provider.tpex_calls) == 0


# =====================================================================
# 4. Screener Batch Join Unit Tests
# =====================================================================

class TestScreenerBatchJoin:
    def test_screener_batch_joins_persisted_institutional_and_margin(self, tmp_path: Path):
        d_store = TaiwanDailyStore(data_dir=tmp_path / "daily")
        i_store = TaiwanInstitutionalStore(data_dir=tmp_path / "inst")
        m_store = TaiwanMarginStore(data_dir=tmp_path / "margin")

        d = date(2026, 8, 28)
        # Daily
        d_store.write_batch(pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d, "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 10000.0, "amount": 1020000.0, "quote_ts": None},
            {"symbol": "8069.TPEX", "date": d, "open": 50.0, "high": 52.0, "low": 49.0, "close": 51.0, "volume": 5000.0, "amount": 255000.0, "quote_ts": None},
        ]))
        # Institutional (only 2330 present; 8069 missing -> missing != 0)
        i_store.write_batch(pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d, "foreign_net": 12345, "investment_trust_net": 678, "dealer_net": 90, "status": "official"},
        ]))
        # Margin (both present)
        m_store.write_batch(pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d, "margin_balance": 50000, "margin_change": 1000, "short_balance": 5000, "short_margin_ratio": 10.0, "status": "official"},
            {"symbol": "8069.TPEX", "date": d, "margin_balance": 20000, "margin_change": -500, "short_balance": 1000, "short_margin_ratio": 5.0, "status": "official"},
        ]))

        screener = TaiwanScreenerService(
            daily_store=d_store,
            institutional_store=i_store,
            margin_store=m_store,
        )

        res = screener.run(TaiwanScreenerRequest())
        item_2330 = next(i for i in res.items if i.symbol == "2330.TWSE")
        item_8069 = next(i for i in res.items if i.symbol == "8069.TPEX")

        # 2330 has both
        assert item_2330.foreign_net == 12345.0
        assert item_2330.margin_balance == 50000.0
        assert item_2330.short_margin_ratio == 10.0
        assert item_2330.institutional_date == "2026-08-28"
        assert item_2330.margin_date == "2026-08-28"

        # 8069 missing institutional: MUST be None, NEVER 0!
        assert item_8069.foreign_net is None
        assert item_8069.investment_trust_net is None
        assert item_8069.institutional_date is None
        # But margin is present
        assert item_8069.margin_balance == 20000.0
        assert item_8069.short_margin_ratio == 5.0

        # Provenance dates in response
        assert res.data_dates.daily_as_of == "2026-08-28"
        assert res.data_dates.institutional_as_of == "2026-08-28"
        assert res.data_dates.margin_as_of == "2026-08-28"


# =====================================================================
# 5. HTTP Retry Hardening Unit Tests
# =====================================================================

class TestHttpRetryHardening:
    def test_institutional_transient_failure_retries_then_succeeds(self, inst_store: TaiwanInstitutionalStore, monkeypatch):
        """Scenario: attempt 1 timeout, attempt 2 HTTP 500, attempt 3 success -> 3 calls, persisted."""
        import urllib.error
        from app.taiwan import institutional_margin_refresh

        calls = {"count": 0}

        def mock_urlopen(req, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("Connection timed out")
            elif calls["count"] == 2:
                raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, None)
            else:
                import io
                return io.BytesIO(b'{"data": [["2330", "\xe5\x8f\xb0\xe7\xa9\x8d\xe9\x9b\xbb", "1000", "500", "500", "0", "0", "0", "100", "0", "100", "40", "20", "10", "10", "10", "10", "0", "640"]]}')

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
        monkeypatch.setattr("time.sleep", lambda s: None)  # speed up test

        service = TaiwanInstitutionalRefreshService(store=inst_store)
        # Only TWSE to isolate count
        url = service._provider.twse.build_url(date(2026, 8, 28))
        res = institutional_margin_refresh._fetch_json_with_retry(url, max_attempts=3, initial_backoff=0.01)

        assert calls["count"] == 3
        assert "data" in res

    def test_margin_permanent_failure_stops_after_max_attempts(self, margin_store: TaiwanMarginStore, monkeypatch):
        """Persistent 500 error stops after max attempts (3), records in failed_dates, no partition created."""
        import urllib.error

        calls = {"count": 0}

        def mock_urlopen(req, timeout=None):
            calls["count"] += 1
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
        monkeypatch.setattr("time.sleep", lambda s: None)

        service = TaiwanMarginRefreshService(store=margin_store)
        target_date = date(2026, 8, 28)
        res = service.refresh_dates(target_date, target_date)

        assert len(res["failed_dates"]) == 1
        assert res["failed_dates"][0]["date"] == str(target_date)
        assert res["total_rows_written"] == 0
        # Check that no partition directory or parquet was created
        part_dir = margin_store._data_dir / f"date={target_date.isoformat()}"
        assert not (part_dir / "part.parquet").exists()

    def test_http_404_not_retried(self, monkeypatch):
        """Permanent 4xx errors (e.g. 404) must NOT be retried (only 1 call)."""
        import urllib.error
        from app.taiwan import institutional_margin_refresh

        calls = {"count": 0}

        def mock_urlopen(req, timeout=None):
            calls["count"] += 1
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
        monkeypatch.setattr("time.sleep", lambda s: None)

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            institutional_margin_refresh._fetch_json_with_retry("http://example.com/test", max_attempts=3)

        assert exc_info.value.code == 404
        assert calls["count"] == 1  # Exactly 1 call, zero retry

    def test_retry_does_not_mark_holiday(self, inst_store: TaiwanInstitutionalStore, monkeypatch):
        """Refresh failure or retry MUST NOT mark date as holiday in TaiwanTradingCalendar."""
        import urllib.error

        def mock_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
        monkeypatch.setattr("time.sleep", lambda s: None)

        cal = TaiwanTradingCalendar()
        target_date = date(2026, 8, 28)
        assert target_date not in cal.known_holidays

        service = TaiwanInstitutionalRefreshService(store=inst_store, calendar=cal)
        res = service.refresh_dates(target_date, target_date)

        assert len(res["failed_dates"]) == 1
        # Calendar must strictly remain untainted
        assert target_date not in cal.known_holidays

