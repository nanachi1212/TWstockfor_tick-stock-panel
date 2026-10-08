"""Taiwan Market Screener Service (Phase 6B).

POST /api/taiwan/screener/run

Architecture:
  TaiwanSecurityMaster (Universe)
  -> TaiwanDailyStore.read_latest_per_symbol() (Batch daily snapshot)
  -> TaiwanDailyStore.read_range() for batch indicators (MA5/10/20, RSI14, Momentum5d, VolRatio5d)
  -> Batch MarketProfile price limits (calc_limits_for_pct with tick size)
  -> Batch Institutional & Margin joins
  -> Strongly typed Pydantic filters (whitelist)
  -> Deterministic Sort with symbol ASC tie-breaker
  -> Total count
  -> Pagination (slice)
  -> Strongly typed API response

NO request-time HTTP calls to external providers.
"""
from __future__ import annotations

import contextlib
import json
import logging
from datetime import date, timedelta
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.corporate_actions import CorporateActionStore, event_market_open
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.monthly_revenue_evidence import (
    MonthlyRevenueEvidenceStore,
    RevenueEvidenceSnapshot,
)
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.taiwan_values import parse_number
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.selection_v2 import (
    STRATEGY_IDS,
    V2_STRATEGY_IDS,
    apply_strategy,
    rank_strategy,
    strategy_metadata,
    strategy_readiness,
)
from app.taiwan.technical_indicators import MIN_BARS_RSI_14, wilder_rsi_expr
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)

_DAILY_NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume", "amount")

ExchangeFilter = Literal["TWSE", "TPEX", "ALL"]
InstrumentFilter = Literal["stock", "etf", "ALL"]
SortField = Literal[
    "symbol", "close", "change_pct", "volume", "amount",
    "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d",
    "foreign_net", "foreign_net_5d", "investment_trust_net",
    "investment_trust_net_5d", "dealer_net",
    "margin_balance_change", "short_balance", "short_margin_ratio",
    "pe", "pb", "dividend_yield", "revenue_yoy", "revenue_mom",
    "latest_eps", "foreign_shareholding_ratio", "foreign_shareholding_change_20d", "quant_score",
    "trend_liquidity_v1", "institutional_momentum_v1", "growth_trend_v1",
    "breakout_v1", "multi_factor_consensus_v1",
]
SortDir = Literal["asc", "desc"]

#: Frozen trend_liquidity_v1 parameters. Historical PIT evaluation imports these
#: and the pure functions below, so live and historical selection share one rule.
TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD = 50_000_000
TREND_LIQUIDITY_V1_TREND_SESSIONS = 20
TREND_LIQUIDITY_V1_BATCH_SIZE = 20

TrendAdjustmentStatus = Literal["verified", "partial", "unavailable"]


def compute_trend_liquidity_v1_indicators(
    hist: pl.DataFrame, dates: list[date], events, as_of: date,
) -> tuple[pl.DataFrame | None, TrendAdjustmentStatus, str | None]:
    """PIT-adjusted close, MA20 and 5-session momentum at ``as_of``.

    ``hist`` holds raw daily rows (symbol, date, close) and ``dates`` the verified
    sessions ending at ``as_of``; only symbols with a row on every session count.
    ``events`` are the verified-coverage corporate actions inside that window.
    """
    if hist.is_empty():
        return None, "unavailable", "trend_history"
    hist = hist.filter(pl.col("date").is_in(dates))
    complete = (hist.group_by("symbol").agg(pl.col("date").n_unique().alias("days"))
                .filter(pl.col("days") == len(dates))["symbol"].to_list())
    skipped = 0
    adjustment_unverified = False
    hist = hist.filter(pl.col("symbol").is_in(complete))
    if hist.is_empty():
        return None, "unavailable", "trend_history"
    hist = hist.select("symbol", "date", "close").sort("symbol", "date")
    by_symbol: dict[str, list] = {}
    for event in events:
        by_symbol.setdefault(event.symbol, []).append(event)
    adjusted_parts = [hist.filter(~pl.col("symbol").is_in(list(by_symbol)))]
    for symbol, symbol_events in by_symbol.items():
        subset = hist.filter(pl.col("symbol") == symbol)
        if subset.is_empty():
            continue
        adjusted = adjust_prices_as_of(
            subset, as_of=as_of, events=symbol_events, price_columns=("close",)
        )
        if adjusted.status != "verified":
            skipped += 1
            if any(event.status != "verified" for event in symbol_events):
                adjustment_unverified = True
            continue
        adjusted_parts.append(adjusted.to_frame().select("symbol", "date", "close"))
    adjusted_hist = pl.concat(adjusted_parts).sort("symbol", "date")
    if adjusted_hist.is_empty():
        return pl.DataFrame(), "partial" if adjustment_unverified else "verified", (
            "trend_history" if skipped else None
        )
    latest = (adjusted_hist.with_columns(
        pl.col("close").rolling_mean(TREND_LIQUIDITY_V1_TREND_SESSIONS).over("symbol")
        .alias("trend_ma20"),
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0)
        .alias("trend_momentum_5d"),
    ).with_columns(pl.col("date").max().over("symbol").alias("_latest"))
        .filter(pl.col("date") == pl.col("_latest"))
        .select("symbol", pl.col("close").alias("trend_adjusted_close"),
                "trend_ma20", "trend_momentum_5d"))
    return latest, "partial" if adjustment_unverified else "verified", (
        "trend_history" if skipped else None
    )


def trend_liquidity_v1_candidates(frame: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """Price, liquidity and PIT trend rules; regulatory exclusion is applied separately."""
    return frame.filter(
        (pl.col("date") == as_of)
        & (pl.col("momentum_5d") > 0)
        & (pl.col("close") > 0)
        & (pl.col("amount") >= TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD)
        & (pl.col("trend_adjusted_close") > pl.col("ma20"))
    )


def rank_trend_liquidity_v1(frame: pl.DataFrame) -> pl.DataFrame:
    """5D momentum desc, turnover desc, symbol asc."""
    return frame.sort(["momentum_5d", "amount", "symbol"], descending=[True, True, False])


def _chip_date_current(cached: dict[str, Any], floor: str | None) -> bool:
    data_date = str(cached.get("data_date") or "")[:10]
    return bool(floor and data_date and data_date >= floor)


class TaiwanScreenerRequest(BaseModel):
    """Strongly typed Taiwan Screener Request Body."""

    exchange: ExchangeFilter = "ALL"
    preset: Literal[
        "trend_liquidity_v1", "institutional_momentum_v1", "growth_trend_v1",
        "breakout_v1", "multi_factor_consensus_v1",
    ] | None = None
    instrument: InstrumentFilter = "ALL"
    industry: str | None = None  # None or specific industry name
    # Code or name fragment typed by the user (e.g. "正2", "金控", "2330"); literal, case-insensitive.
    keyword: str | None = Field(default=None, max_length=20)
    symbol_scope: list[str] | None = Field(default=None, exclude=True, repr=False)
    # Internal callers (beginner selection) opt into the verified five-session
    # institutional flow and revenue status without applying a preset filter.
    extended_factors: bool = Field(default=False, exclude=True, repr=False)

    # Price & Volume filters
    price_min: float | None = None
    price_max: float | None = None
    change_pct_min: float | None = None  # 0.05 = 5%
    change_pct_max: float | None = None
    volume_min: float | None = None  # in shares
    volume_max: float | None = None
    amount_min: float | None = None  # in TWD
    amount_max: float | None = None

    # Technical Indicators
    rsi_14_min: float | None = None
    rsi_14_max: float | None = None
    momentum_5d_min: float | None = None
    momentum_5d_max: float | None = None
    vol_ratio_5d_min: float | None = None
    vol_ratio_5d_max: float | None = None
    above_ma5: bool | None = None
    above_ma20: bool | None = None

    # Institutional (in shares)
    foreign_net_min: float | None = None
    foreign_net_max: float | None = None
    investment_trust_net_min: float | None = None
    investment_trust_net_max: float | None = None
    dealer_net_min: float | None = None
    dealer_net_max: float | None = None
    streak_investor: Literal["foreign", "investment_trust", "dealer"] = "foreign"
    streak_direction: Literal["buy", "sell"] = "buy"
    streak_min_days: int | None = Field(default=None, ge=1, le=60)

    # Margin & Short
    margin_balance_change_min: float | None = None
    margin_balance_change_max: float | None = None
    short_balance_min: float | None = None
    short_balance_max: float | None = None
    short_margin_ratio_min: float | None = None  # 10.0 = 10%
    short_margin_ratio_max: float | None = None

    # Price Limit proximity
    near_upper_limit: bool | None = None  # distance_to_upper <= 0.03
    near_lower_limit: bool | None = None  # distance_to_lower <= 0.03
    distance_to_upper_limit_max: float | None = None
    distance_to_lower_limit_max: float | None = None

    # Fundamentals (Valuation)
    pe_min: float | None = None
    pe_max: float | None = None
    pb_min: float | None = None
    pb_max: float | None = None
    dividend_yield_min: float | None = None  # 3.0 = 3%

    # Fundamentals (Revenue)
    revenue_yoy_min: float | None = None  # 20.0 = 20%
    revenue_mom_min: float | None = None  # 5.0 = 5%

    # Fundamentals (Profitability)
    eps_min: float | None = None
    net_income_positive: bool | None = None

    # Chips (Shareholding & Lending)
    foreign_shareholding_ratio_min: float | None = None  # 20.0 = 20%
    foreign_shareholding_change_20d_min: float | None = None  # 0.0 = 0%
    securities_lending_anomaly_exclude: bool | None = None  # True: 排除異常暴增

    # Event and Regulatory Filters (A11)
    exclude_disposition: bool | None = None
    exclude_suspended: bool | None = None
    exclude_risk_events: bool | None = None
    recent_revenue_or_earnings: bool | None = None

    # Quant Score
    quant_score_min: float | None = None

    # Pagination & Sorting
    sort_by: SortField = "symbol"
    sort_order: SortDir = "asc"
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)


