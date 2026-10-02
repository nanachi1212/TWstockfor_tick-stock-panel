from __future__ import annotations

from unittest.mock import patch

from app.taiwan.buy_point import BuyPointMarketData, BuyPointSignal
from app.taiwan.buy_point_service import BuyPointService, strategy_definition_digest


def _triggered_signal() -> BuyPointSignal:
    return BuyPointSignal(
        strategy_id="quant_pullback",
        symbol="2330.TWSE",
        detected_at="2026-10-02T10:00:00+08:00",
        data_as_of="2026-10-01",
        status="triggered",
        price=95,
        reference_high=100,
        entry_zone_low=94,
        entry_zone_high=97,
        explanation="test",
        freshness="daily_cached",
    )


def _market_data(lows: list[float]) -> BuyPointMarketData:
    dates = [
        "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
        "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01",
    ][-len(lows):]
    return BuyPointMarketData(
        symbol="2330.TWSE",
        data_as_of="2026-10-01",
        freshness="daily_cached",
        price=95,
        daily=[
            {"date": day, "low": low, "close": 95}
            for day, low in zip(dates, lows, strict=True)
        ],
    )


def test_candidate_builds_server_trade_plan_from_complete_raw_lows(tmp_path):
    svc = BuyPointService(tmp_path, stop_lookback=10)
    with (
        patch("app.taiwan.buy_point_service.build_market_data", return_value=_market_data(list(range(89, 99)))),
        patch("app.taiwan.buy_point_service.evaluate_buy_point", return_value=_triggered_signal()),
    ):
        candidate = svc.candidate(
            "2330.TWSE",
            "quant_pullback",
            instrument_type="stock",
            evidence_as_of="2026-10-01",
        )

    assert candidate.trade_plan is not None
    assert candidate.trade_plan.stop_price == 89
    assert candidate.trade_plan.strategy_id == "quant_pullback"
    assert candidate.plan_unavailable_reason is None


def test_candidate_fails_closed_when_raw_stop_window_is_incomplete(tmp_path):
    svc = BuyPointService(tmp_path, stop_lookback=10)
    with (
        patch("app.taiwan.buy_point_service.build_market_data", return_value=_market_data([90] * 9)),
        patch("app.taiwan.buy_point_service.evaluate_buy_point", return_value=_triggered_signal()),
    ):
        candidate = svc.candidate(
            "2330.TWSE",
            "quant_pullback",
            instrument_type="stock",
            evidence_as_of="2026-10-01",
        )

    assert candidate.trade_plan is None
    assert candidate.plan_unavailable_reason == "complete_raw_stop_lows_unavailable"


def test_strategy_definition_digest_excludes_generated_catalog_timestamps():
    from app.taiwan.buy_point import builtin_presets

    first = builtin_presets()[0]
    second = first.model_copy(
        update={"created_at": "different", "updated_at": "different"}
    )
    assert strategy_definition_digest(first) == strategy_definition_digest(second)
