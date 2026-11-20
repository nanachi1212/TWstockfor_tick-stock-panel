"""Group weekly observations of daily strategies and summarize their four-week returns."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from functools import cache
from itertools import groupby
from math import isclose, isfinite
from statistics import fmean

Evaluate = Callable[[str, date, float | None], dict[int, dict]]
SAMPLE_WARNING = "樣本不足，僅供參考"


@dataclass(frozen=True)
class _Observation:
    symbol: str
    pattern: str
    week: str
    day: date
    decision: str
    close: float | None
    reason: str | None
    execution_session: str | None
    execution_open_status: str
    executable_reference_open: float | None

    @property
    def week_number(self) -> int:
        # ISO weeks start on Monday; date.toordinal() == 1 on Monday 0001-01-01.
        return (self.day.toordinal() - 1) // 7


def _meets_threshold(value: float, threshold: float) -> bool:
    # Decimal exchange prices can produce 7.99999999999999% at an exact 8% boundary.
    return value >= threshold or isclose(value, threshold, rel_tol=0.0, abs_tol=1e-12)


def _classification(result: dict, neutral_pct: float) -> str:
    status = result["status"]
    if status in ("pending", "unavailable"):
        if result.get("return_pct") is not None:
            raise ValueError("Unevaluable weekly return must be null")
        return status
    value = result.get("return_pct")
    if status != "completed" or value is None or not isfinite(value):
        raise ValueError("Completed weekly outcome needs a finite return")
    if value == 0 or not _meets_threshold(abs(value), neutral_pct):
        return "NEUTRAL"
    return "WIN" if value > 0 else "LOSS"


def _observations(records: Iterable[dict]) -> tuple[dict, dict, int]:
    by_strategy: dict[str, dict[tuple[str, str, str], _Observation]] = defaultdict(dict)
    names: dict[str, str] = {}
    duplicates = 0
    for record in records:
        strategy = record["source_strategy"]
        strategy_id = strategy["id"]
        names[strategy_id] = strategy["name"]
        day = date.fromisoformat(str(record["last_trading_day"]))
        if record["week"] != day.strftime("%G-W%V"):
            raise ValueError(f"Weekly record date and ISO week disagree: {record['week']} {day}")
        if record["decision"] not in ("signal", "no_signal", "watch"):
            raise ValueError(f"Unknown weekly decision: {record['decision']}")
        row = _Observation(record["symbol"], record["pattern_tag"], record["week"], day,
                           record["decision"], record["reference_close"], record.get("reason"),
                           record.get("execution_session"), record.get("execution_open_status", "not_recorded"),
                           record.get("executable_reference_open"))
        key = (row.symbol, row.pattern, row.week)
        existing = by_strategy[strategy_id].get(key)
        if existing is None:
            by_strategy[strategy_id][key] = row
        elif row == existing:
            duplicates += 1
        else:
            raise ValueError(f"Conflicting weekly decisions for {strategy_id} {key}")
    return by_strategy, names, duplicates


def _signal_groups(rows: list[_Observation], strategy_id: str, evaluate: Evaluate,
                   max_gap_weeks: int, neutral_pct: float) -> list[dict]:
    groups = []
    signals = sorted((r for r in rows if r.decision == "signal"), key=lambda r: (r.symbol, r.pattern, r.day))
    for (symbol, pattern), picks in groupby(signals, key=lambda r: (r.symbol, r.pattern)):
        previous_week = None
        for pick in picks:
            if previous_week is None or pick.week_number - previous_week > max_gap_weeks:
                outcomes = evaluate(symbol, pick.day, pick.close)
                groups.append({
                    "strategy_id": strategy_id, "symbol": symbol, "pattern_tag": pattern,
                    "start_week": pick.week, "start_day": pick.day.isoformat(), "reference_close": pick.close,
                    "execution_session": pick.execution_session, "execution_open_status": pick.execution_open_status,
                    "executable_reference_open": pick.executable_reference_open,
                    "weeks": [], "outcomes": outcomes, "classification": _classification(outcomes[4], neutral_pct),
                })
            groups[-1]["weeks"].append(pick.week)
            previous_week = pick.week_number
    return groups


def _summary(strategy_id: str, name: str, groups: list[dict], missed_count: int | None) -> dict:
    counts = Counter(g["classification"] for g in groups)
    returns = [g["outcomes"][4]["return_pct"] for g in groups if g["outcomes"][4]["status"] == "completed"]
    count = len(returns)
    # Equal-weight realized expectancy includes the actual returns of neutral groups.
    # This empirical estimator is the mean over completed groups, in percentage points.
    mean = fmean(returns) if returns else None
    return {
        "strategy_id": strategy_id, "strategy_name": name,
        "raw_signal_count": sum(len(g["weeks"]) for g in groups), "group_count": len(groups),
        "completed_count": count, "pending_count": counts["pending"], "unavailable_count": counts["unavailable"],
        "win_count": counts["WIN"], "neutral_count": counts["NEUTRAL"], "loss_count": counts["LOSS"],
        "win_rate_pct": 100 * counts["WIN"] / count if count else None,
        "mean_return_pct": mean, "expectancy_pct": mean,
        # Signed worst loss. A fully non-losing sample has zero loss; an empty sample stays null.
        "max_loss_pct": min(0.0, min(returns)) if count else None,
        "missed_count": missed_count, "sample_warning": SAMPLE_WARNING if count < 30 else None,
    }


def build_weekly_review(records: Iterable[dict], evaluate: Evaluate, *, max_gap_weeks: int = 2,
                        neutral_pct: float = 1.0, missed_pct: float = 8.0,
                        strategy_names: dict[str, str] | None = None) -> dict:
    """Evaluate each signal group once, using its first week as the entry reference.

    Rates and thresholds are percentage points. The win-rate denominator includes every
    evaluable four-week signal group. MISSED counts distinct no_signal decisions per week.
    """
    if isinstance(max_gap_weeks, bool) or not isinstance(max_gap_weeks, int) or max_gap_weeks < 0:
        raise ValueError("max_gap_weeks must be a nonnegative integer")
    if not all(isfinite(n) and n >= 0 for n in (neutral_pct, missed_pct)):
        raise ValueError("Weekly return thresholds must be finite and nonnegative")
    by_strategy, names, duplicates = _observations(records)
    # A saved formal batch can contain zero picks and no known earlier drops.
    names = (strategy_names or {}) | names
    evaluate_once = cache(evaluate)
    strategies, groups, missed = [], [], []
    for strategy_id in sorted(names):
        rows = list(by_strategy[strategy_id].values())
        grouped = _signal_groups(rows, strategy_id, evaluate_once, max_gap_weeks, neutral_pct)
        missed_before = len(missed)
        no_signal_counts: Counter[str] = Counter()
        for row in sorted((r for r in rows if r.decision == "no_signal"), key=lambda r: (r.day, r.symbol, r.pattern)):
            outcome = evaluate_once(row.symbol, row.day, row.close)[4]
            _classification(outcome, neutral_pct)
            no_signal_counts[outcome["status"]] += 1
            if outcome["status"] == "completed" and _meets_threshold(outcome["return_pct"], missed_pct):
                missed.append({
                    "strategy_id": strategy_id, "symbol": row.symbol, "pattern_tag": row.pattern,
                    "week": row.week, "start_day": row.day.isoformat(), "reason": row.reason,
                    "reference_close": row.close, "outcome": outcome, "classification": "MISSED",
                })
        groups.extend(grouped)
        measured_missed = len(missed) - missed_before if not no_signal_counts or no_signal_counts["completed"] else None
        summary = _summary(strategy_id, names[strategy_id], grouped, measured_missed)
        summary.update(no_signal_count=no_signal_counts.total(), no_signal_completed_count=no_signal_counts["completed"],
                       no_signal_pending_count=no_signal_counts["pending"], no_signal_unavailable_count=no_signal_counts["unavailable"])
        strategies.append(summary)
    return {"strategies": strategies, "groups": groups, "missed": missed, "duplicate_count": duplicates}
