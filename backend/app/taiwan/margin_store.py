"""Taiwan Margin Persistence Layer.

Provides atomic per-date parquet writes, incremental refresh, dedup, and
batch reads for Taiwan Margin Trading and Short Selling (融資融券) data.

Partition layout:
    data/taiwan/margin/date=YYYY-MM-DD/part.parquet
Partition column: date
Canonical symbol: 2330.TWSE / 8069.TPEX
Canonical units:  shares (股)
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.providers.base import (
    AmountUnit,
    PriceSemantics,
    SourceMetadata,
    VolumeUnit,
)

logger = logging.getLogger(__name__)

TAIWAN_MARGIN_COLUMNS: list[str] = [
    "symbol",
    "date",
    "trade_date",
    "margin_previous_balance",
    "margin_buy",
    "margin_sell",
    "margin_cash_redemption",
    "margin_balance",
    "margin_change",
    "short_previous_balance",
    "short_sell",
    "short_cover",
    "short_stock_redemption",
    "short_balance",
    "short_change",
    "short_margin_ratio",
    "unit",
    "status",
    "source",
]

DEFAULT_MARGIN_METADATA = SourceMetadata(
    source_name="taiwan_margin_store",
    volume_unit=VolumeUnit.SHARES,
    amount_unit=AmountUnit.TWD,
    price_semantics=PriceSemantics.RAW,
)


def _schema() -> dict[str, pl.DataType]:
    return {
        "symbol": pl.String,
        "date": pl.Date,
        "trade_date": pl.Date,
        "margin_previous_balance": pl.Int64,
        "margin_buy": pl.Int64,
        "margin_sell": pl.Int64,
        "margin_cash_redemption": pl.Int64,
        "margin_balance": pl.Int64,
        "margin_change": pl.Int64,
        "short_previous_balance": pl.Int64,
        "short_sell": pl.Int64,
        "short_cover": pl.Int64,
        "short_stock_redemption": pl.Int64,
        "short_balance": pl.Int64,
        "short_change": pl.Int64,
        "short_margin_ratio": pl.Float64,
        "unit": pl.String,
        "status": pl.String,
        "source": pl.String,
    }


class TaiwanMarginStore:
    """Taiwan margin and short trading parquet persistence.

    Layout: <settings.data_dir>/taiwan/margin/date=YYYY-MM-DD/part.parquet
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        # Phase 8B-5.0.7: 省略 data_dir 时锚定到 settings.data_dir(与
        # TaiwanDailyStore/TaiwanSecurityMaster 同一套 Phase 8B-5.0.6 canonical
        # root), 不再是 cwd-dependent 的裸相对路径。显式传入 data_dir(所有既有
        # deterministic test 都这样做)时行为不变。
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "margin"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._partition_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock_for(self, d: date) -> threading.Lock:
        key = d.isoformat()
        with self._locks_guard:
            return self._partition_locks[key]

    def write_batch(self, df: pl.DataFrame, partition_date: date | None = None) -> int:
        """Atomically append rows to the margin parquet dataset, grouped by date."""
        if df.is_empty():
            return 0

        # Ensure required columns
        for col_name, dtype in _schema().items():
            if col_name not in df.columns:
                df = df.with_columns(pl.lit(None, dtype=dtype).alias(col_name))
            else:
                if col_name in ("date", "trade_date"):
                    if df.schema[col_name] != pl.Date:
                        df = df.with_columns(pl.col(col_name).cast(pl.String).str.to_date("%Y-%m-%d", strict=False))
                elif df.schema[col_name] != dtype:
                    df = df.with_columns(pl.col(col_name).cast(dtype, strict=False))

        # Compute short_margin_ratio if not provided or all null
        if "short_margin_ratio" in df.columns:
            df = df.with_columns(
                pl.when((pl.col("margin_balance").is_not_null()) & (pl.col("margin_balance") > 0) & (pl.col("short_balance").is_not_null()))
                .then((pl.col("short_balance") / pl.col("margin_balance") * 100.0).round(2))
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .alias("short_margin_ratio")
            )

        df = df.select(list(_schema().keys()))

        if partition_date is not None:
            df = df.with_columns(pl.lit(partition_date).cast(pl.Date).alias("date"))
            if "trade_date" in df.columns:
                df = df.with_columns(pl.coalesce(["trade_date", pl.lit(partition_date)]).alias("trade_date"))

        distinct_dates = df["date"].unique().to_list()
        total_written = 0

        for d in distinct_dates:
            if d is None:
                continue
            part_df = df.filter(pl.col("date") == d)
            written = self._write_single_partition(d, part_df)
            total_written += written

        return total_written

    def _write_single_partition(self, partition_date: date, new_df: pl.DataFrame) -> int:
        with self._lock_for(partition_date):
            part_dir = self._data_dir / f"date={partition_date.isoformat()}"
            part_dir.mkdir(parents=True, exist_ok=True)
            target = part_dir / "part.parquet"

            if target.exists():
                existing = pl.read_parquet(target)
                combined = (
                    pl.concat([existing, new_df], how="diagonal_relaxed")
                    .unique(subset=["symbol", "date"], keep="last")
                    .sort(["symbol", "date"])
                )
            else:
                combined = new_df.unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])

            fd, tmp_path = tempfile.mkstemp(
                prefix=f"part_{partition_date.isoformat()}_",
                suffix=".tmp",
                dir=str(part_dir),
            )
            os.close(fd)

            try:
                combined.write_parquet(tmp_path, compression="snappy")
                retries = 3
                for attempt in range(retries):
                    try:
                        os.replace(tmp_path, target)
                        break
                    except OSError as exc:
                        if attempt == retries - 1:
                            raise
                        import time
                        time.sleep(0.05 * (attempt + 1))
            except BaseException:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass
                raise

            return new_df.height

    def read_all(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """Return all persisted rows, optionally filtered by *symbols*."""
        return self.read_range(symbols, None, None)

    def read_latest_date_rows(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """Return rows from the latest available trading date partition."""
        latest = self.latest_date()
        if latest is None:
            return pl.DataFrame(schema=_schema())
        return self.read_range(symbols, latest, latest)

    def read_latest_per_symbol(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """Return the latest available row for each symbol."""
        df = self.read_all(symbols)
        if df.is_empty():
            return df
        return (
            df.with_columns(
                pl.col("date").max().over("symbol").alias("_max_date")
            )
            .filter(pl.col("date") == pl.col("_max_date"))
            .drop("_max_date")
            .sort(["symbol", "date"])
        )

    def read_range(
        self,
        symbols: list[str] | None,
        start: date | None,
        end: date | None,
    ) -> pl.DataFrame:
        """Return all rows for *symbols* between *start* and *end* (inclusive)."""
        files = sorted(self._data_dir.glob("date=*/part.parquet"))
        if not files:
            return pl.DataFrame(schema=_schema())

        frames: list[pl.DataFrame] = []
        for f in files:
            part = f.parent.name
            try:
                d = date.fromisoformat(part.split("=", 1)[1])
            except (IndexError, ValueError):
                continue

            if start and d < start:
                continue
            if end and d > end:
                continue

            part_df = pl.read_parquet(f)
            if symbols:
                part_df = part_df.filter(pl.col("symbol").is_in(symbols))
            if not part_df.is_empty():
                frames.append(part_df)

        if not frames:
            return pl.DataFrame(schema=_schema())

        return (
            pl.concat(frames, how="diagonal_relaxed")
            .unique(subset=["symbol", "date"], keep="last")
            .sort(["symbol", "date"])
        )

    def available_dates(self) -> list[date]:
        """Return a sorted list of all distinct partition dates present in storage."""
        dates: list[date] = []
        for p in self._data_dir.glob("date=*"):
            if not (p / "part.parquet").exists():
                continue
            name = p.name
            try:
                d = date.fromisoformat(name.split("=", 1)[1])
                dates.append(d)
            except (IndexError, ValueError):
                continue
        return sorted(dates)

    def latest_date(self) -> date | None:
        """Return the maximum date partition currently stored, or None if empty."""
        dates = self.available_dates()
        return dates[-1] if dates else None

    def known_symbols(self) -> list[str]:
        """Return sorted list of all distinct symbols across all partitions."""
        dates = self.available_dates()
        if not dates:
            return []
        syms: set[str] = set()
        for d in dates:
            part_path = self._data_dir / f"date={d.isoformat()}" / "part.parquet"
            if part_path.exists():
                s_df = pl.read_parquet(part_path, columns=["symbol"])
                syms.update(s_df["symbol"].to_list())
        return sorted(syms)
