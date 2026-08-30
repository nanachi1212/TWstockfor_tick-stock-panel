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

import logging
from datetime import date, datetime
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.market_rules import PriceLimitModel
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)

ExchangeFilter = Literal["TWSE", "TPEX", "ALL"]
InstrumentFilter = Literal["stock", "etf", "ALL"]
SortField = Literal[
    "symbol", "close", "change_pct", "volume", "amount",
    "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d",
    "foreign_net", "foreign_net_5d", "investment_trust_net",
    "investment_trust_net_5d", "dealer_net",
    "margin_balance_change", "short_balance", "short_margin_ratio",
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


class DataDatesInfo(BaseModel):
    daily_as_of: str | None = None
    institutional_as_of: str | None = None
    margin_as_of: str | None = None


class TaiwanScreenerResponse(BaseModel):
    items: list[ScreenerResultItem]
    total: int
    page: int
    page_size: int
    sort_by: str
    sort_order: str
    data_dates: DataDatesInfo
    degraded_sections: list[str] = []


class TaiwanScreenerService:
    """Production Taiwan Market Screener Service (Batch & Local)."""

    def __init__(
        self,
        security_master: TaiwanSecurityMaster | None = None,
        daily_store: TaiwanDailyStore | None = None,
    ) -> None:
        self.security_master = security_master or get_security_master()
        self.daily_store = daily_store or TaiwanDailyStore()

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
        latest_daily = self.daily_store.read_latest_per_symbol(valid_symbols)
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

        # Step 7: Apply Strongly Typed Filters
        filtered = self._apply_filters(combined, req)

        # Step 8: Total count (before pagination)
        total = filtered.height

        # Step 9: Deterministic Sort (with symbol ASC tie-breaker)
        sorted_df = self._apply_sort(filtered, req.sort_by, req.sort_order)

        # Step 10: Pagination
        offset = (req.page - 1) * req.page_size
        paged_df = sorted_df.slice(offset, req.page_size)

        # Step 11: Serialize items
        items = self._build_items(paged_df)

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

        hist = self.daily_store.read_range(symbols, start_d, end_d)
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

        # RSI 14 computation
        diff = pl.col("close").diff().over("symbol")
        gain = pl.when(diff > 0).then(diff).otherwise(0.0).rolling_mean(14).over("symbol")
        loss = pl.when(diff < 0).then(-diff).otherwise(0.0).rolling_mean(14).over("symbol")
        rs = gain / pl.when(loss == 0).then(0.00001).otherwise(loss)
        rsi = (100.0 - (100.0 / (1.0 + rs))).alias("rsi_14")
        hist = hist.with_columns(rsi)

        # Keep only the latest row per symbol
        latest_inds = (
            hist.with_columns(pl.col("date").max().over("symbol").alias("_max_d"))
            .filter(pl.col("date") == pl.col("_max_d"))
            .select(["symbol", "change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"])
        )
        return latest_inds

    def _enrich_price_limits(self, df: pl.DataFrame) -> pl.DataFrame:
        """Enrich with tick-size aware price limits and distance metrics."""
        rows = []
        for r in df.iter_rows(named=True):
            sym = r["symbol"]
            close = r.get("close")
            inst = self.security_master.get_instrument(sym)

            if inst is None or close is None:
                rows.append({
                    **r,
                    "price_limit_pct": None,
                    "is_no_limit": False,
                    "limit_up": None,
                    "limit_down": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            try:
                limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
            except ValueError as e:
                logger.debug("Unconfirmed regulatory profile for %s: %s", inst.symbol, e)
                # Unconfirmed profile: cannot verify regulatory limit safely.
                # Must set price limit fields to None and NOT match near-limit filters.
                rows.append({
                    **r,
                    "price_limit_pct": None,
                    "is_no_limit": False,
                    "limit_up": None,
                    "limit_down": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            is_no_limit = limit_pct is None

            if is_no_limit or limit_pct is None:
                rows.append({
                    **r,
                    "price_limit_pct": None,
                    "is_no_limit": True,
                    "limit_up": None,
                    "limit_down": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            upper, lower = MarketProfileBridge.calc_limits(close, inst)
            dist_up = (upper - close) / close if (upper and close > 0) else None
            dist_dn = (close - lower) / close if (lower and close > 0) else None

            rows.append({
                **r,
                "price_limit_pct": limit_pct,
                "is_no_limit": False,
                "limit_up": upper,
                "limit_down": lower,
                "distance_to_upper_limit": dist_up,
                "distance_to_lower_limit": dist_dn,
            })

        return pl.DataFrame(rows)

    def _join_institutional_margin(
        self, df: pl.DataFrame, symbols: list[str]
    ) -> tuple[pl.DataFrame, str | None, str | None, list[str]]:
        """Join institutional and margin metadata safely."""
        degraded = []
        inst_date = None
        margin_date = None

        # Add null placeholders for institutional and margin fields
        cols = [
            "foreign_net", "foreign_net_5d", "investment_trust_net", "investment_trust_net_5d",
            "dealer_net", "institutional_date", "institutional_status",
            "margin_balance", "margin_balance_change", "short_balance", "short_margin_ratio",
            "margin_date", "margin_status",
        ]
        df = self._add_null_cols(df, cols)
        return df, inst_date, margin_date, degraded

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

    def _build_items(self, df: pl.DataFrame) -> list[ScreenerResultItem]:
        items = []
        for r in df.iter_rows(named=True):
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
            ))
        return items

    def _add_null_cols(self, df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
        for c in cols:
            if c not in df.columns:
                df = df.with_columns(pl.lit(None).alias(c))
        return df
