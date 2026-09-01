"""Polars Factor Pipeline for Taiwan Chip (三大法人) and Margin (資券) Indicators.

Responsibilities:
  - Computes rolling multi-day institutional flows:
      * foreign_net_5d
      * investment_trust_net_5d
      * dealer_net_5d
  - Computes margin balance momentum and short-margin ratio:
      * margin_balance_change
      * short_margin_ratio
  - Full compatibility with existing Polars pipelines and Screener
"""
from __future__ import annotations

import polars as pl


def compute_chip_factors(df: pl.DataFrame) -> pl.DataFrame:
    """Compute 5-day rolling net institutional flows on a Polars DataFrame.

    Requires columns: ['trade_date', 'symbol', 'foreign_net', 'investment_trust_net', 'dealer_net']
    """
    if df.is_empty():
        return df

    sorted_df = df.sort(["symbol", "trade_date"])

    return sorted_df.with_columns([
        pl.col("foreign_net")
        .rolling_sum(window_size=5, min_samples=1)
        .over("symbol")
        .alias("foreign_net_5d"),
        pl.col("investment_trust_net")
        .rolling_sum(window_size=5, min_samples=1)
        .over("symbol")
        .alias("investment_trust_net_5d"),
        pl.col("dealer_net")
        .rolling_sum(window_size=5, min_samples=1)
        .over("symbol")
        .alias("dealer_net_5d"),
    ])



def compute_margin_factors(df: pl.DataFrame) -> pl.DataFrame:
    """Compute margin momentum and short-margin ratio on a Polars DataFrame.

    Requires columns: ['trade_date', 'symbol', 'margin_balance', 'margin_previous_balance', 'short_balance']
    """
    if df.is_empty():
        return df

    sorted_df = df.sort(["symbol", "trade_date"])

    return sorted_df.with_columns([
        (pl.col("margin_balance") - pl.col("margin_previous_balance")).alias("margin_balance_change"),
        pl.when(pl.col("margin_balance") > 0)
        .then(pl.col("short_balance") / pl.col("margin_balance") * 100.0)
        .otherwise(0.0)
        .round(2)
        .alias("short_margin_ratio"),
    ])
