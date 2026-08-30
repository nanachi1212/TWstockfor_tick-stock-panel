"""Taiwan market modules.

This package contains Taiwan-specific market abstractions:
  - symbol: Canonical symbol representation and provider conversion
  - market_rules: Trading rules, fees, taxes, limits, tick sizes, market profiles
  - providers: TWSE / TPEx / FinMind / Yahoo adapters
  - universe: Official Security Master & dynamic universe generation
"""
from __future__ import annotations

from app.taiwan.market_rules import (
    BacktestMode,
    EXAMPLE_BROKER_MIN_COMMISSION,
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
from app.taiwan.universe import (
    MarketProfileBridge,
    TaiwanInstrument,
    TaiwanSecurityMaster,
    UniverseType,
    get_security_master,
)

__all__ = [
    "BacktestMode",
    "EXAMPLE_BROKER_MIN_COMMISSION",
    "LotModel",
    "MarketProfileBridge",
    "PriceLimitClass",
    "PriceLimitModel",
    "RegulatoryRuleUnavailableError",
    "SecuritiesTaxModel",
    "SettlementModel",
    "TaiwanInstrument",
    "TaiwanMarketProfile",
    "TaiwanSecurityMaster",
    "TaxClass",
    "TickSizeClass",
    "TickSizeModel",
    "TradingCostModel",
    "UniverseType",
    "get_security_master",
]
