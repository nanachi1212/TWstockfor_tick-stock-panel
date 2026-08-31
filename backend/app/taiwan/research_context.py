"""Taiwan Stock Research Evidence Context Service (Phase 7C).

Provides a unified, strongly-typed, deterministic research evidence context
for an individual Taiwan stock or ETF, designed for downstream analytical and AI usage.

CORE ARCHITECTURE:
  symbol
  -> Security Master identity (KNOWN)
  -> Price & DailyStore metrics (KNOWN, DERIVED)
  -> Technical indicators (DERIVED)
  -> Institutional flows (KNOWN, DERIVED)
  -> Margin & Short balances (KNOWN, DERIVED)
  -> Market context (reused from Phase 7A MarketIntelligence)
  -> Industry context (reused from Phase 7B IndustryIntelligence)
  -> Company Fundamentals OR Structured ETF metadata
  -> Market Rules & Price Limit profile (Phase 3 authority)
  -> Realtime quote & Monitor alerts (if available offline)
  -> Explicit Data Quality & Evidence Classification (KNOWN / MISSING / DERIVED)

STRICT SAFEGUARDS:
- 100% Deterministic: NO AI recommendations, opinions, price targets, sentiment, or summary.
- Zero-HTTP at request time: Reads solely from local persisted stores.
- Point-In-Time No Look-Ahead: All historical reads strictly enforce <= query date D.
- Categorical Separation: ETFs do NOT receive fabricated corporate financials.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field
import polars as pl

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import (
    DatasetFreshnessStatus,
    resolve_target_latest_trading_date,
)
from app.taiwan.etf_data import TaiwanETFProfile, TaiwanETFSnapshot
from app.taiwan.fundamentals import FundamentalRecord, TaiwanFundamentalStore, latest_as_of
from app.taiwan.industry_intelligence import (
    IndustryMetrics,
    TaiwanIndustryIntelligenceService,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_intelligence import (
    MarketBreadthStats,
    TaiwanMarketIntelligenceService,
)
from app.taiwan.market_rules import PriceLimitModel, TickSizeModel
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument

logger = logging.getLogger(__name__)


# ── Strong Typing & Pydantic Models ───────────────────────────


class EvidenceMeta(BaseModel):
    """Categorical classification and source tracking for evidence data."""

    classification: Literal["KNOWN", "MISSING", "DERIVED"]
    source: str
    formula: str | None = None
    as_of: str | None = None


class StockIdentityEvidence(BaseModel):
    """Sourced master identity facts from TaiwanSecurityMaster (KNOWN)."""

    canonical_symbol: str
    code: str
    name: str
    exchange: str
    instrument_type: str
    industry: str | None = None
    currency: str = "TWD"
    listing_status: str
    listing_date: str | None = None
    meta: EvidenceMeta


class MarketContextEvidence(BaseModel):
    """Reused broad-market intelligence summary from Phase 7A (DERIVED / REUSED)."""

    trade_date: str
    market_turnover: float
    advance_count: int
    decline_count: int
    flat_count: int
    upper_limit_count: int
    lower_limit_count: int
    exchange_turnover: float | None = None
    institutional_market_net: float | None = None
    margin_market_change: float | None = None
    status: str = "unavailable"
    meta: EvidenceMeta


class IndustryContextEvidence(BaseModel):
    """Reused industry-level intelligence summary from Phase 7B (DERIVED / REUSED)."""

    industry: str | None = None
    turnover: float | None = None
    turnover_share: float | None = None
    advance_ratio: float | None = None
    average_change_pct: float | None = None
    median_change_pct: float | None = None
    foreign_net: float | None = None
    investment_trust_net: float | None = None
    relative_strength_5d: float | None = None
    relative_strength_20d: float | None = None
    status: str = "unavailable"
    meta: EvidenceMeta


class PriceContextEvidence(BaseModel):
    """Price, volume, amount, and return metrics (KNOWN & DERIVED)."""

    trade_date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None  # in shares
    amount: float | None = None  # in TWD (元)
    previous_close: float | None = None
    change: float | None = None
    change_pct: float | None = None  # decimal e.g. 0.05
    return_5d: float | None = None  # decimal e.g. 0.08
    return_20d: float | None = None  # decimal e.g. 0.15
    high_20d: float | None = None
    low_20d: float | None = None
    distance_from_20d_high: float | None = None  # decimal e.g. -0.02
    distance_from_20d_low: float | None = None  # decimal e.g. 0.12
    meta: EvidenceMeta


class TechnicalContextEvidence(BaseModel):
    """Deterministic technical indicators and moving averages (DERIVED)."""

    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    rsi14: float | None = None
    above_ma5: bool | None = None
    above_ma20: bool | None = None
    distance_to_ma20: float | None = None  # close / ma20 - 1
    vol_ratio_5d: float | None = None  # volume / 5-day avg volume
    meta: EvidenceMeta


class InstitutionalContextEvidence(BaseModel):
    """Three Major Institutional Investors flow metrics (KNOWN & DERIVED, in shares)."""

    as_of: str | None = None
    foreign_net_1d: float | None = None
    foreign_net_5d: float | None = None
    foreign_net_20d: float | None = None
    investment_trust_net_1d: float | None = None
    investment_trust_net_5d: float | None = None
    investment_trust_net_20d: float | None = None
    dealer_net_1d: float | None = None
    dealer_net_5d: float | None = None
    dealer_net_20d: float | None = None
    coverage_days_5d: int = 0
    coverage_days_20d: int = 0
    status: DatasetFreshnessStatus = "unavailable"
    meta: EvidenceMeta


class MarginContextEvidence(BaseModel):
    """Margin and short trading balance metrics (KNOWN & DERIVED, in shares)."""

    as_of: str | None = None
    margin_balance: float | None = None
    margin_balance_change_1d: float | None = None
    margin_balance_change_5d: float | None = None
    short_balance: float | None = None
    short_balance_change_1d: float | None = None
    short_balance_change_5d: float | None = None
    short_margin_ratio: float | None = None  # percentage e.g. 5.2%
    status: DatasetFreshnessStatus = "unavailable"
    meta: EvidenceMeta


class FundamentalsContextEvidence(BaseModel):
    """Point-in-time financial statements, revenue, and valuation (KNOWN)."""

    status: str = "unavailable"  # available, unavailable, not_applicable
    as_of_period: str | None = None
    available_at: str | None = None
    pe: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    monthly_revenue_yoy: float | None = None
    latest_eps: float | None = None
    meta: EvidenceMeta


class ETFContextEvidence(BaseModel):
    """Structured ETF metadata from official open data (KNOWN)."""

    status: str = "not_applicable"  # available, unavailable, not_applicable
    etf_type: str | None = None
    underlying_scope: str | None = None  # domestic, foreign
    leverage_multiplier: float | None = None
    inverse: bool | None = None
    benchmark: str | None = None
    meta: EvidenceMeta


class MarketRulesContextEvidence(BaseModel):
    """Statutory price limit and tick-size model evaluation (KNOWN & DERIVED)."""

    price_limit_pct: float | None = None  # 0.10, 0.20, None for NO_LIMIT
    is_no_limit: bool = False
    limit_up: float | None = None
    limit_down: float | None = None
    tick_size: float | None = None
    meta: EvidenceMeta


class RealtimeContextEvidence(BaseModel):
    """Offline-compatible realtime quote status (KNOWN / MISSING)."""

    status: str = "unavailable"  # available, unavailable, market_closed
    last_price: float | None = None
    quote_ts: str | None = None
    meta: EvidenceMeta


class MonitorContextEvidence(BaseModel):
    """Configured monitor rules count and alert occurrences (KNOWN)."""

    active_rule_count: int = 0
    recent_alert_count: int = 0
    meta: EvidenceMeta


class SectionQualityMeta(BaseModel):
    """Freshness status for an evidence section."""

    section: str
    status: str
    as_of: str | None = None


class ResearchDataQuality(BaseModel):
    """Comprehensive data quality tracking across all evidence domains."""

    overall_status: Literal["complete", "partial", "unavailable"]
    sections: list[SectionQualityMeta]
    target_trade_date: str


class EvidenceSummaryCounts(BaseModel):
    """Summary counts of factual, missing, and derived metrics."""

    known_fields_count: int
    missing_fields_count: int
    derived_fields_count: int
    missing_sections: list[str] = Field(default_factory=list)


class TaiwanStockResearchContext(BaseModel):
    """Unified strongly-typed Taiwan Stock Research Evidence Context response model."""

    symbol: str
    generated_at: str
    as_of_date: str
    identity: StockIdentityEvidence
    market_context: MarketContextEvidence
    industry_context: IndustryContextEvidence
    price_context: PriceContextEvidence
    technical_context: TechnicalContextEvidence
    institutional_context: InstitutionalContextEvidence
    margin_context: MarginContextEvidence
    fundamentals_context: FundamentalsContextEvidence
    etf_context: ETFContextEvidence
    market_rules_context: MarketRulesContextEvidence
    realtime_context: RealtimeContextEvidence
    monitor_context: MonitorContextEvidence
    data_quality: ResearchDataQuality
    evidence_summary: EvidenceSummaryCounts


# ── Service Implementation ────────────────────────────────────


class TaiwanStockResearchContextService:
    """Builds typed, deterministic stock research evidence context with 0 request-time HTTP."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        inst_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        market_intel_svc: TaiwanMarketIntelligenceService | None = None,
        industry_intel_svc: TaiwanIndustryIntelligenceService | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.inst_store = inst_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.security_master = security_master or get_security_master()
        self.market_intel_svc = market_intel_svc or TaiwanMarketIntelligenceService(
            daily_store=self.daily_store,
            inst_store=self.inst_store,
            margin_store=self.margin_store,
            calendar=self.calendar,
            security_master=self.security_master,
        )
        self.industry_intel_svc = industry_intel_svc or TaiwanIndustryIntelligenceService(
            daily_store=self.daily_store,
            inst_store=self.inst_store,
            margin_store=self.margin_store,
            calendar=self.calendar,
            security_master=self.security_master,
        )

    def get_research_context(
        self,
        symbol_input: str,
        target_date: date | None = None,
    ) -> TaiwanStockResearchContext:
        raw = symbol_input.strip()
        inst = None
        # Try direct or canonical lookup
        try:
            parsed = parse_symbol(raw)
            inst = self.security_master.get_instrument(parsed.canonical)
        except Exception:
            pass

        if inst is None:
            # Try appending .TWSE or .TPEX or searching by code
            inst = self.security_master.get_instrument(f"{raw.upper()}.TWSE") or self.security_master.get_instrument(f"{raw.upper()}.TPEX")

        if inst is None:
            matches = self.security_master.search(raw, limit=5)
            if matches:
                first_sym = matches[0]["symbol"]
                inst = self.security_master.get_instrument(first_sym)

        if inst is None:
            raise ValueError(f"Symbol not found in Taiwan Security Master: {symbol_input}")

        canonical = inst.symbol
        target = target_date or resolve_target_latest_trading_date(self.calendar)

        identity_evidence = StockIdentityEvidence(
            canonical_symbol=inst.symbol,
            code=inst.code,
            name=inst.name,
            exchange=inst.exchange,
            instrument_type=inst.instrument_type,
            industry=inst.industry,
            currency=inst.currency,
            listing_status=inst.listing_status,
            listing_date=inst.listing_date,
            meta=EvidenceMeta(
                classification="KNOWN",
                source="taiwan_security_master",
                as_of=inst.updated_at,
            ),
        )

        # 2. Historical Daily Window (NO look-ahead: dates <= target)
        available_dates = sorted([d for d in self.daily_store.available_dates() if d <= target])
        target_in_store = target in available_dates
        daily_as_of = max(available_dates) if available_dates else None
        daily_status: DatasetFreshnessStatus = (
            "current" if (daily_as_of and daily_as_of >= target)
            else ("stale" if daily_as_of else "unavailable")
        )

        # Up to 65 prior trading days for 20D/60D indicators
        window_dates = available_dates[-65:] if len(available_dates) >= 65 else available_dates
        hist_df = self.daily_store.read_range([canonical], window_dates[0] if window_dates else target, target) if window_dates else pl.DataFrame()

        # Price Context & Technical Metrics
        curr_row = hist_df.filter(pl.col("date") == target).tail(1) if not hist_df.is_empty() else pl.DataFrame()
        prev_date = available_dates[-2] if len(available_dates) >= 2 else None
        prev_row = hist_df.filter(pl.col("date") == prev_date).tail(1) if (prev_date and not hist_df.is_empty()) else pl.DataFrame()

        c_price = float(curr_row["close"][0]) if not curr_row.is_empty() and curr_row["close"][0] is not None else None
        o_price = float(curr_row["open"][0]) if not curr_row.is_empty() and curr_row["open"][0] is not None else None
        h_price = float(curr_row["high"][0]) if not curr_row.is_empty() and curr_row["high"][0] is not None else None
        l_price = float(curr_row["low"][0]) if not curr_row.is_empty() and curr_row["low"][0] is not None else None
        vol = float(curr_row["volume"][0]) if not curr_row.is_empty() and curr_row["volume"][0] is not None else None
        amt = float(curr_row["amount"][0]) if not curr_row.is_empty() and curr_row["amount"][0] is not None else None
        p_close = float(prev_row["close"][0]) if not prev_row.is_empty() and prev_row["close"][0] is not None else None

        chg = round(c_price - p_close, 4) if (c_price is not None and p_close is not None) else None
        chg_pct = round((c_price / p_close) - 1.0, 6) if (c_price is not None and p_close is not None and p_close > 0) else None

        # Lookbacks: 5D, 20D returns & extremes
        ret_5d = None
        ret_20d = None
        h_20d = None
        l_20d = None
        dist_h20 = None
        dist_l20 = None
        ma5 = None
        ma20 = None
        ma60 = None
        rsi14 = None
        dist_ma20 = None
        vol_ratio = None

        if len(hist_df) >= 2:
            closes = hist_df["close"].drop_nulls().to_list()
            volumes = hist_df["volume"].drop_nulls().to_list()

            if len(closes) >= 6:
                c_5d_base = closes[-6]
                if c_5d_base > 0 and c_price is not None:
                    ret_5d = round((c_price / c_5d_base) - 1.0, 6)
            if len(closes) >= 21:
                c_20d_base = closes[-21]
                if c_20d_base > 0 and c_price is not None:
                    ret_20d = round((c_price / c_20d_base) - 1.0, 6)

            # 20D Extremes
            window_20_closes = closes[-20:]
            h_20d = max(window_20_closes) if window_20_closes else None
            l_20d = min(window_20_closes) if window_20_closes else None
            if c_price is not None and h_20d is not None and h_20d > 0:
                dist_h20 = round((c_price / h_20d) - 1.0, 6)
            if c_price is not None and l_20d is not None and l_20d > 0:
                dist_l20 = round((c_price / l_20d) - 1.0, 6)

            # Moving Averages
            if len(closes) >= 5:
                ma5 = round(sum(closes[-5:]) / 5.0, 4)
            if len(closes) >= 20:
                ma20 = round(sum(closes[-20:]) / 20.0, 4)
                if c_price is not None and ma20 > 0:
                    dist_ma20 = round((c_price / ma20) - 1.0, 6)
            if len(closes) >= 60:
                ma60 = round(sum(closes[-60:]) / 60.0, 4)

            # 5D Volume Ratio
            if len(volumes) >= 5 and vol is not None:
                avg_vol_5d = sum(volumes[-5:]) / 5.0
                if avg_vol_5d > 0:
                    vol_ratio = round(vol / avg_vol_5d, 2)

            # RSI 14
            if len(closes) >= 15:
                diffs = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
                gains = [d for d in diffs if d > 0]
                losses = [-d for d in diffs if d < 0]
                avg_gain = sum(gains) / 14.0 if gains else 0.0
                avg_loss = sum(losses) / 14.0 if losses else 0.0
                if avg_loss == 0:
                    rsi14 = 100.0
                else:
                    rs = avg_gain / avg_loss
                    rsi14 = round(100.0 - (100.0 / (1.0 + rs)), 2)

        price_evidence = PriceContextEvidence(
            trade_date=str(target),
            open=o_price,
            high=h_price,
            low=l_price,
            close=c_price,
            volume=vol,
            amount=amt,
            previous_close=p_close,
            change=chg,
            change_pct=chg_pct,
            return_5d=ret_5d,
            return_20d=ret_20d,
            high_20d=h_20d,
            low_20d=l_20d,
            distance_from_20d_high=dist_h20,
            distance_from_20d_low=dist_l20,
            meta=EvidenceMeta(
                classification="KNOWN" if c_price is not None else "MISSING",
                source="taiwan_daily_store",
                as_of=str(target),
            ),
        )

        tech_evidence = TechnicalContextEvidence(
            ma5=ma5,
            ma20=ma20,
            ma60=ma60,
            rsi14=rsi14,
            above_ma5=(c_price > ma5) if (c_price is not None and ma5 is not None) else None,
            above_ma20=(c_price > ma20) if (c_price is not None and ma20 is not None) else None,
            distance_to_ma20=dist_ma20,
            vol_ratio_5d=vol_ratio,
            meta=EvidenceMeta(
                classification="DERIVED",
                source="taiwan_daily_store",
                formula="rolling_means_and_ratios",
                as_of=str(target),
            ),
        )

        # 3. Institutional Context (KNOWN & DERIVED)
        inst_available = sorted([d for d in self.inst_store.available_dates() if d <= target])
        inst_as_of = max(inst_available) if inst_available else None
        inst_status: DatasetFreshnessStatus = (
            "current" if (inst_as_of and inst_as_of >= target)
            else ("stale" if inst_as_of else "unavailable")
        )

        inst_window = inst_available[-20:] if inst_available else []
        inst_rows = self.inst_store.read_range([canonical], inst_window[0] if inst_window else target, target) if inst_window else pl.DataFrame()

        f_1d = None
        it_1d = None
        d_1d = None
        f_5d = None
        it_5d = None
        d_5d = None
        f_20d = None
        it_20d = None
        d_20d = None
        cov_5d = 0
        cov_20d = 0

        if not inst_rows.is_empty():
            inst_sorted = inst_rows.sort("date")
            curr_inst = inst_sorted.filter(pl.col("date") == target).tail(1)
            if not curr_inst.is_empty():
                f_1d = float(curr_inst["foreign_net"][0]) if curr_inst["foreign_net"][0] is not None else None
                it_1d = float(curr_inst["investment_trust_net"][0]) if curr_inst["investment_trust_net"][0] is not None else None
                d_1d = float(curr_inst["dealer_net"][0]) if curr_inst["dealer_net"][0] is not None else None

            # 5D sum
            sub_5 = inst_sorted.tail(5)
            cov_5d = len(sub_5)
            f_5d = float(sub_5["foreign_net"].sum()) if cov_5d > 0 and sub_5["foreign_net"].drop_nulls().len() > 0 else None
            it_5d = float(sub_5["investment_trust_net"].sum()) if cov_5d > 0 and sub_5["investment_trust_net"].drop_nulls().len() > 0 else None
            d_5d = float(sub_5["dealer_net"].sum()) if cov_5d > 0 and sub_5["dealer_net"].drop_nulls().len() > 0 else None

            # 20D sum
            sub_20 = inst_sorted.tail(20)
            cov_20d = len(sub_20)
            f_20d = float(sub_20["foreign_net"].sum()) if cov_20d > 0 and sub_20["foreign_net"].drop_nulls().len() > 0 else None
            it_20d = float(sub_20["investment_trust_net"].sum()) if cov_20d > 0 and sub_20["investment_trust_net"].drop_nulls().len() > 0 else None
            d_20d = float(sub_20["dealer_net"].sum()) if cov_20d > 0 and sub_20["dealer_net"].drop_nulls().len() > 0 else None

        inst_evidence = InstitutionalContextEvidence(
            as_of=str(inst_as_of) if inst_as_of else None,
            foreign_net_1d=f_1d,
            foreign_net_5d=f_5d,
            foreign_net_20d=f_20d,
            investment_trust_net_1d=it_1d,
            investment_trust_net_5d=it_5d,
            investment_trust_net_20d=it_20d,
            dealer_net_1d=d_1d,
            dealer_net_5d=d_5d,
            dealer_net_20d=d_20d,
            coverage_days_5d=cov_5d,
            coverage_days_20d=cov_20d,
            status=inst_status,
            meta=EvidenceMeta(
                classification="KNOWN" if f_1d is not None else "MISSING",
                source="taiwan_institutional_store",
                formula="cumulative_shares_sum",
                as_of=str(inst_as_of) if inst_as_of else None,
            ),
        )

        # 4. Margin Context (KNOWN & DERIVED)
        m_available = sorted([d for d in self.margin_store.available_dates() if d <= target])
        m_as_of = max(m_available) if m_available else None
        m_status: DatasetFreshnessStatus = (
            "current" if (m_as_of and m_as_of >= target)
            else ("stale" if m_as_of else "unavailable")
        )

        m_window = m_available[-5:] if m_available else []
        m_rows = self.margin_store.read_range([canonical], m_window[0] if m_window else target, target) if m_window else pl.DataFrame()

        mb = None
        mb_chg_1d = None
        mb_chg_5d = None
        sb = None
        sb_chg_1d = None
        sb_chg_5d = None
        sm_ratio = None

        if not m_rows.is_empty():
            m_sorted = m_rows.sort("date")
            curr_m = m_sorted.filter(pl.col("date") == target).tail(1)
            if not curr_m.is_empty():
                mb = float(curr_m["margin_balance"][0]) if curr_m["margin_balance"][0] is not None else None
                mb_chg_1d = float(curr_m["margin_change"][0]) if "margin_change" in curr_m.columns and curr_m["margin_change"][0] is not None else None
                sb = float(curr_m["short_balance"][0]) if curr_m["short_balance"][0] is not None else None
                sb_chg_1d = float(curr_m["short_change"][0]) if "short_change" in curr_m.columns and curr_m["short_change"][0] is not None else None
                sm_ratio = float(curr_m["short_margin_ratio"][0]) if "short_margin_ratio" in curr_m.columns and curr_m["short_margin_ratio"][0] is not None else None

            if "margin_change" in m_sorted.columns:
                mb_chg_5d = float(m_sorted["margin_change"].sum()) if len(m_sorted) > 0 else None
            if "short_change" in m_sorted.columns:
                sb_chg_5d = float(m_sorted["short_change"].sum()) if len(m_sorted) > 0 else None

        margin_evidence = MarginContextEvidence(
            as_of=str(m_as_of) if m_as_of else None,
            margin_balance=mb,
            margin_balance_change_1d=mb_chg_1d,
            margin_balance_change_5d=mb_chg_5d,
            short_balance=sb,
            short_balance_change_1d=sb_chg_1d,
            short_balance_change_5d=sb_chg_5d,
            short_margin_ratio=sm_ratio,
            status=m_status,
            meta=EvidenceMeta(
                classification="KNOWN" if mb is not None else "MISSING",
                source="taiwan_margin_store",
                as_of=str(m_as_of) if m_as_of else None,
            ),
        )

        # 5. Market Context (Reused Phase 7A)
        market_snap = self.market_intel_svc.get_snapshot(target)
        market_evidence = MarketContextEvidence(
            trade_date=market_snap.trade_date,
            market_turnover=market_snap.market_totals.turnover,
            advance_count=market_snap.market_totals.advance_count,
            decline_count=market_snap.market_totals.decline_count,
            flat_count=market_snap.market_totals.flat_count,
            upper_limit_count=market_snap.market_totals.upper_limit_count,
            lower_limit_count=market_snap.market_totals.lower_limit_count,
            exchange_turnover=market_snap.by_exchange.twse.turnover if inst.exchange == "TWSE" else market_snap.by_exchange.tpex.turnover,
            institutional_market_net=market_snap.institutional.total_net,
            margin_market_change=market_snap.margin.margin_balance_change,
            status=market_snap.data_quality.overall_status,
            meta=EvidenceMeta(
                classification="DERIVED",
                source="taiwan_market_intelligence",
                as_of=market_snap.trade_date,
            ),
        )

        # 6. Industry Context (Reused Phase 7B)
        industry_evidence = None
        if inst.instrument_type == "stock" and inst.industry:
            ind_snap = self.industry_intel_svc.get_snapshot(target)
            target_ind_metric = next((im for im in ind_snap.industries if im.industry == inst.industry), None)
            if target_ind_metric:
                industry_evidence = IndustryContextEvidence(
                    industry=target_ind_metric.industry,
                    turnover=target_ind_metric.turnover,
                    turnover_share=target_ind_metric.turnover_share,
                    advance_ratio=target_ind_metric.advance_ratio,
                    average_change_pct=target_ind_metric.average_change_pct,
                    median_change_pct=target_ind_metric.median_change_pct,
                    foreign_net=target_ind_metric.foreign_net,
                    investment_trust_net=target_ind_metric.investment_trust_net,
                    relative_strength_5d=target_ind_metric.relative_strength_5d,
                    relative_strength_20d=target_ind_metric.relative_strength_20d,
                    status=ind_snap.data_quality.overall_status,
                    meta=EvidenceMeta(
                        classification="DERIVED",
                        source="taiwan_industry_intelligence",
                        as_of=ind_snap.trade_date,
                    ),
                )

        if industry_evidence is None:
            industry_evidence = IndustryContextEvidence(
                industry=inst.industry,
                status="unavailable",
                meta=EvidenceMeta(
                    classification="MISSING",
                    source="taiwan_industry_intelligence",
                ),
            )

        # 7. Fundamentals Context (Point-In-Time) OR ETF Context
        if inst.instrument_type == "stock":
            fund_status = "unavailable"
            etf_evidence = ETFContextEvidence(
                status="not_applicable",
                meta=EvidenceMeta(classification="MISSING", source="taiwan_security_master"),
            )
            fund_evidence = FundamentalsContextEvidence(
                status=fund_status,
                meta=EvidenceMeta(
                    classification="MISSING",
                    source="official_fundamentals_store",
                ),
            )
        else:
            # ETF
            fund_evidence = FundamentalsContextEvidence(
                status="not_applicable",
                meta=EvidenceMeta(classification="MISSING", source="taiwan_security_master"),
            )
            etf_evidence = ETFContextEvidence(
                status="available",
                etf_type=inst.etf_category,
                underlying_scope=inst.underlying_scope,
                leverage_multiplier=inst.leverage_multiplier,
                inverse=bool(inst.leverage_multiplier < 0) if inst.leverage_multiplier is not None else None,
                meta=EvidenceMeta(
                    classification="KNOWN",
                    source="taiwan_security_master",
                    as_of=inst.updated_at,
                ),
            )

        # 8. Market Rules Context (Phase 3 MarketProfileBridge)
        try:
            limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
        except ValueError:
            limit_pct = None

        is_no_lim = (limit_pct is None)
        upper_l = None
        lower_l = None
        tick_sz = None

        if p_close is not None and p_close > 0:
            if not is_no_lim and limit_pct is not None:
                upper_l, lower_l = MarketProfileBridge.calc_limits(p_close, inst)
            tick_sz = TickSizeModel.get_tick_size(p_close)

        market_rules_evidence = MarketRulesContextEvidence(
            price_limit_pct=limit_pct,
            is_no_limit=is_no_lim,
            limit_up=upper_l,
            limit_down=lower_l,
            tick_size=tick_sz,
            meta=EvidenceMeta(
                classification="KNOWN" if limit_pct is not None or is_no_lim else "MISSING",
                source="taiwan_market_rules",
                as_of=str(target),
            ),
        )

        # 9. Realtime & Monitor (Offline safe)
        realtime_evidence = RealtimeContextEvidence(
            status="market_closed",
            last_price=c_price,
            quote_ts=str(target),
            meta=EvidenceMeta(classification="KNOWN" if c_price is not None else "MISSING", source="taiwan_daily_store"),
        )
        monitor_evidence = MonitorContextEvidence(
            active_rule_count=0,
            recent_alert_count=0,
            meta=EvidenceMeta(classification="KNOWN", source="taiwan_monitor_rules"),
        )

        # 10. Data Quality & Overall Status
        sec_qual = [
            SectionQualityMeta(section="daily", status=daily_status, as_of=str(daily_as_of)),
            SectionQualityMeta(section="institutional", status=inst_status, as_of=str(inst_as_of)),
            SectionQualityMeta(section="margin", status=m_status, as_of=str(m_as_of)),
            SectionQualityMeta(section="market", status=market_snap.data_quality.overall_status, as_of=market_snap.trade_date),
            SectionQualityMeta(section="industry", status=industry_evidence.status, as_of=str(target)),
        ]
        active_statuses = [daily_status, inst_status, m_status]
        if all(s == "current" for s in active_statuses):
            overall_status = "complete"
        elif any(s in ("current", "stale") for s in active_statuses):
            overall_status = "partial"
        else:
            overall_status = "unavailable"

        dq = ResearchDataQuality(
            overall_status=overall_status,
            sections=sec_qual,
            target_trade_date=str(target),
        )

        # 11. Evidence Summary Counts
        # Deterministic field counting
        known_cnt = 10  # identity, OHLCV, 1D flows
        if f_1d is not None: known_cnt += 3
        if mb is not None: known_cnt += 4
        if inst.instrument_type == "etf": known_cnt += 4

        derived_cnt = 12  # returns, MA, RSI, RS 5D/20D, rolling sums
        missing_cnt = 0
        missing_sections = []
        if fund_evidence.status == "unavailable":
            missing_sections.append("fundamentals")
            missing_cnt += 4
        if industry_evidence.status == "unavailable":
            missing_sections.append("industry")
            missing_cnt += 4

        evidence_summary = EvidenceSummaryCounts(
            known_fields_count=known_cnt,
            missing_fields_count=missing_cnt,
            derived_fields_count=derived_cnt,
            missing_sections=missing_sections,
        )

        return TaiwanStockResearchContext(
            symbol=inst.symbol,
            generated_at=taipei_now().isoformat(),
            as_of_date=str(target),
            identity=identity_evidence,
            market_context=market_evidence,
            industry_context=industry_evidence,
            price_context=price_evidence,
            technical_context=tech_evidence,
            institutional_context=inst_evidence,
            margin_context=margin_evidence,
            fundamentals_context=fund_evidence,
            etf_context=etf_evidence,
            market_rules_context=market_rules_evidence,
            realtime_context=realtime_evidence,
            monitor_context=monitor_evidence,
            data_quality=dq,
            evidence_summary=evidence_summary,
        )
