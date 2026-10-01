"""Current institutional statistics over canonical stores; no estimated amounts."""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Literal

import polars as pl
from pydantic import BaseModel

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.enrichment.factors import INVESTORS, compute_institutional_window
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.research_metrics import ResearchMetric, research_metric, research_sessions
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

InstitutionalWindow = Literal[5, 10, 20, 45, 60]
VALID_FLOW_STATUSES = ["official", "available"]


class InvestorStatistics(BaseModel):
    net_shares: ResearchMetric
    net_lots: ResearchMetric
    net_volume_ratio: ResearchMetric
    buy_streak: ResearchMetric
    sell_streak: ResearchMetric
    streak_capped: bool


class InstitutionalStatisticsRow(BaseModel):
    symbol: str
    name: str
    exchange: str
    investors: dict[str, InvestorStatistics]


class InstitutionalStatisticsSnapshot(BaseModel):
    date: str
    window: InstitutionalWindow
    sessions: list[str]
    universe: str = "active_supported_stocks_and_etfs"
    aggregates: list[InstitutionalStatisticsRow]
    securities: list[InstitutionalStatisticsRow]


class TaiwanInstitutionalStatisticsService:
    def __init__(
        self, institutional_store: TaiwanInstitutionalStore | None = None,
        daily_store: TaiwanDailyStore | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        evidence_store: ObservedUniverseStore | None = None,
    ) -> None:
        self.store = institutional_store or TaiwanInstitutionalStore()
        self.daily = daily_store or TaiwanDailyStore()
        self.master = security_master or get_security_master()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.evidence = evidence_store or ObservedUniverseStore()

    def get_snapshot(
        self, target_date: date | None = None, window: InstitutionalWindow = 5,
    ) -> InstitutionalStatisticsSnapshot:
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        known = set(self.store.available_dates()) | set(self.daily.available_dates())
        sessions = research_sessions(target, window, known, self.calendar, self.evidence)
        master = self.master.to_dataframe(supported_only=True).filter(
            (pl.col("listing_status") == "active")
            & pl.col("instrument_type").is_in(["stock", "etf"])
        )
        symbols = master["symbol"].to_list()
        flows = self.store.read_range(symbols, sessions[0], target)
        daily = self.daily.read_range(symbols, sessions[0], target)
        if not flows.is_empty():
            flows = flows.filter(
                pl.col("date").is_in(sessions)
                & pl.col("status").is_in(VALID_FLOW_STATUSES)
                & (pl.col("trade_date") == pl.col("date"))
                & ~pl.col("has_discrepancy").fill_null(False)
            )
        if not daily.is_empty():
            daily = daily.filter(pl.col("date").is_in(sessions))
        complete_dates: set[date] = set()
        # Presence of a date partition alone never proves exchange completeness.
        if symbols and not flows.is_empty():
            valid = flows.filter(pl.all_horizontal(
                [pl.col(f"{i}_net").is_not_null() for i in INVESTORS]
            ))
            counts = valid.group_by("date").agg(pl.col("symbol").n_unique().alias("n"))
            both_exchanges = set(master["exchange"].unique().to_list()) == {"TWSE", "TPEX"}
            if both_exchanges:
                complete_dates = set(counts.filter(pl.col("n") == len(symbols))["date"].to_list())

        grouped_flows = {key[0]: frame for key, frame in flows.partition_by("symbol", as_dict=True).items()} if not flows.is_empty() else {}
        grouped_daily = {key[0]: frame for key, frame in daily.partition_by("symbol", as_dict=True).items()} if not daily.is_empty() else {}
        securities = [self._row(
            r["symbol"], r["name"], r["exchange"], [r["symbol"]],
            grouped_flows.get(r["symbol"], pl.DataFrame()),
            grouped_daily.get(r["symbol"], pl.DataFrame()), sessions, target, complete_dates,
        ) for r in master.sort("symbol").iter_rows(named=True)]
        aggregates = []
        for exchange in ("ALL", "TWSE", "TPEX"):
            scope = symbols if exchange == "ALL" else master.filter(pl.col("exchange") == exchange)["symbol"].to_list()
            sub = flows.filter(pl.col("symbol").is_in(scope)) if not flows.is_empty() else flows
            prices = daily.filter(pl.col("symbol").is_in(scope)) if not daily.is_empty() else daily
            aggregates.append(self._row(exchange, exchange, exchange, scope, sub, prices, sessions, target, complete_dates))
        return InstitutionalStatisticsSnapshot(
            date=str(target), window=window, sessions=[str(d) for d in sessions],
            aggregates=aggregates, securities=securities,
        )

    def _row(
        self, symbol: str, name: str, exchange: str, symbols: list[str],
        flows: pl.DataFrame, daily: pl.DataFrame, sessions: list[date],
        target: date, complete_dates: set[date],
    ) -> InstitutionalStatisticsRow:
        daily_aggregate = flows if len(symbols) == 1 else flows.group_by("date").agg([
            pl.when(pl.col(f"{i}_net").count() > 0).then(pl.col(f"{i}_net").sum())
            .otherwise(None).alias(f"{i}_net") for i in INVESTORS
        ]) if not flows.is_empty() else pl.DataFrame()
        calculated = compute_institutional_window(daily_aggregate, sessions, complete_dates)
        flow_rows = flows.to_dicts()
        volumes = {(r["symbol"], r["date"]): float(r["volume"]) for r in daily.iter_rows(named=True)
                   if r.get("volume") is not None and isfinite(float(r["volume"])) and r["volume"] >= 0}
        sources = sorted({r["source"] for r in flow_rows if r.get("source")}) or ["taiwan_institutional_store"]
        investors = {}
        for investor in INVESTORS:
            stats = calculated[investor]
            valid = [r for r in flow_rows if r.get(f"{investor}_net") is not None]
            days = stats["coverage_dates"]
            complete = set(sessions) <= complete_dates
            net = research_metric(stats["net_shares"], "shares", target, sessions, days, len(symbols), len(valid), sources, complete=complete)
            lots = net.model_copy(update={"value": net.value / 1000 if net.value is not None else None, "unit": "lots"})
            # Numerator and denominator use exactly the same security-date pairs.
            matched = [r for r in valid if (r["symbol"], r["date"]) in volumes]
            volume = sum(volumes[(r["symbol"], r["date"])] for r in matched)
            ratio = sum(r[f"{investor}_net"] for r in matched) / volume if volume > 0 else None
            ratio_dates = {r["date"] for r in matched}
            ratio_metric = research_metric(ratio, "ratio", target, sessions, ratio_dates, len(symbols), len(matched), [*sources, "taiwan_daily_store:volume_shares"], complete=complete)
            streak_args = (target, sessions, days, len(symbols), len(valid), sources)
            investors[investor] = InvestorStatistics(
                net_shares=net, net_lots=lots, net_volume_ratio=ratio_metric,
                buy_streak=research_metric(stats["buy_streak"], "sessions", *streak_args, complete=complete),
                sell_streak=research_metric(stats["sell_streak"], "sessions", *streak_args, complete=complete),
                streak_capped=stats["streak_capped"],
            )
        return InstitutionalStatisticsRow(symbol=symbol, name=name, exchange=exchange, investors=investors)
