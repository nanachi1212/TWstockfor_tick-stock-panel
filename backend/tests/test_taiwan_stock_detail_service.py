"""Unit tests for TaiwanStockDetailService (Phase 6A).

Verifies:
  - Aggregation of Identity, Price Limit, Realtime, Daily, Institutional, Margin, Factors, Context, Monitor.
  - Handling of domestic stock (2330.TWSE, ±10%).
  - Handling of ETF (0050.TWSE, ±10%).
  - Handling of Leveraged ETF (00631L.TWSE, ±20%).
  - Handling of Inverse ETF (00632R.TWSE, ±10%).
  - Handling of Foreign Equity ETF (00646.TWSE, NO_LIMIT).
  - Handling of TPEx equity (8069.TPEX, TPEx exchange and context).
  - Partial data tolerance when one or more providers fail/empty.
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.detail_models import (
    SectionMeta,
    TaiwanDailyRow,
    TaiwanHistoricalDaily,
    TaiwanStockDetailResponse,
    TaiwanStockRealtime,
)
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.realtime.models import TaiwanRealtimeQuote
from app.taiwan.symbol import parse_symbol


def _make_sample_quote(symbol: str, price: float, prev_close: float) -> TaiwanRealtimeQuote:
    _, ex = symbol.split(".")
    meta = SourceMeta(
        source="twse:mis",
        source_url="https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
        trade_date=date(2026, 8, 28),
        fetched_at=datetime(2026, 8, 30, 20, 0, 0),
        status="official_snapshot",
        is_realtime=False,
    )
    return TaiwanRealtimeQuote(
        symbol=symbol,
        name="測試標的",
        exchange=ex,
        last_price=price,
        prev_close=prev_close,
        open=price,
        high=price + 5,
        low=price - 5,
        change=price - prev_close,
        change_pct=round((price - prev_close) / prev_close * 100, 2),
        volume=1000000,
        amount=price * 1000000,
        quote_time=datetime(2026, 8, 28, 13, 30, 0),
        trade_date=date(2026, 8, 28),
        market_status="closed",
        source_meta=meta,
        bids=[(price, 10000), (price - 1, 20000)],
        asks=[(price + 1, 15000), (price + 2, 25000)],
    )


def test_service_stock_aggregation_2330():
    svc = TaiwanStockDetailService()
    # Mock providers to keep test deterministic and fast
    mock_rt = MagicMock()
    mock_rt.get_quotes.return_value = {"2330.TWSE": _make_sample_quote("2330.TWSE", 2420.0, 2410.0)}
    svc.realtime_service = mock_rt

    res = svc.get_stock_detail("2330.TWSE", days=30)
    assert isinstance(res, TaiwanStockDetailResponse)
    assert res.symbol == "2330.TWSE"
    assert res.identity.name == "台積電"
    assert res.identity.exchange == "TWSE"
    assert res.identity.instrument_type == "stock"
    assert res.price_limit.is_no_limit is False
    assert res.price_limit.price_limit_pct == 0.1
    assert res.price_limit.limit_up == 2650.0
    assert res.price_limit.limit_down == 2170.0
    assert res.realtime.last_price == 2420.0
    assert len(res.realtime.bids) == 2
    assert len(res.realtime.asks) == 2
    assert res.market_context.benchmark_symbol == "TAIEX"


def test_service_realtime_preserves_delayed_quote_freshness():
    svc = TaiwanStockDetailService()
    quote = _make_sample_quote("2330.TWSE", 2420.0, 2410.0)
    quote.source_meta = SourceMeta(
        source="yahoo:chart",
        source_url="https://query1.finance.yahoo.com/",
        trade_date=date(2026, 8, 28),
        fetched_at=datetime(2026, 8, 30, 20, 0, 0),
        status="delayed",
        source_type="third_party_aggregator",
        freshness_class="delayed_15m",
        is_realtime=False,
    )
    mock_rt = MagicMock()
    mock_rt.get_quotes.return_value = {"2330.TWSE": quote}
    svc.realtime_service = mock_rt

    result = svc.get_stock_detail("2330.TWSE", days=30)

    assert result.realtime.meta.source_type == "third_party_aggregator"
    assert result.realtime.meta.freshness_class == "delayed_15m"
    assert result.realtime.meta.is_realtime is False


def test_service_etf_no_limit_00646():
    svc = TaiwanStockDetailService()
    mock_rt = MagicMock()
    mock_rt.get_quotes.return_value = {"00646.TWSE": _make_sample_quote("00646.TWSE", 76.85, 76.95)}
    svc.realtime_service = mock_rt

    res = svc.get_stock_detail("00646.TWSE")
    assert res.symbol == "00646.TWSE"
    assert res.identity.instrument_type == "etf"
    assert res.identity.etf_category == "foreign_equity"
    assert res.price_limit.is_no_limit is True
    assert res.price_limit.price_limit_pct is None
    assert res.price_limit.limit_up is None
    assert res.price_limit.limit_down is None


def test_service_leveraged_inverse_etfs():
    svc = TaiwanStockDetailService()
    mock_rt = MagicMock()
    mock_rt.get_quotes.side_effect = lambda syms: {
        "00631L.TWSE": _make_sample_quote("00631L.TWSE", 36.3, 35.95),
        "00632R.TWSE": _make_sample_quote("00632R.TWSE", 9.89, 9.95),
    }
    svc.realtime_service = mock_rt

    # 00631L (Domestic Leveraged 2x -> ±20%)
    res_l = svc.get_stock_detail("00631L.TWSE")
    assert res_l.price_limit.is_no_limit is False
    assert res_l.price_limit.price_limit_pct == 0.2
    assert res_l.price_limit.limit_up == 43.14

    # 00632R (Domestic Inverse 1x -> ±10%)
    res_r = svc.get_stock_detail("00632R.TWSE")
    assert res_r.price_limit.is_no_limit is False
    assert res_r.price_limit.price_limit_pct == 0.1
    assert res_r.price_limit.limit_up == 10.94


def test_service_tpex_equity_8069():
    svc = TaiwanStockDetailService()
    mock_rt = MagicMock()
    mock_rt.get_quotes.return_value = {"8069.TPEX": _make_sample_quote("8069.TPEX", 160.5, 159.0)}
    svc.realtime_service = mock_rt

    res = svc.get_stock_detail("8069.TPEX")
    assert res.symbol == "8069.TPEX"
    assert res.identity.name == "元太"
    assert res.identity.exchange == "TPEX"
    assert res.market_context.benchmark_symbol == "TPEX_INDEX"
    assert res.market_context.benchmark_name == "櫃買指數"


def test_service_partial_failure_tolerance():
    svc = TaiwanStockDetailService()
    # Simulate failed realtime, failed daily, failed institutional
    mock_rt = MagicMock()
    mock_rt.get_quotes.side_effect = RuntimeError("Realtime network timeout")
    svc.realtime_service = mock_rt

    mock_daily = MagicMock()
    mock_daily.get_daily.return_value = pl.DataFrame()
    svc.hybrid_provider = mock_daily

    mock_inst = MagicMock()
    mock_inst.fetch_live_day.side_effect = RuntimeError("TWSE T86 timeout")
    svc.institutional_provider = mock_inst

    mock_margin = MagicMock()
    mock_margin.fetch_live_day.side_effect = RuntimeError("MI_MARGN timeout")
    svc.margin_provider = mock_margin

    # Service should still return complete valid response with status='unavailable'
    res = svc.get_stock_detail("2330.TWSE")
    assert isinstance(res, TaiwanStockDetailResponse)
    assert res.symbol == "2330.TWSE"
    assert res.identity.name == "台積電"
    assert res.realtime.meta.status == "unavailable"
    assert res.daily_history.status == "unavailable"
    assert res.institutional.status == "unavailable"
    assert res.margin.status == "unavailable"
    assert res.overall_data_quality == "degraded"


def test_turnover_fallback_requires_exact_same_session():
    quote = TaiwanStockRealtime(
        amount=None,
        meta=SectionMeta(source="twse:mis", trade_date="2026-09-24", status="available"),
    )
    daily = TaiwanHistoricalDaily(
        rows=[TaiwanDailyRow(date="2026-09-24", open=10, high=11, low=9, close=10, volume=1000, amount=123456)],
        meta=SectionMeta(source="official_daily", trade_date="2026-09-24", status="available"),
    )
    result = TaiwanStockDetailService._apply_same_session_amount_fallback(quote, daily)
    assert result.amount == 123456
    assert result.amount_meta.status == "fallback"
    assert result.amount_meta.trade_date == "2026-09-24"

    mismatch = TaiwanStockRealtime(
        amount=None,
        meta=SectionMeta(source="twse:mis", trade_date="2026-09-29", status="available"),
    )
    result = TaiwanStockDetailService._apply_same_session_amount_fallback(mismatch, daily)
    assert result.amount is None
    assert result.amount_meta.status == "unavailable"


def test_recent_sessions_use_confirmed_partitions_not_calendar_offsets():
    svc = object.__new__(TaiwanStockDetailService)
    svc.daily_store = MagicMock()
    svc.daily_store.available_dates.return_value = [
        date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 29),
    ]
    svc.trading_calendar = TaiwanTradingCalendar(
        known_holidays={date(2026, 9, 25), date(2026, 9, 28)},
    )
    assert svc._recent_confirmed_sessions() == [
        date(2026, 9, 29), date(2026, 9, 24), date(2026, 9, 23), date(2026, 9, 22), date(2026, 9, 21),
    ]


def test_section_meta_exposes_explicit_availability_contract():
    meta = SectionMeta(
        source="finmind:dataset",
        trade_date="2026-09-24",
        status="unavailable",
        fallback_reason="dataset returned no rows",
    )
    assert meta.reason == "dataset returned no rows"
    assert meta.data_date == "2026-09-24"
    assert meta.freshness == "unavailable"


def test_institutional_fallback_uses_confirmed_sessions_across_holiday_gap():
    svc = object.__new__(TaiwanStockDetailService)
    svc._recent_confirmed_sessions = lambda count=5: [date(2026, 9, 29), date(2026, 9, 24)]
    meta = SourceMeta(
        source="tpex:daily_trade",
        source_url="https://example.invalid",
        fetched_at=datetime(2026, 9, 29, 20, 0),
        trade_date=date(2026, 9, 24),
        status="official",
    )
    flow = SimpleNamespace(
        foreign_net=108263,
        investment_trust_net=-76000,
        dealer_net=646384,
        computed_net=678647,
        official_net=678647,
        meta=meta,
    )
    svc.institutional_provider = MagicMock()
    svc.institutional_provider.fetch_live_day.side_effect = lambda **kwargs: (
        [flow] if kwargs["trade_date"] == date(2026, 9, 24) else []
    )

    result = svc._aggregate_institutional(parse_symbol("8358.TPEX"))

    assert result.status == "available"
    assert result.total_net == 678647
    assert result.meta.source == "tpex:daily_trade"
    assert result.meta.data_date == "2026-09-24"
    assert [call.kwargs["trade_date"] for call in svc.institutional_provider.fetch_live_day.call_args_list] == [
        date(2026, 9, 29), date(2026, 9, 24),
    ]


def test_institutional_prefers_persisted_rows_and_computes_rolling_shares():
    svc = object.__new__(TaiwanStockDetailService)
    svc._recent_confirmed_sessions = lambda count=5: [date(2026, 9, 29), date(2026, 9, 24)]
    svc.institutional_store = MagicMock()
    svc.institutional_store.read_range.return_value = pl.DataFrame(
        {
            "symbol": ["8358.TPEX", "8358.TPEX"],
            "date": [date(2026, 9, 24), date(2026, 9, 29)],
            "foreign_net": [108263, -2000],
            "investment_trust_net": [-76000, 1000],
            "dealer_net": [646384, 500],
            "official_net": [678647, -500],
            "computed_net": [678647, -500],
            "source": ["tpex:daily_trade", "tpex:daily_trade"],
        }
    )
    svc.institutional_provider = MagicMock()

    result = svc._aggregate_institutional(parse_symbol("8358.TPEX"))

    assert result.status == "available"
    assert result.foreign_net == -2000
    assert result.foreign_net_5d == 106263
    assert result.investment_trust_net_5d == -75000
    assert result.dealer_net_5d == 646884
    assert result.meta.source == "tpex:daily_trade"
    assert result.meta.data_date == "2026-09-29"
    svc.institutional_provider.fetch_live_day.assert_not_called()


def test_margin_fallback_uses_confirmed_sessions_and_tpex_unavailable_metadata():
    svc = object.__new__(TaiwanStockDetailService)
    svc._recent_confirmed_sessions = lambda count=5: [date(2026, 9, 29), date(2026, 9, 24)]
    svc.margin_provider = MagicMock()
    svc.margin_provider.fetch_live_day.return_value = []

    result = svc._aggregate_margin(parse_symbol("8358.TPEX"))

    assert result.status == "unavailable"
    assert result.margin_balance is None
    assert result.meta.source == "tpex:margin_balance"
    assert result.meta.status == "unavailable"
    assert result.meta.reason == "official margin/short source returned no row for 8358 across 2 confirmed sessions"
    assert result.meta.data_date == "2026-09-29"
    assert result.meta.freshness == "unavailable"
    assert [call.kwargs["trade_date"] for call in svc.margin_provider.fetch_live_day.call_args_list] == [
        date(2026, 9, 29), date(2026, 9, 24),
    ]


def test_margin_prefers_persisted_row_across_holiday_gap():
    svc = object.__new__(TaiwanStockDetailService)
    svc._recent_confirmed_sessions = lambda count=5: [date(2026, 9, 29), date(2026, 9, 24)]
    svc.margin_store = MagicMock()
    svc.margin_store.read_range.return_value = pl.DataFrame(
        {
            "symbol": ["8358.TPEX"],
            "date": [date(2026, 9, 24)],
            "margin_balance": [25572000],
            "margin_change": [-417000],
            "short_balance": [541000],
            "short_change": [-10000],
            "short_margin_ratio": [2.12],
            "source": ["tpex:margin_balance"],
        }
    )
    svc.margin_provider = MagicMock()

    result = svc._aggregate_margin(parse_symbol("8358.TPEX"))

    assert result.status == "available"
    assert result.margin_balance == 25572000
    assert result.short_balance == 541000
    assert result.meta.status == "stale"
    assert result.meta.data_date == "2026-09-24"
    assert result.meta.reason == "latest persisted margin row predates the newest confirmed session"
    svc.margin_provider.fetch_live_day.assert_not_called()


def test_market_context_reads_matching_persisted_benchmark():
    svc = object.__new__(TaiwanStockDetailService)
    repo = MagicMock()
    repo.execute_all.return_value = [(date(2026, 9, 24), 260.0), (date(2026, 9, 23), 255.0)]
    result = svc._aggregate_market_context("TPEX", repo=repo)
    assert result.benchmark_symbol == "TPEX_INDEX"
    assert result.benchmark_name == "櫃買指數"
    assert result.close == 260.0
    assert result.change_pct == pytest.approx(1.960784)
    assert result.meta.source == "persisted:kline_index_daily"
    assert repo.execute_all.call_args.args[1] == ["TPEX_INDEX"]


def test_market_context_selects_taiex_and_reports_unavailable_contract():
    svc = object.__new__(TaiwanStockDetailService)
    repo = MagicMock()
    repo.execute_all.return_value = []

    result = svc._aggregate_market_context("TWSE", repo=repo)

    assert result.benchmark_symbol == "TAIEX"
    assert result.benchmark_name == "發行量加權股價指數"
    assert result.close is None
    assert result.meta.source == "persisted:kline_index_daily"
    assert result.meta.status == "unavailable"
    assert result.meta.reason == "persisted benchmark TAIEX is unavailable"
    assert result.meta.freshness == "unavailable"
    assert repo.execute_all.call_args.args[1] == ["TAIEX"]
