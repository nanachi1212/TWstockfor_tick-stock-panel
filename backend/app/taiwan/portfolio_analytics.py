"""Portfolio-level analytics for the browser ledger's current holdings.

Performance is a hypothetical "hold today's shares through the window" series,
not the user's realized history. Prices are adjusted with the verified
corporate-action snapshot; the window shrinks to that coverage and any symbol
whose adjustment cannot be verified makes performance unavailable (fail-closed).
The intended benchmark is 0050.TWSE, matching selection review. Its metrics
remain unavailable until ETF distribution coverage can be verified.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

import polars as pl

from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import ObservedUniverseStore

BENCHMARK_SYMBOL = "0050.TWSE"
BENCHMARK_NAME = "元大台灣50（加權指數代理）"
LOOKBACK_DAYS = 365
MIN_SESSIONS = 20
TRADING_DAYS_PER_YEAR = 252


def series_metrics(values: list[float], bench: list[float] | None) -> dict[str, float | None]:
    """Return decimals: total_return, benchmark_return, volatility, max_drawdown; beta is a ratio."""
    if len(values) < 2 or (bench is not None and len(values) != len(bench)):
        raise ValueError("series must be aligned and have at least two points")
    if any(not math.isfinite(v) or v <= 0 for v in [*values, *(bench or [])]):
        raise ValueError("prices must be finite and positive")
    rp = [b / a - 1 for a, b in pairwise(values)]
    rb = [b / a - 1 for a, b in pairwise(bench)] if bench is not None else []
    n = len(rp)
    mp, mb = sum(rp) / n, sum(rb) / n
    var_p = sum((x - mp) ** 2 for x in rp) / (n - 1) if n > 1 else 0.0
    var_b = sum((x - mb) ** 2 for x in rb) / (n - 1) if n > 1 else 0.0
    cov = sum((x - mp) * (y - mb) for x, y in zip(rp, rb, strict=True)) / (n - 1) if n > 1 and bench is not None else 0.0
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {
        "total_return": values[-1] / values[0] - 1,
        "benchmark_return": bench[-1] / bench[0] - 1 if bench is not None else None,
        "volatility": math.sqrt(var_p * TRADING_DAYS_PER_YEAR),
        "benchmark_volatility": math.sqrt(var_b * TRADING_DAYS_PER_YEAR) if bench is not None else None,
        "beta": cov / var_b if var_b > 0 else None,
        "max_drawdown": mdd,
    }


class PortfolioAnalyticsService:
    def __init__(self, *, daily: TaiwanDailyStore | None = None,
                 actions: CorporateActionStore | None = None, master=None,
                 evidence: ObservedUniverseStore | None = None) -> None:
        self.daily = daily or TaiwanDailyStore()
        self.actions = actions or CorporateActionStore()
        self.evidence = evidence or ObservedUniverseStore()
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
        positions: list[dict[str, Any]] = []
        unpriced: list[str] = []
        stale: list[str] = []
        as_of: date | None = self.daily.latest_date() if holdings else None
        for symbol, shares in holdings.items():
            close, day = closes.get(symbol, (None, None))
            if close is None or not math.isfinite(close) or close <= 0:
                unpriced.append(symbol)
                continue
            if day != as_of:
                stale.append(symbol)
                continue
            value = close * shares
            if not math.isfinite(value):
                unpriced.append(symbol)
                continue
            inst = self.master.get_instrument(symbol)
            if inst is not None and inst.instrument_type == "etf":
                label = "ETF"
            else:
                label = (inst.industry if inst is not None else None) or "未分類"
            groups[label] = groups.get(label, 0.0) + value
            positions.append({"symbol": symbol, "market_value": value})
        total = sum(groups.values())
        complete = bool(holdings) and not (unpriced or stale) and math.isfinite(total) and total > 0
        sectors = sorted(({"industry": k, "market_value": v, "weight": v / total}
                          for k, v in groups.items()), key=lambda x: (-x["market_value"], x["industry"])) if complete else []
        positions = sorted(({**p, "weight": p["market_value"] / total} for p in positions),
                           key=lambda x: (-x["market_value"], x["symbol"])) if complete else []
        # shortcut: ETF weights stay at product level until constituent data is available.
        industries = [s for s in sectors if s["industry"] not in {"ETF", "未分類"}]
        return {
            "status": "available" if complete else ("partial" if positions or groups else "unavailable"),
            "as_of": as_of.isoformat() if as_of else None,
            "total_market_value": total if complete else None,
            "sectors": sectors,
            "positions": positions,
            "concentration": {
                "largest_position": positions[0],
                "top3_weight": sum(p["weight"] for p in positions[:3]),
                "largest_industry": industries[0] if industries and "未分類" not in groups else None,
            } if complete else None,
            "unpriced_symbols": unpriced,
            "stale_symbols": stale,
        }

    def _performance(self, holdings: dict[str, float]) -> dict[str, Any]:
        out: dict[str, Any] = {"status": "unavailable", "reason": None, "window_start": None,
                               "window_end": None, "sessions": 0, "metrics": None, "series": [],
                               "excluded_symbols": [], "missing_sessions": []}
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
        symbols = list(dict.fromkeys([*holdings, BENCHMARK_SYMBOL]))
        # shortcut: stock action sources do not prove ETF distributions; add a verified ETF source before enabling them.
        unsupported = [s for s in symbols if s == BENCHMARK_SYMBOL or
                       (inst := self.master.get_instrument(s)) is None or inst.instrument_type != "stock"]
        for symbol in unsupported:
            out["excluded_symbols"].append({"symbol": symbol, "reason": "asset_action_coverage_unavailable"})
        if any(s in holdings for s in unsupported):
            out["reason"] = "asset_action_coverage_unavailable"
            return out
        symbols = [s for s in symbols if s not in unsupported]
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
        if any(s["symbol"] in holdings for s in out["excluded_symbols"]):
            out["reason"] = "symbol_data_insufficient"
            return out
        start = max(frame["date"].min() for frame in frames.values())
        out.update(window_start=start.isoformat(), window_end=end.isoformat())
        observed = set(raw["date"].to_list())
        for symbol, frame in frames.items():
            valid = set(frame.filter(pl.col(symbol).is_finite() & (pl.col(symbol) > 0))["date"].to_list())
            day = start
            while day <= end:
                if day not in valid and (day in observed or self.evidence.day_evidence(
                        symbol.rsplit(".", 1)[-1], day).status != "non_trading"):
                    out["missing_sessions"].append({"symbol": symbol, "date": day.isoformat()})
                day += timedelta(days=1)
        if out["missing_sessions"]:
            out["reason"] = "incomplete_sessions"
            return out
        wide = frames[symbols[0]]
        for symbol in symbols[1:]:
            wide = wide.join(frames[symbol], on="date", how="inner")
        wide = wide.sort("date")
        if wide.height < MIN_SESSIONS:
            out["reason"] = "insufficient_sessions"
            out["sessions"] = wide.height
            return out
        values = wide.select(pl.sum_horizontal(
            [pl.col(s) * shares for s, shares in holdings.items()])).to_series().to_list()
        if any(not math.isfinite(v) or v <= 0 for v in values):
            out["reason"] = "invalid_portfolio_value"
            return out
        bench = wide[BENCHMARK_SYMBOL].to_list() if BENCHMARK_SYMBOL in frames else None
        dates = wide["date"].to_list()
        out.update(
            status="available" if bench is not None else "partial",
            reason=None if bench is not None else "benchmark_distribution_unverified", window_start=dates[0].isoformat(),
            window_end=dates[-1].isoformat(), sessions=len(dates),
            metrics=series_metrics(values, bench),
            series=[{"date": d.isoformat(), "portfolio": v / values[0] * 100,
                     "benchmark": bench[i] / bench[0] * 100 if bench is not None else None}
                    for i, (d, v) in enumerate(zip(dates, values, strict=True))],
        )
        return out
