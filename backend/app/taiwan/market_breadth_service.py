"""Local research snapshot; no HTTP, strategy admission or current-master join."""
# ruff: noqa: RUF001 -- Traditional Chinese product explanations.
from __future__ import annotations

from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.benchmark_store import TaiwanBenchmarkStore
from app.taiwan.corporate_actions import CorporateActionEvent, CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.fundamentals import TaiwanFundamentalStore
from app.taiwan.market_breadth import (
    BreadthWindowCache,
    MarketBreadthStats,
    accumulate_ad_line,
    calculate_breadth,
)
from app.taiwan.market_valuation import MarketValuationStats, calculate_valuation
from app.taiwan.pit_universe import PitUniverse
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now

Market = Literal["TWSE", "TPEX", "composite"]
Sections = Literal["breadth", "valuation", "all"]


class BreadthValuationResponse(BaseModel):
    contract_version: Literal[1] = 1
    generated_at: str
    requested_as_of: date
    as_of: date | None = None
    market: Market
    sections: Sections = "all"
    status: str
    stale: bool = False
    history: list[MarketBreadthStats] = Field(default_factory=list)
    latest: MarketBreadthStats | None = None
    valuation: MarketValuationStats | None = None
    warnings: list[str] = Field(default_factory=list)
    usage_scope: Literal["descriptive_history"] = "descriptive_history"
    strategy_lab_eligible: Literal[False] = False


def fundamental_store() -> TaiwanFundamentalStore:
    from app.config import settings

    return TaiwanFundamentalStore(Path(settings.data_dir))


