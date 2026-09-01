"""Tests for Deterministic Multi-Stock Objective Research Comparison Service (Phase 7G).

Covers:
- `_MemoizingSnapshotProxy`: request-scoped cache, correctly avoids repeated
  `get_snapshot()` computation across symbols sharing one date, delegates
  everything else untouched, never persists beyond one instance.
- `resolve_canonical_symbols`: dedupe, unsupported-symbol exclusion, uses the
  real Security Master (identity lookups do not require local daily price
  data, unlike price/technical context — see test_taiwan_research_context.py
  precedent for this same distinction).
- `TaiwanStockComparisonService.compare()`: 2-5 symbol bounds, duplicate
  collapse, unsupported-symbol handling, mixed stock/TWSE/TPEx/ETF/leveraged/
  inverse instrument types, zero-is-not-missing, `not_applicable` ETF vs stock
  asymmetry preserved, shared target_date propagation, no-look-ahead.
- API routing: POST /stocks/compare, POST /stocks/compare/ai-research, and
  regression that GET /stocks/{symbol} + POST /stocks/{symbol}/ai-research
  remain unaffected by the route-registration reordering (route collision fix).

All deterministic-comparison-logic tests construct/mock the underlying stores
directly (mirroring the established pattern in test_taiwan_research_context.py)
so they do not depend on the local Taiwan daily/institutional/margin parquet
cache being populated in this environment.
"""
from datetime import date
from unittest.mock import MagicMock, patch
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.abnormal_diagnostics import (
    TaiwanAbnormalDiagnosticsSnapshot,
    DiagnosticsDataQuality,
)
from app.taiwan.comparison import (
    MAX_COMPARE_SYMBOLS,
    MIN_COMPARE_SYMBOLS,
    ComparisonInstrumentResult,
    TaiwanStockComparisonService,
    _MemoizingSnapshotProxy,
    resolve_canonical_symbols,
)
from app.taiwan.research_context import (
    EvidenceMeta,
    ETFContextEvidence,
    FundamentalsContextEvidence,
    IndustryContextEvidence,
    InstitutionalContextEvidence,
    MarginContextEvidence,
    MarketContextEvidence,
    MarketRulesContextEvidence,
    MonitorContextEvidence,
    PriceContextEvidence,
    RealtimeContextEvidence,
    ResearchDataQuality,
    EvidenceSummaryCounts,
    StockIdentityEvidence,
    TaiwanStockResearchContext,
    TechnicalContextEvidence,
)


# ── Shared Test Fixture Helper ────────────────────────────────


def build_context(
    symbol: str,
    code: str,
    name: str,
    *,
    instrument_type: str = "stock",
    trade_date: date = date(2026, 8, 28),
    close: float | None = 100.0,
    return_5d: float | None = 0.02,
    foreign_net_1d: float | None = 0.0,
    fundamentals_available: bool = True,
    etf_leverage: float | None = None,
) -> TaiwanStockResearchContext:
    """Build a minimal-but-valid TaiwanStockResearchContext without touching real stores.

    Mirrors the established mocking convention in test_taiwan_research_context.py
    (constructing typed evidence directly) so comparison-service tests remain
    fully independent of the local daily/institutional/margin parquet cache.
    """
    meta = EvidenceMeta(classification="KNOWN", source="test_fixture", as_of=str(trade_date))

    is_etf = instrument_type == "etf"

    fundamentals = (
        FundamentalsContextEvidence(status="not_applicable", meta=meta)
        if is_etf
        else FundamentalsContextEvidence(
            status="available" if fundamentals_available else "unavailable",
            pe=18.5 if fundamentals_available else None,
            meta=meta,
        )
    )
    etf_ctx = (
        ETFContextEvidence(
            status="available",
            etf_type="leveraged" if etf_leverage and etf_leverage != 1.0 else "normal",
            leverage_multiplier=etf_leverage,
            inverse=bool(etf_leverage is not None and etf_leverage < 0),
            meta=meta,
        )
        if is_etf
        else ETFContextEvidence(status="not_applicable", meta=meta)
    )

    return TaiwanStockResearchContext(
        symbol=symbol,
        generated_at="2026-08-28T16:00:00+08:00",
        as_of_date=str(trade_date),
        identity=StockIdentityEvidence(
            canonical_symbol=symbol,
            code=code,
            name=name,
            exchange="TWSE" if ".TWSE" in symbol else "TPEX",
            instrument_type=instrument_type,
            industry=None if is_etf else "半導體業",
            listing_status="active",
            meta=meta,
        ),
        market_context=MarketContextEvidence(
            trade_date=str(trade_date),
            market_turnover=1_000_000_000.0,
            advance_count=500,
            decline_count=300,
            flat_count=50,
            upper_limit_count=2,
            lower_limit_count=1,
            status="complete",
            meta=meta,
        ),
        industry_context=IndustryContextEvidence(status="unavailable", meta=meta),
        price_context=PriceContextEvidence(
            trade_date=str(trade_date),
            close=close,
            change_pct=0.01 if close else None,
            return_5d=return_5d,
            meta=meta,
        ),
        technical_context=TechnicalContextEvidence(meta=meta),
        institutional_context=InstitutionalContextEvidence(
            as_of=str(trade_date),
            foreign_net_1d=foreign_net_1d,
            status="current",
            meta=meta,
        ),
        margin_context=MarginContextEvidence(status="unavailable", meta=meta),
        fundamentals_context=fundamentals,
        etf_context=etf_ctx,
        market_rules_context=MarketRulesContextEvidence(price_limit_pct=0.10, meta=meta),
        realtime_context=RealtimeContextEvidence(status="market_closed", last_price=close, meta=meta),
        monitor_context=MonitorContextEvidence(meta=meta),
        data_quality=ResearchDataQuality(
            overall_status="complete",
            sections=[],
            target_trade_date=str(trade_date),
        ),
        evidence_summary=EvidenceSummaryCounts(
            known_fields_count=10,
            missing_fields_count=0,
            derived_fields_count=5,
        ),
    )


