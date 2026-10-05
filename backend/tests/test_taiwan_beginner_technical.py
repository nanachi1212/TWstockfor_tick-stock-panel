# ruff: noqa: RUF001 -- assertions use the user-facing Traditional Chinese text.
from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.beginner_selection import BeginnerSelectionService
from app.taiwan.beginner_technical import build_beginner_technical_panel
from app.taiwan.market_rules import TickSizeModel

AS_OF = "2026-10-02"


def _market(state="favorable"):
    return SimpleNamespace(
        state=state,
        as_of=AS_OF,
        strongest_industries=["半導體業"],
    )


def _plan(**overrides):
    values = {
        "entry_zone_low": 176.5,
        "entry_zone_high": 182.5,
        "reference_high": 194.0,
        "breakout_trigger": 195.5,
        "stop_price": 166.0,
        "evidence_as_of": AS_OF,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _metrics(**overrides):
    values = {
        "adjusted_close": 190.0,
        "ma5": 188.0,
        "ma20": 180.0,
        "ma60": 170.0,
        "atr_14": 5.7,
        "today_volume": 2_000_000.0,
        "average_volume_20d": 1_000_000.0,
        "volume_ratio_20d": 2.0,
        "price_change_pct": 1.5,
        "foreign_net_5d": 500_000.0,
        "investment_trust_net_5d": 100_000.0,
        "dealer_net_5d": -50_000.0,
        "institutional_complete_sessions": 5,
        "institutional_status": "official",
        "institutional_as_of": AS_OF,
        "margin_balance": 10_300_000.0,
        "margin_change": 300_000.0,
        "short_balance": 1_000_000.0,
        "short_change": 5_000.0,
        "margin_status": "official",
        "margin_as_of": "2026-10-01",
        "stock_return_20d": 12.0,
        "benchmark_return_20d": 3.0,
        "relative_return_20d": 9.0,
        "low_20d": 160.0,
        "high_20d": 195.0,
        "range_position_pct": 85.7,
        "revenue_yoy": 25.0,
        "revenue_mom": 3.0,
        "revenue_status": "available",
        "revenue_as_of": "2026-08",
        "eps": 8.0,
        "financials_as_of": "2026-06-30",
        "pe": 18.0,
        "valuation_as_of": AS_OF,
        "quote_freshness": "current",
    }
    values.update(overrides)
    return values


def _panel(*, current=190.0, plan=None, metrics=None, market=None):
    return build_beginner_technical_panel(
        current_price=current,
        as_of=AS_OF,
        plan=_plan() if plan is None else plan,
        metrics=_metrics() if metrics is None else metrics,
        market=_market() if market is None else market,
        industry="半導體業",
        action_summary="等待回檔，不追價。",
    )


def test_trade_plan_maps_support_resistance_invalidation_and_tick_legality():
    panel = _panel()
    assert panel.support.support_zone_low == 176.5
    assert panel.support.support_zone_high == 182.5
    assert panel.resistance.resistance == 195.5
    assert panel.invalidation.invalidation == 166.0
    for value in (176.5, 182.5, 195.5, 166.0):
        assert TickSizeModel.round_order_price(value) == value


def test_resistance_falls_back_to_reference_high_and_never_uses_level_at_or_below_current():
    fallback = _panel(plan=_plan(breakout_trigger=189.0, reference_high=194.0))
    assert fallback.resistance.resistance == 194.0
    unavailable = _panel(plan=_plan(breakout_trigger=190.0, reference_high=189.5))
    assert unavailable.resistance.resistance is None
    assert unavailable.resistance.resistance_distance_pct is None

    breakout = _panel(plan=_plan(entry_zone_low=None, entry_zone_high=None))
    assert breakout.support.status == "data_insufficient"
    assert breakout.resistance.status == "available"
    assert breakout.invalidation.status == "available"


@pytest.mark.parametrize(
    ("updates", "state"),
    [
        ({}, "strong"),
        ({"adjusted_close": 160.0}, "weak"),
        ({"adjusted_close": 175.0}, "neutral"),
        ({"ma60": None}, "unavailable"),
    ],
)
def test_ma_5_20_60_classification_and_missing_data(updates, state):
    panel = _panel(metrics=_metrics(**updates))
    assert panel.moving_averages.state == state
    assert panel.moving_averages.status == ("data_insufficient" if state == "unavailable" else "available")


@pytest.mark.parametrize(
    ("price_change", "ratio", "pattern"),
    [
        (1.0, 1.2, "price_up_volume_up"),
        (1.0, 0.8, "price_up_volume_down"),
        (-1.0, 1.2, "price_down_volume_up"),
        (-1.0, 0.8, "price_down_volume_down"),
    ],
)
def test_volume_ratio_and_four_price_volume_states(price_change, ratio, pattern):
    panel = _panel(metrics=_metrics(
        price_change_pct=price_change,
        volume_ratio_20d=ratio,
        average_volume_20d=2_000_000 / ratio,
    ))
    assert panel.volume.ratio == ratio
    assert panel.volume.pattern == pattern


def test_inner_outer_is_data_insufficient_and_never_filled_with_zero():
    evidence = _panel().inner_outer
    assert evidence.status == "data_insufficient"
    assert evidence.inner_pct is None and evidence.outer_pct is None
    assert evidence.as_of is None


def test_institutional_requires_complete_five_sessions():
    complete = _panel().institutional
    assert complete.status == "available"
    assert complete.complete_sessions == 5
    assert complete.total_net_5d == 550_000

    incomplete = _panel(metrics=_metrics(institutional_complete_sessions=4)).institutional
    assert incomplete.status == "data_insufficient"
    assert incomplete.total_net_5d is None


def test_margin_uses_latest_official_date_and_rejects_stale_status():
    current = _panel().margin
    assert current.status == "available"
    assert current.as_of == "2026-10-01"
    assert current.margin_state == "increase_fast"
    stale = _panel(metrics=_metrics(margin_status="stale")).margin
    assert stale.status == "data_insufficient"
    assert stale.margin_state == "unavailable"


def test_relative_strength_range_position_volatility_and_extreme_yoy_warning():
    panel = _panel(metrics=_metrics(revenue_yoy=250.0))
    assert panel.relative_strength.state == "stronger"
    assert panel.relative_strength.excess_return_pct == 9.0
    assert panel.range_position.position_pct == pytest.approx(85.7)
    assert panel.volatility.level == "normal"
    assert panel.fundamentals.warning is not None


def test_stale_quote_marks_daily_technical_evidence_stale():
    panel = _panel(metrics=_metrics(quote_freshness="stale"))
    assert panel.support.freshness == "stale"
    assert panel.moving_averages.freshness == "stale"
    assert panel.volume.freshness == "stale"
    assert panel.relative_strength.freshness == "stale"
    assert panel.range_position.freshness == "stale"
    assert panel.volatility.freshness == "stale"


def test_fundamentals_keep_pe_only_and_separate_source_dates():
    panel = _panel(metrics=_metrics(
        revenue_status="unavailable", revenue_yoy=None, revenue_mom=None, eps=None,
    ))
    assert panel.fundamentals.status == "available"
    assert panel.fundamentals.pe == 18.0
    assert panel.fundamentals.revenue_as_of == "2026-08"
    assert panel.fundamentals.financials_as_of == "2026-06-30"
    assert panel.fundamentals.valuation_as_of == AS_OF


def test_missing_benchmark_stays_unavailable_and_key_risks_are_limited():
    panel = _panel(metrics=_metrics(benchmark_return_20d=None, relative_return_20d=None))
    assert panel.relative_strength.status == "data_insufficient"
    assert panel.relative_strength.benchmark_return_pct is None
    assert 2 <= len(panel.key_risks) <= 4


def test_service_reuses_pit_factor_panel_for_ma_volume_atr_and_0050():
    sessions = [date(2026, 6, 1) + timedelta(days=index) for index in range(65)]
    rows = []
    for symbol, base, step in (("2330.TWSE", 100.0, 1.0), ("0050.TWSE", 100.0, 0.2)):
        for index, day in enumerate(sessions):
            close = base + index * step
            rows.append({
                "symbol": symbol, "date": day, "open": close - 0.5,
                "high": close + 1.0, "low": close - 1.0, "close": close,
                "volume": 1_000_000.0 + index * 10_000,
                "amount": 100_000_000.0, "quote_ts": None,
            })
    frame = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))

    class Daily:
        def available_dates(self):
            return sessions

        def read_range(self, symbols, start, end):
            return frame.filter(
                pl.col("symbol").is_in(symbols)
                & (pl.col("date") >= start)
                & (pl.col("date") <= end)
            )

    screener = SimpleNamespace(
        daily_store=Daily(),
        action_store=SimpleNamespace(read_verified_window=lambda start, end: ()),
    )
    metrics = BeginnerSelectionService(screener=screener)._technical_metrics(
        ["2330.TWSE"], sessions[-1]
    )["2330.TWSE"]
    assert metrics["ma5"] is not None and metrics["ma20"] is not None and metrics["ma60"] is not None
    assert metrics["volume_ratio_20d"] is not None
    assert metrics["atr_14"] is not None
    assert metrics["stock_return_20d"] > metrics["benchmark_return_20d"]
    assert metrics["relative_return_20d"] > 0
