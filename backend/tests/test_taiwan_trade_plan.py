from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.buy_point import BuyPointSignal
from app.taiwan.corporate_actions import (
    CorporateActionEvent,
    CorporateActionStore,
    event_market_open,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.live_contract import canonical_hash
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.trade_plan import (
    PRICE_ADJUSTMENT_SEMANTICS,
    TRADE_PLAN_VERSION,
    TradePlanEvaluator,
    build_trade_plan,
    evaluate_trade_plan,
)

EVIDENCE = date(2026, 9, 1)


def _signal(symbol="2330.TWSE", **updates):
    values = {
        "strategy_id": "quant_pullback",
        "symbol": symbol,
        "detected_at": "2026-09-01T13:30:00+08:00",
        "data_as_of": EVIDENCE.isoformat(),
        "status": "triggered",
        "price": 95.0,
        "reference_high": 100.0,
        "entry_zone_low": 94.0,
        "entry_zone_high": 97.0,
        "quant_score": 80.0,
        "explanation": "test",
        "freshness": "daily_cached",
    }
    values.update(updates)
    return BuyPointSignal(**values)


def _plan(**kwargs):
    return build_trade_plan(
        kwargs.pop("signal", _signal()),
        instrument_type=kwargs.pop("instrument_type", "stock"),
        stop_reference_price=kwargs.pop("stop_reference_price", 90.0),
        reward_risk_ratio=kwargs.pop("reward_risk_ratio", 2.0),
        entry_window_days=kwargs.pop("entry_window_days", 3),
        max_holding_days=kwargs.pop("max_holding_days", 3),
        **kwargs,
    )


def _bars(rows):
    return pl.DataFrame([
        {"symbol": "2330.TWSE", "date": day, "open": opening, "high": high,
         "low": low, "close": close}
        for day, opening, high, low, close in rows
    ]).with_columns(pl.col("date").cast(pl.Date))


def _evaluate(plan, rows, *, sessions=None, **kwargs):
    bars = _bars(rows)
    days = sessions or bars["date"].to_list()
    return evaluate_trade_plan(
        plan,
        bars=bars,
        sessions=days,
        evaluated_as_of=kwargs.pop("evaluated_as_of", days[-1]),
        **kwargs,
    )


def test_plan_prices_use_stock_and_etf_tick_classes_at_boundaries():
    signal = _signal(
        price=100.24,
        reference_high=100.24,
        entry_zone_low=49.99,
        entry_zone_high=100.24,
    )
    stock = _plan(signal=signal, stop_reference_price=99.74)
    etf = _plan(signal=signal.model_copy(update={"symbol": "0050.TWSE"}),
                instrument_type="etf", stop_reference_price=99.74)
    # 99.74 is still in the stock's sub-100 0.10 tier; the derived target then
    # crosses into the 0.50 tier.  ETF prices stay on their 0.05 tier.
    assert (stock.planned_entry_price, stock.stop_price, stock.target_price) == (100.0, 99.7, 100.5)
    assert (etf.planned_entry_price, etf.stop_price, etf.target_price) == (100.25, 99.75, 101.25)
    assert stock.entry_zone_low == 50.0
    assert etf.entry_zone_low == 49.99


def test_hashes_are_canonical_stable_and_symbol_specific():
    first = _plan()
    again = _plan()
    other_symbol = _plan(signal=_signal("2454.TWSE"))
    assert first.plan_identity == again.plan_identity == other_symbol.plan_identity
    assert first.plan_instance_id == again.plan_instance_id
    assert first.plan_instance_id != other_symbol.plan_instance_id
    assert first.plan_identity == canonical_hash({
        "rule_version": TRADE_PLAN_VERSION,
        "strategy_id": "quant_pullback",
        "entry_semantics": "pullback_limit",
        "fill_semantics": "first_low_lte_limit_fill_min_open_limit",
        "stop_method": "lookback_low",
        "stop_lookback": 10,
        "reward_risk_ratio": 2.0,
        "entry_window_days": 3,
        "max_holding_days": 3,
        "instrument_type": "stock",
        "price_adjustment_semantics": PRICE_ADJUSTMENT_SEMANTICS,
        "cost_assumption": "gross",
    })
    assert first.price_adjustment_semantics == PRICE_ADJUSTMENT_SEMANTICS
    assert first.evidence_as_of == EVIDENCE


@pytest.mark.parametrize(
    "signal_updates,plan_updates",
    [
        ({"data_as_of": "2026-09-02"}, {}),
        ({"price": 95.1}, {}),
        ({"reference_high": 101.0}, {}),
        ({"entry_zone_low": 93.0, "entry_zone_high": 96.0}, {}),
        ({}, {"stop_reference_price": 91.0}),
    ],
)
def test_plan_instance_hash_changes_with_symbol_evidence_and_prices(
    signal_updates, plan_updates,
):
    baseline = _plan()
    changed = _plan(signal=_signal(**signal_updates), **plan_updates)
    assert changed.plan_identity == baseline.plan_identity
    assert changed.plan_instance_id != baseline.plan_instance_id


@pytest.mark.parametrize(
    "change",
    [
        {"stop_method": "signal_candle_low"},
        {"stop_lookback": 11},
        {"reward_risk_ratio": 3.0},
        {"entry_window_days": 4},
        {"max_holding_days": 4},
        {"price_adjustment_semantics": "raw_unadjusted"},
    ],
)
def test_plan_identity_changes_with_required_definition_fields(change):
    assert _plan(**change).plan_identity != _plan().plan_identity


def test_breakout_definition_changes_entry_and_fill_identity():
    breakout = _signal(
        strategy_id="breakout_high",
        entry_zone_low=None,
        entry_zone_high=None,
        breakout_trigger=100.0,
    )
    plan = _plan(signal=breakout, entry_semantics="breakout_stop")
    assert plan.entry_semantics == "breakout_stop"
    assert plan.fill_semantics == "first_high_gte_trigger_fill_max_open_trigger"
    assert plan.plan_identity != _plan().plan_identity


def test_strategy_and_instrument_type_change_definition_identity():
    other_strategy = _plan(signal=_signal(strategy_id="another_pullback"))
    etf = _plan(signal=_signal("0050.TWSE"), instrument_type="etf")
    assert other_strategy.plan_identity != _plan().plan_identity
    assert etf.plan_identity != _plan().plan_identity


def test_pullback_limit_and_breakout_stop_use_exact_gap_fill_rules():
    pullback = _evaluate(
        _plan(max_holding_days=1),
        [(date(2026, 9, 2), 95.0, 99.0, 94.0, 96.0)],
    )
    assert pullback.entry_fill_price == 95.0
    assert pullback.exit_reason == "max_holding"

    pullback_at_limit = _evaluate(
        _plan(max_holding_days=1),
        [(date(2026, 9, 2), 98.0, 99.0, 96.0, 98.0)],
    )
    assert pullback_at_limit.entry_fill_price == 97.0

    breakout_signal = _signal(
        strategy_id="breakout_high",
        entry_zone_low=None,
        entry_zone_high=None,
        breakout_trigger=100.0,
    )
    breakout = _evaluate(
        _plan(signal=breakout_signal, entry_semantics="breakout_stop", max_holding_days=1),
        [(date(2026, 9, 2), 102.0, 104.0, 101.0, 103.0)],
    )
    assert breakout.entry_fill_price == 102.0
    assert breakout.exit_fill_price == 103.0

    breakout_at_trigger = _evaluate(
        _plan(signal=breakout_signal, entry_semantics="breakout_stop", max_holding_days=1),
        [(date(2026, 9, 2), 99.0, 101.0, 98.0, 100.0)],
    )
    assert breakout_at_trigger.entry_fill_price == 100.0


def test_stop_and_target_gap_fills_and_intraday_levels():
    stop = _evaluate(
        _plan(),
        [
            (date(2026, 9, 2), 95.0, 99.0, 94.0, 96.0),
            (date(2026, 9, 3), 89.0, 91.0, 88.0, 90.0),
        ],
    )
    assert (stop.exit_reason, stop.exit_fill_price) == ("stop", 89.0)

    stop_at_level = _evaluate(
        _plan(),
        [
            (date(2026, 9, 2), 95.0, 99.0, 94.0, 96.0),
            (date(2026, 9, 3), 92.0, 93.0, 89.0, 90.0),
        ],
    )
    assert stop_at_level.exit_fill_price == 90.0

    target = _evaluate(
        _plan(),
        [
            (date(2026, 9, 2), 95.0, 99.0, 94.0, 96.0),
            (date(2026, 9, 3), 112.0, 113.0, 110.0, 112.0),
        ],
    )
    assert (target.exit_reason, target.exit_fill_price) == ("target", 112.0)

    target_at_level = _evaluate(
        _plan(),
        [
            (date(2026, 9, 2), 95.0, 99.0, 94.0, 96.0),
            (date(2026, 9, 3), 100.0, 112.0, 99.0, 110.0),
        ],
    )
    assert target_at_level.exit_fill_price == 111.0


def test_entry_window_and_max_holding_are_session_based():
    plan = _plan(entry_window_days=2, max_holding_days=2)
    first = date(2026, 9, 2)
    immature = _evaluate(plan, [(first, 99.0, 100.0, 98.0, 99.0)])
    assert immature.status == "immature"
    not_triggered = _evaluate(plan, [
        (first, 99.0, 100.0, 98.0, 99.0),
        (date(2026, 9, 4), 99.0, 100.0, 98.0, 99.0),
    ])
    assert not_triggered.status == "not_triggered"

    expired = _evaluate(plan, [
        (first, 95.0, 99.0, 94.0, 96.0),
        (date(2026, 9, 4), 96.0, 100.0, 95.0, 98.0),
    ])
    assert (expired.status, expired.exit_session, expired.exit_fill_price) == (
        "triggered", date(2026, 9, 4), 98.0,
    )


def test_daily_bar_ambiguity_rules_fail_closed():
    plan = _plan()
    at_open_both = _evaluate(
        plan, [(date(2026, 9, 2), 95.0, 112.0, 89.0, 100.0)]
    )
    assert at_open_both.status == "undeterminable"

    intraday_any = _evaluate(
        plan, [(date(2026, 9, 2), 98.0, 100.0, 89.0, 96.0)]
    )
    assert intraday_any.status == "undeterminable"

    after_entry_both = _evaluate(plan, [
        (date(2026, 9, 2), 95.0, 99.0, 94.0, 96.0),
        (date(2026, 9, 3), 100.0, 112.0, 89.0, 100.0),
    ])
    assert after_entry_both.status == "undeterminable"


def test_missing_session_and_unadjustable_action_are_data_insufficient():
    plan = _plan()
    day1, day2 = date(2026, 9, 2), date(2026, 9, 3)
    missing = _evaluate(
        plan,
        [(day1, 95.0, 99.0, 94.0, 96.0)],
        sessions=[day1, day2],
        evaluated_as_of=day2,
    )
    assert missing.status == "data_insufficient"

    uncovered = _evaluate(
        plan,
        [(day1, 95.0, 99.0, 94.0, 96.0)],
        company_action_coverage_verified=False,
    )
    assert (uncovered.status, uncovered.reason) == (
        "data_insufficient", "corporate_action_coverage",
    )

    action = CorporateActionEvent(
        symbol="2330.TWSE",
        exchange="TWSE",
        effective_date=day1,
        effective_at=event_market_open(day1),
        event_type="stock_dividend",
        previous_close=None,
        reference_price=None,
        factor=None,
        cash_dividend=None,
        free_share_ratio=None,
        reduction_ratio=None,
        source="TWT49U",
        source_url="https://example.test",
        retrieved_at=datetime(2026, 9, 1, 14, tzinfo=TAIPEI),
        status="data_insufficient",
        reason="missing_factor",
    )
    outcome = _evaluate(
        plan,
        [(day1, 48.0, 49.0, 47.0, 48.0)],
        company_actions=[action],
    )
    assert outcome.status == "data_insufficient"


def test_verified_company_action_normalizes_plan_and_prices():
    plan = _plan(max_holding_days=1)
    day = date(2026, 9, 2)
    action = CorporateActionEvent(
        symbol="2330.TWSE",
        exchange="TWSE",
        effective_date=day,
        effective_at=event_market_open(day),
        event_type="stock_dividend",
        previous_close=100.0,
        reference_price=50.0,
        factor=0.5,
        cash_dividend=0.0,
        free_share_ratio=1.0,
        reduction_ratio=None,
        source="TWT49U",
        source_url="https://example.test",
        retrieved_at=datetime(2026, 9, 1, 14, tzinfo=TAIPEI),
        status="verified",
        precision_method="official_reference_price",
    )
    outcome = _evaluate(
        plan,
        [(day, 48.0, 49.0, 47.0, 48.5)],
        company_actions=[action],
    )
    assert outcome.status == "triggered"
    assert outcome.entry_fill_price == 48.0
    assert outcome.exit_fill_price == 48.5
    assert outcome.price_adjustment_semantics == PRICE_ADJUSTMENT_SEMANTICS


def test_raw_corporate_action_window_adjusts_once_and_rejects_preadjusted_input():
    plan = _plan(max_holding_days=2)
    entry_day, expiry_day = date(2026, 9, 2), date(2026, 9, 3)
    action = CorporateActionEvent(
        symbol="2330.TWSE",
        exchange="TWSE",
        effective_date=expiry_day,
        effective_at=event_market_open(expiry_day),
        event_type="stock_dividend",
        previous_close=100.0,
        reference_price=50.0,
        factor=0.5,
        cash_dividend=0.0,
        free_share_ratio=1.0,
        reduction_ratio=None,
        source="TWT49U",
        source_url="https://example.test",
        retrieved_at=datetime(2026, 9, 2, 14, tzinfo=TAIPEI),
        status="verified",
        precision_method="official_reference_price",
    )
    raw = _bars([
        (entry_day, 95.0, 99.0, 94.0, 96.0),
        (expiry_day, 48.0, 50.0, 47.0, 49.0),
    ])
    outcome = evaluate_trade_plan(
        plan,
        bars=raw,
        sessions=[entry_day, expiry_day],
        evaluated_as_of=expiry_day,
        company_actions=[action],
    )
    assert (outcome.status, outcome.entry_fill_price) == ("triggered", 47.5)
    assert (outcome.exit_reason, outcome.exit_fill_price) == ("max_holding", 49.0)

    preadjusted = adjust_prices_as_of(
        raw,
        as_of=expiry_day,
        events=[action],
        price_columns=("open", "high", "low", "close"),
    ).to_frame()
    rejected = evaluate_trade_plan(
        plan,
        bars=preadjusted,
        sessions=[entry_day, expiry_day],
        evaluated_as_of=expiry_day,
        company_actions=[action],
    )
    assert (rejected.status, rejected.reason) == (
        "data_insufficient", "daily_or_adjustment_data",
    )


def test_terminal_outcome_ignores_later_missing_bar_but_required_bar_still_fails():
    entry_day, missing_day = date(2026, 9, 2), date(2026, 9, 3)
    bars = _bars([(entry_day, 95.0, 99.0, 94.0, 96.0)])
    completed = evaluate_trade_plan(
        _plan(max_holding_days=1),
        bars=bars,
        sessions=[entry_day],
        evaluated_as_of=entry_day,
    )
    advanced = evaluate_trade_plan(
        _plan(max_holding_days=1),
        bars=bars,
        sessions=[entry_day, missing_day],
        evaluated_as_of=missing_day,
    )
    comparable = {"entry_session", "entry_fill_price", "entry_at_open", "exit_session",
                  "exit_fill_price", "exit_reason", "gross_return_pct", "status", "reason"}
    assert advanced.model_dump(include=comparable) == completed.model_dump(include=comparable)

    required = evaluate_trade_plan(
        _plan(max_holding_days=2),
        bars=bars,
        sessions=[entry_day, missing_day],
        evaluated_as_of=missing_day,
    )
    assert (required.status, required.reason) == (
        "data_insufficient", "daily_or_adjustment_data",
    )


def test_evidence_as_of_is_strict_session_boundary():
    plan = _plan()
    with pytest.raises(ValueError, match="after evidence_as_of"):
        evaluate_trade_plan(
            plan,
            bars=_bars([(EVIDENCE, 95.0, 99.0, 94.0, 96.0)]),
            sessions=[EVIDENCE],
            evaluated_as_of=EVIDENCE,
        )


def test_store_evaluator_fails_closed_on_unresolved_session_evidence(tmp_path):
    evaluator = TradePlanEvaluator(
        daily_store=TaiwanDailyStore(tmp_path / "daily"),
        calendar=TaiwanTradingCalendar(),
        action_store=CorporateActionStore(tmp_path / "actions"),
    )
    outcome = evaluator.evaluate(_plan(), evaluated_as_of=date(2026, 9, 2))
    assert outcome.status == "data_insufficient"
    assert outcome.reason == "trading_session_evidence"


def test_store_evaluator_preserves_terminal_result_before_later_evidence_gap(tmp_path):
    entry_day, later_day = date(2026, 9, 2), date(2026, 9, 3)

    def store_at(path, *, include_later):
        store = TaiwanDailyStore(path)
        days = [entry_day, later_day] if include_later else [entry_day]
        store.write_batch(pl.DataFrame([
            {
                "symbol": "2330.TWSE", "date": day, "open": 95.0,
                "high": 99.0, "low": 94.0, "close": 96.0,
                "volume": 1_000_000.0, "amount": 96_000_000.0, "quote_ts": 0,
            }
            for day in days
        ]))
        return store

    action_store = SimpleNamespace(
        read_verified_coverage=lambda: (EVIDENCE, entry_day, ()),
    )
    plan = _plan(max_holding_days=1)
    unresolved = TradePlanEvaluator(
        daily_store=store_at(tmp_path / "unresolved", include_later=False),
        calendar=TaiwanTradingCalendar(known_trading_days={entry_day}),
        action_store=action_store,
    ).evaluate(plan, evaluated_as_of=later_day)
    assert (unresolved.status, unresolved.exit_session) == ("triggered", entry_day)

    later_coverage_gap = TradePlanEvaluator(
        daily_store=store_at(tmp_path / "coverage_gap", include_later=True),
        calendar=TaiwanTradingCalendar(known_trading_days={entry_day, later_day}),
        action_store=action_store,
    ).evaluate(plan, evaluated_as_of=later_day)
    assert (later_coverage_gap.status, later_coverage_gap.exit_session) == (
        "triggered", entry_day,
    )
