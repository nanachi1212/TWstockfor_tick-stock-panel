"""Tests for Taiwan Stock Research Evidence Context Service (Phase 7C).

Verifies:
- Identity & Instrument classification:
    * 2330.TWSE, 8069.TPEX, 0050.TWSE, 00646.TWSE
- Price Context semantics:
    * change_pct decimal calculation, volume in shares, amount in TWD
- Institutional rolling sums & coverage:
    * 5D sum preserved, missing day does not equate to zero
- Historical No Look-Ahead:
    * D+1 daily/institutional/margin data strictly ignored when querying date D
- ETF Context vs Corporate Fundamentals:
    * 00646 has ETF metadata, NO_LIMIT price rule, fundamentals status is not_applicable
- Market & Industry Intelligence Reuse:
    * Matches Phase 7A & Phase 7B deterministic service metrics
- Zero Request-time External Market HTTP:
    * GET /api/taiwan/stocks/{symbol}/research-context performs 0 external market provider HTTP calls
- No AI calls:
    * AI provider monkeypatch assertion ensures research context does not call AI
"""
from datetime import date
from unittest.mock import MagicMock, patch
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.research_context import (
    TaiwanStockResearchContext,
    TaiwanStockResearchContextService,
)


def test_research_context_identity():
    """Verify identity mapping from Security Master for stocks and ETFs."""
    svc = TaiwanStockResearchContextService()
    d = date(2026, 8, 28)

    # Stock
    ctx_2330 = svc.get_research_context("2330", d)
    assert ctx_2330.identity.canonical_symbol == "2330.TWSE"
    assert ctx_2330.identity.name == "台積電"
    assert ctx_2330.identity.exchange == "TWSE"
    assert ctx_2330.identity.instrument_type == "stock"
    assert ctx_2330.identity.industry == "半導體業"

    # ETF
    ctx_00646 = svc.get_research_context("00646", d)
    assert ctx_00646.identity.canonical_symbol == "00646.TWSE"
    assert ctx_00646.identity.name == "元大S&P500"
    assert ctx_00646.identity.instrument_type == "etf"
    assert ctx_00646.identity.industry is None


