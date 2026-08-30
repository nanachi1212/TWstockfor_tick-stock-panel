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

from app.taiwan.enrichment.models import EtfCategory
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
    cfi_code: str | None      # ISO 10962 CFI code (e.g. "ESVUFR", "CEOJEU", "CEOIEU")
    raw_category: str         # Official category string ("股票", "ETF", "特別股", etc.)
    is_supported: bool        # True only for tradable stock/etf in current phase
    source: str               # "TWSE_ISIN" | "TPEX_ISIN"
    updated_at: str           # ISO timestamp string
    etf_category: str | None = None  # EtfCategory value if instrument_type == "etf"
    classification_source: str | None = None  # ClassificationSource value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketProfileBridge:
    """Translates TaiwanInstrument metadata into Phase 3 Market Profile rule classes.

    Strictly refuses to apply real trading regulations (tax, price limits) if an ETF's
    classification is unconfirmed or derived solely from name heuristics.
    """

    CONFIRMED_SOURCES = {
        "official_metadata",
        "cfi_code",
    }

    @classmethod
    def verify_confirmed_etf(cls, instrument: TaiwanInstrument) -> None:
        if instrument.instrument_type == "etf":
            source = instrument.classification_source or "unknown"
            if source not in cls.CONFIRMED_SOURCES:
                raise ValueError(
                    f"Refusing to apply regulatory market rules to unconfirmed ETF {instrument.symbol} "
                    f"(source: {source!r}). Confirmed official product metadata or CFI code is required."
                )

    @classmethod
    def get_tax_class(cls, instrument: TaiwanInstrument) -> TaxClass:
        if instrument.instrument_type == "etf":
            cls.verify_confirmed_etf(instrument)
            cat = instrument.etf_category or EtfCategory.UNKNOWN.value
            if cat == EtfCategory.BOND.value:
                return TaxClass.BOND_ETF
            return TaxClass.DOMESTIC_ETF
        return TaxClass.ORDINARY_STOCK

    @classmethod
    def get_tick_size_class(cls, instrument: TaiwanInstrument) -> TickSizeClass:
        if instrument.instrument_type == "etf":
            return TickSizeClass.ETF
        return TickSizeClass.ORDINARY_STOCK

    @classmethod
    def get_price_limit_class(cls, instrument: TaiwanInstrument) -> PriceLimitClass:
        if instrument.instrument_type == "stock":
            return PriceLimitClass.ORDINARY_TEN_PERCENT

        if instrument.instrument_type == "etf":
            cls.verify_confirmed_etf(instrument)
            cat = instrument.etf_category or EtfCategory.UNKNOWN.value
            if cat == EtfCategory.DOMESTIC_EQUITY.value:
                return PriceLimitClass.ORDINARY_TEN_PERCENT
            elif cat in (
                EtfCategory.FOREIGN_EQUITY.value,
                EtfCategory.BOND.value,
                EtfCategory.LEVERAGED.value,
                EtfCategory.INVERSE.value,
            ):
                return PriceLimitClass.NO_LIMIT
            elif cat == EtfCategory.UNKNOWN.value:
                raise ValueError(
                    f"Cannot determine price limit for UNKNOWN ETF {instrument.symbol}. "
                    "Refusing to silently default to domestic 10% limit."
                )

        return PriceLimitClass.ORDINARY_TEN_PERCENT


