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

import logging
import math
from datetime import date, datetime
from typing import Any

from app.taiwan.enrichment.models import (
    DatasetType,
    MarketIndex,
    SourceMeta,
    StalePolicy,
)
from app.taiwan.providers.taiwan_values import TAIPEI

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


def _clean_float(val: Any) -> float | None:
    """Parse an official number; missing or malformed stays None, never 0."""
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s or s == "--":
        return None
    try:
        number = float(s)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


class TaiwanIndexProvider:
    """Parses and normalizes TAIEX and TPEx Index daily series."""

    TWSE_INDEX_URL = "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"
    TWSE_MONTHLY_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={day:%Y%m%d}&response=json"
    TPEX_INDEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_index"

    def parse_taiex_monthly(self, report: dict, target: date, url: str) -> list[MarketIndex]:
        """Read only the requested session from the official website's named columns."""
        if not isinstance(report, dict):
            raise ValueError("TWSE monthly index response is not a report")
        if "沒有符合條件的資料" in str(report.get("stat")):
            return []
        title = str(report.get("title", "")).split()
        if (report.get("stat") != "OK" or title[-1:] != ["發行量加權股價指數歷史資料"]
                or not str(report.get("date", "")).startswith(target.strftime("%Y%m"))):
            raise ValueError("Unexpected TWSE monthly index report")
        fields = report.get("fields")
        columns = {"日期": "Date", "開盤指數": "OpeningIndex", "最高指數": "HighestIndex",
                   "最低指數": "LowestIndex", "收盤指數": "ClosingIndex"}
        if (not isinstance(fields, list) or any(fields.count(key) != 1 for key in columns)
                or not isinstance(report.get("data"), list)):
            raise ValueError("Unexpected TWSE monthly index columns or rows")
        indices = {key: fields.index(label) for label, key in columns.items()}
        selected = []
        for row in report["data"]:
            if not isinstance(row, list) or len(row) != len(fields):
                raise ValueError("Invalid TWSE monthly index row")
            if _parse_tw_date(row[indices["Date"]]) == target:
                selected.append({key: row[i] for key, i in indices.items()})
        if not selected:
            return []
        result = self.parse_taiex_rows(selected, url)
        if len(selected) != 1 or len(result) != 1:
            raise ValueError("Missing or ambiguous TWSE monthly index close")
        index = result[0]
        if (not all(v is not None and math.isfinite(v) and v > 0
                    for v in (index.open, index.high, index.low, index.close))
                or not (index.low <= min(index.open, index.close) <= max(index.open, index.close) <= index.high)):
            raise ValueError("Invalid TWSE monthly index OHLC values")
        return result

    def parse_taiex_rows(
        self,
        rows: list[dict[str, Any]],
        source_url: str | None = None,
    ) -> list[MarketIndex]:
        results: list[MarketIndex] = []
        url = source_url or self.TWSE_INDEX_URL
        fetched_at = datetime.now(TAIPEI)

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
            if close_idx is None:
                continue  # no close, no index point; other missing values stay None, never 0

            # Calculate change against previous close if available in series
            prev_close = open_idx
            if results:
                prev_close = results[-1].close
            change = round(close_idx - prev_close, 2) if prev_close is not None else None
            change_pct = (None if change is None
                          else round(change / prev_close * 100.0, 2) if prev_close > 0 else 0.0)

            meta = SourceMeta(
                source="twse:MI_5MINS_HIST",
                source_url=url,
                fetched_at=fetched_at,
                trade_date=dt,
                status="official_close",
                is_realtime=False,
                available_fields=("open", "high", "low", "close", "change", "change_pct"),
                is_stale=StalePolicy.is_stale(DatasetType.INDEX, dt, fetched_at),
                source_type="official_open_data",
                freshness_class="eod_snapshot",
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
        fetched_at = datetime.now(TAIPEI)

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
            if close_idx is None:
                continue  # no close, no index point; other missing values stay None, never 0
            if change is None:
                prev_close = change_pct = None
            else:
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
                source_type="official_open_data",
                freshness_class="eod_snapshot",
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
