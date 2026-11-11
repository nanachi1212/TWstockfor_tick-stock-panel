"""Taiwan 歷史流通股數: 缺少 point-in-time 股本分母時保持 fail-closed。"""
from __future__ import annotations

from datetime import date

import polars as pl


def apply_historical_float_shares(rows: pl.DataFrame, *, today: date) -> pl.DataFrame:
    """當日保留 rows.float_shares; 台股歷史日期沒有 point-in-time 股本, 一律置 null。"""
    if rows.is_empty() or not {"symbol", "date", "float_shares"} <= set(rows.columns):
        return rows
    taiwan = (
        pl.col("symbol").str.ends_with(".TWSE")
        | pl.col("symbol").str.ends_with(".TPEX")
    )
    trade_date = pl.col("date").cast(pl.Date, strict=False)
    return rows.with_columns(
        pl.when(taiwan & (trade_date != pl.lit(today)))
        .then(None)
        .otherwise(pl.col("float_shares"))
        .cast(pl.Float64)
        .alias("float_shares")
    )