def _empty_diag_snapshot(trade_date: date) -> TaiwanAbnormalDiagnosticsSnapshot:
    return TaiwanAbnormalDiagnosticsSnapshot(
        trade_date=str(trade_date),
        generated_at="2026-08-28T16:00:00+08:00",
        universe_count=0,
        diagnostic_count=0,
        items=[],
        data_quality=DiagnosticsDataQuality(
            target_trade_date=str(trade_date),
            universe_supported_count=0,
            evaluated_symbol_count=0,
            diagnostic_symbol_count=0,
            daily_status="unavailable",
            institutional_status="unavailable",
            margin_status="unavailable",
            overall_status="unavailable",
        ),
        provenance=[],
    )


def _make_stub_service(contexts: dict[str, TaiwanStockResearchContext]) -> TaiwanStockComparisonService:
    """Build a TaiwanStockComparisonService whose research lookups are fully
    stubbed (no real store access), so tests exercise only comparison.py's
    own orchestration logic (bounds, dedupe, unsupported handling, date
    propagation) without depending on local market data."""

    class _StubResearchService:
        def __init__(self):
            self.calls: list[tuple[str, date]] = []

        def get_research_context(self, symbol, target_date=None):
            self.calls.append((symbol, target_date))
            if symbol not in contexts:
                raise ValueError(f"no fixture for {symbol}")
            return contexts[symbol]

    stub = _StubResearchService()

    mock_sm = MagicMock()

    def _get_instrument(sym):
        norm = sym.upper()
        for s in contexts:
            if s.upper() == norm or s.split(".")[0] == norm:
                return MagicMock(symbol=s)
        return None

    mock_sm.get_instrument.side_effect = _get_instrument
    mock_sm.search.return_value = []

    mock_diag_svc = MagicMock()
    mock_diag_svc.get_diagnostics.return_value = _empty_diag_snapshot(date(2026, 8, 28))

    svc = TaiwanStockComparisonService(
        security_master=mock_sm,
        research_svc_factory=lambda: stub,
        diag_svc=mock_diag_svc,
    )
    svc._test_stub = stub  # expose for call-order assertions
    return svc


# ── _MemoizingSnapshotProxy ────────────────────────────────────


def test_memoizing_snapshot_proxy_caches_by_date():
    """First call computes; identical-date subsequent calls hit the cache, not the inner service."""
    inner = MagicMock()
    inner.get_snapshot.return_value = "SNAPSHOT_RESULT"
    proxy = _MemoizingSnapshotProxy(inner)

    d = date(2026, 8, 28)
    r1 = proxy.get_snapshot(d)
    r2 = proxy.get_snapshot(d)
    r3 = proxy.get_snapshot(d)

    assert r1 == r2 == r3 == "SNAPSHOT_RESULT"
    assert inner.get_snapshot.call_count == 1  # only the FIRST call actually computed


