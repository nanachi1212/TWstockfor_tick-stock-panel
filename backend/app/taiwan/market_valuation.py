"""Official individual valuation medians and descriptive saved-history ranks."""
from __future__ import annotations

import math
import threading
from collections import Counter
from datetime import date
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from app.taiwan.fundamentals import (
    FundamentalRecord,
    TaiwanFundamentalStore,
    TaiwanOfficialFundamentals,
)
from app.taiwan.market_breadth import ResearchMetric, metric
from app.taiwan.providers.http import taiwan_client

_SAVE_LOCK = threading.Lock()


class ValuationMetric(ResearchMetric):
    percentile: float | None = None
    percentile_sample_count: int = 0
    percentile_start: date | None = None
    percentile_end: date | None = None
    percentile_status: str = "insufficient_history"


class MarketValuationStats(BaseModel):
    market: str
    as_of: date
    source: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    retrieved_at: str | None = None
    available_at: str | None = None
    publication_time_status: Literal["unverified"] = "unverified"
    usage_scope: Literal["descriptive_history"] = "descriptive_history"
    strategy_lab_eligible: Literal[False] = False
    methodology: str = "individual_stock_median"
    composite_method: str = "pooled_same_session_individual_records"
    coverage_denominator: str = "observed_valuation_records_union_observed_bars"
    universe_complete: Literal[False] = False
    status: str
    metrics: dict[str, ValuationMetric]


def _values(records: list[FundamentalRecord], field: str,
            expected_symbols: set[str]) -> tuple[list[float], Counter[str]]:
    values: list[float] = []
    reasons: Counter[str] = Counter()
    reasons["missing_valuation_record"] = len(expected_symbols - {r.symbol for r in records})
    for record in records:
        raw = (record.values or {}).get(field)
        if record.status not in {"official", "data_insufficient"}:
            reasons["source_unavailable"] += 1
        elif raw is None:
            reasons["missing_value"] += 1
        elif not isinstance(raw, (float, int)) or not math.isfinite(raw):
            reasons["invalid_value"] += 1
        elif raw < 0 or (field != "dividend_yield" and raw == 0):
            reasons["nonpositive_pe" if field == "pe" else "invalid_value"] += 1
        else:
            values.append(float(raw))
    return values, Counter({k: n for k, n in reasons.items() if n})


def calculate_valuation(records: list[FundamentalRecord], *, day: date, market: str,
                        expected_symbols: set[str] | None = None) -> MarketValuationStats:
    exchanges = ("TWSE", "TPEX") if market == "composite" else (market,)
    # Select the latest observed revision for descriptive history, never PIT.
    latest: dict[tuple[str, str], FundamentalRecord] = {}
    for record in sorted(records, key=lambda r: r.retrieved_at):
        if (record.dataset == "valuation" and record.period_end <= day.isoformat()
                and record.symbol.rsplit(".", 1)[-1] in exchanges):
            latest[(record.symbol, record.period_end)] = record
    current = [r for r in latest.values() if r.period_end == day.isoformat()]
    expected = {s for s in expected_symbols or set() if s.rsplit(".", 1)[-1] in exchanges}
    complete_composite = market != "composite" or {r.symbol.rsplit(".", 1)[-1] for r in current} == set(exchanges)
    metrics: dict[str, ValuationMetric] = {}
    for field in ("pe", "pb", "dividend_yield"):
        values, reasons = _values(current, field, expected)
        base = metric(values, reasons, unit="percent" if field == "dividend_yield" else "ratio")
        base.value = median(values) if values and complete_composite else None
        if not complete_composite:
            base.status = "unavailable"
        past: dict[str, list[FundamentalRecord]] = {}
        for record in latest.values():
            if record.period_end < day.isoformat():
                past.setdefault(record.period_end, []).append(record)
        observations: list[tuple[str, float]] = []
        for stamp, group in sorted(past.items()):
            if market == "composite" and {r.symbol.rsplit(".", 1)[-1] for r in group} != set(exchanges):
                continue
            historic_values, _ = _values(group, field, set())
            if historic_values:
                observations.append((stamp, median(historic_values)))
        ranked = len(observations) >= 20 and base.value is not None
        # Midrank ties: flat history ranks at 50%, not misleadingly at 100%.
        percentile = None
        if ranked and base.value is not None:
            percentile = (sum(v < base.value for _, v in observations)
                          + 0.5 * sum(v == base.value for _, v in observations)) / len(observations)
        metrics[field] = ValuationMetric(
            **base.model_dump(), percentile=percentile, percentile_sample_count=len(observations),
            percentile_start=date.fromisoformat(observations[0][0]) if observations else None,
            percentile_end=date.fromisoformat(observations[-1][0]) if observations else None,
            percentile_status="available" if ranked else "insufficient_history",
        )
    return MarketValuationStats(
        market=market, as_of=day, source=sorted({r.source for r in current}),
        source_urls=sorted({r.source_url for r in current}),
        retrieved_at=max((r.retrieved_at.isoformat() for r in current), default=None),
        status="unavailable" if not complete_composite or not any(m.value is not None for m in metrics.values())
        else "partial" if any(m.status != "available" for m in metrics.values()) else "available",
        metrics=metrics,
    )


def refresh_valuation(store: TaiwanFundamentalStore,
                      provider: TaiwanOfficialFundamentals | None = None) -> dict[str, object]:
    """Explicit refresh only. Failed exchanges preserve their saved records."""
    owned = provider is None
    provider = provider or TaiwanOfficialFundamentals(taiwan_client(timeout=30))
    failures: list[dict[str, str]] = []
    rows: list[FundamentalRecord] = []
    try:
        for exchange in ("TWSE", "TPEX"):
            try:
                rows.extend(provider.valuation_snapshot(exchange))
            except Exception:
                # Never return upstream exceptions/response bodies or configuration.
                failures.append({"market": exchange, "reason": "official_valuation_fetch_failed"})
        if rows:
            with _SAVE_LOCK:
                store.save(rows)
    finally:
        if owned:
            provider.client.close()
    return {"status": "partial" if rows and failures else "available" if rows else "unavailable",
            "records_saved": len(rows), "failed": failures,
            "as_of": sorted({r.period_end for r in rows}),
            "usage_scope": "descriptive_history", "available_at": None}
