"""Stock-detail daily history: persisted store + provider, freshness and cache."""
from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now


def _rows(symbol: str, days: list) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol] * len(days), "date": days,
        "open": [100.0] * len(days), "high": [101.0] * len(days),
        "low": [99.0] * len(days), "close": [100.0] * len(days),
        "volume": [1000.0] * len(days), "amount": [100000.0] * len(days),
        "quote_ts": [None] * len(days),
    })


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaiwanDailyStore(data_dir=Path(tmp) / "daily")
        today = taipei_now().date()
        days = [today - timedelta(days=n) for n in range(40, 0, -1)]
        # The market (0050) has three newer sessions than 2330.
        store.write_batch(pl.concat([_rows("2330.TWSE", days[:-3]), _rows("0050.TWSE", days)]))
        provider = MagicMock()
        svc = TaiwanStockDetailService(
            hybrid_provider=provider, institutional_provider=MagicMock(),
            margin_provider=MagicMock(), index_provider=MagicMock(),
            fundamental_chips_service=MagicMock(), daily_store=store,
            institutional_store=MagicMock(), margin_store=MagicMock(),
            trading_calendar=TaiwanTradingCalendar(),
        )
        yield svc, provider, days


def test_provider_failure_marks_old_persisted_bars_stale(service):
    svc, provider, days = service
    provider.get_daily.side_effect = RuntimeError("TWSE down")
    history, source, stale = svc._load_daily_history("2330.TWSE")
    daily = svc._aggregate_daily(history, source, 180, stale)
    assert history.height == 37
    assert daily.status == "stale" and daily.meta.is_stale
    assert daily.meta.trade_date == days[-4].isoformat()
    assert all(v.is_stale for v in svc._aggregate_timeframe_verdicts("2330.TWSE", history, stale))


def test_provider_fills_newer_sessions_and_stays_fresh(service):
    svc, provider, days = service
    provider.get_daily.return_value = _rows("2330.TWSE", days[-5:]).with_columns(
        pl.lit("twse").alias("source"))
    history, source, stale = svc._load_daily_history("2330.TWSE")
    daily = svc._aggregate_daily(history, source, 180, stale)
    assert history.height == 40 and history.get_column("date").n_unique() == 40
    assert stale is None and daily.status == "available" and not daily.meta.is_stale
    assert source == "taiwan_daily_store+twse"


def test_store_rows_are_cached_until_a_partition_changes(service):
    svc, provider, days = service
    provider.get_daily.return_value = pl.DataFrame()
    svc.daily_store.read_range = MagicMock(wraps=svc.daily_store.read_range)
    svc._load_daily_history("2330.TWSE")
    svc._load_daily_history("2330.TWSE")
    assert svc.daily_store.read_range.call_count == 1
    svc.daily_store.write_batch(_rows("2330.TWSE", days[-3:]))
    history, _, stale = svc._load_daily_history("2330.TWSE")
    assert svc.daily_store.read_range.call_count == 2
    assert history.height == 40 and stale is None
