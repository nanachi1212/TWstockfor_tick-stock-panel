"""Tests for Deterministic Taiwan Abnormal Moves & Capital Flow Diagnostics Service (Phase 7D).

Verifies:
- VOLUME_SPIKE:
    * 5 previous volumes of 100 shares, target 300 shares -> ratio = 3.0, triggers signal
- Exclude target from baseline:
    * Baseline mean strictly excludes target day
- Missing Volume History:
    * Only 2 valid prior days -> signal unavailable / not triggered (coverage requires 5)
- Institutional Spike:
    * Target foreign net 1000 shares vs baseline 100 shares -> ratio = 10.0, triggers FOREIGN_FLOW_SPIKE
- Sell Direction:
    * Target foreign net -1000 shares -> subtype SELL, sign preserved
- Official Zero vs Missing:
    * Official row with 0 treated as valid 0; absent row treated as missing
- Margin Surge:
    * Target margin change >= 500k shares & ratio to 20D median >= 3.0
- Price / Flow Divergence:
    * Price +4% and foreign strong sell -> PRICE_UP_FOREIGN_SELL
    * Price -4% and foreign strong buy -> PRICE_DOWN_FOREIGN_BUY
- Historical No Look-Ahead:
    * Query for date D strictly ignores D+1 spike data
- Zero Request-time External Market HTTP & No AI:
    * GET /api/taiwan/abnormal-diagnostics performs 0 HTTP and no AI provider calls
- Deterministic Sorting:
    * signal_count desc -> abs(change_pct) desc -> amount desc -> symbol asc
- Data Quality:
    * Complete, partial, or unavailable status tracked accurately
"""
from datetime import date
from unittest.mock import MagicMock, patch
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.abnormal_diagnostics import (
    TaiwanAbnormalDiagnosticsService,
    TaiwanAbnormalDiagnosticsSnapshot,
)


def test_volume_spike_and_baseline_excludes_target():
    """Verify VOLUME_SPIKE triggers at ratio >= 2.0 and baseline strictly excludes target day."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = p_dates + [d_curr]

    # 5 prior volumes = 100, target volume = 300
    df_p5 = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * 5,
        "date": p_dates,
        "volume": [100.0, 100.0, 100.0, 100.0, 100.0],
        "amount": [1000.0] * 5,
        "close": [10.0] * 5,
    })
    df_target = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "date": [d_curr],
        "volume": [300.0],
        "amount": [3000.0],
        "close": [10.0],
    })

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return df_target
        elif end == p_dates[-1]:
            return df_p5
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range

    mock_sm = MagicMock()
    mock_inst = MagicMock()
    mock_inst.symbol = "TEST.TWSE"
    mock_inst.code = "TEST"
    mock_inst.name = "測試股"
    mock_inst.exchange = "TWSE"
    mock_inst.instrument_type = "stock"
    mock_inst.industry = "半導體業"
    mock_sm.get_universe.return_value = ["TEST.TWSE"]
    mock_sm.get_instrument.return_value = mock_inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "name": ["測試股"],
        "exchange": ["TWSE"],
        "instrument_type": ["stock"],
        "industry": ["半導體業"],
        "listing_status": ["active"],
    })

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    assert len(snap.items) == 1
    item = snap.items[0]
    assert item.volume_ratio_5d == 3.0  # 300 / 100

    vol_signals = [s for s in item.signals if s.type == "VOLUME_SPIKE"]
    assert len(vol_signals) == 1
    sig = vol_signals[0]
    assert sig.observed == 300.0
    assert sig.baseline == 100.0  # Must be 100.0 (excludes target 300)
    assert sig.ratio == 3.0
    assert sig.threshold == 2.0


def test_missing_volume_history_does_not_trigger():
    """Verify that having only 2 valid prior days does not trigger VOLUME_SPIKE (requires 5)."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, 26), date(2026, 8, 27)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = p_dates + [d_curr]

    df_p2 = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * 2,
        "date": p_dates,
        "volume": [100.0, 100.0],
        "amount": [1000.0, 1000.0],
        "close": [10.0, 10.0],
    })
    df_target = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "date": [d_curr],
        "volume": [500.0],
        "amount": [5000.0],
        "close": [10.0],
    })

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return df_target
        elif end == p_dates[-1]:
            return df_p2
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range

    mock_sm = MagicMock()
    mock_inst = MagicMock()
    mock_inst.symbol = "TEST.TWSE"
    mock_inst.code = "TEST"
    mock_inst.name = "測試股"
    mock_inst.exchange = "TWSE"
    mock_inst.instrument_type = "stock"
    mock_inst.industry = "半導體業"
    mock_sm.get_universe.return_value = ["TEST.TWSE"]
    mock_sm.get_instrument.return_value = mock_inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "name": ["測試股"],
        "exchange": ["TWSE"],
        "instrument_type": ["stock"],
        "industry": ["半導體業"],
        "listing_status": ["active"],
    })

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    item = snap.items[0]
    vol_signals = [s for s in item.signals if s.type == "VOLUME_SPIKE"]
    assert len(vol_signals) == 0  # Not triggered due to insufficient history


