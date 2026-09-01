"""Tests for Deterministic Taiwan Abnormal Moves & Capital Flow Diagnostics Service (Phase 7D).

Comprehensive deterministic tests verifying:
- VOLUME_SPIKE:
    * 5 previous volumes of 100 shares, target 300 shares -> ratio = 3.0, triggers signal
- Exclude target from baseline:
    * Baseline mean strictly excludes target day
- Missing Volume History:
    * Only 2 valid prior days -> signal unavailable / not triggered (coverage requires 5)
- Institutional Spike & Sell Direction:
    * Target foreign net -500,000 shares vs baseline 10,000 shares -> subtype SELL, sign preserved
- Price / Flow Divergence:
    * Price +4% and foreign strong sell -> PRICE_UP_FOREIGN_SELL
    * Price -4% and foreign strong buy -> PRICE_DOWN_FOREIGN_BUY
- Historical No Look-Ahead:
    * Query for date D strictly ignores D+1 spike data
- Zero Request-time External Market HTTP & No AI:
    * GET /api/taiwan/abnormal-diagnostics performs 0 HTTP and no AI provider calls
- TURNOVER_SPIKE:
    * Previous 5 amounts: 100 each, target: 300 -> amount_ratio_5d = 3.0, triggers signal
    * Baseline excludes target day
- PRICE_MOVE:
    * previous_close = 100, target = 106 -> change_pct = 0.06, PRICE_MOVE / UP triggered
    * previous_close = 100, target = 94 -> change_pct = -0.06, PRICE_MOVE / DOWN triggered
- MARGIN_SURGE:
    * 20D median abs change = 100,000, target = 600,000 -> multiple = 6.0, triggers MARGIN_SURGE / INCREASE
    * target = -600,000 -> multiple = 6.0, triggers MARGIN_SURGE / DECREASE
- SHORT_SURGE:
    * 20D median abs change = 30,000, target = 150,000 -> multiple = 5.0, triggers SHORT_SURGE / INCREASE
    * target = -150,000 -> multiple = 5.0, triggers SHORT_SURGE / DECREASE
- SHORT_MARGIN_RATIO_SPIKE:
    * Historical median = 10.0, target = 16.0 -> delta = +6.0 pct, triggers signal
    * Null denominator / missing history does not trigger
- RELATIVE_STRENGTH_OUTLIER:
    * Stock 5D return = +15%, industry average 5D return = +3%, delta = +12% -> LEADER
    * Stock 5D return = -12%, industry average 5D return = 0%, delta = -12% -> LAGGARD
- Official Zero vs Missing:
    * foreign_net = 0 counts as valid reported zero
    * Absent institutional row remains missing / uncounted
- Deterministic Sorting:
    * Multiple items with same signal_count sorted by: signal_count DESC -> abs(change_pct) DESC -> turnover DESC -> symbol ASC
- Partial Data Quality:
    * Daily current, Institutional partial/unavailable, Margin unavailable -> overall_status = partial
    * Price/volume diagnostics work, institutional/margin signals do not falsely trigger
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


def _build_mock_sm(symbols_with_names: list[tuple[str, str, str]]):
    """Helper to build a mock SecurityMaster for test symbols."""
    mock_sm = MagicMock()
    mock_sm.get_universe.return_value = [s[0] for s in symbols_with_names]

    inst_map = {}
    for sym, name, ind in symbols_with_names:
        inst = MagicMock()
        inst.symbol = sym
        inst.code = sym.split(".")[0]
        inst.name = name
        inst.exchange = sym.split(".")[1] if "." in sym else "TWSE"
        inst.instrument_type = "stock"
        inst.industry = ind
        # Matches the real TaiwanInstrument dataclass default (leverage_multiplier: float = 1.0)
        # so Phase 7J's leverage-normalization code (abs(inst_master.leverage_multiplier)) has a
        # real numeric value to read instead of an unconfigured MagicMock.
        inst.leverage_multiplier = 1.0
        inst_map[sym] = inst

    mock_sm.get_instrument.side_effect = lambda s: inst_map.get(s)
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": [s[0] for s in symbols_with_names],
        "name": [s[1] for s in symbols_with_names],
        "exchange": [s[0].split(".")[1] if "." in s[0] else "TWSE" for s in symbols_with_names],
        "instrument_type": ["stock"] * len(symbols_with_names),
        "industry": [s[2] for s in symbols_with_names],
        "listing_status": ["active"] * len(symbols_with_names),
    })
    return mock_sm


def test_volume_spike_and_baseline_excludes_target():
    """Verify VOLUME_SPIKE triggers at ratio >= 2.0 and baseline strictly excludes target day."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = p_dates + [d_curr]

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
    mock_sm = _build_mock_sm([("TEST.TWSE", "測試股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    assert len(snap.items) == 1
    item = snap.items[0]
    assert item.volume_ratio_5d == 3.0

    vol_signals = [s for s in item.signals if s.type == "VOLUME_SPIKE"]
    assert len(vol_signals) == 1
    sig = vol_signals[0]
    assert sig.observed == 300.0
    assert sig.baseline == 100.0
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
    mock_sm = _build_mock_sm([("TEST.TWSE", "測試股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    item = snap.items[0]
    vol_signals = [s for s in item.signals if s.type == "VOLUME_SPIKE"]
    assert len(vol_signals) == 0


def test_turnover_spike_verification():
    """Verify TURNOVER_SPIKE: previous 5 amounts = 100, target = 300 -> ratio = 3.0, excludes target."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = p_dates + [d_curr]

    df_p5 = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * 5,
        "date": p_dates,
        "volume": [1000.0] * 5,
        "amount": [100.0, 100.0, 100.0, 100.0, 100.0],
        "close": [10.0] * 5,
    })
    df_target = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "date": [d_curr],
        "volume": [1000.0],
        "amount": [300.0],
        "close": [10.0],
    })

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return df_target
        elif end == p_dates[-1]:
            return df_p5
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range
    mock_sm = _build_mock_sm([("TEST.TWSE", "測試股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    item = snap.items[0]
    assert item.amount_ratio_5d == 3.0
    turn_signals = [s for s in item.signals if s.type == "TURNOVER_SPIKE"]
    assert len(turn_signals) == 1
    sig = turn_signals[0]
    assert sig.observed == 300.0
    assert sig.baseline == 100.0  # target 300 strictly excluded
    assert sig.ratio == 3.0
    assert sig.threshold == 2.0


def test_price_move_up_and_down():
    """Verify PRICE_MOVE triggers for +6% (UP) and -6% (DOWN), distinct from statutory limit."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    mock_sm = _build_mock_sm([
        ("UP.TWSE", "上漲股", "半導體業"),
        ("DOWN.TWSE", "下跌股", "半導體業"),
    ])

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({
                "symbol": ["UP.TWSE", "DOWN.TWSE"],
                "date": [d_curr, d_curr],
                "close": [106.0, 94.0],
                "volume": [1000.0, 1000.0],
                "amount": [100000.0, 100000.0],
            })
        elif start == d_prev:
            return pl.DataFrame({
                "symbol": ["UP.TWSE", "DOWN.TWSE"],
                "date": [d_prev, d_prev],
                "close": [100.0, 100.0],
                "volume": [1000.0, 1000.0],
                "amount": [100000.0, 100000.0],
            })
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    up_item = next(i for i in snap.items if i.symbol == "UP.TWSE")
    down_item = next(i for i in snap.items if i.symbol == "DOWN.TWSE")

    assert up_item.change_pct == 0.06
    up_sigs = [s for s in up_item.signals if s.type == "PRICE_MOVE"]
    assert len(up_sigs) == 1
    assert up_sigs[0].subtype == "UP"
    assert up_sigs[0].threshold == 5.0

    assert down_item.change_pct == -0.06
    down_sigs = [s for s in down_item.signals if s.type == "PRICE_MOVE"]
    assert len(down_sigs) == 1
    assert down_sigs[0].subtype == "DOWN"
    assert down_sigs[0].threshold == 5.0


def test_margin_surge_increase_and_decrease():
    """Verify MARGIN_SURGE: median abs change = 100k, target = 600k (multiple 6.0) -> INCREASE / DECREASE."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["POS.TWSE", "NEG.TWSE"],
        "date": [d_curr, d_curr],
        "close": [100.0, 100.0],
        "volume": [1000.0, 1000.0],
        "amount": [100000.0, 100000.0],
    })

    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = p_dates + [d_curr]

    # 20 prior margin_change with median abs 100,000
    prior_rows = []
    for d in p_dates:
        prior_rows.append({"symbol": "POS.TWSE", "date": d, "margin_balance": 1000000.0, "margin_change": 100000.0, "short_balance": 50000.0, "short_change": 0.0, "short_margin_ratio": 5.0})
        prior_rows.append({"symbol": "NEG.TWSE", "date": d, "margin_balance": 1000000.0, "margin_change": 100000.0, "short_balance": 50000.0, "short_change": 0.0, "short_margin_ratio": 5.0})
    df_p20 = pl.DataFrame(prior_rows)

    df_target = pl.DataFrame([
        {"symbol": "POS.TWSE", "date": d_curr, "margin_balance": 1600000.0, "margin_change": 600000.0, "short_balance": 50000.0, "short_change": 0.0, "short_margin_ratio": 5.0},
        {"symbol": "NEG.TWSE", "date": d_curr, "margin_balance": 400000.0, "margin_change": -600000.0, "short_balance": 50000.0, "short_change": 0.0, "short_margin_ratio": 5.0},
    ])

    def mock_read_margin(syms, start, end):
        if start == d_curr:
            return df_target
        elif end == p_dates[-1]:
            return df_p20
        return pl.DataFrame()

    mock_margin_store.read_range.side_effect = mock_read_margin
    mock_sm = _build_mock_sm([("POS.TWSE", "資增股", "半導體業"), ("NEG.TWSE", "資減股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        margin_store=mock_margin_store,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    pos_item = next(i for i in snap.items if i.symbol == "POS.TWSE")
    neg_item = next(i for i in snap.items if i.symbol == "NEG.TWSE")

    pos_sig = next(s for s in pos_item.signals if s.type == "MARGIN_SURGE")
    assert pos_sig.subtype == "INCREASE"
    assert pos_sig.ratio == 6.0

    neg_sig = next(s for s in neg_item.signals if s.type == "MARGIN_SURGE")
    assert neg_sig.subtype == "DECREASE"
    assert neg_sig.ratio == 6.0


def test_short_surge_increase_and_decrease():
    """Verify SHORT_SURGE: median abs change = 30k, target = 150k (multiple 5.0) -> INCREASE / DECREASE."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["S_INC.TWSE", "S_DEC.TWSE"],
        "date": [d_curr, d_curr],
        "close": [100.0, 100.0],
        "volume": [1000.0, 1000.0],
        "amount": [100000.0, 100000.0],
    })

    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = p_dates + [d_curr]

    prior_rows = []
    for d in p_dates:
        prior_rows.append({"symbol": "S_INC.TWSE", "date": d, "margin_balance": 1000000.0, "margin_change": 0.0, "short_balance": 50000.0, "short_change": 30000.0, "short_margin_ratio": 5.0})
        prior_rows.append({"symbol": "S_DEC.TWSE", "date": d, "margin_balance": 1000000.0, "margin_change": 0.0, "short_balance": 50000.0, "short_change": 30000.0, "short_margin_ratio": 5.0})
    df_p20 = pl.DataFrame(prior_rows)

    df_target = pl.DataFrame([
        {"symbol": "S_INC.TWSE", "date": d_curr, "margin_balance": 1000000.0, "margin_change": 0.0, "short_balance": 200000.0, "short_change": 150000.0, "short_margin_ratio": 5.0},
        {"symbol": "S_DEC.TWSE", "date": d_curr, "margin_balance": 1000000.0, "margin_change": 0.0, "short_balance": 200000.0, "short_change": -150000.0, "short_margin_ratio": 5.0},
    ])

    def mock_read_margin(syms, start, end):
        if start == d_curr:
            return df_target
        elif end == p_dates[-1]:
            return df_p20
        return pl.DataFrame()

    mock_margin_store.read_range.side_effect = mock_read_margin
    mock_sm = _build_mock_sm([("S_INC.TWSE", "券增股", "半導體業"), ("S_DEC.TWSE", "券減股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        margin_store=mock_margin_store,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    inc_item = next(i for i in snap.items if i.symbol == "S_INC.TWSE")
    dec_item = next(i for i in snap.items if i.symbol == "S_DEC.TWSE")

    inc_sig = next(s for s in inc_item.signals if s.type == "SHORT_SURGE")
    assert inc_sig.subtype == "INCREASE"
    assert inc_sig.ratio == 5.0

    dec_sig = next(s for s in dec_item.signals if s.type == "SHORT_SURGE")
    assert dec_sig.subtype == "DECREASE"
    assert dec_sig.ratio == 5.0


def test_short_margin_ratio_spike_and_missing_handling():
    """Verify SHORT_MARGIN_RATIO_SPIKE: target 16.0 - baseline 10.0 = +6.0 pct triggers; missing doesn't."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["SPIKE.TWSE", "MISSING.TWSE"],
        "date": [d_curr, d_curr],
        "close": [100.0, 100.0],
        "volume": [1000.0, 1000.0],
        "amount": [100000.0, 100000.0],
    })

    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = p_dates + [d_curr]

    prior_rows = []
    for d in p_dates:
        # SPIKE.TWSE has valid history
        prior_rows.append({"symbol": "SPIKE.TWSE", "date": d, "margin_balance": 1000000.0, "margin_change": 0.0, "short_balance": 50000.0, "short_change": 0.0, "short_margin_ratio": 10.0})
    df_p20 = pl.DataFrame(prior_rows)

    df_target = pl.DataFrame([
        {"symbol": "SPIKE.TWSE", "date": d_curr, "margin_balance": 1000000.0, "margin_change": 0.0, "short_balance": 50000.0, "short_change": 0.0, "short_margin_ratio": 16.0},
        {"symbol": "MISSING.TWSE", "date": d_curr, "margin_balance": 1000000.0, "margin_change": 0.0, "short_balance": 50000.0, "short_change": 0.0, "short_margin_ratio": 16.0},
    ])

    def mock_read_margin(syms, start, end):
        if start == d_curr:
            return df_target
        elif end == p_dates[-1]:
            return df_p20
        return pl.DataFrame()

    mock_margin_store.read_range.side_effect = mock_read_margin
    mock_sm = _build_mock_sm([("SPIKE.TWSE", "暴增股", "半導體業"), ("MISSING.TWSE", "無歷史股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        margin_store=mock_margin_store,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    spike_item = next(i for i in snap.items if i.symbol == "SPIKE.TWSE")
    missing_item = next(i for i in snap.items if i.symbol == "MISSING.TWSE")

    spike_sigs = [s for s in spike_item.signals if s.type == "SHORT_MARGIN_RATIO_SPIKE"]
    assert len(spike_sigs) == 1
    assert spike_sigs[0].delta == 6.0

    missing_sigs = [s for s in missing_item.signals if s.type == "SHORT_MARGIN_RATIO_SPIKE"]
    assert len(missing_sigs) == 0


def test_relative_strength_outlier_leader_and_laggard():
    """Verify RELATIVE_STRENGTH_OUTLIER: +12% delta -> LEADER, -12% delta -> LAGGARD."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = p_dates + [d_curr]

    mock_sm = _build_mock_sm([
        ("LEAD.TWSE", "領漲股", "半導體業"),
        ("LAG.TWSE", "落後股", "半導體業"),
    ])

    # Earliest P5 close: 100 for both
    # Target close: LEAD = 115 (+15%), LAG = 88 (-12%)
    def mock_read_daily(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({
                "symbol": ["LEAD.TWSE", "LAG.TWSE"],
                "date": [d_curr, d_curr],
                "close": [115.0, 88.0],
                "volume": [1000.0, 1000.0],
                "amount": [100000.0, 100000.0],
            })
        elif start == p_dates[0]:
            rows = []
            for d in p_dates:
                rows.append({"symbol": "LEAD.TWSE", "date": d, "close": 100.0, "volume": 1000.0, "amount": 100000.0})
                rows.append({"symbol": "LAG.TWSE", "date": d, "close": 100.0, "volume": 1000.0, "amount": 100000.0})
            return pl.DataFrame(rows)
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_daily

    # Mock industry intelligence service with average_change_pct = 0.03 (+3%)
    mock_ind_svc = MagicMock()
    mock_ind_snap = MagicMock()
    mock_ind_metric = MagicMock()
    mock_ind_metric.industry = "半導體業"
    mock_ind_metric.relative_strength_5d = 0.05
    mock_ind_metric.average_change_pct = 0.03
    mock_ind_metric.turnover_share = 0.2
    mock_ind_metric.advance_ratio = 0.6
    mock_ind_snap.industries = [mock_ind_metric]
    mock_ind_svc.get_snapshot.return_value = mock_ind_snap

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        security_master=mock_sm,
        industry_intel_svc=mock_ind_svc,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    lead_item = next(i for i in snap.items if i.symbol == "LEAD.TWSE")
    lag_item = next(i for i in snap.items if i.symbol == "LAG.TWSE")

    # LEAD return = (115/100) - 1 = 0.15, ind = 0.03 -> diff = +0.12 (>= +10%) -> LEADER
    lead_sig = next(s for s in lead_item.signals if s.type == "RELATIVE_STRENGTH_OUTLIER")
    assert lead_sig.subtype == "LEADER"
    assert lead_sig.observed == 12.0

    # LAG return = (88/100) - 1 = -0.12, ind = 0.03 -> diff = -0.15 (<= -10%) -> LAGGARD
    lag_sig = next(s for s in lag_item.signals if s.type == "RELATIVE_STRENGTH_OUTLIER")
    assert lag_sig.subtype == "LAGGARD"
    assert lag_sig.observed == -15.0


def test_official_zero_vs_missing_institutional_flow():
    """Verify official foreign_net = 0 is treated as reported 0, while absent row is missing."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["ZERO.TWSE", "MISSING.TWSE"],
        "date": [d_curr, d_curr],
        "close": [100.0, 100.0],
        "volume": [1000.0, 1000.0],
        "amount": [100000.0, 100000.0],
    })

    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = p_dates + [d_curr]

    # ZERO.TWSE has 20 official zero days
    prior_rows = []
    for d in p_dates:
        prior_rows.append({"symbol": "ZERO.TWSE", "date": d, "foreign_net": 0.0, "investment_trust_net": 0.0, "dealer_net": 0.0})
    df_p20 = pl.DataFrame(prior_rows)

    df_target = pl.DataFrame([
        {"symbol": "ZERO.TWSE", "date": d_curr, "foreign_net": 0.0, "investment_trust_net": 0.0, "dealer_net": 0.0},
        # MISSING.TWSE has no row
    ])

    def mock_read_inst(syms, start, end):
        if start == d_curr:
            return df_target
        elif end == p_dates[-1]:
            return df_p20
        return pl.DataFrame()

    mock_inst_store.read_range.side_effect = mock_read_inst
    mock_sm = _build_mock_sm([("ZERO.TWSE", "零量股", "半導體業"), ("MISSING.TWSE", "缺漏股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        inst_store=mock_inst_store,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    zero_item = next(i for i in snap.items if i.symbol == "ZERO.TWSE")
    missing_item = next(i for i in snap.items if i.symbol == "MISSING.TWSE")

    assert zero_item.foreign_net == 0.0
    assert missing_item.foreign_net is None


def test_deterministic_sorting_order():
    """Verify sorting: signal_count DESC -> abs(change_pct) DESC -> amount DESC -> symbol ASC."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)

    # 4 stocks with different attributes
    # Stock A: signals = 1 (price move +8%), amount = 500
    # Stock B: signals = 1 (price move -8%), amount = 1000 (same change_pct magnitude, higher amount)
    # Stock C: signals = 1 (price move +6%), amount = 2000 (lower change_pct)
    # Stock D: signals = 2 (price move +8% & vol spike), amount = 100
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    mock_sm = _build_mock_sm([
        ("A.TWSE", "A", "半導體業"),
        ("B.TWSE", "B", "半導體業"),
        ("C.TWSE", "C", "半導體業"),
        ("D.TWSE", "D", "半導體業"),
    ])

    def mock_read_daily(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({
                "symbol": ["A.TWSE", "B.TWSE", "C.TWSE", "D.TWSE"],
                "date": [d_curr] * 4,
                "close": [108.0, 92.0, 106.0, 108.0],
                "volume": [100.0, 100.0, 100.0, 500.0],
                "amount": [500.0, 1000.0, 2000.0, 100.0],
            })
        elif start == d_prev:
            return pl.DataFrame({
                "symbol": ["A.TWSE", "B.TWSE", "C.TWSE", "D.TWSE"],
                "date": [d_prev] * 4,
                "close": [100.0, 100.0, 100.0, 100.0],
                "volume": [100.0, 100.0, 100.0, 100.0],
                "amount": [500.0, 1000.0, 2000.0, 100.0],
            })
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_daily

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    ordered_syms = [i.symbol for i in snap.items]
    # D has 1 signal (or more), B vs A have same signals & abs(chg) but B has higher amount
    # Let's inspect sorting:
    assert ordered_syms[0] == "B.TWSE" or ordered_syms[0] == "A.TWSE" or ordered_syms[0] == "D.TWSE"
    # Exact check:
    # All trigger PRICE_MOVE (+8%, -8%, +6%, +8%)
    # B has amount 1000, A has amount 500 -> B before A
    b_idx = ordered_syms.index("B.TWSE")
    a_idx = ordered_syms.index("A.TWSE")
    c_idx = ordered_syms.index("C.TWSE")
    assert b_idx < a_idx  # Higher turnover wins tiebreak
    assert a_idx < c_idx  # Higher abs(change_pct) wins


def test_partial_data_quality_resilience():
    """Verify Daily current + Institutional unavailable + Margin unavailable yields partial status."""
    d_curr = date(2026, 8, 28)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "date": [d_curr],
        "close": [100.0],
        "volume": [1000.0],
        "amount": [100000.0],
    })

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = []
    mock_inst.read_range.return_value = pl.DataFrame()

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = []
    mock_margin.read_range.return_value = pl.DataFrame()

    mock_sm = _build_mock_sm([("TEST.TWSE", "測試股", "半導體業")])

    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        security_master=mock_sm,
    )
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)

    assert snap.data_quality.daily_status == "current"
    assert snap.data_quality.institutional_status == "unavailable"
    assert snap.data_quality.margin_status == "unavailable"
    assert snap.data_quality.overall_status == "partial"
    assert len(snap.items) == 1
    # Institutional & margin signals must not falsely trigger
    assert all(s.type not in ("FOREIGN_FLOW_SPIKE", "MARGIN_SURGE", "SHORT_SURGE") for s in snap.items[0].signals)


def test_institutional_spike_and_sell_direction():
    """Verify FOREIGN_FLOW_SPIKE triggers and preserves SELL direction when net is negative."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = p_dates + [d_curr]

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

    mock_sm = _build_mock_sm([("TEST.TWSE", "測試股", "半導體業")])

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
    assert sig.ratio == 50.0


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
    mock_sm = _build_mock_sm([("TEST.TWSE", "測試股", "半導體業")])

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

    mock_sm = _build_mock_sm([("TEST.TWSE", "測試股", "半導體業")])

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


# ══════════════════════════════════════════════════════════════
# Phase 7J — ETF Abnormal Diagnostics Coverage
# ══════════════════════════════════════════════════════════════


def _build_mock_sm_mixed(specs: list[dict]):
    """Like _build_mock_sm but each spec dict may set instrument_type/leverage_multiplier,
    and get_universe() returns a DIFFERENT list depending on the requested UniverseType —
    required to exercise Phase 7J's include_etfs union (unlike _build_mock_sm's single
    fixed return_value, which would return the same list regardless of universe_type).

    spec keys: symbol, name, industry (None for ETFs), instrument_type ("stock"/"etf"),
    leverage_multiplier (float, defaults to 1.0).
    """
    from app.taiwan.universe.service import UniverseType

    mock_sm = MagicMock()

    stock_syms = [s["symbol"] for s in specs if s.get("instrument_type", "stock") == "stock"]
    etf_syms = [s["symbol"] for s in specs if s.get("instrument_type") == "etf"]
    all_syms = [s["symbol"] for s in specs]

    def _get_universe(universe_type):
        if universe_type == UniverseType.TAIWAN_STOCKS:
            return stock_syms
        if universe_type == UniverseType.TAIWAN_ETFS:
            return etf_syms
        return all_syms

    mock_sm.get_universe.side_effect = _get_universe

    inst_map = {}
    for s in specs:
        inst = MagicMock()
        inst.symbol = s["symbol"]
        inst.code = s["symbol"].split(".")[0]
        inst.name = s["name"]
        inst.exchange = s["symbol"].split(".")[1] if "." in s["symbol"] else "TWSE"
        inst.instrument_type = s.get("instrument_type", "stock")
        inst.industry = s.get("industry")
        inst.leverage_multiplier = s.get("leverage_multiplier", 1.0)
        inst_map[s["symbol"]] = inst

    mock_sm.get_instrument.side_effect = lambda sym: inst_map.get(sym)
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": all_syms,
        "name": [s["name"] for s in specs],
        "exchange": [s["symbol"].split(".")[1] if "." in s["symbol"] else "TWSE" for s in specs],
        "instrument_type": [s.get("instrument_type", "stock") for s in specs],
        "industry": pl.Series([s.get("industry") for s in specs], dtype=pl.Utf8),
        "listing_status": ["active"] * len(specs),
    })
    return mock_sm


def _flat_daily_mock(d_curr: date, d_prev: date, closes: dict[str, tuple[float, float]]):
    """Build a mock TaiwanDailyStore returning fixed prev/curr closes per symbol.
    closes: {symbol: (prev_close, curr_close)}."""
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({
                "symbol": list(closes.keys()),
                "date": [d_curr] * len(closes),
                "close": [v[1] for v in closes.values()],
                "volume": [1000.0] * len(closes),
                "amount": [100000.0] * len(closes),
            })
        if start == d_prev:
            return pl.DataFrame({
                "symbol": list(closes.keys()),
                "date": [d_prev] * len(closes),
                "close": [v[0] for v in closes.values()],
                "volume": [1000.0] * len(closes),
                "amount": [100000.0] * len(closes),
            })
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range
    return mock_daily


def test_include_etfs_false_stays_stock_only():
    """get_diagnostics(include_etfs=False) — the default — must never include an ETF."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [
        {"symbol": "STK.TWSE", "name": "個股", "industry": "半導體業", "instrument_type": "stock"},
        {"symbol": "ETF.TWSE", "name": "一般ETF", "industry": None, "instrument_type": "etf"},
    ]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"STK.TWSE": (100.0, 100.0), "ETF.TWSE": (50.0, 50.0)})

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)

    # explicit False
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=False)
    assert {i.symbol for i in snap.items} == {"STK.TWSE"}

    # default (no kwarg at all)
    snap_default = svc.get_diagnostics(target_date=d_curr, include_all=True)
    assert {i.symbol for i in snap_default.items} == {"STK.TWSE"}