def test_memoizing_snapshot_proxy_different_dates_recompute():
    """Different dates are cached independently — no cross-date leakage."""
    inner = MagicMock()
    inner.get_snapshot.side_effect = lambda d: f"snap-{d}"
    proxy = _MemoizingSnapshotProxy(inner)

    d1, d2 = date(2026, 8, 27), date(2026, 8, 28)
    assert proxy.get_snapshot(d1) == f"snap-{d1}"
    assert proxy.get_snapshot(d2) == f"snap-{d2}"
    assert proxy.get_snapshot(d1) == f"snap-{d1}"  # cached, not recomputed with wrong date
    assert inner.get_snapshot.call_count == 2


def test_memoizing_snapshot_proxy_delegates_other_attrs():
    """Non-get_snapshot attributes/methods pass through untouched to the inner service."""
    inner = MagicMock()
    inner.some_other_method.return_value = "OK"
    proxy = _MemoizingSnapshotProxy(inner)

    assert proxy.some_other_method() == "OK"


def test_memoizing_snapshot_proxy_is_request_scoped_not_global():
    """Two independently constructed proxies never share cache state."""
    inner1 = MagicMock()
    inner1.get_snapshot.return_value = "A"
    inner2 = MagicMock()
    inner2.get_snapshot.return_value = "B"

    proxy1 = _MemoizingSnapshotProxy(inner1)
    proxy2 = _MemoizingSnapshotProxy(inner2)

    d = date(2026, 8, 28)
    assert proxy1.get_snapshot(d) == "A"
    assert proxy2.get_snapshot(d) == "B"
    assert inner1.get_snapshot.call_count == 1
    assert inner2.get_snapshot.call_count == 1


# ── resolve_canonical_symbols (real Security Master — identity-only, no daily data needed) ──


def test_resolve_canonical_symbols_dedupe_and_canonicalize():
    from app.taiwan.universe import get_security_master

    sm = get_security_master()
    resolved, unsupported = resolve_canonical_symbols(["2330", "2330.TWSE", "2330"], sm)
    assert resolved == ["2330.TWSE"]
    assert unsupported == []


def test_resolve_canonical_symbols_unsupported_excluded():
    from app.taiwan.universe import get_security_master

    sm = get_security_master()
    resolved, unsupported = resolve_canonical_symbols(["2330.TWSE", "NOT_A_REAL_SYMBOL_XYZ"], sm)
    assert "2330.TWSE" in resolved
    assert "NOT_A_REAL_SYMBOL_XYZ" in unsupported


# ── TaiwanStockComparisonService.compare() — stubbed research lookups ────


