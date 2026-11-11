"""Taiwan Data Enrichment Package (三大法人、融資融券、官方快照與市場指數).

Exported symbols:
  - SourceMeta, DatasetType, StalePolicy, EtfCategory
  - InstitutionalFlow, TaiwanInstitutionalProvider, TwseInstitutionalAdapter, TpexInstitutionalAdapter
  - MarginTrading, TaiwanMarginProvider, TwseMarginAdapter, TpexMarginAdapter
  - MarketIndex, TaiwanIndexProvider
"""
from __future__ import annotations

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
    ClassificationSource,
    DatasetType,
    EtfCategory,
    InstitutionalFlow,
    MarginTrading,
    MarketIndex,
    SourceMeta,
    StalePolicy,
)

__all__ = [
    "ClassificationSource",
    "DatasetType",
    "EtfCategory",
    "InstitutionalFlow",
    "MarginTrading",
    "MarketIndex",
    "SourceMeta",
    "StalePolicy",
    "TaiwanIndexProvider",
    "TaiwanInstitutionalProvider",
    "TaiwanMarginProvider",
    "TpexInstitutionalAdapter",
    "TpexMarginAdapter",
    "TwseInstitutionalAdapter",
    "TwseMarginAdapter",
]
