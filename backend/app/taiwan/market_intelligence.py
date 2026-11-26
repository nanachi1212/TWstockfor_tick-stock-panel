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
from datetime import date, timedelta
from typing import Literal

import polars as pl
from pydantic import BaseModel

from app.taiwan.corporate_actions import CorporateActionStore, announced_price_limits
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import (
    DatasetFreshnessStatus,
    resolve_target_latest_trading_date,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.observed_universe import TPEX_SOURCE, TWSE_SOURCE, ObservedUniverseStore
from app.taiwan.pit_universe import PitUniverse
from app.taiwan.providers.taiwan_values import parse_taiwan_date
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.research_metrics import research_sessions
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)
MarketDatasetStatus = DatasetFreshnessStatus | Literal["partial"]


def _complete_sum(frame: pl.DataFrame, column: str) -> float | None:
    if column not in frame.columns or frame[column].null_count():
        return None
    return float(frame[column].sum())


def _aggregate_status(as_of: date, expected: date, *values: float | None) -> MarketDatasetStatus:
    if all(value is None for value in values):
        return "unavailable"
    if as_of < expected:
        return "stale"
    return "partial" if None in values else "current"


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
    official_compared_count: int = 0
    inferred_compared_count: int = 0
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
    status: MarketDatasetStatus = "unavailable"


class MarginMarketAggregate(BaseModel):
    """Aggregate market-wide margin trading and short selling balances (in shares)."""

    trade_date: str | None = None
    row_count: int = 0
    margin_balance: float | None = None  # in shares
    margin_balance_change: float | None = None  # in shares
    short_balance: float | None = None  # in shares
    short_balance_change: float | None = None  # in shares
    aggregate_short_margin_ratio: float | None = None  # short_balance / margin_balance * 100
    status: MarketDatasetStatus = "unavailable"


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


def persisted_index_snapshot(target: date | None = None) -> MarketIndexesSnapshot:
    """Offline projection of persisted official benchmark closes (no network).

    change_pct is a fraction; trade_date is the persisted close date,
    which may be older than target while official publication lags.
    """
    from app.taiwan.benchmark_store import latest_benchmark

    def snap(symbol: str, name: str) -> IndexSnapshot:
        row = latest_benchmark(symbol, on_or_before=target)
        if not row:
            return IndexSnapshot(symbol=symbol, name=name, status="unavailable")
        return IndexSnapshot(
            symbol=symbol, name=name, trade_date=row["date"].isoformat(), close=row["close"],
            change=row["change"], change_pct=row["change_pct"],
            status="official_close" if target is None or row["date"] >= target else "stale",
        )

    return MarketIndexesSnapshot(
        taiex=snap("TAIEX", "發行量加權股價指數"),
        tpex_index=snap("TPEX_INDEX", "櫃買指數"),
    )