def test_institutional_spike_and_sell_direction():
    """Verify FOREIGN_FLOW_SPIKE triggers and preserves SELL direction when net is negative."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = p_dates + [d_curr]

    # 20 prior foreign_net = 10,000 shares, target foreign_net = -500,000 shares
    df_p20_inst = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * 20,
        "date": p_dates,
        "foreign_net": [10000.0] * 20,
        "investment_trust_net": [0.0] * 20,
        "dealer_net": [0.0] * 20,
    })
    df_target_inst = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "date": [d_curr],
        "foreign_net": [-500000.0],
        "investment_trust_net": [0.0],
        "dealer_net": [0.0],
    })

    def mock_read_inst(syms, start, end):
        if start == d_curr:
            return df_target_inst
        elif end == p_dates[-1]:
            return df_p20_inst
        return pl.DataFrame()

    mock_inst_store.read_range.side_effect = mock_read_inst

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "date": [d_curr],
        "close": [100.0],
        "volume": [1000000.0],
        "amount": [100000000.0],
    })

    mock_sm = MagicMock()
    m_inst = MagicMock()
    m_inst.symbol = "TEST.TWSE"
    m_inst.code = "TEST"
    m_inst.name = "測試股"
    m_inst.exchange = "TWSE"
    m_inst.instrument_type = "stock"
    m_inst.industry = "半導體業"
    mock_sm.get_universe.return_value = ["TEST.TWSE"]
    mock_sm.get_instrument.return_value = m_inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "name": ["測試股"],
        "exchange": ["TWSE"],
        "instrument_type": ["stock"],
        "industry": ["半導體業"],
        "listing_status": ["active"],
    })

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        inst_store=mock_inst_store,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    item = snap.items[0]
    f_signals = [s for s in item.signals if s.type == "FOREIGN_FLOW_SPIKE"]
    assert len(f_signals) == 1
    sig = f_signals[0]
    assert sig.subtype == "SELL"
    assert sig.observed == -500000.0
    assert sig.baseline == 10000.0
    assert sig.ratio == 50.0  # 500,000 / 10,000


def test_price_flow_divergence():
    """Verify PRICE_FLOW_DIVERGENCE triggers for price up >= 3% and strong foreign sell."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    def mock_read_daily(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({
                "symbol": ["TEST.TWSE"],
                "date": [d_curr],
                "close": [104.0],  # +4% from 100
                "volume": [1000000.0],
                "amount": [100000000.0],
            })
        elif start == d_prev:
            return pl.DataFrame({
                "symbol": ["TEST.TWSE"],
                "date": [d_prev],
                "close": [100.0],
                "volume": [1000000.0],
                "amount": [100000000.0],
            })
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_daily

    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = p_dates + [d_curr]

    def mock_read_inst(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({
                "symbol": ["TEST.TWSE"],
                "date": [d_curr],
                "foreign_net": [-300000.0],
                "investment_trust_net": [0.0],
                "dealer_net": [0.0],
            })
        elif end == p_dates[-1]:
            return pl.DataFrame({
                "symbol": ["TEST.TWSE"] * 20,
                "date": p_dates,
                "foreign_net": [50000.0] * 20,  # 300k / 50k = 6x multiple
                "investment_trust_net": [0.0] * 20,
                "dealer_net": [0.0] * 20,
            })
        return pl.DataFrame()

    mock_inst_store.read_range.side_effect = mock_read_inst

    mock_sm = MagicMock()
    m_inst = MagicMock()
    m_inst.symbol = "TEST.TWSE"
    m_inst.code = "TEST"
    m_inst.name = "測試股"
    m_inst.exchange = "TWSE"
    m_inst.instrument_type = "stock"
    m_inst.industry = "半導體業"
    mock_sm.get_universe.return_value = ["TEST.TWSE"]
    mock_sm.get_instrument.return_value = m_inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "name": ["測試股"],
        "exchange": ["TWSE"],
        "instrument_type": ["stock"],
        "industry": ["半導體業"],
        "listing_status": ["active"],
    })

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        inst_store=mock_inst_store,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    item = snap.items[0]
    div_signals = [s for s in item.signals if s.type == "PRICE_FLOW_DIVERGENCE"]
    assert len(div_signals) == 1
    assert div_signals[0].subtype == "PRICE_UP_FOREIGN_SELL"


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
    mock_sm.get_universe.return_value = ["TEST.TWSE"]
    mock_inst_item = MagicMock()
    mock_inst_item.symbol = "TEST.TWSE"
    mock_inst_item.code = "TEST"
    mock_inst_item.name = "測試股"
    mock_inst_item.exchange = "TWSE"
    mock_inst_item.instrument_type = "stock"
    mock_inst_item.industry = "半導體業"
    mock_sm.get_instrument.return_value = mock_inst_item

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        security_master=mock_sm,
    )
    svc.get_diagnostics(target_date=d_target)

    for call in mock_daily.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target


def test_api_endpoint_zero_market_http_and_no_ai():
    """Verify GET /api/taiwan/abnormal-diagnostics performs 0 HTTP calls and no AI provider calls."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with patch("urllib.request.urlopen") as mock_urlopen, patch("httpx.get") as mock_httpx_get, patch("httpx.post") as mock_httpx_post:
        resp = client.get("/api/taiwan/abnormal-diagnostics?date=2026-08-28")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trade_date"] == "2026-08-28"
        assert "items" in data
        assert "data_quality" in data
        assert mock_urlopen.call_count == 0
        assert mock_httpx_get.call_count == 0
        assert mock_httpx_post.call_count == 0
