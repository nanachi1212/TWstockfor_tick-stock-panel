"""Live Network Smoke Tests for Taiwan Realtime Data Layer (Phase 5A).

Integrates against real TWSE MIS official public endpoints and Yahoo Finance.
Strictly respects non-trading-hours semantics (market closed / official snapshot).
"""
from __future__ import annotations

import pytest

from app.taiwan.realtime.calendar import get_market_status, taipei_now
from app.taiwan.realtime.mis_provider import TwseMisRealtimeProvider
from app.taiwan.realtime.models import MarketStatus, RealtimeStatus
from app.taiwan.realtime.service import TaiwanRealtimeService
from app.taiwan.realtime.yahoo_provider import YahooRealtimeProvider


@pytest.mark.integration
class TestTaiwanRealtimeLiveSmoke:
    """Network-dependent live smoke tests for Taiwan realtime quotes."""

    def test_live_mis_batch_fetch(self):
        """Verify real TWSE MIS live batch query for key instruments."""
        provider = TwseMisRealtimeProvider()
        symbols = ["2330.TWSE", "2454.TWSE", "0050.TWSE", "8069.TPEX"]
        quotes = provider.fetch_quotes(symbols)

        assert len(quotes) >= 3, f"Expected at least 3 symbols, got {len(quotes)}"

        # 1. 2330 TSMC
        if "2330.TWSE" in quotes:
            q_2330 = quotes["2330.TWSE"]
            assert q_2330.symbol == "2330.TWSE"
            assert q_2330.last_price is not None and q_2330.last_price > 500.0
            assert q_2330.prev_close is not None and q_2330.prev_close > 500.0
            assert q_2330.volume is not None and q_2330.volume > 1000
            assert q_2330.source_meta.source == "twse:mis"
            # Depth book check
            if q_2330.bids:
                assert q_2330.bid_price is not None
                assert q_2330.bid_volume is not None and q_2330.bid_volume >= 1000

        # 2. 0050 ETF
        if "0050.TWSE" in quotes:
            q_0050 = quotes["0050.TWSE"]
            assert q_0050.symbol == "0050.TWSE"
            assert q_0050.last_price is not None and q_0050.last_price > 50.0
            assert q_0050.volume is not None and q_0050.volume > 1000

        # 3. 8069 TPEx
        if "8069.TPEX" in quotes:
            q_8069 = quotes["8069.TPEX"]
            assert q_8069.symbol == "8069.TPEX"
            assert q_8069.exchange == "TPEX"
            assert q_8069.last_price is not None and q_8069.last_price > 50.0

    def test_live_service_with_caching(self):
        """Verify TaiwanRealtimeService live orchestrator and cache hit metrics."""
        svc = TaiwanRealtimeService(cache_ttl_seconds=3.0)
        syms = ["2330.TWSE", "0050.TWSE"]

        # Call 1: Cache miss -> network fetch
        q_map_1 = svc.get_quotes(syms)
        assert len(q_map_1) == 2
        assert svc.cache_hits == 0
        assert svc.cache_misses == 2
        assert svc.provider_requests == 1

        # Call 2: Within TTL -> cache hit without network request
        q_map_2 = svc.get_quotes(syms)
        assert len(q_map_2) == 2
        assert svc.cache_hits == 2
        assert svc.provider_requests == 1

    def test_live_cross_source_price_consistency(self):
        """Cross-validate price consistency between MIS and Yahoo for 2330.TWSE."""
        mis_provider = TwseMisRealtimeProvider()
        yahoo_provider = YahooRealtimeProvider()

        mis_quotes = mis_provider.fetch_quotes(["2330.TWSE"])
        yahoo_quote = yahoo_provider.fetch_single_quote("2330.TWSE")

        if "2330.TWSE" in mis_quotes and yahoo_quote is not None:
            q_mis = mis_quotes["2330.TWSE"]
            # Prices should match within reasonable tick or match exactly if post-close
            if q_mis.last_price and yahoo_quote.last_price:
                diff = abs(q_mis.last_price - yahoo_quote.last_price)
                assert diff <= 5.0, (
                    f"Price mismatch between MIS ({q_mis.last_price}) "
                    f"and Yahoo ({yahoo_quote.last_price})"
                )
