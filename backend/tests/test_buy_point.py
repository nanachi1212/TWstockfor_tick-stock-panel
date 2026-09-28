from pathlib import Path

from app.taiwan.buy_point import (
    BuyPointConditions,
    BuyPointMarketData,
    BuyPointRiskFilters,
    BuyPointStrategy,
    BuyPointStrategyStore,
    builtin_presets,
    evaluate_buy_point,
    strategy_catalog,
)


def strategy(**kwargs):
    now = "2026-09-29T00:00:00+00:00"
    return BuyPointStrategy(
        id="test", name="測試", description="", category="test",
        conditions=BuyPointConditions(**kwargs),
        risk_filters=BuyPointRiskFilters(exclude_regulatory_unknown=False),
        created_at=now, updated_at=now,
    )


def data(**kwargs):
    return BuyPointMarketData(
        symbol="2330.TWSE", name="台積電", risk_data_available=True,
        quant_universe=True, **kwargs,
    )


def test_catalog_has_ten_presets():
    assert len(builtin_presets()) == 10
    assert {item.id for item in builtin_presets()} == {
        "quant_pullback", "breakout_high", "volume_breakout", "institutional_turn",
        "foreign_holding", "revenue_pullback", "earnings_confirmed", "value_growth",
        "event_confirmed", "resonance",
    }


def test_quant_triggered_and_explained():
    signal = evaluate_buy_point(
        strategy(quant_min=70),
        data(quant_score=78),
    )
    assert signal.status == "triggered"
    assert "Quant >= 70" in signal.triggered_conditions
    assert signal.failed_conditions == []


def test_missing_is_unavailable_not_false():
    signal = evaluate_buy_point(strategy(quant_min=70), data())
    assert signal.status == "unavailable"
    assert "Quant >= 70" in signal.failed_conditions


def test_approaching_breakout_is_explicit():
    closes = [{"close": value} for value in [95, 96, 97, 98, 99, 99.5]]
    signal = evaluate_buy_point(
        strategy(quant_min=60, breakout_window=5),
        data(quant_score=70, daily=closes, price=98.5),
    )
    assert signal.status == "approaching"
    assert "接近突破價" in signal.triggered_conditions


def test_risk_gate_blocks_even_when_conditions_pass():
    signal = evaluate_buy_point(
        strategy(quant_min=60),
        data(quant_score=80, severe_event_risk=True),
    )
    assert signal.status == "blocked"
    assert signal.risk_flags == ["重大事件風險"]


def test_builtin_override_preserves_ten_strategy_catalog(tmp_path: Path):
    store = BuyPointStrategyStore(tmp_path / "strategies.json")
    disabled = builtin_presets()[0].model_copy(update={"enabled": False})
    store.save(disabled)
    catalog = strategy_catalog(store)
    assert len(catalog) == 10
    assert next(item for item in catalog if item.id == disabled.id).enabled is False
