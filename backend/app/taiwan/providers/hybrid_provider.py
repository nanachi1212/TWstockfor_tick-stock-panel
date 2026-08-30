"""Taiwan Hybrid Market Data Provider.

Implements the MarketDataProvider protocol:
  - Base protocol: app.data_providers.base.MarketDataProvider
  - Primary provider for daily: FinMind (official volume alignment + turnover TWD)
  - Secondary fallback for daily: Yahoo Finance (long history coverage)
  - Canonical domain symbols enforced at boundaries.
"""
from __future__ import annotations

import logging
from datetime import datetime

import polars as pl

from app.data_providers.base import AssetType, ProviderCapabilities
from app.taiwan.providers.finmind_provider import FinMindAdapter
from app.taiwan.providers.yahoo_provider import YahooFinanceAdapter
from app.taiwan.symbol import Exchange, TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)


class TaiwanHybridProvider:
    """Unified Taiwan Market Data Provider conforming to MarketDataProvider protocol."""
    name = "taiwan"
    capabilities = ProviderCapabilities(
        instruments=True,
        daily=True,
        adj_factor=False,
        minute=False,
        realtime=False,
        financial=False,
    )

    def __init__(
        self,
        finmind_token: str = "",
        primary: str = "finmind",
    ) -> None:
        self.finmind = FinMindAdapter(token=finmind_token)
        self.yahoo = YahooFinanceAdapter()
        self.primary = primary.lower()

    def get_instruments(self, asset_type: AssetType = "stock") -> pl.DataFrame:
        """Return canonical instruments dataframe from official TaiwanSecurityMaster.

        Production path uses authoritative TWSE + TPEx security master.
        """
        from app.taiwan.universe import get_security_master

        master = get_security_master()
        return master.to_provider_dataframe(asset_type=asset_type)


    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: AssetType = "stock",
        on_chunk_done: object | None = None,  # noqa: ARG002
    ) -> pl.DataFrame:
        """Fetch normalized daily K rows with primary -> fallback orchestration."""
        if not symbols:
            return pl.DataFrame()

        # Parse into canonical TaiwanSymbols
        canonical_symbols: list[TaiwanSymbol] = []
        for s in symbols:
            try:
                canonical_symbols.append(parse_symbol(s))
            except Exception as e:
                logger.warning("Skipping invalid Taiwan symbol %r: %s", s, e)

        if not canonical_symbols:
            return pl.DataFrame()

        primary_adapter = self.finmind if self.primary == "finmind" else self.yahoo
        fallback_adapter = self.yahoo if self.primary == "finmind" else self.finmind

        # Try primary
        try:
            df = primary_adapter.fetch_daily(
                canonical_symbols,
                start_time=start_time,
                end_time=end_time,
                asset_type=asset_type,
            )
            if not df.is_empty():
                return df
        except Exception as e:
            logger.warning("Primary adapter %s failed: %s; trying fallback", primary_adapter.metadata.source_name, e)

        # Fallback
        try:
            logger.info("Using fallback adapter %s", fallback_adapter.metadata.source_name)
            return fallback_adapter.fetch_daily(
                canonical_symbols,
                start_time=start_time,
                end_time=end_time,
                asset_type=asset_type,
            )
        except Exception as e:
            logger.error("Fallback adapter %s also failed: %s", fallback_adapter.metadata.source_name, e)
            return pl.DataFrame()

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: AssetType = "stock",
    ) -> pl.DataFrame:
        """Return empty adjustment factors (Phase 2 records raw price semantics)."""
        return pl.DataFrame()

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: AssetType = "stock",
        freq: str = "1m",
        on_chunk_done: object | None = None,
    ) -> pl.DataFrame:
        return pl.DataFrame()

    def get_realtime(
        self,
        universes: list[str] | None = None,
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        return pl.DataFrame()
