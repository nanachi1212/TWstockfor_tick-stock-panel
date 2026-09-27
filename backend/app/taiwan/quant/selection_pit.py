"""Historical point-in-time evaluation of the frozen ``trend_liquidity_v1`` selector.

The question answered: had v1 run after the close of historical session ``t``,
using only evidence that existed then, what would it have selected, and how did
those picks score under the formal forward definition (next-session open entry,
entry day = 1D, exact 5D/20D sessions, corporate-action normalized price return)?

Strict reproduction needs evidence for *every* v1 rule on that session:

* the ordinary-stock universe of both exchanges (A2a census + verified subtype);
* verified trading sessions for the 20-session trend window;
* verified corporate-action coverage for PIT price normalization;
* the regulatory status (disposition / suspension / delisting) known before the
  entry session.

A session that lacks any of it is excluded under a named blocker. Nothing is
relaxed, and an excluded session never produces picks or returns. Missing
evidence is never read as "no risk".

``degraded_diagnostics`` only counts how many verified-type TWSE stocks pass the
price/liquidity/trend rules, to describe coverage. It carries no picks and no
returns, and it is kept apart from the strict result.

Selection reuses the live screener's pure functions and return scoring reuses the
forward review's ``_paper_return``; this module owns no second v1 rule. Results
go to their own ledger, never to the forward snapshot file.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.corporate_actions import CorporateActionEvent, CorporateActionStore
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore
from app.taiwan.quant.live_contract import canonical_hash, canonical_json
from app.taiwan.regulatory_history import REGULATORY_BLOCKERS
from app.taiwan.screener import (
    TREND_LIQUIDITY_V1_BATCH_SIZE,
    TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD,
    TREND_LIQUIDITY_V1_TREND_SESSIONS,
    compute_trend_liquidity_v1_indicators,
    rank_trend_liquidity_v1,
    trend_liquidity_v1_candidates,
)
from app.taiwan.selection_review_service import (
    DEFAULT_BENCHMARK_SYMBOL,
    FORWARD_PRICE_ADJUSTMENT,
    FORWARD_RULE_VERSION,
    TaiwanSelectionReviewService,
)

ARTIFACT_TYPE = "historical_pit_selection_evaluation"

#: Session-level reasons a v1 selection cannot be reproduced. Each is evidence
#: that is missing, never a statement about the market.
BLOCKERS: dict[str, str] = {
    "tpex_instrument_subtype_blocked":
        "TPEx historical ordinary-stock subtype has no official point-in-time source",
    "twse_instrument_subtype_unresolved":
        "an observed TWSE security with a price bar has no verified historical subtype",
    "regulatory_history_unavailable":
        "no point-in-time disposition/suspension/delisting record for the entry session",
    "corporate_action_coverage_unavailable":
        "verified corporate-action coverage does not span the 20-session trend window",
    "corporate_action_unverified":
        "an unverified corporate action blocks PIT normalization (live lock would refuse)",
    "trading_day_unverified":
        "a weekday inside the trend window has no trading/non-trading evidence",
    "entry_session_not_observed":
        "no verified trading session exists yet after the source session",
    "twse_market_session_unobserved":
        "the TWSE census has no observed membership for this session",
    "tpex_market_session_unobserved":
        "the TPEx census has no observed membership for this session",
    **REGULATORY_BLOCKERS,
}

#: v1 screens listed and OTC stocks together; both must be evidenced each session.
V1_EXCHANGES = ("TWSE", "TPEX")


@dataclass(frozen=True)
class RegulatoryEvidence:
    """Regulatory status that was on record before each entry session."""

    source: str
    covered_targets: frozenset[date] = frozenset()
    excluded_by_target: Mapping[date, frozenset[str]] = field(default_factory=dict)
    #: Specific missing evidence per entry session; a target absent from both this
    #: and ``covered_targets`` has no regulatory history at all.
    blockers_by_target: Mapping[date, tuple[str, ...]] = field(default_factory=dict)

    def blockers_for(self, target: date | None) -> tuple[str, ...]:
        if target is not None and target in self.covered_targets:
            return ()
        if target is not None and self.blockers_by_target.get(target):
            return tuple(self.blockers_by_target[target])
        return ("regulatory_history_unavailable",)


NO_REGULATORY_HISTORY = RegulatoryEvidence(
    source=("none: the event pipeline only stores current OpenAPI snapshots; "
            "no historical disposition/suspension archive exists"),
)


@dataclass(frozen=True)
class TrendLiquidityV1PitSpec:
    """Pinned methodology; any change requires a version bump."""

    version: str = "trend-liquidity-v1-historical-pit-2"
    strategy_id: str = FORWARD_RULE_VERSION
    horizons: tuple[int, ...] = (1, 5, 20)
    primary_cohort: int = 10
    rank_groups: tuple[tuple[int, int], ...] = ((1, 3), (1, 5), (1, 10), (11, 20))
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL

    def __post_init__(self) -> None:
        if self.strategy_id != "trend_liquidity_v1" or self.horizons != (1, 5, 20):
            raise ValueError("historical evaluation is pinned to trend_liquidity_v1 1D/5D/20D")

    def describe(self) -> dict[str, Any]:
        return {
            "spec_version": self.version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_id,
            "selection_rules": {
                "universe": "listed (TWSE) and OTC (TPEx) ordinary stocks observed that session",
                "min_turnover_twd": TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD,
                "turnover_basis": "raw official traded value of the source session",
                "trend": "PIT adjusted close > PIT adjusted MA20",
                "momentum": "PIT adjusted 5-session momentum > 0",
                "trend_window_sessions": TREND_LIQUIDITY_V1_TREND_SESSIONS,
                "regulatory_exclusion": (
                    "disposition published on or before the source session whose period "
                    "covers the entry session; TPEx suspension in the status list dated on "
                    "the source session; TWSE termination effective on or before the entry "
                    "session within the live two-calendar-year window"),
                "ranking": "momentum_5d desc, then turnover desc, then symbol asc",
                "batch_size": TREND_LIQUIDITY_V1_BATCH_SIZE,
                "primary_cohort": self.primary_cohort,
                "short_batch": "keep the actual count when fewer than batch_size qualify",
                "implementation": "app.taiwan.screener pure functions shared with the live preset",
            },
            "universe_contract": {
                "twse": "A2a observed membership + A2b type evidence at first observation",
                "tpex": "A2a observed membership; historical subtype BLOCKED (probe §9.4)",
                "forbidden": ["current security master", "current listing status",
                              "current regulatory status", "fundamentals"],
            },
            "price_semantics": {
                "price_adjustment": FORWARD_PRICE_ADJUSTMENT,
                "source": "A2a official daily observations (delisted history included)",
                "total_return": False,
                "costs_and_slippage": "not deducted",
                "features": "adjusted as of the source session close; later actions never apply",
            },
            "entry": "open of the next verified trading session after the source close",
            "horizon_definitions": {
                "1": "entry session close (entry day counts as 1D)",
                "5": "close of the 5th verified session counting the entry session as 1",
                "20": "close of the 20th verified session counting the entry session as 1",
                "missing_price": "unavailable; a later session is never substituted",
            },
            "benchmark": {
                "symbol": self.benchmark_symbol,
                "semantics": "same entry open and horizon close, same price normalization",
            },
            "baselines": {
                "included": [self.benchmark_symbol],
                "excluded_same_universe_baseline": (
                    "the repository has no existing fair same-universe baseline; "
                    "quant.baseline is the factor composite, a different strategy"),
            },
            "regime_breakdown": "not included: requires a strict sample",
            "strict_reproducibility": {
                "required_evidence": sorted(BLOCKERS),
                "policy": "any blocker excludes the session; no relaxation",
            },
            "metrics": {
                "unit": "percent",
                "hit_rate": "share of completed picks with return > 0",
                "beat_benchmark_rate": "share of picks with excess return > 0",
                "pooling": "pick-level across strict sessions",
                "rank_groups": [f"{low}-{high}" for low, high in self.rank_groups],
                "stability": "calendar year of the source session",
            },
            "degraded_diagnostics": ("candidate counts on the verified-type TWSE subset "
                                     "before regulatory exclusion; no picks, no returns"),
        }

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.describe())


TREND_LIQUIDITY_V1_PIT_SPEC = TrendLiquidityV1PitSpec()


@dataclass(frozen=True)
class HistoricalPitInputs:
    """Evidence available to the evaluation; every field is historical PIT data."""

    #: Verified trading sessions, ascending (official month tables).
    sessions: tuple[date, ...]
    #: Raw official daily rows: symbol, date, open, close, amount.
    prices: pl.DataFrame
    #: Observed rows: date, market_symbol, exchange, instrument_type,
    #: instrument_type_status, price_bar_available.
    universe: pl.DataFrame
    events: tuple[CorporateActionEvent, ...]
    #: Verified five-source coverage span, or None.
    action_coverage: tuple[date, date] | None
    regulatory: RegulatoryEvidence = NO_REGULATORY_HISTORY
    #: Exchanges with no historical subtype source; they block every session,
    #: whether or not their census observed it.
    blocked_exchanges: frozenset[str] = frozenset()
    #: Weekdays without trading or non-trading evidence.
    unresolved_days: frozenset[date] = frozenset()
    identity: Mapping[str, Any] = field(default_factory=dict)


def _by_date(frame: pl.DataFrame) -> dict[date, pl.DataFrame]:
    if frame.is_empty():
        return {}
    parts = frame.partition_by("date", as_dict=True)
    return {(key[0] if isinstance(key, tuple) else key): part for key, part in parts.items()}


def _valid_price(value: object) -> bool:
    return value is not None and math.isfinite(float(value)) and float(value) > 0


class _Evidence:
    """Indexed views over the inputs so each session reads only its window."""

    def __init__(self, inputs: HistoricalPitInputs) -> None:
        sessions = list(inputs.sessions)
        if sessions != sorted(set(sessions)):
            raise ValueError("sessions must be sorted and unique")
        required = {"symbol", "date", "open", "close", "amount"}
        if not required <= set(inputs.prices.columns):
            raise ValueError("historical prices require symbol, date, open, close, amount")
        if inputs.prices.select(pl.struct("symbol", "date").is_duplicated().any()).item():
            raise ValueError("duplicate historical symbol/session price")
        self.inputs = inputs
        self.sessions = sessions
        self.prices = _by_date(inputs.prices.select(
            "symbol", "date", *(pl.col(c).cast(pl.Float64) for c in ("open", "close", "amount"))))
        self.universe = _by_date(inputs.universe)
        self.events = sorted(inputs.events, key=lambda e: (e.effective_date, e.symbol))
        self.event_dates = [e.effective_date for e in self.events]
        self.unresolved = sorted(inputs.unresolved_days)
        self._rows: dict[date, dict[str, dict[str, Any]]] = {}

    def events_between(self, start: date, end: date) -> list[CorporateActionEvent]:
        low = bisect.bisect_left(self.event_dates, start)
        high = bisect.bisect_right(self.event_dates, end)
        return self.events[low:high]

    def action_window(self, start: date, end: date) -> list[CorporateActionEvent] | None:
        """Same contract as ``CorporateActionStore.read_verified_window``."""
        coverage = self.inputs.action_coverage
        if coverage is None or coverage[0] > start or coverage[1] < end:
            return None
        events = self.events_between(start, end)
        return None if any(e.status == "provider_error" for e in events) else events

    def unresolved_between(self, start: date, end: date) -> bool:
        index = bisect.bisect_left(self.unresolved, start)
        return index < len(self.unresolved) and self.unresolved[index] <= end

    def window_prices(self, window: Sequence[date]) -> pl.DataFrame:
        parts = [self.prices[day] for day in window if day in self.prices]
        return pl.concat(parts) if parts else pl.DataFrame(
            schema={"symbol": pl.String, "date": pl.Date, "open": pl.Float64,
                    "close": pl.Float64, "amount": pl.Float64})

    def row(self, day: date, symbol: str) -> dict[str, Any] | None:
        if day not in self._rows:
            frame = self.prices.get(day)
            self._rows[day] = ({r["symbol"]: r for r in frame.iter_rows(named=True)}
                               if frame is not None else {})
        return self._rows[day].get(symbol)


def _select(evidence: _Evidence, index: int) -> dict[str, Any]:
    """Blockers and the v1 candidate ranking over the verified-type universe."""
    sessions = evidence.sessions
    source = sessions[index]
    window = sessions[index - TREND_LIQUIDITY_V1_TREND_SESSIONS + 1:index + 1]
    target = sessions[index + 1] if index + 1 < len(sessions) else None
    blockers: list[str] = []
    universe = evidence.universe.get(source)
    if universe is None or universe.is_empty():
        raise ValueError(f"no observed universe for verified session {source}")
    observed_exchanges = set(universe["exchange"].to_list())
    for exchange in V1_EXCHANGES:
        if exchange in evidence.inputs.blocked_exchanges:
            blockers.append(f"{exchange.lower()}_instrument_subtype_blocked")
        elif exchange not in observed_exchanges:
            blockers.append(f"{exchange.lower()}_market_session_unobserved")
    unverified = universe.filter(pl.col("instrument_type_status") != "verified")
    if "price_bar_available" in unverified.columns:
        unverified = unverified.filter(pl.col("price_bar_available"))
    for exchange in sorted(set(unverified["exchange"].to_list())):
        blockers.append(f"{exchange.lower()}_instrument_subtype_unresolved")
    if target is None:
        blockers.append("entry_session_not_observed")
    blockers.extend(evidence.inputs.regulatory.blockers_for(target))
    # A lost weekday before the next observed session would make that session a
    # wrong entry date, so the check runs through the entry session.
    if evidence.unresolved_between(window[0], target or source):
        blockers.append("trading_day_unverified")
        return {"source": source, "target": target, "blockers": blockers, "candidates": None}
    events = evidence.action_window(window[0], source)
    if events is None:
        blockers.append("corporate_action_coverage_unavailable")
        return {"source": source, "target": target, "blockers": blockers, "candidates": None}

    admitted = set(universe.filter(
        (pl.col("instrument_type_status") == "verified") & (pl.col("instrument_type") == "stock")
    )["market_symbol"].to_list())
    today = evidence.prices.get(source)
    if today is None:
        raise ValueError(f"no official prices for verified session {source}")
    today = today.filter(pl.col("symbol").is_in(admitted))
    # Only a symbol meeting the raw turnover floor can be selected, and only an
    # unverified action can turn the window "partial"; skipping every other symbol
    # leaves both the ranking and the lock status identical to a full pass.
    liquid = set(today.filter(pl.col("amount") >= TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD)["symbol"].to_list())
    liquid |= {e.symbol for e in events if e.status != "verified" and e.symbol in admitted}
    hist = evidence.window_prices(window).filter(pl.col("symbol").is_in(liquid))
    indicators, status, _ = compute_trend_liquidity_v1_indicators(
        hist, window, [e for e in events if e.symbol in liquid], source)
    if status == "partial":
        blockers.append("corporate_action_unverified")
    if indicators is None or indicators.is_empty():
        candidates = today.head(0)
    else:
        combined = today.join(indicators, on="symbol", how="inner").rename(
            {"trend_ma20": "ma20", "trend_momentum_5d": "momentum_5d"})
        candidates = rank_trend_liquidity_v1(trend_liquidity_v1_candidates(combined, source))
    return {"source": source, "target": target, "blockers": blockers,
            "candidates": candidates, "adjustment_status": status}


def _horizon(evidence: _Evidence, index: int, horizon: int, symbol: str) -> dict[str, Any]:
    sessions = evidence.sessions
    end_index = index + horizon
    if end_index >= len(sessions):
        return {"status": "pending", "return_pct": None, "reason": "horizon_not_reached"}
    entry, end = sessions[index + 1], sessions[end_index]
    if evidence.unresolved_between(sessions[index] + timedelta(days=1), end):
        return {"status": "unavailable", "return_pct": None, "reason": "trading_day_unverified"}
    events = evidence.action_window(entry, end)
    if events is None:
        return {"status": "unavailable", "return_pct": None,
                "reason": "corporate_action_coverage_unavailable"}
    entry_row, end_row = evidence.row(entry, symbol), evidence.row(end, symbol)
    if entry_row is None or not _valid_price(entry_row["open"]):
        return {"status": "unavailable", "return_pct": None, "reason": "missing_entry_open"}
    if end_row is None or not _valid_price(end_row["close"]):
        return {"status": "unavailable", "return_pct": None, "reason": "missing_horizon_close"}
    value = TaiwanSelectionReviewService._paper_return(
        symbol, entry, end, entry_row, end_row, [e for e in events if e.symbol == symbol])
    if value is None:
        return {"status": "unavailable", "return_pct": None, "reason": "price_adjustment_unavailable"}
    return {"status": "completed", "return_pct": value, "reason": None}


def _score_picks(evidence: _Evidence, index: int, picks: pl.DataFrame,
                 spec: TrendLiquidityV1PitSpec) -> list[dict[str, Any]]:
    benchmark = {h: _horizon(evidence, index, h, spec.benchmark_symbol) for h in spec.horizons}
    rows = []
    for rank, pick in enumerate(picks.iter_rows(named=True), start=1):
        row: dict[str, Any] = {
            "source_session": evidence.sessions[index].isoformat(),
            "entry_session": evidence.sessions[index + 1].isoformat(),
            "rank": rank, "symbol": pick["symbol"],
            "momentum_5d": pick["momentum_5d"], "amount": pick["amount"],
            "adjusted_close": pick["trend_adjusted_close"], "ma20": pick["ma20"],
        }
        for horizon in spec.horizons:
            result = _horizon(evidence, index, horizon, pick["symbol"])
            bench = benchmark[horizon]
            excess = (result["return_pct"] - bench["return_pct"]
                      if result["status"] == "completed" and bench["status"] == "completed"
                      else None)
            row[f"h{horizon}d"] = {
                **result,
                "benchmark_status": bench["status"],
                "benchmark_return_pct": bench["return_pct"],
                "benchmark_reason": bench["reason"],
                "excess_return_pct": excess,
            }
        rows.append(row)
    return rows


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pct(count: int, total: int) -> float | None:
    return count / total * 100 if total else None


def horizon_metrics(picks: Sequence[Mapping[str, Any]], horizon: int) -> dict[str, Any]:
    """Pick-level metrics; every value is None, never 0, when there is no sample."""
    cells = [pick[f"h{horizon}d"] for pick in picks]
    returns = [c["return_pct"] for c in cells if c["status"] == "completed"]
    bench = [c["benchmark_return_pct"] for c in cells
             if c["status"] == "completed" and c["benchmark_status"] == "completed"]
    excess = [c["excess_return_pct"] for c in cells if c["excess_return_pct"] is not None]
    return {
        "n": len(returns),
        "pending": sum(c["status"] == "pending" for c in cells),
        "unavailable": sum(c["status"] == "unavailable" for c in cells),
        "hit_rate_pct": _pct(sum(v > 0 for v in returns), len(returns)),
        "avg_return_pct": _mean(returns),
        "median_return_pct": statistics.median(returns) if returns else None,
        "benchmark_n": len(bench),
        "avg_benchmark_return_pct": _mean(bench),
        "excess_n": len(excess),
        "avg_excess_return_pct": _mean(excess),
        "median_excess_return_pct": statistics.median(excess) if excess else None,
        "beat_benchmark_rate_pct": _pct(sum(v > 0 for v in excess), len(excess)),
    }


def _distribution(counts: Sequence[int]) -> dict[str, Any] | None:
    if not counts:
        return None
    ordered = sorted(counts)
    quantiles = statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) > 1 \
        else [float(ordered[0])] * 3
    return {
        "sessions": len(ordered), "min": ordered[0], "p25": quantiles[0],
        "median": statistics.median(ordered), "p75": quantiles[2], "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
        "sessions_below_batch_size": sum(c < TREND_LIQUIDITY_V1_BATCH_SIZE for c in ordered),
        "sessions_with_zero": sum(c == 0 for c in ordered),
    }


def _ranges(records: Sequence[tuple[date, tuple[str, ...]]]) -> list[dict[str, Any]]:
    """Run-length encode consecutive sessions sharing the same blocker set."""
    ranges: list[dict[str, Any]] = []
    for day, blockers in records:
        if ranges and ranges[-1]["blockers"] == list(blockers):
            ranges[-1]["end"] = day.isoformat()
            ranges[-1]["sessions"] += 1
        else:
            ranges.append({"start": day.isoformat(), "end": day.isoformat(),
                           "sessions": 1, "blockers": list(blockers)})
    return ranges


def _strict_result(picks: list[dict[str, Any]], counts: list[int], sessions: list[str],
                   spec: TrendLiquidityV1PitSpec) -> dict[str, Any]:
    if not sessions:
        return {
            "status": "no_strict_sample",
            "claimable": False,
            "strict_sessions": 0,
            "picks": 0,
            "message": ("No historical session had point-in-time evidence for every "
                        "trend_liquidity_v1 rule, so no v1 historical performance exists. "
                        "This reflects missing evidence, not a zero return."),
            "candidate_count_distribution": None,
            "top10": None, "full_batch": None, "rank_groups": None, "by_year": None,
        }

    def block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {str(h): horizon_metrics(rows, h) for h in spec.horizons}

    top = [p for p in picks if p["rank"] <= spec.primary_cohort]
    # Years and session counts come from the strict sessions themselves, so a
    # strict session with no candidates still counts in its year.
    sessions_by_year: dict[str, int] = {}
    for day in sessions:
        sessions_by_year[day[:4]] = sessions_by_year.get(day[:4], 0) + 1
    return {
        "status": "available",
        "claimable": True,
        "strict_sessions": len(sessions),
        "picks": len(picks),
        "message": None,
        "candidate_count_distribution": _distribution(counts),
        "top10": block(top),
        "full_batch": block(picks),
        "rank_groups": {f"{low}-{high}": block([p for p in picks if low <= p["rank"] <= high])
                        for low, high in spec.rank_groups},
        "by_year": {year: {
            "sessions": count,
            "top10": block([p for p in top if p["source_session"][:4] == year]),
            "full_batch": block([p for p in picks if p["source_session"][:4] == year]),
        } for year, count in sorted(sessions_by_year.items())},
    }


def evaluate_trend_liquidity_v1_history(
    inputs: HistoricalPitInputs,
    spec: TrendLiquidityV1PitSpec = TREND_LIQUIDITY_V1_PIT_SPEC,
    *,
    diagnostics: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Deterministic evaluation body; identical inputs give an identical result."""
    evidence = _Evidence(inputs)
    sessions = evidence.sessions
    first = TREND_LIQUIDITY_V1_TREND_SESSIONS - 1
    requested = list(range(first, len(sessions)))
    records: list[tuple[date, tuple[str, ...]]] = []
    blocker_counts: dict[str, int] = {}
    strict_sessions: list[str] = []
    strict_counts: list[int] = []
    strict_picks: list[dict[str, Any]] = []
    degraded_counts: list[int] = []
    degraded_partial = 0
    for step, index in enumerate(requested):
        if progress is not None:
            progress(step, len(requested))
        selected = _select(evidence, index)
        blockers = tuple(sorted(set(selected["blockers"])))
        records.append((selected["source"], blockers))
        for name in blockers:
            blocker_counts[name] = blocker_counts.get(name, 0) + 1
        candidates = selected["candidates"]
        if not blockers:
            excluded = inputs.regulatory.excluded_by_target.get(selected["target"], frozenset())
            eligible = candidates.filter(~pl.col("symbol").is_in(sorted(excluded)))
            strict_sessions.append(selected["source"].isoformat())
            strict_counts.append(eligible.height)
            strict_picks.extend(_score_picks(
                evidence, index, eligible.head(TREND_LIQUIDITY_V1_BATCH_SIZE), spec))
        elif diagnostics and candidates is not None:
            if selected.get("adjustment_status") == "partial":
                degraded_partial += 1
            else:
                degraded_counts.append(candidates.height)

    excluded = [(day, blockers) for day, blockers in records if blockers]
    combos: dict[str, int] = {}
    for _, blockers in excluded:
        key = "+".join(blockers)
        combos[key] = combos.get(key, 0) + 1
    requested_days = [sessions[i] for i in requested]
    strict_first = strict_sessions[0] if strict_sessions else None
    return {
        "evaluation_range": {
            "verified_sessions": len(sessions),
            "first_verified_session": sessions[0].isoformat() if sessions else None,
            "last_verified_session": sessions[-1].isoformat() if sessions else None,
            "warmup_sessions": min(first, len(sessions)),
            "first_requested_session": requested_days[0].isoformat() if requested_days else None,
            "last_requested_session": requested_days[-1].isoformat() if requested_days else None,
            "earliest_strict_session": strict_first,
        },
        "reproducibility": {
            "requested_sessions": len(requested),
            "strict_fully_reproducible_sessions": len(strict_sessions),
            "excluded_sessions": len(excluded),
            "blocker_session_counts": dict(sorted(blocker_counts.items())),
            "blocker_combination_counts": dict(sorted(combos.items())),
            "blocker_descriptions": {k: BLOCKERS.get(k, k) for k in sorted(blocker_counts)},
            "excluded_session_ranges": _ranges(excluded),
        },
        "strict_result": _strict_result(strict_picks, strict_counts, strict_sessions, spec),
        "strict_picks": strict_picks,
        "degraded_diagnostics": {
            "diagnostic_only": True,
            "claimable": False,
            "label": ("NOT trend_liquidity_v1: verified-type TWSE subset, TPEx excluded, "
                      "regulatory exclusion not applied"),
            "sessions_computed": len(degraded_counts) if diagnostics else 0,
            "sessions_corporate_action_unverified": degraded_partial,
            "candidate_count_distribution": _distribution(degraded_counts) if diagnostics else None,
            "picks_and_returns": "not computed by design",
        },
    }


