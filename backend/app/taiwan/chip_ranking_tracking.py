"""Forward-only tracking of the daily chip ranking (籌碼排行追蹤).

Same principle as ``advice_tracking``: a ranking is frozen once, with its
recording time and definition digest, and is never rebuilt for a past date
(TDCC weeks and the universe are not point-in-time, see ``chip_ranking``).
Outcomes are derived from stored daily bars at read time.

Return semantics (gross, no fees or tax): the ranking for session D is only
known after D's close, so entry is the open of the next trading-calendar
session after D, and the 1/5-session exits are that session's close and the 5th
session's close. Prices are adjusted with the verified corporate-action
snapshot. The 0050 benchmark stays unavailable until ETF distributions are verified.

Fail-closed: a snapshot recorded after the entry session's open is ``late``; a
horizon is ``unavailable`` unless every pick has a bar on the exact calendar
entry/exit dates (no survivor averages, no shifted dates). Complete stock
returns are ``partial`` because benchmark-relative metrics are unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import polars as pl

from app.file_lock import file_lock
from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "0050.TWSE"
HORIZONS = (1, 5)
MIN_DAYS = 20  # matured ranking days before averages are shown
DETAIL_DAYS = 20  # newest days returned with per-pick detail; the summary uses all days
MARKET_OPEN = time(9, 0)
DEFINITION_VERSION = "chip_ranking_v1"


def _default_path() -> Path:
    from app.taiwan.data_root import taiwan_data_root

    return taiwan_data_root() / "chip_ranking_tracking.jsonl"


def _default_next_session() -> Callable[[date], date | None]:
    from app.taiwan.daily_update import next_market_session
    from app.taiwan.observed_universe import ObservedUniverseStore
    from app.taiwan.realtime.calendar import TaiwanTradingCalendar

    calendar, evidence = TaiwanTradingCalendar(), ObservedUniverseStore()
    return lambda day: next_market_session(day, calendar, evidence)


def ranking_definition(snapshot: dict[str, Any]) -> dict[str, Any]:
    from app.taiwan.chip_ranking import TOP_N

    return {"version": DEFINITION_VERSION, "weights": snapshot.get("weights"), "top_n": TOP_N,
            "min_avg_volume_5d": snapshot.get("min_avg_volume_5d"), "min_close": snapshot.get("min_close")}


def _digest(definition: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


class ChipRankingTracker:
    def __init__(self, *, path: Path | None = None, daily: TaiwanDailyStore | None = None,
                 actions: CorporateActionStore | None = None,
                 next_session: Callable[[date], date | None] | None = None) -> None:
        self.path = path or _default_path()
        self.daily = daily or TaiwanDailyStore()
        self.actions = actions or CorporateActionStore()
        self._next_session = next_session

    def records(self) -> list[dict[str, Any]]:
        with file_lock(self.path.with_suffix(".lock")):
            if not self.path.exists():
                return []
            out = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    logger.warning("chip ranking tracking: skipped unreadable line")
            return out

    def freeze(self, snapshot: dict[str, Any], *, recorded_at: datetime | None = None) -> dict[str, Any]:
        """Append the snapshot's top lists once per ranking date; a repeat call is a no-op."""
        picks = [{"symbol": r["symbol"], "name": r.get("name"), "exchange": exchange, "rank": i + 1,
                  "chip_score": r["chip_score"]}
                 for exchange in ("TWSE", "TPEX") for i, r in enumerate(snapshot.get(exchange) or [])]
        if not picks:
            return {"status": "skipped", "reason": "empty_ranking", "date": snapshot.get("date")}
        definition = ranking_definition(snapshot)
        with file_lock(self.path.with_suffix(".lock")):
            if any(r.get("date") == snapshot["date"] for r in self.records()):
                return {"status": "skipped", "reason": "already_frozen", "date": snapshot["date"]}
            record = {"date": snapshot["date"], "recorded_at": (recorded_at or taipei_now()).isoformat(),
                      "definition": definition, "definition_digest": _digest(definition), "picks": picks}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"status": "frozen", "date": snapshot["date"], "picks": len(picks)}

    def report(self) -> dict[str, Any]:
        records = sorted(self.records(), key=lambda r: r["date"], reverse=True)
        empty = {h: {"days": 0, "picks": 0, "sample_sufficient": False, "avg_return": None, "avg_benchmark": None,
                     "avg_excess": None, "win_rate": None, "beat_rate": None} for h in map(_key, HORIZONS)}
        out: dict[str, Any] = {"benchmark_symbol": BENCHMARK_SYMBOL, "min_days": MIN_DAYS, "summary": empty,
                               "definition_digest": None, "other_definition_days": 0, "days": []}
        if not records:
            return out
        next_session = self._next_session or _default_next_session()
        latest = self.daily.latest_date()
        coverage = self.actions.read_verified_coverage()
        plans = [self._plan(r, next_session) for r in records]
        spans = [(p[0], p[-1]) for p in plans if p]
        prices = None
        if latest is not None and spans:
            symbols = sorted({p["symbol"] for r in records for p in r["picks"]})
            frame = self.daily.read_range(symbols, min(s[0] for s in spans), min(max(s[1] for s in spans), latest))
            prices = {(r["symbol"], r["date"]): r for r in frame.select("symbol", "date", "open", "close").iter_rows(named=True)}
        days = [self._evaluate(r, plan, latest, coverage, prices) for r, plan in zip(records, plans, strict=True)]
        current = records[0].get("definition_digest")
        mine = [d for d in days if d["definition_digest"] == current]
        out.update(definition_digest=current, other_definition_days=len(days) - len(mine), days=days[:DETAIL_DAYS])
        for h in HORIZONS:
            k = _key(h)
            done = [d[k] for d in mine if d["status"] == "tracked" and d[k]["status"] in {"available", "partial"}]
            picks = [p for d in mine if d["status"] == "tracked" and d[k]["status"] in {"available", "partial"}
                     for p in d["picks"] if p.get(f"return_{h}") is not None]
            enough = len(done) >= MIN_DAYS
            out["summary"][k] = {
                "days": len(done), "picks": len(picks), "sample_sufficient": enough,
                "avg_return": _mean([d["avg_return"] for d in done]) if enough else None,
                "avg_benchmark": None,
                "avg_excess": None,
                "win_rate": _mean([p[f"return_{h}"] > 0 for p in picks]) if enough else None,
                "beat_rate": None,
            }
        return out

    @staticmethod
    def _plan(record: dict[str, Any], next_session: Callable[[date], date | None]) -> list[date]:
        """Calendar sessions after the ranking date, as many as the longest horizon needs."""
        sessions: list[date] = []
        cur = next_session(date.fromisoformat(record["date"]))
        while cur is not None and len(sessions) < max(HORIZONS):
            sessions.append(cur)
            cur = next_session(cur)
        return sessions

    def _evaluate(self, record: dict[str, Any], plan: list[date], latest: date | None, coverage,
                  prices: dict[tuple[str, date], dict[str, Any]] | None) -> dict[str, Any]:
        out: dict[str, Any] = {"date": record["date"], "recorded_at": record["recorded_at"],
                               "definition_digest": record.get("definition_digest"),
                               "entry_date": plan[0].isoformat() if plan else None, "status": "pending",
                               "picks": [dict(p) for p in record["picks"]]}
        for h in HORIZONS:
            out[_key(h)] = {"status": "pending", "exit_date": None}
        if not plan:
            return out
        recorded = datetime.fromisoformat(record["recorded_at"])
        if recorded >= datetime.combine(plan[0], MARKET_OPEN, tzinfo=TAIPEI):
            out["status"] = "late"
            return out
        if latest is None or prices is None or plan[0] > latest:
            return out
        out["status"] = "tracked"
        entry = plan[0]
        for h in HORIZONS:
            k = _key(h)
            if len(plan) < h or plan[h - 1] > latest:
                continue
            exit_ = plan[h - 1]
            unavailable = {"status": "unavailable", "exit_date": exit_.isoformat()}
            if coverage is None or not (coverage[0] <= entry and exit_ <= coverage[1]):
                out[k] = {**unavailable, "reason": "corporate_action_coverage"}
                continue
            symbols = [p["symbol"] for p in record["picks"]]
            returns = self._returns(symbols, entry, exit_, coverage[2], prices)
            for p in out["picks"]:
                r = returns.get(p["symbol"])
                p[f"return_{h}"] = r
                p[f"excess_{h}"] = None
            valid = [p for p in out["picks"] if p[f"return_{h}"] is not None]
            if not valid:
                out[k] = {**unavailable, "reason": "price_data"}
            elif len(valid) < len(out["picks"]):
                out[k] = {**unavailable, "reason": "incomplete_picks"}  # no survivor-only averages
            else:
                # shortcut: ETF distribution coverage is unavailable; enable comparison only with a verified source.
                out[k] = {"status": "partial", "reason": "benchmark_distribution_unverified",
                          "exit_date": exit_.isoformat(), "picks": len(valid),
                          "avg_return": _mean([p[f"return_{h}"] for p in valid]), "benchmark_return": None,
                          "avg_excess": None}
        return out

    @staticmethod
    def _returns(symbols: list[str], entry: date, exit_: date, events,
                 prices: dict[tuple[str, date], dict[str, Any]]) -> dict[str, float | None]:
        window = tuple(e for e in events if entry < e.effective_date <= exit_)
        out: dict[str, float | None] = {}
        for symbol in symbols:
            bars = [prices.get((symbol, d)) for d in sorted({entry, exit_})]
            if any(r is None or any(r[c] is None or not math.isfinite(r[c]) or r[c] <= 0
                                   for c in ("open", "close")) for r in bars):
                out[symbol] = None
                continue
            symbol_events = tuple(e for e in window if e.symbol == symbol)
            if symbol_events:
                adjusted = adjust_prices_as_of(pl.DataFrame(bars), as_of=exit_, events=symbol_events, price_columns=("open", "close"))
                if adjusted.status != "verified":
                    out[symbol] = None
                    continue
                bars = adjusted.to_frame().to_dicts()
            value = bars[-1]["close"] / bars[0]["open"] - 1
            out[symbol] = value if math.isfinite(value) else None
        return out


def _key(horizon: int) -> str:
    return f"h{horizon}"


def _mean(values: list[float] | list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def freeze_latest_ranking(tracker: ChipRankingTracker | None = None) -> dict[str, Any]:
    """Post-close hook: freeze the ranking only once its margin input is published (evening).

    Without margin data the ranking silently drops a component and renormalizes, which is a
    different score than the final one users see, so the freeze waits; the evening catch-ups retry.
    """
    from app.taiwan.chip_ranking import TaiwanChipRankingService
    from app.taiwan.margin_store import TaiwanMarginStore

    snapshot = TaiwanChipRankingService().snapshot()
    if not snapshot.get("date") or date.fromisoformat(snapshot["date"]) not in TaiwanMarginStore().available_dates():
        return {"status": "skipped", "reason": "margin_pending", "date": snapshot.get("date")}
    return (tracker or ChipRankingTracker()).freeze(snapshot)
