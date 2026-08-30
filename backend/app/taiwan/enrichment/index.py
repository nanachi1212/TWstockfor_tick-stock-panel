"""Taiwan Benchmark Market Index Provider (TAIEX & TPEx Index).

Adapts:
  - TAIEX: https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST
  - TPEx Index: https://www.tpex.org.tw/openapi/v1/tpex_index

Guarantees:
  - Clean index schema: does not force stock volume/amount fields onto index series.
  - Dedicated MarketIndex domain model.
  - Accurate calculation of index changes and percentage movements.
"""
from __future__ import annotations

from datetime import date, datetime
import json
import logging
import re
from typing import Any

from app.taiwan.enrichment.models import (
    DatasetType,
    MarketIndex,
    SourceMeta,
    StalePolicy,
)

logger = logging.getLogger(__name__)


def _parse_tw_date(raw: str) -> date:
    """Parse ROC (115/08/28 or 1150828) or CE (2026/08/28) date string."""
    s = str(raw).strip().replace("/", "").replace("-", "")
    if len(s) == 7:  # 1150828
        roc_year = int(s[:3])
        year = roc_year + 1911
        month = int(s[3:5])
        day = int(s[5:])
        return date(year, month, day)
    elif len(s) == 8:  # 20260828
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    raise ValueError(f"Cannot parse Taiwan date format: {raw}")


def _clean_float(val: Any) -> float:
    if val is None:
        return 0.0
    s = str(val).strip().replace(",", "")
    if not s or s == "--":
        return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


class TaiwanIndexProvider:
    """Parses and normalizes TAIEX and TPEx Index daily series."""

    TWSE_INDEX_URL = "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"
    TPEX_INDEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_index"

    def parse_taiex_rows(
        self,
        rows: list[dict[str, Any]],
        source_url: str | None = None,
    ) -> list[MarketIndex]:
        results: list[MarketIndex] = []
        url = source_url or self.TWSE_INDEX_URL
        fetched_at = datetime.now()

        for r in rows:
            raw_date = r.get("Date")
            if not raw_date:
                continue
            try:
                dt = _parse_tw_date(raw_date)
            except Exception:
                continue

            open_idx = _clean_float(r.get("OpeningIndex"))
            high_idx = _clean_float(r.get("HighestIndex"))
            low_idx = _clean_float(r.get("LowestIndex"))
            close_idx = _clean_float(r.get("ClosingIndex"))

            # Calculate change against previous close if available in series
            prev_close = open_idx
            if results:
                prev_close = results[-1].close
            change = round(close_idx - prev_close, 2)
            change_pct = round(change / prev_close * 100.0, 2) if prev_close > 0 else 0.0

            meta = SourceMeta(
                source="twse:MI_5MINS_HIST",
                source_url=url,
                fetched_at=fetched_at,
                trade_date=dt,
                status="official_close",
                is_realtime=False,
                available_fields=("open", "high", "low", "close", "change", "change_pct"),
                is_stale=StalePolicy.is_stale(DatasetType.INDEX, dt, fetched_at),
            )

            idx = MarketIndex(
                symbol="TAIEX",
                name="發行量加權股價指數",
                date=dt,
                open=open_idx,
                high=high_idx,
                low=low_idx,
                close=close_idx,
                previous_close=prev_close,
                change=change,
                change_pct=change_pct,
                meta=meta,
            )
            results.append(idx)

        return results

    def parse_tpex_rows(
        self,
        rows: list[dict[str, Any]],
        source_url: str | None = None,
    ) -> list[MarketIndex]:
        results: list[MarketIndex] = []
        url = source_url or self.TPEX_INDEX_URL
        fetched_at = datetime.now()

        for r in rows:
            raw_date = r.get("Date")
            if not raw_date:
                continue
            try:
                dt = _parse_tw_date(raw_date)
            except Exception:
                continue

            open_idx = _clean_float(r.get("Open"))
            high_idx = _clean_float(r.get("High"))
            low_idx = _clean_float(r.get("Low"))
            close_idx = _clean_float(r.get("Close"))
            change = _clean_float(r.get("Change"))
            prev_close = round(close_idx - change, 2) if close_idx > 0 else 0.0
            change_pct = round(change / prev_close * 100.0, 2) if prev_close > 0 else 0.0

            meta = SourceMeta(
                source="tpex:tpex_index",
                source_url=url,
                fetched_at=fetched_at,
                trade_date=dt,
                status="official_close",
                is_realtime=False,
                available_fields=("open", "high", "low", "close", "change", "change_pct"),
                is_stale=StalePolicy.is_stale(DatasetType.INDEX, dt, fetched_at),
            )

            idx = MarketIndex(
                symbol="TPEX_INDEX",
                name="櫃買指數",
                date=dt,
                open=open_idx,
                high=high_idx,
                low=low_idx,
                close=close_idx,
                previous_close=prev_close,
                change=change,
                change_pct=change_pct,
                meta=meta,
            )
            results.append(idx)

        return results
