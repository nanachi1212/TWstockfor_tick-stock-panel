"""Local file-based minimal cache for FinMind dataset queries.

Avoids duplicate external API requests when browsing stock details repeatedly.
Enforces data_date, fetched_at, status classification, and does NOT coerce missing into 0.
Stored under: data/taiwan/finmind_cache/{dataset}/{symbol}.json
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Default Cache TTLs in seconds
DATASET_TTL: dict[str, int] = {
    "TaiwanStockMonthRevenue": 24 * 3600,       # 24 hours
    "TaiwanStockFinancialStatements": 7 * 86400, # 7 days
    "TaiwanStockShareholding": 6 * 3600,        # 6 hours
    "TaiwanStockSecuritiesLending": 6 * 3600,   # 6 hours
}
NEGATIVE_TTL = 900  # 15 minutes for unavailable or error responses


def _cache_root() -> Path:
    p = settings.data_dir / "taiwan" / "finmind_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


class FinMindCache:
    """Minimal file cache for FinMind datasets."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or _cache_root()

    def _file_path(self, dataset: str, symbol: str) -> Path:
        safe_sym = symbol.replace("/", "_").replace(":", "_")
        d = self.cache_dir / dataset
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe_sym}.json"

    def get(self, dataset: str, symbol: str) -> dict[str, Any] | None:
        """Get cached payload if not expired; returns None on miss or expiration."""
        path = self._file_path(dataset, symbol)
        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            fetched_at_str = raw.get("fetched_at")
            if not fetched_at_str:
                return None

            fetched_at = datetime.fromisoformat(fetched_at_str)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            elapsed = (now - fetched_at).total_seconds()

            status = raw.get("status", "available")
            max_age = NEGATIVE_TTL if status in ("unavailable", "error") else DATASET_TTL.get(dataset, 6 * 3600)

            if elapsed < max_age:
                return raw
            return None
        except Exception as e:
            logger.warning("FinMindCache read failed for %s/%s: %s", dataset, symbol, e)
            return None

    def set(
        self,
        dataset: str,
        symbol: str,
        data: Any,
        *,
        data_date: str | None = None,
        status: str = "available",
        error_msg: str | None = None,
    ) -> None:
        """Write payload to cache."""
        path = self._file_path(dataset, symbol)
        now_iso = datetime.now(UTC).isoformat()
        payload = {
            "symbol": symbol,
            "dataset": dataset,
            "data_date": data_date,
            "fetched_at": now_iso,
            "status": status,
            "error_msg": error_msg,
            "data": data,
        }
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.warning("FinMindCache write failed for %s/%s: %s", dataset, symbol, e)

    def list_cached_symbols(self, dataset: str) -> list[str]:
        """List all cached symbols for a given dataset."""
        d = self.cache_dir / dataset
        if not d.exists():
            return []
        return [f.stem for f in d.glob("*.json")]

    def clear(self, dataset: str | None = None, symbol: str | None = None) -> None:
        """Clear cached entries."""
        if dataset and symbol:
            p = self._file_path(dataset, symbol)
            if p.exists():
                p.unlink()
        elif dataset:
            d = self.cache_dir / dataset
            if d.exists():
                for f in d.glob("*.json"):
                    f.unlink()
        else:
            for f in self.cache_dir.rglob("*.json"):
                f.unlink()

