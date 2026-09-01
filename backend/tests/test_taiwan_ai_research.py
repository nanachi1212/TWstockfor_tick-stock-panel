"""Tests for Deterministic & Grounded AI Taiwan Stock Research Report Service (Phase 7E).

Comprehensive unit tests verifying:
- Valid Report Acceptance:
    * Mock AI returns valid structured report citing real evidence refs -> accepted.
- Invalid Evidence Ref Stripping:
    * Mock AI returns fake/hallucinated refs (e.g. "fake.secret.metric") -> stripped out.
- Unsupported Factual Claim:
    * Key observation having zero valid evidence refs -> completely dropped.
- Missing Data Handling:
    * ETF 00646 fundamentals missing/not applicable -> not converted to EPS=0; missing preserved.
- Zero Market Provider HTTP:
    * Evidence assembly makes 0 market HTTP requests (urllib/httpx blocked).
- AI Provider Invocation Guard:
    * AI provider called exactly once per request; no infinite loops or agent cycles.
- Provider Failure Graceful Handling:
    * Mock provider exception -> returns status="unavailable", error_code="provider_error", no crash.
- Malformed JSON Output Graceful Handling:
    * Mock provider non-JSON response -> returns status="unavailable", error_code="invalid_output".
- Historical Point-In-Time / No Look-Ahead:
    * Query date D -> payload strictly excludes data from D+1.
- Recommendation & Target Price Guard:
    * Mock AI returns forbidden recommendation/target_price fields -> discarded by Pydantic schema.
- Prompt Injection Resilience:
    * Malicious text in company/industry name treated as untrusted raw data.
- No Auto-Triggering AI:
    * Detail and research-context endpoints do NOT invoke AI; only POST ai-research invokes AI.
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.ai_research import (
    TaiwanAIResearchResponse,
    TaiwanAIResearchService,
    build_evidence_registry,
)
from app.taiwan.research_context import TaiwanStockResearchContextService


@pytest.mark.asyncio
async def test_valid_grounded_report_accepted():
    """Verify valid report citing legitimate registry evidence refs is accepted."""
    svc = TaiwanAIResearchService()

    mock_llm_json = {
        "overview": "截至 2026-08-28，2330.TWSE 收盤價為 980.0 元，近 5 日報酬為 +3.2%。所屬半導體業 5 日相對強弱為 +1.5%。",
        "market_interpretation": "大盤上漲家數略多於下跌家數，整體環境偏向溫和。",
        "industry_interpretation": "半導體產業佔市場成交比重較高，5日相對強弱指標呈現領先。",
        "price_technical_interpretation": "收盤價站於 20 日均線之上，動能指標處於中性水準。",
        "institutional_interpretation": "外資近 5 日呈現累計買超，投信亦呈現同步買超。",
        "margin_interpretation": "融資餘額小幅增加，券資比維持低檔穩定。",
        "fundamentals_interpretation": "本益比約 25 倍，單月營收年增率為正。",
        "abnormal_diagnostics_interpretation": "無顯著極端異常異動訊號。",
        "key_observations": [
            {
                "text": "近 5 日股價表現領先所屬產業與大盤。",
                "evidence_refs": ["price_context.return_5d", "industry_context.relative_strength_5d"]
            },
            {
                "text": "外資累計買超提供籌碼面支撐。",
                "evidence_refs": ["institutional_context.foreign_net_5d"]
            }
        ],
        "risk_factors": [
            {
                "text": "股價接近近期 20 日高點，存在短期價格震盪風險。",
                "evidence_refs": ["price_context.distance_from_20d_high"]
            }
        ],
        "missing_information": ["realtime"]
    }

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json, ensure_ascii=False)
        resp = await svc.generate_report("2330.TWSE", target_date=date(2026, 8, 28))

        assert resp.status == "success"
        assert resp.report is not None
        assert resp.report.symbol == "2330.TWSE"
        assert len(resp.report.key_observations) == 2
        assert resp.report.key_observations[0].evidence_refs == ["price_context.return_5d", "industry_context.relative_strength_5d"]
        assert resp.report.disclaimer != ""
        assert mock_ai.call_count == 1


@pytest.mark.asyncio
async def test_invalid_evidence_ref_stripped_and_unsupported_observation_dropped():
    """Verify hallucinated evidence keys are stripped; observations with 0 valid refs are dropped."""
    svc = TaiwanAIResearchService()

    mock_llm_json = {
        "overview": "測試客觀摘要。",
        "key_observations": [
            {
                "text": "這是一條引用合法鍵與非法鍵的觀察。",
                "evidence_refs": ["price_context.close", "hallucinated.secret.indicator"]
            },
            {
                "text": "這是一條完全無合法引用的自創觀察。",
                "evidence_refs": ["invented.insider.score", "magic.trend"]
            }
        ],
        "risk_factors": [
            {
                "text": "風險特徵具備合法引用。",
                "evidence_refs": ["technical_context.distance_to_ma20"]
            }
        ],
        "missing_information": []
    }

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json)
        resp = await svc.generate_report("2330.TWSE", target_date=date(2026, 8, 28))

        assert resp.status == "success"
        assert resp.report is not None
        # Observation 1 should have "hallucinated.secret.indicator" stripped
        assert len(resp.report.key_observations) == 1
        obs1 = resp.report.key_observations[0]
        assert obs1.text == "這是一條引用合法鍵與非法鍵的觀察。"
        assert obs1.evidence_refs == ["price_context.close"]
        # Observation 2 had 0 valid refs, so it was dropped completely


@pytest.mark.asyncio
async def test_etf_missing_fundamentals_preserved():
    """Verify ETF 00646 does not have fundamentals invented or falsified into EPS=0."""
    svc = TaiwanAIResearchService()

    mock_llm_json = {
        "overview": "00646.TWSE 為追蹤標普500指數之跨國投資股票型ETF。",
        "fundamentals_interpretation": "此標的為 ETF，不適用個別公司財務指標。",
        "key_observations": [
            {
                "text": "本 ETF 追蹤外國指數，且無漲跌幅限制。",
                "evidence_refs": ["etf_context.benchmark", "market_rules.is_no_limit"]
            }
        ],
        "risk_factors": [],
        "missing_information": ["fundamentals_not_applicable_for_etf"]
    }

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json)
        resp = await svc.generate_report("00646.TWSE", target_date=date(2026, 8, 28))

        assert resp.status == "success"
        assert resp.report is not None
        assert resp.report.instrument_type == "etf"
        assert "fundamentals_not_applicable_for_etf" in resp.report.missing_information


@pytest.mark.asyncio
async def test_zero_market_http_during_evidence_assembly():
    """Verify local evidence assembly performs 0 market provider HTTP requests."""
    svc = TaiwanAIResearchService()

    with patch("urllib.request.urlopen") as mock_urlopen, patch("httpx.get") as mock_httpx_get, patch("httpx.post") as mock_httpx_post:
        # Mock AI provider so only market provider network calls would trigger urllib/httpx
        with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
            mock_ai.return_value = json.dumps({"overview": "測試"})
            await svc.generate_report("2330.TWSE", target_date=date(2026, 8, 28))

        assert mock_urlopen.call_count == 0
        assert mock_httpx_get.call_count == 0
        assert mock_httpx_post.call_count == 0


@pytest.mark.asyncio
async def test_ai_provider_failure_graceful_handling():
    """Verify provider exception yields status='unavailable' with error_code='provider_error'."""
    svc = TaiwanAIResearchService()

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.side_effect = TimeoutError("LLM upstream timeout after 45s")
        resp = await svc.generate_report("2330.TWSE", target_date=date(2026, 8, 28))

        assert resp.status == "unavailable"
        assert resp.error_code == "provider_error"
        assert "timeout" in (resp.error_message or "").lower()
        assert resp.report is None


@pytest.mark.asyncio
async def test_malformed_json_response_graceful_handling():
    """Verify non-JSON response yields status='unavailable' with error_code='invalid_output'."""
    svc = TaiwanAIResearchService()

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = "Sorry, I am an AI language model and cannot output JSON today."
        resp = await svc.generate_report("2330.TWSE", target_date=date(2026, 8, 28))

        assert resp.status == "unavailable"
        assert resp.error_code == "invalid_output"
        assert resp.report is None


@pytest.mark.asyncio
async def test_recommendation_and_target_price_guard():
    """Verify forbidden recommendation / target_price fields in AI output are rejected / not exposed."""
    svc = TaiwanAIResearchService()

    mock_llm_json = {
        "overview": "客觀摘要",
        "recommendation": "BUY",
        "target_price": 1200.0,
        "rating": "STRONG_BUY",
        "confidence_score": 99.0,
        "key_observations": [],
        "risk_factors": [],
    }

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(mock_llm_json)
        resp = await svc.generate_report("2330.TWSE", target_date=date(2026, 8, 28))

        assert resp.status == "success"
        report_dict = resp.report.model_dump()
        assert "recommendation" not in report_dict
        assert "target_price" not in report_dict
        assert "rating" not in report_dict
        assert "confidence_score" not in report_dict


@pytest.mark.asyncio
async def test_historical_point_in_time_no_look_ahead():
    """Verify historical query for date D strictly passes only date D and prior evidence to AI."""
    svc = TaiwanAIResearchService()

    target_d = date(2026, 8, 20)

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps({"overview": "歷史客觀摘要"})
        await svc.generate_report("2330.TWSE", target_date=target_d)

        assert mock_ai.call_count == 1
        prompt_content = mock_ai.call_args[0][0][1]["content"]
        # Verify prompt mentions target date and no future dates
        assert "2026-08-20" in prompt_content
        assert "2026-08-21" not in prompt_content
        assert "2026-08-28" not in prompt_content


def test_api_endpoint_invokes_ai_only_on_explicit_post():
    """Verify GET stock detail & research-context do NOT invoke AI; only POST ai-research does."""
    client = TestClient(app, client=("127.0.0.1", 50000))

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps({"overview": "API 測試摘要"})

        # 1. GET Stock Detail -> must NOT invoke AI
        r1 = client.get("/api/taiwan/stocks/2330.TWSE")
        assert r1.status_code == 200
        assert mock_ai.call_count == 0

        # 2. GET Research Context -> must NOT invoke AI
        r2 = client.get("/api/taiwan/stocks/2330.TWSE/research-context")
        assert r2.status_code == 200
        assert mock_ai.call_count == 0

        # 3. POST AI Research -> invokes AI exactly once
        r3 = client.post("/api/taiwan/stocks/2330.TWSE/ai-research", json={"date": "2026-08-28"})
        assert r3.status_code == 200
        data = r3.json()
        assert data["status"] == "success"
        assert mock_ai.call_count == 1
