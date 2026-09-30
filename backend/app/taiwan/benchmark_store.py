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
from pathlib import Path
from typing import Any

import polars as pl

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

    def latest(self, symbol: str) -> dict[str, Any] | None:
        rows = self.read().filter(pl.col("symbol") == symbol).sort("date")
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

    def refresh(self, fetch_json=None) -> dict[str, Any]:
        """Fetch month-to-date official series for both exchanges (2 HTTP calls)."""
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
            try:
                payload = fetch(url)
                rows = payload if isinstance(payload, list) else []
                parsed = [r for r in parse(rows, url) if r.close > 0]
                frame = pl.DataFrame(
                    [{
                        "symbol": symbol, "date": r.date,
                        # Parser maps missing numbers to 0.0; keep them missing.
                        "open": r.open or None, "high": r.high or None, "low": r.low or None,
                        "close": r.close, "source": r.meta.source, "source_url": url,
                        "retrieved_at": retrieved_at,
                    } for r in parsed],
                    schema=_SCHEMA,
                )
                stats["rows_written"] += self.write(frame)
                latest = self.latest(symbol)
                stats["latest"][symbol] = str(latest["date"]) if latest else None
            except Exception as exc:
                stats["failed"].append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"[:200]})
        return stats


def latest_benchmark(symbol: str, store: TaiwanBenchmarkStore | None = None) -> dict[str, Any] | None:
    try:
        return (store or TaiwanBenchmarkStore()).latest(symbol)
    except (OSError, pl.exceptions.PolarsError):
        return None