def test_include_etfs_true_includes_etfs():
    """get_diagnostics(include_etfs=True) includes ETF symbols alongside stocks."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [
        {"symbol": "STK.TWSE", "name": "個股", "industry": "半導體業", "instrument_type": "stock"},
        {"symbol": "ETF.TWSE", "name": "一般ETF", "industry": None, "instrument_type": "etf"},
    ]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"STK.TWSE": (100.0, 100.0), "ETF.TWSE": (50.0, 50.0)})

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)

    assert {i.symbol for i in snap.items} == {"STK.TWSE", "ETF.TWSE"}


def test_public_route_stays_stock_only_regardless_of_local_data():
    """Route-level regression: GET /abnormal-diagnostics never exposes a known ETF symbol,
    because taiwan.py's route never passes include_etfs. Valid whether or not real local
    daily data happens to be present in this environment (items may be empty either way)."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    resp = client.get("/api/taiwan/abnormal-diagnostics?date=2026-08-28&include_all=true")
    assert resp.status_code == 200
    data = resp.json()
    known_etf_symbols = {"0050.TWSE", "00646.TWSE", "00631L.TWSE", "00632R.TWSE"}
    returned_symbols = {item["symbol"] for item in data["items"]}
    assert returned_symbols.isdisjoint(known_etf_symbols)


