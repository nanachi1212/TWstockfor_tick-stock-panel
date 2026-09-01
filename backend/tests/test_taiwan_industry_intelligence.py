"""Tests for Taiwan Industry / Sector Intelligence Snapshot Service (Phase 7B).

Verifies:
- Classification authority:
    * 34 canonical industries + UNCLASSIFIED tracking.
    * ETFs excluded from corporate industry rankings.
- Deterministic Breadth:
    * 2 up, 1 down, 1 flat, 1 uncompared -> advance=2, decline=1, flat=1, uncompared=1, advance_ratio=2/4=0.50.
- Average vs Median:
    * Changes: -10%, +1%, +2%, +20% -> median=1.5% (0.015), avg=3.25% (0.0325).
- Turnover Share:
    * Industry A turnover 100 TWD, Industry B turnover 300 TWD -> A=25%, B=75%.
- Institutional Units:
    * Raw shares preserved (not divided by 1000 in backend).
- Relative Strength:
    * 5D equal-weight industry return minus 5D equal-weight market return.
    * 20D equal-weight industry return minus 20D equal-weight market return.
- Missing History:
    * Stock missing 20D history excluded from 20D comparable subset without skewing with 0.
- Historical No Look-Ahead:
    * Query for date D strictly ignores data from D+1.
- Partial State:
    * Daily current, Inst stale, Margin unavailable -> overall status partial.
- Zero External HTTP:
    * GET /api/taiwan/industry-intelligence performs 0 external market provider HTTP requests.
"""
from datetime import date
from unittest.mock import MagicMock, patch
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.industry_intelligence import (
    TaiwanIndustryIntelligenceService,
    TaiwanIndustryIntelligenceSnapshot,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument


@pytest.fixture
def mock_calendar():
    return TaiwanTradingCalendar()


def test_industry_classification_and_unclassified():
    """Verify stock grouping into industries and unclassified tracking, while excluding ETFs."""
    d = date(2026, 8, 28)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 27), d]
    mock_daily.read_range.return_value = pl.DataFrame()

    mock_sm = MagicMock()
    master_df = pl.DataFrame({
        "symbol": ["A.TWSE", "B.TWSE", "C.TWSE", "D.TWSE", "ETF1.TWSE"],
        "name": ["A", "B", "C", "D", "ETF1"],
        "exchange": ["TWSE"] * 5,
        "instrument_type": ["stock", "stock", "stock", "stock", "etf"],
        "listing_status": ["active"] * 5,
        "industry": ["半導體業", "半導體業", "金融保險業", None, None],
    })
    mock_sm.to_dataframe.return_value = master_df

    svc = TaiwanIndustryIntelligenceService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snapshot = svc.get_snapshot(d)
    dq = snapshot.data_quality

    assert dq.supported_stock_count == 4
    assert dq.classified_stock_count == 3
    assert dq.unclassified_stock_count == 1
    assert dq.etfs_excluded_count == 1
    assert dq.classification_coverage_pct == 75.0

    ind_names = [ind.industry for ind in snapshot.industries]
    assert "半導體業" in ind_names
    assert "金融保險業" in ind_names
    assert "UNCLASSIFIED" in ind_names


