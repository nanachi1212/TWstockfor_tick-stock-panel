"""Institutional (三大法人) rolling-window aggregation over observed sessions."""
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
