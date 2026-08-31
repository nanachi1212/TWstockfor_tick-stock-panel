"""Deterministic Taiwan Abnormal Moves & Capital Flow Diagnostics Service (Phase 7D).

Answers:
  "Which Taiwan stocks are behaving abnormally today, and what objective evidence explains the abnormality?"
using only deterministic, traceable, locally persisted data.

CORE RULES:
- 100% Deterministic: NO AI recommendations, opinions, heat scores, or composite scores.
- Zero-HTTP at request time: Reads exclusively from local persisted Daily, Institutional, Margin stores.
- Point-In-Time No Look-Ahead: Target day is strictly excluded from historical baselines.
- Explicit Evidence & Formulas: Every triggered signal provides observed, baseline, ratio/delta, threshold, formula, and coverage.
- Purely Objective Classifications:
    * VOLUME_SPIKE: target_volume / mean(previous_5_valid_volumes) >= 2.0 (coverage >= 5)
    * TURNOVER_SPIKE: target_amount / mean(previous_5_valid_amounts) >= 2.0 (coverage >= 5)
    * PRICE_MOVE: abs(change_pct) >= 0.05 (>= 5% move)
    * FOREIGN_FLOW_SPIKE: abs(foreign_net) / mean(abs(previous_20_foreign_net)) >= 3.0 (with abs(net) >= 100,000 shares)
    * TRUST_FLOW_SPIKE: abs(trust_net) / mean(abs(previous_20_trust_net)) >= 3.0 (with abs(net) >= 50,000 shares)
    * DEALER_FLOW_SPIKE: abs(dealer_net) / mean(abs(previous_20_dealer_net)) >= 3.0 (with abs(net) >= 50,000 shares)
    * MARGIN_SURGE: abs(margin_change) >= 500,000 shares AND abs(margin_change) / median(abs(previous_20)) >= 3.0
    * SHORT_SURGE: abs(short_change) >= 100,000 shares AND abs(short_change) / median(abs(previous_20)) >= 3.0
    * SHORT_MARGIN_RATIO_SPIKE: (target_sm_ratio - median_previous_20_sm_ratio) >= 5.0 percentage points
    * PRICE_FLOW_DIVERGENCE: abs(change_pct) >= 0.03 AND strong opposite institutional flow
    * RELATIVE_STRENGTH_OUTLIER: abs(stock_5d_return - industry_5d_return) >= 0.10 (>= 10% excess return)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field
import polars as pl

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import DatasetFreshnessStatus, resolve_target_latest_trading_date
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.service import UniverseType

logger = logging.getLogger(__name__)


# ── Strong Typing & Pydantic Schemas ──────────────────────────


class DiagnosticSignalEvidence(BaseModel):
    """Evidence details supporting a triggered abnormal signal."""

    type: str = Field(..., description="訊號名稱: VOLUME_SPIKE, PRICE_MOVE, FOREIGN_FLOW_SPIKE 等")
    subtype: str | None = Field(None, description="子類型: 如 BUY / SELL 或 PRICE_UP_FOREIGN_SELL")
    severity: Literal["low", "moderate", "high", "extreme"] = Field("moderate", description="確定性嚴重度判定")
    observed: float = Field(..., description="當日觀測值")
    baseline: float | None = Field(None, description="基期比較值 (如 5D/20D 均值或中位數)")
    ratio: float | None = Field(None, description="觀測值相對於基期之倍數")
    delta: float | None = Field(None, description="差異差值 (如券資比變化點數)")
    threshold: float = Field(..., description="觸發門檻值")
    formula: str = Field(..., description="客觀透明計算公式")
    lookback_sessions: int = Field(..., description="基期交易日數")
    valid_sessions: int = Field(..., description="有效回溯日數")
    source: str = Field(..., description="來源持久化資料庫標識")
    status: str = Field("active", description="狀態: active 或 partial")


class CompactMarketContext(BaseModel):
    """Compact broad market breadth for context."""

    trade_date: str
    advance_ratio: float | None = None
    market_turnover: float | None = None
    overall_status: str = "unavailable"


class CompactIndustryContext(BaseModel):
    """Compact industry peer context."""

    industry: str | None = None
    turnover_share: float | None = None
    advance_ratio: float | None = None
    relative_strength_5d: float | None = None
    relative_strength_20d: float | None = None


class TaiwanAbnormalDiagnosticItem(BaseModel):
    """Deterministic diagnostic results and triggered signals for an individual stock."""

    symbol: str
    code: str
    name: str
    exchange: str
    industry: str | None = None
    close: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_pct: float | None = None  # decimal e.g. 0.05
    volume: float | None = None  # in shares
    amount: float | None = None  # in TWD (元)
    volume_ratio_5d: float | None = None
    amount_ratio_5d: float | None = None
    foreign_net: float | None = None  # in shares
    investment_trust_net: float | None = None  # in shares
    margin_balance_change: float | None = None  # in shares
    short_balance_change: float | None = None  # in shares
    short_margin_ratio: float | None = None  # percentage
    signal_count: int = Field(0, description="觸發之客觀異常訊號數量")
    signals: list[DiagnosticSignalEvidence] = Field(default_factory=list)
    market_context: CompactMarketContext
    industry_context: CompactIndustryContext


class DiagnosticsDataQuality(BaseModel):
    """Data quality and coverage metadata for abnormal diagnostics."""

    target_trade_date: str
    universe_supported_count: int
    evaluated_symbol_count: int
    diagnostic_symbol_count: int
    daily_status: DatasetFreshnessStatus
    institutional_status: DatasetFreshnessStatus
    margin_status: DatasetFreshnessStatus
    overall_status: Literal["complete", "partial", "unavailable"]


class TaiwanAbnormalDiagnosticsSnapshot(BaseModel):
    """Unified strongly-typed snapshot response for Taiwan abnormal moves diagnostics."""

    trade_date: str
    generated_at: str
    universe_count: int
    diagnostic_count: int
    items: list[TaiwanAbnormalDiagnosticItem]
    data_quality: DiagnosticsDataQuality
    provenance: list[str]


# ── Service Implementation ────────────────────────────────────


class TaiwanAbnormalDiagnosticsService:
    """Computes deterministic abnormal move & capital flow diagnostics via batch store processing."""

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

    def _resolve_prior_sessions(self, target_date: date) -> tuple[list[date], list[date]]:
        """Resolve prior 5 and prior 20 trading dates strictly < target_date (no look-ahead)."""
        available = sorted([d for d in self.daily_store.available_dates() if d < target_date])
        prior_5 = available[-5:] if len(available) >= 5 else available
        prior_20 = available[-20:] if len(available) >= 20 else available
        return prior_5, prior_20

    def get_diagnostics(
        self,
        target_date: date | None = None,
        include_all: bool = False,
        signal_filter: str | None = None,
        industry_filter: str | None = None,
        exchange_filter: str | None = None,
    ) -> TaiwanAbnormalDiagnosticsSnapshot:
        """Run batch diagnostics across Taiwan universe for target_date with zero request-time HTTP."""
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        self.security_master.ensure_loaded()

        # 1. Base Universe: Active supported stocks (ETFs excluded from corporate flow diagnostics)
        stock_symbols = self.security_master.get_universe(UniverseType.TAIWAN_STOCKS)
        universe_count = len(stock_symbols)
        if not stock_symbols:
            return self._empty_snapshot(target)

        # 2. Prior sessions resolution (strictly < target)
        prior_5_dates, prior_20_dates = self._resolve_prior_sessions(target)

        # 3. Batch Read: DailyStore
        df_target_daily = self.daily_store.read_range(stock_symbols, target, target)
        prev_date = prior_5_dates[-1] if prior_5_dates else None
        df_prev_daily = self.daily_store.read_range(stock_symbols, prev_date, prev_date) if prev_date else pl.DataFrame()

        # 5-session & 20-session history for baselines
        p5_start = prior_5_dates[0] if prior_5_dates else target
        p20_start = prior_20_dates[0] if prior_20_dates else target
        df_p5_daily = self.daily_store.read_range(stock_symbols, p5_start, prior_5_dates[-1]) if prior_5_dates else pl.DataFrame()
        df_p20_daily = self.daily_store.read_range(stock_symbols, p20_start, prior_20_dates[-1]) if prior_20_dates else pl.DataFrame()

        # 4. Batch Read: InstitutionalStore
        df_target_inst = self.inst_store.read_range(stock_symbols, target, target)
        inst_p20_dates = sorted([d for d in self.inst_store.available_dates() if d < target])[-20:]
        df_p20_inst = self.inst_store.read_range(stock_symbols, inst_p20_dates[0], inst_p20_dates[-1]) if inst_p20_dates else pl.DataFrame()

        # 5. Batch Read: MarginStore
        df_target_margin = self.margin_store.read_range(stock_symbols, target, target)
        margin_p20_dates = sorted([d for d in self.margin_store.available_dates() if d < target])[-20:]
        df_p20_margin = self.margin_store.read_range(stock_symbols, margin_p20_dates[0], margin_p20_dates[-1]) if margin_p20_dates else pl.DataFrame()

        # 6. Context Caches (Phase 7A & 7B)
        market_snap = self.market_intel_svc.get_snapshot(target)
        ind_snap = self.industry_intel_svc.get_snapshot(target)
        ind_metrics_map: dict[str, Any] = {im.industry: im for im in ind_snap.industries}

        tot_adv = market_snap.market_totals.advance_count
        tot_dec = market_snap.market_totals.decline_count
        tot_flt = market_snap.market_totals.flat_count
        tot_compared = tot_adv + tot_dec + tot_flt
        adv_ratio = round(tot_adv / tot_compared, 4) if tot_compared > 0 else None

        compact_market = CompactMarketContext(
            trade_date=market_snap.trade_date,
            advance_ratio=adv_ratio,
            market_turnover=market_snap.market_totals.turnover,
            overall_status=market_snap.data_quality.overall_status,
        )

        # 7. Aggregate Baseline Statistics with Polars
        p5_vol_stats = df_p5_daily.group_by("symbol").agg([
            pl.col("volume").mean().alias("mean_vol_5d"),
            pl.col("volume").count().alias("count_vol_5d"),
            pl.col("amount").mean().alias("mean_amt_5d"),
            pl.col("amount").count().alias("count_amt_5d"),
        ]) if not df_p5_daily.is_empty() else pl.DataFrame()

        p20_inst_stats = df_p20_inst.group_by("symbol").agg([
            pl.col("foreign_net").abs().mean().alias("mean_abs_foreign_20d"),
            pl.col("investment_trust_net").abs().mean().alias("mean_abs_trust_20d"),
            pl.col("dealer_net").abs().mean().alias("mean_abs_dealer_20d"),
            pl.col("foreign_net").count().alias("count_inst_20d"),
        ]) if not df_p20_inst.is_empty() else pl.DataFrame()

        p20_margin_stats = df_p20_margin.group_by("symbol").agg([
            pl.col("margin_change").abs().median().alias("median_abs_margin_change_20d"),
            pl.col("short_change").abs().median().alias("median_abs_short_change_20d"),
            pl.col("short_margin_ratio").median().alias("median_sm_ratio_20d"),
            pl.col("margin_change").count().alias("count_margin_20d"),
        ]) if not df_p20_margin.is_empty() else pl.DataFrame()

        # 5-day return baseline for stocks: close at target vs close at 5 sessions ago
        c5_base_map: dict[str, float] = {}
        if not df_p5_daily.is_empty():
            earliest_p5 = df_p5_daily.filter(pl.col("date") == prior_5_dates[0])
            for row in earliest_p5.iter_rows(named=True):
                if row["close"] is not None and row["close"] > 0:
                    c5_base_map[row["symbol"]] = float(row["close"])

        # Construct lookup maps for fast row generation
        target_daily_map = {r["symbol"]: r for r in df_target_daily.iter_rows(named=True)} if not df_target_daily.is_empty() else {}
        prev_daily_map = {r["symbol"]: r for r in df_prev_daily.iter_rows(named=True)} if not df_prev_daily.is_empty() else {}
        p5_vol_map = {r["symbol"]: r for r in p5_vol_stats.iter_rows(named=True)} if not p5_vol_stats.is_empty() else {}
        target_inst_map = {r["symbol"]: r for r in df_target_inst.iter_rows(named=True)} if not df_target_inst.is_empty() else {}
        p20_inst_map = {r["symbol"]: r for r in p20_inst_stats.iter_rows(named=True)} if not p20_inst_stats.is_empty() else {}
        target_margin_map = {r["symbol"]: r for r in df_target_margin.iter_rows(named=True)} if not df_target_margin.is_empty() else {}
        p20_margin_map = {r["symbol"]: r for r in p20_margin_stats.iter_rows(named=True)} if not p20_margin_stats.is_empty() else {}

        items: list[TaiwanAbnormalDiagnosticItem] = []

        # 8. Evaluate Diagnostic Signals Per Symbol
        for sym in stock_symbols:
            inst_master = self.security_master.get_instrument(sym)
            if inst_master is None:
                continue

            if exchange_filter and inst_master.exchange != exchange_filter:
                continue
            if industry_filter and inst_master.industry != industry_filter:
                continue

            t_daily = target_daily_map.get(sym)
            if not t_daily:
                continue  # untraded / missing in daily snapshot

            p_daily = prev_daily_map.get(sym)
            t_inst = target_inst_map.get(sym)
            t_margin = target_margin_map.get(sym)
            v_stat = p5_vol_map.get(sym)
            i_stat = p20_inst_map.get(sym)
            m_stat = p20_margin_map.get(sym)

            close_p = float(t_daily["close"]) if t_daily["close"] is not None else None
            prev_close_p = float(p_daily["close"]) if (p_daily and p_daily["close"] is not None) else None
            vol = float(t_daily["volume"]) if t_daily["volume"] is not None else None
            amt = float(t_daily["amount"]) if t_daily["amount"] is not None else None

            chg = round(close_p - prev_close_p, 4) if (close_p is not None and prev_close_p is not None) else None
            chg_pct = round((close_p / prev_close_p) - 1.0, 6) if (close_p is not None and prev_close_p is not None and prev_close_p > 0) else None

            vol_ratio = None
            amt_ratio = None
            f_net = float(t_inst["foreign_net"]) if (t_inst and t_inst["foreign_net"] is not None) else None
            it_net = float(t_inst["investment_trust_net"]) if (t_inst and t_inst["investment_trust_net"] is not None) else None
            m_chg = float(t_margin["margin_change"]) if (t_margin and "margin_change" in t_margin and t_margin["margin_change"] is not None) else None
            s_chg = float(t_margin["short_change"]) if (t_margin and "short_change" in t_margin and t_margin["short_change"] is not None) else None
            sm_ratio = float(t_margin["short_margin_ratio"]) if (t_margin and "short_margin_ratio" in t_margin and t_margin["short_margin_ratio"] is not None) else None

            signals: list[DiagnosticSignalEvidence] = []

            # ── Signal 1: VOLUME_SPIKE ──
            if v_stat and v_stat["count_vol_5d"] == 5 and v_stat["mean_vol_5d"] > 0 and vol is not None:
                mean_v5 = float(v_stat["mean_vol_5d"])
                vol_ratio = round(vol / mean_v5, 2)
                if vol_ratio >= 2.0:
                    sev: Literal["low", "moderate", "high", "extreme"] = "moderate"
                    if vol_ratio >= 5.0: sev = "extreme"
                    elif vol_ratio >= 3.0: sev = "high"
                    signals.append(DiagnosticSignalEvidence(
                        type="VOLUME_SPIKE",
                        severity=sev,
                        observed=vol,
                        baseline=round(mean_v5, 1),
                        ratio=vol_ratio,
                        threshold=2.0,
                        formula="target_volume / mean(previous_5_volumes)",
                        lookback_sessions=5,
                        valid_sessions=5,
                        source="taiwan_daily_store",
                    ))

            # ── Signal 2: TURNOVER_SPIKE ──
            if v_stat and v_stat["count_amt_5d"] == 5 and v_stat["mean_amt_5d"] > 0 and amt is not None:
                mean_a5 = float(v_stat["mean_amt_5d"])
                amt_ratio = round(amt / mean_a5, 2)
                if amt_ratio >= 2.0:
                    sev: Literal["low", "moderate", "high", "extreme"] = "moderate"
                    if amt_ratio >= 5.0: sev = "extreme"
                    elif amt_ratio >= 3.0: sev = "high"
                    signals.append(DiagnosticSignalEvidence(
                        type="TURNOVER_SPIKE",
                        severity=sev,
                        observed=amt,
                        baseline=round(mean_a5, 1),
                        ratio=amt_ratio,
                        threshold=2.0,
                        formula="target_amount / mean(previous_5_amounts)",
                        lookback_sessions=5,
                        valid_sessions=5,
                        source="taiwan_daily_store",
                    ))

            # ── Signal 3: PRICE_MOVE ──
            if chg_pct is not None and abs(chg_pct) >= 0.05:
                sev: Literal["low", "moderate", "high", "extreme"] = "extreme" if abs(chg_pct) >= 0.095 else ("high" if abs(chg_pct) >= 0.07 else "moderate")
                signals.append(DiagnosticSignalEvidence(
                    type="PRICE_MOVE",
                    subtype="UP" if chg_pct > 0 else "DOWN",
                    severity=sev,
                    observed=round(chg_pct * 100.0, 2),
                    baseline=0.0,
                    delta=round(chg_pct * 100.0, 2),
                    threshold=5.0,
                    formula="abs(close / previous_close - 1) >= 0.05",
                    lookback_sessions=1,
                    valid_sessions=1,
                    source="taiwan_daily_store",
                ))

            # ── Signal 4: FOREIGN_FLOW_SPIKE ──
            f_mult = None
            if f_net is not None and i_stat and i_stat["count_inst_20d"] >= 15:
                mean_abs_f20 = float(i_stat["mean_abs_foreign_20d"] or 0)
                if mean_abs_f20 >= 10000 and abs(f_net) >= 100000:  # at least 100張 (100,000 shares)
                    f_mult = round(abs(f_net) / mean_abs_f20, 2)
                    if f_mult >= 3.0:
                        sev = "extreme" if f_mult >= 6.0 else "high"
                        signals.append(DiagnosticSignalEvidence(
                            type="FOREIGN_FLOW_SPIKE",
                            subtype="BUY" if f_net > 0 else "SELL",
                            severity=sev,
                            observed=f_net,
                            baseline=round(mean_abs_f20, 1),
                            ratio=f_mult,
                            threshold=3.0,
                            formula="abs(foreign_net) / mean(abs(previous_20_foreign_net)) >= 3.0",
                            lookback_sessions=20,
                            valid_sessions=int(i_stat["count_inst_20d"]),
                            source="taiwan_institutional_store",
                        ))

            # ── Signal 5: TRUST_FLOW_SPIKE ──
            it_mult = None
            if it_net is not None and i_stat and i_stat["count_inst_20d"] >= 15:
                mean_abs_it20 = float(i_stat["mean_abs_trust_20d"] or 0)
                if mean_abs_it20 >= 5000 and abs(it_net) >= 50000:  # at least 50張
                    it_mult = round(abs(it_net) / mean_abs_it20, 2)
                    if it_mult >= 3.0:
                        sev = "extreme" if it_mult >= 6.0 else "high"
                        signals.append(DiagnosticSignalEvidence(
                            type="TRUST_FLOW_SPIKE",
                            subtype="BUY" if it_net > 0 else "SELL",
                            severity=sev,
                            observed=it_net,
                            baseline=round(mean_abs_it20, 1),
                            ratio=it_mult,
                            threshold=3.0,
                            formula="abs(trust_net) / mean(abs(previous_20_trust_net)) >= 3.0",
                            lookback_sessions=20,
                            valid_sessions=int(i_stat["count_inst_20d"]),
                            source="taiwan_institutional_store",
                        ))

            # ── Signal 6: MARGIN_SURGE ──
            if m_chg is not None and m_stat and m_stat["count_margin_20d"] >= 15:
                med_abs_m20 = float(m_stat["median_abs_margin_change_20d"] or 0)
                if med_abs_m20 > 0 and abs(m_chg) >= 500000:  # at least 500張 (500,000 shares)
                    m_mult = round(abs(m_chg) / med_abs_m20, 2)
                    if m_mult >= 3.0:
                        signals.append(DiagnosticSignalEvidence(
                            type="MARGIN_SURGE",
                            subtype="INCREASE" if m_chg > 0 else "DECREASE",
                            severity="high" if m_mult >= 6.0 else "moderate",
                            observed=m_chg,
                            baseline=round(med_abs_m20, 1),
                            ratio=m_mult,
                            threshold=3.0,
                            formula="abs(margin_change) >= 500k AND ratio_to_20d_median >= 3.0",
                            lookback_sessions=20,
                            valid_sessions=int(m_stat["count_margin_20d"]),
                            source="taiwan_margin_store",
                        ))

            # ── Signal 7: SHORT_SURGE ──
            if s_chg is not None and m_stat and m_stat["count_margin_20d"] >= 15:
                med_abs_s20 = float(m_stat["median_abs_short_change_20d"] or 0)
                if med_abs_s20 > 0 and abs(s_chg) >= 100000:  # at least 100張
                    s_mult = round(abs(s_chg) / med_abs_s20, 2)
                    if s_mult >= 3.0:
                        signals.append(DiagnosticSignalEvidence(
                            type="SHORT_SURGE",
                            subtype="INCREASE" if s_chg > 0 else "DECREASE",
                            severity="high" if s_mult >= 6.0 else "moderate",
                            observed=s_chg,
                            baseline=round(med_abs_s20, 1),
                            ratio=s_mult,
                            threshold=3.0,
                            formula="abs(short_change) >= 100k AND ratio_to_20d_median >= 3.0",
                            lookback_sessions=20,
                            valid_sessions=int(m_stat["count_margin_20d"]),
                            source="taiwan_margin_store",
                        ))

            # ── Signal 8: SHORT_MARGIN_RATIO_SPIKE ──
            if sm_ratio is not None and m_stat and m_stat["count_margin_20d"] >= 15:
                med_sm20 = float(m_stat["median_sm_ratio_20d"] or 0)
                sm_delta = round(sm_ratio - med_sm20, 2)
                if sm_delta >= 5.0:  # >= 5% percentage point increase
                    signals.append(DiagnosticSignalEvidence(
                        type="SHORT_MARGIN_RATIO_SPIKE",
                        severity="high" if sm_delta >= 10.0 else "moderate",
                        observed=sm_ratio,
                        baseline=med_sm20,
                        delta=sm_delta,
                        threshold=5.0,
                        formula="(short_margin_ratio - median_20d_ratio) >= 5.0 pct",
                        lookback_sessions=20,
                        valid_sessions=int(m_stat["count_margin_20d"]),
                        source="taiwan_margin_store",
                    ))

            # ── Signal 9: PRICE_FLOW_DIVERGENCE ──
            if chg_pct is not None and abs(chg_pct) >= 0.03:
                # Up price with strong foreign sell
                if chg_pct >= 0.03 and f_net is not None and f_net <= -200000 and (f_mult is not None and f_mult >= 2.0):
                    signals.append(DiagnosticSignalEvidence(
                        type="PRICE_FLOW_DIVERGENCE",
                        subtype="PRICE_UP_FOREIGN_SELL",
                        severity="high",
                        observed=chg_pct * 100.0,
                        baseline=f_net,
                        threshold=3.0,
                        formula="change_pct >= 3% AND foreign_net <= -200k with multiple >= 2x",
                        lookback_sessions=20,
                        valid_sessions=int(i_stat["count_inst_20d"] if i_stat else 1),
                        source="taiwan_daily_store+taiwan_institutional_store",
                    ))
                # Down price with strong foreign buy
                elif chg_pct <= -0.03 and f_net is not None and f_net >= 200000 and (f_mult is not None and f_mult >= 2.0):
                    signals.append(DiagnosticSignalEvidence(
                        type="PRICE_FLOW_DIVERGENCE",
                        subtype="PRICE_DOWN_FOREIGN_BUY",
                        severity="high",
                        observed=chg_pct * 100.0,
                        baseline=f_net,
                        threshold=3.0,
                        formula="change_pct <= -3% AND foreign_net >= 200k with multiple >= 2x",
                        lookback_sessions=20,
                        valid_sessions=int(i_stat["count_inst_20d"] if i_stat else 1),
                        source="taiwan_daily_store+taiwan_institutional_store",
                    ))

            # ── Signal 10: RELATIVE_STRENGTH_OUTLIER ──
            ind_m = ind_metrics_map.get(inst_master.industry or "")
            c5_base = c5_base_map.get(sym)
            if ind_m and ind_m.relative_strength_5d is not None and c5_base and close_p is not None:
                ret_5d = (close_p / c5_base) - 1.0
                # Excess over industry equal-weight 5D
                # RS 5D = ind_5d_return - mkt_5d_return
                # If stock 5D return exceeds industry by >= 10%
                diff_5d = round(ret_5d - (ind_m.average_change_pct or 0.0), 4)
                if abs(diff_5d) >= 0.10:
                    signals.append(DiagnosticSignalEvidence(
                        type="RELATIVE_STRENGTH_OUTLIER",
                        subtype="LEADER" if diff_5d > 0 else "LAGGARD",
                        severity="high" if abs(diff_5d) >= 0.15 else "moderate",
                        observed=round(diff_5d * 100.0, 2),
                        baseline=round((ind_m.average_change_pct or 0.0) * 100.0, 2),
                        delta=round(diff_5d * 100.0, 2),
                        threshold=10.0,
                        formula="abs(stock_5d_return - industry_mean_return) >= 10%",
                        lookback_sessions=5,
                        valid_sessions=5,
                        source="taiwan_daily_store+taiwan_industry_intelligence",
                    ))

            # Filter by specific signal type if requested
            if signal_filter:
                signals = [s for s in signals if s.type == signal_filter or s.subtype == signal_filter]

            if not include_all and len(signals) == 0:
                continue

            compact_ind = CompactIndustryContext(
                industry=inst_master.industry,
                turnover_share=ind_m.turnover_share if ind_m else None,
                advance_ratio=ind_m.advance_ratio if ind_m else None,
                relative_strength_5d=ind_m.relative_strength_5d if ind_m else None,
                relative_strength_20d=ind_m.relative_strength_20d if ind_m else None,
            )

            items.append(TaiwanAbnormalDiagnosticItem(
                symbol=inst_master.symbol,
                code=inst_master.code,
                name=inst_master.name,
                exchange=inst_master.exchange,
                industry=inst_master.industry,
                close=close_p,
                previous_close=prev_close_p,
                change=chg,
                change_pct=chg_pct,
                volume=vol,
                amount=amt,
                volume_ratio_5d=vol_ratio,
                amount_ratio_5d=amt_ratio,
                foreign_net=f_net,
                investment_trust_net=it_net,
                margin_balance_change=m_chg,
                short_balance_change=s_chg,
                short_margin_ratio=sm_ratio,
                signal_count=len(signals),
                signals=signals,
                market_context=compact_market,
                industry_context=compact_ind,
            ))

        # 9. Deterministic Multi-key Sorting:
        # signal_count desc -> abs(change_pct) desc -> amount desc -> symbol asc
        items.sort(
            key=lambda x: (
                -x.signal_count,
                -abs(x.change_pct or 0.0),
                -(x.amount or 0.0),
                x.symbol,
            )
        )

        # 10. Data Quality Compilation
        d_status: DatasetFreshnessStatus = "current" if len(df_target_daily) > 0 else "unavailable"
        i_status: DatasetFreshnessStatus = "current" if len(df_target_inst) > 0 else "unavailable"
        m_status: DatasetFreshnessStatus = "current" if len(df_target_margin) > 0 else "unavailable"
        overall: Literal["complete", "partial", "unavailable"] = (
            "complete" if all(s == "current" for s in [d_status, i_status, m_status])
            else ("partial" if d_status == "current" else "unavailable")
        )

        dq = DiagnosticsDataQuality(
            target_trade_date=str(target),
            universe_supported_count=universe_count,
            evaluated_symbol_count=len(target_daily_map),
            diagnostic_symbol_count=len(items),
            daily_status=d_status,
            institutional_status=i_status,
            margin_status=m_status,
            overall_status=overall,
        )

        return TaiwanAbnormalDiagnosticsSnapshot(
            trade_date=str(target),
            generated_at=taipei_now().isoformat(),
            universe_count=universe_count,
            diagnostic_count=len(items),
            items=items,
            data_quality=dq,
            provenance=[
                "taiwan_security_master",
                "taiwan_daily_store",
                "taiwan_institutional_store",
                "taiwan_margin_store",
                "taiwan_market_intelligence",
                "taiwan_industry_intelligence",
            ],
        )

    def _empty_snapshot(self, target: date) -> TaiwanAbnormalDiagnosticsSnapshot:
        return TaiwanAbnormalDiagnosticsSnapshot(
            trade_date=str(target),
            generated_at=taipei_now().isoformat(),
            universe_count=0,
            diagnostic_count=0,
            items=[],
            data_quality=DiagnosticsDataQuality(
                target_trade_date=str(target),
                universe_supported_count=0,
                evaluated_symbol_count=0,
                diagnostic_symbol_count=0,
                daily_status="unavailable",
                institutional_status="unavailable",
                margin_status="unavailable",
                overall_status="unavailable",
            ),
            provenance=["taiwan_security_master"],
        )
