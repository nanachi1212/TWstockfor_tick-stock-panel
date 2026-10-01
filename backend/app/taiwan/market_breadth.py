"""Descriptive breadth over observable bars, with independent eligibility evidence.

No current security master is consulted. Verified historical stock eligibility
does not certify a complete historical listing universe (suspended stocks may
be absent from the census). Price normalization remains owned by adjust.py.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.corporate_actions import CorporateActionEvent

OBSERVED_LABEL = "依可觀測日 K 計算的研究統計"
MetricStatus = Literal["available", "partial", "unavailable"]


class ResearchMetric(BaseModel):
    value: float | None = None
    included_count: int = 0
    excluded_count: int = 0
    excluded_reason_counts: dict[str, int] = Field(default_factory=dict)
    coverage: float | None = None
    status: MetricStatus = "unavailable"
    unit: str
    numerator: int | None = None


class MarketBreadthStats(BaseModel):
    as_of: date
    market: str
    source: list[str]
    retrieved_at: str | None = None
    price_retrieved_at_status: Literal["unavailable_in_legacy_daily_store"] = "unavailable_in_legacy_daily_store"
    eligibility_retrieved_at: str | None = None
    missing_markets: list[str] = Field(default_factory=list)
    available_at: str | None = None
    usage_scope: Literal["descriptive_history"] = "descriptive_history"
    strategy_lab_eligible: Literal[False] = False
    universe_status: Literal["observed"] = "observed"
    universe_label: str = OBSERVED_LABEL
    historical_eligibility_status: str
    eligibility_verified_count: int
    eligibility_unknown_count: int
    universe_complete: Literal[False] = False
    coverage_denominator: str = "observable_bars_and_verified_observed_stocks"
    metrics: dict[str, ResearchMetric]
    advances: int | None = None
    declines: int | None = None
    unchanged: int | None = None
    ad_segment_start: date | None = None


def metric(values: list[float], reasons: Counter[str], *, unit: str,
           ratio: bool = False) -> ResearchMetric:
    included, excluded = len(values), sum(reasons.values())
    total = included + excluded
    return ResearchMetric(
        value=(sum(values) / included if ratio else sum(values)) if included else None,
        included_count=included, excluded_count=excluded,
        excluded_reason_counts=dict(sorted(reasons.items())),
        coverage=included / total if total else None,
        status="partial" if included and excluded else "available" if included else "unavailable",
        unit=unit, numerator=int(sum(values)) if ratio and included else None,
    )


def calculate_breadth(
    daily: pl.DataFrame, *, day: date, market: str,
    sessions: Mapping[str, Sequence[date]],
    eligibility: Mapping[str, tuple[str | None, str]],
    verified_observed_stocks: Sequence[str],
    unresolved_days: Mapping[str, set[date]],
    actions_for_window: Callable[[date, date], tuple[CorporateActionEvent, ...] | None],
    source: list[str] | None = None, retrieved_at: str | None = None,
    prices_by_symbol: dict[str, dict[date, dict[str, Any]]] | None = None,
) -> MarketBreadthStats:
    prices: dict[str, dict[date, dict[str, Any]]] = prices_by_symbol if prices_by_symbol is not None else {}
    if prices_by_symbol is None:
        for row in daily.filter(pl.col("date") <= day).sort("date").iter_rows(named=True):
            prices.setdefault(row["symbol"], {})[row["date"]] = row
    exchanges = ("TWSE", "TPEX") if market == "composite" else (market,)
    candidates = {s for s, bars in prices.items() if day in bars and s.rsplit(".", 1)[-1] in exchanges}
    candidates.update(s for s in verified_observed_stocks if s.rsplit(".", 1)[-1] in exchanges)
    unknown = sum(eligibility.get(s, (None, "unknown"))[1] != "verified" for s in candidates)
    verified = sum(eligibility.get(s, (None, "unknown")) == ("stock", "verified") for s in candidates)
    metrics: dict[str, ResearchMetric] = {}
    signs: list[float] = []
    missing_markets = [ex for ex in exchanges if not any(s.endswith(f".{ex}") for s in candidates)]
    event_windows: dict[date, dict[str, list[CorporateActionEvent]] | None] = {}
    normalized_windows: dict[str, tuple[date, tuple[str, ...], dict[date, dict[str, Any]]]] = {}
    # A verified larger window at the same date anchor can serve a smaller
    # descriptive window. Failed windows never enter this per-request cache.
    for name, length in (("new_high_52w", 0), ("new_low_52w", 0), ("ma240", 240),
                         ("ma60", 60), ("ma20", 20), ("ad_net", 2)):
        values: list[float] = []
        reasons: Counter[str] = Counter()
        for symbol in sorted(candidates):
            kind, type_status = eligibility.get(symbol, (None, "unknown"))
            if type_status == "verified" and kind != "stock":
                reasons["not_ordinary_stock"] += 1
                continue
            exchange = symbol.rsplit(".", 1)[-1]
            calendar = [d for d in sessions.get(exchange, ()) if d <= day]
            bars = prices.get(symbol, {})
            if not calendar or calendar[-1] != day:
                reasons["missing_target_session"] += 1
                continue
            if length:
                expected = calendar[-length:]
                if len(expected) < length:
                    reasons["insufficient_history"] += 1
                    continue
            else:
                # Cover 52 calendar weeks, including the boundary session, plus
                # today's bar. A short IPO window must not claim a yearly high.
                boundary = day - timedelta(weeks=52)
                prior = [d for d in calendar if d <= boundary]
                if not prior:
                    reasons["insufficient_history"] += 1
                    continue
                expected = [d for d in calendar if d >= prior[-1]]
            start = expected[0]
            if any(start <= d <= day for d in unresolved_days.get(exchange, set())):
                reasons["calendar_unverified"] += 1
                continue
            if any(d not in bars for d in expected):
                first = min(bars) if bars else day
                reasons["insufficient_history" if first > start else "missing_session_price"] += 1
                continue
            columns = ("close",) if length else ("high", "low", "close")
            if any(bars[d].get(c) is None or not math.isfinite(float(bars[d][c]))
                   or float(bars[d][c]) <= 0 for d in expected for c in columns):
                reasons["invalid_price"] += 1
                continue
            if start not in event_windows:
                actions = actions_for_window(start, day)
                grouped: dict[str, list[CorporateActionEvent]] = {}
                if actions is not None:
                    for event in actions:
                        if start < event.effective_date <= day:
                            grouped.setdefault(event.symbol, []).append(event)
                event_windows[start] = grouped if actions is not None else None
            grouped_events = event_windows[start]
            if grouped_events is None:
                reasons["corporate_action_coverage_unavailable"] += 1
                continue
            relevant = grouped_events.get(symbol, ())
            window = [bars[d] for d in expected]
            if relevant:
                cached = normalized_windows.get(symbol)
                if cached is not None and cached[0] <= start and set(columns) <= set(cached[1]):
                    window = [cached[2][d] for d in expected]
                else:
                    adjusted = adjust_prices_as_of(pl.DataFrame(window), as_of=day,
                                                   events=relevant, price_columns=columns)
                    if adjusted.status != "verified":
                        reasons["incomparable_corporate_action"] += 1
                        continue
                    window = adjusted.to_frame().sort("date").to_dicts()
                    normalized_windows[symbol] = (start, columns, {r["date"]: r for r in window})
            current = float(window[-1]["close"])
            if name.startswith("ma"):
                values.append(float(current > sum(float(r["close"]) for r in window) / length))
            elif name == "new_high_52w":
                values.append(float(window[-1]["high"] > max(r["high"] for r in window[:-1])))
            elif name == "new_low_52w":
                values.append(float(window[-1]["low"] < min(r["low"] for r in window[:-1])))
            else:
                previous = float(window[-2]["close"])
                values.append(float((current > previous) - (current < previous)))
        metrics[name] = metric(values, reasons, unit="ratio" if name.startswith("ma") else "stocks",
                               ratio=name.startswith("ma"))
        if missing_markets and metrics[name].value is not None:
            metrics[name].status = "partial"
        if name == "ad_net":
            signs = values
    metrics["ad_line"] = metrics["ad_net"].model_copy(update={"value": None, "status": "unavailable"})
    metrics = {key: metrics[key] for key in ("ma20", "ma60", "ma240", "new_high_52w", "new_low_52w", "ad_net", "ad_line")}
    return MarketBreadthStats(
        as_of=day, market=market, source=source or ["taiwan_daily_store"],
        eligibility_retrieved_at=retrieved_at,
        missing_markets=missing_markets,
        historical_eligibility_status="verified" if candidates and not unknown else "unverified",
        eligibility_verified_count=verified, eligibility_unknown_count=unknown, metrics=metrics,
        advances=signs.count(1.0) if signs else None,
        declines=signs.count(-1.0) if signs else None,
        unchanged=signs.count(0.0) if signs else None,
    )


def accumulate_ad_line(history: list[MarketBreadthStats]) -> None:
    """Restart after gaps; never silently carry an A/D line across missing days."""
    running: float | None = None
    segment: date | None = None
    partial = False
    for item in history:
        net = item.metrics["ad_net"]
        if net.value is None:
            running, segment = None, None
            partial = False
        else:
            if running is None:
                running, segment = 0.0, item.as_of
            running += net.value
            partial = partial or net.status != "available"
        item.ad_segment_start = segment
        item.metrics["ad_line"] = net.model_copy(update={
            "value": running, "status": "partial" if partial and running is not None else net.status,
        })
