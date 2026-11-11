"""One definition per indicator: the full pipeline and the intraday incremental path must agree."""
from __future__ import annotations

import random
from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators import pipeline
from app.repository import DataStore, KlineRepository

SYMBOL = "2330.TWSE"
FIRST_DAY = date(2026, 1, 1)


def _bars(count: int, *, flat_from: int | None = None, flat_len: int = 0) -> pl.DataFrame:
    rng = random.Random(7)
    rows = []
    close = 100.0
    for i in range(count):
        flat = flat_from is not None and flat_from <= i < flat_from + flat_len
        if not flat:
            close = round(max(5.0, close * (1 + rng.uniform(-0.03, 0.03))), 2)
        spread = 0.0 if flat else round(close * rng.uniform(0.0, 0.02), 2)
        rows.append({
            "symbol": SYMBOL,
            "date": FIRST_DAY + timedelta(days=i),
            "open": close,
            "high": round(close + spread, 2),
            "low": round(close - spread, 2),
            "close": close,
            "raw_close": close,
            "raw_high": round(close + spread, 2),
            "raw_low": round(close - spread, 2),
            "volume": 1000.0 + rng.randint(0, 500),
            "amount": 1.0e6,
            "consecutive_limit_ups": 0,
            "consecutive_limit_downs": 0,
        })
    return pl.DataFrame(rows).with_columns(
        pl.col("consecutive_limit_ups").cast(pl.UInt32),
        pl.col("consecutive_limit_downs").cast(pl.UInt32),
    )


COMPARED = [
    "ma5", "ma10", "ma20", "ma30", "ma60",
    "ema5", "ema10", "ema20", "ema30", "ema60",
    "macd_dif", "macd_dea", "macd_hist", "boll_upper", "boll_lower",
    "kdj_k", "kdj_d", "kdj_j", "atr_14", "vol_ma5", "vol_ma10", "vol_ratio_5d",
    "high_60d", "low_60d", "momentum_5d", "momentum_10d", "momentum_20d",
    "momentum_30d", "momentum_60d", "annual_vol_20d", "rsi_6", "rsi_14", "rsi_24",
    "change_pct",
]
SIGNALS = list(pipeline.SIGNAL_DEPENDENCIES)


def _full_and_incremental(tmp_path, bars: pl.DataFrame):
    history, today = bars.head(-1), bars.tail(1)
    full = pipeline.compute_signals(pipeline.compute_indicators(bars))

    repo = KlineRepository(DataStore(tmp_path))
    repo._enriched_history_cache = pipeline.compute_signals(pipeline.compute_indicators(history))
    repo._build_live_agg(history["date"].max())
    live_agg = repo._live_agg_cache
    assert live_agg is not None and not live_agg.is_empty()

    prev = repo._enriched_history_cache.filter(pl.col("date") == history["date"].max())
    inc = pipeline.compute_enriched_today(
        live_agg, prev, today.select("symbol", "date", "open", "high", "low", "close", "volume", "amount"),
    )
    return full.tail(1).row(0, named=True), inc.row(0, named=True)


def test_incremental_row_matches_full_pipeline_for_every_shared_indicator(tmp_path):
    full, inc = _full_and_incremental(tmp_path, _bars(160))
    for column in COMPARED:
        assert full[column] is not None, column
        assert inc[column] == pytest.approx(full[column], rel=1e-9, abs=1e-9), column
    for column in SIGNALS:
        assert inc[column] == full[column], column


def test_flat_stretch_never_turns_kdj_into_nan(tmp_path):
    bars = _bars(160, flat_from=140, flat_len=19)
    full = pipeline.compute_indicators(bars)
    kd = full.filter(pl.col("date") >= FIRST_DAY + timedelta(days=148))
    assert kd["kdj_k"].is_nan().sum() == 0 and kd["kdj_k"].null_count() == 0

    full_row, inc_row = _full_and_incremental(tmp_path, bars)
    for column in ("kdj_k", "kdj_d", "kdj_j"):
        assert inc_row[column] == pytest.approx(full_row[column], rel=1e-9, abs=1e-9)


