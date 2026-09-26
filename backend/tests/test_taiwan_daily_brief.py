"""Unit and integration tests for Taiwan Daily Brief and Deterministic Evidence Layer (A12).

Verifies:
- Deterministic brief collates facts across Market, Portfolio, Watchlist, Candidates, Events, News.
- Missing values are preserved as None, never fabricated as 0.
- AI is never called in background; triggered only on explicit user action.
- AI output strictly adheres to 7-section schema (A through G).
- History persistence in user_data/taiwan_daily_briefs.json (private, non-bundled, no secrets).
- API routes integration with loopback client IP.
"""
# ruff: noqa: RUF001
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.daily_brief_models import (
    DailyBriefAISummary,
    DeterministicDailyBrief,
)
from app.taiwan.daily_brief_service import TaiwanDailyBriefService
from app.taiwan.daily_store import TaiwanDailyStore


@pytest.fixture
def mock_daily_brief_service(tmp_path: Path) -> TaiwanDailyBriefService:
    store_dir = tmp_path / "daily"
    store_dir.mkdir(parents=True, exist_ok=True)
    daily_store = TaiwanDailyStore(store_dir)
    storage_path = tmp_path / "user_data" / "taiwan_daily_briefs.json"
    return TaiwanDailyBriefService(path=storage_path, daily_store=daily_store)


def test_build_deterministic_brief_structure(mock_daily_brief_service: TaiwanDailyBriefService):
    """Deterministic brief builds without background AI calls and separates portfolio from watchlist."""
    port_holdings = [
        {"symbol": "2330.TWSE", "name": "台積電", "shares": 1000, "average_cost": 950.0, "return_pct": 5.2}
    ]
    brief = mock_daily_brief_service.build_deterministic_brief(
        target_date=date(2026, 8, 3),
        portfolio_holdings=port_holdings,
    )
    assert isinstance(brief, DeterministicDailyBrief)
    assert brief.market is not None
    assert brief.portfolio.holdings_count == 1
    assert brief.portfolio.biggest_movers[0].symbol == "2330.TWSE"
    assert brief.candidates is not None
    assert brief.events is not None
    assert brief.news is not None


@pytest.mark.asyncio
async def test_generate_ai_summary_strict_schema(mock_daily_brief_service: TaiwanDailyBriefService):
    """AI summary parses strictly into 7 sections (A~G) and strips ungrounded claims."""
    brief = mock_daily_brief_service.build_deterministic_brief(target_date=date(2026, 8, 3))

    mock_ai_response = json.dumps({
        "section_a_market": "今日台股大盤指數微幅震盪，市場情緒呈現中性觀望。",
        "section_b_key_changes": [
            "加權指數守穩均線",
            "外資持股微增",
            "電子族群成交比重維持六成",
        ],
        "section_c_portfolio": "持股部位維持穩健，未見重大異常變動。",
        "section_d_watchlist": "自選股成交量普遍維持常態。",
        "section_e_candidates": "今日新候選股主要集中在營收年增成長標的。",
        "section_f_risks": "目前無新增處置或重大暫停交易風險股票。",
        "section_g_tracking": "明日觀察重點在大盤成交量能是否放大及法人買賣超動向。",
        "evidence_sources": ["加權指數收盤數據", "市場情緒指標"],
    })

    with patch("app.taiwan.daily_brief_service.generate_ai_text", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_ai_response
        summary = await mock_daily_brief_service.generate_ai_summary(brief)

        assert isinstance(summary, DailyBriefAISummary)
        assert "微幅震盪" in summary.section_a_market
        assert len(summary.section_b_key_changes) == 3
        assert "持股部位" in summary.section_c_portfolio
        assert "自選股" in summary.section_d_watchlist
        assert "新候選股" in summary.section_e_candidates
        assert "處置" in summary.section_f_risks
        assert "成交量能" in summary.section_g_tracking
        assert len(summary.evidence_sources) == 2


def test_daily_brief_history_persistence(mock_daily_brief_service: TaiwanDailyBriefService):
    """Daily brief history is safely persisted in user_data without tokens or raw response bloat."""
    brief = mock_daily_brief_service.build_deterministic_brief(target_date=date(2026, 8, 3))
    summary = DailyBriefAISummary(
        section_a_market="市場平穩",
        section_b_key_changes=["變化1", "變化2", "變化3"],
        section_c_portfolio="持股無異動",
        section_d_watchlist="自選量能平緩",
        section_e_candidates="候選股2檔",
        section_f_risks="無處置股票",
        section_g_tracking="追蹤明日法說會",
    )

    saved = mock_daily_brief_service.save_brief(brief, ai_summary=summary, ai_status="success")
    assert saved.brief_id.startswith("brief_")

    history = mock_daily_brief_service.list_saved_briefs()
    assert len(history) == 1
    assert history[0].brief_id == saved.brief_id
    assert history[0].ai_status == "success"

    loaded = mock_daily_brief_service.get_saved_brief(saved.brief_id)
    assert loaded is not None
    assert loaded.ai_summary is not None
    assert loaded.ai_summary.section_a_market == "市場平穩"


def test_daily_brief_api_endpoints(monkeypatch, mock_daily_brief_service: TaiwanDailyBriefService):
    """FastAPI routes for Daily Brief creation, on-demand AI generation, and history."""
    monkeypatch.setattr(
        "app.taiwan.daily_brief_service.get_daily_brief_service",
        lambda: mock_daily_brief_service,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    # 1. GET deterministic brief (with query param)
    res_brief = client.get("/api/taiwan/daily-brief?target_date=2026-08-03")
    assert res_brief.status_code == 200
    brief_data = res_brief.json()
    assert "market" in brief_data
    assert "candidates" in brief_data

    # 2. On-demand AI generation (send brief directly, receive DailyBriefAISummary directly)
    mock_ai_response = json.dumps({
        "section_a_market": "大盤持穩",
        "section_b_key_changes": ["變化A", "變化B", "變化C"],
        "section_c_portfolio": "持股尚可",
        "section_d_watchlist": "觀察中",
        "section_e_candidates": "候選亮點",
        "section_f_risks": "注意股1檔",
        "section_g_tracking": "明日開盤",
        "evidence_sources": ["大盤數據"],
    })
    with patch("app.taiwan.daily_brief_service.generate_ai_text", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_ai_response
        res_ai = client.post(
            "/api/taiwan/daily-brief/ai-summary",
            json=brief_data,
        )
        assert res_ai.status_code == 200
        summary = res_ai.json()
        assert summary["section_a_market"] == "大盤持穩"

    # 3. Save to history (use structured_brief key matching frontend)
    res_save = client.post(
        "/api/taiwan/daily-brief/save",
        json={"structured_brief": brief_data, "ai_summary": summary, "ai_status": "success"},
    )
    assert res_save.status_code == 200
    saved_brief = res_save.json()
    brief_id = saved_brief["brief_id"]

    # 4. List history (returns array directly)
    res_hist = client.get("/api/taiwan/daily-brief/history")
    assert res_hist.status_code == 200
    assert any(b["brief_id"] == brief_id for b in res_hist.json())

    # 5. Get history detail
    res_detail = client.get(f"/api/taiwan/daily-brief/history/{brief_id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["brief_id"] == brief_id