def test_compare_two_common_stocks():
    ctx_a = build_context("2330.TWSE", "2330", "台積電", return_5d=0.05)
    ctx_b = build_context("2881.TWSE", "2881", "富邦金", return_5d=-0.01)
    svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    resp = svc.compare(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    assert len(resp.instruments) == 2
    assert resp.unsupported_symbols == []
    assert {i.symbol for i in resp.instruments} == {"2330.TWSE", "2881.TWSE"}


def test_compare_twse_and_tpex():
    ctx_a = build_context("2881.TWSE", "2881", "富邦金")
    ctx_b = build_context("8069.TPEX", "8069", "元太", instrument_type="stock")
    svc = _make_stub_service({"2881.TWSE": ctx_a, "8069.TPEX": ctx_b})

    resp = svc.compare(["2881.TWSE", "8069.TPEX"], target_date=date(2026, 8, 28))
    exchanges = {i.context.identity.exchange for i in resp.instruments}
    assert exchanges == {"TWSE", "TPEX"}


def test_compare_common_stock_and_normal_etf_preserves_not_applicable():
    ctx_stock = build_context("2330.TWSE", "2330", "台積電", fundamentals_available=True)
    ctx_etf = build_context("0050.TWSE", "0050", "元大台灣50", instrument_type="etf", etf_leverage=1.0)
    svc = _make_stub_service({"2330.TWSE": ctx_stock, "0050.TWSE": ctx_etf})

    resp = svc.compare(["2330.TWSE", "0050.TWSE"], target_date=date(2026, 8, 28))
    by_symbol = {i.symbol: i.context for i in resp.instruments}

    # Stock: real fundamentals available (not not_applicable, not unavailable)
    assert by_symbol["2330.TWSE"].fundamentals_context.status == "available"
    assert by_symbol["2330.TWSE"].fundamentals_context.pe is not None
    # ETF: fundamentals explicitly not_applicable — NOT coerced to unavailable or 0
    assert by_symbol["0050.TWSE"].fundamentals_context.status == "not_applicable"
    assert by_symbol["0050.TWSE"].fundamentals_context.pe is None
    # ETF metadata present, distinct category — never implies "worse" than the stock
    assert by_symbol["0050.TWSE"].etf_context.status == "available"
    assert by_symbol["2330.TWSE"].etf_context.status == "not_applicable"


def test_compare_normal_and_leveraged_etf():
    ctx_normal = build_context("0050.TWSE", "0050", "元大台灣50", instrument_type="etf", etf_leverage=1.0)
    ctx_lev = build_context("00631L.TWSE", "00631L", "元大台灣50正2", instrument_type="etf", etf_leverage=2.0)
    svc = _make_stub_service({"0050.TWSE": ctx_normal, "00631L.TWSE": ctx_lev})

    resp = svc.compare(["0050.TWSE", "00631L.TWSE"], target_date=date(2026, 8, 28))
    by_symbol = {i.symbol: i.context for i in resp.instruments}

    assert by_symbol["0050.TWSE"].etf_context.leverage_multiplier == 1.0
    assert by_symbol["00631L.TWSE"].etf_context.leverage_multiplier == 2.0
    assert by_symbol["00631L.TWSE"].etf_context.inverse is False


def test_compare_leveraged_and_inverse_etf():
    ctx_lev = build_context("00631L.TWSE", "00631L", "元大台灣50正2", instrument_type="etf", etf_leverage=2.0)
    ctx_inv = build_context("00632R.TWSE", "00632R", "元大台灣50反1", instrument_type="etf", etf_leverage=-1.0)
    svc = _make_stub_service({"00631L.TWSE": ctx_lev, "00632R.TWSE": ctx_inv})

    resp = svc.compare(["00631L.TWSE", "00632R.TWSE"], target_date=date(2026, 8, 28))
    by_symbol = {i.symbol: i.context for i in resp.instruments}

    assert by_symbol["00631L.TWSE"].etf_context.inverse is False
    assert by_symbol["00632R.TWSE"].etf_context.inverse is True
    assert by_symbol["00632R.TWSE"].etf_context.leverage_multiplier == -1.0


def test_compare_zero_value_is_not_missing():
    """foreign_net_1d == 0 must remain 0, never coerced to None/missing."""
    ctx_a = build_context("2330.TWSE", "2330", "台積電", foreign_net_1d=0.0)
    ctx_b = build_context("2881.TWSE", "2881", "富邦金", foreign_net_1d=12345.0)
    svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    resp = svc.compare(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))
    by_symbol = {i.symbol: i.context for i in resp.instruments}

    assert by_symbol["2330.TWSE"].institutional_context.foreign_net_1d == 0.0
    assert by_symbol["2330.TWSE"].institutional_context.foreign_net_1d is not None
    assert by_symbol["2881.TWSE"].institutional_context.foreign_net_1d == 12345.0


def test_compare_shared_target_date_propagates_identically():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    target = date(2026, 8, 20)
    svc.compare(["2330.TWSE", "2881.TWSE"], target_date=target)

    calls = svc._test_stub.calls
    assert len(calls) == 2
    assert all(d == target for (_sym, d) in calls)  # every symbol received the IDENTICAL date object


def test_compare_duplicate_symbols_dedupe_still_succeeds():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    resp = svc.compare(["2330.TWSE", "2330", "2881.TWSE"], target_date=date(2026, 8, 28))
    assert len(resp.instruments) == 2  # duplicate "2330"/"2330.TWSE" collapsed to one


def test_compare_duplicate_symbols_collapsing_below_min_raises():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    svc = _make_stub_service({"2330.TWSE": ctx_a})

    with pytest.raises(ValueError):
        svc.compare(["2330.TWSE", "2330"], target_date=date(2026, 8, 28))


def test_compare_unsupported_symbol_mixed_with_valid_succeeds():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    resp = svc.compare(["2330.TWSE", "2881.TWSE", "NOT_REAL_XYZ"], target_date=date(2026, 8, 28))
    assert len(resp.instruments) == 2
    assert "NOT_REAL_XYZ" in resp.unsupported_symbols


