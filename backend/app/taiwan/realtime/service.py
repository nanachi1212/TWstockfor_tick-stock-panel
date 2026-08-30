"""Taiwan Realtime Market Data Service.

Orchestrates multi-tier real-time data fetching, in-memory caching,
rate limiting protection, freshness / stale detection, and fallback chain.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time as dt_time, timedelta

import logging
import threading
import time
from typing import Any

from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.enrichment.quote import TaiwanOfficialQuoteProvider
from app.taiwan.realtime.calendar import (
    TAIPEI_TZ,
    TaiwanTradingCalendar,
    taipei_now,
)
from app.taiwan.realtime.mis_provider import TwseMisRealtimeProvider
from app.taiwan.realtime.models import (
    MarketStatus,
    RealtimeFreshnessPolicy,
    RealtimeStatus,
    TaiwanRealtimeQuote,
)
from app.taiwan.realtime.yahoo_provider import YahooRealtimeProvider
from app.taiwan.symbol import TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)

# Canonical close time for Taiwan market (13:30 Taipei)
_MARKET_CLOSE_TIME = dt_time(13, 30, 0)


class TaiwanRealtimeService:
    """Thread-safe Real-time Quotation Service with 4-level Fallback Chain."""

    def __init__(
        self,
        cache_ttl_seconds: float = 3.0,
        freshness_policy: RealtimeFreshnessPolicy | None = None,
        trading_calendar: TaiwanTradingCalendar | None = None,
        mis_provider: TwseMisRealtimeProvider | None = None,
        yahoo_provider: YahooRealtimeProvider | None = None,
        official_close_provider: TaiwanOfficialQuoteProvider | None = None,
    ) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self.freshness_policy = freshness_policy or RealtimeFreshnessPolicy()
        self.trading_calendar = trading_calendar or TaiwanTradingCalendar()
        self.mis_provider = mis_provider or TwseMisRealtimeProvider()
        self.yahoo_provider = yahoo_provider or YahooRealtimeProvider()
        self.official_close_provider = official_close_provider or TaiwanOfficialQuoteProvider()

        # In-memory hot cache: (canonical, market_session) -> (monotonic_expiry, quote)
        self._cache: dict[tuple[str, str], tuple[float, TaiwanRealtimeQuote]] = {}
        self._lock = threading.Lock()

        # Metrics
        self.cache_hits = 0
        self.cache_misses = 0
        self.provider_requests = 0


    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def get_quotes(
        self,
        symbols: list[TaiwanSymbol | str],
        force_refresh: bool = False,
        daily_kline_fallback_fn: Any | None = None,
    ) -> dict[str, TaiwanRealtimeQuote]:
        """Fetch quotes in batch for specified symbols adhering to fallback chain.

        Fallback Order:
          1. In-memory Hot Cache (if fresh and not force_refresh)
          2. Primary Provider: TWSE MIS (Live L1/L2)
          3. Secondary Provider: Yahoo Finance
          4. Official Close Snapshot: TWSE STOCK_DAY_ALL / TPEx quotes
          5. Latest Daily K-Line (if daily_kline_fallback_fn provided)
        """
        if not symbols:
            return {}

        now_mono = time.monotonic()
        now_tpe = taipei_now()
        cur_market_status = self.trading_calendar.get_market_status(
            now_tpe, require_verified_trading_day=True,
        )
        parsed_symbols = [s if isinstance(s, TaiwanSymbol) else parse_symbol(s) for s in symbols]
        results: dict[str, TaiwanRealtimeQuote] = {}
        to_fetch: list[TaiwanSymbol] = []

        # 1. Check in-memory cache (scoped by market session to prevent cross-session leakage)
        with self._lock:
            for ts in parsed_symbols:
                canonical = ts.canonical
                cache_key = (canonical, cur_market_status.value)
                if not force_refresh and cache_key in self._cache:
                    expiry, cached_quote = self._cache[cache_key]
                    if now_mono < expiry:
                        results[canonical] = cached_quote
                        self.cache_hits += 1
                        continue
                self.cache_misses += 1
                to_fetch.append(ts)

        if not to_fetch:
            return results


        # 2. Primary Provider: TWSE MIS
        self.provider_requests += 1
        fetched_quotes: dict[str, TaiwanRealtimeQuote] = {}
        try:
            fetched_quotes = self.mis_provider.fetch_quotes(to_fetch)
        except Exception as e:
            logger.warning("Primary MIS provider failed: %s", e)

        # Confirm today as trading day if first-party MIS provides verified quotes for today
        has_first_party_today = any(
            q.trade_date == now_tpe.date() and not q.source_meta.is_stale
            for q in fetched_quotes.values()
        )
        if has_first_party_today and now_tpe.weekday() < 5 and now_tpe.date() not in self.trading_calendar.known_holidays:
            self.trading_calendar.add_trading_day(now_tpe.date())
            cur_market_status = self.trading_calendar.get_market_status(
                now_tpe, require_verified_trading_day=True,
            )

        for sym, q in fetched_quotes.items():
            q.market_status = cur_market_status.value
            results[sym] = q

        missing_after_primary = [ts for ts in to_fetch if ts.canonical not in results]

        # 3. Secondary Provider: Yahoo Finance
        if missing_after_primary:
            self.provider_requests += 1
            try:
                yahoo_quotes = self.yahoo_provider.fetch_quotes(missing_after_primary)
                for sym, q in yahoo_quotes.items():
                    q.market_status = cur_market_status.value
                    results[sym] = q
            except Exception as e:
                logger.warning("Secondary Yahoo provider failed: %s", e)

        missing_after_secondary = [ts for ts in to_fetch if ts.canonical not in results]

        # 4. Official Close Snapshot Fallback
        if missing_after_secondary:
            for ts in missing_after_secondary:
                try:
                    snap = self.official_close_provider.get_quote(ts.canonical)
                    if snap:
                        now_tpe = taipei_now()
                        # Derive quote_time from official close date at canonical 13:30 Taipei
                        snap_quote_time = datetime.combine(
                            snap.date, _MARKET_CLOSE_TIME, tzinfo=TAIPEI_TZ,
                        )
                        # Snapshot during an open/unverified session is inherently stale
                        snap_is_stale = cur_market_status in (
                            MarketStatus.OPEN,
                            MarketStatus.SCHEDULED_OPEN_UNVERIFIED,
                            MarketStatus.PRE_OPEN,
                        )
                        meta = SourceMeta(
                            source=snap.source,
                            source_url="",
                            fetched_at=now_tpe.isoformat(),
                            trade_date=snap.date,
                            status=RealtimeStatus.OFFICIAL_SNAPSHOT_FALLBACK.value,
                            is_realtime=False,
                            fallback_reason="Realtime providers unavailable; fell back to official close snapshot",
                            available_fields=("last_price", "prev_close", "volume", "amount"),
                            is_stale=snap_is_stale,
                            source_type="official_open_data",
                            freshness_class="eod_snapshot",
                            is_best_effort=False,
                            documented_sla=False,
                        )
                        rt_quote = TaiwanRealtimeQuote(
                            symbol=ts.canonical,
                            name=snap.name,
                            exchange=ts.exchange,
                            last_price=snap.close,
                            prev_close=snap.previous_close,
                            open=snap.open,
                            high=snap.high,
                            low=snap.low,
                            change=snap.change,
                            change_pct=snap.change_pct,
                            volume=snap.volume,
                            amount=snap.amount,
                            quote_time=snap_quote_time,
                            trade_date=snap.date,
                            market_status=cur_market_status.value,
                            source_meta=meta,
                        )
                        results[ts.canonical] = rt_quote
                except Exception as e:
                    logger.debug("Official snapshot fallback failed for %s: %s", ts.canonical, e)

        missing_after_snapshot = [ts for ts in to_fetch if ts.canonical not in results]

        # 5. Latest Daily K-Line Fallback
        if missing_after_snapshot and daily_kline_fallback_fn:
            for ts in missing_after_snapshot:
                try:
                    daily_row = daily_kline_fallback_fn(ts.canonical)
                    if daily_row:
                        now_tpe = taipei_now()
                        raw_date = daily_row.get("date")

                        # Timestamp safety: derive from daily row date, never use fetch time
                        if isinstance(raw_date, date):
                            t_date = raw_date
                            # Derive quote_time at canonical 13:30 close
                            daily_quote_time = datetime.combine(
                                t_date, _MARKET_CLOSE_TIME, tzinfo=TAIPEI_TZ,
                            )
                            fallback_reason = "All realtime & snapshot sources failed; fell back to latest Daily K"
                        else:
                            # Date missing or unreliable — refuse to fake
                            t_date = now_tpe.date()
                            daily_quote_time = None
                            fallback_reason = (
                                "All realtime & snapshot sources failed; fell back to latest Daily K; "
                                "daily row date missing, refused to fake quote timestamp"
                            )

                        meta = SourceMeta(
                            source="daily_kline",
                            source_url="",
                            fetched_at=now_tpe.isoformat(),
                            trade_date=t_date,
                            status=RealtimeStatus.DAILY_FALLBACK.value,
                            is_realtime=False,
                            fallback_reason=fallback_reason,
                            available_fields=("last_price", "prev_close", "volume"),
                            is_stale=True,
                            source_type="local_store",
                            freshness_class="daily_cached",
                            is_best_effort=False,
                            documented_sla=False,
                        )
                        results[ts.canonical] = TaiwanRealtimeQuote(
                            symbol=ts.canonical,
                            name=ts.canonical,
                            exchange=ts.exchange,
                            last_price=daily_row.get("close"),
                            prev_close=daily_row.get("prev_close"),
                            open=daily_row.get("open"),
                            high=daily_row.get("high"),
                            low=daily_row.get("low"),
                            change=daily_row.get("change"),
                            change_pct=daily_row.get("change_pct"),
                            volume=daily_row.get("volume"),
                            amount=daily_row.get("amount"),
                            quote_time=daily_quote_time,
                            trade_date=t_date,
                            market_status=cur_market_status.value,
                            source_meta=meta,
                        )
                except Exception as e:
                    logger.debug("Daily fallback failed for %s: %s", ts.canonical, e)

        # 6. Apply Freshness / Stale Policy & populate Cache
        with self._lock:
            for sym, q in results.items():
                threshold = self.freshness_policy.get_threshold_for_source(q.source_meta.source)
                # Stale detection during regular open session
                if cur_market_status in (MarketStatus.OPEN, MarketStatus.SCHEDULED_OPEN_UNVERIFIED) and q.quote_time:
                    age_seconds = (now_tpe - q.quote_time).total_seconds()
                    if age_seconds > threshold:
                        new_status = (
                            RealtimeStatus.STALE.value
                            if q.source_meta.status == RealtimeStatus.REALTIME.value
                            else q.source_meta.status
                        )
                        q.source_meta = replace(q.source_meta, is_stale=True, status=new_status)

                # Update in-memory cache scoped by session

                cache_key = (sym, cur_market_status.value)
                self._cache[cache_key] = (now_mono + self.cache_ttl_seconds, q)

        return results


    def get_quote(
        self,
        symbol: TaiwanSymbol | str,
        force_refresh: bool = False,
        daily_kline_fallback_fn: Any | None = None,
    ) -> TaiwanRealtimeQuote | None:
        """Fetch quote for a single symbol."""
        res = self.get_quotes(
            [symbol],
            force_refresh=force_refresh,
            daily_kline_fallback_fn=daily_kline_fallback_fn,
        )
        ts = symbol if isinstance(symbol, TaiwanSymbol) else parse_symbol(symbol)
        return res.get(ts.canonical)


_default_service: TaiwanRealtimeService | None = None


def get_realtime_service() -> TaiwanRealtimeService:
    """Get or instantiate global singleton TaiwanRealtimeService."""
    global _default_service
    if _default_service is None:
        _default_service = TaiwanRealtimeService()
    return _default_service

