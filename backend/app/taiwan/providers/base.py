"""Base definitions and unit contracts for Taiwan market data providers.

Core principles:
  - Source volume unit must be explicitly declared (never guess between shares and lots).
  - Source amount unit must be explicitly declared (or marked UNAVAILABLE).
  - Price semantics must be explicitly declared (RAW vs ADJUSTED).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

import polars as pl

from app.data_providers.base import AssetType


class VolumeUnit(str, Enum):
    """Source volume units.

    Every provider MUST explicitly declare its native volume unit.
    Normalizers convert all incoming data deterministically to SHARES.
    """
    SHARES = "shares"  # 股 (TWSE official, FinMind, Yahoo)
    LOTS = "lots"      # 張 (1 lot = 1,000 shares)


class AmountUnit(str, Enum):
    """Source amount (turnover) units."""
    TWD = "TWD"                    # 元 (新台幣)
    THOUSAND_TWD = "thousand_TWD"  # 千元
    MILLION_TWD = "million_TWD"    # 百萬元
    UNAVAILABLE = "unavailable"    # Source does not provide turnover (e.g. Yahoo)


class PriceSemantics(str, Enum):
    """Whether prices are unadjusted (raw) or split/dividend-adjusted."""
    RAW = "raw"
    ADJUSTED = "adjusted"


@dataclass(frozen=True)
class SourceMetadata:
    """Metadata describing a specific provider's data semantics."""
    source_name: str
    volume_unit: VolumeUnit
    amount_unit: AmountUnit
    price_semantics: PriceSemantics
    rate_limit_rpm: int = 60
    supports_history: bool = True
    supports_etf: bool = True
    supports_tpex: bool = True


PROVENANCE_COLS = [
    "provider", "source", "source_url", "retrieved_at", "trade_date", "status",
]


class TaiwanSourceAdapter(Protocol):
    """Protocol for single-source Taiwan data adapters."""
    metadata: SourceMetadata

    def fetch_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType = "stock",
    ) -> pl.DataFrame:
        """Fetch daily raw records from this source."""
        ...
