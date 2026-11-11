"""Taiwan price-limit rules (percentage and limit-price arithmetic)."""

from __future__ import annotations

import math
from typing import Any

import polars as pl


def taiwan_price_limit_pct(
    symbol: str,
    *,
    limit_class: Any | None = None,
) -> float | None:
    """Delegate Taiwan price limit percentages strictly to Taiwan PriceLimitModel.

    PriceLimitModel is the single authoritative source of truth.
    Does not let symbol suffix alone dictate 10%.
    """
    from app.taiwan.market_rules import PriceLimitClass, PriceLimitModel

    if limit_class is not None:
        if isinstance(limit_class, str):
            limit_class = PriceLimitClass(limit_class)
        return PriceLimitModel.get_limit_pct(limit_class)

    return PriceLimitModel.get_limit_pct(PriceLimitClass.ORDINARY_TEN_PERCENT)


def price_limit_pct(
    symbol: str,
    *,
    limit_class: Any | None = None,
) -> float | None:
    """Taiwan limit percentage; any non-Taiwan symbol has no known limit (None)."""
    if symbol.endswith((".TWSE", ".TPEX")):
        return taiwan_price_limit_pct(symbol, limit_class=limit_class)
    return None


def taiwan_limit_profiles(instruments: pl.DataFrame) -> pl.DataFrame:
    """Resolve each declared instrument once through the authoritative Taiwan bridge.

    This is a metadata adapter, not a ticker classifier. No master/network lookup
    occurs in the compute path; absent or unconfirmed classifications stay null.
    """
    from app.taiwan.symbol import parse_symbol
    from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument

    schema = {"symbol": pl.String, "_tw_limit_pct": pl.Float64, "_tw_no_limit": pl.Boolean,
              "_tw_tick_class": pl.String}
    profiles = []
    if "symbol" not in instruments.columns:
        return pl.DataFrame(schema=schema)
    for row in instruments.unique(subset=["symbol"], keep="last").iter_rows(named=True):
        symbol = str(row.get("symbol") or "")
        if not symbol.endswith((".TWSE", ".TPEX")):
            continue
        profile = {"symbol": symbol, "_tw_limit_pct": None, "_tw_no_limit": None, "_tw_tick_class": None}
        try:
            parsed = parse_symbol(symbol)
            kind = row.get("instrument_type") or row.get("asset_type")
            if (kind not in {"stock", "etf"} or row.get("is_supported") is False
                    or row.get("exchange") not in (None, "", parsed.exchange.value)):
                profiles.append(profile)
                continue
            raw_multiplier = row.get("leverage_multiplier")
            multiplier = float(raw_multiplier) if raw_multiplier is not None else 1.0
            if not math.isfinite(multiplier) or multiplier == 0:
                raise ValueError("Invalid declared leverage multiplier")
            instrument = TaiwanInstrument(
                symbol=symbol, code=parsed.code, exchange=parsed.exchange.value,
                name=str(row.get("name") or ""), instrument_type=kind,
                listing_status=str(row.get("listing_status") or row.get("status") or "active"),
                listing_date=row.get("listing_date") or row.get("list_date"),
                isin=row.get("isin"), industry=row.get("industry"), cfi_code=row.get("cfi_code"),
                raw_category=str(row.get("raw_category") or ""), is_supported=True,
                source=str(row.get("source") or ""), updated_at=str(row.get("updated_at") or ""),
                etf_category=row.get("etf_category"), classification_source=row.get("classification_source"),
                underlying_scope=row.get("underlying_scope"),
                leverage_multiplier=multiplier,
            )
            pct = MarketProfileBridge.get_price_limit_pct(instrument)
            profile.update(_tw_limit_pct=pct, _tw_no_limit=pct is None,
                           _tw_tick_class=MarketProfileBridge.get_tick_size_class(instrument).value)
        except (TypeError, ValueError):
            pass
        profiles.append(profile)
    return pl.DataFrame(profiles, schema=schema)


def polars_price_limit_pct(
    symbol: pl.Expr,
    *,
    taiwan_limit_pct: pl.Expr | None = None,
) -> pl.Expr:
    """Return a vectorized Polars expression for the daily limit (null for non-Taiwan symbols)."""
    is_taiwan = (symbol.str.ends_with(".TWSE") | symbol.str.ends_with(".TPEX")).fill_null(False)
    return (
        pl.when(is_taiwan).then(taiwan_limit_pct if taiwan_limit_pct is not None else pl.lit(None))
        .otherwise(None)
        .cast(pl.Float64)
    )


def polars_limit_price(
    previous: pl.Expr,
    limit_pct: pl.Expr,
    *,
    up: bool,
    tick_class: pl.Expr | None = None,
) -> pl.Expr:
    """Use Taiwan inward ticks when classified, retaining legacy cent arithmetic otherwise."""
    sign = 1 if up else -1
    numerator = ((1 + sign * limit_pct) * 100).round(0).cast(pl.Int64)
    cents = (previous * 100 + 0.5).floor().cast(pl.Int64)
    legacy = ((cents * numerator + 50) // 100) / 100
    if tick_class is None:
        return legacy

    from app.taiwan.market_rules import TickSizeClass, TickSizeModel

    raw = previous * (1 + sign * limit_pct)
    if not up:
        raw = pl.max_horizontal(raw, pl.lit(0.01))

    def tick_for(tiers: tuple[tuple[float, float], ...]) -> pl.Expr:
        tick = pl.lit(tiers[-1][1])
        for upper_bound, size in reversed(tiers[:-1]):
            tick = pl.when(raw < upper_bound).then(size).otherwise(tick)
        return tick

    # Same tier table and inward rounding as TickSizeModel, entirely native Polars.
    tick = pl.when(tick_class == TickSizeClass.ETF.value).then(
        tick_for(TickSizeModel.ETF_TICK_TIERS)
    ).otherwise(tick_for(TickSizeModel.STOCK_TICK_TIERS))
    steps = (raw / tick + 1e-9).floor() if up else (raw / tick - 1e-9).ceil()
    taiwan = pl.when((previous > 0) & limit_pct.is_not_null()).then((steps * tick).round(4)).otherwise(None)
    return pl.when(tick_class.is_null()).then(legacy).when(
        tick_class.is_in([TickSizeClass.ORDINARY_STOCK.value, TickSizeClass.ETF.value])
    ).then(taiwan).otherwise(None)