class DatasetQualityMeta(BaseModel):
    """Freshness and provenance metadata for a single dataset."""

    dataset: str
    as_of: str | None = None
    status: MarketDatasetStatus = "unavailable"
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
    universe_status: Literal["verified", "partial", "unavailable"] = "verified"
    price_limit_unavailable_count: int = 0
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
        evidence_store: ObservedUniverseStore | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.evidence_store = evidence_store or ObservedUniverseStore()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.action_store = CorporateActionStore()
        self.inst_store = inst_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.security_master = security_master or get_security_master()

    def get_previous_trading_date(self, target_date: date) -> date | None:
        """Find the immediately preceding trading date using TaiwanTradingCalendar and store history."""
        sessions = research_sessions(
            target_date - timedelta(days=1), 1, set(self.daily_store.available_dates()),
            self.calendar, self.evidence_store,
        )
        return sessions[0] if sessions else None

    def _calc_breadth(self, df: pl.DataFrame) -> MarketBreadthStats:
        """Compute MarketBreadthStats from joined current & previous dataframe."""
        if df.is_empty():
            return MarketBreadthStats()

        snapshot_rows = len(df)
        traded = df.filter(pl.col("volume") > 0)
        traded_count = len(traded)
        turnover = float(df["amount"].sum() or 0.0)

        # An exchange X (not comparable) is distinct from zero price movement.
        has_close = pl.col("close").is_finite() & (pl.col("close") > 0)
        direction = pl.when(has_close).then(
            pl.coalesce(
                pl.col("official_direction"),
                pl.when(pl.col("prev_close") > 0).then(
                    pl.when(pl.col("close") > pl.col("prev_close")).then(pl.lit("up"))
                    .when(pl.col("close") < pl.col("prev_close")).then(pl.lit("down"))
                    .otherwise(pl.lit("flat"))
                ),
            )
        ).otherwise(pl.lit("uncompared"))
        counts = df.select(direction.alias("direction")).group_by("direction").len()
        by_direction = dict(counts.iter_rows())
        advance_count, decline_count, flat_count = (by_direction.get(k, 0) for k in ("up", "down", "flat"))
        uncompared_count = snapshot_rows - advance_count - decline_count - flat_count
        official_count = df.filter(has_close & pl.col("official_direction").is_in(["up", "down", "flat"])).height

        # Limit counts
        limit_ups = len(df.filter(pl.col("is_limit_up") == True))  # noqa: E712  polars expression
        limit_downs = len(df.filter(pl.col("is_limit_down") == True))  # noqa: E712  polars expression

        return MarketBreadthStats(
            supported_count=snapshot_rows,
            snapshot_row_count=snapshot_rows,
            traded_count=traded_count,
            advance_count=advance_count,
            decline_count=decline_count,
            flat_count=flat_count,
            uncompared_count=uncompared_count,
            official_compared_count=official_count,
            inferred_compared_count=snapshot_rows - uncompared_count - official_count,
            upper_limit_count=limit_ups,
            lower_limit_count=limit_downs,
            turnover=turnover,
        )

    def _official_census(self, day: date) -> pl.DataFrame:
        rows = self.evidence_store.read_range(None, day, day)
        return rows.filter(
            (pl.col("date") == day) & pl.col("observed")
            & (((pl.col("exchange") == "TWSE") & (pl.col("source") == TWSE_SOURCE))
               | ((pl.col("exchange") == "TPEX") & (pl.col("source") == TPEX_SOURCE)))
        )

    def _historical_market(self, day: date):
        # Daily partitions only contain supported instruments. Keep those dated observations
        # as a partial fallback for a missing census; never add today's active list.
        daily = self.daily_store.read_range(None, day, day)
        historical = self._official_census(day)
        truth = PitUniverse(census=self.evidence_store).as_of(day, observed=historical)
        classified = truth.filter(pl.col("instrument_type_status") == "verified")
        supported = classified.filter(pl.col("instrument_type").is_in(["stock", "etf"])).select(
            pl.col("market_symbol").alias("symbol"), "exchange", "instrument_type")
        markets = set(historical["exchange"].to_list())
        unknown = truth.filter(pl.col("instrument_type_status") != "verified")
        # Include only observations already known to be supported from a dated daily partition.
        # If the census exists for a market, it controls membership for that market.
        if not daily.is_empty():
            dated = daily.select("symbol").unique().with_columns(
                pl.col("symbol").str.split(".").list.last().alias("exchange"),
                pl.lit(None, dtype=pl.String).alias("instrument_type"))
            inferred = dated.filter(~pl.col("exchange").is_in(sorted(markets))
                                    | pl.col("symbol").is_in(unknown["market_symbol"].to_list()))
            supported = pl.concat([supported, inferred])
        prices = historical.select(
            (pl.col("raw_code") + "." + pl.col("exchange")).alias("symbol"), "close", "volume", "amount", "official_direction"
        ).join(supported.select("symbol"), on="symbol", how="semi")
        if not daily.is_empty():
            daily = daily.select("symbol", "close", "volume", "amount").join(supported.select("symbol"), on="symbol", how="semi")
            prices = pl.concat([prices, daily.join(prices.select("symbol"), on="symbol", how="anti")], how="diagonal_relaxed")
        complete = markets == {"TWSE", "TPEX"} and unknown.is_empty()
        status = "verified" if complete else "partial" if not supported.is_empty() else "unavailable"
        return supported, prices, status

    def get_snapshot(self, target_date: date | None = None) -> TaiwanMarketIntelligenceSnapshot:
        """Build a deterministic Taiwan Market Intelligence Snapshot for target_date."""
        latest_session = resolve_target_latest_trading_date(self.calendar)
        target = target_date or latest_session
        # The resolved target stays the freshness reference even when the
        # figures below fall back to the latest available day; comparing the
        # fallback day with itself used to report stale data as current.
        expected = target

        # Pre-resolve daily availability so we can fall back gracefully when the
        # resolved target date has no data (e.g. data is stale and not yet updated).
        # Only fall back automatically when the caller did not supply an explicit date.
        daily_available = self.daily_store.available_dates()
        daily_as_of = max((d for d in daily_available if d <= expected), default=None)
        if target_date is None and daily_as_of is not None and daily_as_of < target:
            target = daily_as_of

        prev_d = self.get_previous_trading_date(target)

        historical = target != latest_session
        if historical:
            universe_df, curr_daily, universe_status = self._historical_market(target)
        else:
            universe_df = self.security_master.to_dataframe(supported_only=True)
            if not universe_df.is_empty():
                universe_df = universe_df.filter(
                    (pl.col("listing_status") == "active") & pl.col("instrument_type").is_in(["stock", "etf"]))
            live_symbols = universe_df["symbol"].to_list() if not universe_df.is_empty() else []
            curr_daily = self.daily_store.read_range(live_symbols, target, target)
            if not curr_daily.is_empty():
                directions = self._official_census(target).select(
                    (pl.col("raw_code") + "." + pl.col("exchange")).alias("symbol"), "official_direction")
                curr_daily = curr_daily.join(directions, on="symbol", how="left")
            universe_status = "verified" if live_symbols else "unavailable"
        supported_count = len(universe_df)
        symbols = universe_df["symbol"].to_list() if supported_count else []
        prev_daily = self.daily_store.read_range(symbols, prev_d, prev_d) if prev_d else pl.DataFrame()
        prior_announcements = {}
        if prev_d and not curr_daily.is_empty():
            previous_census = self._official_census(prev_d)
            for row in previous_census.filter(pl.col("exchange") == "TPEX").select(
                "raw_code", "next_upper_limit", "next_lower_limit"
            ).drop_nulls().iter_rows():
                code, upper, lower = row
                # A full order-entry range (such as a foreign bond ETF) cannot
                # establish a daily fluctuation limit.
                valid = 0 < lower < upper and not (lower == 0.01 and upper >= 9995)
                prior_announcements[f"{code}.TPEX"] = (upper, lower) if valid else None
        if historical and prev_d and not curr_daily.is_empty():
            # Delisted securities can survive only in the historical exchange census.
            prior = previous_census.select(
                (pl.col("raw_code") + "." + pl.col("exchange")).alias("symbol"), "close")
            if not prev_daily.is_empty():
                prior = pl.concat([prior, prev_daily.select("symbol", "close").join(prior.select("symbol"), on="symbol", how="anti")], how="vertical_relaxed")
            prev_daily = prior

        daily_as_of = target if not curr_daily.is_empty() else None
        # Check daily status
        daily_status: MarketDatasetStatus = "unavailable"
        if daily_as_of is not None:
            if daily_as_of < expected:
                daily_status = "stale"
            elif daily_as_of in self.daily_store.partial_dates():
                daily_status = "partial"
            else:
                daily_status = "current"

        matched_symbols_count = len(curr_daily)
        missing_count = max(0, supported_count - matched_symbols_count)

        limit_unavailable = 0
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

            events = self.action_store.events_for_price_reference(target)
            action_limits = {e.symbol: announced_price_limits(e) for e in events}
            if action_limits:
                # Old partitions have no exchange direction. An ex-date alone cannot
                # establish an official advance/decline; never manufacture one.
                joined = joined.with_columns(
                    pl.when(pl.col("symbol").is_in(list(action_limits)) & pl.col("official_direction").is_null())
                    .then(pl.lit("uncompared")).otherwise(pl.col("official_direction")).alias("official_direction")
                )

            # Join metadata
            meta_sub = universe_df.select(["symbol", "exchange", "instrument_type"])
            joined = joined.join(meta_sub, on="symbol", how="left")

            # Determine limit-up / limit-down per row
            limit_up_flags = []
            limit_down_flags = []
            confirmed = sorted({d for d in daily_available if d < target}
                               | set(self.calendar.recent_confirmed_sessions(target - timedelta(days=1), 5)))
            fifth_prior_session = confirmed[-5] if len(confirmed) >= 5 else None
            for r in joined.iter_rows(named=True):
                sym = r["symbol"]
                close = r.get("close")
                prev_close = r.get("prev_close")
                if close is None or close <= 0:
                    bounds = None
                elif sym in action_limits:
                    bounds = action_limits[sym]
                elif sym in prior_announcements:
                    bounds = prior_announcements[sym]
                elif prev_close is None or prev_close <= 0 or r["official_direction"] == "uncompared":
                    bounds = None
                elif historical:
                    # A dated instrument type does not establish that session's limits
                    # (e.g. initial trading sessions or historical exchange rule changes).
                    bounds = None
                else:
                    inst = self.security_master.get_instrument(sym)
                    try:
                        if inst and inst.instrument_type == "stock" and (
                            not fifth_prior_session or not inst.listing_date
                            or parse_taiwan_date(inst.listing_date) > fifth_prior_session
                        ):
                            # Initial trading sessions may be exempt; the current registry
                            # alone cannot distinguish an IPO from an exchange transfer.
                            bounds = None
                        else:
                            bounds = MarketProfileBridge.calc_limits(prev_close, inst) if inst else None
                    except ValueError:
                        bounds = None
                if bounds is not None:
                    upper, lower = bounds
                    if (upper is not None and close > upper) or (lower is not None and close < lower):
                        bounds = None
                if bounds is None:
                    limit_unavailable += 1
                    limit_up_flags.append(False)
                    limit_down_flags.append(False)
                    continue
                upper, lower = bounds
                is_up = upper is not None and close == upper
                is_dn = lower is not None and close == lower
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
        inst_available = self.inst_store.available_dates()
        inst_as_of = max((d for d in inst_available if d <= expected), default=None)
        inst_df = (
            self.inst_store.read_range(None, inst_as_of, inst_as_of)
            if inst_as_of
            else pl.DataFrame()
        )
        inst_status: MarketDatasetStatus = "unavailable"

        if inst_as_of and not inst_df.is_empty():
            f_net = _complete_sum(inst_df, "foreign_net")
            it_net = _complete_sum(inst_df, "investment_trust_net")
            d_net = _complete_sum(inst_df, "dealer_net")
            inst_status = _aggregate_status(inst_as_of, expected, f_net, it_net, d_net)
            tot_net = f_net + it_net + d_net if f_net is not None and it_net is not None and d_net is not None else None
            inst_agg = InstitutionalMarketAggregate(
                trade_date=str(inst_as_of),
                row_count=len(inst_df),
                foreign_net=f_net,
                investment_trust_net=it_net,
                dealer_net=d_net,
                total_net=tot_net,
                status=inst_status,
            )
        else:
            inst_agg = InstitutionalMarketAggregate(
                trade_date=str(inst_as_of) if inst_as_of else None,
                row_count=0,
                status=inst_status,
            )

        # 5. Margin Aggregation (NO look-ahead)
        m_available = self.margin_store.available_dates()
        m_as_of = max((d for d in m_available if d <= expected), default=None)
        m_df = (
            self.margin_store.read_range(None, m_as_of, m_as_of)
            if m_as_of
            else pl.DataFrame()
        )
        m_status: MarketDatasetStatus = "unavailable"

        if m_as_of and not m_df.is_empty():
            mb = _complete_sum(m_df, "margin_balance")
            mc = _complete_sum(m_df, "margin_change")
            sb = _complete_sum(m_df, "short_balance")
            sc = _complete_sum(m_df, "short_change")
            m_status = _aggregate_status(m_as_of, expected, mb, mc, sb, sc)
            # Compute aggregate ratio safely: sum(short) / sum(margin) * 100
            ratio = round((sb / mb * 100.0), 2) if mb is not None and mb > 0 and sb is not None else None

            margin_agg = MarginMarketAggregate(
                trade_date=str(m_as_of),
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
                trade_date=str(m_as_of) if m_as_of else None,
                row_count=0,
                status=m_status,
            )

        # 6. Indexes Integration (Pure offline / persisted fallback)
        # Note: In accordance with zero-HTTP acceptance rules, do not fetch network index data.
        indexes_snapshot = persisted_index_snapshot(expected)
        index_dates = [i.trade_date for i in (indexes_snapshot.taiex, indexes_snapshot.tpex_index)
                       if i and i.status != "unavailable" and i.trade_date]

        # 7. Data Quality & Overall Status
        dataset_statuses = [daily_status, inst_status, m_status]
        if all(s == "current" for s in dataset_statuses) and universe_status == "verified":
            overall_status = "complete"
        elif any(s != "unavailable" for s in dataset_statuses):
            overall_status = "partial"
        else:
            overall_status = "unavailable"

        dq = DataQualityReport(
            target_trade_date=str(expected),
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
                as_of=min(index_dates) if index_dates else None,
                status=("current" if index_dates and min(index_dates) >= str(expected)
                        else "stale" if index_dates else "unavailable"),
                source="taiwan_benchmark_store",
            ),
            universe_status=universe_status,
            price_limit_unavailable_count=limit_unavailable,
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
