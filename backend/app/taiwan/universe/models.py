"""Data models and universe definitions for Taiwan Security Master.

Responsibilities:
  - Canonical representation of Taiwan financial instruments
  - Instrument classification (stock, etf, unsupported)
  - Official metadata tracking (ISIN, listing date, industry, CFI code)
  - Predefined screener universes
  - Bridge to Phase 3 TaiwanMarketProfile rule classes
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from app.taiwan.market_rules import PriceLimitClass, TaxClass, TickSizeClass


class UniverseType(str, Enum):
    """Predefined market universes for Taiwan securities."""
    TAIWAN_ALL = "taiwan_all"        # All active TWSE + TPEx stocks & ETFs
    TWSE_ALL = "twse_all"            # All active TWSE stocks & ETFs
    TPEX_ALL = "tpex_all"            # All active TPEx stocks & ETFs
    TAIWAN_STOCKS = "taiwan_stocks"  # All active TWSE + TPEx ordinary stocks
    TAIWAN_ETFS = "taiwan_etfs"      # All active TWSE + TPEx ETFs


@dataclass(frozen=True)
class TaiwanInstrument:
    """Canonical representation of a Taiwan financial security."""
    symbol: str               # e.g. "2330.TWSE", "8069.TPEX", "0050.TWSE"
    code: str                 # e.g. "2330", "8069", "0050"
    exchange: str             # "TWSE" | "TPEX"
    name: str                 # Official Traditional Chinese name (e.g. "台積電")
    instrument_type: str      # "stock" | "etf" | "unsupported"
    listing_status: str       # "active" | "delisted" | "suspended" | "unsupported"
    listing_date: str | None  # "YYYY/MM/DD" or None
    isin: str | None          # e.g. "TW0002330008"
    industry: str | None      # e.g. "半導體業", "光電業"
    cfi_code: str | None      # ISO 10962 CFI code (e.g. "ESVUFR", "CEOJEU")
    raw_category: str         # Official category string ("股票", "ETF", "特別股", etc.)
    is_supported: bool        # True only for tradable stock/etf in current phase
    source: str               # "TWSE_ISIN" | "TPEX_ISIN"
    updated_at: str           # ISO timestamp string

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketProfileBridge:
    """Translates TaiwanInstrument metadata into Phase 3 Market Profile rule classes."""

    @staticmethod
    def get_tax_class(instrument: TaiwanInstrument) -> TaxClass:
        if instrument.instrument_type == "etf":
            return TaxClass.DOMESTIC_ETF
        return TaxClass.ORDINARY_STOCK

    @staticmethod
    def get_tick_size_class(instrument: TaiwanInstrument) -> TickSizeClass:
        if instrument.instrument_type == "etf":
            return TickSizeClass.ETF
        return TickSizeClass.ORDINARY_STOCK

    @staticmethod
    def get_price_limit_class(instrument: TaiwanInstrument) -> PriceLimitClass:
        # Domestic ordinary stocks and domestic equity ETFs have 10% limit
        return PriceLimitClass.ORDINARY_TEN_PERCENT