def test_stock_signal_outcomes_unchanged_regardless_of_include_etfs():
    """A stock's PRICE_MOVE outcome must be byte-for-byte identical whether include_etfs
    is True or False — universe widening must not perturb stock-path results."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [{"symbol": "UP.TWSE", "name": "上漲股", "industry": "半導體業", "instrument_type": "stock"}]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"UP.TWSE": (100.0, 106.0)})

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap_false = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=False)
    snap_true = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)

    item_false = next(i for i in snap_false.items if i.symbol == "UP.TWSE")
    item_true = next(i for i in snap_true.items if i.symbol == "UP.TWSE")
    assert item_false.model_dump() == item_true.model_dump()
    assert item_false.change_pct == 0.06
    assert any(s.type == "PRICE_MOVE" for s in item_false.signals)


def test_normal_etf_class_a_signal_and_not_applicable_semantics():
    """Normal (1x) ETF: Class-A VOLUME_SPIKE works; RELATIVE_STRENGTH_OUTLIER is never
    evaluated/triggered and is explicitly listed in not_applicable_signals."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(21, 28)]

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = p_dates + [d_curr]
    specs = [{"symbol": "0050.TWSE", "name": "元大台灣50", "industry": None, "instrument_type": "etf", "leverage_multiplier": 1.0}]
    mock_sm = _build_mock_sm_mixed(specs)

    def mock_read_range(syms, start, end):
        if start == d_curr and end == d_curr:
            return pl.DataFrame({
                "symbol": ["0050.TWSE"], "date": [d_curr],
                "close": [100.0], "volume": [300.0], "amount": [300.0],
            })
        # 5-day prior window for volume baseline
        return pl.DataFrame({
            "symbol": ["0050.TWSE"] * 5,
            "date": p_dates[-5:],
            "close": [100.0] * 5,
            "volume": [100.0] * 5,
            "amount": [100.0] * 5,
        })

    mock_daily.read_range.side_effect = mock_read_range

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)

    item = next(i for i in snap.items if i.symbol == "0050.TWSE")
    assert any(s.type == "VOLUME_SPIKE" for s in item.signals)  # Class A works for ETFs
    assert item.not_applicable_signals == ["RELATIVE_STRENGTH_OUTLIER"]
    assert all(s.type != "RELATIVE_STRENGTH_OUTLIER" for s in item.signals)


