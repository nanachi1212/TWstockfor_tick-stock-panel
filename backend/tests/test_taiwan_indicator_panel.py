"""Indicator panel: full time series, one formula, honest warm-up."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.technical_indicators import (
    MIN_BARS_MACD,
    MIN_BARS_RSI_14,
    PANEL_INDICATOR_COLS,
    compute_taiwan_daily_indicators,
    compute_taiwan_indicator_panel,
    wilder_rsi_expr,
)


def _history(n: int, symbol: str = "2330.TWSE", start: float = 100.0) -> pl.DataFrame:
    closes = [start + (i % 7) - (i % 3) * 0.5 for i in range(n)]
    return pl.DataFrame({
        "symbol": [symbol] * n,
        "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "close": closes,
        "volume": [1000.0 + i for i in range(n)],
    })


def test_panel_returns_the_whole_series_not_just_latest() -> None:
    history = _history(80)
    panel = compute_taiwan_indicator_panel(history)
    assert panel.height == 80
    assert panel.columns == ["symbol", "date", *PANEL_INDICATOR_COLS, "price_semantics"]
    assert panel["date"].to_list() == history["date"].to_list()


def test_latest_wrapper_is_the_panels_last_row() -> None:
    history = _history(80)
    panel = compute_taiwan_indicator_panel(history)
    latest = compute_taiwan_daily_indicators(history)

    assert latest.height == 1
    last = panel.sort("date").tail(1).to_dicts()[0]
    row = latest.to_dicts()[0]
    for col in PANEL_INDICATOR_COLS:
        assert row[col] == pytest.approx(last[col]), col


def test_warmup_is_per_row_not_per_symbol_total() -> None:
    """A long history must not retro-validate its own early rows."""
    panel = compute_taiwan_indicator_panel(_history(80)).sort("date")
    rsi = panel["rsi_14"].to_list()
    macd = panel["macd_dif"].to_list()

    assert all(v is None for v in rsi[: MIN_BARS_RSI_14 - 1]), "early RSI must be null"
    assert rsi[MIN_BARS_RSI_14 - 1] is not None
    assert all(v is None for v in macd[: MIN_BARS_MACD - 1]), "early MACD must be null"
    assert macd[MIN_BARS_MACD - 1] is not None


def test_insufficient_history_is_null_never_zero() -> None:
    panel = compute_taiwan_indicator_panel(_history(12)).sort("date")
    for col in ("ma20", "ma60", "rsi_14", "macd_dif", "macd_dea", "macd_hist"):
        values = panel[col].to_list()
        assert all(v is None for v in values), col
        assert 0.0 not in [v for v in values if v is not None]
    assert panel["ma5"].to_list()[-1] is not None


def test_rsi_has_exactly_one_definition() -> None:
    history = _history(60)
    panel = compute_taiwan_indicator_panel(history).sort("date")
    direct = (
        history.sort(["symbol", "date"])
        .with_columns(wilder_rsi_expr().alias("rsi_14"))["rsi_14"].to_list()[-1]
    )
    assert panel["rsi_14"].to_list()[-1] == pytest.approx(direct)


def test_price_col_is_explicit_and_semantics_are_recorded() -> None:
    history = _history(60).with_columns(
        (pl.col("close") * 0.9).alias("adj_close")
    )
    raw = compute_taiwan_indicator_panel(history, price_col="close")
    adjusted = compute_taiwan_indicator_panel(
        history, price_col="adj_close", price_semantics="adjusted")

    assert set(raw["price_semantics"].to_list()) == {"raw"}
    assert set(adjusted["price_semantics"].to_list()) == {"adjusted"}
    # a constant 0.9 scale leaves ratio indicators identical …
    assert adjusted["rsi_14"].to_list()[-1] == pytest.approx(raw["rsi_14"].to_list()[-1])
    assert adjusted["momentum_5d"].to_list()[-1] == pytest.approx(
        raw["momentum_5d"].to_list()[-1])
    # … but level indicators move
    assert adjusted["ma20"].to_list()[-1] == pytest.approx(raw["ma20"].to_list()[-1] * 0.9)


def test_unknown_price_semantics_is_rejected() -> None:
    with pytest.raises(ValueError, match="price_semantics"):
        compute_taiwan_indicator_panel(_history(30), price_semantics="forward_adjusted")


def test_missing_column_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing column"):
        compute_taiwan_indicator_panel(_history(30), price_col="adj_close")


def test_multi_symbol_series_do_not_bleed_into_each_other() -> None:
    history = pl.concat([_history(40, "2330.TWSE"), _history(18, "8069.TPEX", 50.0)])
    panel = compute_taiwan_indicator_panel(history)

    tsmc = panel.filter(pl.col("symbol") == "2330.TWSE").sort("date")
    eink = panel.filter(pl.col("symbol") == "8069.TPEX").sort("date")
    assert tsmc.height == 40 and eink.height == 18
    assert tsmc["macd_dif"].to_list()[-1] is not None
    assert eink["macd_dif"].to_list()[-1] is None, "18 bars < MIN_BARS_MACD"
    assert eink["rsi_14"].to_list()[-1] is not None


def test_empty_input_returns_typed_empty_panel() -> None:
    panel = compute_taiwan_indicator_panel(pl.DataFrame())
    assert panel.is_empty()
    assert "price_semantics" in panel.columns