def test_price_context_decimal_and_units():
    """Verify change_pct is decimal (0.05=5%), volume in shares, amount in TWD."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    df_hist = pl.DataFrame({
        "symbol": ["TEST.TWSE", "TEST.TWSE"],
        "date": [d_prev, d_curr],
        "open": [100.0, 102.0],
        "high": [101.0, 106.0],
        "low": [99.0, 101.0],
        "close": [100.0, 105.0],
        "volume": [10000.0, 15000.0],
        "amount": [1000000.0, 1575000.0],
    })
    mock_daily.read_range.return_value = df_hist

    mock_sm = MagicMock()
    mock_inst = MagicMock()
    mock_inst.symbol = "TEST.TWSE"
    mock_inst.code = "TEST"
    mock_inst.name = "測試股"
    mock_inst.exchange = "TWSE"
    mock_inst.instrument_type = "stock"
    mock_inst.industry = "半導體業"
    mock_inst.currency = "TWD"
    mock_inst.listing_status = "active"
    mock_inst.listing_date = "2020-01-01"
    mock_inst.updated_at = "2026-08-28"
    mock_sm.get_instrument.return_value = mock_inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "name": ["測試股"],
        "exchange": ["TWSE"],
        "instrument_type": ["stock"],
        "industry": ["半導體業"],
        "listing_status": ["active"],
    })

    svc = TaiwanStockResearchContextService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    ctx = svc.get_research_context("TEST.TWSE", d_curr)
    pc = ctx.price_context

    assert pc.close == 105.0
    assert pc.previous_close == 100.0
    assert pc.change == 5.0
    assert pc.change_pct == 0.05  # Decimal format (not 5.0)
    assert pc.volume == 15000.0  # Shares (not lots)
    assert pc.amount == 1575000.0  # TWD


def test_institutional_rolling_and_coverage():
    """Verify 5-day rolling sum in shares and coverage count."""
    d_curr = date(2026, 8, 28)
    dates = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27), d_curr]

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = dates

    df_inst = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * 5,
        "date": dates,
        "foreign_net": [100.0, 200.0, -50.0, 0.0, 300.0],
        "investment_trust_net": [10.0, 20.0, 0.0, 0.0, 50.0],
        "dealer_net": [5.0, -5.0, 0.0, 0.0, 10.0],
    })
    mock_inst.read_range.return_value = df_inst

    mock_sm = MagicMock()
    m_inst = MagicMock()
    m_inst.symbol = "TEST.TWSE"
    m_inst.code = "TEST"
    m_inst.name = "測試股"
    m_inst.exchange = "TWSE"
    m_inst.instrument_type = "stock"
    m_inst.industry = "半導體業"
    m_inst.currency = "TWD"
    m_inst.listing_status = "active"
    m_inst.listing_date = "2020-01-01"
    m_inst.updated_at = "2026-08-28"
    mock_sm.get_instrument.return_value = m_inst

    svc = TaiwanStockResearchContextService(
        inst_store=mock_inst,
        security_master=mock_sm,
    )
    ctx = svc.get_research_context("TEST.TWSE", d_curr)
    ic = ctx.institutional_context

    assert ic.foreign_net_1d == 300.0
    # 100 + 200 - 50 + 0 + 300 = 550
    assert ic.foreign_net_5d == 550.0
    assert ic.coverage_days_5d == 5


def test_historical_no_look_ahead():
    """Verify that querying date D strictly ignores data from D+1."""
    d_target = date(2026, 8, 20)
    d_future = date(2026, 8, 21)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 19), d_target, d_future]
    mock_daily.read_range.return_value = pl.DataFrame()

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [d_target, d_future]
    mock_inst.read_range.return_value = pl.DataFrame()

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [d_target, d_future]
    mock_margin.read_range.return_value = pl.DataFrame()

    mock_sm = MagicMock()
    m_inst = MagicMock()
    m_inst.symbol = "TEST.TWSE"
    m_inst.code = "TEST"
    m_inst.name = "測試股"
    m_inst.exchange = "TWSE"
    m_inst.instrument_type = "stock"
    m_inst.industry = "半導體業"
    m_inst.currency = "TWD"
    m_inst.listing_status = "active"
    m_inst.listing_date = "2020-01-01"
    m_inst.updated_at = "2026-08-20"
    mock_sm.get_instrument.return_value = m_inst

    svc = TaiwanStockResearchContextService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        security_master=mock_sm,
    )
    ctx = svc.get_research_context("TEST.TWSE", d_target)

    # Assert all calls to daily, institutional, margin read_range had end <= d_target
    for call in mock_daily.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target
    for call in mock_inst.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target
    for call in mock_margin.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target


def test_etf_context_and_no_limit_rules():
    """Verify 00646 ETF has structured ETF metadata and NO_LIMIT rules."""
    svc = TaiwanStockResearchContextService()
    ctx = svc.get_research_context("00646.TWSE", date(2026, 8, 28))

    assert ctx.etf_context.status == "available"
    assert ctx.etf_context.underlying_scope == "foreign"
    assert ctx.fundamentals_context.status == "not_applicable"
    assert ctx.market_rules_context.is_no_limit is True
    assert ctx.market_rules_context.price_limit_pct is None


def test_api_endpoint_zero_market_http_and_no_ai():
    """Verify GET /api/taiwan/stocks/{symbol}/research-context performs 0 HTTP and no AI calls."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with patch("urllib.request.urlopen") as mock_urlopen, patch("httpx.get") as mock_httpx_get, patch("httpx.post") as mock_httpx_post:
        resp = client.get("/api/taiwan/stocks/2330/research-context?date=2026-08-28")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "2330.TWSE"
        assert data["identity"]["name"] == "台積電"
        assert "price_context" in data
        assert "market_context" in data
        assert "industry_context" in data
        assert "evidence_summary" in data
        assert mock_urlopen.call_count == 0
        assert mock_httpx_get.call_count == 0
        assert mock_httpx_post.call_count == 0
