"""Taiwan market rules must survive shared enrichment and custom realtime routing."""
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import polars as pl
import pytest
from test_realtime_enriched_resume import _live_state, _previous_enriched

from app.indicators.pipeline import _compute_limit_signals_today, compute_limit_signals
from app.market_time import trading_minutes_elapsed_from_dt
from app.price_limits import polars_price_limit_pct
from app.taiwan.realtime.calendar import TAIPEI_TZ


def _bars(symbol, previous=10.0, current=11.0):
    return pl.DataFrame({
        "symbol": [symbol, symbol], "date": [date(2026, 6, 1), date(2026, 6, 2)],
        "open": [previous, current], "high": [previous, current], "low": [previous, current],
        "close": [previous, current], "raw_close": [previous, current],
        "raw_high": [previous, current], "raw_low": [previous, current], "volume": [1000.0, 1000.0],
    })


def _signals(symbol, metadata, engine="daily", previous=10.0, current=11.0):
    instruments = pl.DataFrame([{"symbol": symbol, **metadata}])
    if engine == "daily":
        result = compute_limit_signals(_bars(symbol, previous, current), instruments)
    else:
        today = _bars(symbol, previous, current).tail(1).with_columns(pl.lit(previous).alias("_prev_close_raw"))
        result = _compute_limit_signals_today(today, instruments)
    assert not any(column.startswith("_tw_") for column in result.columns)
    return result.row(-1, named=True)


@pytest.mark.parametrize("engine", ["daily", "incremental"])
@pytest.mark.parametrize("symbol", ["3010.TWSE", "3010.TPEX", "6880.TWSE", "2330.TWSE"])
def test_taiwan_ordinary_stock_does_not_enter_chinese_board_or_st_rules(symbol, engine):
    row = _signals(symbol, {"asset_type": "stock", "name": "TEST"}, engine)
    assert row["signal_limit_up"] is True
    assert row["consecutive_limit_ups"] == 1


@pytest.mark.parametrize("engine", ["daily", "incremental"])
@pytest.mark.parametrize("symbol,metadata,expected", [
    ("0050.TWSE", {"asset_type": "etf", "classification_source": "official_metadata", "underlying_scope": "domestic"}, True),
    ("00631L.TWSE", {"asset_type": "etf"}, False),
    ("00632R.TWSE", {"asset_type": "etf"}, True),
    ("00646.TWSE", {"asset_type": "etf", "classification_source": "official_metadata", "underlying_scope": "foreign"}, False),
    ("00720B.TWSE", {"asset_type": "etf", "classification_source": "cfi_code", "underlying_scope": "foreign", "etf_category": "bond"}, False),
    ("00999.TWSE", {"asset_type": "etf", "classification_source": "name_heuristic", "underlying_scope": "domestic"}, None),
    ("9999.TPEX", {}, None),
    ("2330.TWSE", {"asset_type": "unsupported"}, None),
    ("2330.TWSE", {"asset_type": "stock", "exchange": "TPEX"}, None),
])
def test_instrument_classification_and_unknowns_are_respected(symbol, metadata, expected, engine):
    row = _signals(symbol, metadata, engine)
    assert row["signal_limit_up"] is expected
    if expected is None:
        assert row["consecutive_limit_ups"] is None


def test_vector_unknown_market_and_missing_taiwan_classification_are_null():
    rows = pl.DataFrame({"symbol": ["3010.US", "3010", "9999.TWSE", "300001.SZ", None],
                         "date": [date(2026, 6, 2)] * 5})
    values = rows.select(polars_price_limit_pct(pl.col("symbol"), pl.col("date"), pl.lit(False)))
    assert values.to_series().to_list() == [None, None, None, 0.2, None]


@pytest.mark.parametrize("hour,minute,expected", [
    (8, 59, 0), (9, 0, 0), (9, 15, 15), (11, 30, 150), (12, 0, 180),
    (12, 30, 210), (13, 0, 240), (13, 30, 270), (15, 0, 270),
])
def test_elapsed_minutes_use_continuous_taiwan_session(hour, minute, expected):
    local = datetime(2026, 11, 4, hour, minute, tzinfo=TAIPEI_TZ)
    assert trading_minutes_elapsed_from_dt(local) == expected
    assert trading_minutes_elapsed_from_dt(local.astimezone(UTC)) == expected


def test_elapsed_minutes_normalize_timezone_and_preserve_seconds():
    assert trading_minutes_elapsed_from_dt(datetime(2026, 11, 4, 1, 0, 30, tzinfo=UTC)) == 0.5


def test_taiwan_stock_name_does_not_activate_historical_st_restrictions():
    result = compute_limit_signals(
        _bars("2330.TWSE", current=10.5),
        pl.DataFrame({"symbol": ["2330.TWSE"], "asset_type": ["stock"], "name": ["TEST"]}),
    )
    assert result["signal_limit_up"][-1] is False


