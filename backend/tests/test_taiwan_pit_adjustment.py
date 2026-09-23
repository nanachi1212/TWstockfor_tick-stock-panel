"""Feature, realized label and volume boundaries use the same canonical events."""
from dataclasses import replace
from datetime import date, datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.taiwan.adjust import (
    adjust_prices_as_of,
    adjust_prices_for_presentation,
    forward_adjusted_return,
    volume_window_status,
    window_crosses_share_count_event,
)
from app.taiwan.corporate_actions import (
    CorporateActionEvent,
    CorporateActionStore,
    event_market_open,
)
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.feature_manifest import (
    DatasetCapability,
    FeatureManifest,
    FeatureSpec,
    training_matrix,
)
from app.taiwan.technical_indicators import compute_taiwan_indicator_panel

D = date(2018, 1, 2)
SYMBOL = "2330.TWSE"


def history(n=5):
    return pl.DataFrame({"symbol": [SYMBOL] * n, "date": [D + timedelta(days=i) for i in range(n)],
                         "open": [100.0] * n, "high": [110.0] * n, "low": [90.0] * n,
                         "close": [100.0] * n, "volume": [1000.0] * n, "amount": [100000.0] * n})


def event(day=D + timedelta(days=1), kind="cash_dividend", factor=.9):
    return CorporateActionEvent(
        symbol=SYMBOL, exchange="TWSE", effective_date=day, effective_at=event_market_open(day),
        event_type=kind, previous_close=100., reference_price=100 * factor, factor=factor,
        cash_dividend=10. if kind == "cash_dividend" else None,
        free_share_ratio=1. if kind == "stock_dividend" else None, reduction_ratio=None,
        source="TWTAUU" if kind == "capital_reduction" else "TWT49U",
        source_url="https://www.twse.com.tw/", retrieved_at=datetime(2026, 9, 23, tzinfo=TAIPEI),
        status="verified", precision_method="official_reference_ratio")


def test_future_events_do_not_change_frozen_historical_output(tmp_path):
    store = CorporateActionStore(tmp_path)
    store.save([event()])
    h = history()
    before = adjust_prices_as_of(h, as_of=D + timedelta(days=4), events=store.read())
    store.save([event(date(2024, 1, 2)), event(date(2026, 1, 2), "par_change", .1)])
    after = adjust_prices_as_of(h, as_of=D + timedelta(days=4), events=store.read())
    assert before.status == after.status == "verified"
    assert_frame_equal(before.to_frame(), after.to_frame())
    assert before.to_frame()["close"].to_list() == [90., 100., 100., 100., 100.]


def test_ohlcv_input_and_daily_store_are_immutable(tmp_path):
    from app.taiwan.daily_store import TaiwanDailyStore
    h = history().with_columns(pl.lit(0).alias("quote_ts"))
    raw = h.clone()
    store = TaiwanDailyStore(tmp_path)
    store.write_batch(h)
    hashes = {p: p.read_bytes() for p in tmp_path.glob("date=*/part.parquet")}
    result = adjust_prices_as_of(h, as_of=D + timedelta(days=4), events=[event()])
    assert_frame_equal(h, raw)
    for p, content in hashes.items():
        assert p.read_bytes() == content
    output = result.to_frame()
    assert_frame_equal(output.select("volume", "amount"), h.select("volume", "amount"))
    assert output["open"][0] == 90
    assert output["high"][0] == 99
    assert output["low"][0] == 81


@pytest.mark.parametrize("kind,factor", [("cash_dividend", .9), ("stock_dividend", .5),
                                         ("capital_reduction", 2), ("par_change", .1)])
def test_forward_event_types_use_normalized_price_outcome(kind, factor):
    result = forward_adjusted_return(history(), start_session=D, horizon_sessions=2,
                                      events=[event(kind=kind, factor=factor)])
    assert result.status == "verified"
    assert result.value == pytest.approx(1 / factor - 1)
    assert result.end_session == D + timedelta(days=2)


@pytest.mark.parametrize("offset,expected", [(0, 0), (1, 1 / .9 - 1), (2, 1 / .9 - 1), (3, 0)])
def test_forward_boundaries_start_exclusive_end_inclusive(offset, expected):
    result = forward_adjusted_return(history(), start_session=D, horizon_sessions=2,
                                      events=[event(D + timedelta(days=offset))])
    assert result.value == pytest.approx(expected)


