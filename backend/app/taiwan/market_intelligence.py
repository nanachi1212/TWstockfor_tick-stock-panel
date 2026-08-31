"""Taiwan Market Intelligence Snapshot Service (Phase 7A).

Provides deterministic, traceable, structured market intelligence for a given trading date:
- Daily Breadth: Advances, Declines, Flats, Upper/Lower Limit Counts, Traded Counts, Turnover.
- Market Slices: Total Market, By Exchange (TWSE, TPEx), By Instrument (Stock, ETF).
- Institutional Flows: Market-wide aggregate of Foreign, Investment Trust, and Dealer nets.
- Margin Balances: Market-wide aggregate of Margin Balance/Change and Short Balance/Change.
- Market Benchmarks: TAIEX and TPEx Index status (if available).
- Data Quality & Provenance: Target date resolution, per-dataset freshness, coverage counts.

STRICT CONSTRAINTS:
- 100% Deterministic: NO AI, NO sentiment score, NO mysterious ranking.
- Zero-HTTP at request time: Reads exclusively from local persisted Parquet stores and TradingCalendar.
- Historical Support: Exact-date snapshot with NO look-ahead.
- Authoritative Price Limits: Uses MarketProfileBridge/PriceLimitModel, never naive 10%.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
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
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)


# ── Pydantic Response Contract Models ─────────────────────────


class MarketBreadthStats(BaseModel):
    """Deterministic price movement and trading activity statistics."""

    supported_count: int = 0
    snapshot_row_count: int = 0
    traded_count: int = 0
    advance_count: int = 0
    decline_count: int = 0
    flat_count: int = 0
    uncompared_count: int = 0
    upper_limit_count: int = 0
    lower_limit_count: int = 0
    turnover: float = 0.0  # in TWD (元)


class MarketExchangeBreakdown(BaseModel):
    """Market breadth separated by exchange."""

    twse: MarketBreadthStats
    tpex: MarketBreadthStats


class MarketInstrumentBreakdown(BaseModel):
    """Market breadth separated by instrument category."""

    stock: MarketBreadthStats
    etf: MarketBreadthStats


class InstitutionalMarketAggregate(BaseModel):
    """Aggregate market-wide flow of Three Major Institutional Investors (in shares)."""

    trade_date: str | None = None
    row_count: int = 0
    foreign_net: float | None = None  # in shares
    investment_trust_net: float | None = None  # in shares
    dealer_net: float | None = None  # in shares
    total_net: float | None = None  # in shares (sum of foreign, it, dealer)
    status: DatasetFreshnessStatus = "unavailable"


class MarginMarketAggregate(BaseModel):
    """Aggregate market-wide margin trading and short selling balances (in shares)."""

    trade_date: str | None = None
    row_count: int = 0
    margin_balance: float | None = None  # in shares
    margin_balance_change: float | None = None  # in shares
    short_balance: float | None = None  # in shares
    short_balance_change: float | None = None  # in shares
    aggregate_short_margin_ratio: float | None = None  # short_balance / margin_balance * 100
    status: DatasetFreshnessStatus = "unavailable"


class IndexSnapshot(BaseModel):
    """Benchmark index point (TAIEX or TPEx Index)."""

    symbol: str
    name: str
    trade_date: str | None = None
    close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    status: str = "unavailable"


class MarketIndexesSnapshot(BaseModel):
    """Market benchmark indexes."""

    taiex: IndexSnapshot | None = None
    tpex_index: IndexSnapshot | None = None


class DatasetQualityMeta(BaseModel):
    """Freshness and provenance metadata for a single dataset."""

    dataset: str
    as_of: str | None = None
    status: DatasetFreshnessStatus = "unavailable"
    source: str = "local_store"


class DataQualityReport(BaseModel):
    """Data quality and provenance tracking."""

    target_trade_date: str
    previous_trade_date: str | None = None
    overall_status: Literal["complete", "partial", "unavailable"] = "unavailable"
    daily: DatasetQualityMeta
    institutional: DatasetQualityMeta
    margin: DatasetQualityMeta
    indexes: DatasetQualityMeta
    universe_supported_symbols: int = 0
    daily_snapshot_symbols: int = 0
    missing_symbols_count: int = 0


class TaiwanMarketIntelligenceSnapshot(BaseModel):
    """Unified deterministic Taiwan Market Intelligence Snapshot response contract."""

    trade_date: str
    generated_at: str
    market_totals: MarketBreadthStats
    by_exchange: MarketExchangeBreakdown
    by_instrument: MarketInstrumentBreakdown
    institutional: InstitutionalMarketAggregate
    margin: MarginMarketAggregate
    indexes: MarketIndexesSnapshot
    data_quality: DataQualityReport


# ── Service Implementation ────────────────────────────────────


class TaiwanMarketIntelligenceService:
    """Computes deterministic market breadth, institutional & margin aggregates from local stores."""

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

    def get_previous_trading_date(self, target_date: date) -> date | None:
        """Find the immediately preceding trading date using TaiwanTradingCalendar and store history."""
        available = self.daily_store.available_dates()
        prior_dates = [d for d in available if d < target_date]
        if prior_dates:
            return max(prior_dates)

        # Fall back to calendar iteration if daily store doesn't have prior dates
        cur = target_date - timedelta(days=1)
        while self.calendar.is_trading_day(cur) is False:
            cur -= timedelta(days=1)
        return cur

    def _calc_breadth(self, df: pl.DataFrame) -> MarketBreadthStats:
        """Compute MarketBreadthStats from joined current & previous dataframe."""
        if df.is_empty():
            return MarketBreadthStats()

        snapshot_rows = len(df)
        traded = df.filter(pl.col("volume") > 0)
        traded_count = len(traded)
        turnover = float(df["amount"].sum() or 0.0)

        # Comparable with previous close
        comparable = df.filter(pl.col("prev_close").is_not_null())
        uncompared_count = snapshot_rows - len(comparable)

        advance_count = len(comparable.filter(pl.col("close") > pl.col("prev_close")))
        decline_count = len(comparable.filter(pl.col("close") < pl.col("prev_close")))
        flat_count = len(comparable.filter(pl.col("close") == pl.col("prev_close")))

        # Limit counts
        limit_ups = len(df.filter(pl.col("is_limit_up") == True))
        limit_downs = len(df.filter(pl.col("is_limit_down") == True))

        return MarketBreadthStats(
            supported_count=snapshot_rows,
            snapshot_row_count=snapshot_rows,
            traded_count=traded_count,
            advance_count=advance_count,
            decline_count=decline_count,
            flat_count=flat_count,
            uncompared_count=uncompared_count,
            upper_limit_count=limit_ups,
            lower_limit_count=limit_downs,
            turnover=turnover,
        )

    def get_snapshot(self, target_date: date | None = None) -> TaiwanMarketIntelligenceSnapshot:
        """Build a deterministic Taiwan Market Intelligence Snapshot for target_date."""
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        prev_d = self.get_previous_trading_date(target)

        # 1. Load Security Master supported universe
        universe_df = self.security_master.to_dataframe(supported_only=True)
        if not universe_df.is_empty():
            universe_df = universe_df.filter(pl.col("listing_status") == "active")
            universe_df = universe_df.filter(pl.col("instrument_type").is_in(["stock", "etf"]))
        supported_symbols = universe_df["symbol"].to_list() if not universe_df.is_empty() else []
        supported_count = len(supported_symbols)

        # 2. Load DailyStore current date and previous date (NO look-ahead)
        curr_daily = self.daily_store.read_range(supported_symbols, target, target)
        prev_daily = self.daily_store.read_range(supported_symbols, prev_d, prev_d) if prev_d else pl.DataFrame()

        # Check daily status
        daily_available = self.daily_store.available_dates()
        daily_as_of = max(daily_available) if daily_available else None
        daily_status: DatasetFreshnessStatus = (
            "current" if (daily_as_of and daily_as_of >= target)
            else ("stale" if daily_as_of else "unavailable")
        )

        matched_symbols_count = len(curr_daily)
        missing_count = max(0, supported_count - matched_symbols_count)

        # 3. Compute price limits and comparisons
        if not curr_daily.is_empty():
            # Join previous close
            if not prev_daily.is_empty():
                prev_sub = prev_daily.select([
                    pl.col("symbol"),
                    pl.col("close").alias("prev_close"),
                ])
                joined = curr_daily.join(prev_sub, on="symbol", how="left")
            else:
                joined = curr_daily.with_columns(pl.lit(None).cast(pl.Float64).alias("prev_close"))

            # Join metadata
            meta_sub = universe_df.select(["symbol", "exchange", "instrument_type"])
            joined = joined.join(meta_sub, on="symbol", how="left")

            # Determine limit-up / limit-down per row
            limit_up_flags = []
            limit_down_flags = []
            for r in joined.iter_rows(named=True):
                sym = r["symbol"]
                close = r.get("close")
                prev_close = r.get("prev_close")
                inst = self.security_master.get_instrument(sym)

                if inst is None or close is None or prev_close is None or prev_close <= 0:
                    limit_up_flags.append(False)
                    limit_down_flags.append(False)
                    continue

                try:
                    limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
                except ValueError:
                    limit_up_flags.append(False)
                    limit_down_flags.append(False)
                    continue

                if limit_pct is None:  # NO_LIMIT instruments (e.g. foreign ETF) never count as hit limit
                    limit_up_flags.append(False)
                    limit_down_flags.append(False)
                    continue

                upper, lower = MarketProfileBridge.calc_limits(prev_close, inst)
                is_up = bool(upper is not None and close >= upper)
                is_dn = bool(lower is not None and close <= lower)
                limit_up_flags.append(is_up)
                limit_down_flags.append(is_dn)

            joined = joined.with_columns([
                pl.Series("is_limit_up", limit_up_flags),
                pl.Series("is_limit_down", limit_down_flags),
            ])

            # Compute totals & breakdowns
            totals = self._calc_breadth(joined)
            totals.supported_count = supported_count

            twse_df = joined.filter(pl.col("exchange") == "TWSE")
            tpex_df = joined.filter(pl.col("exchange") == "TPEX")
            twse_breadth = self._calc_breadth(twse_df)
            twse_breadth.supported_count = len(universe_df.filter(pl.col("exchange") == "TWSE"))
            tpex_breadth = self._calc_breadth(tpex_df)
            tpex_breadth.supported_count = len(universe_df.filter(pl.col("exchange") == "TPEX"))

            stock_df = joined.filter(pl.col("instrument_type") == "stock")
            etf_df = joined.filter(pl.col("instrument_type") == "etf")
            stock_breadth = self._calc_breadth(stock_df)
            stock_breadth.supported_count = len(universe_df.filter(pl.col("instrument_type") == "stock"))
            etf_breadth = self._calc_breadth(etf_df)
            etf_breadth.supported_count = len(universe_df.filter(pl.col("instrument_type") == "etf"))

        else:
            totals = MarketBreadthStats(supported_count=supported_count)
            twse_breadth = MarketBreadthStats()
            tpex_breadth = MarketBreadthStats()
            stock_breadth = MarketBreadthStats()
            etf_breadth = MarketBreadthStats()

        by_exchange = MarketExchangeBreakdown(twse=twse_breadth, tpex=tpex_breadth)
        by_instrument = MarketInstrumentBreakdown(stock=stock_breadth, etf=etf_breadth)

        # 4. Institutional Aggregation (NO look-ahead)
        inst_df = self.inst_store.read_range(None, target, target)
        inst_available = self.inst_store.available_dates()
        inst_as_of = max(inst_available) if inst_available else None
        inst_status: DatasetFreshnessStatus = (
            "current" if (inst_as_of and inst_as_of >= target)
            else ("stale" if inst_as_of else "unavailable")
        )

        if not inst_df.is_empty():
            f_net = float(inst_df["foreign_net"].sum() or 0.0)
            it_net = float(inst_df["investment_trust_net"].sum() or 0.0)
            d_net = float(inst_df["dealer_net"].sum() or 0.0)
            tot_net = f_net + it_net + d_net
            inst_agg = InstitutionalMarketAggregate(
                trade_date=str(target),
                row_count=len(inst_df),
                foreign_net=f_net,
                investment_trust_net=it_net,
                dealer_net=d_net,
                total_net=tot_net,
                status=inst_status,
            )
        else:
            inst_agg = InstitutionalMarketAggregate(
                trade_date=str(target) if inst_as_of else None,
                row_count=0,
                status=inst_status,
            )

        # 5. Margin Aggregation (NO look-ahead)
        m_df = self.margin_store.read_range(None, target, target)
        m_available = self.margin_store.available_dates()
        m_as_of = max(m_available) if m_available else None
        m_status: DatasetFreshnessStatus = (
            "current" if (m_as_of and m_as_of >= target)
            else ("stale" if m_as_of else "unavailable")
        )

        if not m_df.is_empty():
            mb = float(m_df["margin_balance"].sum() or 0.0)
            mc = float(m_df["margin_change"].sum() or 0.0) if "margin_change" in m_df.columns else None
            sb = float(m_df["short_balance"].sum() or 0.0)
            sc = float(m_df["short_change"].sum() or 0.0) if "short_change" in m_df.columns else None
            # Compute aggregate ratio safely: sum(short) / sum(margin) * 100
            ratio = round((sb / mb * 100.0), 2) if mb > 0 else None

            margin_agg = MarginMarketAggregate(
                trade_date=str(target),
                row_count=len(m_df),
                margin_balance=mb,
                margin_balance_change=mc,
                short_balance=sb,
                short_balance_change=sc,
                aggregate_short_margin_ratio=ratio,
                status=m_status,
            )
        else:
            margin_agg = MarginMarketAggregate(
                trade_date=str(target) if m_as_of else None,
                row_count=0,
                status=m_status,
            )

        # 6. Indexes Integration (Pure offline / persisted fallback)
        # Note: In accordance with zero-HTTP acceptance rules, do not fetch network index data.
        indexes_snapshot = MarketIndexesSnapshot(
            taiex=IndexSnapshot(
                symbol="TAIEX",
                name="發行量加權股價指數",
                trade_date=str(target),
                status="unavailable",
            ),
            tpex_index=IndexSnapshot(
                symbol="TPEX_INDEX",
                name="櫃買指數",
                trade_date=str(target),
                status="unavailable",
            ),
        )

        # 7. Data Quality & Overall Status
        dataset_statuses = [daily_status, inst_status, m_status]
        if all(s == "current" for s in dataset_statuses):
            overall_status = "complete"
        elif any(s == "current" for s in dataset_statuses) or any(s == "stale" for s in dataset_statuses):
            overall_status = "partial"
        else:
            overall_status = "unavailable"

        dq = DataQualityReport(
            target_trade_date=str(target),
            previous_trade_date=str(prev_d) if prev_d else None,
            overall_status=overall_status,
            daily=DatasetQualityMeta(
                dataset="daily",
                as_of=str(daily_as_of) if daily_as_of else None,
                status=daily_status,
                source="taiwan_daily_store",
            ),
            institutional=DatasetQualityMeta(
                dataset="institutional",
                as_of=str(inst_as_of) if inst_as_of else None,
                status=inst_status,
                source="taiwan_institutional_store",
            ),
            margin=DatasetQualityMeta(
                dataset="margin",
                as_of=str(m_as_of) if m_as_of else None,
                status=m_status,
                source="taiwan_margin_store",
            ),
            indexes=DatasetQualityMeta(
                dataset="indexes",
                as_of=None,
                status="unavailable",
                source="taiwan_index_provider",
            ),
            universe_supported_symbols=supported_count,
            daily_snapshot_symbols=matched_symbols_count,
            missing_symbols_count=missing_count,
        )

        return TaiwanMarketIntelligenceSnapshot(
            trade_date=str(target),
            generated_at=taipei_now().isoformat(),
            market_totals=totals,
            by_exchange=by_exchange,
            by_instrument=by_instrument,
            institutional=inst_agg,
            margin=margin_agg,
            indexes=indexes_snapshot,
            data_quality=dq,
        )
