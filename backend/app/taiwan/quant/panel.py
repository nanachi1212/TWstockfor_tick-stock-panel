"""PIT Taiwan factor rows. Every feature date has its own price anchor."""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import polars as pl

from app.taiwan.adjust import adjust_prices_as_of, reject_presentation
from app.taiwan.corporate_actions import CorporateActionEvent
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.technical_indicators import compute_taiwan_indicator_panel

FACTOR_VERSION = "tw-factors-v1"
TECHNICAL = (
    "ma5", "ma10", "ma20", "ma60", "ma20_slope", "ma60_slope", "rsi_14",
    "macd_dif", "macd_dea", "macd_hist", "macd_hist_streak", "momentum_5d",
    "momentum_20d", "momentum_60d", "distance_to_ma20", "atr_14", "volatility_20d",
)
LIQUIDITY = ("relative_volume", "amount", "adv20_twd")
CHIP = (
    "foreign_net_1d", "foreign_net_3d", "foreign_net_5d", "foreign_net_20d",
    "trust_net_1d", "trust_net_3d", "trust_net_5d", "trust_net_20d",
    "dealer_net_1d", "dealer_net_5d", "dealer_net_20d",
    "foreign_buy_streak", "foreign_sell_streak", "trust_buy_streak",
    "trust_sell_streak", "institutional_sync_buy", "institutional_sync_sell",
)
MARGIN = (
    "margin_balance", "margin_change_1d", "margin_change_5d", "short_balance",
    "short_change_5d", "short_margin_ratio",
)
RELATIVE = tuple(
    f"{prefix}_{n}d" for prefix in ("stock_return", "market_return", "relative_to_market")
    for n in (5, 20, 60, 120)
)
INDUSTRY_DISABLED = ("relative_to_industry", "industry_return", "industry_rank")
FACTORS = (*TECHNICAL, *LIQUIDITY, *CHIP, *MARGIN, *RELATIVE, *INDUSTRY_DISABLED)
_PRICE_REQUIRED = {"symbol", "date", "open", "high", "low", "close", "volume", "amount"}


@dataclass(frozen=True)
class FactorPanel:
    values: pl.DataFrame
    coverage: pl.DataFrame
    factor_version: str
    policy_version: str
    universe_tier: str


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _available(row: dict[str, Any] | None, day: date) -> bool:
    if row is None or row.get("status") not in (None, "verified", "ok", "available"):
        return False
    timestamp = row.get("available_at")
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp)
    return isinstance(timestamp, datetime) and timestamp.utcoffset() is not None and timestamp <= market_close(day)


def _source_map(frame: pl.DataFrame | None, columns: set[str]) -> dict[tuple[str, date], dict[str, Any]]:
    if frame is None or frame.is_empty():
        return {}
    if not {"symbol", "date", *columns} <= set(frame.columns):
        raise ValueError(f"source missing required columns: {sorted(columns)}")
    if frame.schema["date"] != pl.Date or frame.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("source dates must be trading-session Date with unique symbol/date")
    return {(row["symbol"], row["date"]): row for row in frame.iter_rows(named=True)}


def _sum_window(source: dict[tuple[str, date], dict[str, Any]], symbol: str,
                sessions: list[date], field: str, n: int) -> tuple[float | None, str]:
    if len(sessions) < n:
        return None, "insufficient_history"
    rows = [source.get((symbol, day)) for day in sessions[-n:]]
    if any(not _available(row, day) for row, day in zip(rows, sessions[-n:], strict=True)):
        return None, "unavailable"
    values = [_finite(row.get(field)) for row in rows if row is not None]
    if len(values) != n or any(value is None for value in values):
        return None, "data_insufficient"
    return sum(values), "available"


def _streak(source: dict[tuple[str, date], dict[str, Any]], symbol: str,
            sessions: list[date], field: str, sign: int) -> tuple[float | None, str]:
    if not sessions:
        return None, "insufficient_history"
    count = 0
    for day in reversed(sessions):
        row = source.get((symbol, day))
        if not _available(row, day):
            return None, "unavailable"
        value = _finite(row.get(field)) if row is not None else None
        if value is None:
            return None, "data_insufficient"
        if value * sign <= 0:
            break
        count += 1
    return float(count), "available"


