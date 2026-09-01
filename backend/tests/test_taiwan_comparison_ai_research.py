"""Tests for Optional AI Multi-Stock Objective Research Comparison Service (Phase 7G).

Mocks `generate_ai_text` (same pattern as test_taiwan_ai_research.py) and
stubs `TaiwanStockComparisonService` with fixture contexts (via the shared
`build_context` helper in test_taiwan_stock_comparison.py) so these tests are
fully independent of the local Taiwan daily/institutional/margin data cache.

Covers:
- valid grounded comparison, namespaced multi-symbol evidence refs
- invalid / mixed-valid namespaced evidence ref stripping
- one-sided observation (refs from only one symbol) accepted
- malformed provider JSON output -> invalid_output
- provider failure -> provider_error
- ranking/recommendation-language guard rejection (whole-response, not partial rewrite)
- exactly ONE generate_ai_text call regardless of symbol count
- AI is never invoked by the deterministic comparison path
"""
from datetime import date
from unittest.mock import AsyncMock, patch
import json
import pytest

from app.taiwan.comparison_ai_research import (
    TaiwanComparisonAIResearchService,
)
from tests.test_taiwan_stock_comparison import build_context, _make_stub_service


@pytest.mark.asyncio
async def test_valid_grounded_comparison_accepted():
    ctx_a = build_context("2330.TWSE", "2330", "台積電", return_5d=0.05)
    ctx_b = build_context("2881.TWSE", "2881", "富邦金", return_5d=-0.01)
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    mock_llm_json = {
        "comparison_overview": "2330.TWSE 與 2881.TWSE 近 5 日報酬呈現不同方向的資料表現。",
        "price_technical_comparison": "2330.TWSE 報酬率較高，2881.TWSE 報酬率為負值。",
        "key_observations": [
            {
                "text": "2330.TWSE 5 日報酬高於 2881.TWSE。",
                "evidence_refs": ["2330.TWSE.price_context.return_5d", "2881.TWSE.price_context.return_5d"],
            }
        ],
        "risk_factors": [],
        "missing_information": [],
    }

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
        resp = await svc.generate_comparison(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    assert resp.status == "success"
    assert resp.report is not None
    assert set(resp.report.symbols) == {"2330.TWSE", "2881.TWSE"}
    assert len(resp.report.key_observations) == 1
    assert resp.report.key_observations[0].evidence_refs == [
        "2330.TWSE.price_context.return_5d",
        "2881.TWSE.price_context.return_5d",
    ]
    assert mock_ai.call_count == 1  # exactly ONE LLM call for the whole comparison
    assert resp.report.disclaimer != ""


@pytest.mark.asyncio
async def test_invalid_namespaced_evidence_ref_stripped():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    mock_llm_json = {
        "comparison_overview": "測試",
        "key_observations": [
            {
                "text": "混合合法與非法引用。",
                "evidence_refs": ["2330.TWSE.price_context.close", "2330.TWSE.hallucinated.metric"],
            },
            {
                "text": "完全非法引用，應被丟棄。",
                "evidence_refs": ["9999.TWSE.made_up.key"],
            },
        ],
        "risk_factors": [],
        "missing_information": [],
    }

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
        resp = await svc.generate_comparison(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    assert resp.status == "success"
    assert len(resp.report.key_observations) == 1  # second observation dropped (0 valid refs)
    obs = resp.report.key_observations[0]
    assert obs.evidence_refs == ["2330.TWSE.price_context.close"]  # invalid ref stripped


@pytest.mark.asyncio
async def test_one_sided_observation_accepted():
    """An observation citing evidence from only ONE symbol is a legitimate comparison claim."""
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("00646.TWSE", "00646", "元大S&P500", instrument_type="etf", etf_leverage=1.0)
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "00646.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    mock_llm_json = {
        "comparison_overview": "測試",
        "fundamentals_comparison": "2330.TWSE 提供個股基本面資料，00646.TWSE 為 ETF 不適用個別公司財務指標。",
        "key_observations": [
            {
                "text": "2330.TWSE 提供本益比資料，此指標於 ETF 標的不適用。",
                "evidence_refs": ["2330.TWSE.fundamentals_context.pe"],
            }
        ],
        "risk_factors": [],
        "missing_information": [],
    }

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
        resp = await svc.generate_comparison(["2330.TWSE", "00646.TWSE"], target_date=date(2026, 8, 28))

    assert resp.status == "success"
    assert len(resp.report.key_observations) == 1
    assert resp.report.key_observations[0].evidence_refs == ["2330.TWSE.fundamentals_context.pe"]


@pytest.mark.asyncio
async def test_malformed_json_response_graceful_handling():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = "Sorry, I cannot output JSON today."
        resp = await svc.generate_comparison(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    assert resp.status == "unavailable"
    assert resp.error_code == "invalid_output"
    assert resp.report is None


@pytest.mark.asyncio
async def test_provider_failure_graceful_handling():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.side_effect = TimeoutError("upstream timeout")
        resp = await svc.generate_comparison(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    assert resp.status == "unavailable"
    assert resp.error_code == "provider_error"
    assert resp.report is None


@pytest.mark.asyncio
async def test_ranking_language_guard_rejects_whole_response_zh():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    mock_llm_json = {
        "comparison_overview": "客觀摘要正常文字。",
        "risk_factors": [
            {
                "text": "2330.TWSE 是最佳選擇，因此風險較低。",  # forbidden ranking phrase
                "evidence_refs": ["2330.TWSE.price_context.close"],
            }
        ],
        "key_observations": [],
        "missing_information": [],
    }

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
        resp = await svc.generate_comparison(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    # Whole response rejected — NOT a partial rewrite with the offending sentence stripped.
    assert resp.status == "unavailable"
    assert resp.error_code == "invalid_output"
    assert resp.report is None


@pytest.mark.asyncio
async def test_ranking_language_guard_rejects_whole_response_en():
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    mock_llm_json = {
        "comparison_overview": "2330.TWSE is the best pick among the compared instruments.",
        "key_observations": [],
        "risk_factors": [],
        "missing_information": [],
    }

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
        resp = await svc.generate_comparison(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    assert resp.status == "unavailable"
    assert resp.error_code == "invalid_output"
    assert resp.report is None


@pytest.mark.asyncio
async def test_objective_comparative_language_is_not_rejected():
    """Sanity check: objective comparison prose must NOT trip the guard (would break the feature)."""
    ctx_a = build_context("2330.TWSE", "2330", "台積電", return_5d=0.05)
    ctx_b = build_context("2881.TWSE", "2881", "富邦金", return_5d=-0.01)
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})
    svc = TaiwanComparisonAIResearchService(comparison_svc=comparison_svc)

    mock_llm_json = {
        "comparison_overview": "2330.TWSE 報酬率較高，RSI 較高，相對強度較強。",
        "key_observations": [],
        "risk_factors": [],
        "missing_information": [],
    }

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
        resp = await svc.generate_comparison(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))

    assert resp.status == "success"


@pytest.mark.asyncio
async def test_deterministic_comparison_never_invokes_ai():
    """Opening/loading the deterministic comparison must never call the AI provider."""
    ctx_a = build_context("2330.TWSE", "2330", "台積電")
    ctx_b = build_context("2881.TWSE", "2881", "富邦金")
    comparison_svc = _make_stub_service({"2330.TWSE": ctx_a, "2881.TWSE": ctx_b})

    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        comparison_svc.compare(["2330.TWSE", "2881.TWSE"], target_date=date(2026, 8, 28))
        assert mock_ai.call_count == 0