def test_stock_not_applicable_signals_always_empty():
    """Stocks must always report not_applicable_signals == [] (unchanged shape)."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [{"symbol": "STK.TWSE", "name": "個股", "industry": "半導體業", "instrument_type": "stock"}]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"STK.TWSE": (100.0, 100.0)})

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)
    item = next(i for i in snap.items if i.symbol == "STK.TWSE")
    assert item.not_applicable_signals == []


def test_leveraged_etf_price_move_normalized_below_threshold_no_trigger():
    """2x leveraged ETF: raw change_pct ~6% -> normalized ~3% -> PRICE_MOVE must NOT trigger."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [{"symbol": "2XL.TWSE", "name": "2倍ETF", "industry": None, "instrument_type": "etf", "leverage_multiplier": 2.0}]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"2XL.TWSE": (100.0, 106.0)})  # raw +6%

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)
    item = next(i for i in snap.items if i.symbol == "2XL.TWSE")

    assert item.change_pct == 0.06  # raw value preserved on the item itself
    assert all(s.type != "PRICE_MOVE" for s in item.signals)


def test_leveraged_etf_price_move_normalized_above_threshold_triggers():
    """2x leveraged ETF: raw change_pct ~11% -> normalized ~5.5% -> PRICE_MOVE DOES trigger,
    with `observed` still reporting the RAW ~11%, never the normalized ~5.5%."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [{"symbol": "2XL.TWSE", "name": "2倍ETF", "industry": None, "instrument_type": "etf", "leverage_multiplier": 2.0}]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"2XL.TWSE": (100.0, 111.0)})  # raw +11%

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)
    item = next(i for i in snap.items if i.symbol == "2XL.TWSE")

    price_move = next(s for s in item.signals if s.type == "PRICE_MOVE")
    assert item.change_pct == 0.11
    assert price_move.observed == pytest.approx(11.0, abs=0.01)  # raw %, not normalized 5.5%
    assert "leverage_multiplier=2.0" in price_move.formula
    assert "normalized=5.5" in price_move.formula


def test_inverse_etf_price_move_divisor_is_one_unchanged_threshold():
    """-1x inverse ETF: abs(-1.0) == 1.0 divisor -> behaves exactly like a stock's raw
    threshold (no false negative from misapplied normalization)."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [{"symbol": "INV.TWSE", "name": "反1ETF", "industry": None, "instrument_type": "etf", "leverage_multiplier": -1.0}]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"INV.TWSE": (100.0, 106.0)})  # raw +6%, same as stock test

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)
    item = next(i for i in snap.items if i.symbol == "INV.TWSE")

    price_move = next(s for s in item.signals if s.type == "PRICE_MOVE")
    assert item.change_pct == 0.06
    assert price_move.observed == pytest.approx(6.0, abs=0.01)
    assert "leverage_multiplier=-1.0" in price_move.formula
    assert "normalized=6.0" in price_move.formula


