"""Deterministic Multi-Stock Objective Research Comparison Service (Phase 7G).

Compares 2-5 Taiwan-listed instruments (TWSE/TPEx common stocks, normal/leveraged/
inverse ETFs) side-by-side, purely by reusing the existing per-symbol deterministic
evidence layer (Phase 7C `TaiwanStockResearchContextService`) and the whole-market
abnormal diagnostics batch (Phase 7D `TaiwanAbnormalDiagnosticsService`).

STRICT BOUNDARIES (unchanged from Phase 7C/7D, inherited by composition):
- 100% Deterministic: NO AI, NO recommendations, NO computed rankings/deltas.
- Zero-HTTP at request time: reuses stores through the existing services.
- Point-In-Time No Look-Ahead: every compared symbol shares the identical `target_date`.
- Categorical Separation: ETF `not_applicable` vs stock `unavailable` semantics are
  inherited unchanged from `research_context.py` per-symbol output — never coerced.
- This module does NOT modify `research_context.py`, `market_intelligence.py`,
  `industry_intelligence.py`, or `abnormal_diagnostics.py` — it only composes them.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from pydantic import BaseModel, Field

from app.taiwan.abnormal_diagnostics import (
    TaiwanAbnormalDiagnosticsService,
    TaiwanAbnormalDiagnosticItem,
)
from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.research_context import (
    TaiwanStockResearchContext,
    TaiwanStockResearchContextService,
)
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe import get_security_master

logger = logging.getLogger(__name__)

MIN_COMPARE_SYMBOLS = 2
MAX_COMPARE_SYMBOLS = 5


# ── Request-Scoped Memoizing Proxy (private to this module) ──────
#
# `TaiwanMarketIntelligenceService.get_snapshot()` and
# `TaiwanIndustryIntelligenceService.get_snapshot()` have no internal cache —
# every call fully recomputes from the underlying stores, regardless of
# whether the service instance is shared. `research_context.py` is NOT
# modified to add caching; instead this proxy wraps ONE service instance for
# the lifetime of a single `compare()` call, so that N symbols sharing the
# same `target_date` trigger the real computation exactly once (the first
# `get_snapshot(date)` call) and N-1 cheap in-memory cache hits thereafter.
# This proxy is never exposed outside this module and never outlives one
# comparison request — it is not a general-purpose cache.


class _MemoizingSnapshotProxy:
    """Request-scoped cache around `.get_snapshot(date)`. Discarded after one compare() call."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._cache: dict[date, Any] = {}

    def get_snapshot(self, target_date: date, *args: Any, **kwargs: Any) -> Any:
        cache_key = target_date
        if cache_key not in self._cache:
            self._cache[cache_key] = self._inner.get_snapshot(target_date, *args, **kwargs)
        return self._cache[cache_key]

    def __getattr__(self, name: str) -> Any:
        # Delegate anything else (e.g. constructor-injected stores) untouched.
        return getattr(self._inner, name)


# ── Strong Typing & Pydantic Schemas ──────────────────────────


class TaiwanStockCompareRequest(BaseModel):
    """Request payload for POST /api/taiwan/stocks/compare and /compare/ai-research."""

    symbols: list[str] = Field(
        ...,
        min_length=MIN_COMPARE_SYMBOLS,
        max_length=MAX_COMPARE_SYMBOLS,
        description=f"欲比較之台股/ETF代碼清單 ({MIN_COMPARE_SYMBOLS}-{MAX_COMPARE_SYMBOLS} 檔)",
    )
    date: str | None = Field(None, description="指定交易日 (YYYY-MM-DD)，預設為最新完成交易日；所有標的共用同一日期")


class ComparisonInstrumentResult(BaseModel):
    """One resolved instrument's deterministic evidence within a comparison."""

    symbol: str
    context: TaiwanStockResearchContext
    diagnostic_item: TaiwanAbnormalDiagnosticItem | None = None


class TaiwanStockComparisonResponse(BaseModel):
    """Unified strongly-typed deterministic comparison response."""

    symbols_requested: list[str]
    comparison_date: str
    generated_at: str
    instruments: list[ComparisonInstrumentResult] = Field(default_factory=list)
    unsupported_symbols: list[str] = Field(default_factory=list, description="無法解析之代碼 (不存在於 Security Master)")


# ── Service Implementation ────────────────────────────────────


