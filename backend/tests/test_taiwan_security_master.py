"""Unit tests for Taiwan Security Master Service & Universes.

Covers:
  - TWSE + TPEx merging and duplicate handling
  - Parquet cache persistence and loading
  - Universe generation (TAIWAN_ALL, TWSE_ALL, TPEX_ALL, STOCKS, ETFS)
  - Multi-attribute search (code, canonical symbol, Traditional Chinese name)
  - Market Profile Metadata Bridge
  - Removal of Phase 2 hardcoded bootstrap list
  - Golden symbol availability (2330.TWSE, 0050.TWSE, 8069.TPEX)
"""
from __future__ import annotations

from pathlib import Path
import polars as pl
import pytest

from app.data_providers.registry import get_provider
from app.taiwan.market_rules import PriceLimitClass, TaxClass, TickSizeClass
from app.taiwan.universe import (
    MarketProfileBridge,
    TaiwanSecurityMaster,
    UniverseType,
)
from app.taiwan.universe.adapters import TpexInstrumentAdapter, TwseInstrumentAdapter
from tests.test_taiwan_instruments import TPEX_SAMPLE_HTML, TWSE_SAMPLE_HTML


class _MockTwseAdapter(TwseInstrumentAdapter):
    def get_instruments(self, html_content: str | None = None):
        return super().get_instruments(html_content or TWSE_SAMPLE_HTML)


class _MockTpexAdapter(TpexInstrumentAdapter):
    def get_instruments(self, html_content: str | None = None):
        return super().get_instruments(html_content or TPEX_SAMPLE_HTML)


@pytest.fixture
def offline_master(tmp_path: Path) -> TaiwanSecurityMaster:
    cache_file = tmp_path / "test_sec_master.parquet"
    master = TaiwanSecurityMaster(
        cache_path=cache_file,
        twse_adapter=_MockTwseAdapter(),
        tpex_adapter=_MockTpexAdapter(),
    )
    master.load_from_adapters()
    return master


class TestTaiwanSecurityMasterOffline:
    """Offline unit tests for TaiwanSecurityMaster orchestrator."""

    def test_merge_and_deduplication(self, offline_master: TaiwanSecurityMaster):
        df = offline_master.to_dataframe()
        symbols = df["symbol"].to_list()
        assert len(symbols) == len(set(symbols)), "Canonical symbols must be strictly unique"
        assert "2330.TWSE" in symbols
        assert "8069.TPEX" in symbols
        assert "0050.TWSE" in symbols

    def test_persistence_cache_roundtrip(self, offline_master: TaiwanSecurityMaster, tmp_path: Path):
        save_file = tmp_path / "saved_master.parquet"
        offline_master.save_cache(save_file)
        assert save_file.exists()

        new_master = TaiwanSecurityMaster(cache_path=save_file)
        success = new_master.load_cache()
        assert success is True
        assert new_master.get_instrument("2330.TWSE") is not None
        assert new_master.get_instrument("2330.TWSE").name == "台積電"

    def test_universe_generation(self, offline_master: TaiwanSecurityMaster):
        all_univ = offline_master.get_universe(UniverseType.TAIWAN_ALL)
        twse_univ = offline_master.get_universe(UniverseType.TWSE_ALL)
        tpex_univ = offline_master.get_universe(UniverseType.TPEX_ALL)
        stock_univ = offline_master.get_universe(UniverseType.TAIWAN_STOCKS)
        etf_univ = offline_master.get_universe(UniverseType.TAIWAN_ETFS)

        # Unsupported securities (warrants, pref shares) must be excluded from universes
        assert "052330.TWSE" not in all_univ
        assert "1101B.TWSE" not in all_univ

        # Supported active securities included
        assert "2330.TWSE" in all_univ
        assert "8069.TPEX" in all_univ
        assert "0050.TWSE" in all_univ

        # Market filters
        assert "2330.TWSE" in twse_univ
        assert "8069.TPEX" not in twse_univ
        assert "8069.TPEX" in tpex_univ

        # Category filters
        assert "2330.TWSE" in stock_univ
        assert "0050.TWSE" not in stock_univ
        assert "0050.TWSE" in etf_univ

    def test_multi_attribute_search(self, offline_master: TaiwanSecurityMaster):
        # 1. Search by stock code
        res_2330 = offline_master.search("2330")
        assert len(res_2330) > 0
        assert res_2330[0]["symbol"] == "2330.TWSE"

        # 2. Search by canonical symbol
        res_sym = offline_master.search("8069.TPEX")
        assert len(res_sym) > 0
        assert res_sym[0]["symbol"] == "8069.TPEX"

        # 3. Search by Traditional Chinese name
        res_name = offline_master.search("元太")
        assert len(res_name) > 0
        assert res_name[0]["symbol"] == "8069.TPEX"

        res_etf = offline_master.search("元大台灣50")
        assert len(res_etf) > 0
        assert res_etf[0]["symbol"] == "0050.TWSE"

    def test_market_profile_metadata_bridge(self, offline_master: TaiwanSecurityMaster):
        tsmc = offline_master.get_instrument("2330.TWSE")
        assert tsmc is not None
        assert MarketProfileBridge.get_tax_class(tsmc) == TaxClass.ORDINARY_STOCK
        assert MarketProfileBridge.get_tick_size_class(tsmc) == TickSizeClass.ORDINARY_STOCK
        assert MarketProfileBridge.get_price_limit_class(tsmc) == PriceLimitClass.ORDINARY_TEN_PERCENT

        etf = offline_master.get_instrument("0050.TWSE")
        assert etf is not None
        assert MarketProfileBridge.get_tax_class(etf) == TaxClass.DOMESTIC_ETF
        assert MarketProfileBridge.get_tick_size_class(etf) == TickSizeClass.ETF

    def test_bootstrap_list_removed_from_hybrid_provider(self):
        """Verify TaiwanHybridProvider.get_instruments() queries Security Master."""
        provider = get_provider("taiwan")
        df = provider.get_instruments("stock")
        assert isinstance(df, pl.DataFrame)
        # Output columns must follow standard provider contract
        expected_cols = {"symbol", "name", "code", "exchange", "asset_type", "source", "list_date", "status", "currency", "lot_size"}
        assert set(df.columns) == expected_cols
        # Production master contains far more than the 6 hardcoded bootstrap items
        assert df.height > 6, f"Expected full universe, got {df.height} items"