def test_forward_no_event_and_insufficient_horizon():
    assert forward_adjusted_return(history(), start_session=D, horizon_sessions=2, events=[]).value == 0
    result = forward_adjusted_return(history(), start_session=D, horizon_sessions=6, events=[])
    assert result.value is None
    assert result.status == "data_insufficient"


def test_same_future_event_is_excluded_from_feature_but_included_in_label():
    h, actions = history(), [event()]
    feature = adjust_prices_as_of(h, as_of=D, events=actions)
    assert feature.to_frame()["close"].to_list() == [100.]
    label = forward_adjusted_return(h, start_session=D, horizon_sessions=2, events=actions)
    assert label.value == pytest.approx(1 / .9 - 1)


def test_exact_effective_time_and_no_future_daily_bar():
    action = event()
    before = adjust_prices_as_of(history(), as_of=action.effective_at - timedelta(seconds=1), events=[action])
    at = adjust_prices_as_of(history(), as_of=action.effective_at, events=[action])
    assert before.to_frame()["close"].to_list() == [100.]
    assert at.to_frame()["close"].to_list() == [90.]
    assert at.to_frame()["date"].to_list() == [D]  # ex-date close has not occurred


def test_future_invalid_event_is_ignored_but_inside_window_fails_closed():
    bad = replace(event(), factor=None, status="data_insufficient", reason="missing_reference")
    assert adjust_prices_as_of(history(), as_of=D, events=[bad]).status == "verified"
    result = adjust_prices_as_of(history(), as_of=D + timedelta(days=3), events=[bad])
    assert result.status == "data_insufficient"
    assert result.to_frame()["close"][0] is None


def test_duplicate_conflicts_fail_closed_in_adjustment():
    result = adjust_prices_as_of(history(), as_of=D + timedelta(days=2), events=[event(), event(factor=.8)])
    assert result.status == "data_insufficient"
    assert result.to_frame()["close"][0] is None


def test_multi_symbol_factors_do_not_bleed():
    h = pl.concat([history(), history().with_columns(pl.lit("8069.TPEX").alias("symbol"))])
    result = adjust_prices_as_of(h, as_of=D + timedelta(days=4), events=[event()]).to_frame()
    assert result.filter(pl.col("symbol") == "8069.TPEX")["close"].to_list() == [100.] * 5


def _manifest():
    return FeatureManifest("test", (FeatureSpec("close", "corporate_action", "price"),))


def _capabilities():
    return {"corporate_action": DatasetCapability("corporate_action", availability_policy="market_mechanism_inferred")}


def test_presentation_wrapper_and_export_are_both_rejected_by_training():
    display = adjust_prices_for_presentation(history(), as_of=date(2026, 1, 1), events=[event()])
    assert display.usage_scope == "presentation_only"
    for frame in (display, display.to_frame()):
        with pytest.raises(ValueError, match="presentation_only"):
            training_matrix(frame, _manifest(), _capabilities())
        with pytest.raises(ValueError, match="presentation_only"):
            compute_taiwan_indicator_panel(frame)


def test_pit_window_is_training_safe_only_for_its_own_anchor():
    anchor = D + timedelta(days=4)
    frame = adjust_prices_as_of(history(), as_of=anchor, events=[event()]).to_frame()
    with pytest.raises(ValueError, match="own as_of"):
        training_matrix(frame, _manifest(), _capabilities())
    trained, _ = training_matrix(frame.filter(pl.col("date") == anchor), _manifest(), _capabilities())
    assert trained["close"].to_list() == [100.]
    assert trained["usage_scope"].to_list() == ["pit_feature"]


@pytest.mark.parametrize("kind", ["stock_dividend", "capital_reduction", "par_change"])
def test_volume_boundary_and_amount_exception(kind):
    actions = [event(kind=kind)]
    args = {"symbol": SYMBOL, "start_session": D, "end_session": D + timedelta(days=2)}
    assert window_crosses_share_count_event(actions, **args)
    for measure in ("volume", "relative_volume", "volume_ma", "volume_momentum"):
        assert volume_window_status(actions, **args, measure=measure) == "data_insufficient"
    for measure in ("amount", "ADV20_TWD"):
        assert volume_window_status(actions, **args, measure=measure) == "verified"
    assert not window_crosses_share_count_event([event(D, kind)], **args)


