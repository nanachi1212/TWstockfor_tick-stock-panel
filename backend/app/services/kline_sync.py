"""日 K 同步服務(§7.7 Step 1)。

調度器在 capability 允許下,把符號集合的日 K 批次同步到本地 Parquet。
策略:
  - 日 K 僅使用 `kline.daily.batch`
  - 除權因子僅使用 `adj_factor`
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time

import polars as pl

from app.data_providers.base import AssetType
from app.services import preferences
from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now

logger = logging.getLogger(__name__)


def read_taiwan_daily_history(
    symbols: list[str],
    *,
    days: int,
    end: date,
    start: date | None = None,
    include_indicators: bool = False,
) -> dict:
    """Read one bounded batch from the authoritative raw store; never fetch or copy it.

    ``days`` counts stored trading sessions. Explicit date ranges are inclusive.
    Indicator warm-up uses preceding sessions and is removed before presentation.
    There is no extra cache, so completed daily-store writes are visible immediately.
    """
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.observed_universe import ObservedUniverseStore
    from app.taiwan.quant.live_contract import latest_completed_session
    from app.taiwan.realtime.calendar import TaiwanTradingCalendar
    from app.taiwan.technical_indicators import compute_taiwan_indicator_panel

    if start is not None and start > end:
        raise ValueError("start_date must not be after end_date")
    store = TaiwanDailyStore()
    sessions = sorted(d for d in store.available_dates() if d <= end)
    selected = [d for d in sessions if start is None or d >= start]
    if start is None:
        selected = selected[-days:]
    data: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    if selected and symbols:
        first = sessions.index(selected[0])
        read_start = sessions[max(0, first - 150)] if include_indicators else selected[0]
        history = store.read_range(symbols, read_start, end)
        if not history.is_empty():
            if include_indicators:
                panel = compute_taiwan_indicator_panel(history, price_semantics="raw")
                history = history.join(panel, on=["symbol", "date"], how="left")
            history = history.filter(pl.col("date") >= selected[0]).sort(["symbol", "date"])
            for group in history.partition_by("symbol", maintain_order=True):
                data[group["symbol"][0]] = group.to_dicts()

    # A stalled daily store cannot itself prove freshness. Reuse the verified-session
    # resolver and its 16:00 publication cutoff, bounded by a historical requested end.
    calendar = TaiwanTradingCalendar()
    evidence_store = ObservedUniverseStore()
    reference_now = min(taipei_now(), datetime.combine(end, time.max, tzinfo=TAIPEI_TZ))
    try:
        expected_session = latest_completed_session(
            reference_now,
            lambda day, exchange: evidence_store.day_evidence(exchange, day, calendar=calendar),
        )
    except (ValueError, OSError):
        expected_session = None

    meta = {}
    for symbol, rows in data.items():
        as_of = rows[-1]["date"] if rows else None
        reason = None
        status = "available"
        if not rows:
            status, reason = "unavailable", "no_persisted_daily_history"
        elif (selected and as_of < selected[-1]) or (expected_session and as_of < expected_session):
            status, reason = "stale", "latest_daily_bar_predates_confirmed_session"
        elif expected_session is None:
            status, reason = "partial", "session_freshness_unverified"
        elif len(rows) < (len(selected) if start is not None else days):
            status, reason = "partial", "insufficient_daily_history"
        meta[symbol] = {
            "source": "taiwan_daily_store", "status": status,
            "as_of": as_of, "trade_date": as_of, "reason": reason,
            "expected_session": expected_session,
            "price_semantics": "raw", "volume_unit": "shares", "amount_unit": "TWD",
        }
    return {"data": data, "meta": meta}


def sync_daily_batch(symbols: list[str],
                     count: int | None = None,
                     batch_size: int | None = None,
                     rpm: int | None = None,
                     start_time: datetime | None = None,
                     end_time: datetime | None = None,
                     on_chunk_done: Callable[[int, int], None] | None = None,
                     failed_out: list[str] | None = None) -> pl.DataFrame:
    """Read daily bars through the selected provider; missing capabilities never fall back."""
    from app.data_providers import custom as custom_sources
    from app.data_providers.registry import get_provider

    if not symbols:
        return pl.DataFrame()
    name = preferences.get_daily_data_provider()
    if name != "taiwan" and not custom_sources.provider_has_dataset(name, "daily"):
        if failed_out is not None:
            failed_out.extend(symbols)
        logger.warning("Daily provider %s does not provide daily data", name)
        return pl.DataFrame()
    return get_provider(name).get_daily(
        symbols, start_time=start_time, end_time=end_time, on_chunk_done=on_chunk_done,
    )


# ===== 分鐘 K 同步 =====


def _resolve_minute_provider(
    provider_name: str,
) -> tuple[object | None, bool, str | None]:
    """Return (provider, unavailable, error) without an implicit source fallback."""
    from app.data_providers import custom as custom_sources

    if provider_name == "taiwan":
        return None, True, None
    try:
        if not custom_sources.provider_has_dataset(provider_name, "minute"):
            return None, True, None
        return custom_sources.get_provider(provider_name), False, None
    except Exception as exc:
        return None, True, str(exc)


def _try_custom_minute(
    symbols: list[str],
    start_time: datetime | None,
    end_time: datetime | None,
    asset_type: AssetType,
    freq: str = "1m",
    on_chunk_done: Callable[[int, int, str], None] | None = None,
) -> tuple[pl.DataFrame | None, bool]:
    """Read custom minute bars; failures remain unavailable instead of changing sources."""
    name = preferences.get_minute_data_provider()
    provider, unavailable, error = _resolve_minute_provider(name)
    if unavailable:
        if error is not None:
            logger.warning("minute provider %s resolution failed: %s", name, error)
        return None, True
    wrapped_cb = None
    if on_chunk_done is not None:
        def wrapped_cb(cur: int, total: int) -> None:
            on_chunk_done(cur, total, "custom")
    try:
        return provider.get_minute(
            symbols, start_time=start_time, end_time=end_time,
            asset_type=asset_type, freq=freq, on_chunk_done=wrapped_cb,
        ), False
    except Exception as exc:
        logger.warning("minute provider %s fetch failed: %s", name, exc)
        return None, True


def sync_minute_batch(
    symbols: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    count: int | None = None,
    batch_size: int | None = None,
    rpm: int | None = None,
    on_chunk_done: Callable[[int, int, str], None] | None = None,
    segment_trading_days: int = 20,
    on_segment: Callable[[pl.DataFrame], None] | None = None,
    asset_type: AssetType = "stock",
) -> pl.DataFrame:
    """Read configured custom minute data and preserve the optional persistence callback."""
    if not symbols:
        return pl.DataFrame()
    df, unavailable = _try_custom_minute(
        symbols, start_time=start_time, end_time=end_time,
        asset_type=asset_type, freq="1m", on_chunk_done=on_chunk_done,
    )
    if unavailable or df is None:
        return pl.DataFrame()
    if on_segment and not df.is_empty():
        on_segment(df)
        return pl.DataFrame()
    return df


def fetch_minute_single(
    symbol: str,
    trade_date: date,
    asset_type: AssetType = "stock",
) -> pl.DataFrame:
    """Read one Taiwan trading session only from the configured minute provider."""
    start_time = datetime.combine(trade_date, datetime.min.time(), tzinfo=TAIPEI_TZ).replace(hour=9)
    end_time = start_time.replace(hour=13, minute=30)
    df, unavailable = _try_custom_minute(
        [symbol], start_time=start_time, end_time=end_time, asset_type=asset_type,
    )
    return df if not unavailable and df is not None else pl.DataFrame()


def fetch_adj_factor_single(symbol: str) -> pl.DataFrame:
    """Read factors only from an explicitly configured provider with that dataset."""
    from app.data_providers import custom as custom_sources

    name = preferences.get_adj_factor_provider()
    if name == "same_as_daily":
        name = preferences.get_daily_data_provider()
    if name == "taiwan" or not custom_sources.provider_has_dataset(name, "adj_factor"):
        return pl.DataFrame()
    try:
        return custom_sources.get_provider(name).get_adj_factors([symbol], start_time=None, end_time=None)
    except Exception as exc:
        logger.warning("adjustment factor fetch failed for %s: %s", symbol, exc)
        return pl.DataFrame()
