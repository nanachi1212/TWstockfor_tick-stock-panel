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
    underlying_scope: str | None = None       # "domestic" | "foreign" | "unknown"
    leverage_multiplier: float = 1.0          # e.g. 1.0, 2.0, -1.0
    currency: str = "TWD"
    lot_size: int = 1000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketProfileBridge:
    """Translates TaiwanInstrument metadata into Phase 3 Market Profile rule classes.

    Strictly refuses to apply real trading regulations (tax, price limits) if an ETF's
    classification is unconfirmed or derived solely from name heuristics, or if
    underlying_scope cannot be reliably confirmed.
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
            # Strictly audit: Only plain-vanilla bond ETFs (multiplier == 1.0) are exempt (0% tax).
            # Leveraged/Inverse bond ETFs (e.g. 00688L/00689R) are subject to statutory 0.1% tax
            # under Ministry of Finance ruling (台財稅字第10504709410號).
            if cat == EtfCategory.BOND.value and abs(instrument.leverage_multiplier - 1.0) < 1e-6:
                return TaxClass.BOND_ETF
            return TaxClass.DOMESTIC_ETF
        return TaxClass.ORDINARY_STOCK

    @classmethod
    def get_tick_size_class(cls, instrument: TaiwanInstrument) -> TickSizeClass:
        if instrument.instrument_type == "etf":
            return TickSizeClass.ETF
        return TickSizeClass.ORDINARY_STOCK

    @classmethod
    def get_price_limit_pct(cls, instrument: TaiwanInstrument) -> float | None:
        """Get authoritative price limit percentage for an instrument.

        Ordinary stocks: 0.10 (10%)
        Domestic plain ETF (0050, 006208): 0.10 (10%)
        Domestic leveraged ETF (00631L, 2X): 0.20 (20%)
        Domestic inverse ETF (00632R, -1X): 0.10 (10%)
        Foreign / Bond ETF (00646, 00720B, 00633L): None (NO_LIMIT)
        """
        if instrument.instrument_type == "stock":
            return 0.10

        if instrument.instrument_type == "etf":
            cls.verify_confirmed_etf(instrument)
            cat = instrument.etf_category or EtfCategory.UNKNOWN.value

            scope = instrument.underlying_scope or "unknown"
            if scope == "unknown":
                raise ValueError(
                    f"Cannot determine price limit for ETF {instrument.symbol} with UNKNOWN underlying scope. "
                    "Confirmed underlying scope (domestic or foreign) is required."
                )

            # Foreign-component ETFs and bond ETFs have no price limit in Taiwan
            if scope == "foreign" or cat == EtfCategory.BOND.value:
                return None

            if scope == "domestic":
                # Domestic leveraged / inverse ETF: 10% * abs(multiplier)
                return round(0.10 * abs(instrument.leverage_multiplier), 4)

            raise ValueError(f"Unrecognized underlying scope {scope!r} for ETF {instrument.symbol}.")

        return 0.10

    @classmethod
    def get_price_limit_class(cls, instrument: TaiwanInstrument) -> PriceLimitClass:
        pct = cls.get_price_limit_pct(instrument)
        if pct is None:
            return PriceLimitClass.NO_LIMIT
        if pct == 0.10 and abs(instrument.leverage_multiplier - 1.0) < 1e-6:
            return PriceLimitClass.ORDINARY_TEN_PERCENT
        return PriceLimitClass.LEVERAGED_DOMESTIC

    @classmethod
    def calc_limits(
        cls,
        ref_price: float,
        instrument: TaiwanInstrument,
    ) -> tuple[float | None, float | None]:
        """Calculate authoritative price limit boundary prices for an instrument, aligned to valid ticks."""
        pct = cls.get_price_limit_pct(instrument)
        tick_class = cls.get_tick_size_class(instrument)
        from app.taiwan.market_rules import PriceLimitModel
        return PriceLimitModel.calc_limits_for_pct(ref_price=ref_price, limit_pct=pct, tick_class=tick_class)



