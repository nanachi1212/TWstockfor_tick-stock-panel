"""Unified Aggregation Service for Taiwan Stock Research Workspace.

Orchestrates:
  - Identity & Security Master (canonical symbol, exchange, type, listing, CFI)
  - Price Limits (date-aware limits, multiplier-aware leveraged/inverse ETF, NO_LIMIT)
  - Realtime Quotes (5-level depth bids/asks, Taipei time, provenance)
  - Daily Historical K-Line (bounded recent daily bars via TaiwanHybridProvider)
  - Institutional Investor Flows (Foreign, Investment Trust, Dealer, Total Net, 5D rolling)
  - Margin Trading & Short Selling (balances, changes, short-margin ratio)
  - Factors (institutional rolling, margin momentum, short-margin ratio)
  - Market Benchmark Context (TAIEX for TWSE, TPEx Index for TPEx)
  - Monitor Rules & Recent Alerts summary
  - Robust Partial-Data Fallback (individual section failure does not abort the whole request)
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.detail_models import (
    SectionMeta,
    TaiwanDailyRow,
    TaiwanFactorsData,
    TaiwanHistoricalDaily,
    TaiwanInstitutionalData,
    TaiwanMarginData,
    TaiwanMarketContext,
    TaiwanMonitorSummary,
    TaiwanRecentAlert,
    TaiwanStockDetailResponse,
    TaiwanStockIdentity,
    TaiwanStockPriceLimit,
    TaiwanStockRealtime,
)
from app.taiwan.enrichment.index import TaiwanIndexProvider
from app.taiwan.enrichment.institutional import TaiwanInstitutionalProvider
from app.taiwan.enrichment.margin import TaiwanMarginProvider
from app.taiwan.fundamental_chips_service import (
    TaiwanFundamentalChipsService,
    get_fundamental_chips_service,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.realtime.monitor_engine import get_monitor_engine
from app.taiwan.realtime.service import get_realtime_service
from app.taiwan.symbol import TaiwanSymbol, parse_symbol
from app.taiwan.universe import get_security_master
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)


class TaiwanStockDetailService:
    """Service that aggregates all Taiwan domain data for a single security."""

    def __init__(
        self,
        hybrid_provider: TaiwanHybridProvider | None = None,
        institutional_provider: TaiwanInstitutionalProvider | None = None,
        margin_provider: TaiwanMarginProvider | None = None,
        index_provider: TaiwanIndexProvider | None = None,
        fundamental_chips_service: TaiwanFundamentalChipsService | None = None,
        daily_store: TaiwanDailyStore | None = None,
        institutional_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        trading_calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self.hybrid_provider = hybrid_provider or TaiwanHybridProvider()
        self.institutional_provider = institutional_provider or TaiwanInstitutionalProvider()
        self.margin_provider = margin_provider or TaiwanMarginProvider()
        self.index_provider = index_provider or TaiwanIndexProvider()
        self.fundamental_chips_service = fundamental_chips_service or get_fundamental_chips_service()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.institutional_store = institutional_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.trading_calendar = trading_calendar or TaiwanTradingCalendar()
        self.security_master = get_security_master()
        self.realtime_service = get_realtime_service()
        self.monitor_engine = get_monitor_engine()

    def get_stock_detail(
        self,
        raw_symbol: str,
        days: int = 120,
        repo=None,
    ) -> TaiwanStockDetailResponse:
        """Synchronous top-level entrypoint for stock detail aggregation."""
        canonical_sym = parse_symbol(raw_symbol)
        symbol_str = canonical_sym.canonical

        # 1. Identity from Security Master
        master_item = self.security_master.get_instrument(symbol_str)
        if master_item is not None:
            identity = TaiwanStockIdentity(
                symbol=master_item.symbol,
                code=master_item.code,
                name=master_item.name,
                exchange=master_item.exchange,
                instrument_type=master_item.instrument_type,
                is_supported=master_item.is_supported,
                listing_status=master_item.listing_status,
                listing_date=master_item.listing_date,
                industry=master_item.industry,
                cfi_code=master_item.cfi_code,
                etf_category=master_item.etf_category if master_item.etf_category else None,
            )
        else:
            # Fallback identity for unindexed or mock symbols
            identity = TaiwanStockIdentity(
                symbol=symbol_str,
                code=canonical_sym.code,
                name=canonical_sym.code,
                exchange=canonical_sym.exchange.value.upper(),
                instrument_type="stock",
                is_supported=True,
            )

        # 2. Canonical Price Limits via MarketProfileBridge
        # ValueError here means unconfirmed ETF (regulatory fail-closed); treat as unknown.
        try:
            limit_pct = MarketProfileBridge.get_price_limit_pct(master_item) if master_item else 0.1
            is_no_limit = limit_pct is None
            rule_type = "無漲跌幅限制" if is_no_limit else f"±{int(limit_pct * 100)}%" if limit_pct else "普通股±10%"
        except ValueError:
            limit_pct = None
            is_no_limit = False
            rule_type = "商品分類資料不足"
        price_limit = TaiwanStockPriceLimit(
            limit_up=None,
            limit_down=None,
            price_limit_pct=limit_pct,
            is_no_limit=is_no_limit,
            rule_type=rule_type,
        )

        # 3. Realtime Quote & 5-Level Depth
        realtime_data = self._aggregate_realtime(symbol_str, price_limit)

        # 4. Daily Historical K-Line
        daily_data = self._aggregate_daily(symbol_str, days)
        realtime_data = self._apply_same_session_amount_fallback(realtime_data, daily_data)

        # 5. Institutional Investors (Three Major Institutional Flow)
        institutional_data = self._aggregate_institutional(canonical_sym)

        # 6. Margin Trading & Short Selling
        margin_data = self._aggregate_margin(canonical_sym)

        # 7. Factors
        factors_data = self._aggregate_factors(institutional_data, margin_data)

        # 8. Market Context Benchmark
        market_context = self._aggregate_market_context(canonical_sym.exchange.value.upper(), repo=repo)

        # 9. Monitor Rules Summary
        monitor_summary = self._aggregate_monitor_summary(symbol_str)

        # 10. Recent Alerts
        recent_alerts = self._aggregate_recent_alerts(symbol_str)

        # 10. Fundamentals and Extra Chips (robust fallback: does not abort detail)
        try:
            exchange = identity.exchange
            fundamentals_data = self.fundamental_chips_service.get_fundamentals_bundle(symbol_str, exchange)
        except Exception as e:
            logger.debug("Fundamentals aggregation error for %s: %s", symbol_str, e)
            fundamentals_data = None

        try:
            extra_chips_data = self.fundamental_chips_service.get_extra_chips_bundle(symbol_str)
        except Exception as e:
            logger.debug("Extra chips aggregation error for %s: %s", symbol_str, e)
            extra_chips_data = None

        # Determine overall quality
        sections_statuses = [
            realtime_data.meta.status if realtime_data.meta else "unknown",
            daily_data.status,
            institutional_data.status,
            margin_data.status,
        ]
        if all(s == "available" for s in sections_statuses):
            overall_quality = "good"
        elif any(s == "stale" for s in sections_statuses):
            overall_quality = "stale"
        elif any(s == "available" for s in sections_statuses):
            overall_quality = "partial"
        else:
            overall_quality = "degraded"

        # 11. Recent Events and News (A11)
        recent_events = []
        try:
            from app.taiwan.events_service import get_event_service
            event_svc = get_event_service()
            recent_events = event_svc.get_events(scope="all", symbol=symbol_str, limit=10)
        except Exception as e:
            logger.debug("Recent events aggregation error for %s: %s", symbol_str, e)

        recent_news = []
        news_status = "available"
        news_status_message = None
        news_fetched_at = None
        try:
            from app.taiwan.news_service import get_news_service
            news_svc = get_news_service()
            news_res = news_svc.get_recent_news(symbol_str, limit=10)
            recent_news = news_res.items
            news_status = news_res.status
            news_status_message = news_res.status_message
            news_fetched_at = news_res.fetched_at
        except Exception as e:
            logger.debug("Recent news aggregation error for %s: %s", symbol_str, e)
            news_status = "unavailable"
            news_status_message = str(e)

        return TaiwanStockDetailResponse(
            symbol=symbol_str,
            identity=identity,
            price_limit=price_limit,
            realtime=realtime_data,
            daily_history=daily_data,
            institutional=institutional_data,
            margin=margin_data,
            factors=factors_data,
            market_context=market_context,
            monitor_summary=monitor_summary,
            recent_alerts=recent_alerts,
            fundamentals=fundamentals_data,
            extra_chips=extra_chips_data,
            recent_events=recent_events,
            recent_news=recent_news,
            news_status=news_status,
            news_status_message=news_status_message,
            news_fetched_at=news_fetched_at,
            overall_data_quality=overall_quality,
        )

    def _aggregate_realtime(
        self,
        symbol_str: str,
        price_limit: TaiwanStockPriceLimit,
    ) -> TaiwanStockRealtime:
        """Fetch live quote and enrich price limits."""
        try:
            quote_map = self.realtime_service.get_quotes([symbol_str])
            q = quote_map.get(symbol_str)
            if q is not None:
                # Calculate limit up/down via authoritative MarketProfileBridge.calc_limits
                inst = self.security_master.get_instrument(symbol_str)
                if q.prev_close and not price_limit.is_no_limit and inst:
                    lim_up, lim_dn = MarketProfileBridge.calc_limits(q.prev_close, inst)
                    price_limit.limit_up = lim_up
                    price_limit.limit_down = lim_dn
                elif q.prev_close and not price_limit.is_no_limit and price_limit.price_limit_pct:
                    mult = price_limit.price_limit_pct
                    price_limit.limit_up = round(q.prev_close * (1.0 + mult), 2)
                    price_limit.limit_down = round(q.prev_close * (1.0 - mult), 2)

                meta = None
                if q.source_meta:
                    f_at = q.source_meta.fetched_at
                    f_str = f_at.isoformat() if hasattr(f_at, "isoformat") else str(f_at) if f_at else None
                    meta = SectionMeta(
                        source=q.source_meta.source,
                        trade_date=str(q.source_meta.trade_date) if q.source_meta.trade_date else None,
                        fetched_at=f_str,
                        status="available" if not q.source_meta.is_stale else "stale",
                        is_stale=q.source_meta.is_stale,
                        fallback_reason=q.source_meta.fallback_reason,
                        source_type=q.source_meta.source_type,
                        freshness_class=q.source_meta.freshness_class,
                        is_realtime=q.source_meta.is_realtime,
                    )

                q_t = q.quote_time
                qt_str = q_t.isoformat() if hasattr(q_t, "isoformat") else str(q_t) if q_t else None

                return TaiwanStockRealtime(
                    last_price=q.last_price,
                    prev_close=q.prev_close,
                    open=q.open,
                    high=q.high,
                    low=q.low,
                    change=q.change,
                    change_pct=q.change_pct,
                    volume=q.volume,
                    amount=q.amount,
                    quote_time=qt_str,
                    market_status=q.market_status.value if hasattr(q.market_status, "value") else str(q.market_status),
                    bid_price=q.bid_price,
                    ask_price=q.ask_price,
                    bids=[[float(p), float(v)] for p, v in q.bids],
                    asks=[[float(p), float(v)] for p, v in q.asks],
                    meta=meta,
                )
        except Exception as e:
            logger.warning("Realtime aggregation failed for %s: %s", symbol_str, e)

        return TaiwanStockRealtime(
            market_status="closed",
            meta=SectionMeta(source="realtime_service", status="unavailable", is_stale=False),
        )

    def _aggregate_daily(self, symbol_str: str, days: int) -> TaiwanHistoricalDaily:
        """Fetch historical daily bars from Hybrid provider."""
        try:
            df = self.hybrid_provider.get_daily([symbol_str])
            if not df.is_empty():
                rows: list[TaiwanDailyRow] = []
                # Sort ascending by date
                sorted_df = df.sort("date")
                tail_df = sorted_df.tail(days)
                for item in tail_df.iter_rows(named=True):
                    dt_str = str(item.get("date", ""))
                    if len(dt_str) > 10:
                        dt_str = dt_str[:10]
                    rows.append(
                        TaiwanDailyRow(
                            date=dt_str,
                            open=float(item.get("open") or 0.0),
                            high=float(item.get("high") or 0.0),
                            low=float(item.get("low") or 0.0),
                            close=float(item.get("close") or 0.0),
                            volume=int(item.get("volume") or 0),
                            amount=float(item.get("amount")) if item.get("amount") is not None else None,
                        )
                    )
                latest_date = rows[-1].date if rows else None
                meta = SectionMeta(
                    source=str(df.get_column("source")[0]) if "source" in df.columns else "hybrid_daily",
                    trade_date=latest_date,
                    status="available",
                    is_stale=False,
                )
                return TaiwanHistoricalDaily(status="available", rows=rows, meta=meta)
        except Exception as e:
            logger.warning("Daily aggregation failed for %s: %s", symbol_str, e)

        return TaiwanHistoricalDaily(
            status="unavailable",
            rows=[],
            meta=SectionMeta(
                source="hybrid_daily",
                status="unavailable",
                is_stale=False,
                fallback_reason="no persisted or provider daily rows are available for this symbol",
            ),
        )

    @staticmethod
    def _apply_same_session_amount_fallback(
        realtime: TaiwanStockRealtime,
        daily: TaiwanHistoricalDaily,
    ) -> TaiwanStockRealtime:
        """Use daily amount only when its session exactly matches the quote trade date."""
        if realtime.amount is not None:
            realtime.amount_meta = realtime.meta
            return realtime
        quote_date = realtime.meta.trade_date if realtime.meta else None
        match = next(
            (row for row in daily.rows if row.date == quote_date and row.amount is not None),
            None,
        )
        if match is not None:
            realtime.amount = match.amount
            realtime.amount_meta = SectionMeta(
                source=daily.meta.source if daily.meta else "hybrid_daily",
                trade_date=match.date,
                fetched_at=daily.meta.fetched_at if daily.meta else None,
                status="fallback",
                is_stale=daily.meta.is_stale if daily.meta else False,
                fallback_reason="realtime amount unavailable; using same-session daily amount",
            )
        else:
            realtime.amount_meta = SectionMeta(
                source="realtime+daily",
                trade_date=quote_date,
                status="unavailable",
                is_stale=False,
                fallback_reason="no amount is available for the exact quote trade date",
            )
        return realtime

    def _recent_confirmed_sessions(self, count: int = 5) -> list:
        for available_date in self.daily_store.available_dates():
            self.trading_calendar.add_trading_day(available_date)
        return self.trading_calendar.recent_confirmed_sessions(taipei_now().date(), count)

    def _aggregate_institutional(self, sym: TaiwanSymbol) -> TaiwanInstitutionalData:
        """Fetch institutional data for latest trading day, with safe timeout."""
        source = "tpex:daily_trade" if sym.exchange.value.upper() == "TPEX" else "twse:t86"
        try:
            sessions = self._recent_confirmed_sessions()
        except Exception as exc:
            logger.debug("Confirmed institutional sessions unavailable for %s: %s", sym.canonical, exc)
            sessions = []
        store = getattr(self, "institutional_store", None)
        if store is not None and sessions:
            try:
                persisted = store.read_range([sym.canonical], min(sessions), max(sessions))
                rows = [
                    row
                    for row in persisted.iter_rows(named=True)
                    if row.get("date") in set(sessions)
                ]
                if rows:
                    rows_by_date = {row["date"]: row for row in rows}
                    latest_date = next(session for session in sessions if session in rows_by_date)
                    latest = rows_by_date[latest_date]
                    rolling = [rows_by_date[d] for d in sessions if d in rows_by_date]

                    def _rolling_sum(field: str) -> int | None:
                        values = [row.get(field) for row in rolling]
                        return sum(value for value in values if value is not None) if any(
                            value is not None for value in values
                        ) else None

                    computed = latest.get("computed_net")
                    total = computed if computed is not None else latest.get("official_net")
                    is_stale = latest_date != sessions[0]
                    return TaiwanInstitutionalData(
                        status="available",
                        foreign_net=latest.get("foreign_net"),
                        investment_trust_net=latest.get("investment_trust_net"),
                        dealer_net=latest.get("dealer_net"),
                        total_net=total,
                        foreign_net_5d=_rolling_sum("foreign_net"),
                        investment_trust_net_5d=_rolling_sum("investment_trust_net"),
                        dealer_net_5d=_rolling_sum("dealer_net"),
                        trend=rolling,
                        meta=SectionMeta(
                            source=str(latest.get("source") or source),
                            trade_date=latest_date.isoformat(),
                            status="stale" if is_stale else "available",
                            is_stale=is_stale,
                            fallback_reason=(
                                "latest persisted institutional row predates the newest confirmed session"
                                if is_stale
                                else None
                            ),
                        ),
                    )
            except Exception as exc:
                logger.debug("Persisted institutional data unavailable for %s: %s", sym.canonical, exc)
        attempted: list = []
        failed = False
        try:
            flows = []
            trade_dt = None
            for check_d in sessions:
                attempted.append(check_d)
                try:
                    flows = self.institutional_provider.fetch_live_day(
                        exchange=sym.exchange.value.upper(),
                        trade_date=check_d,
                        target_code=sym.code,
                        timeout=3.0,
                    )
                    if flows:
                        trade_dt = check_d
                        break
                except Exception:
                    failed = True
                    continue

            if flows:
                f = flows[0]
                total = f.computed_net if f.computed_net is not None else f.official_net
                is_stale = bool(f.meta and f.meta.is_stale)
                meta = SectionMeta(
                    source=f.meta.source if f.meta else source,
                    trade_date=trade_dt.isoformat() if trade_dt else None,
                    fetched_at=f.meta.fetched_at.isoformat() if f.meta and f.meta.fetched_at else None,
                    status="stale" if is_stale else "available",
                    is_stale=is_stale,
                )
                return TaiwanInstitutionalData(
                    status="available",
                    foreign_net=f.foreign_net,
                    investment_trust_net=f.investment_trust_net,
                    dealer_net=f.dealer_net,
                    total_net=total,
                    # A single-session provider result is not a 5-session sum.
                    # Keep rolling values unavailable until an authoritative
                    # local rolling dataset supplies them.
                    foreign_net_5d=None,
                    investment_trust_net_5d=None,
                    dealer_net_5d=None,
                    meta=meta,
                )
        except Exception as e:
            logger.debug("Institutional aggregation skipped for %s: %s", sym.canonical, e)
            failed = True

        if not sessions:
            reason = "no confirmed Taiwan trading session is available in the persisted daily store"
        elif failed:
            reason = (
                f"official institutional flow lookup failed or returned no row for {sym.code} "
                f"across {len(attempted)} confirmed sessions"
            )
        else:
            reason = (
                f"official institutional flow source returned no row for {sym.code} "
                f"across {len(attempted)} confirmed sessions"
            )

        return TaiwanInstitutionalData(
            status="unavailable",
            meta=SectionMeta(
                source=source,
                trade_date=attempted[0].isoformat() if attempted else None,
                status="unavailable",
                is_stale=False,
                fallback_reason=reason,
            ),
        )

    def _aggregate_margin(self, sym: TaiwanSymbol) -> TaiwanMarginData:
        """Fetch margin data for latest trading day, with safe timeout."""
        source = "tpex:margin_balance" if sym.exchange.value.upper() == "TPEX" else "twse:mi_margn"
        try:
            sessions = self._recent_confirmed_sessions()
        except Exception as exc:
            logger.debug("Confirmed margin sessions unavailable for %s: %s", sym.canonical, exc)
            sessions = []
        store = getattr(self, "margin_store", None)
        if store is not None and sessions:
            try:
                persisted = store.read_range([sym.canonical], min(sessions), max(sessions))
                rows = [
                    row
                    for row in persisted.iter_rows(named=True)
                    if row.get("date") in set(sessions)
                ]
                if rows:
                    rows_by_date = {row["date"]: row for row in rows}
                    latest_date = next(session for session in sessions if session in rows_by_date)
                    latest = rows_by_date[latest_date]
                    rolling = [rows_by_date[d] for d in sessions if d in rows_by_date]
                    is_stale = latest_date != sessions[0]
                    return TaiwanMarginData(
                        status="available",
                        margin_balance=latest.get("margin_balance"),
                        margin_change=latest.get("margin_change"),
                        short_balance=latest.get("short_balance"),
                        short_change=latest.get("short_change"),
                        short_margin_ratio=latest.get("short_margin_ratio"),
                        trend=rolling,
                        meta=SectionMeta(
                            source=str(latest.get("source") or source),
                            trade_date=latest_date.isoformat(),
                            status="stale" if is_stale else "available",
                            is_stale=is_stale,
                            fallback_reason=(
                                "latest persisted margin row predates the newest confirmed session"
                                if is_stale
                                else None
                            ),
                        ),
                    )
            except Exception as exc:
                logger.debug("Persisted margin data unavailable for %s: %s", sym.canonical, exc)
        attempted: list = []
        failed = False
        try:
            margins = []
            trade_dt = None
            for check_d in sessions:
                attempted.append(check_d)
                try:
                    margins = self.margin_provider.fetch_live_day(
                        exchange=sym.exchange.value.upper(),
                        trade_date=check_d,
                        target_code=sym.code,
                        timeout=3.0,
                    )
                    if margins:
                        trade_dt = check_d
                        break
                except Exception:
                    failed = True
                    continue

            if margins:
                m = margins[0]
                is_stale = bool(m.meta and m.meta.is_stale)
                meta = SectionMeta(
                    source=m.meta.source if m.meta else source,
                    trade_date=trade_dt.isoformat() if trade_dt else None,
                    fetched_at=m.meta.fetched_at.isoformat() if m.meta and m.meta.fetched_at else None,
                    status="stale" if is_stale else "available",
                    is_stale=is_stale,
                )
                return TaiwanMarginData(
                    status="available",
                    margin_balance=m.margin_balance,
                    margin_change=m.margin_change,
                    short_balance=m.short_balance,
                    short_change=m.short_change,
                    short_margin_ratio=m.short_margin_ratio,
                    meta=meta,
                )
        except Exception as e:
            logger.debug("Margin aggregation skipped for %s: %s", sym.canonical, e)
            failed = True

        if not sessions:
            reason = "no confirmed Taiwan trading session is available in the persisted daily store"
        elif failed:
            reason = (
                f"official margin/short lookup failed or returned no row for {sym.code} "
                f"across {len(attempted)} confirmed sessions"
            )
        else:
            reason = (
                f"official margin/short source returned no row for {sym.code} "
                f"across {len(attempted)} confirmed sessions"
            )

        return TaiwanMarginData(
            status="unavailable",
            meta=SectionMeta(
                source=source,
                trade_date=attempted[0].isoformat() if attempted else None,
                status="unavailable",
                is_stale=False,
                fallback_reason=reason,
            ),
        )

    def _aggregate_factors(
        self,
        inst: TaiwanInstitutionalData,
        margin: TaiwanMarginData,
    ) -> TaiwanFactorsData:
        """Collate factors from institutional and margin sections."""
        has_any = (
            inst.status == "available"
            or margin.status == "available"
        )
        if not has_any:
            return TaiwanFactorsData(
                status="unavailable",
                meta=SectionMeta(
                    source="factors_pipeline",
                    status="unavailable",
                    is_stale=False,
                    fallback_reason=(
                        "institutional and margin inputs are unavailable; deterministic factors were not computed"
                    ),
                ),
            )

        return TaiwanFactorsData(
            status="available",
            foreign_net_5d=inst.foreign_net_5d,
            investment_trust_net_5d=inst.investment_trust_net_5d,
            dealer_net_5d=inst.dealer_net_5d,
            margin_balance_change=margin.margin_change,
            short_margin_ratio=margin.short_margin_ratio,
            meta=SectionMeta(
                source="factors_pipeline",
                trade_date=inst.meta.trade_date if inst.meta else None,
                status="available",
                is_stale=False,
            ),
        )

    def _aggregate_market_context(self, exchange: str, repo=None) -> TaiwanMarketContext:
        """Read the exchange benchmark from the existing persisted index store."""
        ex = exchange.upper()
        if ex == "TPEX":
            symbol = "TPEX_INDEX"
            name = "櫃買指數"
        else:
            symbol = "TAIEX"
            name = "發行量加權股價指數"

        from app.taiwan.benchmark_store import latest_benchmark
        from app.taiwan.daily_update import resolve_target_latest_trading_date

        official = latest_benchmark(symbol)
        if official:
            return TaiwanMarketContext(
                benchmark_symbol=symbol,
                benchmark_name=name,
                close=official["close"],
                change=official["change"],
                # This context reports percent points, not fractions.
                change_pct=official["change_pct"] * 100 if official["change_pct"] is not None else None,
                meta=SectionMeta(
                    source=official["source"],
                    trade_date=official["date"].isoformat(),
                    status="available",
                    is_stale=official["date"] < resolve_target_latest_trading_date(),
                ),
            )

        if repo is not None:
            try:
                rows = repo.execute_all(
                    """
                    SELECT date, close
                    FROM kline_index_daily
                    WHERE symbol = ?
                    ORDER BY date DESC
                    LIMIT 2
                    """,
                    [symbol],
                )
                if rows:
                    latest_date, latest_close = rows[0]
                    previous_close = rows[1][1] if len(rows) > 1 else None
                    change = None
                    change_pct = None
                    if latest_close is not None and previous_close not in (None, 0):
                        change = float(latest_close) - float(previous_close)
                        change_pct = change / float(previous_close) * 100
                    return TaiwanMarketContext(
                        benchmark_symbol=symbol,
                        benchmark_name=name,
                        close=float(latest_close) if latest_close is not None else None,
                        change=change,
                        change_pct=change_pct,
                        meta=SectionMeta(
                            source="persisted:kline_index_daily",
                            trade_date=str(latest_date),
                            status="available",
                            is_stale=False,
                        ),
                    )
            except Exception as exc:
                logger.debug("Persisted benchmark unavailable for %s: %s", symbol, exc)

        return TaiwanMarketContext(
            benchmark_symbol=symbol,
            benchmark_name=name,
            close=None,
            change=None,
            change_pct=None,
            meta=SectionMeta(
                source="persisted:kline_index_daily",
                status="unavailable",
                is_stale=False,
                fallback_reason=f"persisted benchmark {symbol} is unavailable",
            ),
        )

    def _aggregate_monitor_summary(self, symbol_str: str) -> TaiwanMonitorSummary:
        """Find active rules configured for this symbol."""
        try:
            all_rules = self.monitor_engine.list_rules()
            matched = [r for r in all_rules if r.symbol == symbol_str]
            active = [r for r in matched if r.enabled]
            return TaiwanMonitorSummary(
                rule_count=len(matched),
                active_count=len(active),
                rules=[
                    {
                        "rule_id": r.rule_id,
                        "name": r.name,
                        "rule_type": r.rule_type.value,
                        "threshold": r.threshold,
                        "enabled": r.enabled,
                        "severity": r.severity.value,
                    }
                    for r in matched
                ],
            )
        except Exception as e:
            logger.warning("Monitor summary error for %s: %s", symbol_str, e)
            return TaiwanMonitorSummary()

    def _aggregate_recent_alerts(self, symbol_str: str) -> list[TaiwanRecentAlert]:
        """Fetch recent alerts for this symbol."""
        from app.config import Settings
        from app.services import alert_store
        alerts: list[TaiwanRecentAlert] = []
        try:
            settings = Settings()
            data_dir = settings.data_dir if hasattr(settings, "data_dir") else None
            if not data_dir:
                from pathlib import Path
                data_dir = Path("data")
            raw_alerts = alert_store.list_recent(data_dir, days=7, limit=200)
            for a in raw_alerts:
                if a.get("symbol") == symbol_str or a.get("rule_symbol") == symbol_str:
                    ts = a.get("ts", 0)
                    dt_str = datetime.fromtimestamp(ts / 1000.0).isoformat() if ts else ""
                    alerts.append(
                        TaiwanRecentAlert(
                            alert_id=str(a.get("alert_id", a.get("id", ""))),
                            rule_id=str(a.get("rule_id", "")),
                            rule_name=str(a.get("rule_name", a.get("name", ""))),
                            rule_type=str(a.get("rule_type", a.get("type", ""))),
                            severity=str(a.get("severity", "warning")),
                            trigger_price=float(a.get("trigger_price", a.get("price", 0.0))),
                            trigger_value=float(a.get("trigger_value", 0.0)),
                            message=str(a.get("message", "")),
                            triggered_at=dt_str,
                        )
                    )
            # Limit to 10
            return alerts[:10]
        except Exception as e:
            logger.debug("Recent alerts aggregation error for %s: %s", symbol_str, e)
            return []


# Global singleton instance
_detail_service: TaiwanStockDetailService | None = None


def get_taiwan_stock_detail_service() -> TaiwanStockDetailService:
    global _detail_service
    if _detail_service is None:
        _detail_service = TaiwanStockDetailService()
    return _detail_service