def _return(values: list[float | None], n: int) -> float | None:
    if len(values) <= n or values[-1] is None or values[-n - 1] is None or values[-n - 1] <= 0:
        return None
    return values[-1] / values[-n - 1] - 1


def build_factor_panel(
    history: pl.DataFrame, *, events: Iterable[CorporateActionEvent],
    policy_version: str, universe_tier: str,
    institutional: pl.DataFrame | None = None, margin: pl.DataFrame | None = None,
    market: pl.DataFrame | None = None, factor_version: str = FACTOR_VERSION,
) -> FactorPanel:
    """Build offline full time-series factors from raw OHLCV and explicit event snapshot.

    Institutional, margin and market rows require a verified ``available_at`` no
    later than the feature session close. Existing stores lack that timestamp,
    so their unaugmented rows remain unavailable rather than becoming fake zeroes.
    ``market`` is an index series with date/close/available_at, not a stock master.
    """
    reject_presentation(history)
    if not isinstance(history, pl.DataFrame) or not set(history.columns).issuperset(_PRICE_REQUIRED):
        raise ValueError("factor panel requires raw daily OHLCV and amount")
    if history.schema["date"] != pl.Date or history.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("daily dates must be unique trading-session Date per symbol")
    if "usage_scope" in history.columns or "price_semantics" in history.columns:
        raise ValueError("factor panel requires raw input; presentation/adjusted price rejected")
    if not factor_version or not policy_version or universe_tier not in ("primary_verified", "secondary_observed"):
        raise ValueError("factor/policy version and valid universe tier are required")
    inst = _source_map(institutional, {"foreign_net", "investment_trust_net", "dealer_net"})
    marg = _source_map(margin, {"margin_balance", "short_balance"})
    if market is not None and not market.is_empty():
        if not {"date", "close", "available_at"} <= set(market.columns):
            raise ValueError("market index requires date/close/available_at")
        if market.schema["date"] != pl.Date or market["date"].n_unique() != market.height:
            raise ValueError("market index dates must be unique")
        market_rows = {row["date"]: row for row in market.iter_rows(named=True)}
    else:
        market_rows = {}
    actions = tuple(events)
    raw = history.sort(["symbol", "date"])
    all_days = raw["date"].unique().sort().to_list()
    market_closes: dict[date, float] = {}
    for day in all_days:
        row = market_rows.get(day)
        if _available(row, day):
            close = _finite(row.get("close")) if row is not None else None
            if close is not None and close > 0:
                market_closes[day] = close
    value_rows: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    for symbol in sorted(raw["symbol"].unique().to_list()):
        series = raw.filter(pl.col("symbol") == symbol)
        dates = series["date"].to_list()
        for index, day in enumerate(dates):
            prefix = series.head(index + 1)
            adjusted = adjust_prices_as_of(prefix, as_of=day, events=actions)
            price = adjusted.to_frame()
            indicator_frame = compute_taiwan_indicator_panel(price)
            indicators = indicator_frame.tail(1).to_dicts()[0]
            bars = price.to_dicts()
            closes = [_finite(bar["close"]) for bar in bars]
            amounts = [_finite(bar["amount"]) for bar in bars]
            volumes = [_finite(bar["volume"]) for bar in bars]
            row: dict[str, Any] = {
                "date": day, "symbol": symbol, "factor_version": factor_version,
                "policy_version": policy_version, "universe_tier": universe_tier,
                "usage_scope": "pit_feature", "adjustment_as_of": market_close(day).isoformat(),
                "adjustment_status": adjusted.status,
            }
            statuses: dict[str, tuple[str, str, str]] = {}

            def put(name: str, value: Any, status: str = "available", reason: str = "",
                    source: str = "taiwan_daily_store", *,
                    _row: dict[str, Any] = row,
                    _statuses: dict[str, tuple[str, str, str]] = statuses,
                    _adjusted_status: str = adjusted.status) -> None:
                number = _finite(value)
                if _adjusted_status != "verified" and name in (*TECHNICAL, "relative_volume", *RELATIVE):
                    number, status, reason = None, "data_insufficient", "adjustment_unverified"
                if number is None and status == "available":
                    status, reason = "insufficient_history", "missing_window_or_value"
                _row[name] = number
                if status != "available":
                    _statuses[name] = (status, reason, source)

            for name in ("ma5", "ma10", "ma20", "ma60", "rsi_14", "macd_dif",
                         "macd_dea", "macd_hist", "momentum_5d", "momentum_20d"):
                put(name, indicators[name], source="pit_adjusted_daily")
            for n in (20, 60):
                previous = None
                if len(closes) >= n + 1 and all(x is not None for x in closes[-n - 1:-1]):
                    previous = sum(closes[-n - 1:-1]) / n
                current = indicators[f"ma{n}"]
                put(f"ma{n}_slope", current / previous - 1 if current and previous else None,
                    source="pit_adjusted_daily")
            histogram = indicator_frame["macd_hist"].to_list()
            if histogram[-1] is None:
                streak = None
            else:
                direction = 1 if histogram[-1] > 0 else -1 if histogram[-1] < 0 else 0
                streak = 0
                if direction:
                    for value in reversed(histogram):
                        if value is None or value * direction <= 0:
                            break
                        streak += direction
            put("macd_hist_streak", streak, source="pit_adjusted_daily")
            put("momentum_60d", _return(closes, 60), source="pit_adjusted_daily")
            put("distance_to_ma20", closes[-1] / indicators["ma20"] - 1
                if closes[-1] is not None and indicators["ma20"] else None,
                source="pit_adjusted_daily")
            if len(bars) >= 15:
                ranges = []
                for j in range(len(bars) - 14, len(bars)):
                    high, low, prior = (_finite(bars[j]["high"]), _finite(bars[j]["low"]), closes[j - 1])
                    if None in (high, low, prior):
                        ranges = []
                        break
                    ranges.append(max(high - low, abs(high - prior), abs(low - prior)))
                atr = sum(ranges) / 14 if len(ranges) == 14 else None
            else:
                atr = None
            put("atr_14", atr, source="pit_adjusted_daily")
            returns = [_return(closes[:j + 1], 1) for j in range(max(1, len(closes) - 20), len(closes))]
            if len(returns) == 20 and all(x is not None for x in returns):
                mean = sum(returns) / 20
                volatility = math.sqrt(sum((x - mean) ** 2 for x in returns) / 20)
            else:
                volatility = None
            put("volatility_20d", volatility, source="pit_adjusted_daily")
            put("amount", amounts[-1], source="taiwan_daily_store")
            put("adv20_twd", sum(amounts[-20:]) / 20
                if len(amounts) >= 20 and all(x is not None for x in amounts[-20:]) else None,
                source="taiwan_daily_store")
            if len(volumes) >= 20 and all(x is not None for x in volumes[-20:]) and sum(volumes[-20:]) > 0:
                epoch = price["_share_count_epoch"].to_list()[-20:]
                if len(set(epoch)) == 1:
                    put("relative_volume", volumes[-1] / (sum(volumes[-20:]) / 20), source="taiwan_daily_store")
                else:
                    put("relative_volume", None, "data_insufficient", "share_count_event_crosses_window")
            else:
                put("relative_volume", None)
            sessions = dates[:index + 1]
            for prefix_name, field, windows in (
                ("foreign_net", "foreign_net", (1, 3, 5, 20)),
                ("trust_net", "investment_trust_net", (1, 3, 5, 20)),
                ("dealer_net", "dealer_net", (1, 5, 20)),
            ):
                for n in windows:
                    value, status = _sum_window(inst, symbol, sessions, field, n)
                    put(f"{prefix_name}_{n}d", value, status, status, "taiwan_institutional_store")
            for name, field, sign in (
                ("foreign_buy_streak", "foreign_net", 1), ("foreign_sell_streak", "foreign_net", -1),
                ("trust_buy_streak", "investment_trust_net", 1),
                ("trust_sell_streak", "investment_trust_net", -1),
            ):
                value, status = _streak(inst, symbol, sessions, field, sign)
                put(name, value, status, status, "taiwan_institutional_store")
            today_inst = inst.get((symbol, day))
            for name, sign in (("institutional_sync_buy", 1), ("institutional_sync_sell", -1)):
                if _available(today_inst, day):
                    nets = [_finite(today_inst.get(field)) for field in
                            ("foreign_net", "investment_trust_net", "dealer_net")]
                    value = float(all(x * sign > 0 for x in nets)) if all(x is not None for x in nets) else None
                    put(name, value, source="taiwan_institutional_store")
                else:
                    put(name, None, "unavailable", "missing_available_at", "taiwan_institutional_store")
            today_margin = marg.get((symbol, day))
            for name, field, n in (
                ("margin_balance", "margin_balance", 1),
                ("margin_change_1d", "margin_balance", 2),
                ("margin_change_5d", "margin_balance", 6),
                ("short_balance", "short_balance", 1),
                ("short_change_5d", "short_balance", 6),
            ):
                if len(sessions) < n:
                    put(name, None, "insufficient_history", "missing_window", "taiwan_margin_store")
                    continue
                selected = [marg.get((symbol, d)) for d in sessions[-n:]]
                if any(not _available(x, d) for x, d in zip(selected, sessions[-n:], strict=True)):
                    put(name, None, "unavailable", "missing_available_at", "taiwan_margin_store")
                    continue
                values = [_finite(x.get(field)) for x in selected]
                value = values[-1] if n == 1 else values[-1] - values[0] if all(x is not None for x in values) else None
                put(name, value, source="taiwan_margin_store")
            if _available(today_margin, day):
                balance = _finite(today_margin.get("margin_balance"))
                short = _finite(today_margin.get("short_balance"))
                put("short_margin_ratio", short / balance if balance and short is not None else None,
                    source="taiwan_margin_store")
            else:
                put("short_margin_ratio", None, "unavailable", "missing_available_at", "taiwan_margin_store")
            for n in (5, 20, 60, 120):
                stock_ret = _return(closes, n)
                put(f"stock_return_{n}d", stock_ret, source="pit_adjusted_daily")
                market_days = dates[max(0, index - n):index + 1]
                if len(market_days) == n + 1 and all(x in market_closes for x in market_days):
                    market_ret = market_closes[day] / market_closes[market_days[0]] - 1
                else:
                    market_ret = None
                put(f"market_return_{n}d", market_ret,
                    "available" if market_ret is not None else "unavailable", "market_history_missing",
                    "market_index")
                put(f"relative_to_market_{n}d", stock_ret - market_ret
                    if stock_ret is not None and market_ret is not None else None,
                    "available" if stock_ret is not None and market_ret is not None else "unavailable",
                    "stock_or_market_history_missing", "pit_adjusted_daily+market_index")
            for name in INDUSTRY_DISABLED:
                put(name, None, "not_pit_safe", "historical_industry_assignment_unverified", "current_industry")
            value_rows.append(row)
            for name, (status, reason, source) in statuses.items():
                observed = (inst.get((symbol, day)) if source == "taiwan_institutional_store"
                            else marg.get((symbol, day)) if source == "taiwan_margin_store"
                            else market_rows.get(day) if source == "market_index" else None)
                exceptions.append({
                    "symbol": symbol, "date": day, "factor": name, "status": status,
                    "as_of": market_close(day).isoformat(),
                    "available_at": str(observed.get("available_at")) if observed and observed.get("available_at") else None,
                    "reason": reason, "source": source,
                })
    values = (pl.DataFrame(value_rows, infer_schema_length=None)
              .with_columns(pl.col(name).cast(pl.Float64) for name in FACTORS)
              .sort(["date", "symbol"])) if value_rows else pl.DataFrame()
    coverage = pl.DataFrame(exceptions).sort(["date", "symbol", "factor"]) if exceptions else pl.DataFrame()
    return FactorPanel(values, coverage, factor_version, policy_version, universe_tier)
