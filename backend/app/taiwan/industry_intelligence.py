"""Taiwan Industry / Sector Intelligence Snapshot Service (Phase 7B).

Provides deterministic, traceable, structured industry-level intelligence for Taiwan stocks:
- Universe: Active supported STOCKS only (ETFs excluded from corporate industry rankings).
- Authority: TaiwanSecurityMaster industry field (34 canonical industries + UNCLASSIFIED tracking).
- Breadth: Advance, Decline, Flat, Uncompared counts, Advance Ratio, Decline Ratio.
- Performance: Average change %, Median change % (decimal 0.03 = 3%).
- Turnover: Industry turnover in TWD, and turnover share against total stock turnover.
- Institutional Flows: Aggregated foreign_net, investment_trust_net, dealer_net per industry (shares).
- Margin Balances: Aggregated margin_balance_change, short_balance_change per industry (shares).
- Relative Strength:
    * 5D RS = Industry equal-weight 5D return - Market stock equal-weight 5D return.
    * 20D RS = Industry equal-weight 20D return - Market stock equal-weight 20D return.
    * Uses trading-session lookbacks (not calendar days).
    * Excludes newly listed stocks without full history from N-day calculation without skewing.
- Top Movers: Top 3 gainers, top 3 losers, top 3 turnover per industry.
- Quality & Provenance: Data freshness, classified/unclassified coverage counts, zero external HTTP.
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
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

logger = logging.getLogger(__name__)


# ── Response Contract Models ──────────────────────────────────


class IndustryConstituentMover(BaseModel):
    """Representative constituent stock item (top gainer, loser, or turnover)."""

    symbol: str
    name: str
    change_pct: float | None = None  # decimal e.g. 0.05
    close: float | None = None
    turnover: float = 0.0  # in TWD


class IndustryMetrics(BaseModel):
    """Deterministic intelligence and breadth metrics for a single Taiwan industry."""

    industry: str
    supported_symbol_count: int = 0
    snapshot_symbol_count: int = 0
    traded_symbol_count: int = 0
    comparable_symbol_count: int = 0

    turnover: float = 0.0  # in TWD (元)
    turnover_share: float = 0.0  # ratio against total active stock turnover e.g. 0.35

    advance_count: int = 0
    decline_count: int = 0
    flat_count: int = 0
    uncompared_count: int = 0

    advance_ratio: float | None = None  # advance / (advance + decline + flat)
    decline_ratio: float | None = None  # decline / (advance + decline + flat)

    average_change_pct: float | None = None  # decimal e.g. 0.02
    median_change_pct: float | None = None  # decimal e.g. 0.015

    foreign_net: float | None = None  # in shares
    investment_trust_net: float | None = None  # in shares
    dealer_net: float | None = None  # in shares

    margin_balance_change: float | None = None  # in shares
    short_balance_change: float | None = None  # in shares

    relative_strength_5d: float | None = None  # decimal e.g. 0.025
    relative_strength_20d: float | None = None  # decimal e.g. -0.010
    relative_strength_5d_comparable_count: int = 0
    relative_strength_20d_comparable_count: int = 0

    top_gainers: list[IndustryConstituentMover] = Field(default_factory=list)
    top_losers: list[IndustryConstituentMover] = Field(default_factory=list)
    top_turnover: list[IndustryConstituentMover] = Field(default_factory=list)


class MarketReferenceStats(BaseModel):
    """Broad stock-market benchmark metrics used for relative strength calculation."""

    trade_date: str
    total_stock_turnover: float = 0.0  # total turnover of active stocks in TWD
    market_equal_weight_return_5d: float | None = None
    market_equal_weight_return_20d: float | None = None
    comparable_stocks_5d_count: int = 0
    comparable_stocks_20d_count: int = 0


class IndustryDataQuality(BaseModel):
    """Quality and completeness report for industry intelligence."""

    target_trade_date: str
    previous_trade_date: str | None = None
    base_date_5d: str | None = None
    base_date_20d: str | None = None

    supported_stock_count: int = 0
    classified_stock_count: int = 0
    unclassified_stock_count: int = 0
    etfs_excluded_count: int = 0

    industry_count: int = 0
    classification_coverage_pct: float = 100.0

    daily_status: DatasetFreshnessStatus = "unavailable"
    institutional_status: DatasetFreshnessStatus = "unavailable"
    margin_status: DatasetFreshnessStatus = "unavailable"
    overall_status: Literal["complete", "partial", "unavailable"] = "unavailable"


class TaiwanIndustryIntelligenceSnapshot(BaseModel):
    """Unified deterministic Taiwan Industry Intelligence Snapshot."""

    trade_date: str
    generated_at: str
    market_reference: MarketReferenceStats
    industries: list[IndustryMetrics]
    data_quality: IndustryDataQuality


# ── Service Implementation ────────────────────────────────────


class TaiwanIndustryIntelligenceService:
    """Computes deterministic industry breadth, returns, flows, and relative strength."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        inst_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.inst_store = inst_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.security_master = security_master or get_security_master()

    def _resolve_lookback_dates(self, target_date: date) -> tuple[date | None, date | None, date | None]:
        """Resolve previous trading day, 5-session lookback base date, and 20-session lookback base date.
        
        Guarantees NO look-ahead: all dates are strictly < target_date.
        """
        available = sorted([d for d in self.daily_store.available_dates() if d <= target_date])
        if not available:
            return None, None, None

        if target_date in available:
            idx = available.index(target_date)
            prior = available[:idx]
        else:
            prior = [d for d in available if d < target_date]

        if not prior:
            return None, None, None

        d_prev = prior[-1]
        d_5d = prior[-5] if len(prior) >= 5 else None
        d_20d = prior[-20] if len(prior) >= 20 else None

        return d_prev, d_5d, d_20d

    def get_snapshot(
        self,
        target_date: date | None = None,
        sort_by: str = "turnover",
        order: str = "desc",
    ) -> TaiwanIndustryIntelligenceSnapshot:
        """Build a deterministic Taiwan Industry Intelligence Snapshot for target_date."""
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        d_prev, d_5d, d_20d = self._resolve_lookback_dates(target)

        # 1. Base Universe: Active Supported Stocks only (ETFs excluded from industry rankings)
        master_df = self.security_master.to_dataframe(supported_only=True)
        active_stocks = master_df.filter(
            (pl.col("listing_status") == "active") & (pl.col("instrument_type") == "stock")
        )
        total_stocks_count = len(active_stocks)
        etf_count = len(master_df.filter(
            (pl.col("listing_status") == "active") & (pl.col("instrument_type") == "etf")
        ))

        # Classify unclassified (industry null/empty -> UNCLASSIFIED)
        active_stocks = active_stocks.with_columns(
            pl.when(pl.col("industry").is_null() | (pl.col("industry").str.strip_chars() == ""))
            .then(pl.lit("UNCLASSIFIED"))
            .otherwise(pl.col("industry"))
            .alias("industry")
        )
        classified_count = len(active_stocks.filter(pl.col("industry") != "UNCLASSIFIED"))
        unclassified_count = total_stocks_count - classified_count
        coverage_pct = round((classified_count / total_stocks_count * 100.0), 2) if total_stocks_count > 0 else 100.0

        symbols = active_stocks["symbol"].to_list()

        # 2. Batch Read Daily Store Data (NO look-ahead)
        curr_daily = self.daily_store.read_range(symbols, target, target)
        prev_daily = self.daily_store.read_range(symbols, d_prev, d_prev) if d_prev else pl.DataFrame()
        hist_5d = self.daily_store.read_range(symbols, d_5d, d_5d) if d_5d else pl.DataFrame()
        hist_20d = self.daily_store.read_range(symbols, d_20d, d_20d) if d_20d else pl.DataFrame()

        # Dataset Freshness
        daily_available = self.daily_store.available_dates()
        daily_as_of = max(daily_available) if daily_available else None
        daily_status: DatasetFreshnessStatus = (
            "current" if (daily_as_of and daily_as_of >= target)
            else ("stale" if daily_as_of else "unavailable")
        )

        # 3. Batch Read Institutional & Margin Stores (NO look-ahead)
        inst_df = self.inst_store.read_range(symbols, target, target)
        inst_available = self.inst_store.available_dates()
        inst_as_of = max(inst_available) if inst_available else None
        inst_status: DatasetFreshnessStatus = (
            "current" if (inst_as_of and inst_as_of >= target)
            else ("stale" if inst_as_of else "unavailable")
        )

        margin_df = self.margin_store.read_range(symbols, target, target)
        m_available = self.margin_store.available_dates()
        m_as_of = max(m_available) if m_available else None
        m_status: DatasetFreshnessStatus = (
            "current" if (m_as_of and m_as_of >= target)
            else ("stale" if m_as_of else "unavailable")
        )

        # Overall Quality Status
        statuses = [daily_status, inst_status, m_status]
        if all(s == "current" for s in statuses):
            overall_status = "complete"
        elif any(s == "current" for s in statuses) or any(s == "stale" for s in statuses):
            overall_status = "partial"
        else:
            overall_status = "unavailable"

        # 4. Join Base Dataframe
        if not curr_daily.is_empty():
            base = active_stocks.select(["symbol", "name", "industry", "exchange"]).join(
                curr_daily.select(["symbol", "close", "amount", "volume"]),
                on="symbol",
                how="left",
            )
            # Fill turnover & volume = 0 if missing from snapshot
            base = base.with_columns([
                pl.col("amount").fill_null(0.0).alias("amount"),
                pl.col("volume").fill_null(0.0).alias("volume"),
            ])
        else:
            base = active_stocks.select(["symbol", "name", "industry", "exchange"]).with_columns([
                pl.lit(None).cast(pl.Float64).alias("close"),
                pl.lit(0.0).alias("amount"),
                pl.lit(0.0).alias("volume"),
            ])

        # Join Previous Close
        if not prev_daily.is_empty():
            prev_sub = prev_daily.select(["symbol", pl.col("close").alias("close_prev")])
            base = base.join(prev_sub, on="symbol", how="left")
        else:
            base = base.with_columns(pl.lit(None).cast(pl.Float64).alias("close_prev"))

        # Join 5D Lookback Close
        if not hist_5d.is_empty():
            sub_5d = hist_5d.select(["symbol", pl.col("close").alias("close_5d")])
            base = base.join(sub_5d, on="symbol", how="left")
        else:
            base = base.with_columns(pl.lit(None).cast(pl.Float64).alias("close_5d"))

        # Join 20D Lookback Close
        if not hist_20d.is_empty():
            sub_20d = hist_20d.select(["symbol", pl.col("close").alias("close_20d")])
            base = base.join(sub_20d, on="symbol", how="left")
        else:
            base = base.with_columns(pl.lit(None).cast(pl.Float64).alias("close_20d"))

        # Join Institutional
        if not inst_df.is_empty():
            inst_sub = inst_df.select([
                "symbol",
                "foreign_net",
                "investment_trust_net",
                "dealer_net",
            ])
            base = base.join(inst_sub, on="symbol", how="left")
        else:
            base = base.with_columns([
                pl.lit(None).cast(pl.Float64).alias("foreign_net"),
                pl.lit(None).cast(pl.Float64).alias("investment_trust_net"),
                pl.lit(None).cast(pl.Float64).alias("dealer_net"),
            ])

        # Join Margin
        if not margin_df.is_empty():
            margin_sub = margin_df.select([
                "symbol",
                pl.col("margin_change").alias("margin_balance_change") if "margin_change" in margin_df.columns else pl.lit(None).cast(pl.Float64).alias("margin_balance_change"),
                pl.col("short_change").alias("short_balance_change") if "short_change" in margin_df.columns else pl.lit(None).cast(pl.Float64).alias("short_balance_change"),
            ])
            base = base.join(margin_sub, on="symbol", how="left")
        else:
            base = base.with_columns([
                pl.lit(None).cast(pl.Float64).alias("margin_balance_change"),
                pl.lit(None).cast(pl.Float64).alias("short_balance_change"),
            ])

        # 5. Compute Stock Returns (decimal)
        base = base.with_columns([
            pl.when(pl.col("close_prev").is_not_null() & (pl.col("close_prev") > 0))
            .then((pl.col("close") / pl.col("close_prev")) - 1.0)
            .otherwise(None)
            .alias("change_pct"),

            pl.when(pl.col("close_5d").is_not_null() & (pl.col("close_5d") > 0))
            .then((pl.col("close") / pl.col("close_5d")) - 1.0)
            .otherwise(None)
            .alias("return_5d"),

            pl.when(pl.col("close_20d").is_not_null() & (pl.col("close_20d") > 0))
            .then((pl.col("close") / pl.col("close_20d")) - 1.0)
            .otherwise(None)
            .alias("return_20d"),
        ])

        # Total market stock turnover denominator
        market_stock_turnover = float(base["amount"].sum() or 0.0)

        # Market-wide equal-weight returns for relative strength baseline
        valid_5d = base.filter(pl.col("return_5d").is_not_null())
        m_ret_5d = float(valid_5d["return_5d"].mean()) if not valid_5d.is_empty() else None
        m_count_5d = len(valid_5d)

        valid_20d = base.filter(pl.col("return_20d").is_not_null())
        m_ret_20d = float(valid_20d["return_20d"].mean()) if not valid_20d.is_empty() else None
        m_count_20d = len(valid_20d)

        market_ref = MarketReferenceStats(
            trade_date=str(target),
            total_stock_turnover=market_stock_turnover,
            market_equal_weight_return_5d=round(m_ret_5d, 6) if m_ret_5d is not None else None,
            market_equal_weight_return_20d=round(m_ret_20d, 6) if m_ret_20d is not None else None,
            comparable_stocks_5d_count=m_count_5d,
            comparable_stocks_20d_count=m_count_20d,
        )

        # 6. Aggregate per Industry
        unique_industries = sorted(base["industry"].unique().to_list())
        industry_metrics_list: list[IndustryMetrics] = []

        for ind in unique_industries:
            ind_df = base.filter(pl.col("industry") == ind)
            supp_cnt = len(ind_df)
            snap_cnt = len(ind_df.filter(pl.col("close").is_not_null()))
            traded_cnt = len(ind_df.filter(pl.col("volume") > 0))
            ind_turnover = float(ind_df["amount"].sum() or 0.0)
            turnover_sh = round(ind_turnover / market_stock_turnover, 6) if market_stock_turnover > 0 else 0.0

            # Breadth
            comp_df = ind_df.filter(pl.col("change_pct").is_not_null())
            comp_cnt = len(comp_df)
            uncomp_cnt = supp_cnt - comp_cnt

            adv_cnt = len(comp_df.filter(pl.col("change_pct") > 0))
            dec_cnt = len(comp_df.filter(pl.col("change_pct") < 0))
            flat_cnt = len(comp_df.filter(pl.col("change_pct") == 0))

            breadth_denom = adv_cnt + dec_cnt + flat_cnt
            adv_ratio = round(adv_cnt / breadth_denom, 4) if breadth_denom > 0 else None
            dec_ratio = round(dec_cnt / breadth_denom, 4) if breadth_denom > 0 else None

            # Average & Median Change %
            avg_chg = round(float(comp_df["change_pct"].mean()), 6) if not comp_df.is_empty() else None
            med_chg = round(float(comp_df["change_pct"].median()), 6) if not comp_df.is_empty() else None

            # Institutional
            has_inst = ind_df.filter(pl.col("foreign_net").is_not_null())
            f_net = float(has_inst["foreign_net"].sum()) if not has_inst.is_empty() else None
            it_net = float(has_inst["investment_trust_net"].sum()) if not has_inst.is_empty() else None
            d_net = float(has_inst["dealer_net"].sum()) if not has_inst.is_empty() else None

            # Margin
            has_m = ind_df.filter(pl.col("margin_balance_change").is_not_null())
            mb_chg = float(has_m["margin_balance_change"].sum()) if not has_m.is_empty() else None
            sb_chg = float(has_m["short_balance_change"].sum()) if not has_m.is_empty() else None

            # Relative Strength 5D
            comp_5d = ind_df.filter(pl.col("return_5d").is_not_null())
            rs_5d_cnt = len(comp_5d)
            if rs_5d_cnt > 0 and m_ret_5d is not None:
                ind_5d_mean = float(comp_5d["return_5d"].mean())
                rs_5d = round(ind_5d_mean - m_ret_5d, 6)
            else:
                rs_5d = None

            # Relative Strength 20D
            comp_20d = ind_df.filter(pl.col("return_20d").is_not_null())
            rs_20d_cnt = len(comp_20d)
            if rs_20d_cnt > 0 and m_ret_20d is not None:
                ind_20d_mean = float(comp_20d["return_20d"].mean())
                rs_20d = round(ind_20d_mean - m_ret_20d, 6)
            else:
                rs_20d = None

            # Top Gainers (max 3: change_pct DESC, amount DESC, symbol ASC)
            valid_movers = ind_df.filter(pl.col("change_pct").is_not_null())
            gainers = (
                valid_movers.sort(["change_pct", "amount", "symbol"], descending=[True, True, False])
                .head(3)
                .iter_rows(named=True)
            )
            top_gainers = [
                IndustryConstituentMover(
                    symbol=r["symbol"],
                    name=r["name"],
                    change_pct=round(r["change_pct"], 6) if r["change_pct"] is not None else None,
                    close=r["close"],
                    turnover=float(r["amount"] or 0.0),
                )
                for r in gainers
            ]

            # Top Losers (max 3: change_pct ASC, amount DESC, symbol ASC)
            losers = (
                valid_movers.sort(["change_pct", "amount", "symbol"], descending=[False, True, False])
                .head(3)
                .iter_rows(named=True)
            )
            top_losers = [
                IndustryConstituentMover(
                    symbol=r["symbol"],
                    name=r["name"],
                    change_pct=round(r["change_pct"], 6) if r["change_pct"] is not None else None,
                    close=r["close"],
                    turnover=float(r["amount"] or 0.0),
                )
                for r in losers
            ]

            # Top Turnover (max 3: amount DESC, symbol ASC)
            turnovers = (
                ind_df.sort(["amount", "symbol"], descending=[True, False])
                .head(3)
                .iter_rows(named=True)
            )
            top_turnovers = [
                IndustryConstituentMover(
                    symbol=r["symbol"],
                    name=r["name"],
                    change_pct=round(r["change_pct"], 6) if r["change_pct"] is not None else None,
                    close=r["close"],
                    turnover=float(r["amount"] or 0.0),
                )
                for r in turnovers
            ]

            metric = IndustryMetrics(
                industry=ind,
                supported_symbol_count=supp_cnt,
                snapshot_symbol_count=snap_cnt,
                traded_symbol_count=traded_cnt,
                comparable_symbol_count=comp_cnt,
                turnover=ind_turnover,
                turnover_share=turnover_sh,
                advance_count=adv_cnt,
                decline_count=dec_cnt,
                flat_count=flat_cnt,
                uncompared_count=uncomp_cnt,
                advance_ratio=adv_ratio,
                decline_ratio=dec_ratio,
                average_change_pct=avg_chg,
                median_change_pct=med_chg,
                foreign_net=f_net,
                investment_trust_net=it_net,
                dealer_net=d_net,
                margin_balance_change=mb_chg,
                short_balance_change=sb_chg,
                relative_strength_5d=rs_5d,
                relative_strength_20d=rs_20d,
                relative_strength_5d_comparable_count=rs_5d_cnt,
                relative_strength_20d_comparable_count=rs_20d_cnt,
                top_gainers=top_gainers,
                top_losers=top_losers,
                top_turnover=top_turnovers,
            )
            industry_metrics_list.append(metric)

        # Sort industries (default turnover DESC)
        reverse = (order.lower() == "desc")
        if sort_by == "turnover":
            industry_metrics_list.sort(key=lambda x: x.turnover, reverse=reverse)
        elif sort_by == "median_change_pct":
            industry_metrics_list.sort(key=lambda x: (x.median_change_pct is not None, x.median_change_pct or 0.0), reverse=reverse)
        elif sort_by == "relative_strength_5d":
            industry_metrics_list.sort(key=lambda x: (x.relative_strength_5d is not None, x.relative_strength_5d or 0.0), reverse=reverse)
        elif sort_by == "relative_strength_20d":
            industry_metrics_list.sort(key=lambda x: (x.relative_strength_20d is not None, x.relative_strength_20d or 0.0), reverse=reverse)
        elif sort_by == "advance_ratio":
            industry_metrics_list.sort(key=lambda x: (x.advance_ratio is not None, x.advance_ratio or 0.0), reverse=reverse)
        elif sort_by == "foreign_net":
            industry_metrics_list.sort(key=lambda x: (x.foreign_net is not None, x.foreign_net or 0.0), reverse=reverse)
        elif sort_by == "investment_trust_net":
            industry_metrics_list.sort(key=lambda x: (x.investment_trust_net is not None, x.investment_trust_net or 0.0), reverse=reverse)

        dq = IndustryDataQuality(
            target_trade_date=str(target),
            previous_trade_date=str(d_prev) if d_prev else None,
            base_date_5d=str(d_5d) if d_5d else None,
            base_date_20d=str(d_20d) if d_20d else None,
            supported_stock_count=total_stocks_count,
            classified_stock_count=classified_count,
            unclassified_stock_count=unclassified_count,
            etfs_excluded_count=etf_count,
            industry_count=len(industry_metrics_list),
            classification_coverage_pct=coverage_pct,
            daily_status=daily_status,
            institutional_status=inst_status,
            margin_status=m_status,
            overall_status=overall_status,
        )

        return TaiwanIndustryIntelligenceSnapshot(
            trade_date=str(target),
            generated_at=taipei_now().isoformat(),
            market_reference=market_ref,
            industries=industry_metrics_list,
            data_quality=dq,
        )
