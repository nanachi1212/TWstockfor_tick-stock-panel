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
import logging
from datetime import date
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.providers.taiwan_values import parse_number
from app.taiwan.realtime.calendar import taipei_now
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
]
SortDir = Literal["asc", "desc"]


class TaiwanScreenerRequest(BaseModel):
    """Strongly typed Taiwan Screener Request Body."""

    exchange: ExchangeFilter = "ALL"
    instrument: InstrumentFilter = "ALL"
    industry: str | None = None  # None or specific industry name

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

    # Institutional (shares)
    foreign_net: float | None = None
    foreign_net_5d: float | None = None
    investment_trust_net: float | None = None
    investment_trust_net_5d: float | None = None
    dealer_net: float | None = None
    institutional_date: str | None = None
    institutional_status: str = "unavailable"

    # Margin (shares & %)
    margin_balance: float | None = None
    margin_balance_change: float | None = None
    short_balance: float | None = None
    short_margin_ratio: float | None = None  # 10.0 = 10%
    margin_date: str | None = None
    margin_status: str = "unavailable"

    # Fundamentals (Valuation, Revenue, Profitability)
    pe: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    revenue_yoy: float | None = None
    revenue_mom: float | None = None
    latest_eps: float | None = None

    # Chips (Foreign shareholding & lending)
    foreign_shareholding_ratio: float | None = None
    foreign_shareholding_change_20d: float | None = None
    securities_lending_anomaly: str | None = None

    # Quant Score
    quant_score: float | None = None

    # Explanation of why the stock was selected
    match_reasons: list[str] = Field(default_factory=list)


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
    ) -> None:
        self.security_master = security_master or get_security_master()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.institutional_store = institutional_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.cache = finmind_cache or FinMindCache()
        self._fundamental_chips_service = fundamental_chips_service

    def _get_fundamental_chips_service(self):
        if self._fundamental_chips_service is None:
            from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService
            self._fundamental_chips_service = TaiwanFundamentalChipsService(cache=self.cache)
        return self._fundamental_chips_service

    def run(self, req: TaiwanScreenerRequest) -> TaiwanScreenerResponse:
        # Step 1: Universe from TaiwanSecurityMaster
        universe_df = self._get_universe(req.exchange, req.instrument)
        if universe_df.is_empty():
            return TaiwanScreenerResponse(
                items=[], total=0, page=req.page, page_size=req.page_size,
                sort_by=req.sort_by, sort_order=req.sort_order,
                data_dates=DataDatesInfo(),
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
            )

        daily_as_of = str(latest_daily["date"].max()) if not latest_daily.is_empty() else None

        # Step 3: Compute batch indicators (needs up to 30 trading days of history)
        df_indicators = self._compute_batch_indicators(valid_symbols)

        # Step 4: Join Universe + Latest Daily + Indicators
        combined = universe_df.join(latest_daily, on="symbol", how="inner")
        if not df_indicators.is_empty():
            combined = combined.join(df_indicators, on="symbol", how="left")
        else:
            combined = self._add_null_cols(combined, ["change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"])

        # Step 5: MarketProfile Price Limits & Distance Calculation
        combined = self._enrich_price_limits(combined)

        # Step 6: Batch Join Institutional & Margin
        combined, inst_date, margin_date, degraded = self._join_institutional_margin(combined, valid_symbols)

        # Step 6.5: Batch Join Cached Fundamentals & Chips
        combined, fund_count, chips_count = self._join_cached_fundamentals_chips(combined, valid_symbols)

        # Step 7: Apply Strongly Typed Filters
        filtered = self._apply_filters(combined, req)

        # Step 8: Total count (before pagination)
        total = filtered.height

        # Step 9: Deterministic Sort (with symbol ASC tie-breaker)
        sorted_df = self._apply_sort(filtered, req.sort_by, req.sort_order)

        # Step 10: Pagination
        offset = (req.page - 1) * req.page_size
        paged_df = sorted_df.slice(offset, req.page_size)

        # Step 11: Serialize items (with match reasons)
        items = self._build_items(paged_df, req)

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
        )

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

    def _compute_batch_indicators(self, symbols: list[str]) -> pl.DataFrame:
        """Compute rolling indicators from past daily store records in a single batch."""
        available_dates = self.daily_store.available_dates()
        if len(available_dates) < 2:
            return pl.DataFrame()

        # Look back up to 35 available partition dates
        start_d = available_dates[max(0, len(available_dates) - 35)]
        end_d = available_dates[-1]

        hist = self._normalize_daily_frame(self.daily_store.read_range(symbols, start_d, end_d))
        if hist.is_empty():
            return pl.DataFrame()

        # Sort symbol ASC, date ASC
        hist = hist.sort(["symbol", "date"])

        # Compute per-symbol metrics using Polars window functions
        hist = hist.with_columns([
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("change_pct"),
            pl.col("close").rolling_mean(5).over("symbol").alias("ma5"),
            pl.col("close").rolling_mean(10).over("symbol").alias("ma10"),
            pl.col("close").rolling_mean(20).over("symbol").alias("ma20"),
            (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0).alias("momentum_5d"),
            (pl.col("volume") / pl.col("volume").rolling_mean(5).over("symbol")).alias("vol_ratio_5d"),
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
        latest_inds = (
            hist.with_columns(pl.col("date").max().over("symbol").alias("_max_d"))
            .filter(pl.col("date") == pl.col("_max_d"))
            .select(["symbol", "change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"])
        )
        return latest_inds

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
        self, df: pl.DataFrame, symbols: list[str]
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

        # 5-day rolling placeholders (or calculated if history present)
        if "foreign_net_5d" not in df.columns:
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
                    pl.col("short_margin_ratio").cast(pl.Float64, strict=False).alias("short_margin_ratio"),
                    pl.col("date").cast(pl.String).alias("margin_date"),
                    pl.col("status").alias("margin_status"),
                ])
                df = df.join(margin_join, on="symbol", how="left")
            else:
                df = self._add_null_cols(df, [
                    "margin_balance", "margin_balance_change", "short_balance", "short_margin_ratio",
                    "margin_date", "margin_status"
                ])
        except Exception as e:
            logger.warning("Batch read margin failed in screener: %s", e)
            degraded.append("margin")
            df = self._add_null_cols(df, [
                "margin_balance", "margin_balance_change", "short_balance", "short_margin_ratio",
                "margin_date", "margin_status"
            ])

        return df, inst_date, margin_date, degraded

    def _join_cached_fundamentals_chips(
        self, df: pl.DataFrame, symbols: list[str]
    ) -> tuple[pl.DataFrame, int, int]:
        """Join cached fundamental metrics, extra chips, and quant scores safely from local store."""
        symbols_set = set(symbols)
        fc_svc = self._get_fundamental_chips_service()

        cached_rev_syms = set(self.cache.list_cached_symbols("TaiwanStockMonthRevenue")) & symbols_set
        cached_fin_syms = set(self.cache.list_cached_symbols("TaiwanStockFinancialStatements")) & symbols_set
        cached_val_syms = set(self.cache.list_cached_symbols("TaiwanValuation")) & symbols_set
        cached_share_syms = set(self.cache.list_cached_symbols("TaiwanStockShareholding")) & symbols_set
        cached_lend_syms = set(self.cache.list_cached_symbols("TaiwanStockSecuritiesLending")) & symbols_set

        fundamental_symbols = cached_rev_syms | cached_fin_syms | cached_val_syms
        chips_symbols = cached_share_syms | cached_lend_syms

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
        eps_map: dict[str, float | None] = {}
        net_inc_map: dict[str, float | None] = {}
        share_ratio_map: dict[str, float | None] = {}
        share_chg20_map: dict[str, float | None] = {}
        lend_anomaly_map: dict[str, str | None] = {}

        for sym in cached_val_syms:
            cached = self.cache.get("TaiwanValuation", sym)
            if cached and cached.get("data"):
                d = cached["data"]
                pe_map[sym] = parse_number(d.get("pe"))
                pb_map[sym] = parse_number(d.get("pb"))
                dy_map[sym] = parse_number(d.get("dividend_yield"))

        for sym in cached_rev_syms:
            cached = self.cache.get("TaiwanStockMonthRevenue", sym)
            if cached and cached.get("data"):
                rev_data = fc_svc._process_month_revenue(
                    cached["data"], cached.get("data_date"), cached.get("fetched_at", "")
                )
                rev_yoy_map[sym] = rev_data.yoy
                rev_mom_map[sym] = rev_data.mom

        for sym in cached_fin_syms:
            cached = self.cache.get("TaiwanStockFinancialStatements", sym)
            if cached and cached.get("data"):
                fin_data = fc_svc._process_financial_statements(
                    cached["data"], cached.get("data_date"), cached.get("fetched_at", "")
                )
                eps_map[sym] = fin_data.latest_eps
                net_inc_map[sym] = fin_data.net_income

        for sym in cached_share_syms:
            cached = self.cache.get("TaiwanStockShareholding", sym)
            if cached and cached.get("data"):
                sh_data = fc_svc._process_shareholding(
                    cached["data"], cached.get("data_date"), cached.get("fetched_at", "")
                )
                share_ratio_map[sym] = sh_data.ratio
                share_chg20_map[sym] = sh_data.change_20d

        for sym in cached_lend_syms:
            cached = self.cache.get("TaiwanStockSecuritiesLending", sym)
            if cached and cached.get("data"):
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
        epss = [eps_map.get(s) for s in df_symbols]
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
            pl.Series("latest_eps", epss, dtype=pl.Float64),
            pl.Series("net_income", net_incs, dtype=pl.Float64),
            pl.Series("foreign_shareholding_ratio", share_ratios, dtype=pl.Float64),
            pl.Series("foreign_shareholding_change_20d", share_chg20s, dtype=pl.Float64),
            pl.Series("securities_lending_anomaly", lend_anomalies, dtype=pl.String),
            pl.Series("quant_score", q_scores, dtype=pl.Float64),
        ])

        return df, len(fundamental_symbols), len(chips_symbols)


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

    def _apply_filters(self, df: pl.DataFrame, req: TaiwanScreenerRequest) -> pl.DataFrame:
        """Apply strongly typed whitelist filters."""
        # Industry
        if req.industry and req.industry != "ALL":
            df = df.filter(pl.col("industry") == req.industry)

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
            df = df.filter(pl.col("close") > pl.col("ma20"))
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
                foreign_net=r.get("foreign_net"),
                foreign_net_5d=r.get("foreign_net_5d"),
                investment_trust_net=r.get("investment_trust_net"),
                investment_trust_net_5d=r.get("investment_trust_net_5d"),
                dealer_net=r.get("dealer_net"),
                institutional_date=r.get("institutional_date"),
                institutional_status=r.get("institutional_status") or "unavailable",
                margin_balance=r.get("margin_balance"),
                margin_balance_change=r.get("margin_balance_change"),
                short_balance=r.get("short_balance"),
                short_margin_ratio=r.get("short_margin_ratio"),
                margin_date=r.get("margin_date"),
                margin_status=r.get("margin_status") or "unavailable",
                pe=r.get("pe"),
                pb=r.get("pb"),
                dividend_yield=r.get("dividend_yield"),
                revenue_yoy=r.get("revenue_yoy"),
                revenue_mom=r.get("revenue_mom"),
                latest_eps=r.get("latest_eps"),
                foreign_shareholding_ratio=r.get("foreign_shareholding_ratio"),
                foreign_shareholding_change_20d=r.get("foreign_shareholding_change_20d"),
                securities_lending_anomaly=r.get("securities_lending_anomaly"),
                quant_score=r.get("quant_score"),
                match_reasons=reasons,
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
