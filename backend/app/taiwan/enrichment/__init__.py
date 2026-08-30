"""Taiwan Data Enrichment Package (三大法人、融資融券、官方快照與市場指數).

Exported symbols:
  - SourceMeta, DatasetType, StalePolicy, EtfCategory
  - InstitutionalFlow, TaiwanInstitutionalProvider, TwseInstitutionalAdapter, TpexInstitutionalAdapter
  - MarginTrading, TaiwanMarginProvider, TwseMarginAdapter, TpexMarginAdapter
  - MarketQuote, TaiwanOfficialQuoteProvider
  - MarketIndex, TaiwanIndexProvider
  - compute_chip_factors, compute_margin_factors
"""
from __future__ import annotations

from app.taiwan.enrichment.factors import compute_chip_factors, compute_margin_factors
from app.taiwan.enrichment.index import TaiwanIndexProvider
from app.taiwan.enrichment.institutional import (
    TaiwanInstitutionalProvider,
    TpexInstitutionalAdapter,
    TwseInstitutionalAdapter,
)
from app.taiwan.enrichment.margin import (
    TaiwanMarginProvider,
    TpexMarginAdapter,
    TwseMarginAdapter,
)
from app.taiwan.enrichment.models import (
    DatasetType,
    EtfCategory,
    InstitutionalFlow,
    MarginTrading,
    MarketIndex,
    MarketQuote,
    SourceMeta,
    StalePolicy,
)
from app.taiwan.enrichment.quote import TaiwanOfficialQuoteProvider

__all__ = [
    "DatasetType",
    "EtfCategory",
    "InstitutionalFlow",
    "MarginTrading",
    "MarketIndex",
    "MarketQuote",
    "SourceMeta",
    "StalePolicy",
    "TaiwanIndexProvider",
    "TaiwanInstitutionalProvider",
    "TaiwanMarginProvider",
    "TaiwanOfficialQuoteProvider",
    "TpexInstitutionalAdapter",
    "TpexMarginAdapter",
    "TwseInstitutionalAdapter",
    "TwseMarginAdapter",
    "compute_chip_factors",
    "compute_margin_factors",
]