# ── Real-data loading, provenance and ledger ────────────────────


class TrendLiquidityV1PitInputError(RuntimeError):
    """Required historical evidence is missing or inconsistent."""


class HistoricalPitRunStore(PrimaryOosRunStore):
    """Append-only ledger for historical PIT selection runs (separate file)."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from app.taiwan.data_root import taiwan_data_root

            path = taiwan_data_root() / "quant" / "historical_pit_selection_runs.sqlite3"
        super().__init__(path)

    def latest_for_spec(self, spec_hash: str) -> dict[str, Any] | None:
        for event in self._read_events():
            if event["event_type"] != "succeeded":
                continue
            payload = json.loads(event["payload_json"])
            context = payload.get("context")
            artifact = payload.get("artifact")
            if (isinstance(context, dict) and context.get("spec_hash") == spec_hash
                    and isinstance(artifact, dict)):
                return {"run_id": event["run_id"], "recorded_at": event["recorded_at"], **artifact}
        return None


def code_fingerprint() -> str:
    """The files that build the evidence and define selection, normalization and scoring."""
    taiwan = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in (Path(__file__).resolve(), taiwan / "screener.py", taiwan / "adjust.py",
                 taiwan / "corporate_actions.py", taiwan / "selection_review_service.py",
                 taiwan / "quant" / "primary_oos_runner.py", taiwan / "observed_universe.py",
                 taiwan / "historical_classification.py", taiwan / "realtime" / "calendar.py"):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _action_marker(store: CorporateActionStore) -> tuple[tuple[date, date] | None, dict[str, Any]]:
    marker = store.path.with_name("coverage.json")
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        span = (date.fromisoformat(record["start"]), date.fromisoformat(record["end"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None, {"status": "unavailable"}
    if (sorted(record.get("sources", ())) != sorted(SOURCE_URLS)
            or record.get("events_sha256") != store.snapshot_digest()):
        return None, {"status": "marker_mismatch"}
    return span, {"status": "verified", "start": span[0].isoformat(), "end": span[1].isoformat(),
                  "sources": sorted(SOURCE_URLS), "events_sha256": record["events_sha256"]}


def load_verified_actions(
    store: CorporateActionStore,
) -> tuple[tuple[date, date] | None, dict[str, Any], tuple[CorporateActionEvent, ...]]:
    """Events plus the coverage record they belong to; a refresh mid-read fails closed."""
    span, record = _action_marker(store)
    if span is None:
        return None, record, ()
    events = tuple(store.read())
    if _action_marker(store)[1] != record:
        raise TrendLiquidityV1PitInputError(
            "corporate-action snapshot changed while loading; rerun the evaluation")
    return span, record, events


def load_regulatory_evidence(
    sessions: Sequence[date], universe: pl.DataFrame,
) -> tuple[RegulatoryEvidence, dict[str, Any]]:
    """Official regulatory history for every (source, entry) pair; changes fail closed."""
    from app.taiwan.instrument_evidence import InstrumentEvidenceStore
    from app.taiwan.regulatory_history import RegulatoryHistoryStore, replay_regulatory_evidence

    store = RegulatoryHistoryStore()
    before = store.digest()
    try:
        _registry, termination, stamps = InstrumentEvidenceStore().load()
        terminations: dict[str, date] | None = dict(
            zip(termination["code"], termination["termination_date"], strict=True))
        retrieved: date | None = datetime.fromisoformat(stamps["termination"]).date()
    except (FileNotFoundError, ValueError, KeyError):
        terminations, retrieved = None, None
    observed = universe.filter(pl.col("price_bar_available")).group_by("date").agg(
        pl.col("market_symbol").str.split(".").list.first().alias("codes"))
    observed_codes = {row["date"]: frozenset(row["codes"]) for row in observed.iter_rows(named=True)}
    pairs = list(zip(sessions[:-1], sessions[1:], strict=True))
    blockers, excluded = replay_regulatory_evidence(
        store, pairs, terminations=terminations, terminations_retrieved=retrieved,
        observed_codes=observed_codes)
    if store.digest() != before:
        raise TrendLiquidityV1PitInputError(
            "regulatory history changed while loading; rerun the evaluation")
    evidence = RegulatoryEvidence(
        source=("official archives: TWSE punish, TPEx disposal, dated TPEx cmode lists, "
                "TWSE termination list"),
        covered_targets=frozenset(target for target, missing in blockers.items() if not missing),
        # A code trades on one market at a time; the live lock resolves it the same way.
        excluded_by_target={target: frozenset(f"{code}.{exchange}" for code in codes
                                              for exchange in V1_EXCHANGES)
                            for target, codes in excluded.items()},
        blockers_by_target=blockers,
    )
    record = {
        "regulatory_history_sha256": before,
        "conflicts": store.conflicts(),
        "termination_retrieved_at": stamps.get("termination") if terminations is not None else None,
        "covered_entry_sessions": len(evidence.covered_targets),
    }
    return evidence, record


def load_trend_liquidity_v1_pit_inputs() -> HistoricalPitInputs:
    """Read A2a/A2b, the verified action snapshot and trading-day evidence."""
    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker

    worker = TaiwanHistoricalBackfillWorker()
    # Hold the shared backfill lock for every store read (as the Primary panel
    # build does), so census and classification cannot change mid-snapshot.
    # A running backfill makes this fail with WorkerBusyError instead of waiting.
    worker.lock.acquire()
    try:
        return _load_locked(worker)
    finally:
        worker.lock.release()


def _load_locked(worker: Any) -> HistoricalPitInputs:
    from app.taiwan.observed_universe import session_candidates
    from app.taiwan.quant.primary_oos_runner import _primary_universe
    from app.taiwan.realtime.calendar import TaiwanTradingCalendar

    census = worker.census_store
    universe, classification_identity = _primary_universe(census, worker.classification_store)
    sessions = sorted(census.session_dates("TWSE"))
    if not sessions:
        raise TrendLiquidityV1PitInputError("verified TWSE sessions are unavailable")
    if not census.month_verification_covers("TWSE", sessions[0], sessions[-1]):
        raise TrendLiquidityV1PitInputError(
            "official month tables do not verify the TWSE session span")
    statuses = census.partition_statuses("TWSE")
    calendar = TaiwanTradingCalendar()
    unresolved = frozenset(
        day for day in session_candidates(census, "TWSE", sessions[0], sessions[-1])
        if statuses.get(day) not in ("observed", "confirmed_non_trading")
        and not (day not in statuses and calendar.day_evidence(day, "TWSE").status == "non_trading"))
    raw = census.read("TWSE")
    prices = raw.select(
        pl.concat_str([pl.col("raw_code"), pl.lit(".TWSE")]).alias("symbol"),
        "date", "open", "close", "amount",
    )
    span, action_record, events = load_verified_actions(CorporateActionStore())
    regulatory, regulatory_record = load_regulatory_evidence(sessions, universe)
    tpex_sessions = frozenset(census.session_dates("TPEX"))
    verification = json.loads((census._verification_path("TWSE")).read_text(encoding="utf-8"))
    identity = {
        "twse_sessions_sha256": canonical_hash([d.isoformat() for d in sessions]),
        "twse_census_partitions_sha256": verification.get("partitions_sha256"),
        "twse_month_verification": {"start": verification.get("start"),
                                    "end": verification.get("end")},
        "a2b_classification_identity": classification_identity,
        "corporate_actions": action_record,
        # Exact date sets, not counts: a corrected backfill that moves a date
        # must change the run identity.
        "tpex_observed_sessions": len(tpex_sessions),
        "tpex_observed_sessions_sha256": canonical_hash(sorted(d.isoformat() for d in tpex_sessions)),
        "tpex_subtype_evidence": "blocked",
        "regulatory": regulatory_record,
        "unresolved_weekdays": len(unresolved),
        "unresolved_weekdays_sha256": canonical_hash(sorted(d.isoformat() for d in unresolved)),
    }
    # The recorded partition digest must still describe the rows read above.
    if not census.month_verification_covers("TWSE", sessions[0], sessions[-1]):
        raise TrendLiquidityV1PitInputError("TWSE census changed while loading; rerun the evaluation")
    return HistoricalPitInputs(
        sessions=tuple(sessions),
        prices=prices,
        universe=universe.filter(pl.col("observed_on_market")),
        events=events,
        action_coverage=span,
        regulatory=regulatory,
        blocked_exchanges=frozenset({"TPEX"}),
        unresolved_days=unresolved,
        identity=identity,
    )


def _data_coverage(preflight: Any, inputs: HistoricalPitInputs) -> dict[str, Any]:
    health = preflight.data_health.describe() if preflight is not None else None
    return {
        "readiness": preflight.readiness.describe() if preflight is not None else None,
        "twse_census": health["census_by_exchange"].get("TWSE") if health else None,
        "tpex_census": health["census_by_exchange"].get("TPEX") if health else None,
        "twse_classification": health["classification"] if health else None,
        "tpex_classification": "blocked: no official historical subtype source",
        "corporate_actions": inputs.identity.get("corporate_actions"),
        "regulatory": {"source": inputs.regulatory.source,
                       "covered_entry_sessions": len(inputs.regulatory.covered_targets)},
        "unresolved_weekdays": len(inputs.unresolved_days),
    }


LIMITATIONS = (
    "TPEx historical ordinary-stock subtype is BLOCKED. Official per-session tables separate "
    "warrants/CBBCs (afterTrading/otc type=EW) and current ISIN registries give share class, "
    "but issuers that left every registry and OTC-to-TWSE transfers have no official subtype "
    "evidence (sampled 2015-2025: 28-93 such rows per session, 6-10 of them liquid).",
    "Regulatory history is rebuilt from official archives with date-level publication: an "
    "announcement dated on or before the source session is known at the cutoff, which is "
    "the formal lock deadline before the entry session opens.",
    "Returns would be price returns normalized for corporate actions, not total return, "
    "before costs and slippage.",
    "A pick whose horizon price is missing (e.g. delisted) is unavailable, not -100%.",
)

FOLLOW_UP_DATA_WORK = (
    "Separate data task: an official historical source for TPEx ordinary-stock subtype that "
    "covers delisted issuers and OTC-to-TWSE transfers.",
    "Secondary: 20 TWT49U corporate-action provider_error events; 2 unresolved TWSE subtypes.",
)


def build_artifact(
    result: Mapping[str, Any], inputs: HistoricalPitInputs, *,
    spec: TrendLiquidityV1PitSpec, code_sha: str, code_fp: str,
    generated_at: str, data_coverage: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "artifact_type": ARTIFACT_TYPE,
        "record_scope": "historical_pit",
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.strategy_id,
        "spec_version": spec.version,
        "spec_fingerprint": spec.fingerprint,
        "spec": spec.describe(),
        "code_fingerprint": code_fp,
        "code_sha": code_sha,
        "dataset_identity": canonical_hash(dict(inputs.identity)),
        "dataset_evidence": dict(inputs.identity),
        "data_coverage": dict(data_coverage),
        "horizon_definitions": spec.describe()["horizon_definitions"],
        "universe_contract": spec.describe()["universe_contract"],
        "price_semantics": spec.describe()["price_semantics"],
        **{key: result[key] for key in ("evaluation_range", "reproducibility",
                                        "strict_result", "degraded_diagnostics")},
        "strict_picks": result["strict_picks"],
        "limitations": list(LIMITATIONS),
        "follow_up_data_work": list(FOLLOW_UP_DATA_WORK),
    }
    return {**body, "result_fingerprint": canonical_hash(body), "generated_at": generated_at}


def run_trend_liquidity_v1_pit_evaluation(
    *,
    store: HistoricalPitRunStore | None = None,
    loader: Callable[[], HistoricalPitInputs] = load_trend_liquidity_v1_pit_inputs,
    preflight_reader: Callable[[], Any] | None = None,
    spec: TrendLiquidityV1PitSpec = TREND_LIQUIDITY_V1_PIT_SPEC,
    code_sha: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(TAIPEI),
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Gate on shared data health, evaluate once, record the artifact."""
    from app.taiwan.backfill_worker import WorkerLock
    from app.taiwan.quant.primary_oos_runner import (
        PrimaryOosNotReadyError,
        _code_sha,
        read_primary_oos_preflight,
    )

    reader = preflight_reader or read_primary_oos_preflight
    preflight = reader()
    if preflight.readiness.status.value != "ready":
        raise PrimaryOosNotReadyError(preflight.readiness)
    store = store or HistoricalPitRunStore()
    with WorkerLock(store.path.with_name(".historical_pit.lock")):
        inputs = loader()
        # The recorded coverage must describe the generation that was loaded.
        fresh = reader()
        if fresh.readiness.status.value != "ready":
            raise PrimaryOosNotReadyError(fresh.readiness)
        if fresh.data_health.describe() != preflight.data_health.describe():
            raise TrendLiquidityV1PitInputError(
                "data health changed while inputs were loading; rerun the evaluation")
        actual_sha = code_sha or _code_sha()
        code_fp = code_fingerprint()
        dataset_identity = canonical_hash(dict(inputs.identity))
        identity_key = canonical_hash({"spec_hash": spec.fingerprint, "code_fingerprint": code_fp,
                                       "dataset_identity": dataset_identity})
        context = {"spec_hash": spec.fingerprint, "dataset_identity": dataset_identity,
                   "code_fingerprint": code_fp, "code_sha": actual_sha}
        run_id = str(uuid.uuid4())
        previous = store.begin(run_id=run_id, identity_key=identity_key,
                               recorded_at=now().isoformat(), context=context)
        if previous is not None:
            return {"reused": True, "run_id": previous["run_id"], **previous["artifact"]}
        try:
            result = evaluate_trend_liquidity_v1_history(inputs, spec, progress=progress)
            artifact = build_artifact(
                result, inputs, spec=spec, code_sha=actual_sha, code_fp=code_fp,
                generated_at=now().isoformat(), data_coverage=_data_coverage(preflight, inputs))
            store.succeed(run_id=run_id, identity_key=identity_key,
                          recorded_at=now().isoformat(), context=context, artifact=artifact)
        except Exception as exc:
            store.fail(run_id=run_id, identity_key=identity_key, recorded_at=now().isoformat(),
                       context=context, error_code=type(exc).__name__)
            raise
    return {"reused": False, "run_id": run_id, **artifact}


def artifact_summary(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """API projection: never forwards per-pick rows or unrelated payload."""
    keys = ("artifact_type", "record_scope", "strategy_id", "strategy_version", "spec_version",
            "spec_fingerprint", "code_fingerprint", "code_sha", "dataset_identity",
            "result_fingerprint", "generated_at", "run_id", "recorded_at", "evaluation_range",
            "reproducibility", "strict_result", "degraded_diagnostics", "data_coverage",
            "horizon_definitions", "universe_contract", "price_semantics", "limitations",
            "follow_up_data_work")
    summary = json.loads(canonical_json({k: artifact.get(k) for k in keys}))
    reproducibility = summary.get("reproducibility")
    if isinstance(reproducibility, dict):
        # The ledger's canonical JSON orders lists by content; restore time order.
        reproducibility["excluded_session_ranges"] = sorted(
            reproducibility.get("excluded_session_ranges") or [], key=lambda r: r["start"])
    return summary
