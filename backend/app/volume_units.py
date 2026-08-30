"""Market-aware daily volume unit conversions."""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

TAIWAN_SUFFIXES = (".TWSE", ".TPEX")


def volume_unit_for_symbols(symbols: Iterable[str]) -> str:
    taiwan = [str(symbol).endswith(TAIWAN_SUFFIXES) for symbol in symbols]
    if taiwan and any(taiwan) and not all(taiwan):
        raise ValueError("mixed Taiwan/CN matrices require one canonical volume unit")
    return "shares" if taiwan and all(taiwan) else "lots"


def volume_shares_expr() -> pl.Expr:
    """Convert canonical daily volume to shares using the symbol market."""
    return (
        pl.when(pl.col("symbol").str.contains(r"\.(?:TWSE|TPEX)$"))
        .then(pl.col("volume"))
        .otherwise(pl.col("volume") * 100.0)
    )


def turnover_rate_pct_expr() -> pl.Expr:
    """Return percentage turnover from market volume and share-count capital."""
    return volume_shares_expr() * 100.0 / pl.col("float_shares")