class ScreenerResultItem(BaseModel):
    """Single instrument screening result."""

    symbol: str
    name: str
    exchange: str
    instrument_type: str
    industry: str | None = None

    # Quotes & Volumes
    close: float | None = None
    trend_adjusted_close: float | None = None
    change_pct: float | None = None  # decimal: 0.05 = 5%
    volume: float | None = None  # shares
    amount: float | None = None  # TWD
    quote_date: str | None = None

    # Price Limits
    price_limit_pct: float | None = None
    is_no_limit: bool = False
    limit_up: float | None = None
    limit_down: float | None = None
    distance_to_upper_limit: float | None = None
    distance_to_lower_limit: float | None = None

    # Indicators
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    rsi_14: float | None = None
    momentum_5d: float | None = None
    vol_ratio_5d: float | None = None
    ma60: float | None = None
    momentum_20d: float | None = None
    vol_ratio_20d: float | None = None
    momentum_acceleration: float | None = None
    breakout_20d_strength: float | None = None
    breakout_60d_strength: float | None = None

    # Institutional (shares)
    foreign_net: float | None = None
    foreign_net_5d: float | None = None
    investment_trust_net: float | None = None
    investment_trust_net_5d: float | None = None
    dealer_net: float | None = None
    dealer_net_5d: float | None = None
    institutional_flow_ratio_5d: float | None = None
    institutional_date: str | None = None
    institutional_status: str = "unavailable"
    institutional_streak: dict[str, Any] | None = None

    # Margin (shares & %)
    margin_balance: float | None = None
    margin_balance_change: float | None = None
    short_balance: float | None = None
    short_balance_change: float | None = None
    short_margin_ratio: float | None = None  # 10.0 = 10%
    margin_date: str | None = None
    margin_status: str = "unavailable"

    # Fundamentals (Valuation, Revenue, Profitability)
    pe: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    revenue_yoy: float | None = None
    revenue_mom: float | None = None
    revenue_yoy_improving: bool | None = None
    revenue_status: str = "unavailable"
    revenue_latest_period: str | None = None
    latest_eps: float | None = None
    financials_as_of: str | None = None
    valuation_as_of: str | None = None

    # Chips (Foreign shareholding & lending)
    foreign_shareholding_ratio: float | None = None
    foreign_shareholding_change_20d: float | None = None
    securities_lending_anomaly: str | None = None

    # Quant Score
    quant_score: float | None = None

    # Explanation of why the stock was selected
    match_reasons: list[str] = Field(default_factory=list)
    risk_status: Literal["clear", "unknown"] | None = None
    strategy_id: str | None = None
    strategy_version: str | None = None
    strategy_signals: list[str] = Field(default_factory=list)
    consensus_hit_count: int | None = None
    consensus_strategy_names: list[str] = Field(default_factory=list)


class DataDatesInfo(BaseModel):
    daily_as_of: str | None = None
    institutional_as_of: str | None = None
    margin_as_of: str | None = None


class ScreenerCoverageInfo(BaseModel):
    total_universe: int = 0
    screened_universe: int = 0
    fundamental_cached_count: int = 0
    chips_cached_count: int = 0
    coverage_note: str = ""


class TaiwanScreenerResponse(BaseModel):
    items: list[ScreenerResultItem]
    total: int
    page: int
    page_size: int
    sort_by: str
    sort_order: str
    data_dates: DataDatesInfo
    degraded_sections: list[str] = []
    coverage_info: ScreenerCoverageInfo | None = None
    missing_quote_count: int = 0
    quote_coverage_status: Literal["verified", "unavailable"] | None = None
    risk_unknown_count: int = 0
    risk_source_status: Literal["available", "partial", "unavailable"] | None = None
    risk_source_statuses: dict[str, str] = {}
    risk_source_as_of: str | None = None
    trend_indicator_basis: Literal["raw", "pit_adjusted"] = "raw"
    trend_adjustment_status: Literal["verified", "partial", "unavailable"] | None = None
    risk_target_date: str | None = None
    strategy_id: str | None = None
    strategy_name: str | None = None
    strategy_version: str | None = None
    strategy_readiness: Literal["ready", "degraded", "unavailable"] | None = None
    strategy_readiness_reasons: list[str] = []
    strategy_coverage: dict[str, int] = {}
    revenue_evidence: dict[str, Any] | None = None


