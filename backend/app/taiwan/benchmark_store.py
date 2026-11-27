"""Persisted official TAIEX / TPEx Index daily closes.

Reuses the existing official parsers (``TaiwanIndexProvider``) and the shared
rate-limited fetch. Two symbols x one month per call fit in one small parquet.
Changes are derived from the previous *persisted* official close only; the
parser's first-row change is not trusted and missing values never become 0.
"""
from __future__ import annotations

import os
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.enrichment.models import MarketIndex
from app.taiwan.realtime.calendar import taipei_now

_SCHEMA = pl.Schema({
    "symbol": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64, "source": pl.String, "source_url": pl.String,
    "retrieved_at": pl.String,
})
_LOCK = threading.Lock()


def _default_path() -> Path:
    from app.config import settings

    return Path(settings.data_dir) / "taiwan" / "benchmark_index.parquet"


class TaiwanBenchmarkStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()

    def read(self) -> pl.DataFrame:
        if not self.path.exists():
            return pl.DataFrame(schema=_SCHEMA)
        return pl.read_parquet(self.path)

    def write(self, frame: pl.DataFrame) -> int:
        if frame.is_empty():
            return 0
        with _LOCK:
            merged = (
                pl.concat([self.read(), frame.select(list(_SCHEMA)).cast(_SCHEMA)], how="vertical")
                .unique(subset=["symbol", "date"], keep="last")
                .sort(["symbol", "date"])
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            os.close(fd)
            try:
                merged.write_parquet(tmp)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return frame.height

    def latest(self, symbol: str, on_or_before: date | None = None) -> dict[str, Any] | None:
        rows = self.read().filter(pl.col("symbol") == symbol)
        if on_or_before is not None:
            # A brief for an older date must not pick up a close published after it.
            rows = rows.filter(pl.col("date") <= on_or_before)
        rows = rows.sort("date")
        if rows.is_empty():
            return None
        last = rows.row(-1, named=True)
        prev = rows.row(-2, named=True)["close"] if rows.height > 1 else None
        change = last["close"] - prev if prev else None
        return {
            **last,
            "previous_close": prev,
            "change": round(change, 2) if change is not None else None,
            "change_pct": change / prev if change is not None and prev else None,
        }

    def _write_indices(self, rows: list[MarketIndex], retrieved_at: str) -> int:
        frame = pl.DataFrame(
            [{
                "symbol": r.symbol, "date": r.date,
                # Missing numbers arrive as None; an index level of 0 is not a value either.
                "open": r.open or None, "high": r.high or None, "low": r.low or None,
                "close": r.close, "source": r.meta.source, "source_url": r.meta.source_url,
                "retrieved_at": retrieved_at,
            } for r in rows], schema=_SCHEMA,
        )
        return self.write(frame)

    def refresh(self, fetch_json=None, *, target_date: date | None = None,
                symbols: tuple[str, ...] | None = None) -> dict[str, Any]:
        """Refresh month-to-date, or repair just the selected symbols' missing target close."""
        from app.taiwan.enrichment.index import TaiwanIndexProvider
        from app.taiwan.institutional_margin_refresh import _fetch_json_with_retry

        fetch = fetch_json or _fetch_json_with_retry
        provider = TaiwanIndexProvider()
        stats: dict[str, Any] = {"rows_written": 0, "latest": {}, "failed": []}
        retrieved_at = taipei_now().isoformat()
        for symbol, url, parse in (
            ("TAIEX", provider.TWSE_INDEX_URL, provider.parse_taiex_rows),
            ("TPEX_INDEX", provider.TPEX_INDEX_URL, provider.parse_tpex_rows),
        ):
            if symbols is not None and symbol not in symbols:
                continue
            try:
                repair = target_date is not None and symbols is not None
                if repair:
                    existing = self.latest(symbol, target_date)
                    stats["latest"][symbol] = str(existing["date"]) if existing else None
                    if existing and existing["date"] == target_date:
                        continue
                payload = fetch(url)
                rows = payload if isinstance(payload, list) else []
                parsed = [r for r in parse(rows, url) if r.close > 0 and (not target_date or r.date <= target_date)]
                if repair:
                    parsed = [r for r in parsed if r.date == target_date]
                stats["rows_written"] += self._write_indices(parsed, retrieved_at)
                latest = self.latest(symbol, target_date)
                stats["latest"][symbol] = str(latest["date"]) if latest else None
            except Exception as exc:
                stats["failed"].append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"[:200]})
        if target_date and (symbols is None or "TAIEX" in symbols) and stats["latest"].get("TAIEX") != target_date.isoformat():
            monthly_url = provider.TWSE_MONTHLY_URL.format(day=target_date)
            try:
                points = provider.parse_taiex_monthly(fetch(monthly_url), target_date, monthly_url)
                if points:
                    stats["rows_written"] += self._write_indices(points, retrieved_at)
                    stats["latest"]["TAIEX"] = target_date.isoformat()
                    stats["failed"] = [entry for entry in stats["failed"] if entry["symbol"] != "TAIEX"]
            except Exception as exc:
                previous = next((item for item in stats["failed"] if item["symbol"] == "TAIEX"), None)
                message = f"TWSE monthly: {type(exc).__name__}: {exc}"[:200]
                if previous:
                    previous["error"] = f"OpenAPI: {previous['error']}; {message}"
                else:
                    stats["failed"].append({"symbol": "TAIEX", "error": message})
        return stats


def latest_benchmark(
    symbol: str, store: TaiwanBenchmarkStore | None = None, on_or_before: date | None = None,
) -> dict[str, Any] | None:
    try:
        return (store or TaiwanBenchmarkStore()).latest(symbol, on_or_before=on_or_before)
    except (OSError, pl.exceptions.PolarsError):
        return None

