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

from datetime import date

import polars as pl

INVESTORS = ("foreign", "investment_trust", "dealer")


def compute_institutional_window(
    df: pl.DataFrame, sessions: list[date], complete_dates: set[date],
) -> dict[str, dict]:
    """Aggregate observed shares and bounded signed streaks without filling gaps.

    ``df`` represents one security or one daily market aggregate. Missing or
    incomplete dates break streaks. Streaks reaching the window edge are lower
    bounds; callers expose the window length rather than claiming lifetime runs.
    """
    rows = {r["date"]: r for r in df.iter_rows(named=True)}
    result = {}
    for investor in INVESTORS:
        column = f"{investor}_net"
        values = {d: rows[d][column] for d in sessions if d in rows and rows[d].get(column) is not None}
        net = sum(values.values()) if values else None
        buy = sell = None
        streak_dates: set[date] = set()
        if sessions and sessions[-1] in values and sessions[-1] in complete_dates:
            sign = 1 if values[sessions[-1]] > 0 else -1 if values[sessions[-1]] < 0 else 0
            run = 0
            for d in reversed(sessions):
                if d not in values or d not in complete_dates:
                    break
                streak_dates.add(d)
                current_sign = 1 if values[d] > 0 else -1 if values[d] < 0 else 0
                if current_sign != sign or sign == 0:
                    break
                run += 1
            buy, sell = (run, 0) if sign > 0 else (0, run) if sign < 0 else (0, 0)
        result[investor] = {
            "net_shares": net, "coverage_dates": set(values),
            "buy_streak": buy, "sell_streak": sell, "streak_dates": streak_dates,
            "streak_capped": buy == len(sessions) or sell == len(sessions),
        }
    return result


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