class MarketBreadthValuationService:
    def __init__(self, *, daily: TaiwanDailyStore | None = None,
                 universe: PitUniverse | None = None,
                 benchmarks: TaiwanBenchmarkStore | None = None,
                 actions: CorporateActionStore | None = None,
                 fundamentals: TaiwanFundamentalStore | None = None,
                 calendar: TaiwanTradingCalendar | None = None) -> None:
        self.daily = daily or TaiwanDailyStore()
        self.universe = universe or PitUniverse()
        self.benchmarks = benchmarks or TaiwanBenchmarkStore()
        self.actions = actions or CorporateActionStore()
        self.fundamentals = fundamentals or fundamental_store()
        self.calendar = calendar or TaiwanTradingCalendar()

    def snapshot(self, as_of: date | None = None, market: Market = "composite",
                 days: int = 20, sections: Sections = "all") -> BreadthValuationResponse:
        requested = as_of or taipei_now().date()
        if not 1 <= days <= 60:
            raise ValueError("history must be between 1 and 60 sessions")
        if sections not in {"breadth", "valuation", "all"}:
            raise ValueError("unsupported research sections")
        if sections == "valuation":
            return self._valuation_snapshot(requested, market, explicit_date=as_of is not None)
        start = requested - timedelta(days=550)
        prices = self.daily.read_range(None, start, requested)
        price_index: dict[str, dict[date, dict[str, Any]]] = {}
        for row in prices.iter_rows(named=True):
            price_index.setdefault(row["symbol"], {})[row["date"]] = row
        benchmark = self.benchmarks.read().filter(pl.col("date") <= requested)
        exchanges = ("TWSE", "TPEX") if market == "composite" else (market,)
        sessions: dict[str, list[date]] = {}
        unresolved: dict[str, set[date]] = {}
        for exchange in exchanges:
            index_symbol = "TAIEX" if exchange == "TWSE" else "TPEX_INDEX"
            dates = set(prices.filter(pl.col("symbol").str.ends_with(f".{exchange}"))["date"].to_list())
            dates.update(benchmark.filter(pl.col("symbol") == index_symbol)["date"].to_list())
            dates.update(self.universe.sessions(exchange, start, requested))
            sessions[exchange] = sorted(d for d in dates if start <= d <= requested)
            unresolved[exchange] = set()
            cursor = start
            while cursor <= requested:
                if cursor not in dates:
                    evidence = self.universe.census.day_evidence(exchange, cursor, calendar=self.calendar)
                    if evidence.status == "trading":
                        sessions[exchange].append(cursor)
                    elif evidence.status == "unresolved":
                        unresolved[exchange].add(cursor)
                cursor += timedelta(days=1)
            sessions[exchange] = sorted(set(sessions[exchange]))
        targets = sorted(set(d for dates in sessions.values() for d in dates))[-days:]
        warnings = ["依可觀測日 K 計算的研究統計；已驗證個股資格仍不證明完整歷史普通股 universe。",
                    "無可驗證首次發布時間，僅供描述性歷史研究，不可接 Strategy Lab。",
                    "估值為個股中位數；composite 合併同交易日樣本，不等於官方指數本益比。",
                    "A/D Line 以本次查詢首個可比較交易日為起點，資料中斷後另起區段。"]
        response = BreadthValuationResponse(generated_at=taipei_now().isoformat(),
                                           requested_as_of=requested, market=market,
                                           sections=sections, status="unavailable", warnings=warnings)
        if not targets:
            return response

        action_coverage = self.actions.read_verified_coverage()

        @cache
        def actions_for_window(first: date, last: date) -> tuple[CorporateActionEvent, ...] | None:
            if action_coverage is None or action_coverage[0] > first or action_coverage[1] < last:
                return None
            events = tuple(e for e in action_coverage[2] if first <= e.effective_date <= last)
            # A covered source with an unusable identified event invalidates
            # that symbol only; normalization owns the per-symbol exclusion.
            return events

        census = self.universe.census.read_range(None, targets[0], targets[-1])
        classification = self.universe.classification.read()
        window_cache = BreadthWindowCache()
        for day in targets:
            truth = self.universe.as_of(day, observed=census, classification=classification)
            eligibility = {r["market_symbol"]: (r["instrument_type"], r["instrument_type_status"])
                           for r in truth.iter_rows(named=True)}
            verified_stocks = [s for s, state in eligibility.items() if state == ("stock", "verified")]
            stamps = census.filter(pl.col("date") == day)["retrieved_at"].drop_nulls().to_list()
            response.history.append(calculate_breadth(
                prices, day=day, market=market, sessions=sessions,
                prices_by_symbol=price_index,
                window_cache=window_cache,
                eligibility=eligibility, verified_observed_stocks=verified_stocks,
                unresolved_days=unresolved, actions_for_window=actions_for_window,
                source=["taiwan_daily_store", "historical_classification", "official_daily_snapshot",
                        "taiwan_benchmark_store", "corporate_action_store"],
                retrieved_at=max(stamps, default=None),
            ))
        accumulate_ad_line(response.history)
        response.latest = response.history[-1]
        response.as_of = targets[-1]
        # As-of is never silently substituted: old snapshots remain visibly stale.
        response.stale = (as_of is not None and response.as_of != requested) or (
            as_of is None and (requested - response.as_of).days > 4)
        expected = set(prices.filter(pl.col("date") == response.as_of)["symbol"].to_list())
        if sections == "all":
            response.valuation = calculate_valuation(self.fundamentals.load(), day=response.as_of,
                                                    market=market, expected_symbols=expected)
        metrics = list(response.latest.metrics.values())
        if response.valuation is not None:
            metrics += list(response.valuation.metrics.values())
        response.status = "unavailable" if all(m.value is None for m in metrics) else (
            "partial" if any(m.status != "available" for m in metrics) else "available")
        return response

    def _valuation_snapshot(self, requested: date, market: Market, *,
                            explicit_date: bool) -> BreadthValuationResponse:
        """Valuation reads saved fundamentals and one raw session, never breadth."""
        records = self.fundamentals.load()
        exchanges = {"TWSE", "TPEX"} if market == "composite" else {market}
        dates = [date.fromisoformat(r.period_end) for r in records
                 if r.dataset == "valuation" and r.period_end <= requested.isoformat()
                 and r.symbol.rsplit(".", 1)[-1] in exchanges]
        target = requested if explicit_date else max(dates, default=None)
        response = BreadthValuationResponse(
            generated_at=taipei_now().isoformat(), requested_as_of=requested, as_of=target,
            market=market, sections="valuation", status="unavailable",
            stale=target is not None and not explicit_date and (requested - target).days > 4,
            warnings=["無可驗證首次發布時間，僅供描述性歷史研究，不可接 Strategy Lab。",
                      "估值為同交易日個股中位數，不等於官方指數本益比。"])
        if target is not None:
            prices = self.daily.read_range(None, target, target)
            response.valuation = calculate_valuation(records, day=target, market=market,
                                                    expected_symbols=set(prices["symbol"].to_list()))
            response.status = response.valuation.status
        return response
