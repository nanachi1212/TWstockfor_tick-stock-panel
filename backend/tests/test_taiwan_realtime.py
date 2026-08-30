"""Offline Unit Tests for Taiwan Realtime Data Layer (Phase 5A).

Tests all components strictly offline with simulated fixtures:
  - Canonical/MIS/Yahoo symbol conversions
  - Deterministic volume normalization (lots * 1000 -> shares)
  - Five-tier depth parsing
  - Market operating schedule & timezone handling
  - Multi-tier fallback chain (Primary -> Secondary -> Snapshot -> Daily)
  - Non-silent fallback enforcement
  - Freshness / stale threshold validation
  - In-memory cache & batch querying
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
import pytest

from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import (
    TAIPEI_TZ,
    get_market_status,
    taipei_now,
)
from app.taiwan.realtime.mis_provider import (
    TwseMisRealtimeProvider,
    _parse_depth_tier,
    to_mis_channel,
)
from app.taiwan.realtime.models import (
    MarketStatus,
    RealtimeStatus,
    TaiwanRealtimeQuote,
)
from app.taiwan.realtime.service import TaiwanRealtimeService
from app.taiwan.realtime.yahoo_provider import (
    YahooRealtimeProvider,
    to_yahoo_ticker,
)
from app.taiwan.symbol import parse_symbol


# ── 1. Symbol & Channel Conversion Tests ─────────────────────────


class TestRealtimeSymbolConversions:
    def test_to_mis_channel(self):
        assert to_mis_channel("2330.TWSE") == "tse_2330.tw"
        assert to_mis_channel("0050.TWSE") == "tse_0050.tw"
        assert to_mis_channel("8069.TPEX") == "otc_8069.tw"
        assert to_mis_channel("00720B.TPEX") == "otc_00720B.tw"

    def test_to_yahoo_ticker(self):
        assert to_yahoo_ticker("2330.TWSE") == "2330.TW"
        assert to_yahoo_ticker("0050.TWSE") == "0050.TW"
        assert to_yahoo_ticker("8069.TPEX") == "8069.TWO"
        assert to_yahoo_ticker("00720B.TPEX") == "00720B.TWO"


# ── 2. Market Calendar & Operating Status Tests ──────────────────


class TestTaiwanMarketCalendar:
    def test_weekend_is_non_trading_day(self):
        # Saturday
        sat = datetime(2026, 8, 29, 10, 0, tzinfo=TAIPEI_TZ)
        assert get_market_status(sat) == MarketStatus.NON_TRADING_DAY
        # Sunday
        sun = datetime(2026, 8, 30, 10, 0, tzinfo=TAIPEI_TZ)
        assert get_market_status(sun) == MarketStatus.NON_TRADING_DAY

    def test_statutory_holiday(self):
        holiday = date(2026, 10, 10)
        dt = datetime(2026, 10, 10, 10, 0, tzinfo=TAIPEI_TZ)
        assert get_market_status(dt, holidays={holiday}) == MarketStatus.NON_TRADING_DAY

    def test_extraordinary_typhoon_closure(self):
        """Extraordinary closure (e.g. Typhoon day) must yield NON_TRADING_DAY, never falsely OPEN."""
        from app.taiwan.realtime.calendar import TaiwanTradingCalendar
        typhoon_day = date(2026, 7, 24)
        cal = TaiwanTradingCalendar(known_holidays={typhoon_day})
        dt = datetime(2026, 7, 24, 10, 30, tzinfo=TAIPEI_TZ)
        assert cal.get_market_status(dt) == MarketStatus.NON_TRADING_DAY

    def test_unverified_trading_day_safety(self):
        """When strict verification is requested and date is not confirmed, return SCHEDULED_OPEN_UNVERIFIED."""
        from app.taiwan.realtime.calendar import TaiwanTradingCalendar
        cal = TaiwanTradingCalendar()  # Empty calendar (no verified dates)
        # Wednesday regular hours
        dt = datetime(2026, 8, 26, 10, 0, tzinfo=TAIPEI_TZ)
        assert cal.get_market_status(dt, require_verified_trading_day=True) == MarketStatus.SCHEDULED_OPEN_UNVERIFIED

    def test_verified_trading_day_returns_open(self):
        """When date is confirmed in known_trading_days, return OPEN during session."""
        from app.taiwan.realtime.calendar import TaiwanTradingCalendar
        trading_day = date(2026, 8, 26)
        cal = TaiwanTradingCalendar(known_trading_days={trading_day})
        dt = datetime(2026, 8, 26, 10, 0, tzinfo=TAIPEI_TZ)
        assert cal.get_market_status(dt, require_verified_trading_day=True) == MarketStatus.OPEN

    def test_trading_day_sessions(self):
        # Wednesday (weekday)
        base = datetime(2026, 8, 26, 0, 0, tzinfo=TAIPEI_TZ)
        # 07:00 -> CLOSED
        assert get_market_status(base.replace(hour=7, minute=0)) == MarketStatus.CLOSED
        # 08:45 -> PRE_OPEN
        assert get_market_status(base.replace(hour=8, minute=45)) == MarketStatus.PRE_OPEN
        # 09:30 -> OPEN
        assert get_market_status(base.replace(hour=9, minute=30)) == MarketStatus.OPEN
        # 13:20 -> OPEN
        assert get_market_status(base.replace(hour=13, minute=20)) == MarketStatus.OPEN
        # 13:45 -> POST_CLOSE
        assert get_market_status(base.replace(hour=13, minute=45)) == MarketStatus.POST_CLOSE
        # 15:00 -> CLOSED
        assert get_market_status(base.replace(hour=15, minute=0)) == MarketStatus.CLOSED



# ── 3. MIS Provider Parsing & Volume Normalization Tests ─────────


class TestTwseMisProviderParsing:
    def test_volume_and_depth_unit_normalization(self):
        """Lots from MIS must be deterministically multiplied by 1,000 to shares."""
        mock_mis_json = {
            "msgArray": [
                {
                    "c": "2330",
                    "n": "台積電",
                    "ch": "tse_2330.tw",
                    "z": "2420.0000",
                    "y": "2410.0000",
                    "o": "2440.0000",
                    "h": "2445.0000",
                    "l": "2410.0000",
                    "v": "13498",  # 13,498 LOTS (張)
                    "t": "13:30:00",
                    "d": "20260828",
                    "b": "2420.0000_2415.0000_",
                    "g": "50_100_",  # 50 and 100 LOTS
                    "a": "2425.0000_2430.0000_",
                    "f": "20_80_",   # 20 and 80 LOTS
                }
            ]
        }
        provider = TwseMisRealtimeProvider()
        quotes = provider.fetch_quotes(["2330.TWSE"], mock_response_json=mock_mis_json)
        assert "2330.TWSE" in quotes
        q = quotes["2330.TWSE"]

        assert q.last_price == 2420.0
        assert q.prev_close == 2410.0
        assert q.change == 10.0
        assert round(q.change_pct, 4) == round((10.0 / 2410.0) * 100, 4)

        # Cumulative volume must be 13,498,000 shares
        assert q.volume == 13498 * 1000

        # Depth volumes must be in shares
        assert q.bid_price == 2420.0
        assert q.bid_volume == 50 * 1000
        assert q.bids == [(2420.0, 50000), (2415.0, 100000)]

        assert q.ask_price == 2425.0
        assert q.ask_volume == 20 * 1000
        assert q.asks == [(2425.0, 20000), (2430.0, 80000)]

        assert q.quote_time == datetime(2026, 8, 28, 13, 30, 0, tzinfo=TAIPEI_TZ)
        assert q.source_meta.source == "twse:mis"

    def test_depth_tier_parser_empty(self):
        assert _parse_depth_tier(None, None) == []
        assert _parse_depth_tier("", "") == []

    def test_mis_is_best_effort_and_observed_latency(self):
        """MIS must be labeled best_effort with no contractual SLA, and observed latency recorded."""
        mock_mis = {
            "msgArray": [
                {"c": "2330", "n": "台積電", "ch": "tse_2330.tw", "z": "2420.0", "y": "2410.0", "d": "20260828", "t": "13:30:00"}
            ]
        }
        provider = TwseMisRealtimeProvider()
        quotes = provider.fetch_quotes(["2330.TWSE"], mock_response_json=mock_mis)
        meta = quotes["2330.TWSE"].source_meta
        assert meta.is_best_effort is True
        assert meta.documented_sla is False
        assert meta.source_type == "first_party_web_endpoint"
        assert meta.freshness_class == "best_effort_near_realtime"
        assert meta.observed_latency_ms is not None

    def test_quote_timestamp_missing_fails_safely(self):
        """When MIS d/t is missing, do NOT substitute datetime.now(); mark stale safely."""
        mock_mis = {
            "msgArray": [
                {"c": "2330", "n": "台積電", "ch": "tse_2330.tw", "z": "2420.0", "y": "2410.0"}  # No d or t!
            ]
        }
        provider = TwseMisRealtimeProvider()
        quotes = provider.fetch_quotes(["2330.TWSE"], mock_response_json=mock_mis)
        q = quotes["2330.TWSE"]
        assert q.quote_time is None
        assert q.source_meta.is_stale is True
        assert q.source_meta.status == RealtimeStatus.STALE.value
        assert "refused to fake with local time" in q.source_meta.fallback_reason

    def test_quote_timestamp_malformed_fails_safely(self):
        """When MIS time is corrupted/malformed, refuse to guess; mark stale."""
        mock_mis = {
            "msgArray": [
                {"c": "2330", "n": "台積電", "ch": "tse_2330.tw", "z": "2420.0", "y": "2410.0", "d": "bad_date", "t": "bad_time"}
            ]
        }
        provider = TwseMisRealtimeProvider()
        quotes = provider.fetch_quotes(["2330.TWSE"], mock_response_json=mock_mis)
        q = quotes["2330.TWSE"]
        assert q.quote_time is None
        assert q.source_meta.is_stale is True
        assert q.source_meta.status == RealtimeStatus.STALE.value



# ── 4. Multi-Tier Fallback Chain Tests ───────────────────────────


class TestRealtimeFallbackChain:
    def test_primary_mis_success(self):
        """Primary provider succeeds -> source='twse:mis'."""
        mock_mis = {
            "msgArray": [
                {
                    "c": "0050",
                    "n": "元大台灣50",
                    "ch": "tse_0050.tw",
                    "z": "106.95",
                    "y": "106.05",
                    "v": "5000",
                    "d": "20260828",
                    "t": "13:30:00",
                }
            ]
        }
        mis = TwseMisRealtimeProvider()
        orig_fetch = mis.fetch_quotes
        mis.fetch_quotes = lambda syms: orig_fetch(syms, mock_response_json=mock_mis)
        svc = TaiwanRealtimeService(mis_provider=mis)

        quotes = svc.get_quotes(["0050.TWSE"])
        assert "0050.TWSE" in quotes
        q = quotes["0050.TWSE"]
        assert q.last_price == 106.95
        assert q.volume == 5000000
        assert q.source_meta.source == "twse:mis"
        assert q.source_meta.fallback_reason is None

    def test_secondary_yahoo_fallback(self):
        """Primary fails -> Secondary Yahoo succeeds with non-silent fallback metadata."""
        mis = TwseMisRealtimeProvider()
        mis.fetch_quotes = lambda syms: {}  # primary fails

        yahoo = YahooRealtimeProvider()
        orig_single = yahoo.fetch_single_quote
        mock_yahoo = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 160.5,
                            "chartPreviousClose": 159.0,
                            "regularMarketVolume": 5122000,
                            "regularMarketTime": 1756359000,
                            "shortName": "E-INK",
                        }
                    }
                ]
            }
        }
        yahoo.fetch_single_quote = lambda sym, mock_response_json=None: orig_single(
            sym, mock_response_json=mock_yahoo
        )

        svc = TaiwanRealtimeService(mis_provider=mis, yahoo_provider=yahoo)
        quotes = svc.get_quotes(["8069.TPEX"])
        assert "8069.TPEX" in quotes
        q = quotes["8069.TPEX"]
        assert q.last_price == 160.5
        assert q.source_meta.source == "yahoo:chart"
        assert q.source_meta.status == RealtimeStatus.FALLBACK.value
        assert "Primary MIS unavailable" in q.source_meta.fallback_reason


    def test_daily_kline_last_resort_fallback(self):
        """All realtime providers fail -> fallback to latest daily with is_stale=True."""
        mis = TwseMisRealtimeProvider()
        mis.fetch_quotes = lambda syms: {}
        yahoo = YahooRealtimeProvider()
        yahoo.fetch_quotes = lambda syms: {}

        mock_daily_store = {
            "2330.TWSE": {
                "close": 2420.0,
                "prev_close": 2410.0,
                "open": 2440.0,
                "high": 2445.0,
                "low": 2410.0,
                "change": 10.0,
                "change_pct": 0.41,
                "volume": 13498000,
                "date": date(2026, 8, 28),
            }
        }

        svc = TaiwanRealtimeService(mis_provider=mis, yahoo_provider=yahoo)
        quotes = svc.get_quotes(
            ["2330.TWSE"],
            daily_kline_fallback_fn=lambda s: mock_daily_store.get(s),
        )
        assert "2330.TWSE" in quotes
        q = quotes["2330.TWSE"]
        assert q.last_price == 2420.0
        assert q.source_meta.source == "daily_kline"
        assert q.source_meta.status == RealtimeStatus.DAILY_FALLBACK.value
        assert q.source_meta.is_stale is True
        assert "fell back to latest Daily K" in q.source_meta.fallback_reason


# ── 5. In-Memory Caching & Batch Querying Tests ───────────────────


class TestRealtimeCachingAndBatch:
    def test_cache_hits_and_force_refresh(self):
        """Verify cache returns identical instance within TTL without hitting provider."""
        mock_mis = {
            "msgArray": [
                {
                    "c": "2330",
                    "n": "台積電",
                    "ch": "tse_2330.tw",
                    "z": "2420.0",
                    "y": "2410.0",
                    "v": "1000",
                    "d": "20260828",
                    "t": "13:30:00",
                }
            ]
        }
        mis = TwseMisRealtimeProvider()
        orig_fetch = mis.fetch_quotes
        mis.fetch_quotes = lambda syms: orig_fetch(syms, mock_response_json=mock_mis)

        svc = TaiwanRealtimeService(cache_ttl_seconds=5.0, mis_provider=mis)

        # 1. First fetch -> Cache miss
        q1 = svc.get_quote("2330.TWSE")
        assert q1 is not None
        assert svc.cache_misses == 1
        assert svc.cache_hits == 0
        assert svc.provider_requests == 1

        # 2. Second fetch within TTL -> Cache hit
        q2 = svc.get_quote("2330.TWSE")
        assert q2 is not None
        assert svc.cache_hits == 1
        assert svc.provider_requests == 1

        # 3. Force refresh -> Bypasses cache
        q3 = svc.get_quote("2330.TWSE", force_refresh=True)
        assert q3 is not None
        assert svc.cache_misses == 2
        assert svc.provider_requests == 2

    def test_configurable_freshness_threshold(self):
        """Verify RealtimeFreshnessPolicy properly distinguishes MIS vs Yahoo thresholds."""
        from app.taiwan.realtime.models import RealtimeFreshnessPolicy

        policy = RealtimeFreshnessPolicy(
            mis_stale_threshold_seconds=10.0,
            yahoo_stale_threshold_seconds=300.0,
        )
        assert policy.get_threshold_for_source("twse:mis") == 10.0
        assert policy.get_threshold_for_source("yahoo:chart") == 300.0
        assert policy.get_threshold_for_source("twse:stock_day_all") == 86400.0

    def test_cache_across_market_status_transition(self):
        """When market transitions from closed to open, old closed session cache must invalidate safely."""
        from app.taiwan.realtime.calendar import MarketStatus
        from unittest.mock import patch

        mock_mis = {
            "msgArray": [
                {"c": "2330", "n": "台積電", "ch": "tse_2330.tw", "z": "2420.0", "y": "2410.0", "d": "20260828", "t": "13:30:00"}
            ]
        }
        mis = TwseMisRealtimeProvider()
        orig_fetch = mis.fetch_quotes
        mis.fetch_quotes = lambda syms: orig_fetch(syms, mock_response_json=mock_mis)
        svc = TaiwanRealtimeService(cache_ttl_seconds=60.0, mis_provider=mis)

        # 1. First fetch in CLOSED session
        with patch("app.taiwan.realtime.service.get_market_status", return_value=MarketStatus.CLOSED):
            q_closed = svc.get_quote("2330.TWSE")
            assert q_closed is not None
            assert svc.cache_misses == 1
            assert svc.cache_hits == 0

            # Second fetch still in CLOSED -> Cache hit!
            q_closed_2 = svc.get_quote("2330.TWSE")
            assert svc.cache_hits == 1

        # 2. Market transitions to OPEN -> Cache miss must occur (no leakage of closed snapshot)
        with patch("app.taiwan.realtime.service.get_market_status", return_value=MarketStatus.OPEN):
            q_open = svc.get_quote("2330.TWSE")
            assert q_open is not None
            # Misses incremented because (2330.TWSE, 'open') was not in cache
            assert svc.cache_misses == 2

    def test_partial_mis_batch_response_single_symbol_fallback(self):
        """When MIS returns 2330 but misses 8069, 8069 must seamlessly fallback to Yahoo without dropping 2330."""
        # MIS only returns 2330!
        mock_mis = {
            "msgArray": [
                {"c": "2330", "n": "台積電", "ch": "tse_2330.tw", "z": "2420.0", "y": "2410.0", "v": "1000", "d": "20260828", "t": "13:30:00"}
            ]
        }
        mis = TwseMisRealtimeProvider()
        orig_mis_fetch = mis.fetch_quotes
        mis.fetch_quotes = lambda syms: orig_mis_fetch(syms, mock_response_json=mock_mis)

        # Yahoo handles 8069!
        mock_yahoo_8069 = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 160.5,
                            "chartPreviousClose": 159.0,
                            "regularMarketVolume": 5122000,
                            "regularMarketTime": 1756359000,
                            "shortName": "E-INK",
                        }
                    }
                ]
            }
        }
        yahoo = YahooRealtimeProvider()
        orig_single = yahoo.fetch_single_quote
        yahoo.fetch_single_quote = lambda sym, mock_response_json=None: orig_single(
            sym, mock_response_json=mock_yahoo_8069
        )

        svc = TaiwanRealtimeService(mis_provider=mis, yahoo_provider=yahoo)
        results = svc.get_quotes(["2330.TWSE", "8069.TPEX"])

        assert len(results) == 2
        # 2330 from MIS
        assert results["2330.TWSE"].source_meta.source == "twse:mis"
        assert results["2330.TWSE"].last_price == 2420.0
        # 8069 seamlessly fell back to Yahoo
        assert results["8069.TPEX"].source_meta.source == "yahoo:chart"
        assert results["8069.TPEX"].source_meta.status == RealtimeStatus.FALLBACK.value
        assert results["8069.TPEX"].last_price == 160.5

