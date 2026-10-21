"""Market money-flow heatmap (資金熱浪), ported from EquiLens 衡析 post-close edition (MIT).

One session: industry tiles sized by turnover (TWD) and colored by the turnover-weighted
raw-close change against the previous research session. ETFs form one "ETF" tile so the
total covers every supported active instrument. A missing previous row stays None, never 0.
The snapshot is "available" only when the official census proves the day's rows complete;
otherwise it is "partial" with a coverage report. Current-universe view, not a Historical PIT evaluation.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.research_metrics import research_sessions
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

CHANGE_COLUMNS = ["symbol", "date", "close", "volume", "amount", "change_pct"]
STOCK_FIELDS = ["symbol", "name", "close", "change_pct", "amount", "volume"]
INDUSTRY_FIELDS = ["industry", "amount", "share", "change_pct", "advance", "decline", "flat", "count", "stocks"]
EXCHANGES = ("TWSE", "TPEX")
MISSING_SAMPLE = 20
#: day_coverage status -> snapshot (status, reason); only a census-proven complete day is "available".
SNAPSHOT_STATUS = {
    "complete": ("available", None),
    "partial": ("partial", "incomplete_daily_coverage"),
    "unknown": ("partial", "census_unknown"),
}


def day_coverage(daily_rows: pl.DataFrame, universe: pl.DataFrame, census: ObservedUniverseStore,
                 day: date) -> dict[str, Any]:
    """Whether ``daily_rows`` hold every ``universe`` symbol the official census listed on ``day``.

    A census code outside ``universe`` (warrant, unsupported type) is not expected, and a universe
    symbol the census did not list (suspended) is not missing. An exchange with universe symbols whose
    census partition is absent or not an observed session makes the day unknown, never complete.
    """
    present = daily_rows.filter(pl.col("date") == day).select("symbol").unique()
    exchanges: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for exchange in EXCHANGES:
        listed = universe.select("symbol").filter(pl.col("symbol").str.ends_with(f".{exchange}"))
        if listed.is_empty():
            continue
        # partition_status falls back to the calendar when no partition exists, so check has() first.
        state = census.partition_status(exchange, day) if census.has(exchange, day) else "missing"
        if state != "observed":
            exchanges[exchange] = {"census": state, "expected": None,
                                   "observed": listed.join(present, on="symbol", how="semi").height, "missing": None}
            continue
        seen = census.read_range(exchange, day, day).select((pl.col("raw_code") + f".{exchange}").alias("symbol"))
        expected = listed.join(seen, on="symbol", how="semi")
        absent = expected.join(present, on="symbol", how="anti")["symbol"].to_list()
        exchanges[exchange] = {"census": state, "expected": expected.height,
                               "observed": expected.height - len(absent), "missing": len(absent)}
        missing += absent
    status = ("unknown" if any(e["expected"] is None for e in exchanges.values())
              else "partial" if missing else "complete")
    return {"status": status, "exchanges": exchanges,
            "missing_symbols": sorted(missing)[:MISSING_SAMPLE], "missing_count": len(missing)}


def previous_coverage_status(daily_rows: pl.DataFrame, universe: pl.DataFrame, census: ObservedUniverseStore,
                             previous: date | None, available: set[date]) -> str | None:
    """day_coverage status of the previous session; "unavailable" when it has no daily partition."""
    if previous is None:
        return None
    if previous not in available:
        return "unavailable"
    return day_coverage(daily_rows, universe, census, previous)["status"]


def session_changes(daily: TaiwanDailyStore, symbols: list[str], sessions: list[date]) -> pl.DataFrame:
    """Rows of the ascending ``sessions`` with change_pct = close / previous-session close - 1 (decimal).

    None on the first session, when the stock has no row in the immediately previous session
    (an earlier session is never used instead), or when either close is not a positive number.
    """
    # ponytail: raw close, so ex-dividend days read as a drop; use adjusted close once a factor source is wired in.
    order = pl.DataFrame({"date": sessions, "_session": range(len(sessions))},
                         schema={"date": pl.Date, "_session": pl.Int64})
    rows = daily.read_range(symbols, sessions[0], sessions[-1]).join(order, on="date").sort("symbol", "_session")
    previous_close = pl.col("close").shift().over("symbol")
    usable = ((pl.col("_session").shift().over("symbol") == pl.col("_session") - 1)
              & previous_close.is_finite() & (previous_close > 0)
              & pl.col("close").is_finite() & (pl.col("close") > 0))
    return rows.with_columns(
        pl.when(usable).then(pl.col("close") / previous_close - 1).alias("change_pct"),
    ).select(CHANGE_COLUMNS)


class TaiwanMarketHeatmapService:
    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        evidence_store: ObservedUniverseStore | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.security_master = security_master or get_security_master()
        self.evidence_store = evidence_store or ObservedUniverseStore()

    def snapshot(self, target_date: date | None = None) -> dict[str, Any]:
        target = target_date or resolve_target_latest_trading_date(self.calendar, evidence_store=self.evidence_store)
        available = set(self.daily_store.available_dates())
        out: dict[str, Any] = {
            "date": str(target), "previous_date": None, "status": "unavailable", "reason": "no_daily_data",
            "price_semantics": "raw_close", "coverage": None, "total_amount": 0.0, "previous_total_amount": None,
            "advance": 0, "decline": 0, "flat": 0, "uncompared": 0, "industries": [],
        }
        if target not in available:
            return out
        sessions = research_sessions(target, 2, available, self.calendar, self.evidence_store)
        previous = sessions[0] if len(sessions) == 2 else None

        industry = pl.col("industry")
        universe = self.security_master.to_dataframe(supported_only=True).filter(
            (pl.col("listing_status") == "active") & pl.col("instrument_type").is_in(["stock", "etf"])
        ).select(
            "symbol", "name",
            pl.when(pl.col("instrument_type") == "etf").then(pl.lit("ETF"))
            .when(industry.is_null() | (industry.str.strip_chars() == "")).then(pl.lit("UNCLASSIFIED"))
            .otherwise(industry).alias("industry"),
        )
        rows = session_changes(self.daily_store, universe["symbol"].to_list(), sessions)
        # A partial day still reports its numbers; status and coverage say they are not the whole market.
        coverage = day_coverage(rows, universe, self.evidence_store, target)
        coverage["previous_status"] = previous_coverage_status(rows, universe, self.evidence_store, previous, available)
        traded = rows.filter(pl.col("amount").is_finite() & (pl.col("amount") > 0))
        today = traded.filter(pl.col("date") == target).join(universe, on="symbol")
        total = float(today["amount"].sum())

        change, amount = pl.col("change_pct"), pl.col("amount")
        compared_amount = amount.filter(change.is_not_null()).sum()
        industries = today.group_by("industry").agg(
            amount.sum(),
            pl.when(compared_amount > 0).then((change * amount).sum() / compared_amount).alias("change_pct"),
            (change > 0).sum().alias("advance"),
            (change < 0).sum().alias("decline"),
            (change == 0).sum().alias("flat"),
            pl.len().alias("count"),
            pl.struct(STOCK_FIELDS).sort_by("amount", "symbol", descending=[True, False]).alias("stocks"),
        ).with_columns((amount / total).alias("share")).sort(["amount", "industry"], descending=[True, False])

        status, reason = SNAPSHOT_STATUS[coverage["status"]]
        out.update(
            previous_date=str(previous) if previous else None, status=status, reason=reason, coverage=coverage,
            total_amount=total,
            previous_total_amount=float(traded.filter(pl.col("date") == previous)["amount"].sum())
            if coverage["previous_status"] == "complete" else None,
            advance=int((today["change_pct"] > 0).sum()), decline=int((today["change_pct"] < 0).sum()),
            flat=int((today["change_pct"] == 0).sum()), uncompared=today["change_pct"].null_count(),
            industries=industries.select(INDUSTRY_FIELDS).to_dicts(),
        )
        return out
