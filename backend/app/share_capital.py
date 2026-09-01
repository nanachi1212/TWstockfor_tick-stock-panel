"""历史股本解析, Taiwan 缺少 PIT denominator 时保持 fail-closed。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def load_share_history(data_dir: Path) -> pl.DataFrame:
    """读取本地财务股本表; 未同步或损坏时返回空表。"""
    path = data_dir / "financials" / "shares" / "part.parquet"
    if not path.exists():
        return pl.DataFrame()
    try:
        shares = pl.read_parquet(path)
        if not {"symbol", "period_end", "float_shares"} <= set(shares.columns):
            return pl.DataFrame()
        return shares
    except Exception:
        return pl.DataFrame()


def apply_historical_float_shares(
    rows: pl.DataFrame,
    shares: pl.DataFrame | None,
    *,
    today: date,
) -> pl.DataFrame:
    """为行情行解析有效流通股本。

    当日保留 rows.float_shares; 历史日期使用公告日不晚于交易日的最新股本。
    既有非 Taiwan 市场保留 current fallback; Taiwan 不允许 current backward-fill。
    """
    required = {"symbol", "date", "float_shares"}
    if rows.is_empty() or not required <= set(rows.columns):
        return rows

    def fail_closed_taiwan_history(frame: pl.DataFrame) -> pl.DataFrame:
        taiwan = (
            pl.col("symbol").str.ends_with(".TWSE")
            | pl.col("symbol").str.ends_with(".TPEX")
        )
        trade_date = pl.col("date").cast(pl.Date, strict=False)
        return frame.with_columns(
            pl.when(taiwan & (trade_date != pl.lit(today)))
            .then(None)
            .otherwise(pl.col("float_shares"))
            .cast(pl.Float64)
            .alias("float_shares")
        )

    if (
        shares is None
        or shares.is_empty()
        or not {"symbol", "period_end", "float_shares"} <= set(shares.columns)
    ):
        return fail_closed_taiwan_history(rows)

    def as_date_expr(column: str) -> pl.Expr:
        dtype = shares.schema[column]
        if dtype == pl.Utf8:
            return pl.col(column).str.to_date(strict=False)
        return pl.col(column).cast(pl.Date, strict=False)

    available_date = as_date_expr("period_end")
    if "announce_date" in shares.columns:
        available_date = as_date_expr("announce_date").fill_null(available_date)

    history = (
        shares
        .select(
            pl.col("symbol").cast(pl.Utf8),
            available_date.alias("_share_available_date"),
            pl.col("period_end").cast(pl.Utf8).alias("_share_period_end"),
            pl.col("float_shares").cast(pl.Float64, strict=False).alias("_historical_float_shares"),
        )
        .filter(
            pl.col("symbol").is_not_null()
            & pl.col("_share_available_date").is_not_null()
            & (pl.col("_historical_float_shares") > 0)
            & ~pl.col("symbol").str.ends_with(".TWSE")
            & ~pl.col("symbol").str.ends_with(".TPEX")
        )
        .sort(["symbol", "_share_available_date", "_share_period_end"])
        .unique(subset=["symbol", "_share_available_date"], keep="last")
        .sort(["symbol", "_share_available_date"])
    )
    if history.is_empty():
        return fail_closed_taiwan_history(rows)

    resolved = (
        rows
        .with_row_index("_share_row_order")
        .with_columns(
            pl.col("symbol").cast(pl.Utf8),
            pl.col("date").cast(pl.Date, strict=False).alias("_share_trade_date"),
        )
        .sort(["symbol", "_share_trade_date"])
        .join_asof(
            history,
            left_on="_share_trade_date",
            right_on="_share_available_date",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .with_columns(
            pl.when(pl.col("_share_trade_date") == pl.lit(today))
            .then(pl.col("float_shares"))
            .when(
                pl.col("symbol").str.ends_with(".TWSE")
                | pl.col("symbol").str.ends_with(".TPEX")
            )
            .then(pl.col("_historical_float_shares"))
            .otherwise(
                pl.coalesce("_historical_float_shares", "float_shares")
            )
            .alias("float_shares")
        )
        .sort("_share_row_order")
    )
    return resolved.drop(
        "_share_row_order",
        "_share_trade_date",
        "_share_available_date",
        "_share_period_end",
        "_historical_float_shares",
    )