class TaiwanScreenerService:
    """Production Taiwan Market Screener Service (Batch & Local)."""

    def __init__(
        self,
        security_master: TaiwanSecurityMaster | None = None,
        daily_store: TaiwanDailyStore | None = None,
        institutional_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        finmind_cache: FinMindCache | None = None,
        fundamental_chips_service: Any | None = None,
        action_store: CorporateActionStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        census_store: ObservedUniverseStore | None = None,
        revenue_evidence_store: MonthlyRevenueEvidenceStore | None = None,
    ) -> None:
        self.security_master = security_master or get_security_master()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.institutional_store = institutional_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.cache = finmind_cache or FinMindCache()
        self._fundamental_chips_service = fundamental_chips_service
        self.action_store = action_store or CorporateActionStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        # Forward readiness depends on official observed-universe evidence.
        # Keep the dependency injectable for tests, but use the authoritative
        # local store by default so normal screening has the same evidence as
        # the lock path.
        self.census_store = census_store or ObservedUniverseStore()
        self.revenue_evidence_store = revenue_evidence_store or MonthlyRevenueEvidenceStore()

    def _get_fundamental_chips_service(self):
        if self._fundamental_chips_service is None:
            from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService
            self._fundamental_chips_service = TaiwanFundamentalChipsService(cache=self.cache)
        return self._fundamental_chips_service

    def run(self, req: TaiwanScreenerRequest) -> TaiwanScreenerResponse:
        if req.preset == "trend_liquidity_v1":
            req = req.model_copy(update={
                "exchange": "ALL", "instrument": "stock",
                "amount_min": TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD,
                "above_ma20": True, "momentum_5d_min": 0.0,
                "sort_by": "trend_liquidity_v1", "sort_order": "desc",
                "page": 1, "page_size": TREND_LIQUIDITY_V1_BATCH_SIZE,
            })
        elif req.preset in V2_STRATEGY_IDS:
            req = req.model_copy(update={
                "exchange": "ALL", "instrument": "stock",
                "amount_min": 50_000_000,
                "sort_by": req.preset, "sort_order": "desc",
                "page": 1, "page_size": 20,
                "above_ma20": None, "momentum_5d_min": None,
            })
        strategy_id = req.preset
        metadata = strategy_metadata(strategy_id) if strategy_id else None
        # Step 1: Universe from TaiwanSecurityMaster
        universe_df = self._get_universe(req.exchange, req.instrument)
        if req.symbol_scope is not None:
            requested = list(dict.fromkeys(req.symbol_scope))
            universe_df = universe_df.filter(pl.col("symbol").is_in(requested))
        if universe_df.is_empty():
            return TaiwanScreenerResponse(
                items=[], total=0, page=req.page, page_size=req.page_size,
                sort_by=req.sort_by, sort_order=req.sort_order,
                data_dates=DataDatesInfo(),
                strategy_id=strategy_id,
                strategy_name=metadata["name"] if metadata else None,
                strategy_version=metadata["version"] if metadata else None,
                strategy_readiness="unavailable" if strategy_id else None,
                strategy_readiness_reasons=["標的資料不可用"] if strategy_id else [],
            )

        valid_symbols = universe_df["symbol"].to_list()

        # Step 2: Batch read latest per symbol from TaiwanDailyStore
        latest_daily = self._normalize_daily_frame(
            self.daily_store.read_latest_per_symbol(valid_symbols),
        )
        if latest_daily.is_empty():
            return TaiwanScreenerResponse(
                items=[], total=0, page=req.page, page_size=req.page_size,
                sort_by=req.sort_by, sort_order=req.sort_order,
                data_dates=DataDatesInfo(),
                strategy_id=strategy_id,
                strategy_name=metadata["name"] if metadata else None,
                strategy_version=metadata["version"] if metadata else None,
                strategy_readiness="unavailable" if strategy_id else None,
                strategy_readiness_reasons=["行情資料不可用"] if strategy_id else [],
            )

        daily_as_of = str(latest_daily["date"].max()) if not latest_daily.is_empty() else None
        risk_target_date: date | None = None
        if req.preset in STRATEGY_IDS:
            def observed(day: date):
                return self.census_store.day_evidence("TWSE", day, calendar=self.calendar)

            risk_target_date = self.calendar.next_potential_session(
                date.fromisoformat(daily_as_of),
                observed if self.census_store is not None else None,
            )
        revenue_evidence: RevenueEvidenceSnapshot | None = None
        if risk_target_date is not None:
            # Strategy data must have been public before the batch's entry
            # session opens; only strictly earlier official observations count.
            revenue_evidence = self.revenue_evidence_store.evidence_as_of(
                event_market_open(risk_target_date)
            )
        elif req.extended_factors:
            # Current-state consumers use the same official observations,
            # restricted to those already made before this query.
            revenue_evidence = self.revenue_evidence_store.evidence_as_of(taipei_now())
        missing_quote_count = len(valid_symbols) - latest_daily.filter(
            pl.col("date") == latest_daily["date"].max()
        ).height
        quote_coverage_status = None
        if req.preset in STRATEGY_IDS:
            quote_coverage_status = self._quote_coverage_status(
                universe_df, latest_daily, date.fromisoformat(daily_as_of)
            )

        # Step 3: Compute batch indicators (needs up to 30 trading days of history)
        df_indicators = self._compute_batch_indicators(
            valid_symbols, extended=req.preset in V2_STRATEGY_IDS
        )
        trend_adjustment_status: Literal["verified", "partial", "unavailable"] | None = None
        trend_indicators: pl.DataFrame | None = None
        if req.preset == "trend_liquidity_v1":
            trend_indicators, trend_adjustment_status, trend_degraded_section = self._compute_trend_indicators(
                valid_symbols, date.fromisoformat(daily_as_of)
            )
            if trend_indicators is None or trend_indicators.is_empty():
                return TaiwanScreenerResponse(
                    items=[], total=0, page=1, page_size=20,
                    sort_by="trend_liquidity_v1", sort_order="desc",
                    data_dates=DataDatesInfo(daily_as_of=daily_as_of),
                    missing_quote_count=missing_quote_count,
                    quote_coverage_status=quote_coverage_status,
                    degraded_sections=([trend_degraded_section] if trend_degraded_section else []),
                    trend_indicator_basis="pit_adjusted",
                    trend_adjustment_status=trend_adjustment_status,
                    risk_target_date=risk_target_date.isoformat(),
                    strategy_id=strategy_id,
                    strategy_name=metadata["name"] if metadata else None,
                    strategy_version=metadata["version"] if metadata else None,
                    strategy_readiness="unavailable",
                    strategy_readiness_reasons=[trend_degraded_section] if trend_degraded_section else ["趨勢資料不可用"],
                )

        # Step 4: Join Universe + Latest Daily + Indicators
        combined = universe_df.join(latest_daily, on="symbol", how="inner")
        if not df_indicators.is_empty():
            combined = combined.join(df_indicators, on="symbol", how="left")
        else:
            combined = self._add_null_cols(combined, ["change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"])
        if trend_indicators is not None:
            combined = combined.join(trend_indicators, on="symbol", how="inner").with_columns(
                pl.col("trend_ma20").alias("ma20"),
                pl.col("trend_momentum_5d").alias("momentum_5d"),
            ).drop("trend_ma20", "trend_momentum_5d")

        # Step 5: MarketProfile Price Limits & Distance Calculation
        combined = self._enrich_price_limits(combined)

        # Step 6: Batch Join Institutional & Margin
        combined, inst_date, margin_date, degraded = self._join_institutional_margin(
            combined, valid_symbols, strategy_id=req.preset,
            five_day_flow=req.extended_factors,
        )
        if req.streak_min_days is not None:
            combined = self._join_institutional_streak(combined, req)
        if req.preset == "trend_liquidity_v1" and trend_degraded_section:
            degraded = [*degraded, trend_degraded_section]

        # Step 6.5: Batch Join Cached Fundamentals & Chips
        combined, fund_count, chips_count = self._join_cached_fundamentals_chips(
            combined, valid_symbols, revenue_evidence=revenue_evidence,
        )

        if req.preset == "trend_liquidity_v1":
            # A missing/stale quote or indicator cannot qualify as a fresh candidate.
            combined = trend_liquidity_v1_candidates(combined, latest_daily["date"].max())
        readiness_frame = combined
        if req.preset in V2_STRATEGY_IDS:
            readiness_frame = apply_strategy(combined, req.preset, filter_candidates=False)
            combined = apply_strategy(combined, req.preset)

        # Step 7: Apply Strongly Typed Filters
        filtered = self._apply_filters(combined, req)

        risk_statuses: dict[str, str] = {}
        risk_source_status: Literal["available", "partial", "unavailable"] | None = None
        risk_source_statuses: dict[str, str] = {}
        risk_source_as_of: str | None = None
        if req.preset in STRATEGY_IDS:
            from app.taiwan.events_service import get_event_service

            try:
                event_svc = get_event_service()
                cached_events, risk_source_status, risk_source_as_of = (
                    event_svc.get_cached_regulatory_snapshot()
                )
                get_sources_status = getattr(event_svc, "get_last_sources_status", None)
                if callable(get_sources_status):
                    _last_status, risk_source_statuses = get_sources_status()
                if risk_source_status == "unavailable":
                    risk_source_statuses = {
                        source: "stale" if status == "available" else status
                        for source, status in risk_source_statuses.items()
                    }
            except Exception:
                event_svc = None
                cached_events, risk_source_status, risk_source_as_of = [], "unavailable", None
            excluded: set[str] = set()
            for symbol in filtered["symbol"].to_list():
                try:
                    if event_svc is None:
                        raise RuntimeError("event service unavailable")
                    risk = event_svc.check_symbol_risk_status(
                        symbol, target_date=risk_target_date, events=cached_events
                    )
                    if (risk["is_disposition"] or risk["is_suspended"]
                            or risk.get("has_risk_event", False)):
                        excluded.add(symbol)
                    else:
                        risk_statuses[symbol] = (
                            "clear" if risk_source_status == "available" else "unknown"
                        )
                except Exception:
                    risk_statuses[symbol] = "unknown"
            if excluded:
                filtered = filtered.filter(~pl.col("symbol").is_in(list(excluded)))

        # Step 8: Total count (before pagination)
        total = filtered.height

        # Step 9: Deterministic Sort (with symbol ASC tie-breaker)
        sorted_df = self._apply_sort(filtered, req.sort_by, req.sort_order)

        # Step 10: Pagination
        offset = (req.page - 1) * req.page_size
        paged_df = sorted_df.slice(offset, req.page_size)

        # Step 11: Serialize items (with match reasons)
        items = self._build_items(paged_df, req)
        if req.preset in STRATEGY_IDS:
            for item in items:
                item.risk_status = risk_statuses.get(item.symbol)

        # Step 12: Coverage info
        coverage_note = (
            f"已篩選 {len(valid_symbols)} 檔標的; 基本面快取涵蓋 {fund_count} 檔, "
            f"籌碼補強快取涵蓋 {chips_count} 檔。未快取之標的若設有相關篩選條件將安全排除。"
        )
        coverage_info = ScreenerCoverageInfo(
            total_universe=len(valid_symbols),
            screened_universe=len(valid_symbols),
            fundamental_cached_count=fund_count,
            chips_cached_count=chips_count,
            coverage_note=coverage_note,
        )

        readiness = None
        readiness_reasons: list[str] = []
        strategy_coverage: dict[str, int] = {}
        if req.preset in STRATEGY_IDS:
            readiness, readiness_reasons, strategy_coverage = strategy_readiness(
                readiness_frame, req.preset,
                quote_coverage_status=quote_coverage_status,
                risk_source_status=risk_source_status,
                revenue_evidence_status=revenue_evidence.status if revenue_evidence else None,
                revenue_mismatch_count=len(revenue_evidence.mismatches) if revenue_evidence else 0,
            )

        return TaiwanScreenerResponse(
            items=items,
            total=total,
            page=req.page,
            page_size=req.page_size,
            sort_by=req.sort_by,
            sort_order=req.sort_order,
            data_dates=DataDatesInfo(
                daily_as_of=daily_as_of,
                institutional_as_of=inst_date,
                margin_as_of=margin_date,
            ),
            degraded_sections=degraded,
            coverage_info=coverage_info,
            missing_quote_count=missing_quote_count if req.preset else 0,
            quote_coverage_status=quote_coverage_status,
            risk_unknown_count=sum(i.risk_status == "unknown" for i in items),
            risk_source_status=risk_source_status,
            risk_source_statuses=risk_source_statuses,
            risk_source_as_of=risk_source_as_of,
            trend_indicator_basis="pit_adjusted" if req.preset == "trend_liquidity_v1" else "raw",
            trend_adjustment_status=trend_adjustment_status,
            risk_target_date=risk_target_date.isoformat() if risk_target_date else None,
            strategy_id=strategy_id,
            strategy_name=metadata["name"] if metadata else None,
            strategy_version=metadata["version"] if metadata else None,
            strategy_readiness=readiness,
            strategy_readiness_reasons=readiness_reasons,
            strategy_coverage=strategy_coverage,
            revenue_evidence=(
                self._revenue_evidence_summary(revenue_evidence, readiness_frame)
                if revenue_evidence is not None else None
            ),
        )

    @staticmethod
    def _revenue_evidence_summary(
        evidence: RevenueEvidenceSnapshot, frame: pl.DataFrame
    ) -> dict[str, Any]:
        """Audit summary of the official revenue evidence behind this run."""
        available = frame.filter(pl.col("revenue_status") == "available")
        periods = available["revenue_latest_period"].drop_nulls().value_counts(sort=True)
        latest_period = periods.row(0)[0] if periods.height else None
        return {
            "status": evidence.status,
            "publication_basis": "official_observation",
            "cutoff": evidence.cutoff.isoformat(),
            "first_observed_at": evidence.first_observed_at,
            "latest_observed_at": evidence.latest_observed_at,
            "digest": evidence.digest,
            "page_count": evidence.page_count,
            "stale_page_count": evidence.stale_page_count,
            "missing_pages": evidence.missing_pages[:24],
            "symbols_with_evidence": len(evidence.rows_by_symbol),
            "revenue_available_count": available.height,
            "revenue_yoy_improvement_count": available.filter(
                pl.col("revenue_yoy_improving").is_not_null()
            ).height,
            "mismatch_count": len(evidence.mismatches),
            "mismatch_symbols": sorted(evidence.mismatches)[:20],
            "latest_period": latest_period,
            "latest_period_count": periods.row(0)[1] if periods.height else 0,
            "status_counts": dict(
                frame["revenue_status"].value_counts(sort=True).iter_rows()
            ),
        }

    def _get_universe(self, exchange: ExchangeFilter, instrument: InstrumentFilter) -> pl.DataFrame:
        """Fetch strictly supported symbols from TaiwanSecurityMaster as a Polars DataFrame."""
        df = self.security_master.to_dataframe(supported_only=True)
        if df.is_empty():
            return df

        # Filter by active status and supported instrument types (stock & etf only)
        df = df.filter(pl.col("listing_status") == "active")
        df = df.filter(pl.col("instrument_type").is_in(["stock", "etf"]))

        if exchange == "TWSE":
            df = df.filter(pl.col("exchange") == "TWSE")
        elif exchange == "TPEX":
            df = df.filter(pl.col("exchange") == "TPEX")

        if instrument == "stock":
            df = df.filter(pl.col("instrument_type") == "stock")
        elif instrument == "etf":
            df = df.filter(pl.col("instrument_type") == "etf")

        return df.select(["symbol", "name", "exchange", "instrument_type", "industry"]).unique(subset=["symbol"])

    def _compute_batch_indicators(self, symbols: list[str], *, extended: bool = False) -> pl.DataFrame:
        """Compute rolling indicators from past daily store records in a single batch."""
        available_dates = self.daily_store.available_dates()
        if len(available_dates) < 2:
            return pl.DataFrame()

        # v1 keeps its existing 35-session window. v2 needs 60 sessions for
        # the fixed breakout and MA60 observations.
        lookback = 65 if extended else 35
        start_d = available_dates[max(0, len(available_dates) - lookback)]
        end_d = available_dates[-1]

        hist = self._normalize_daily_frame(self.daily_store.read_range(symbols, start_d, end_d))
        if hist.is_empty():
            return pl.DataFrame()

        # Sort symbol ASC, date ASC
        hist = hist.sort(["symbol", "date"])

        # Compute per-symbol metrics using Polars window functions
        expressions = [
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("change_pct"),
            pl.col("close").rolling_mean(5).over("symbol").alias("ma5"),
            pl.col("close").rolling_mean(10).over("symbol").alias("ma10"),
            pl.col("close").rolling_mean(20).over("symbol").alias("ma20"),
            (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0).alias("momentum_5d"),
            (pl.col("volume") / pl.col("volume").rolling_mean(5).over("symbol")).alias("vol_ratio_5d"),
        ]
        if extended:
            prior_volume_20 = pl.col("volume").shift(1).rolling_mean(20).over("symbol")
            prior_high_20 = pl.col("high").shift(1).rolling_max(20).over("symbol")
            prior_high_60 = pl.col("high").shift(1).rolling_max(60).over("symbol")
            expressions.extend([
                pl.col("close").rolling_mean(60).over("symbol").alias("ma60"),
                (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).alias("momentum_20d"),
                (pl.col("volume") / prior_volume_20).alias("vol_ratio_20d"),
                (pl.col("close") / prior_high_20 - 1.0).alias("breakout_20d_strength"),
                (pl.col("close") / prior_high_60 - 1.0).alias("breakout_60d_strength"),
            ])
        hist = hist.with_columns(expressions)
        if extended:
            hist = hist.with_columns([
                (pl.col("close") - pl.col("ma20")).alias("_ma20_distance"),
                (pl.col("momentum_5d") - pl.col("momentum_5d").shift(1).over("symbol"))
                .alias("momentum_acceleration"),
            ])

        # RSI 14 — canonical Wilder smoothing, shared with the technical panel.
        # This used to be an SMA-based variant, which gave the same symbol two
        # different RSI values depending on which screen you opened.
        bar_count = pl.col("close").count().over("symbol")
        hist = hist.with_columns(
            pl.when(bar_count >= MIN_BARS_RSI_14)
            .then(wilder_rsi_expr())
            .otherwise(None)
            .alias("rsi_14")
        )

        # Keep only the latest row per symbol
        columns = ["symbol", "change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"]
        if extended:
            columns.extend([
                "ma60", "momentum_20d", "vol_ratio_20d", "momentum_acceleration",
                "breakout_20d_strength", "breakout_60d_strength",
            ])
        latest_inds = (
            hist.with_columns(pl.col("date").max().over("symbol").alias("_max_d"))
            .filter(pl.col("date") == pl.col("_max_d"))
            .select(columns)
        )
        return latest_inds

    def _compute_trend_indicators(
        self, symbols: list[str], as_of: date,
    ) -> tuple[pl.DataFrame | None, TrendAdjustmentStatus, str | None]:
        """Calculate the formal preset's trend signals on a verified PIT price basis."""
        dates = self._recent_verified_sessions(as_of, TREND_LIQUIDITY_V1_TREND_SESSIONS)
        if dates is None:
            return None, "unavailable", "trend_history"
        start = dates[0]
        events = self.action_store.read_verified_window(start, as_of)
        if events is None:
            return None, "unavailable", "corporate_actions"
        hist = self._normalize_daily_frame(self.daily_store.read_range(symbols, start, as_of))
        return compute_trend_liquidity_v1_indicators(hist, dates, events, as_of)

    def _recent_verified_sessions(self, as_of: date, count: int) -> list[date] | None:
        """Do not bridge an absent weekday that might be a lost daily partition."""
        available = set(self.daily_store.available_dates())
        sessions: list[date] = []
        cursor = as_of
        for _ in range(90):
            evidence = (self.census_store.day_evidence("TWSE", cursor, calendar=self.calendar)
                        if self.census_store is not None
                        else self.calendar.day_evidence(cursor, "TWSE"))
            observed_session = cursor in available and (
                evidence.status == "unresolved"
                or (evidence.status == "non_trading" and evidence.evidence_source == "calendar_rule")
            )
            if evidence.status == "trading" or observed_session:
                sessions.append(cursor)
                if len(sessions) == count:
                    return list(reversed(sessions))
            elif evidence.status == "unresolved":
                return None
            cursor -= timedelta(days=1)
        return None

    def _quote_coverage_status(
        self, universe: pl.DataFrame, latest: pl.DataFrame, as_of: date,
    ) -> Literal["verified", "unavailable"]:
        """Prove missing quotes are genuinely absent from the official daily census."""
        quoted = set(latest.filter(pl.col("date") == as_of)["symbol"].to_list())
        missing = set(universe["symbol"].to_list()) - quoted
        if not missing:
            return "verified"
        if self.census_store is None:
            return "unavailable"
        for exchange in ("TWSE", "TPEX"):
            exchange_missing = {s for s in missing if s.endswith(f".{exchange}")}
            if not exchange_missing:
                continue
            try:
                evidence = self.census_store.day_evidence(exchange, as_of, calendar=self.calendar)
                if evidence.status != "trading" or not self.census_store.has(exchange, as_of):
                    return "unavailable"
                official = pl.read_parquet(self.census_store.partition_path(exchange, as_of))
            except (OSError, ValueError, pl.exceptions.PolarsError):
                return "unavailable"
            observed = {f"{code}.{exchange}" for code in official["raw_code"].to_list()}
            if exchange_missing & observed:
                return "unavailable"
        return "verified"

    def _enrich_price_limits(self, df: pl.DataFrame) -> pl.DataFrame:
        """Enrich with tick-size aware price limits and distance metrics."""
        price_limit_pct: list[float | None] = []
        is_no_limit_flags: list[bool] = []
        limit_up: list[float | None] = []
        limit_down: list[float | None] = []
        distance_to_upper_limit: list[float | None] = []
        distance_to_lower_limit: list[float | None] = []
        for r in df.iter_rows(named=True):
            sym = r["symbol"]
            close = r.get("close")
            inst = self.security_master.get_instrument(sym)

            if inst is None or close is None:
                price_limit_pct.append(None)
                is_no_limit_flags.append(False)
                limit_up.append(None)
                limit_down.append(None)
                distance_to_upper_limit.append(None)
                distance_to_lower_limit.append(None)
                continue

            try:
                limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
            except ValueError as e:
                logger.debug("Unconfirmed regulatory profile for %s: %s", inst.symbol, e)
                # Unconfirmed profile: cannot verify regulatory limit safely.
                # Must set price limit fields to None and NOT match near-limit filters.
                price_limit_pct.append(None)
                is_no_limit_flags.append(False)
                limit_up.append(None)
                limit_down.append(None)
                distance_to_upper_limit.append(None)
                distance_to_lower_limit.append(None)
                continue

            no_limit = limit_pct is None

            if no_limit:
                price_limit_pct.append(None)
                is_no_limit_flags.append(True)
                limit_up.append(None)
                limit_down.append(None)
                distance_to_upper_limit.append(None)
                distance_to_lower_limit.append(None)
                continue

            upper, lower = MarketProfileBridge.calc_limits(close, inst)
            dist_up = (upper - close) / close if (upper and close > 0) else None
            dist_dn = (close - lower) / close if (lower and close > 0) else None

            price_limit_pct.append(limit_pct)
            is_no_limit_flags.append(False)
            limit_up.append(upper)
            limit_down.append(lower)
            distance_to_upper_limit.append(dist_up)
            distance_to_lower_limit.append(dist_dn)

        # Keep the upstream schema intact. Building a new DataFrame from row
        # dictionaries makes Polars infer a shared dtype from the first
        # non-null value, which can fail when a provider/cache mixes numeric,
        # string and null values. Explicit Series keep nulls as null and make
        # price-limit enrichment safe for partial snapshots.
        return df.with_columns([
            pl.Series("price_limit_pct", price_limit_pct, dtype=pl.Float64),
            pl.Series("is_no_limit", is_no_limit_flags, dtype=pl.Boolean),
            pl.Series("limit_up", limit_up, dtype=pl.Float64),
            pl.Series("limit_down", limit_down, dtype=pl.Float64),
            pl.Series("distance_to_upper_limit", distance_to_upper_limit, dtype=pl.Float64),
            pl.Series("distance_to_lower_limit", distance_to_lower_limit, dtype=pl.Float64),
        ])

    def _join_institutional_margin(
        self, df: pl.DataFrame, symbols: list[str], strategy_id: str | None = None,
        five_day_flow: bool = False,
    ) -> tuple[pl.DataFrame, str | None, str | None, list[str]]:
        """Join institutional and margin metadata safely via Polars batch join."""
        degraded = []
        inst_date = None
        margin_date = None

        # 1. Batch read latest institutional
        try:
            inst_df = self.institutional_store.read_latest_per_symbol(symbols)
            if not inst_df.is_empty():
                inst_date = str(inst_df["date"].max())
                # Select fields to join
                inst_join = inst_df.select([
                    pl.col("symbol"),
                    pl.col("foreign_net").cast(pl.Float64, strict=False).alias("foreign_net"),
                    pl.col("investment_trust_net").cast(pl.Float64, strict=False).alias("investment_trust_net"),
                    pl.col("dealer_net").cast(pl.Float64, strict=False).alias("dealer_net"),
                    pl.col("date").cast(pl.String).alias("institutional_date"),
                    pl.col("status").alias("institutional_status"),
                ])
                df = df.join(inst_join, on="symbol", how="left")
            else:
                df = self._add_null_cols(df, [
                    "foreign_net", "investment_trust_net", "dealer_net",
                    "institutional_date", "institutional_status"
                ])
        except Exception as e:
            logger.warning("Batch read institutional failed in screener: %s", e)
            degraded.append("institutional")
            df = self._add_null_cols(df, [
                "foreign_net", "investment_trust_net", "dealer_net",
                "institutional_date", "institutional_status"
            ])

        # Keep v1's existing placeholder fields unchanged. v2 explicitly opts
        # into a verified five-session institutional aggregate.
        if strategy_id in V2_STRATEGY_IDS or five_day_flow:
            try:
                end = date.fromisoformat(str(df["date"].max()))
                start = end - timedelta(days=14)
                history = self.institutional_store.read_range(symbols, start, end)
                if not history.is_empty():
                    required_dates = sorted(history["date"].drop_nulls().unique().to_list())[-5:]
                    history = history.filter(pl.col("date").is_in(required_dates))
                    if len(required_dates) == 5:
                        valid_history = history.filter(
                            pl.col("status").is_in(["available", "official"])
                        )
                        complete_symbols = (
                            valid_history.group_by("symbol")
                            .agg([
                                pl.len().alias("_valid_rows"),
                                pl.col("date").n_unique().alias("_valid_sessions"),
                            ])
                            .filter(
                                (pl.col("_valid_rows") == len(required_dates))
                                & (pl.col("_valid_sessions") == len(required_dates))
                            )["symbol"]
                            .to_list()
                        )
                        five_day = valid_history.filter(
                            pl.col("symbol").is_in(complete_symbols)
                        ).group_by("symbol").agg([
                            pl.col("foreign_net").cast(pl.Float64, strict=False).sum().alias("foreign_net_5d"),
                            pl.col("investment_trust_net").cast(pl.Float64, strict=False).sum().alias("investment_trust_net_5d"),
                            pl.col("dealer_net").cast(pl.Float64, strict=False).sum().alias("dealer_net_5d"),
                        ]).with_columns(
                            (pl.col("foreign_net_5d") + pl.col("investment_trust_net_5d")
                             + pl.col("dealer_net_5d")).alias("institutional_flow_5d")
                        )
                        df = df.join(five_day, on="symbol", how="left")
                        if "volume" in df.columns:
                            volume = pl.col("volume").cast(pl.Float64, strict=False)
                            df = df.with_columns(
                                pl.when(volume.is_not_null() & (volume > 0))
                                .then(pl.col("institutional_flow_5d") / volume)
                                .otherwise(None)
                                .alias("institutional_flow_ratio_5d")
                            )
                        else:
                            df = df.with_columns(
                                pl.lit(None, dtype=pl.Float64).alias("institutional_flow_ratio_5d")
                            )
                        df = df.drop("institutional_flow_5d")
                    else:
                        df = self._add_null_cols(df, [
                            "foreign_net_5d", "investment_trust_net_5d", "dealer_net_5d",
                            "institutional_flow_ratio_5d",
                        ])
                else:
                    df = self._add_null_cols(df, [
                        "foreign_net_5d", "investment_trust_net_5d", "dealer_net_5d",
                        "institutional_flow_ratio_5d",
                    ])
            except Exception as e:
                logger.warning("Five-day institutional aggregate failed in screener: %s", e)
                degraded.append("institutional_v2")
                df = self._add_null_cols(df, [
                    "foreign_net_5d", "investment_trust_net_5d", "dealer_net_5d",
                    "institutional_flow_ratio_5d",
                ])
        elif "foreign_net_5d" not in df.columns:
            df = self._add_null_cols(df, ["foreign_net_5d", "investment_trust_net_5d"])

        # 2. Batch read latest margin
        try:
            margin_df = self.margin_store.read_latest_per_symbol(symbols)
            if not margin_df.is_empty():
                margin_date = str(margin_df["date"].max())
                margin_join = margin_df.select([
                    pl.col("symbol"),
                    pl.col("margin_balance").cast(pl.Float64, strict=False).alias("margin_balance"),
                    pl.col("margin_change").cast(pl.Float64, strict=False).alias("margin_balance_change"),
                    pl.col("short_balance").cast(pl.Float64, strict=False).alias("short_balance"),
                    pl.col("short_change").cast(pl.Float64, strict=False).alias("short_balance_change"),
                    pl.col("short_margin_ratio").cast(pl.Float64, strict=False).alias("short_margin_ratio"),
                    pl.col("date").cast(pl.String).alias("margin_date"),
                    pl.col("status").alias("margin_status"),
                ])
                df = df.join(margin_join, on="symbol", how="left")
            else:
                df = self._add_null_cols(df, [
                    "margin_balance", "margin_balance_change", "short_balance", "short_balance_change", "short_margin_ratio",
                    "margin_date", "margin_status"
                ])
        except Exception as e:
            logger.warning("Batch read margin failed in screener: %s", e)
            degraded.append("margin")
            df = self._add_null_cols(df, [
                "margin_balance", "margin_balance_change", "short_balance", "short_balance_change", "short_margin_ratio",
                "margin_date", "margin_status"
            ])

        return df, inst_date, margin_date, degraded

    def _join_cached_fundamentals_chips(
        self, df: pl.DataFrame, symbols: list[str], *,
        revenue_evidence: RevenueEvidenceSnapshot | None = None,
    ) -> tuple[pl.DataFrame, int, int]:
        """Join cached fundamental metrics, extra chips, and quant scores safely from local store.

        Strategy runs pass ``revenue_evidence``: monthly revenue then comes only
        from official observations made before the selection cutoff, and the
        FinMind revenue cache is not consulted at all.
        """
        fc_svc = self._get_fundamental_chips_service()

        cached_rev_keys = (
            {} if revenue_evidence is not None
            else self._cached_symbol_keys("TaiwanStockMonthRevenue", symbols)
        )
        cached_fin_keys = self._cached_symbol_keys("TaiwanStockFinancialStatements", symbols)
        cached_val_keys = self._cached_symbol_keys("TaiwanValuation", symbols)
        cached_share_keys = self._cached_symbol_keys("TaiwanStockShareholding", symbols)
        cached_lend_keys = self._cached_symbol_keys("TaiwanStockSecuritiesLending", symbols)

        fundamental_symbols = set(cached_rev_keys) | set(cached_fin_keys) | set(cached_val_keys)
        # Daily chip datasets stay cached for days (weekend bridge); only the latest session, or the
        # one before it (publication lag), may feed a filter. Older values are excluded, not reused.
        sessions = sorted(self.daily_store.available_dates())
        chips_floor = (sessions[-2] if len(sessions) > 1 else sessions[-1]).isoformat() if sessions else None
        chips_symbols: set[str] = set()

        # Live Quant scores
        quant_scores: dict[str, float] = {}
        try:
            from app.taiwan.quant.live_store import LiveLedger
            ledger = LiveLedger()
            runs = ledger.runs(limit=1)
            if runs and runs[0] and "snapshot" in runs[0]:
                signals = runs[0]["snapshot"].get("signals", [])
                for s in signals:
                    sym = s.get("symbol")
                    score = s.get("score")
                    if sym and score is not None:
                        quant_scores[str(sym)] = float(score)
        except Exception as e:
            logger.debug("Failed to read live quant scores for screener: %s", e)

        pe_map: dict[str, float | None] = {}
        pb_map: dict[str, float | None] = {}
        dy_map: dict[str, float | None] = {}
        rev_yoy_map: dict[str, float | None] = {}
        rev_mom_map: dict[str, float | None] = {}
        rev_yoy_improving_map: dict[str, bool | None] = {}
        rev_yoy_improvement_map: dict[str, float | None] = {}
        rev_status_map: dict[str, str] = {}
        rev_period_map: dict[str, str | None] = {}
        eps_map: dict[str, float | None] = {}
        financials_as_of_map: dict[str, str | None] = {}
        valuation_as_of_map: dict[str, str | None] = {}
        net_inc_map: dict[str, float | None] = {}
        share_ratio_map: dict[str, float | None] = {}
        share_chg20_map: dict[str, float | None] = {}
        lend_anomaly_map: dict[str, str | None] = {}

        for sym, cache_key in cached_val_keys.items():
            cached = self.cache.get("TaiwanValuation", cache_key)
            if cached and cached.get("data"):
                d = cached["data"]
                pe_map[sym] = parse_number(d.get("pe"))
                pb_map[sym] = parse_number(d.get("pb"))
                dy_map[sym] = parse_number(d.get("dividend_yield"))
                valuation_as_of_map[sym] = cached.get("data_date")

        revenue_rows: dict[str, tuple[list[dict[str, Any]], str | None, str]] = {}
        for sym, cache_key in cached_rev_keys.items():
            cached = self.cache.get("TaiwanStockMonthRevenue", cache_key)
            if cached and cached.get("data"):
                revenue_rows[sym] = (cached["data"], cached.get("data_date"), cached.get("fetched_at", ""))
        if revenue_evidence is not None:
            evidence_status = {
                "missing": "evidence_missing",
                "not_observed_before_cutoff": "publication_unknown",
                "stale": "stale",
                "incomplete": "evidence_incomplete",
            }.get(revenue_evidence.status)
            for sym in df["symbol"].to_list():
                if evidence_status is not None:
                    rev_status_map[sym] = evidence_status
                elif sym in revenue_evidence.mismatches:
                    rev_status_map[sym] = "source_mismatch"
                elif sym not in revenue_evidence.rows_by_symbol:
                    rev_status_map[sym] = "evidence_missing"
                else:
                    revenue_rows[sym] = (
                        revenue_evidence.rows_by_symbol[sym], None,
                        revenue_evidence.latest_observed_at or "",
                    )

        for sym, (rows, data_date, fetched_at) in revenue_rows.items():
            # PIT selection already happened for official evidence; the shared
            # calculation below is the unchanged YoY/MoM definition.
            rev_data = fc_svc._process_month_revenue(rows, data_date, fetched_at)
            rev_yoy_map[sym] = rev_data.yoy
            rev_mom_map[sym] = rev_data.mom
            rev_period_map[sym] = rev_data.latest_year_month
            valid_yoy = [item.yoy for item in rev_data.trend if item.yoy is not None]
            if rev_data.meta and rev_data.meta.status == "available" and rev_data.yoy is not None:
                rev_status_map[sym] = "available"
            else:
                rev_status_map[sym] = (
                    "insufficient_history" if revenue_evidence is not None else "unavailable"
                )
            if len(valid_yoy) >= 2:
                rev_yoy_improvement_map[sym] = valid_yoy[-1] - valid_yoy[-2]
                rev_yoy_improving_map[sym] = valid_yoy[-1] > valid_yoy[-2]

        for sym, cache_key in cached_fin_keys.items():
            cached = self.cache.get("TaiwanStockFinancialStatements", cache_key)
            if cached and cached.get("data"):
                fin_data = fc_svc._process_financial_statements(
                    cached["data"], cached.get("data_date"), cached.get("fetched_at", "")
                )
                eps_map[sym] = fin_data.latest_eps
                net_inc_map[sym] = fin_data.net_income
                financials_as_of_map[sym] = fin_data.quarter

        for sym, cache_key in cached_share_keys.items():
            cached = self.cache.get("TaiwanStockShareholding", cache_key)
            if cached and cached.get("data") and _chip_date_current(cached, chips_floor):
                chips_symbols.add(sym)
                sh_data = fc_svc._process_shareholding(
                    cached["data"], cached.get("data_date"), cached.get("fetched_at", "")
                )
                share_ratio_map[sym] = sh_data.ratio
                share_chg20_map[sym] = sh_data.change_20d

        for sym, cache_key in cached_lend_keys.items():
            cached = self.cache.get("TaiwanStockSecuritiesLending", cache_key)
            if cached and cached.get("data") and _chip_date_current(cached, chips_floor):
                chips_symbols.add(sym)
                sl_data = fc_svc._process_securities_lending(
                    cached["data"], cached.get("data_date"), cached.get("fetched_at", "")
                )
                lend_anomaly_map[sym] = sl_data.anomaly_status

        df_symbols = df["symbol"].to_list()
        pes = [pe_map.get(s) for s in df_symbols]
        pbs = [pb_map.get(s) for s in df_symbols]
        dys = [dy_map.get(s) for s in df_symbols]
        rev_yoys = [rev_yoy_map.get(s) for s in df_symbols]
        rev_moms = [rev_mom_map.get(s) for s in df_symbols]
        rev_yoy_improving = [rev_yoy_improving_map.get(s) for s in df_symbols]
        rev_yoy_improvement = [rev_yoy_improvement_map.get(s) for s in df_symbols]
        rev_statuses = [rev_status_map.get(s, "unavailable") for s in df_symbols]
        rev_periods = [rev_period_map.get(s) for s in df_symbols]
        epss = [eps_map.get(s) for s in df_symbols]
        financials_as_of = [financials_as_of_map.get(s) for s in df_symbols]
        valuation_as_of = [valuation_as_of_map.get(s) for s in df_symbols]
        net_incs = [net_inc_map.get(s) for s in df_symbols]
        share_ratios = [share_ratio_map.get(s) for s in df_symbols]
        share_chg20s = [share_chg20_map.get(s) for s in df_symbols]
        lend_anomalies = [lend_anomaly_map.get(s) for s in df_symbols]
        q_scores = [quant_scores.get(s) for s in df_symbols]

        df = df.with_columns([
            pl.Series("pe", pes, dtype=pl.Float64),
            pl.Series("pb", pbs, dtype=pl.Float64),
            pl.Series("dividend_yield", dys, dtype=pl.Float64),
            pl.Series("revenue_yoy", rev_yoys, dtype=pl.Float64),
            pl.Series("revenue_mom", rev_moms, dtype=pl.Float64),
            pl.Series("revenue_yoy_improving", rev_yoy_improving, dtype=pl.Boolean),
            pl.Series("revenue_yoy_improvement", rev_yoy_improvement, dtype=pl.Float64),
            pl.Series("revenue_status", rev_statuses, dtype=pl.String),
            pl.Series("revenue_latest_period", rev_periods, dtype=pl.String),
            pl.Series("latest_eps", epss, dtype=pl.Float64),
            pl.Series("financials_as_of", financials_as_of, dtype=pl.String),
            pl.Series("valuation_as_of", valuation_as_of, dtype=pl.String),
            pl.Series("net_income", net_incs, dtype=pl.Float64),
            pl.Series("foreign_shareholding_ratio", share_ratios, dtype=pl.Float64),
            pl.Series("foreign_shareholding_change_20d", share_chg20s, dtype=pl.Float64),
            pl.Series("securities_lending_anomaly", lend_anomalies, dtype=pl.String),
            pl.Series("quant_score", q_scores, dtype=pl.Float64),
        ])

        return df, len(fundamental_symbols), len(chips_symbols)

    def _cached_symbol_keys(self, dataset: str, symbols: list[str]) -> dict[str, str]:
        """Resolve canonical symbols to valid cache keys without exchange ambiguity."""
        cached_keys = set(self.cache.list_cached_symbols(dataset))
        resolved: dict[str, str] = {}
        raw_to_symbols: dict[str, list[str]] = {}
        for symbol in symbols:
            raw_to_symbols.setdefault(symbol.split(".", 1)[0], []).append(symbol)

        for symbol in symbols:
            candidates = [symbol]
            raw_code = symbol.split(".", 1)[0]
            if len(raw_to_symbols[raw_code]) == 1:
                candidates.append(raw_code)
            for cache_key in candidates:
                if cache_key not in cached_keys:
                    continue
                payload = self.cache.get(dataset, cache_key)
                if payload and payload.get("status") == "available" and payload.get("data"):
                    resolved[symbol] = cache_key
                    break
        return resolved


    @staticmethod
    def _is_recent_valid_revenue(cached: dict[str, Any] | None, ref_date: date) -> bool:
        if not cached or cached.get("status") != "available":
            return False
        rows = cached.get("data")
        if not isinstance(rows, list) or not rows:
            return False
        valid_dates: list[date] = []
        for r in rows:
            d_str = str(r.get("date") or "").strip()
            if d_str:
                with contextlib.suppress(Exception):
                    valid_dates.append(date.fromisoformat(d_str[:10]))
        if not valid_dates:
            return False
        past_dates = [d for d in valid_dates if d <= ref_date]
        if not past_dates:
            return False
        latest_d = max(past_dates)
        return 0 <= (ref_date - latest_d).days <= 65

    @staticmethod
    def _is_recent_valid_financials(cached: dict[str, Any] | None, ref_date: date) -> bool:
        if not cached or cached.get("status") != "available":
            return False
        rows = cached.get("data")
        if not isinstance(rows, list) or not rows:
            return False
        valid_dates: list[date] = []
        for r in rows:
            d_str = str(r.get("date") or "").strip()
            if d_str:
                with contextlib.suppress(Exception):
                    valid_dates.append(date.fromisoformat(d_str[:10]))
        if not valid_dates:
            return False
        past_dates = [d for d in valid_dates if d <= ref_date]
        if not past_dates:
            return False
        latest_d = max(past_dates)
        return 0 <= (ref_date - latest_d).days <= 135

    def _join_institutional_streak(self, df: pl.DataFrame, req: TaiwanScreenerRequest) -> pl.DataFrame:
        from app.taiwan.institutional_statistics import TaiwanInstitutionalStatisticsService

        target = df["date"].max()
        snapshot = TaiwanInstitutionalStatisticsService(
            self.institutional_store, self.daily_store, self.security_master,
            self.calendar, self.census_store,
        ).get_snapshot(target, 60)
        values = []
        for item in snapshot.securities:
            metric = getattr(item.investors[req.streak_investor], f"{req.streak_direction}_streak")
            values.append({"symbol": item.symbol, "_institutional_streak_days": metric.value,
                           "_institutional_streak_meta": json.dumps({**metric.model_dump(), "investor": req.streak_investor, "direction": req.streak_direction})})
        if not values:
            return df.with_columns(pl.lit(None, dtype=pl.Float64).alias("_institutional_streak_days"))
        return df.join(pl.DataFrame(values, schema={"symbol": pl.String, "_institutional_streak_days": pl.Float64,
                                                  "_institutional_streak_meta": pl.String}), on="symbol", how="left").with_columns(
            pl.when(pl.col("date") == target).then(pl.col("_institutional_streak_days"))
            .otherwise(None).alias("_institutional_streak_days")
        )

    def _apply_filters(self, df: pl.DataFrame, req: TaiwanScreenerRequest) -> pl.DataFrame:
        """Apply strongly typed whitelist filters."""
        # Industry
        if req.industry and req.industry != "ALL":
            df = df.filter(pl.col("industry") == req.industry)

        keyword = (req.keyword or "").strip().lower()
        if keyword:
            df = df.filter(
                pl.col("name").fill_null("").str.to_lowercase().str.contains(keyword, literal=True)
                | pl.col("symbol").str.to_lowercase().str.starts_with(keyword)
            )

        # Price
        if req.price_min is not None:
            df = df.filter(pl.col("close") >= req.price_min)
        if req.price_max is not None:
            df = df.filter(pl.col("close") <= req.price_max)

        # Change pct (0.05 = 5%)
        if req.change_pct_min is not None:
            df = df.filter(pl.col("change_pct") >= req.change_pct_min)
        if req.change_pct_max is not None:
            df = df.filter(pl.col("change_pct") <= req.change_pct_max)

        # Volume (shares)
        if req.volume_min is not None:
            df = df.filter(pl.col("volume") >= req.volume_min)
        if req.volume_max is not None:
            df = df.filter(pl.col("volume") <= req.volume_max)

        # Amount (TWD)
        if req.amount_min is not None:
            df = df.filter(pl.col("amount") >= req.amount_min)
        if req.amount_max is not None:
            df = df.filter(pl.col("amount") <= req.amount_max)

        # Indicators
        if req.rsi_14_min is not None:
            df = df.filter(pl.col("rsi_14") >= req.rsi_14_min)
        if req.rsi_14_max is not None:
            df = df.filter(pl.col("rsi_14") <= req.rsi_14_max)

        if req.momentum_5d_min is not None:
            df = df.filter(pl.col("momentum_5d") >= req.momentum_5d_min)
        if req.momentum_5d_max is not None:
            df = df.filter(pl.col("momentum_5d") <= req.momentum_5d_max)

        if req.vol_ratio_5d_min is not None:
            df = df.filter(pl.col("vol_ratio_5d") >= req.vol_ratio_5d_min)
        if req.vol_ratio_5d_max is not None:
            df = df.filter(pl.col("vol_ratio_5d") <= req.vol_ratio_5d_max)

        if req.above_ma5 is True:
            df = df.filter(pl.col("close") > pl.col("ma5"))
        elif req.above_ma5 is False:
            df = df.filter(pl.col("close") <= pl.col("ma5"))

        if req.above_ma20 is True:
            basis_close = pl.col("trend_adjusted_close") if req.preset else pl.col("close")
            df = df.filter(basis_close > pl.col("ma20"))
        elif req.above_ma20 is False:
            df = df.filter(pl.col("close") <= pl.col("ma20"))

        # Price limits
        # Note: NO_LIMIT products have distance = null and will not match near_upper/lower
        if req.near_upper_limit is True:
            df = df.filter(pl.col("distance_to_upper_limit") <= 0.03)
        if req.near_lower_limit is True:
            df = df.filter(pl.col("distance_to_lower_limit") <= 0.03)
        if req.distance_to_upper_limit_max is not None:
            df = df.filter(pl.col("distance_to_upper_limit") <= req.distance_to_upper_limit_max)
        if req.distance_to_lower_limit_max is not None:
            df = df.filter(pl.col("distance_to_lower_limit") <= req.distance_to_lower_limit_max)

        # Institutional
        if req.streak_min_days is not None:
            df = df.filter(pl.col("_institutional_streak_days") >= req.streak_min_days)
        if req.foreign_net_min is not None:
            df = df.filter(pl.col("foreign_net") >= req.foreign_net_min)
        if req.foreign_net_max is not None:
            df = df.filter(pl.col("foreign_net") <= req.foreign_net_max)
        if req.investment_trust_net_min is not None:
            df = df.filter(pl.col("investment_trust_net") >= req.investment_trust_net_min)
        if req.investment_trust_net_max is not None:
            df = df.filter(pl.col("investment_trust_net") <= req.investment_trust_net_max)
        if req.dealer_net_min is not None:
            df = df.filter(pl.col("dealer_net") >= req.dealer_net_min)
        if req.dealer_net_max is not None:
            df = df.filter(pl.col("dealer_net") <= req.dealer_net_max)

        # Margin
        if req.margin_balance_change_min is not None:
            df = df.filter(pl.col("margin_balance_change") >= req.margin_balance_change_min)
        if req.margin_balance_change_max is not None:
            df = df.filter(pl.col("margin_balance_change") <= req.margin_balance_change_max)
        if req.short_balance_min is not None:
            df = df.filter(pl.col("short_balance") >= req.short_balance_min)
        if req.short_balance_max is not None:
            df = df.filter(pl.col("short_balance") <= req.short_balance_max)
        if req.short_margin_ratio_min is not None:
            df = df.filter(pl.col("short_margin_ratio") >= req.short_margin_ratio_min)
        if req.short_margin_ratio_max is not None:
            df = df.filter(pl.col("short_margin_ratio") <= req.short_margin_ratio_max)

        # Valuation
        if req.pe_min is not None:
            df = df.filter(pl.col("pe") >= req.pe_min)
        if req.pe_max is not None:
            df = df.filter(pl.col("pe") <= req.pe_max)
        if req.pb_min is not None:
            df = df.filter(pl.col("pb") >= req.pb_min)
        if req.pb_max is not None:
            df = df.filter(pl.col("pb") <= req.pb_max)
        if req.dividend_yield_min is not None:
            df = df.filter(pl.col("dividend_yield") >= req.dividend_yield_min)

        # Revenue
        if req.revenue_yoy_min is not None:
            df = df.filter(pl.col("revenue_yoy") >= req.revenue_yoy_min)
        if req.revenue_mom_min is not None:
            df = df.filter(pl.col("revenue_mom") >= req.revenue_mom_min)

        # Profitability
        if req.eps_min is not None:
            df = df.filter(pl.col("latest_eps") >= req.eps_min)
        if req.net_income_positive is True:
            df = df.filter(pl.col("net_income") > 0)
        elif req.net_income_positive is False:
            df = df.filter(pl.col("net_income") <= 0)

        # Chips
        if req.foreign_shareholding_ratio_min is not None:
            df = df.filter(pl.col("foreign_shareholding_ratio") >= req.foreign_shareholding_ratio_min)
        if req.foreign_shareholding_change_20d_min is not None:
            df = df.filter(pl.col("foreign_shareholding_change_20d") >= req.foreign_shareholding_change_20d_min)
        if req.securities_lending_anomaly_exclude is True:
            df = df.filter(pl.col("securities_lending_anomaly") != "surge")

        # Quant Score
        if req.quant_score_min is not None:
            df = df.filter(pl.col("quant_score") >= req.quant_score_min)

        # Event and Regulatory Filters (A11)
        if (
            req.exclude_disposition is True
            or req.exclude_suspended is True
            or req.exclude_risk_events is True
            or req.recent_revenue_or_earnings is True
        ):
            from app.taiwan.events_service import get_event_service
            event_svc = get_event_service()
            all_syms = df["symbol"].to_list()
            excluded_syms: set[str] = set()
            keep_only_syms: set[str] | None = None

            if req.recent_revenue_or_earnings is True:
                keep_only_syms = set()
                try:
                    max_d_str = str(df["date"].drop_nulls().max())
                    ref_date = date.fromisoformat(max_d_str[:10])
                except Exception:
                    ref_date = taipei_now().date()

                for s in all_syms:
                    code = s.split(".")[0]
                    rev_cached = self.cache.get("TaiwanStockMonthRevenue", code) or self.cache.get("TaiwanStockMonthRevenue", s)
                    fin_cached = self.cache.get("TaiwanStockFinancialStatements", code) or self.cache.get("TaiwanStockFinancialStatements", s)
                    has_rev = self._is_recent_valid_revenue(rev_cached, ref_date)
                    has_fin = self._is_recent_valid_financials(fin_cached, ref_date)
                    if has_rev or has_fin:
                        keep_only_syms.add(s)

            for s in all_syms:
                risk_status = event_svc.check_symbol_risk_status(s)
                if req.exclude_disposition is True and risk_status["is_disposition"]:
                    excluded_syms.add(s)
                if req.exclude_suspended is True and risk_status["is_suspended"]:
                    excluded_syms.add(s)
                if req.exclude_risk_events is True and risk_status["has_risk_event"]:
                    excluded_syms.add(s)

            if excluded_syms:
                df = df.filter(~pl.col("symbol").is_in(list(excluded_syms)))
            if keep_only_syms is not None:
                df = df.filter(pl.col("symbol").is_in(list(keep_only_syms)))

        return df

    def _apply_sort(self, df: pl.DataFrame, sort_by: str, sort_order: str) -> pl.DataFrame:
        """Sort with deterministic symbol ASC tie-breaker."""
        descending = sort_order == "desc"
        if sort_by == "trend_liquidity_v1":
            return rank_trend_liquidity_v1(df)
        if sort_by in V2_STRATEGY_IDS:
            return rank_strategy(df, sort_by)
        if sort_by not in df.columns:
            sort_by = "symbol"
            descending = False

        if sort_by == "symbol":
            return df.sort("symbol", descending=descending)
        return df.sort([sort_by, "symbol"], descending=[descending, False])

    def _build_items(
        self, df: pl.DataFrame, req: TaiwanScreenerRequest | None = None
    ) -> list[ScreenerResultItem]:
        items = []
        for r in df.iter_rows(named=True):
            reasons: list[str] = []
            strategy_signals: list[str] = []
            strategy_id = req.preset if req and req.preset in STRATEGY_IDS else None
            extended = bool(strategy_id) or bool(req and req.extended_factors)
            if strategy_id == "institutional_momentum_v1":
                strategy_signals = [
                    "5日外資淨買超" if r.get("foreign_net_5d") is not None else "法人資料不可用",
                    "5日投信淨買超" if r.get("investment_trust_net_5d") is not None else "法人資料不可用",
                    "法人流量/成交量達固定門檻" if r.get("institutional_flow_ratio_5d") is not None else "法人流量不可用",
                    "收盤站上 MA20" if r.get("ma20") is not None else "MA20 不可用",
                    "成交金額達流動性門檻" if r.get("amount") is not None else "成交金額不可用",
                ]
                reasons.extend(strategy_signals)
            elif strategy_id == "growth_trend_v1":
                strategy_signals = [
                    f"月營收 YoY {r['revenue_yoy']:+.1f}%" if r.get("revenue_yoy") is not None else "月營收資料不可用",
                    "YoY 改善" if r.get("revenue_yoy_improving") is True else "YoY 趨勢資料不足",
                    "收盤站上 MA20/MA60" if r.get("ma60") is not None else "MA60 不可用",
                    "5D/20D 動能為正" if r.get("momentum_20d") is not None else "20D 動能不可用",
                    "成交金額達流動性門檻" if r.get("amount") is not None else "成交金額不可用",
                ]
                reasons.extend(strategy_signals)
            elif strategy_id == "breakout_v1":
                strategy_signals = [
                    "突破 20D/60D 高點",
                    "20D 量比達固定門檻" if r.get("vol_ratio_20d") is not None else "20D 量能不可用",
                    "動能加速度為正" if r.get("momentum_acceleration") is not None else "動能歷史不足",
                    "收盤站上 MA20" if r.get("ma20") is not None else "MA20 不可用",
                    "成交金額達流動性門檻" if r.get("amount") is not None else "成交金額不可用",
                ]
                reasons.extend(strategy_signals)
            elif strategy_id == "multi_factor_consensus_v1":
                names = [name for name in (r.get("consensus_strategy_names") or "").split("、") if name]
                strategy_signals = [f"命中 {r.get('consensus_hit_count') or 0}/3 策略", *names]
                reasons.extend(strategy_signals)
            if req:
                # Revenue
                rev_yoy = r.get("revenue_yoy")
                if req.revenue_yoy_min is not None and rev_yoy is not None:
                    reasons.append(f"營收年增 {rev_yoy:+.1f}%")
                rev_mom = r.get("revenue_mom")
                if req.revenue_mom_min is not None and rev_mom is not None:
                    reasons.append(f"營收月增 {rev_mom:+.1f}%")

                # Profitability
                eps = r.get("latest_eps")
                if req.eps_min is not None and eps is not None:
                    reasons.append(f"最新季 EPS {eps:.2f}元")
                if req.net_income_positive is True and r.get("net_income") is not None and r["net_income"] > 0:
                    reasons.append("稅後淨利為正")

                # Valuation
                pe = r.get("pe")
                if req.pe_max is not None and pe is not None:
                    reasons.append(f"本益比 {pe:.1f}倍")
                dy = r.get("dividend_yield")
                if req.dividend_yield_min is not None and dy is not None:
                    reasons.append(f"殖利率 {dy:.2f}%")
                pb = r.get("pb")
                if req.pb_max is not None and pb is not None:
                    reasons.append(f"股價淨值比 {pb:.2f}倍")

                # Chips
                f_ratio = r.get("foreign_shareholding_ratio")
                if req.foreign_shareholding_ratio_min is not None and f_ratio is not None:
                    reasons.append(f"外資持股 {f_ratio:.1f}%")
                f_chg20 = r.get("foreign_shareholding_change_20d")
                if req.foreign_shareholding_change_20d_min is not None and f_chg20 is not None:
                    reasons.append(f"外資持股20日變動 {f_chg20:+.2f}%")
                if req.securities_lending_anomaly_exclude is True:
                    sl_anomaly = r.get("securities_lending_anomaly")
                    if sl_anomaly and sl_anomaly != "surge":
                        reasons.append("借券無異常暴增")

                # Quant Score
                q_score = r.get("quant_score")
                if req.quant_score_min is not None and q_score is not None:
                    reasons.append(f"Quant 評分 {q_score:.1f}")

                # Institutional
                f_net = r.get("foreign_net")
                if req.foreign_net_min is not None and f_net is not None:
                    reasons.append(f"外資買超 {int(f_net):,}股")
                it_net = r.get("investment_trust_net")
                if req.investment_trust_net_min is not None and it_net is not None:
                    reasons.append(f"投信買超 {int(it_net):,}股")

                # Technical & Price Limits
                if req.above_ma5 is True:
                    reasons.append("突破5日線")
                if req.above_ma20 is True:
                    reasons.append("站上月線 (MA20)")
                if req.near_upper_limit is True:
                    dist = r.get("distance_to_upper_limit")
                    if dist is not None:
                        reasons.append(f"接近漲停 (距 {dist * 100:.1f}%)")
                if req.rsi_14_min is not None and r.get("rsi_14") is not None:
                    reasons.append(f"RSI(14) {r['rsi_14']:.1f}")
                if req.momentum_5d_min is not None and r.get("momentum_5d") is not None:
                    reasons.append(f"5日動能 {r['momentum_5d'] * 100:+.1f}%")

            items.append(ScreenerResultItem(
                symbol=r["symbol"],
                name=r.get("name") or r["symbol"],
                exchange=r.get("exchange") or "TWSE",
                instrument_type=r.get("instrument_type") or "stock",
                industry=r.get("industry"),
                close=r.get("close"),
                trend_adjusted_close=r.get("trend_adjusted_close"),
                change_pct=r.get("change_pct"),
                volume=r.get("volume"),
                amount=r.get("amount"),
                quote_date=str(r["date"]) if r.get("date") else None,
                price_limit_pct=r.get("price_limit_pct"),
                is_no_limit=r.get("is_no_limit", False),
                limit_up=r.get("limit_up"),
                limit_down=r.get("limit_down"),
                distance_to_upper_limit=r.get("distance_to_upper_limit"),
                distance_to_lower_limit=r.get("distance_to_lower_limit"),
                ma5=r.get("ma5"),
                ma10=r.get("ma10"),
                ma20=r.get("ma20"),
                rsi_14=r.get("rsi_14"),
                momentum_5d=r.get("momentum_5d"),
                vol_ratio_5d=r.get("vol_ratio_5d"),
                ma60=r.get("ma60") if strategy_id else None,
                momentum_20d=r.get("momentum_20d") if strategy_id else None,
                vol_ratio_20d=r.get("vol_ratio_20d") if strategy_id else None,
                momentum_acceleration=r.get("momentum_acceleration") if strategy_id else None,
                breakout_20d_strength=r.get("breakout_20d_strength") if strategy_id else None,
                breakout_60d_strength=r.get("breakout_60d_strength") if strategy_id else None,
                foreign_net=r.get("foreign_net"),
                foreign_net_5d=r.get("foreign_net_5d"),
                investment_trust_net=r.get("investment_trust_net"),
                investment_trust_net_5d=r.get("investment_trust_net_5d"),
                dealer_net=r.get("dealer_net"),
                dealer_net_5d=r.get("dealer_net_5d"),
                institutional_flow_ratio_5d=r.get("institutional_flow_ratio_5d") if extended else None,
                institutional_date=r.get("institutional_date"),
                institutional_status=r.get("institutional_status") or "unavailable",
                institutional_streak=json.loads(r["_institutional_streak_meta"]) if r.get("_institutional_streak_meta") else None,
                margin_balance=r.get("margin_balance"),
                margin_balance_change=r.get("margin_balance_change"),
                short_balance=r.get("short_balance"),
                short_balance_change=r.get("short_balance_change"),
                short_margin_ratio=r.get("short_margin_ratio"),
                margin_date=r.get("margin_date"),
                margin_status=r.get("margin_status") or "unavailable",
                pe=r.get("pe"),
                pb=r.get("pb"),
                dividend_yield=r.get("dividend_yield"),
                revenue_yoy=r.get("revenue_yoy"),
                revenue_mom=r.get("revenue_mom"),
                revenue_yoy_improving=r.get("revenue_yoy_improving") if strategy_id else None,
                revenue_status=r.get("revenue_status") if extended else "unavailable",
                revenue_latest_period=r.get("revenue_latest_period"),
                latest_eps=r.get("latest_eps"),
                financials_as_of=r.get("financials_as_of"),
                valuation_as_of=r.get("valuation_as_of"),
                foreign_shareholding_ratio=r.get("foreign_shareholding_ratio"),
                foreign_shareholding_change_20d=r.get("foreign_shareholding_change_20d"),
                securities_lending_anomaly=r.get("securities_lending_anomaly"),
                quant_score=r.get("quant_score"),
                match_reasons=reasons,
                strategy_id=strategy_id,
                strategy_version=(strategy_metadata(strategy_id)["version"] if strategy_id else None),
                strategy_signals=strategy_signals,
                consensus_hit_count=r.get("consensus_hit_count") if strategy_id else None,
                consensus_strategy_names=(
                    [name for name in (r.get("consensus_strategy_names") or "").split("、") if name]
                    if strategy_id == "multi_factor_consensus_v1" else []
                ),
            ))
        return items


    def _add_null_cols(self, df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
        for c in cols:
            if c not in df.columns:
                df = df.with_columns(pl.lit(None).alias(c))
        return df

    @staticmethod
    def _normalize_daily_frame(df: pl.DataFrame) -> pl.DataFrame:
        """Normalize cache/provider values before arithmetic and joins.

        Historical partitions can contain a numeric value, a numeric string,
        or null for the same field. `strict=False` turns malformed values
        into null, so a bad row becomes unavailable data instead of a request
        time Polars schema/type error or a fabricated zero.
        """
        if df.is_empty():
            return df
        expressions = [pl.col("symbol").cast(pl.String, strict=False).alias("symbol")]
        if "date" in df.columns:
            expressions.append(pl.col("date").cast(pl.Date, strict=False).alias("date"))
        expressions.extend(
            pl.col(column).cast(pl.Float64, strict=False).alias(column)
            for column in _DAILY_NUMERIC_COLUMNS
            if column in df.columns
        )
        return df.with_columns(expressions)