def test_cash_dividend_does_not_block_share_volume():
    assert not window_crosses_share_count_event([event()], symbol=SYMBOL,
                                               start_session=D, end_session=D + timedelta(days=2))


def test_indicator_volume_ma_is_unavailable_until_window_is_after_event():
    h = history(20)
    frame = adjust_prices_as_of(h, as_of=D + timedelta(days=19),
                                events=[event(D + timedelta(days=10), "stock_dividend", .5)]).to_frame()
    panel = compute_taiwan_indicator_panel(frame)
    assert panel["vol_ma5"][9] == 1000
    assert panel["vol_ma5"][10] is None
    assert panel["vol_ma5_status"][10] == "data_insufficient"
    assert panel["vol_ma5"][14] == 1000
    assert panel["usage_scope"][0] == "pit_feature"


@pytest.mark.parametrize("column", ["volume", "amount", "unknown"])
def test_price_api_does_not_adjust_non_price_columns(column):
    with pytest.raises(ValueError, match="OHLC"):
        adjust_prices_as_of(history(), as_of=D, events=[], price_columns=(column,))


def test_duplicate_history_and_naive_time_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        adjust_prices_as_of(pl.concat([history(), history()]), as_of=D, events=[])
    with pytest.raises(ValueError, match="timezone-aware"):
        adjust_prices_as_of(history(), as_of=datetime(2018, 1, 2), events=[])


def test_horizon_counts_supplied_sessions_not_calendar_days():
    h = history(3).with_columns(pl.Series("date", [D, D + timedelta(days=3), D + timedelta(days=6)]))
    result = forward_adjusted_return(h, start_session=D, horizon_sessions=2, events=[])
    assert result.end_session == D + timedelta(days=6)


def test_legacy_adjusted_panel_cannot_be_silently_admitted_to_training():
    panel = compute_taiwan_indicator_panel(history(), price_semantics="adjusted")
    with pytest.raises(ValueError, match="provenance"):
        training_matrix(panel, _manifest(), _capabilities())


def test_latest_indicator_wrapper_preserves_pit_provenance():
    from app.taiwan.technical_indicators import compute_taiwan_daily_indicators

    frame = adjust_prices_as_of(history(), as_of=D + timedelta(days=4), events=[]).to_frame()
    latest = compute_taiwan_daily_indicators(frame)
    assert latest["usage_scope"].to_list() == ["pit_feature"]
    assert latest["date"].to_list() == [D + timedelta(days=4)]


def test_presentation_is_rejected_by_regime_and_breadth():
    from app.taiwan.quant.regime import classify_market_regime, market_breadth_above_ma

    frame = adjust_prices_for_presentation(history(), as_of=D + timedelta(days=4), events=[]).to_frame()
    with pytest.raises(ValueError, match="presentation_only"):
        classify_market_regime(frame)
    with pytest.raises(ValueError, match="presentation_only"):
        market_breadth_above_ma(frame, D)


def test_indicator_window_must_not_mix_multiple_adjustment_anchors():
    frames = [adjust_prices_as_of(history(), as_of=D + timedelta(days=i), events=[]).to_frame()
              for i in (3, 4)]
    with pytest.raises(ValueError, match="anchor"):
        compute_taiwan_indicator_panel(pl.concat(frames))


def test_regime_as_of_excludes_future_price_bars():
    from app.taiwan.quant.regime import classify_market_regime

    raw = history(80).with_columns(
        pl.when(pl.col("date") > D + timedelta(days=59)).then(10000.)
        .otherwise(pl.col("close")).alias("close"))
    anchor = D + timedelta(days=59)
    frozen = classify_market_regime(raw.head(60), as_of=anchor)
    assert classify_market_regime(raw, as_of=anchor) == frozen


def test_regime_and_breadth_do_not_consume_insufficient_adjustment():
    from app.taiwan.quant.regime import classify_market_regime, market_breadth_above_ma

    anchor = D + timedelta(days=79)
    unresolved = replace(event(), status="data_insufficient", factor=None, reason="missing_reference")
    frame = adjust_prices_as_of(history(80), as_of=anchor, events=[unresolved]).to_frame()
    with pytest.raises(ValueError, match="data_insufficient"):
        classify_market_regime(frame, as_of=anchor)
    assert market_breadth_above_ma(frame.with_columns(pl.lit(90.).alias("ma20")), anchor) is None
