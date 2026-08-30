"""Strict normalizer for Taiwan market data.

Enforces:
  1. Deterministic VolumeUnit conversion into canonical shares (never guess).
  2. Deterministic AmountUnit conversion into canonical TWD (or None if unavailable).
  3. Strict OHLC integrity rules (high >= low, high >= open, etc.).
  4. Non-zero/non-dummy price sanitization (never convert '--' or halt symbols to 0).
  5. Elimination of duplicate (symbol, date) rows.
  6. Output aligned with repository DAILY_COLS schema:
     ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"]
"""
from __future__ import annotations

import logging
from typing import Any

import polars as pl

from app.data_providers.normalizer import DAILY_COLS, to_polars
from app.taiwan.providers.base import AmountUnit, SourceMetadata, VolumeUnit
from app.taiwan.symbol import TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)


def normalize_taiwan_daily(
    data: Any,
    metadata: SourceMetadata,
    default_symbol: str | TaiwanSymbol | None = None,
) -> pl.DataFrame:
    """Normalize raw daily market data from a declared source adapter.

    Args:
        data: Raw rows (list[dict], DataFrame, or dict).
        metadata: Explicit source metadata declaring volume_unit, amount_unit, etc.
        default_symbol: Default canonical symbol if missing in raw rows.

    Returns:
        Cleaned Polars DataFrame strictly matching DAILY_COLS.
    """
    df = to_polars(data)
    if df.is_empty():
        return pl.DataFrame(schema={col: pl.Float64 for col in DAILY_COLS})

    # Standardize column names
    rename_map = {
        "stock_id": "symbol",
        "ts_code": "symbol",
        "trade_date": "date",
        "datetime": "date",
        "Date": "date",
        "Open": "open",
        "High": "high",
        "max": "high",
        "Low": "low",
        "min": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
        "Trading_Volume": "volume",
        "Trading_money": "amount",
        "vol": "volume",
        "amt": "amount",
        "timestamp": "quote_ts",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})

    # Canonical Symbol Assignment & Normalization
    canonical_sym_str = None
    if default_symbol is not None:
        if isinstance(default_symbol, TaiwanSymbol):
            canonical_sym_str = default_symbol.canonical
        elif isinstance(default_symbol, str):
            canonical_sym_str = default_symbol

    if "symbol" not in df.columns:
        if not canonical_sym_str:
            raise ValueError("No symbol column found and no default_symbol provided")
        df = df.with_columns(pl.lit(canonical_sym_str).alias("symbol"))
    else:
        # If symbol column exists, normalize non-canonical entries if default_symbol provided
        if canonical_sym_str:
            df = df.with_columns(pl.lit(canonical_sym_str).alias("symbol"))
        else:
            # Cast to string
            df = df.with_columns(pl.col("symbol").cast(pl.Utf8))

    # Date normalization
    if "date" in df.columns:
        if df.schema["date"] != pl.Date:
            # Handle string dates "YYYY-MM-DD", "YYYYMMDD", or Epoch ms/s
            if df.schema["date"] in (pl.Utf8, pl.String):
                df = df.with_columns(
                    pl.coalesce([
                        pl.col("date").str.strptime(pl.Date, format="%Y-%m-%d", strict=False),
                        pl.col("date").str.strptime(pl.Date, format="%Y%m%d", strict=False),
                        pl.col("date").str.strptime(pl.Date, format="%Y/%m/%d", strict=False),
                    ]).alias("date")
                )
            elif df.schema["date"] in (pl.Datetime,):
                df = df.with_columns(pl.col("date").dt.date().alias("date"))
            elif df.schema["date"] in (pl.Int64, pl.Float64):
                # Unix timestamp (seconds or ms)
                is_ms = df.select(pl.col("date").max()).item() > 10_000_000_000
                unit = "ms" if is_ms else "s"
                df = df.with_columns(
                    pl.from_epoch(pl.col("date").cast(pl.Int64), time_unit=unit).dt.date().alias("date")
                )
    else:
        raise ValueError("Missing 'date' column in daily data")

    # Drop rows where date failed to parse
    df = df.filter(pl.col("date").is_not_null())
    if df.is_empty():
        return pl.DataFrame()

    # Price numeric sanitization (replace invalid strings '--', '', None with null)
    price_cols = ["open", "high", "low", "close"]
    for col in price_cols:
        if col in df.columns:
            if df.schema[col] in (pl.Utf8, pl.String):
                df = df.with_columns(
                    pl.when(pl.col(col).str.strip_chars().is_in(["--", "", "None", "null", "NaN"]))
                    .then(None)
                    .otherwise(pl.col(col))
                    .cast(pl.Float64, strict=False)
                    .alias(col)
                )
            else:
                df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
        else:
            raise ValueError(f"Missing mandatory price column: {col}")

    # Volume numeric sanitization and deterministic unit conversion
    if "volume" not in df.columns:
        raise ValueError("Missing mandatory 'volume' column")

    if df.schema["volume"] in (pl.Utf8, pl.String):
        df = df.with_columns(
            pl.col("volume").str.replace_all(",", "").cast(pl.Float64, strict=False)
        )
    else:
        df = df.with_columns(pl.col("volume").cast(pl.Float64, strict=False))

    # Deterministic Volume Unit Conversion
    if metadata.volume_unit == VolumeUnit.SHARES:
        # Already in shares
        pass
    elif metadata.volume_unit == VolumeUnit.LOTS:
        # 1 lot (張) = 1,000 shares (股)
        df = df.with_columns((pl.col("volume") * 1000.0).alias("volume"))
    else:
        raise ValueError(f"Unsupported volume unit: {metadata.volume_unit!r}")

    # Amount numeric sanitization and deterministic unit conversion
    if metadata.amount_unit == AmountUnit.UNAVAILABLE:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("amount"))
    elif "amount" in df.columns:
        if df.schema["amount"] in (pl.Utf8, pl.String):
            df = df.with_columns(
                pl.col("amount").str.replace_all(",", "").cast(pl.Float64, strict=False)
            )
        else:
            df = df.with_columns(pl.col("amount").cast(pl.Float64, strict=False))

        if metadata.amount_unit == AmountUnit.TWD:
            pass
        elif metadata.amount_unit == AmountUnit.THOUSAND_TWD:
            df = df.with_columns((pl.col("amount") * 1000.0).alias("amount"))
        elif metadata.amount_unit == AmountUnit.MILLION_TWD:
            df = df.with_columns((pl.col("amount") * 1000000.0).alias("amount"))
        else:
            raise ValueError(f"Unsupported amount unit: {metadata.amount_unit!r}")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("amount"))

    # quote_ts
    if "quote_ts" in df.columns:
        df = df.with_columns(pl.col("quote_ts").cast(pl.Int64, strict=False))
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Int64).alias("quote_ts"))

    # ── Strict OHLC Integrity Rules (Prompt Rule 9) ──
    # Valid positive prices, no zero/negative prices
    valid_prices = (
        (pl.col("open") > 0)
        & (pl.col("high") > 0)
        & (pl.col("low") > 0)
        & (pl.col("close") > 0)
    )
    # Logical bound checks: high >= low, high >= open, high >= close, low <= open, low <= close
    logical_bounds = (
        (pl.col("high") >= pl.col("low"))
        & (pl.col("high") >= pl.col("open"))
        & (pl.col("high") >= pl.col("close"))
        & (pl.col("low") <= pl.col("open"))
        & (pl.col("low") <= pl.col("close"))
    )
    valid_volume = pl.col("volume") >= 0
    valid_amount = pl.col("amount").is_null() | (pl.col("amount") >= 0)

    df = df.filter(valid_prices & logical_bounds & valid_volume & valid_amount)
    if df.is_empty():
        return pl.DataFrame()

    # Deduplicate by (symbol, date), keep last
    df = df.unique(subset=["symbol", "date"], keep="last")

    # Sort ascending by symbol and date
    df = df.sort(["symbol", "date"])

    # Keep only DAILY_COLS
    keep = [c for c in DAILY_COLS if c in df.columns]
    return df.select(keep)
