"""Daily volume unit conversions."""

from __future__ import annotations

import polars as pl


def turnover_rate_pct_expr() -> pl.Expr:
    """Return percentage turnover; Taiwan volume and float_shares are both in shares."""
    return pl.col("volume") * 100.0 / pl.col("float_shares")