def resolve_canonical_symbols(
    symbols: list[str],
    security_master: Any,
) -> tuple[list[str], list[str]]:
    """Resolve raw symbol inputs to canonical symbols, deduped, order-preserving.

    Returns (resolved_canonical_symbols, unsupported_raw_inputs).
    Reuses the exact same lookup fallback chain as
    TaiwanStockResearchContextService.get_research_context (research_context.py:333-353).
    """
    resolved: list[str] = []
    seen: set[str] = set()
    unsupported: list[str] = []

    for raw in symbols:
        raw = (raw or "").strip()
        if not raw:
            unsupported.append(raw)
            continue

        inst = None
        try:
            parsed = parse_symbol(raw)
            inst = security_master.get_instrument(parsed.canonical)
        except Exception:
            pass

        if inst is None:
            inst = security_master.get_instrument(f"{raw.upper()}.TWSE") or security_master.get_instrument(
                f"{raw.upper()}.TPEX"
            )

        if inst is None:
            matches = security_master.search(raw, limit=5)
            if matches:
                inst = security_master.get_instrument(matches[0]["symbol"])

        if inst is None:
            unsupported.append(raw)
            continue

        canonical = inst.symbol
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(canonical)

    return resolved, unsupported


class TaiwanStockComparisonService:
    """Deterministic multi-symbol comparison orchestration (Phase 7G)."""

    def __init__(
        self,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: Any | None = None,
        research_svc_factory: Any | None = None,
        diag_svc: TaiwanAbnormalDiagnosticsService | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.security_master = security_master or get_security_master()
        # Factory so tests can inject a stubbed TaiwanStockResearchContextService;
        # defaults to constructing one per compare() call with a fresh memoizing proxy pair.
        self._research_svc_factory = research_svc_factory
        self.diag_svc = diag_svc or TaiwanAbnormalDiagnosticsService(calendar=self.calendar)

    def _build_research_service(self) -> TaiwanStockResearchContextService:
        if self._research_svc_factory is not None:
            return self._research_svc_factory()
        market_proxy = _MemoizingSnapshotProxy(TaiwanMarketIntelligenceService(calendar=self.calendar))
        industry_proxy = _MemoizingSnapshotProxy(TaiwanIndustryIntelligenceService(calendar=self.calendar))
        return TaiwanStockResearchContextService(
            calendar=self.calendar,
            security_master=self.security_master,
            market_intel_svc=market_proxy,
            industry_intel_svc=industry_proxy,
        )

    def compare(
        self,
        symbols: list[str],
        target_date: date | None = None,
    ) -> TaiwanStockComparisonResponse:
        from app.taiwan.realtime.calendar import taipei_now

        now_iso = taipei_now().isoformat()

        resolved_symbols, unsupported = resolve_canonical_symbols(symbols, self.security_master)

        if len(resolved_symbols) < MIN_COMPARE_SYMBOLS:
            raise ValueError(
                f"可比較之有效標的不足 (至少需要 {MIN_COMPARE_SYMBOLS} 檔)，"
                f"無法解析: {unsupported or symbols}"
            )

        # Resolve the shared comparison date ONCE — every symbol uses this exact same date.
        resolved_date = target_date or resolve_target_latest_trading_date(self.calendar)

        research_svc = self._build_research_service()

        instruments: list[ComparisonInstrumentResult] = []
        for symbol in resolved_symbols:
            try:
                ctx = research_svc.get_research_context(symbol, target_date=resolved_date)
            except Exception as e:
                logger.warning("Comparison: failed to assemble context for %s: %s", symbol, e)
                unsupported.append(symbol)
                continue
            instruments.append(ComparisonInstrumentResult(symbol=symbol, context=ctx, diagnostic_item=None))

        if len(instruments) < MIN_COMPARE_SYMBOLS:
            raise ValueError(
                f"可比較之有效標的不足 (至少需要 {MIN_COMPARE_SYMBOLS} 檔)，"
                f"無法解析: {unsupported}"
            )

        # Abnormal diagnostics: ONE whole-market batch call at the shared date, then
        # filter to the resolved symbol set (mirrors ai_research.py:382-392).
        try:
            # include_etfs=True (Phase 7J): comparison already renders ETF instruments
            # correctly per-symbol (leverage badges, not_applicable fundamentals, etc.) —
            # this is one of the two internal consumers proven safe to opt in (the public
            # /abnormal-diagnostics endpoint and TaiwanScreener.tsx never pass this flag).
            diag_snap = self.diag_svc.get_diagnostics(target_date=resolved_date, include_all=True, include_etfs=True)
            diag_by_symbol = {item.symbol: item for item in diag_snap.items}
            for instrument in instruments:
                instrument.diagnostic_item = diag_by_symbol.get(instrument.symbol)
        except Exception as e:
            logger.warning("Comparison: diagnostics lookup failed for date %s: %s", resolved_date, e)

        return TaiwanStockComparisonResponse(
            symbols_requested=symbols,
            comparison_date=str(resolved_date),
            generated_at=now_iso,
            instruments=instruments,
            unsupported_symbols=unsupported,
        )