def test_short_history_nulls_indicators_without_enough_bars():
    state = pl.DataFrame({
        "symbol": [SYMBOL], "ema5": [10.0], "ema10": [10.0], "ema20": [10.0], "ema30": [10.0],
        "ema60": [10.0], "macd_dea": [0.0], "kdj_k": [50.0], "kdj_d": [50.0], "atr_14": [0.2],
        "close": [10.0], "high": [10.1], "low": [9.9], "annual_vol_20d": [0.1],
        "_ema12": [10.0], "_ema26": [10.0], "_adj_factor": [1.0],
        "_vol_19d_pct_sum": [0.0], "_vol_19d_pct_sq_sum": [0.0],
        "_ma5_partial_sum": [40.0], "_ma10_partial_sum": [90.0], "_ma20_partial_sum": [190.0],
        "_ma30_partial_sum": [290.0], "_ma60_partial_sum": [590.0],
        "_boll_partial_sum": [190.0], "_boll_partial_sq_sum": [1900.0],
        "_high_59d": [10.1], "_low_59d": [9.9],
        "_close_5d_ago": [10.0], "_close_10d_ago": [10.0], "_close_20d_ago": [10.0],
        "_close_30d_ago": [10.0], "_close_60d_ago": [10.0],
        "_vol_ma5_partial_sum": [4000.0], "_vol_ma10_partial_sum": [9000.0], "_vol_ma5_prev_sum": [5000.0],
        "_kdj_8d_low": [9.9], "_kdj_8d_high": [10.1],
        "_window_len": [30],
        "_rsi_avg_gain_6": [0.01], "_rsi_avg_loss_6": [0.01], "_rsi_avg_gain_14": [0.01],
        "_rsi_avg_loss_14": [0.01], "_rsi_avg_gain_24": [0.01], "_rsi_avg_loss_24": [0.01],
    })
    today = pl.DataFrame({
        "symbol": [SYMBOL], "date": [date(2026, 7, 30)], "open": [10.1], "high": [10.2],
        "low": [10.0], "close": [10.2], "volume": [1200.0], "amount": [12240.0],
    })
    row = pipeline.compute_enriched_today(state, pl.DataFrame(), today).row(0, named=True)

    assert row["ma20"] is not None and row["momentum_20d"] is not None  # 30 bars cover these
    assert row["ma60"] is None and row["high_60d"] is None and row["momentum_60d"] is None
    assert row["macd_dif"] is None  # 30 earlier bars + today = 31 < 35 -> still warming up
    assert row["rsi_14"] is not None


def test_ex_date_limit_reference_ignores_later_ex_rights_multiplier():
    """Day3 ex-date (x2) and a later ex-date on day5 (x1.5): the day3 reference is 10 / 2 = 5.0.

    A raw close of 5.20 is below the 5.50 limit-up. Using the fully adjusted previous close
    (10 / 3 = 3.33) as the reference would call it limit-up.
    """
    days = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
    raw = pl.DataFrame({
        "symbol": [SYMBOL] * 5,
        "date": days,
        "open": [10.0, 10.0, 5.2, 5.2, 3.5],
        "high": [10.0, 10.0, 5.2, 5.2, 3.5],
        "low": [10.0, 10.0, 5.2, 5.2, 3.5],
        "close": [10.0, 10.0, 5.2, 5.2, 3.5],
        "volume": [10000.0] * 5,
        "amount": [1.0e7] * 5,
    })
    factors = pl.DataFrame({
        "symbol": [SYMBOL] * 2,
        "trade_date": [days[2], days[4]],
        "ex_factor": [2.0, 1.5],
    })
    instruments = pl.DataFrame({
        "symbol": [SYMBOL], "asset_type": ["stock"], "name": ["普通股"], "float_shares": [1.0e8],
    })

    df = pipeline.compute_enriched(raw, factors=factors, instruments=instruments)

    ex_day = df.filter(pl.col("date") == days[2])
    assert ex_day["signal_limit_up"][0] is False
