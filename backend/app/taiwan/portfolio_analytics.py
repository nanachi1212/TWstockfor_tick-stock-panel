"""Portfolio-level analytics for the browser ledger's current holdings.

Performance is a hypothetical "hold today's shares through the window" series,
not the user's realized history. Prices are adjusted with the verified
corporate-action snapshot; the window shrinks to that coverage and any symbol
whose adjustment cannot be verified makes performance unavailable (fail-closed).
The benchmark is 0050.TWSE from the same daily store, matching selection review,
because the persisted TAIEX series is month-to-date only.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import polars as pl

from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore

BENCHMARK_SYMBOL = "0050.TWSE"
BENCHMARK_NAME = "元大台灣50（加權指數代理）"
LOOKBACK_DAYS = 365
MIN_SESSIONS = 20
TRADING_DAYS_PER_YEAR = 252


def series_metrics(values: list[float], bench: list[float]) -> dict[str, float | None]:
    """Return decimals: total_return, benchmark_return, volatility, max_drawdown; beta is a ratio."""
    if len(values) != len(bench) or len(values) < 2:
        raise ValueError("series must be aligned and have at least two points")
    rp = [b / a - 1 for a, b in zip(values, values[1:])]
    rb = [b / a - 1 for a, b in zip(bench, bench[1:])]
    n = len(rp)
    mp, mb = sum(rp) / n, sum(rb) / n
    var_p = sum((x - mp) ** 2 for x in rp) / (n - 1) if n > 1 else 0.0
    var_b = sum((x - mb) ** 2 for x in rb) / (n - 1) if n > 1 else 0.0
    cov = sum((x - mp) * (y - mb) for x, y in zip(rp, rb)) / (n - 1) if n > 1 else 0.0
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {
        "total_return": values[-1] / values[0] - 1,
        "benchmark_return": bench[-1] / bench[0] - 1,
        "volatility": math.sqrt(var_p * TRADING_DAYS_PER_YEAR),
        "benchmark_volatility": math.sqrt(var_b * TRADING_DAYS_PER_YEAR),
        "beta": cov / var_b if var_b > 0 else None,
        "max_drawdown": mdd,
    }


class PortfolioAnalyticsService:
    def __init__(self, *, daily: TaiwanDailyStore | None = None,
                 actions: CorporateActionStore | None = None, master=None) -> None:
        self.daily = daily or TaiwanDailyStore()
        self.actions = actions or CorporateActionStore()
        if master is None:
            from app.taiwan.universe import get_security_master
            master = get_security_master()
        self.master = master

    def analyze(self, holdings: dict[str, float]) -> dict[str, Any]:
        holdings = {s: q for s, q in holdings.items() if q > 0}
        return {
            "benchmark_symbol": BENCHMARK_SYMBOL,
            "benchmark_name": BENCHMARK_NAME,
            "allocation": self._allocation(holdings),
            "performance": self._performance(holdings),
        }

    def _allocation(self, holdings: dict[str, float]) -> dict[str, Any]:
        latest = self.daily.read_latest_per_symbol(list(holdings)) if holdings else None
        closes = {r["symbol"]: (r["close"], r["date"]) for r in latest.iter_rows(named=True)} if latest is not None else {}
        groups: dict[str, float] = {}
        unpriced: list[str] = []
        as_of: date | None = None
        for symbol, shares in holdings.items():
            close, day = closes.get(symbol, (None, None))
            if close is None or not math.isfinite(close) or close <= 0:
                unpriced.append(symbol)
                continue
            as_of = max(as_of, day) if as_of else day
            inst = self.master.get_instrument(symbol)
            if inst is not None and inst.instrument_type == "etf":
                label = "ETF"
            else:
                label = (inst.industry if inst is not None else None) or "未分類"
            groups[label] = groups.get(label, 0.0) + close * shares
        total = sum(groups.values())
        sectors = sorted(({"industry": k, "market_value": v, "weight": v / total}
                          for k, v in groups.items()), key=lambda x: -x["market_value"])
        return {
            "status": "unavailable" if total <= 0 else ("partial" if unpriced else "available"),
            "as_of": as_of.isoformat() if as_of else None,
            "total_market_value": total if total > 0 else None,
            "sectors": sectors,
            "unpriced_symbols": unpriced,
        }

    def _performance(self, holdings: dict[str, float]) -> dict[str, Any]:
        out: dict[str, Any] = {"status": "unavailable", "reason": None, "window_start": None,
                               "window_end": None, "sessions": 0, "metrics": None, "series": [],
                               "excluded_symbols": []}
        if not holdings:
            out["reason"] = "no_holdings"
            return out
        coverage = self.actions.read_verified_coverage()
        latest_day = self.daily.latest_date()
        if coverage is None or latest_day is None:
            out["reason"] = "corporate_action_coverage_unavailable" if coverage is None else "no_daily_data"
            return out
        end = min(latest_day, coverage[1])
        start = max(end - timedelta(days=LOOKBACK_DAYS), coverage[0])
        events = tuple(e for e in coverage[2] if start <= e.effective_date <= end)
        symbols = [*holdings, BENCHMARK_SYMBOL]
        raw = self.daily.read_range(symbols, start, end).select("symbol", "date", "close")
        frames: dict[str, pl.DataFrame] = {}
        for symbol in symbols:
            rows = raw.filter(pl.col("symbol") == symbol).unique("date").sort("date")
            if rows.is_empty():
                out["excluded_symbols"].append({"symbol": symbol, "reason": "no_price_history"})
                continue
            adjusted = adjust_prices_as_of(rows, as_of=end, events=events, price_columns=("close",))
            if adjusted.status != "verified":
                out["excluded_symbols"].append({"symbol": symbol, "reason": "price_adjustment_unavailable"})
                continue
            frames[symbol] = adjusted.to_frame().select("date", pl.col("close").alias(symbol))
        if out["excluded_symbols"]:
            out["reason"] = "symbol_data_insufficient"
            return out
        # Only sessions where every holding and the benchmark have a close; no fill.
        wide = frames[BENCHMARK_SYMBOL]
        for symbol in holdings:
            wide = wide.join(frames[symbol], on="date", how="inner")
        wide = wide.sort("date")
        if wide.height < MIN_SESSIONS:
            out["reason"] = "insufficient_sessions"
            out["sessions"] = wide.height
            return out
        values = wide.select(pl.sum_horizontal(
            [pl.col(s) * shares for s, shares in holdings.items()])).to_series().to_list()
        bench = wide[BENCHMARK_SYMBOL].to_list()
        dates = wide["date"].to_list()
        out.update(
            status="available", reason=None, window_start=dates[0].isoformat(),
            window_end=dates[-1].isoformat(), sessions=len(dates),
            metrics=series_metrics(values, bench),
            series=[{"date": d.isoformat(), "portfolio": v / values[0] * 100, "benchmark": b / bench[0] * 100}
                    for d, v, b in zip(dates, values, bench)],
        )
        return out