@pytest.mark.parametrize("hour,minute,ratio", [(9, 15, 23.4), (12, 0, 1.95), (13, 30, 1.3)])
def test_custom_realtime_enrichment_uses_quote_timestamp_and_taiwan_session(monkeypatch, hour, minute, ratio):
    from app.services.quote_service import QuoteService

    symbol = "2330.TWSE"
    when = datetime(2026, 11, 4, hour, minute, tzinfo=TAIPEI_TZ)
    repo = MagicMock()
    repo.get_live_agg.return_value = _live_state().with_columns(pl.lit(symbol).alias("symbol"))
    repo.get_enriched_latest.return_value = (
        _previous_enriched().with_columns(pl.lit(symbol).alias("symbol")), date(2026, 11, 3),
    )
    repo.get_instruments.return_value = pl.DataFrame({"symbol": [symbol], "asset_type": ["stock"]})
    monkeypatch.setattr("app.indicators.pipeline.attach_deviation_columns_today", lambda frame, *args: frame)
    service = QuoteService.__new__(QuoteService)
    service._repo = repo
    service.get_index_quotes = lambda: pl.DataFrame()
    daily = pl.DataFrame({
        "symbol": [symbol], "date": [when.date()], "open": [10.0], "high": [10.0],
        "low": [10.0], "close": [10.0], "volume": [1300.0], "amount": [13000.0],
        "quote_ts": [int(when.timestamp() * 1000)],
    })
    service._flush_live_enriched(daily)
    repo.flush_live_enriched_asset.assert_called_once()
    kind, result = repo.flush_live_enriched_asset.call_args.args
    assert kind == "stock"
    assert result["vol_ratio_5d"][0] == pytest.approx(ratio)
    assert result["signal_volume_surge"][0] is (ratio >= 2)


@pytest.mark.parametrize("multiplier", [0.0, float("nan"), float("inf")])
def test_invalid_etf_multiplier_remains_unavailable(multiplier):
    row = _signals("00999.TWSE", {
        "asset_type": "etf", "classification_source": "official_metadata",
        "underlying_scope": "domestic", "leverage_multiplier": multiplier,
    })
    assert row["signal_limit_up"] is None


@pytest.mark.parametrize("engine", ["daily", "incremental"])
@pytest.mark.parametrize("asset_type,symbol,current,expected", [
    ("stock", "2330.TWSE", 50.8, True),
    ("etf", "0050.TWSE", 50.8, False),
    ("etf", "0050.TWSE", 50.85, True),
])
def test_taiwan_limits_use_class_specific_valid_ticks(engine, asset_type, symbol, current, expected):
    metadata = {"asset_type": asset_type, "classification_source": "official_metadata", "underlying_scope": "domestic"}
    row = _signals(symbol, metadata, engine, previous=46.25, current=current)
    assert row["signal_limit_up"] is expected


@pytest.mark.parametrize("engine", ["daily", "incremental"])
@pytest.mark.parametrize("as_of,expected", [(date(2026, 6, 2), True), (date(2026, 6, 1), False)])
def test_current_authoritative_limit_still_overrides_tick_estimate(engine, as_of, expected):
    row = _signals("2330.TWSE", {"asset_type": "stock", "limit_up": 50.0, "as_of": as_of},
                   engine, previous=46.25, current=50.0)
    assert row["signal_limit_up"] is expected


@pytest.mark.parametrize("previous,up,down", [
    (9.09, 9.99, 8.19), (9.10, 10.0, 8.19),
    (45.45, 49.95, 40.95), (45.46, 50.0, 40.95),
    (46.25, 50.8, 41.65), (90.99, 100.0, 81.9),
    (454.6, 500.0, 409.5), (909.1, 1000.0, 819.0),
])
def test_stock_vector_limit_prices_match_fixed_tier_boundaries(previous, up, down):
    from app.price_limits import polars_limit_price
    from app.taiwan.market_rules import PriceLimitModel, TickSizeClass

    frame = pl.DataFrame({"previous": [previous], "limit": [0.1], "tick_class": [TickSizeClass.ORDINARY_STOCK.value]})
    expected = {True: up, False: down}
    assert PriceLimitModel.calc_limits_for_pct(previous, 0.1) == pytest.approx((up, down))
    for direction, value in expected.items():
        got = frame.select(polars_limit_price(pl.col("previous"), pl.col("limit"),
                                             tick_class=pl.col("tick_class"), up=direction)).item()
        assert got == pytest.approx(value)


@pytest.mark.parametrize("previous,up,down", [
    (45.45, 49.99, 40.91), (45.46, 50.0, 40.92),
    (46.25, 50.85, 41.63), (90.99, 100.05, 81.9),
])
def test_etf_vector_limit_prices_match_fixed_tier_boundaries(previous, up, down):
    from app.price_limits import polars_limit_price
    from app.taiwan.market_rules import PriceLimitModel, TickSizeClass

    frame = pl.DataFrame({"previous": [previous], "limit": [0.1], "tick_class": [TickSizeClass.ETF.value]})
    assert PriceLimitModel.calc_limits_for_pct(previous, 0.1, TickSizeClass.ETF) == pytest.approx((up, down))
    for direction, value in {True: up, False: down}.items():
        got = frame.select(polars_limit_price(pl.col("previous"), pl.col("limit"),
                                             tick_class=pl.col("tick_class"), up=direction)).item()
        assert got == pytest.approx(value)
