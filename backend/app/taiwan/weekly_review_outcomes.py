"""Forward outcomes from locally stored official Taiwan market sessions and prices."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import polars as pl

from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.benchmark_store import TaiwanBenchmarkStore
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import TPEX_SOURCE, TWSE_SOURCE, ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.regulatory_history import RegulatoryHistoryStore
from app.taiwan.weekly_selection_review import _official_bars

HORIZONS = (1, 4, 8, 13)
BENCHMARKS = {"TWSE": "TAIEX", "TPEX": "TPEX_INDEX"}
BENCHMARK_SOURCES = {"TAIEX": "twse:MI_5MINS_HIST", "TPEX_INDEX": "tpex:tpex_index"}
DAY = timedelta(days=1)


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _pct(price: float, base: float) -> float:
    return 100.0 * (price - base) / base


class _Bar(NamedTuple):
    close: float | None
    high: float | None
    low: float | None
    volume: float | None

    def valid(self) -> bool:
        return (all(_positive(x) for x in (self.close, self.low, self.high))
                and self.low <= self.close <= self.high and _positive(self.volume))


@dataclass(frozen=True)
class _Week:
    sessions: tuple[date, ...]
    unknown: tuple[date, ...]
    closed: bool

    def last_session(self) -> date | None:
        if self.closed and self.sessions:
            last = self.sessions[-1]
            if not self.unknown or self.unknown[-1] < last:
                return last
        return None


def _outcome(status, benchmark, end=None, flags=()):
    return {
        "status": status, "end_date": end.isoformat() if end else None,
        "benchmark_symbol": benchmark, "return_pct": None, "benchmark_return_pct": None,
        "excess_return_pct": None, "mfe_pct": None, "mae_pct": None,
        "price_basis": None, "flags": list(flags),
    }


class WeeklyOutcomeEvaluator:
    """Load each market partition once per report. Dates are Taipei session dates.

    as_of is inclusive, after that day's official close. An unresolved historical
    session makes an affected horizon unavailable. A still-open week is pending.
    """

    def __init__(self, taiwan_root: Path, start: date, as_of: date, *,
                 calendar: TaiwanTradingCalendar | None = None) -> None:
        if start > as_of:
            raise ValueError("weekly review start must be on or before as_of")
        root = Path(taiwan_root)
        self.as_of = as_of
        self._weeks = {market: {} for market in BENCHMARKS}
        self._bars = defaultdict(dict)
        self._bench = defaultdict(dict)
        self._events = defaultdict(list)
        self._provider_errors = set()
        self._coverage = None
        self._results = {}
        market_trades = defaultdict(set)
        start = _monday(start)
        if (root / "daily").is_dir():
            daily = _official_bars(TaiwanDailyStore(root / "daily"), start, as_of)
            for symbol, day, *values in daily.select(
                "symbol", "date", "close", "high", "low", "volume"
            ).iter_rows():
                self._bars[symbol][day] = _Bar(*values)
                if _positive(values[0]):
                    market_trades[symbol.rsplit(".", 1)[-1]].add(day)

        indices = TaiwanBenchmarkStore(root / "benchmark_index.parquet").read()
        for symbol, day, close, source in indices.select("symbol", "date", "close", "source").iter_rows():
            if (day is not None and start <= day <= as_of and symbol in BENCHMARK_SOURCES
                    and source == BENCHMARK_SOURCES[symbol] and _positive(close)):
                self._bench[symbol][day] = close
        calendar = calendar or TaiwanTradingCalendar()
        census_dir = root / "observed_universe"
        census = ObservedUniverseStore(census_dir) if census_dir.is_dir() else None
        for market, benchmark in BENCHMARKS.items():
            market_trades[market].update(self._bench[benchmark])
            self._load_weeks(start, market, calendar, census, market_trades[market])

        dates = [d for week in self._weeks["TPEX"].values() for d in week.sessions]
        self._suspended = RegulatoryHistoryStore(root / "regulatory_history").suspended_on(dates)
        coverage = CorporateActionStore(root / "adj_factor").read_verified_coverage()
        if coverage:
            self._coverage = coverage[:2]
            for event in coverage[2]:
                if start <= event.effective_date <= as_of:
                    # TWT49U's per-security detail response can fail after the
                    # official daily event list succeeded. Other errors lack
                    # this proof of scope and invalidate the market window.
                    if event.status == "provider_error" and not (
                        event.source == "TWT49U" and event.exchange == "TWSE"
                        and event.reason == "detail_provider_error"
                    ):
                        self._provider_errors.add(event.effective_date)
                    self._events[event.symbol].append(event)

    def _load_weeks(self, start, market, calendar, census, market_trades):
        for offset in range(0, (self.as_of - start).days + 1, 7):
            monday = start + offset * DAY
            sessions, unknown = [], []
            closed = True
            for n in range(7):
                day = monday + n * DAY
                schedule = calendar.day_evidence(day, market)
                if day > self.as_of:
                    if schedule.status != "non_trading":
                        closed = False
                    continue
                evidence = schedule
                if census is not None and census.has(market, day):
                    evidence = census.day_evidence(market, day, calendar=calendar)
                    if evidence.status == "trading":
                        self._load_census_prices(census, market, day)
                    if evidence.status == "unresolved" and schedule.status != "unresolved":
                        evidence = schedule
                conflict = (schedule.reason == "conflicting_calendar_evidence"
                            or (evidence.status == "trading" and day in calendar.known_holidays)
                            or (evidence.status == "non_trading" and evidence.reason != "weekend"
                                and (day in market_trades or day in calendar.known_trading_days)))
                if conflict:
                    unknown.append(day)
                elif evidence.status == "trading" or day in market_trades:
                    sessions.append(day)
                elif evidence.status == "unresolved":
                    unknown.append(day)
            self._weeks[market][monday] = _Week(tuple(sessions), tuple(unknown), closed)

    def _load_census_prices(self, census, market, day):
        # Older daily partitions lack row provenance. The exchange census retains
        # the original dated OHLCV for those sessions, including its official source.
        path = census.partition_path(market, day)
        columns = ["date", "exchange", "source", "observed", "raw_code", "close", "high", "low", "volume"]
        rows = pl.read_parquet(path, columns=columns, hive_partitioning=False)
        if rows.filter((pl.col("date") != day).fill_null(True)
                       | (pl.col("exchange") != market).fill_null(True)).height:
            raise ValueError("census_partition_date_or_market_mismatch")
        source = TWSE_SOURCE if market == "TWSE" else TPEX_SOURCE
        rows = rows.filter(pl.col("observed") & (pl.col("source") == source))
        for code, *values in rows.select("raw_code", "close", "high", "low", "volume").iter_rows():
            if code:
                history = self._bars[f"{code}.{market}"]
                census_bar = _Bar(*values)
                if day not in history or (not history[day].valid() and census_bar.valid()):
                    history[day] = census_bar

    def week_close(self, day: date, market: str) -> date | None:
        week = self._weeks.get(market, {}).get(_monday(day))
        return week.last_session() if week else None

    def official_close(self, symbol: str, day: date) -> float | None:
        bar = self._bars.get(symbol, {}).get(day)
        return bar.close if bar and _positive(bar.close) and _positive(bar.volume) else None

    def evaluate(self, symbol: str, start_day: date, reference_close: float | None) -> dict[int, dict]:
        """Return 1/4/8/13-week outcomes in percentage points from this frozen close."""
        cache_key = symbol, start_day, reference_close
        if cache_key in self._results:
            return self._results[cache_key]
        market = symbol.rsplit(".", 1)[-1]
        benchmark = BENCHMARKS.get(market)
        weeks = [w for monday, w in self._weeks.get(market, {}).items()
                 if monday > start_day and w.closed and (w.sessions or w.unknown)]
        result = {}
        for horizon in HORIZONS:
            if benchmark is None:
                outcome = _outcome("unavailable", None, flags=["unknown_market"])
            elif len(weeks) < horizon:
                outcome = _outcome("pending", benchmark)
            else:
                window = weeks[:horizon]
                end = window[-1].last_session()
                days = [day for w in window for day in w.sessions]
                if self.week_close(start_day, market) != start_day or any(w.unknown for w in window):
                    outcome = _outcome("unavailable", benchmark, end, ["session_calendar_incomplete"])
                else:
                    outcome = self._measure(symbol, start_day, reference_close, days, benchmark)
                    indices = self._bench[benchmark]
                    if start_day in indices and end in indices:
                        index_return = _pct(indices[end], indices[start_day])
                        outcome["benchmark_return_pct"] = index_return
                        if outcome["return_pct"] is not None:
                            outcome["excess_return_pct"] = outcome["return_pct"] - index_return
                    else:
                        outcome["flags"].append("benchmark_unavailable")
            result[horizon] = outcome
        self._results[cache_key] = result
        return result

    def _measure(self, symbol, start, reference, days, benchmark):
        end = days[-1]
        flags = []
        base = self.official_close(symbol, start)
        if not _positive(reference) or base is None:
            flags.append("reference_close_unavailable")
        elif not math.isclose(base, reference, rel_tol=1e-8):
            flags.append("reference_close_mismatch")
        rows = self._bars.get(symbol, {})
        if any(d not in rows or not rows[d].valid() for d in days):
            flags.append("missing_official_ohlcv")
        code, market = symbol.rsplit(".", 1)
        if market == "TPEX" and any(code in self._suspended.get(d, ()) for d in days):
            flags.append("suspended")
        events = [e for e in self._events.get(symbol, ()) if start < e.effective_date <= end]
        if (self._coverage is None or self._coverage[0] > days[0] or self._coverage[1] < end
                or any(start < d <= end for d in self._provider_errors)):
            flags.append("corporate_action_coverage_unverified")
        if events and ("corporate_action_coverage_unverified" in flags or any(e.status != "verified" for e in events)):
            flags.append("corporate_action_unadjusted")
        if flags:
            return _outcome("unavailable", benchmark, end, flags)

        held = [rows[d] for d in days]
        price_basis = "verified_raw_no_action"
        if events:
            # The entry day's high and low precede this trade. Only its closing
            # price is a required observation, while each held bar needs its range.
            entry = pl.DataFrame({"date": [start], "symbol": [symbol], "close": [base]})
            adjusted_entry = adjust_prices_as_of(entry, as_of=end, events=events, price_columns=("close",))
            frame = pl.DataFrame([{"date": d, "symbol": symbol, "close": rows[d].close,
                                   "high": rows[d].high, "low": rows[d].low} for d in days])
            adjusted = adjust_prices_as_of(frame, as_of=end, events=events,
                                           price_columns=("close", "high", "low"))
            if adjusted_entry.status != "verified" or adjusted.status != "verified":
                return _outcome("unavailable", benchmark, end, ["corporate_action_unadjusted"])
            base = adjusted_entry.to_frame()["close"][0]
            held = [_Bar(close, high, low, None) for close, high, low in
                    adjusted.to_frame().select("close", "high", "low").iter_rows()]
            price_basis = "pit_adjusted"

        result = _outcome("completed", benchmark, end)
        result.update(return_pct=_pct(held[-1].close, base), mfe_pct=_pct(max(b.high for b in held), base),
                      mae_pct=_pct(min(b.low for b in held), base), price_basis=price_basis)
        return result