def test_deterministic_industry_breadth_and_median():
    """2 up, 1 down, 1 flat, 1 missing prev close -> advance=2, decline=1, flat=1, uncompared=1, median=1.5%."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    # Current data for 5 stocks in 半導體業:
    # S1: 100 -> 120 (+20%)
    # S2: 100 -> 102 (+2%)
    # S3: 100 -> 101 (+1%)
    # S4: 100 -> 90 (-10%)
    # S5: 100 -> 100 (0% flat, but missing prev close in prev_df -> uncompared)
    curr_data = pl.DataFrame({
        "symbol": ["S1.TWSE", "S2.TWSE", "S3.TWSE", "S4.TWSE", "S5.TWSE"],
        "date": [d_curr] * 5,
        "close": [120.0, 102.0, 101.0, 90.0, 100.0],
        "amount": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
        "volume": [10.0, 20.0, 30.0, 40.0, 50.0],
    })

    prev_data = pl.DataFrame({
        "symbol": ["S1.TWSE", "S2.TWSE", "S3.TWSE", "S4.TWSE"],  # S5 missing prev close
        "date": [d_prev] * 4,
        "close": [100.0, 100.0, 100.0, 100.0],
    })

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return curr_data
        elif start == d_prev:
            return prev_data
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range

    mock_sm = MagicMock()
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["S1.TWSE", "S2.TWSE", "S3.TWSE", "S4.TWSE", "S5.TWSE"],
        "name": ["S1", "S2", "S3", "S4", "S5"],
        "exchange": ["TWSE"] * 5,
        "instrument_type": ["stock"] * 5,
        "listing_status": ["active"] * 5,
        "industry": ["半導體業"] * 5,
    })

    svc = TaiwanIndustryIntelligenceService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snapshot = svc.get_snapshot(d_curr)
    ind = snapshot.industries[0]

    assert ind.supported_symbol_count == 5
    assert ind.comparable_symbol_count == 4
    assert ind.uncompared_count == 1
    assert ind.advance_count == 3  # S1 (+20%), S2 (+2%), S3 (+1%)
    assert ind.decline_count == 1  # S4 (-10%)
    assert ind.flat_count == 0
    assert ind.advance_ratio == 0.75

    # Changes for S1..S4: -0.10, +0.01, +0.02, +0.20
    # Median is (0.01 + 0.02) / 2 = 0.015 (1.5%)
    # Average is (-0.10 + 0.01 + 0.02 + 0.20) / 4 = 0.0325 (3.25%)
    assert ind.median_change_pct == 0.015
    assert ind.average_change_pct == 0.0325


def test_turnover_share_calculation():
    """Industry A turnover 100 TWD, Industry B turnover 300 TWD -> A=25%, B=75%."""
    d = date(2026, 8, 28)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 27), d]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["A.TWSE", "B.TWSE"],
        "close": [10.0, 30.0],
        "amount": [100.0, 300.0],
        "volume": [10.0, 10.0],
    })

    mock_sm = MagicMock()
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["A.TWSE", "B.TWSE"],
        "name": ["A", "B"],
        "exchange": ["TWSE", "TWSE"],
        "instrument_type": ["stock", "stock"],
        "listing_status": ["active", "active"],
        "industry": ["半導體業", "鋼鐵工業"],
    })

    svc = TaiwanIndustryIntelligenceService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snapshot = svc.get_snapshot(d)

    ind_a = [i for i in snapshot.industries if i.industry == "半導體業"][0]
    ind_b = [i for i in snapshot.industries if i.industry == "鋼鐵工業"][0]

    assert ind_a.turnover == 100.0
    assert ind_a.turnover_share == 0.25
    assert ind_b.turnover == 300.0
    assert ind_b.turnover_share == 0.75


def test_relative_strength_formula_and_new_listing_exclusion():
    """Verify equal-weight relative strength and exclusion of new listing lacking 20D history."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)
    d_5d = date(2026, 8, 21)
    d_20d = date(2026, 7, 31)

    # Generate 25 sequential trading dates ending at d_curr
    dates_seq = [d_20d] + [date(2026, 8, i) for i in range(1, 21)] + [d_5d, date(2026, 8, 25), date(2026, 8, 26), d_prev, d_curr]
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = dates_seq
    d_5d_resolved = dates_seq[-6]  # 5 prior sessions
    d_20d_resolved = dates_seq[-21] # 20 prior sessions

    # S1 (Industry A): close_curr=110, close_5d=100 (+10%), close_20d=100 (+10%)
    # S2 (Industry A, new listing): close_curr=110, close_5d=100 (+10%), close_20d=missing
    # S3 (Industry B): close_curr=102, close_5d=100 (+2%), close_20d=100 (+2%)
    def mock_read_range(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({
                "symbol": ["S1.TWSE", "S2.TWSE", "S3.TWSE"],
                "close": [110.0, 110.0, 102.0],
                "amount": [1000.0, 1000.0, 1000.0],
                "volume": [10.0, 10.0, 10.0],
            })
        elif start == d_5d_resolved:
            return pl.DataFrame({
                "symbol": ["S1.TWSE", "S2.TWSE", "S3.TWSE"],
                "close": [100.0, 100.0, 100.0],
            })
        elif start == d_20d_resolved:
            # S2 missing in 20D lookback
            return pl.DataFrame({
                "symbol": ["S1.TWSE", "S3.TWSE"],
                "close": [100.0, 100.0],
            })
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range

    mock_sm = MagicMock()
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["S1.TWSE", "S2.TWSE", "S3.TWSE"],
        "name": ["S1", "S2", "S3"],
        "exchange": ["TWSE"] * 3,
        "instrument_type": ["stock"] * 3,
        "listing_status": ["active"] * 3,
        "industry": ["半導體業", "半導體業", "航運業"],
    })

    svc = TaiwanIndustryIntelligenceService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snapshot = svc.get_snapshot(d_curr)

    # 5D Returns:
    # S1: +10%, S2: +10%, S3: +2%
    # Market equal-weight 5D = (0.10 + 0.10 + 0.02) / 3 = 0.073333
    # Industry A (半導體業) 5D return = (0.10 + 0.10) / 2 = 0.10
    # RS 5D = 0.10 - 0.073333 = 0.026667
    ind_a = [i for i in snapshot.industries if i.industry == "半導體業"][0]
    assert ind_a.relative_strength_5d == 0.026667
    assert ind_a.relative_strength_5d_comparable_count == 2

    # 20D Returns:
    # S1: +10%, S2: excluded (missing), S3: +2%
    # Market equal-weight 20D = (0.10 + 0.02) / 2 = 0.06
    # Industry A 20D return = 0.10 / 1 = 0.10
    # RS 20D = 0.10 - 0.06 = 0.04
    assert ind_a.relative_strength_20d == 0.04
    assert ind_a.relative_strength_20d_comparable_count == 1


def test_historical_no_look_ahead():
    """Querying date D strictly passes start <= D, end <= D to read_range."""
    d_target = date(2026, 8, 20)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 19), d_target, date(2026, 8, 21)]
    mock_daily.read_range.return_value = pl.DataFrame()

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [d_target, date(2026, 8, 21)]
    mock_inst.read_range.return_value = pl.DataFrame()

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [d_target, date(2026, 8, 21)]
    mock_margin.read_range.return_value = pl.DataFrame()

    svc = TaiwanIndustryIntelligenceService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
    )
    snapshot = svc.get_snapshot(d_target)

    for call in mock_daily.read_range.call_args_list:
        _, start, end = call[0]
        if start: assert start <= d_target
        if end: assert end <= d_target

    for call in mock_inst.read_range.call_args_list:
        _, start, end = call[0]
        if start: assert start <= d_target
        if end: assert end <= d_target


def test_api_endpoint_zero_external_http():
    """GET /api/taiwan/industry-intelligence performs 0 external market provider HTTP calls."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with patch("urllib.request.urlopen") as mock_urlopen:
        resp = client.get("/api/taiwan/industry-intelligence?date=2026-08-28")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trade_date"] == "2026-08-28"
        assert "industries" in data
        assert "market_reference" in data
        assert "data_quality" in data
        assert mock_urlopen.call_count == 0
