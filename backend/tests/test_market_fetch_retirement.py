"""Removed markets never trigger a vendor fallback or mutate existing market data."""
from datetime import date, datetime
from unittest.mock import Mock

import polars as pl
import pytest

from app.services import kline_sync
from app.tickflow.pools import get_pool


@pytest.mark.parametrize("symbol", ["600000.SH", "000001.SZ", "830799.BJ"])
def test_ashare_daily_fetch_rejected_before_provider_lookup(monkeypatch, symbol):
    lookup = Mock()
    monkeypatch.setattr("app.data_providers.registry.get_provider", lookup)
    with pytest.raises(ValueError, match="僅提供台股"):
        kline_sync.sync_daily_batch([symbol])
    lookup.assert_not_called()


def test_selected_custom_daily_source_is_used(monkeypatch):
    expected = pl.DataFrame({"symbol": ["2330.TWSE"], "close": [100.0]})
    provider = Mock()
    provider.get_daily.return_value = expected
    monkeypatch.setattr(kline_sync.preferences, "get_daily_data_provider", lambda: "custom_daily")
    monkeypatch.setattr("app.data_providers.custom.provider_has_dataset", lambda name, ds: ds == "daily")
    monkeypatch.setattr("app.data_providers.registry.get_provider", lambda name: provider)
    assert kline_sync.sync_daily_batch(["2330.TWSE"]).equals(expected)
    provider.get_daily.assert_called_once()


def test_missing_custom_daily_dataset_stays_unavailable(monkeypatch):
    lookup = Mock()
    monkeypatch.setattr(kline_sync.preferences, "get_daily_data_provider", lambda: "missing_daily")
    monkeypatch.setattr("app.data_providers.custom.provider_has_dataset", lambda *args: False)
    monkeypatch.setattr("app.data_providers.registry.get_provider", lookup)
    failed = []
    assert kline_sync.sync_daily_batch(["2330.TWSE"], failed_out=failed).is_empty()
    assert failed == ["2330.TWSE"]
    lookup.assert_not_called()


def test_failed_custom_minute_source_does_not_switch_provider(monkeypatch):
    provider = Mock()
    provider.get_minute.side_effect = ConnectionError("offline")
    monkeypatch.setattr(kline_sync.preferences, "get_minute_data_provider", lambda: "custom_minute")
    monkeypatch.setattr("app.data_providers.custom.provider_has_dataset", lambda *args: True)
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda name: provider)
    assert kline_sync.fetch_minute_single("2330.TWSE", date(2026, 11, 3)).is_empty()
    provider.get_minute.assert_called_once()


def test_custom_minute_stream_persists_once(monkeypatch):
    expected = pl.DataFrame({"symbol": ["2330.TWSE"], "datetime": [datetime(2026, 11, 3, 9)]})
    provider = Mock()
    provider.get_minute.return_value = expected
    monkeypatch.setattr(kline_sync.preferences, "get_minute_data_provider", lambda: "custom_minute")
    monkeypatch.setattr("app.data_providers.custom.provider_has_dataset", lambda *args: True)
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda name: provider)
    persisted = []
    result = kline_sync.sync_minute_batch(["2330.TWSE"], on_segment=persisted.append)
    assert result.is_empty()
    assert len(persisted) == 1
    assert persisted[0].equals(expected)


@pytest.mark.parametrize("pool", ["CSI300", "CSI500", "SSE50", "CN_Equity_A", "CN_Index"])
def test_ashare_pools_rejected_without_reading_cache(pool):
    with pytest.raises(ValueError, match="Unsupported market"):
        get_pool(pool)


def test_custom_realtime_cannot_write_ashare_prices():
    from app.services.quote_service import QuoteService

    service = QuoteService()
    service._repo = Mock()
    with pytest.raises(ValueError, match="canonical Taiwan"):
        service._process_full_market_records(
            [{"symbol": "600000.SH", "last_price": 10.0}], t0=0.0, now_ts=0.0,
        )
    service._repo.flush_live_daily.assert_not_called()


def test_custom_adjustment_source_receives_optional_date_bounds(monkeypatch):
    expected = pl.DataFrame({"symbol": ["2330.TWSE"], "ex_factor": [1.0]})
    class Provider:
        def get_adj_factors(self, symbols, start_time, end_time):
            assert symbols == ["2330.TWSE"]
            assert start_time is None and end_time is None
            return expected

    monkeypatch.setattr(kline_sync.preferences, "get_adj_factor_provider", lambda: "custom_adj")
    monkeypatch.setattr("app.data_providers.custom.provider_has_dataset", lambda *args: True)
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda name: Provider())
    assert kline_sync.fetch_adj_factor_single("2330.TWSE").equals(expected)
