"""Taiwan Hybrid Market Data Provider.

Implements the MarketDataProvider protocol:
  - Base protocol: app.data_providers.base.MarketDataProvider
  - Primary provider for daily: TWSE/TPEx official endpoints
  - Secondary fallback: FinMind, then Yahoo Finance
  - Canonical domain symbols enforced at boundaries.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import polars as pl

from app.data_providers.base import AssetType, ProviderCapabilities
from app.taiwan.providers.finmind_provider import FinMindAdapter
from app.taiwan.providers.official_provider import OfficialTaiwanAdapter
from app.taiwan.providers.yahoo_provider import YahooFinanceAdapter
from app.taiwan.symbol import TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)


class TaiwanHybridProvider:
    """Unified Taiwan Market Data Provider conforming to MarketDataProvider protocol."""
    name = "taiwan"
    capabilities = ProviderCapabilities(
        instruments=True,
        daily=True,
        adj_factor=False,
        minute=False,
        realtime=True,
        financial=True,
    )

    def __init__(
        self,
        finmind_token: str = "",
        primary: str = "official",
    ) -> None:
        self.finmind = FinMindAdapter(token=finmind_token)
        self.yahoo = YahooFinanceAdapter()
        self.official = OfficialTaiwanAdapter()
        self.primary = primary.lower()

    def get_fundamentals(
        self,
        symbol: str,
        dataset: str,
        *,
        exchange: str,
        security_type: str = "stock",
        start_date: date | None = None,
        end_date: date | None = None,
    ):
        """Use the authoritative Taiwan fundamentals seam without changing factors."""
        from app.taiwan.fundamentals import TaiwanOfficialFundamentals

        provider = TaiwanOfficialFundamentals()
        if dataset == "monthly_revenue":
            return provider.monthly_revenue(symbol, exchange, security_type=security_type)
        if dataset == "financial_statement":
            return provider.financial_statement(symbol, exchange, security_type=security_type)
        if dataset == "dividend_lifecycle_event":
            if start_date is None or end_date is None:
                raise ValueError("dividend lifecycle events require start_date and end_date")
            return provider.dividend_lifecycle_events(
                symbol,
                exchange,
                start_date,
                end_date,
                security_type=security_type,
            )
        raise ValueError(f"unsupported Taiwan fundamentals dataset: {dataset}")

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
        on_chunk_done: object | None = None,
    ) -> pl.DataFrame:
        """Fetch normalized daily K rows with primary -> fallback orchestration."""
        if not symbols:
            return pl.DataFrame()
        del on_chunk_done

        # Parse into canonical TaiwanSymbols
        canonical_symbols: list[TaiwanSymbol] = []
        for s in symbols:
            try:
                canonical_symbols.append(parse_symbol(s))
            except Exception as e:
                logger.warning("Skipping invalid Taiwan symbol %r: %s", s, e)

        if not canonical_symbols:
            return pl.DataFrame()

        adapters = [self.official, self.finmind, self.yahoo] if self.primary == "official" else [self.finmind, self.yahoo]
        for index, adapter in enumerate(adapters):
            try:
                df = adapter.fetch_daily(
                canonical_symbols,
                start_time=start_time,
                end_time=end_time,
                asset_type=asset_type,
            )
                if not df.is_empty():
                    if index:
                        df = df.with_columns(pl.lit("third_party_fallback").alias("status"))
                    return df
            except Exception as e:
                logger.warning("Taiwan adapter %s failed: %s", adapter.metadata.source_name, e)
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
        del universes
        requested = symbols or []
        if not requested:
            return pl.DataFrame()
        try:
            quote = self.official.fetch_quote(requested)
            if not quote.is_empty():
                return quote
        except Exception as exc:
            logger.warning("Official Taiwan quote failed: %s", exc)
        fallback = self.get_daily(requested)
        if fallback.is_empty():
            return fallback
        return fallback.sort("date").group_by("symbol", maintain_order=True).tail(1).with_columns(
            pl.lit("third_party_fallback").alias("status")
        )
