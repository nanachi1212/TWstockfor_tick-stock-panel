"""Forward-only tracking of the daily chip ranking (籌碼排行追蹤).

Same principle as ``advice_tracking``: a ranking is frozen once, with its
recording time, and is never rebuilt for a past date (TDCC weeks and the
universe are not point-in-time, see ``chip_ranking``). Outcomes are derived
from stored daily bars at read time.

Return semantics (gross, no fees or tax): the ranking for session D is only
known after D's close, so entry is the open of the first stored session after
D, and the 1/5-session exits are that session's close and the 5th session's
close. Prices are adjusted with the verified corporate-action snapshot. The
benchmark is 0050.TWSE over the same entry/exit. A snapshot recorded after the
entry session's open is ``late`` and excluded. A pick without a bar, or without
a verified adjustment, has no return (never 0).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.adjust import adjust_prices_as_of
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "0050.TWSE"
HORIZONS = (1, 5)
MIN_DAYS = 20  # matured ranking days before averages are shown
MARKET_OPEN = time(9, 0)
_LOCK = threading.Lock()


def _default_path() -> Path:
    from app.taiwan.data_root import taiwan_data_root

    return taiwan_data_root() / "chip_ranking_tracking.jsonl"


class ChipRankingTracker:
    def __init__(self, *, path: Path | None = None, daily: TaiwanDailyStore | None = None,
                 actions: CorporateActionStore | None = None) -> None:
        self.path = path or _default_path()
        self.daily = daily or TaiwanDailyStore()
        self.actions = actions or CorporateActionStore()

    def records(self) -> list[dict[str, Any]]:
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
        with _LOCK:
            if any(r.get("date") == snapshot["date"] for r in self.records()):
                return {"status": "skipped", "reason": "already_frozen", "date": snapshot["date"]}
            record = {"date": snapshot["date"], "recorded_at": (recorded_at or taipei_now()).isoformat(),
                      "weights": snapshot.get("weights"), "picks": picks}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"status": "frozen", "date": snapshot["date"], "picks": len(picks)}

    def report(self) -> dict[str, Any]:
        records = sorted(self.records(), key=lambda r: r["date"], reverse=True)
        sessions = self.daily.available_dates()
        coverage = self.actions.read_verified_coverage()
        days = [self._evaluate(r, sessions, coverage) for r in records]
        summary = {}
        for h in HORIZONS:
            done = [d[f"h{h}"] for d in days if d["status"] == "tracked" and d[f"h{h}"]["status"] == "available"]
            picks = [p for d in days if d["status"] == "tracked" for p in d["picks"] if p.get(f"return_{h}") is not None]
            enough = len(done) >= MIN_DAYS
            summary[f"h{h}"] = {
                "days": len(done), "picks": len(picks), "sample_sufficient": enough,
                "avg_return": _mean([d["avg_return"] for d in done]) if enough else None,
                "avg_benchmark": _mean([d["benchmark_return"] for d in done]) if enough else None,
                "avg_excess": _mean([d["avg_excess"] for d in done]) if enough else None,
                "win_rate": _mean([p[f"return_{h}"] > 0 for p in picks]) if enough else None,
                "beat_rate": _mean([p[f"excess_{h}"] > 0 for p in picks]) if enough else None,
            }
        return {"benchmark_symbol": BENCHMARK_SYMBOL, "min_days": MIN_DAYS, "summary": summary, "days": days}

    def _evaluate(self, record: dict[str, Any], sessions: list[date], coverage) -> dict[str, Any]:
        ranked = date.fromisoformat(record["date"])
        # ponytail: stored daily partitions stand in for the trading calendar; a missing
        # partition would shift the horizon. Daily update backfills gaps, so it should not happen.
        after = [s for s in sessions if s > ranked]
        out: dict[str, Any] = {"date": record["date"], "recorded_at": record["recorded_at"],
                               "entry_date": after[0].isoformat() if after else None, "status": "pending",
                               "picks": [dict(p) for p in record["picks"]]}
        for h in HORIZONS:
            out[f"h{h}"] = {"status": "pending", "exit_date": None}
        recorded = datetime.fromisoformat(record["recorded_at"])
        if after and recorded >= datetime.combine(after[0], MARKET_OPEN, tzinfo=TAIPEI):
            out["status"] = "late"
            return out
        if not after:
            return out
        out["status"] = "tracked"
        symbols = [p["symbol"] for p in record["picks"]] + [BENCHMARK_SYMBOL]
        for h in HORIZONS:
            if len(after) < h:
                continue
            entry, exit_ = after[0], after[h - 1]
            if coverage is None or not (coverage[0] <= entry and exit_ <= coverage[1]):
                out[f"h{h}"] = {"status": "unavailable", "reason": "corporate_action_coverage", "exit_date": exit_.isoformat()}
                continue
            returns = self._returns(symbols, entry, exit_, coverage[2])
            bench = returns.get(BENCHMARK_SYMBOL)
            for p in out["picks"]:
                r = returns.get(p["symbol"])
                p[f"return_{h}"] = r
                p[f"excess_{h}"] = r - bench if r is not None and bench is not None else None
            valid = [p for p in out["picks"] if p[f"excess_{h}"] is not None]
            if bench is None or not valid:
                out[f"h{h}"] = {"status": "unavailable", "reason": "price_data", "exit_date": exit_.isoformat()}
                continue
            out[f"h{h}"] = {"status": "available", "exit_date": exit_.isoformat(), "picks": len(valid),
                            "avg_return": _mean([p[f"return_{h}"] for p in valid]),
                            "benchmark_return": bench,
                            "avg_excess": _mean([p[f"excess_{h}"] for p in valid])}
        return out

    def _returns(self, symbols: list[str], entry: date, exit_: date, events) -> dict[str, float | None]:
        raw = self.daily.read_range(symbols, entry, exit_).filter(pl.col("date").is_in([entry, exit_]))
        window = tuple(e for e in events if entry < e.effective_date <= exit_)
        out: dict[str, float | None] = {}
        for symbol in symbols:
            rows = raw.filter(pl.col("symbol") == symbol).unique("date").sort("date")
            if rows.filter(pl.col("date") == entry).is_empty() or rows.filter(pl.col("date") == exit_).is_empty():
                out[symbol] = None
                continue
            adjusted = adjust_prices_as_of(rows, as_of=exit_, events=window, price_columns=("open", "close"))
            if adjusted.status != "verified":
                out[symbol] = None
                continue
            frame = adjusted.to_frame()
            buy = frame.filter(pl.col("date") == entry)["open"][0]
            sell = frame.filter(pl.col("date") == exit_)["close"][0]
            out[symbol] = sell / buy - 1 if buy and buy > 0 and sell is not None else None
        return out


def _mean(values: list[float] | list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def freeze_latest_ranking() -> dict[str, Any]:
    """Post-close hook: freeze today's ranking for later forward evaluation."""
    from app.taiwan.chip_ranking import TaiwanChipRankingService

    return ChipRankingTracker().freeze(TaiwanChipRankingService().snapshot())