def test_compare_unsupported_symbol_only_raises():
    svc = _make_stub_service({})

    with pytest.raises(ValueError):
        svc.compare(["NOT_REAL_A", "NOT_REAL_B"], target_date=date(2026, 8, 28))


def test_compare_abnormal_diagnostics_computed_once_and_filtered():
    """get_diagnostics is called exactly ONCE regardless of symbol count, then filtered per symbol."""
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    svc.compare(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))
    assert svc.diag_svc.get_diagnostics.call_count == 1


# ── Pydantic-level request validation (min/max symbols) ───────


def test_request_schema_rejects_single_symbol():
    from app.taiwan.comparison import TaiwanStockCompareRequest

    with pytest.raises(Exception):
        TaiwanStockCompareRequest(symbols=["2330.TWSE"])


def test_request_schema_rejects_six_symbols():
    from app.taiwan.comparison import TaiwanStockCompareRequest

    with pytest.raises(Exception):
        TaiwanStockCompareRequest(symbols=[f"S{i}.TWSE" for i in range(6)])


def test_request_schema_accepts_boundary_counts():
    from app.taiwan.comparison import TaiwanStockCompareRequest

    TaiwanStockCompareRequest(symbols=["A.TWSE", "B.TWSE"])  # min=2
    TaiwanStockCompareRequest(symbols=[f"S{i}.TWSE" for i in range(MAX_COMPARE_SYMBOLS)])  # max=5
    assert MIN_COMPARE_SYMBOLS == 2 and MAX_COMPARE_SYMBOLS == 5


# ── API Routing Regression (route-collision fix) ───────────────


def test_routing_compare_and_single_symbol_endpoints_do_not_collide():
    """Proves all four routes resolve to their correct handlers after the
    /stocks/compare* routes were registered before any /stocks/{symbol}* route."""
    client = TestClient(app, client=("127.0.0.1", 50000))

    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    stub_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    # 1. POST /stocks/compare -> comparison handler (NOT /stocks/{symbol}, symbol="compare")
    with patch("app.api.taiwan.TaiwanStockComparisonService", return_value=stub_svc):
        r1 = client.post("/api/taiwan/stocks/compare", json={"symbols": ["2330.TWSE", "2881.TWSE"]})
    assert r1.status_code == 200
    data1 = r1.json()
    assert "instruments" in data1  # TaiwanStockComparisonResponse shape
    assert "daily_history" not in data1  # NOT TaiwanStockDetailResponse shape (single-symbol handler)

    # 2. POST /stocks/compare/ai-research -> comparison AI handler (NOT /stocks/{symbol}/ai-research)
    with patch("app.api.taiwan.TaiwanComparisonAIResearchService") as mock_ai_svc_cls:
        from app.taiwan.comparison_ai_research import TaiwanComparisonAIResearchResponse
        import asyncio

        async def _fake_generate_comparison(symbols, target_date=None):
            return TaiwanComparisonAIResearchResponse(
                status="unavailable",
                error_code="provider_error",
                generated_at="2026-08-28T16:00:00+08:00",
            )

        mock_instance = MagicMock()
        mock_instance.generate_comparison = _fake_generate_comparison
        mock_ai_svc_cls.return_value = mock_instance

        r2 = client.post(
            "/api/taiwan/stocks/compare/ai-research",
            json={"symbols": ["2330.TWSE", "2881.TWSE"]},
        )
    assert r2.status_code == 200
    data2 = r2.json()
    assert "prompt_version" in data2
    assert data2["prompt_version"] == "taiwan_stock_comparison_v1"  # comparison prompt, not single-symbol Phase 7E's

    # 3. GET /stocks/2330.TWSE -> unaffected, still single-symbol handler
    r3 = client.get("/api/taiwan/stocks/2330.TWSE")
    assert r3.status_code == 200
    data3 = r3.json()
    assert "daily_history" in data3

    # 4. POST /stocks/2330.TWSE/ai-research -> unaffected, still single-symbol AI handler
    from unittest.mock import AsyncMock

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps({"overview": "路由回歸測試"})
        r4 = client.post("/api/taiwan/stocks/2330.TWSE/ai-research", json={"date": "2026-08-28"})
    assert r4.status_code == 200
    data4 = r4.json()
    assert data4["prompt_version"] == "taiwan_stock_research_v1"  # single-symbol Phase 7E prompt version