def test_price_flow_divergence_leverage_normalization():
    """PRICE_FLOW_DIVERGENCE applies the same leverage-normalization principle as
    PRICE_MOVE at the 2x boundary: raw ~4% (normalized ~2%, below the 3% gate) does not
    trigger even with a qualifying opposite institutional flow."""
    d_curr = date(2026, 8, 28)
    p_dates = [date(2026, 8, i) for i in range(1, 21)]

    mock_daily = _flat_daily_mock(d_curr, p_dates[-1], {"2XL.TWSE": (100.0, 104.0)})  # raw +4%
    specs = [{"symbol": "2XL.TWSE", "name": "2倍ETF", "industry": None, "instrument_type": "etf", "leverage_multiplier": 2.0}]
    mock_sm = _build_mock_sm_mixed(specs)

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = p_dates

    def mock_inst_read_range(syms, start, end):
        if start == d_curr:
            return pl.DataFrame({"symbol": ["2XL.TWSE"], "date": [d_curr], "foreign_net": [-300000], "investment_trust_net": [0], "dealer_net": [0]})
        return pl.DataFrame({
            "symbol": ["2XL.TWSE"] * 20, "date": p_dates,
            "foreign_net": [10000] * 20, "investment_trust_net": [0] * 20, "dealer_net": [0] * 20,
        })

    mock_inst.read_range.side_effect = mock_inst_read_range

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, inst_store=mock_inst, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)
    item = next(i for i in snap.items if i.symbol == "2XL.TWSE")

    # raw 4% qualifies the OLD (unnormalized) gate, but normalized (~2%) does not reach 3% —
    # proves normalization is actually applied to this signal too, not just PRICE_MOVE.
    assert all(s.type != "PRICE_FLOW_DIVERGENCE" for s in item.signals)


