"""Data models and provenance metadata for Taiwan Data Enrichment.

Covers:
  - SourceMeta: Detailed origin, timestamps, status, fallback reasons, and freshness.
  - StalePolicy: Dataset-specific freshness policies (Quotes vs Daily vs Chip vs Margin vs Index).
  - InstitutionalFlow: Three Major Institutional Investors (Foreign, Investment Trust, Dealer).
  - MarginTrading: Margin Trading & Short Selling with strict unit normalization (shares).
  - MarketQuote: Official closing snapshot / quote with explicit fallback metadata.
  - MarketIndex: TAIEX and TPEx benchmark market index series.
  - EtfCategory: Hardened ETF classification (domestic equity, foreign, bond, leveraged, inverse, unknown).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any


class DatasetType(str, Enum):
    QUOTE = "quote"
    DAILY = "daily"
    INSTITUTIONAL = "institutional"
    MARGIN = "margin"
    INDEX = "index"


class ClassificationSource(str, Enum):
    OFFICIAL_METADATA = "official_metadata"  # From TWSE t187ap47_L or official fund category
    CFI_CODE = "cfi_code"                    # From ISO 10962 CFI code in ISIN master
    NAME_HEURISTIC = "name_heuristic"        # Degraded fallback based on multi-character keywords
    UNKNOWN = "unknown"


class EtfCategory(str, Enum):

    DOMESTIC_EQUITY = "domestic_equity"    # e.g. 0050, 006208 (10% limit, 0.1% tax)
    FOREIGN_EQUITY = "foreign_equity"      # e.g. 00646, 00830 (NO_LIMIT, 0.1% tax)
    BOND = "bond"                          # e.g. 00720B, 00679B (NO_LIMIT, 0% tax incentive)
    LEVERAGED = "leveraged"                # e.g. 00631L (NO_LIMIT or custom limit)
    INVERSE = "inverse"                    # e.g. 00632R (NO_LIMIT or custom limit)
    UNKNOWN = "unknown"                    # Explicit fallback, never silently assume domestic


class StalePolicy:
    """Evaluates dataset-specific staleness rules without hardcoding a global threshold."""

    # Default staleness thresholds by dataset
    THRESHOLDS = {
        DatasetType.QUOTE: timedelta(hours=4),           # Quote stale after 4h past session close
        DatasetType.DAILY: timedelta(days=4),            # Daily stale after 4 calendar days (weekend buffer)
        DatasetType.INSTITUTIONAL: timedelta(days=4),    # Chip stale after 4 calendar days
        DatasetType.MARGIN: timedelta(days=4),           # Margin stale after 4 calendar days
        DatasetType.INDEX: timedelta(days=4),            # Index stale after 4 calendar days
    }

    @classmethod
    def is_stale(
        cls,
        dataset: DatasetType,
        trade_date: date | None,
        fetched_at: datetime,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now()
        threshold = cls.THRESHOLDS.get(dataset, timedelta(days=4))
        if trade_date is None:
            return (current_time - fetched_at) > threshold

        # Compare trade_date relative to current_time
        trade_dt = datetime(trade_date.year, trade_date.month, trade_date.day, 13, 30)
        return (current_time - trade_dt) > threshold


@dataclass(frozen=True)
class SourceMeta:
    """Provenance and data integrity metadata for all Taiwan market feeds.

    Clearly differentiates between formal documented APIs and best-effort web endpoints,
    preventing unwarranted assumptions of contractual exchange SLAs.
    """
    source: str                      # e.g. "twse:mis", "twse:t86", "yahoo:chart", "twse:stock_day_all"
    source_url: str                  # Endpoint URL
    fetched_at: datetime | str       # Timestamp when request succeeded
    trade_date: date | None          # Applicable trading date
    status: str                      # "realtime", "official_snapshot", "fallback", "stale", etc.
    provider: str = ""              # Supplying organization/adapter (twse, tpex, ...)
    is_realtime: bool = False        # True only during live regular trading hours
    fallback_reason: str | None = None  # Non-empty if fallback was triggered
    available_fields: tuple[str, ...] = ()
    is_stale: bool = False
    source_type: str = "unknown" # "first_party_web_endpoint", "third_party_aggregator", "official_open_data", "local_store", "unknown"
    freshness_class: str = "unknown" # "best_effort_near_realtime", "delayed_15m", "eod_snapshot", "daily_cached", "unknown"
    is_best_effort: bool = False    # Must be explicitly set True by providers that lack contractual SLA
    documented_sla: bool = False    # False indicates lack of consumer SLA from TWSE/TPEx for public web scraping
    observed_latency_ms: float | None = None # Empirically observed network roundtrip, not SLA contract

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(self.fetched_at, datetime):
            data["fetched_at"] = self.fetched_at.isoformat()
        else:
            data["fetched_at"] = str(self.fetched_at)
        data["trade_date"] = self.trade_date.isoformat() if self.trade_date else None
        data["retrieved_at"] = data["fetched_at"]
        return data

    @property
    def retrieved_at(self) -> datetime | str:
        return self.fetched_at



@dataclass(frozen=True)
class InstitutionalFlow:
    """Standardized Three Major Institutional Investors trading record.

    All quantities are strictly denominated in shares.
    """
    symbol: str
    trade_date: date
    # Foreign Investors (外資及陸資)
    foreign_buy: int
    foreign_sell: int
    foreign_net: int
    # Investment Trust (投信)
    investment_trust_buy: int
    investment_trust_sell: int
    investment_trust_net: int
    # Dealers Combined (自營商合計)
    dealer_buy: int
    dealer_sell: int
    dealer_net: int
    unit: str = "shares"
    # Dealer Sub-accounts (自營商避險與自行買賣, 若官方提供)
    dealer_proprietary_buy: int = 0
    dealer_proprietary_sell: int = 0
    dealer_proprietary_net: int = 0
    dealer_hedge_buy: int = 0
    dealer_hedge_sell: int = 0
    dealer_hedge_net: int = 0
    # Integrity validation
    official_net: int = 0
    computed_net: int = 0
    has_discrepancy: bool = False
    meta: SourceMeta | None = None

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["trade_date"] = self.trade_date.isoformat()
        if self.meta:
            res["meta"] = self.meta.to_dict()
        return res


@dataclass(frozen=True)
class MarginTrading:
    """Standardized Margin Trading & Short Selling record.

    Source units (lots/張) are deterministically normalized to shares (*1000).
    """
    symbol: str
    trade_date: date
    unit: str                         # Strictly "shares"
    source_unit: str                  # "lots" (張) or "shares"
    lot_multiplier: int               # 1000 for lots, 1 for shares
    # Margin (融資)
    margin_previous_balance: int
    margin_buy: int
    margin_sell: int
    margin_cash_redemption: int
    margin_balance: int
    margin_change: int
    # Short Selling (融券)
    short_previous_balance: int
    short_sell: int
    short_cover: int
    short_stock_redemption: int
    short_balance: int
    short_change: int
    # Computed metrics
    short_margin_ratio: float | None  # Derived by the factor layer, not the raw provider
    note: str = ""
    meta: SourceMeta | None = None

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["trade_date"] = self.trade_date.isoformat()
        if self.meta:
            res["meta"] = self.meta.to_dict()
        return res


@dataclass(frozen=True)
class MarketQuote:
    """Official Taiwan market snapshot / closing quote with provenance."""
    symbol: str
    name: str
    price: float
    open: float
    high: float
    low: float
    previous_close: float
    change: float
    change_pct: float
    volume: int                       # shares
    amount: float                     # TWD
    trade_time: datetime
    meta: SourceMeta

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["trade_time"] = self.trade_time.isoformat()
        res["meta"] = self.meta.to_dict()
        return res


@dataclass(frozen=True)
class MarketIndex:
    """Taiwan market benchmark index series point (TAIEX, TPEx Index)."""
    symbol: str                       # "TAIEX" | "TPEX_INDEX"
    name: str                         # "發行量加權股價指數" | "櫃買指數"
    date: date
    open: float
    high: float
    low: float
    close: float
    previous_close: float
    change: float
    change_pct: float
    meta: SourceMeta

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["date"] = self.date.isoformat()
        res["meta"] = self.meta.to_dict()
        return res
