"""Official Taiwan ETF structured data, separate from company fundamentals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import unescape
from typing import Any

import httpx

from app.taiwan.providers.http import taiwan_client
from app.taiwan.providers.taiwan_values import TAIPEI, parse_number, parse_taiwan_date

TWSE_ETF_PRODUCTS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"


@dataclass(frozen=True)
class TaiwanETFProfile:
    symbol: str
    name: str | None
    exchange: str
    benchmark: str | None
    etf_type: str | None
    inception_date: str | None
    listing_date: str | None
    currency: str
    provider: str
    source: str
    source_url: str
    retrieved_at: datetime
    status: str
    issuer: str | None = None
    leverage_multiplier: float | None = None
    inverse: bool | None = None
    distribution_policy: str | None = None


@dataclass(frozen=True)
class TaiwanETFSnapshot:
    symbol: str
    as_of_date: str
    nav: float | None
    estimated_nav: float | None
    market_price: float | None
    premium_discount: float | None
    aum: float | None
    outstanding_units: int | None
    issued_units: int | None
    currency: str
    provider: str
    source: str
    source_url: str
    retrieved_at: datetime
    status: str
    raw_unit: str
    normalized_unit: str
    published_at: datetime | None = None
    available_at: datetime | None = None


class TaiwanOfficialETFData:
    """Current official ETF metadata; date-only records remain PIT-unavailable."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or taiwan_client(timeout=30.0)

    def profile(self, symbol: str, exchange: str) -> TaiwanETFProfile:
        retrieved, row = self._row(symbol, exchange)
        name_raw = str(row.get("基金簡稱") or "").strip()
        benchmark_raw = str(row.get("標的指數/追蹤指數名稱") or "").strip()
        return TaiwanETFProfile(
            symbol=symbol,
            name=unescape(name_raw).strip() or None,
            exchange=exchange,
            benchmark=unescape(benchmark_raw).strip() or None,
            etf_type=str(row.get("基金類型") or "").strip() or None,
            inception_date=self._official_date(row.get("成立日期")),
            listing_date=self._official_date(row.get("上市日期")),
            currency="TWD",
            provider="TWSE",
            source="twse:etf_product_metadata",
            source_url=TWSE_ETF_PRODUCTS_URL,
            retrieved_at=retrieved,
            status="official",
        )

    def snapshot(self, symbol: str, exchange: str) -> TaiwanETFSnapshot:
        retrieved, row = self._row(symbol, exchange)
        raw_units = row.get("發行單位數/轉換數")
        units = parse_number(raw_units)
        report_date = self._official_date(row.get("出表日期"))
        return TaiwanETFSnapshot(
            symbol=symbol,
            as_of_date=report_date or "",
            nav=None,
            estimated_nav=None,
            market_price=None,
            premium_discount=None,
            aum=None,
            outstanding_units=None,
            issued_units=None if units is None else int(units),
            currency="TWD",
            provider="TWSE",
            source="twse:etf_product_metadata",
            source_url=TWSE_ETF_PRODUCTS_URL,
            retrieved_at=retrieved,
            status="data_insufficient",
            raw_unit="units",
            normalized_unit="units",
        )

    def _row(self, symbol: str, exchange: str) -> tuple[datetime, dict[str, Any]]:
        if exchange != "TWSE":
            raise LookupError("official TPEx ETF structured dataset is data_insufficient")
        response = self.client.get(TWSE_ETF_PRODUCTS_URL)
        response.raise_for_status()
        retrieved = datetime.now(TAIPEI)
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("official ETF product schema changed")
        code = symbol.split(".")[0]
        for row in rows:
            if str(row.get("基金代號") or "").strip() == code:
                return retrieved, row
        raise LookupError(f"official ETF row not found for {symbol}")

    @staticmethod
    def _official_date(raw: object) -> str | None:
        value = str(raw or "").strip()
        return parse_taiwan_date(value).isoformat() if value else None