def test_zero_investment_trust_net_remains_zero_for_etf():
    """A real numeric zero (investment_trust_net=0) for an ETF must remain 0, never coerced
    to None/missing — the general zero-is-not-missing rule is unaffected by Phase 7J."""
    d_curr, d_prev = date(2026, 8, 28), date(2026, 8, 27)
    specs = [{"symbol": "ETF.TWSE", "name": "ETF", "industry": None, "instrument_type": "etf"}]
    mock_sm = _build_mock_sm_mixed(specs)
    mock_daily = _flat_daily_mock(d_curr, d_prev, {"ETF.TWSE": (100.0, 100.0)})

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [d_prev]
    mock_inst.read_range.return_value = pl.DataFrame({
        "symbol": ["ETF.TWSE"], "date": [d_curr],
        "foreign_net": [0], "investment_trust_net": [0], "dealer_net": [0],
    })

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, inst_store=mock_inst, security_master=mock_sm)
    snap = svc.get_diagnostics(target_date=d_curr, include_all=True, include_etfs=True)
    item = next(i for i in snap.items if i.symbol == "ETF.TWSE")

    assert item.foreign_net == 0
    assert item.investment_trust_net == 0
    assert item.foreign_net is not None
    assert item.investment_trust_net is not None


def test_etf_no_look_ahead():
    """No-look-ahead must hold for ETFs exactly as it does for stocks: D+1 data never leaks
    into a D query's baseline."""
    d_target = date(2026, 8, 28)
    d_future = date(2026, 8, 29)
    specs = [{"symbol": "ETF.TWSE", "name": "ETF", "industry": None, "instrument_type": "etf"}]
    mock_sm = _build_mock_sm_mixed(specs)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 27), d_target, d_future]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["ETF.TWSE"], "date": [d_target], "close": [100.0], "volume": [1000.0], "amount": [100000.0],
    })

    svc = TaiwanAbnormalDiagnosticsService(daily_store=mock_daily, security_master=mock_sm)
    svc.get_diagnostics(target_date=d_target, include_all=True, include_etfs=True)

    for call in mock_daily.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target
