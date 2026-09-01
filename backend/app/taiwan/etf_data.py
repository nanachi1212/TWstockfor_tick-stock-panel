"""Official Taiwan ETF structured data, separate from company fundamentals."""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.taiwan.providers.taiwan_values import TAIPEI, parse_number, parse_taiwan_date

TWSE_ETF_PRODUCTS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime(value: str | datetime | None) -> datetime | None:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result is not None and result.tzinfo is None:
        raise ValueError("ETF timestamps must be timezone-aware")
    return result


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

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("retrieved_at", "published_at", "available_at"):
            result[name] = _iso(getattr(self, name))
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TaiwanETFSnapshot:
        data = {field.name: raw.get(field.name) for field in fields(cls)}
        for name in ("retrieved_at", "published_at", "available_at"):
            data[name] = _datetime(data[name])
        return cls(**data)


@dataclass(frozen=True)
class TaiwanETFDistribution:
    symbol: str
    distribution_per_unit: float | None
    ex_date: str | None
    record_date: str | None
    payment_date: str | None
    currency: str
    provider: str
    source: str
    source_url: str
    retrieved_at: datetime
    status: str
    raw_unit: str = "TWD_per_unit"
    normalized_unit: str = "TWD_per_unit"
    published_at: datetime | None = None
    available_at: datetime | None = None


@dataclass(frozen=True)
class TaiwanETFHolding:
    etf_symbol: str
    holding_symbol: str | None
    name: str
    weight: float | None
    as_of_date: str
    currency: str | None
    provider: str
    source: str
    source_url: str
    retrieved_at: datetime
    status: str
    coverage: str
    raw_unit: str = "percent"
    normalized_unit: str = "ratio"

    def __post_init__(self) -> None:
        if self.coverage not in {"full", "partial", "unknown"}:
            raise ValueError("invalid holdings coverage")


def calculate_premium_discount(
    market_price: float | None,
    nav: float | None,
    *,
    price_date: str,
    nav_date: str,
) -> float | None:
    """Calculate a ratio only for compatible values on the same date."""
    if market_price is None or nav is None or nav <= 0 or price_date != nav_date:
        return None
    return market_price / nav - 1


def latest_snapshot_as_of(
    records: Iterable[TaiwanETFSnapshot], symbol: str, query_at: datetime,
) -> TaiwanETFSnapshot | None:
    query_at = _datetime(query_at)
    eligible = [
        row for row in records
        if row.symbol == symbol and row.available_at is not None and query_at > row.available_at
    ]
    return max(eligible, key=lambda row: (row.as_of_date, row.available_at)) if eligible else None


class TaiwanETFSnapshotStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "taiwan" / "etf" / "snapshots.jsonl"

    def load(self) -> list[TaiwanETFSnapshot]:
        if not self.path.exists():
            return []
        return [TaiwanETFSnapshot.from_dict(json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

    def save(self, records: Iterable[TaiwanETFSnapshot]) -> int:
        merged = {(row.symbol, row.as_of_date, row.source): row for row in self.load()}
        for row in records:
            merged[(row.symbol, row.as_of_date, row.source)] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(merged.values(), key=lambda row: (row.symbol, row.as_of_date, row.source))
        payload = "\n".join(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True) for row in rows)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        temporary.replace(self.path)
        return len(rows)


class TaiwanOfficialETFData:
    """Current official ETF metadata; date-only records remain PIT-unavailable."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30.0)

    def profile(self, symbol: str, exchange: str) -> TaiwanETFProfile:
        retrieved, row = self._row(symbol, exchange)
        return TaiwanETFProfile(
            symbol=symbol,
            name=str(row.get("基金簡稱") or "").strip() or None,
            exchange=exchange,
            benchmark=str(row.get("標的指數/追蹤指數名稱") or "").strip() or None,
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
