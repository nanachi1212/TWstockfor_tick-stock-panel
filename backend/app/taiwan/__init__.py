"""Taiwan market modules.

This package contains Taiwan-specific market abstractions:
  - symbol: Canonical symbol representation and provider conversion
  - market_rules: Trading rules, fees, taxes, limits, tick sizes, market profiles
  - providers: TWSE / TPEx / FinMind / Yahoo adapters
  - universe: Official Security Master & dynamic universe generation
"""
from __future__ import annotations

from app.taiwan.enrichment import (
    EtfCategory,
    InstitutionalFlow,
    MarginTrading,
    MarketIndex,
    SourceMeta,
    StalePolicy,
    TaiwanIndexProvider,
    TaiwanInstitutionalProvider,
    TaiwanMarginProvider,
)
from app.taiwan.market_rules import (
    EXAMPLE_BROKER_MIN_COMMISSION,
    BacktestMode,
    LotModel,
    PriceLimitClass,
    PriceLimitModel,
    RegulatoryRuleUnavailableError,
    SecuritiesTaxModel,
    SettlementModel,
    TaiwanMarketProfile,
    TaxClass,
    TickSizeClass,
    TickSizeModel,
    TradingCostModel,
)
from app.taiwan.realtime import (
    MarketStatus,
    RealtimeStatus,
    TaiwanRealtimeQuote,
    TaiwanRealtimeService,
    TwseMisRealtimeProvider,
    YahooRealtimeProvider,
    get_market_status,
    get_realtime_service,
    taipei_now,
    taipei_today,
)
from app.taiwan.universe import (
    MarketProfileBridge,
    TaiwanInstrument,
    TaiwanSecurityMaster,
    UniverseType,
    get_security_master,
)

__all__ = [
    "EXAMPLE_BROKER_MIN_COMMISSION",
    "BacktestMode",
    "EtfCategory",
    "InstitutionalFlow",
    "LotModel",
    "MarginTrading",
    "MarketIndex",
    "MarketProfileBridge",
    "MarketStatus",
    "PriceLimitClass",
    "PriceLimitModel",
    "RealtimeStatus",
    "RegulatoryRuleUnavailableError",
    "SecuritiesTaxModel",
    "SettlementModel",
    "SourceMeta",
    "StalePolicy",
    "TaiwanIndexProvider",
    "TaiwanInstitutionalProvider",
    "TaiwanInstrument",
    "TaiwanMarginProvider",
    "TaiwanMarketProfile",
    "TaiwanRealtimeQuote",
    "TaiwanRealtimeService",
    "TaiwanSecurityMaster",
    "TaxClass",
    "TickSizeClass",
    "TickSizeModel",
    "TradingCostModel",
    "TwseMisRealtimeProvider",
    "UniverseType",
    "YahooRealtimeProvider",
    "get_market_status",
    "get_realtime_service",
    "get_security_master",
    "taipei_now",
    "taipei_today",
]
