"""Taiwan Market Screener Service (Phase 6B).

POST /api/taiwan/screener/run

- Universe from TaiwanSecurityMaster (canonical symbol, is_supported=True, stock/etf only)
- Batch/vectorized enriched parquet path (no N symbols -> N HTTP calls)
- All filters are strongly typed; no SQL/Polars expression passthrough
- missing data = None / unavailable (never 0)
- Deterministic sort with canonical symbol tie-breaker
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Literal

import polars as pl

from app.taiwan.enrichment.factors import compute_chip_factors, compute_margin_factors
from app.taiwan.enrichment.models import (
    DatasetType,
    InstitutionalFlow,
    MarginTrading,
    StalePolicy,
)
from app.taiwan.market_rules import MarketProfileBridge, PriceLimitModel
from app.taiwan.symbol import Exchange, InstrumentType, TaiwanSymbol, parse_symbol
from app.taiwan.universe.models import TaiwanInstrument
from app.taiwan.universe.service import TaiwanSecurityMaster, get_security_master

logger = logging.getLogger(__name__)

# ============================================================
# Typed filter models (no SQL WHERE strings)
# ============================================================

ExchangeFilter = Literal["TWSE", "TPEX", "ALL"]
InstrumentFilter = Literal["stock", "etf", "ALL"]
IndustryFilter = Literal[
    "半導體", "電子", "金融", "生技", "鋼鐵", "塑膠", "紡織", "運輸",
    "食品", "營建", "水泥", "汽車", "電機", "電纜", "化學", "百貨",
    "觀光", "通信", "網通", "其他", "ALL",
]
SortField = Literal[
    "symbol", "price", "change_pct", "volume", "amount",
    "foreign_net", "foreign_net_5d", "investment_trust_net",
    "investment_trust_net_5d", "dealer_net", "margin_balance_change",
    "short_margin_ratio", "turnover_rate",
]
SortDir = Literal["asc", "desc"]


class PriceRangeFilter:
    __slots__ = ("min", "max")

    def __init__(self, min: float | None = None, max: float | None = None) -> None:
        self.min = min
        self.max = max

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.min is not None:
            df = df.filter(pl.col("price") >= self.min)
        if self.max is not None:
            df = df.filter(pl.col("price") <= self.max)
        return df


class ChangePctFilter:
    """change_pct is decimal: 0.05 = 5%."""

    __slots__ = ("min", "max")

    def __init__(self, min: float | None = None, max: float | None = None) -> None:
        self.min = min
        self.max = max

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.min is not None:
            df = df.filter(pl.col("change_pct") >= self.min)
        if self.max is not None:
            df = df.filter(pl.col("change_pct") <= self.max)
        return df


class VolumeFilter:
    """Volume in shares (canonical)."""

    __slots__ = ("min", "max")

    def __init__(self, min: float | None = None, max: float | None = None) -> None:
        self.min = min
        self.max = max

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.min is not None:
            df = df.filter(pl.col("volume") >= self.min)
        if self.max is not None:
            df = df.filter(pl.col("volume") <= self.max)
        return df


class AmountFilter:
    """Amount in TWD (canonical)."""

    __slots__ = ("min", "max")

    def __init__(self, min: float | None = None, max: float | None = None) -> None:
        self.min = min
        self.max = max

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.min is not None:
            df = df.filter(pl.col("amount") >= self.min)
        if self.max is not None:
            df = df.filter(pl.col("amount") <= self.max)
        return df


class InstitutionalFilter:
    """Institutional net filters (in shares). values are decimal of net shares."""

    __slots__ = (
        "foreign_net_min", "foreign_net_max",
        "foreign_net_5d_min", "foreign_net_5d_max",
        "investment_trust_net_min", "investment_trust_net_max",
        "investment_trust_net_5d_min", "investment_trust_net_5d_max",
        "dealer_net_min", "dealer_net_max",
    )

    def __init__(
        self,
        foreign_net_min: float | None = None, foreign_net_max: float | None = None,
        foreign_net_5d_min: float | None = None, foreign_net_5d_max: float | None = None,
        investment_trust_net_min: float | None = None, investment_trust_net_max: float | None = None,
        investment_trust_net_5d_min: float | None = None, investment_trust_net_5d_max: float | None = None,
        dealer_net_min: float | None = None, dealer_net_max: float | None = None,
    ) -> None:
        self.foreign_net_min = foreign_net_min
        self.foreign_net_max = foreign_net_max
        self.foreign_net_5d_min = foreign_net_5d_min
        self.foreign_net_5d_max = foreign_net_5d_max
        self.investment_trust_net_min = investment_trust_net_min
        self.investment_trust_net_max = investment_trust_net_max
        self.investment_trust_net_5d_min = investment_trust_net_5d_min
        self.investment_trust_net_5d_max = investment_trust_net_5d_max
        self.dealer_net_min = dealer_net_min
        self.dealer_net_max = dealer_net_max

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.foreign_net_min is not None:
            df = df.filter(pl.col("foreign_net") >= self.foreign_net_min)
        if self.foreign_net_max is not None:
            df = df.filter(pl.col("foreign_net") <= self.foreign_net_max)
        if self.foreign_net_5d_min is not None:
            df = df.filter(pl.col("foreign_net_5d") >= self.foreign_net_5d_min)
        if self.foreign_net_5d_max is not None:
            df = df.filter(pl.col("foreign_net_5d") <= self.foreign_net_5d_max)
        if self.investment_trust_net_min is not None:
            df = df.filter(pl.col("investment_trust_net") >= self.investment_trust_net_min)
        if self.investment_trust_net_max is not None:
            df = df.filter(pl.col("investment_trust_net") <= self.investment_trust_net_max)
        if self.investment_trust_net_5d_min is not None:
            df = df.filter(pl.col("investment_trust_net_5d") >= self.investment_trust_net_5d_min)
        if self.investment_trust_net_5d_max is not None:
            df = df.filter(pl.col("investment_trust_net_5d") <= self.investment_trust_net_5d_max)
        if self.dealer_net_min is not None:
            df = df.filter(pl.col("dealer_net") >= self.dealer_net_min)
        if self.dealer_net_max is not None:
            df = df.filter(pl.col("dealer_net") <= self.dealer_net_max)
        return df


class MarginFilter:
    __slots__ = (
        "margin_balance_change_min", "margin_balance_change_max",
        "short_margin_ratio_min", "short_margin_ratio_max",
        "short_balance_min", "short_balance_max",
    )

    def __init__(
        self,
        margin_balance_change_min: float | None = None, margin_balance_change_max: float | None = None,
        short_margin_ratio_min: float | None = None, short_margin_ratio_max: float | None = None,
        short_balance_min: float | None = None, short_balance_max: float | None = None,
    ) -> None:
        self.margin_balance_change_min = margin_balance_change_min
        self.margin_balance_change_max = margin_balance_change_max
        self.short_margin_ratio_min = short_margin_ratio_min
        self.short_margin_ratio_max = short_margin_ratio_max
        self.short_balance_min = short_balance_min
        self.short_balance_max = short_balance_max

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.margin_balance_change_min is not None:
            df = df.filter(pl.col("margin_balance_change") >= self.margin_balance_change_min)
        if self.margin_balance_change_max is not None:
            df = df.filter(pl.col("margin_balance_change") <= self.margin_balance_change_max)
        if self.short_margin_ratio_min is not None:
            df = df.filter(pl.col("short_margin_ratio") >= self.short_margin_ratio_min)
        if self.short_margin_ratio_max is not None:
            df = df.filter(pl.col("short_margin_ratio") <= self.short_margin_ratio_max)
        if self.short_balance_min is not None:
            df = df.filter(pl.col("short_balance") >= self.short_balance_min)
        if self.short_balance_max is not None:
            df = df.filter(pl.col("short_balance") <= self.short_balance_max)
        return df


class IndustryFilter:
    __slots__ = ("value",)

    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.value and self.value != "ALL":
            df = df.filter(pl.col("industry") == self.value)
        return df


class PriceLimitFilter:
    """Near price limit filters using distance_to_upper/lower (not Monitor Rule semantics)."""

    __slots__ = ("near_upper_limit", "near_lower_limit", "distance_to_upper_limit_max", "distance_to_lower_limit_max")

    def __init__(
        self,
        near_upper_limit: bool | None = None,
        near_lower_limit: bool | None = None,
        distance_to_upper_limit_max: float | None = None,
        distance_to_lower_limit_max: float | None = None,
    ) -> None:
        self.near_upper_limit = near_upper_limit
        self.near_lower_limit = near_lower_limit
        self.distance_to_upper_limit_max = distance_to_upper_limit_max
        self.distance_to_lower_limit_max = distance_to_lower_limit_max

    def filter(self, df: pl.DataFrame) -> pl.DataFrame:
        # near_upper_limit / near_lower_limit are handled via distance thresholds.
        # For NO_LIMIT products, these fields are null (not_applicable) and won't match.
        if self.distance_to_upper_limit_max is not None:
            df = df.filter(pl.col("distance_to_upper_limit") <= self.distance_to_upper_limit_max)
        if self.distance_to_lower_limit_max is not None:
            df = df.filter(pl.col("distance_to_lower_limit") <= self.distance_to_lower_limit_max)
        return df


# ============================================================
# Request / Response schemas
# ============================================================

class TaiwanScreenerRequest:
    """Strongly typed Taiwan screener request body (POST /api/taiwan/screener/run)."""

    def __init__(
        self,
        *,
        exchange: ExchangeFilter = "ALL",
        instrument: InstrumentFilter = "ALL",
        industry: IndustryFilter | None = None,
        price: PriceRangeFilter | None = None,
        change_pct: ChangePctFilter | None = None,
        volume: VolumeFilter | None = None,
        amount: AmountFilter | None = None,
        foreign_net: InstitutionalFilter | None = None,
        investment_trust_net: InstitutionalFilter | None = None,
        dealer_net: InstitutionalFilter | None = None,
        margin: MarginFilter | None = None,
        price_limit: PriceLimitFilter | None = None,
        page: int = 1,
        page_size: int = 50,
        sort_field: SortField = "symbol",
        sort_dir: SortDir = "asc",
    ) -> None:
        self.exchange = exchange
        self.instrument = instrument
        self.industry = industry or IndustryFilter()
        self.price = price or PriceRangeFilter()
        self.change_pct = change_pct or ChangePctFilter()
        self.volume = volume or VolumeFilter()
        self.amount = amount or AmountFilter()
        self.foreign_net = foreign_net or InstitutionalFilter()
        self.investment_trust_net = investment_trust_net or InstitutionalFilter()
        self.dealer_net = dealer_net or InstitutionalFilter()
        self.margin = margin or MarginFilter()
        self.price_limit = price_limit or PriceLimitFilter()
        self.page = max(1, page)
        self.page_size = max(1, min(200, page_size))
        self.sort_field = sort_field
        self.sort_dir = sort_dir


class ScreenerResultRow:
    """A single row in screener results."""

    __slots__ = (
        "symbol", "name", "exchange", "instrument_type", "industry",
        "price", "change_pct", "volume", "amount",
        "foreign_net", "foreign_net_5d", "investment_trust_net", "investment_trust_net_5d", "dealer_net",
        "margin_balance_change", "short_balance", "short_margin_ratio",
        "price_limit_pct", "is_no_limit", "distance_to_upper_limit", "distance_to_lower_limit",
        "quote_date", "institutional_date", "margin_date",
        "institutional_status", "margin_status",
    )

    def __init__(self, **kwargs: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k))

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class DataDates:
    __slots__ = ("quote_date", "institutional_date", "margin_date")

    def __init__(self, quote_date: date | None = None, institutional_date: date | None = None, margin_date: date | None = None) -> None:
        self.quote_date = quote_date
        self.institutional_date = institutional_date
        self.margin_date = margin_date

    def to_dict(self) -> dict[str, str | None]:
        return {
            "quote_date": self.quote_date.isoformat() if self.quote_date else None,
            "institutional_date": self.institutional_date.isoformat() if self.institutional_date else None,
            "margin_date": self.margin_date.isoformat() if self.margin_date else None,
        }


class TaiwanScreenerResponse:
    """Strongly typed screener response."""

    def __init__(
        self,
        *,
        results: list[ScreenerResultRow] = [],
        total: int = 0,
        page: int = 1,
        page_size: int = 50,
        sort_field: str = "symbol",
        sort_dir: str = "asc",
        data_dates: DataDates | None = None,
        degraded_sections: list[str] | None = None,
    ) -> None:
        self.results = results
        self.total = total
        self.page = page
        self.page_size = page_size
        self.sort_field = sort_field
        self.sort_dir = sort_dir
        self.data_dates = data_dates or DataDates()
        self.degraded_sections = degraded_sections or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "sort_field": self.sort_field,
            "sort_dir": self.sort_dir,
            "data_dates": self.data_dates.to_dict(),
            "degraded_sections": self.degraded_sections,
        }


# ============================================================
# Service
# ============================================================

_SORT_FIELD_MAP: dict[str, pl.Expr] = {
    "symbol": pl.col("symbol"),
    "price": pl.col("price"),
    "change_pct": pl.col("change_pct"),
    "volume": pl.col("volume"),
    "amount": pl.col("amount"),
    "foreign_net": pl.col("foreign_net"),
    "foreign_net_5d": pl.col("foreign_net_5d"),
    "investment_trust_net": pl.col("investment_trust_net"),
    "investment_trust_net_5d": pl.col("investment_trust_net_5d"),
    "dealer_net": pl.col("dealer_net"),
    "margin_balance_change": pl.col("margin_balance_change"),
    "short_margin_ratio": pl.col("short_margin_ratio"),
    "turnover_rate": pl.col("turnover_rate"),
}


class TaiwanScreenerService:
    """Taiwan market screener backed by TaiwanSecurityMaster + enriched parquet batch path."""

    def __init__(self, security_master: TaiwanSecurityMaster | None = None) -> None:
        self.security_master = security_master or get_security_master()

    def run(self, request: TaiwanScreenerRequest) -> TaiwanScreenerResponse:
        # Step 1: universe from Security Master (requirement #3)
        instruments = self._get_universe(request.exchange, request.instrument)
        if instruments.is_empty():
            return TaiwanScreenerResponse(
                results=[], total=0, page=request.page, page_size=request.page_size,
                sort_field=request.sort_field, sort_dir=request.sort_dir,
                data_dates=DataDates(), degraded_sections=[],
            )

        canonical_symbols = instruments.select("symbol").to_series().to_list()

        # Step 2: load enriched data batch
        enriched, quote_date = self._load_enriched_batch(canonical_symbols)
        degraded: list[str] = []

        # Step 3: join with security master identity info
        base = self._join_identity(enriched, instruments)

        # Step 4: compute derived fields (price limits, distance, etc.)
        base = self._enrich_with_market_profile(base)

        # Step 5: join institutional & margin batch data
        base, inst_degraded, margin_degraded = self._join_institutional_margin(base, canonical_symbols)
        degraded.extend(inst_degraded)
        degraded.extend(margin_degraded)

        # Step 6: apply filters
        df = base
        request.price.filter(df)  # noqa: PLW3201 — kept for clarity; replaced below
        # Apply each filter
        df = request.price.filter(df)
        df = request.change_pct.filter(df)
        df = request.volume.filter(df)
        df = request.amount.filter(df)
        df = request.industry.filter(df)
        df = request.foreign_net.filter(df)
        df = request.investment_trust_net.filter(df)
        df = request.dealer_net.filter(df)
        df = request.margin.filter(df)
        df = request.price_limit.filter(df)

        # Step 7: deterministic sort
        sort_expr = _SORT_FIELD_MAP.get(request.sort_field, pl.col("symbol"))
        if request.sort_dir == "desc":
            sort_expr = sort_expr.desc()
        else:
            sort_expr = sort_expr.asc()
        df = df.sort([sort_expr, pl.col("symbol").asc()])

        # Step 8: total count (before pagination)
        total = df.height

        # Step 9: pagination
        offset = (request.page - 1) * request.page_size
        df_page = df.slice(offset, request.page_size)

        # Step 10: build rows
        results = self._build_rows(df_page, request)

        data_dates = DataDates(
            quote_date=quote_date,
            institutional_date=self._latest_institutional_date(),
            margin_date=self._latest_margin_date(),
        )

        return TaiwanScreenerResponse(
            results=results, total=total, page=request.page, page_size=request.page_size,
            sort_field=request.sort_field, sort_dir=request.sort_dir,
            data_dates=data_dates, degraded_sections=degraded,
        )

    def _get_universe(self, exchange: ExchangeFilter, instrument: InstrumentFilter) -> pl.DataFrame:
        """Get universe from TaiwanSecurityMaster (canonical symbols only)."""
        if exchange == "ALL" and instrument == "ALL":
            u = self.security_master.get_universe("TAIWAN_ALL")
        elif exchange == "ALL":
            u = self.security_master.get_universe("TAIWAN_STOCKS" if instrument == "stock" else "TAIWAN_ETFS")
        elif instrument == "ALL":
            u = self.security_master.get_universe("TWSE_ALL" if exchange == "TWSE" else "TPEX_ALL")
        else:
            u = self.security_master.get_universe(
                "TWSE_ALL" if exchange == "TWSE" else "TPEX_ALL",
            )
            # Further filter by instrument type
            if instrument == "stock":
                u = u.filter(pl.col("instrument_type") == "stock")
            elif instrument == "etf":
                u = u.filter(pl.col("instrument_type") == "etf")

        # Exclude unsupported / warrants / ETN
        u = u.filter(pl.col("is_supported") == True)
        u = u.filter(~pl.col("instrument_type").is_in(["warrant", "etn"]))
        # Only stock/etf with canonical symbol
        u = u.filter(pl.col("instrument_type").is_in(["stock", "etf"]))
        u = u.filter(pl.col("exchange").is_in(["TWSE", "TPEX"]))

        # Keep canonical symbol as join key
        return u.select(["symbol", "name", "exchange", "instrument_type", "industry"])

    def _load_enriched_batch(self, symbols: list[str]) -> tuple[pl.DataFrame, date | None]:
        """Load enriched daily data for all symbols in one batch."""
        try:
            repo = self._get_repo()
            df, latest_date = repo.get_enriched_latest()
            if df.is_empty():
                logger.info("Screener: enriched latest is empty")
                return pl.DataFrame(), None
            # Filter to universe symbols
            df = df.filter(pl.col("symbol").is_in(symbols))
            return df, latest_date
        except Exception as e:
            logger.warning("Screener: failed to load enriched batch: %s", e)
            return pl.DataFrame(), None

    def _join_identity(self, enriched: pl.DataFrame, instruments: pl.DataFrame) -> pl.DataFrame:
        """Join enriched data with security master identity columns."""
        if enriched.is_empty():
            return enriched
        inst_cols = [c for c in ["name", "industry"] if c in instruments.columns]
        if not inst_cols:
            return enriched
        joined = enriched.join(
            instruments.select(["symbol"] + inst_cols).unique(subset=["symbol"]),
            on="symbol", how="left",
        )
        return joined

    def _enrich_with_market_profile(self, df: pl.DataFrame) -> pl.DataFrame:
        """Add price limit fields using MarketProfileBridge / PriceLimitModel."""
        if df.is_empty():
            return df

        rows = []
        for row in df.iter_rows(named=True):
            symbol = row.get("symbol")
            try:
                parsed = parse_symbol(symbol) if isinstance(symbol, str) else symbol
                master_item = self.security_master.get_instrument(symbol)
            except Exception:
                rows.append({
                    **row,
                    "price_limit_pct": None,
                    "is_no_limit": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            if master_item is None:
                rows.append({
                    **row,
                    "price_limit_pct": None,
                    "is_no_limit": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            limit_pct = MarketProfileBridge.get_price_limit_pct(master_item)
            is_no_limit = limit_pct is None

            close = row.get("close") or row.get("last_price")
            prev_close = close  # fallback

            if close is not None and not is_no_limit and limit_pct is not None:
                upper, lower = PriceLimitModel.calc_limits_for_pct(close, limit_pct, MarketProfileBridge.get_tick_size_class(master_item))
                dist_upper = (upper - close) / close if upper and close else None
                dist_lower = (close - lower) / close if lower and close else None
            else:
                upper = None
                lower = None
                dist_upper = None
                dist_lower = None

            rows.append({
                **row,
                "price_limit_pct": limit_pct,
                "is_no_limit": is_no_limit,
                "limit_up": upper,
                "limit_down": lower,
                "distance_to_upper_limit": dist_upper,
                "distance_to_lower_limit": dist_lower,
            })

        return pl.DataFrame(rows)

    def _join_institutional_margin(
        self, base: pl.DataFrame, symbols: list[str]
    ) -> tuple[pl.DataFrame, list[str], list[str]]:
        """Join institutional + margin batch data. Returns (df, inst_degraded, margin_degraded)."""
        degraded: list[str] = []
        inst_degraded: list[str] = []
        margin_degraded: list[str] = []

        if base.is_empty():
            return base, inst_degraded, margin_degraded

        # Institutional: use TaiwanInstitutionalProvider batch
        try:
            from app.taiwan.enrichment.institutional import TaiwanInstitutionalProvider
            inst_provider = TaiwanInstitutionalProvider()
            inst_df = inst_provider.fetch_all_batch(symbols)
            if inst_df is not None and not inst_df.is_empty():
                # Compute 5-day rolling factors
                inst_df = compute_chip_factors(inst_df)
                # Aggregate to latest per symbol
                inst_latest = inst_df.sort(["symbol", "trade_date"]).group_by("symbol").agg([
                    pl.col("foreign_net").last().alias("foreign_net"),
                    pl.col("investment_trust_net").last().alias("investment_trust_net"),
                    pl.col("dealer_net").last().alias("dealer_net"),
                    pl.col("foreign_net_5d").last().alias("foreign_net_5d"),
                    pl.col("investment_trust_net_5d").last().alias("investment_trust_net_5d"),
                    pl.col("trade_date").last().alias("institutional_date"),
                ])
                base = base.join(inst_latest, on="symbol", how="left")
            else:
                inst_degraded.append("institutional")
                base = self._add_null_columns(base, ["foreign_net", "investment_trust_net", "dealer_net",
                                                     "foreign_net_5d", "investment_trust_net_5d", "institutional_date"])
        except Exception as e:
            logger.warning("Screener institutional join failed: %s", e)
            inst_degraded.append("institutional")
            base = self._add_null_columns(base, ["foreign_net", "investment_trust_net", "dealer_net",
                                                 "foreign_net_5d", "investment_trust_net_5d", "institutional_date"])

        # Margin: use TaiwanMarginProvider batch
        try:
            from app.taiwan.enrichment.margin import TaiwanMarginProvider
            margin_provider = TaiwanMarginProvider()
            margin_df = margin_provider.fetch_all_batch(symbols)
            if margin_df is not None and not margin_df.is_empty():
                margin_df = compute_margin_factors(margin_df)
                margin_latest = margin_df.sort(["symbol", "trade_date"]).group_by("symbol").agg([
                    pl.col("margin_balance").last().alias("margin_balance"),
                    pl.col("margin_balance_change").last().alias("margin_balance_change"),
                    pl.col("short_balance").last().alias("short_balance"),
                    pl.col("short_margin_ratio").last().alias("short_margin_ratio"),
                    pl.col("trade_date").last().alias("margin_date"),
                ])
                base = base.join(margin_latest, on="symbol", how="left")
            else:
                margin_degraded.append("margin")
                base = self._add_null_columns(base, ["margin_balance", "margin_balance_change",
                                                     "short_balance", "short_margin_ratio", "margin_date"])
        except Exception as e:
            logger.warning("Screener margin join failed: %s", e)
            margin_degraded.append("margin")
            base = self._add_null_columns(base, ["margin_balance", "margin_balance_change",
                                                 "short_balance", "short_margin_ratio", "margin_date"])

        return base, inst_degraded, margin_degraded

    def _add_null_columns(self, df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
        for c in cols:
            if c not in df.columns:
                df = df.with_columns(pl.lit(None).alias(c))
        return df

    def _latest_institutional_date(self) -> date | None:
        try:
            from app.taiwan.enrichment.institutional import TaiwanInstitutionalProvider
            return TaiwanInstitutionalProvider().latest_trade_date()
        except Exception:
            return None

    def _latest_margin_date(self) -> date | None:
        try:
            from app.taiwan.enrichment.margin import TaiwanMarginProvider
            return TaiwanMarginProvider().latest_trade_date()
        except Exception:
            return None

    def _build_rows(self, df: pl.DataFrame, request: TaiwanScreenerRequest) -> list[ScreenerResultRow]:
        rows = []
        for row in df.iter_rows(named=True):
            rows.append(ScreenerResultRow(
                symbol=row.get("symbol"),
                name=row.get("name"),
                exchange=row.get("exchange"),
                instrument_type=row.get("instrument_type"),
                industry=row.get("industry"),
                price=row.get("close") or row.get("last_price"),
                change_pct=row.get("change_pct"),
                volume=row.get("volume"),
                amount=row.get("amount"),
                foreign_net=row.get("foreign_net"),
                foreign_net_5d=row.get("foreign_net_5d"),
                investment_trust_net=row.get("investment_trust_net"),
                investment_trust_net_5d=row.get("investment_trust_net_5d"),
                dealer_net=row.get("dealer_net"),
                margin_balance_change=row.get("margin_balance_change"),
                short_balance=row.get("short_balance"),
                short_margin_ratio=row.get("short_margin_ratio"),
                price_limit_pct=row.get("price_limit_pct"),
                is_no_limit=row.get("is_no_limit"),
                distance_to_upper_limit=row.get("distance_to_upper_limit"),
                distance_to_lower_limit=row.get("distance_to_lower_limit"),
                quote_date=row.get("date") if isinstance(row.get("date"), date) else None,
                institutional_date=row.get("institutional_date") if isinstance(row.get("institutional_date"), date) else None,
                margin_date=row.get("margin_date") if isinstance(row.get("margin_date"), date) else None,
                institutional_status="available" if row.get("foreign_net") is not None else "unavailable",
                margin_status="available" if row.get("short_balance") is not None else "unavailable",
            ))
        return rows

    def _get_repo(self):
        from app.tickflow.repository import KlineRepository, DataStore
        from app.config import settings
        from app.tickflow import KlineRepository as _KR
        # Try to get the shared repo from app state, fallback to creating one
        # (in production, app.state.repo is set in main.py lifespan)
        try:
            from app.main import app
            if hasattr(app.state, "repo"):
                return app.state.repo
        except Exception:
            pass
        store = DataStore(settings.data_dir)
        return KlineRepository(store)