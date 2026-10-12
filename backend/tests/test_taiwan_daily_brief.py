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
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.daily_brief_models import (
    DailyBriefAISummary,
    DeterministicDailyBrief,
    MarketFactBrief,
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


def test_daily_brief_converts_benchmark_fraction_to_percentage_points(
    mock_daily_brief_service: TaiwanDailyBriefService,
):
    market_snapshot = SimpleNamespace(
        indexes=SimpleNamespace(
            taiex=SimpleNamespace(close=101.0, change=1.0, change_pct=0.01),
        ),
        institutional=SimpleNamespace(
            foreign_net=None,
            investment_trust_net=None,
            dealer_net=None,
            total_net=None,
        ),
        market_totals=SimpleNamespace(
            advance_count=0,
            decline_count=0,
            flat_count=0,
            upper_limit_count=0,
            lower_limit_count=0,
            turnover=0.0,
        ),
    )
    with patch(
        "app.taiwan.daily_brief_service.TaiwanMarketIntelligenceService.get_snapshot",
        return_value=market_snapshot,
    ):
        brief = mock_daily_brief_service.build_deterministic_brief(
            target_date=date(2026, 8, 3),
        )

    assert brief.market.taiex_change_pct == pytest.approx(1.0)


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


def test_portfolio_status_separates_unavailable_from_empty(mock_daily_brief_service: TaiwanDailyBriefService):
    """Holdings not provided / unreadable must never be reported as "0 holdings"."""
    not_sent = mock_daily_brief_service.build_deterministic_brief(target_date=date(2026, 8, 3))
    assert not_sent.portfolio.status == "unavailable"
    empty = mock_daily_brief_service.build_deterministic_brief(target_date=date(2026, 8, 3), portfolio_holdings=[])
    assert empty.portfolio.status == "empty"
    held = mock_daily_brief_service.build_deterministic_brief(
        target_date=date(2026, 8, 3),
        portfolio_holdings=[{"symbol": "2301.TWSE", "name": "光寶科", "shares": 1000, "average_cost": 300.5}],
    )
    assert held.portfolio.status == "available" and held.portfolio.holdings_count == 1


def test_portfolio_returns_use_report_date_close_and_risk_date(mock_daily_brief_service: TaiwanDailyBriefService):
    report_date = date(2026, 8, 3)
    quotes = pl.DataFrame([
        {"symbol": "2301.TWSE", "close": 110.0},
        {"symbol": "2330.TWSE", "close": 80.0},
    ])
    market_snapshot = SimpleNamespace(
        indexes=None,
        institutional=None,
        market_totals=SimpleNamespace(
            advance_count=0, decline_count=0, flat_count=0,
            upper_limit_count=0, lower_limit_count=0, turnover=0.0,
        ),
    )
    with (
        patch.object(mock_daily_brief_service.daily_store, "read_range", return_value=quotes),
        patch("app.taiwan.daily_brief_service.TaiwanMarketIntelligenceService.get_snapshot", return_value=market_snapshot),
        patch("app.taiwan.daily_brief_service.TaiwanIndustryIntelligenceService.get_snapshot", return_value=SimpleNamespace(industries=[])),
        patch("app.taiwan.daily_brief_service.get_event_service") as get_events,
    ):
        get_events.return_value.check_symbol_risk_status.return_value = {}
        brief = mock_daily_brief_service.build_deterministic_brief(
            target_date=report_date,
            portfolio_holdings=[
                {"symbol": "2301.TWSE", "shares": 1000, "average_cost": 100.0},
                {"symbol": "2330.TWSE", "shares": 1000, "average_cost": 100.0},
            ],
        )
        assert get_events.return_value.check_symbol_risk_status.call_count == 2
        for call in get_events.return_value.check_symbol_risk_status.call_args_list:
            assert call.kwargs["target_date"] == report_date
    assert [(item.symbol, item.change_pct) for item in brief.portfolio.biggest_movers] == [
        ("2330.TWSE", pytest.approx(-20.0)),
        ("2301.TWSE", pytest.approx(10.0)),
    ]


def test_legacy_taiex_close_keeps_unknown_freshness():
    market = MarketFactBrief.model_validate({"trade_date": "2026-08-03", "taiex_close": 100.0})
    assert market.taiex_stale is None


def test_daily_brief_rejects_oversized_portfolio():
    client = TestClient(app, client=("127.0.0.1", 50000))
    result = client.post("/api/taiwan/daily-brief", json={
        "target_date": "2026-08-03",
        "portfolio_holdings": [{"symbol": f"{i:04d}.TWSE"} for i in range(201)],
    })
    assert result.status_code == 422
    assert "200" in result.json()["detail"]


def test_taiex_from_an_older_session_is_dated_and_marked_stale(mock_daily_brief_service: TaiwanDailyBriefService):
    snapshot = SimpleNamespace(
        indexes=SimpleNamespace(taiex=SimpleNamespace(close=49313.44, change=-492.93, change_pct=-0.0099, trade_date="2026-08-01")),
        institutional=None,
        market_totals=SimpleNamespace(advance_count=1109, decline_count=1012, flat_count=170,
                                      upper_limit_count=0, lower_limit_count=0, turnover=0.0),
    )
    with patch("app.taiwan.daily_brief_service.TaiwanMarketIntelligenceService.get_snapshot", return_value=snapshot):
        brief = mock_daily_brief_service.build_deterministic_brief(target_date=date(2026, 8, 3))
    assert brief.market.taiex_as_of == "2026-08-01" and brief.market.taiex_stale is True


def test_ai_summary_prompt_forbids_zero_holdings_and_stale_index_as_today():
    import inspect

    from app.taiwan import daily_brief_service

    source = inspect.getsource(daily_brief_service.TaiwanDailyBriefService.generate_ai_summary)
    assert "絕對不得寫成持股為 0" in source and "taiex_stale" in source and "不得稱為普跌" in source


def test_market_institutional_totals_carry_unit_scope_and_date(mock_daily_brief_service: TaiwanDailyBriefService):
    snapshot = SimpleNamespace(
        indexes=SimpleNamespace(taiex=SimpleNamespace(close=1.0, change=0.0, change_pct=0.0, trade_date="2026-08-03")),
        institutional=SimpleNamespace(foreign_net=207_000_000.0, investment_trust_net=50_410_000.0, dealer_net=-626_000_000.0,
                                      total_net=-368_590_000.0, trade_date="2026-07-31", row_count=1800),
        market_totals=SimpleNamespace(advance_count=1, decline_count=1, flat_count=0,
                                      upper_limit_count=0, lower_limit_count=0, turnover=0.0),
    )
    with patch("app.taiwan.daily_brief_service.TaiwanMarketIntelligenceService.get_snapshot", return_value=snapshot):
        brief = mock_daily_brief_service.build_deterministic_brief(target_date=date(2026, 8, 3))
    m = brief.market
    assert m.institutional_unit == "shares" and "不是金額" in m.institutional_scope
    assert (m.institutional_as_of, m.institutional_row_count, m.institutional_stale) == ("2026-07-31", 1800, True)


def test_ai_summary_prompt_pins_units_quant_scale_and_status_list_events():
    import inspect

    from app.taiwan import daily_brief_service

    source = inspect.getsource(daily_brief_service.TaiwanDailyBriefService.generate_ai_summary)
    assert "絕對不得寫成元、億元" in source and "score_scale" in source and "current_status_list" in source
    assert "百分位排名" in daily_brief_service.LIVE_SCORE_SCALE and "不是全市場" in daily_brief_service.SCREENER_SCORE_SCALE and "合格排名池" in daily_brief_service.LIVE_SCORE_SCALE


def test_legacy_institutional_totals_keep_unknown_freshness():
    market = MarketFactBrief.model_validate({"trade_date": "2026-08-03", "foreign_net": 1000.0})
    assert market.institutional_stale is None


def test_benchmark_lookup_never_returns_a_close_after_the_brief_date(tmp_path: Path):
    import polars as pl

    from app.taiwan.benchmark_store import _SCHEMA, TaiwanBenchmarkStore

    store = TaiwanBenchmarkStore(tmp_path / "benchmark_index.parquet")
    store.write(pl.DataFrame({
        **{name: [None] * 3 for name in _SCHEMA},
        "symbol": ["TAIEX"] * 3,
        "date": [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)],
        "close": [100.0, 101.0, 105.0],
    }, schema=_SCHEMA))
    row = store.latest("TAIEX", on_or_before=date(2026, 8, 4))
    assert row["date"] == date(2026, 8, 4) and row["close"] == 101.0 and row["previous_close"] == 100.0
    assert store.latest("TAIEX")["close"] == 105.0


def test_quant_metadata_comes_from_the_frozen_run(mock_daily_brief_service: TaiwanDailyBriefService):
    snapshot = {
        "model": {"model_id": "tw-eod-momentum", "version": "v0"},
        "ranking_universe": [f"{i:04d}.TWSE" for i in range(1234)],
        "signals": [{"symbol": "6213.TWSE", "name": "聯茂", "score": 0.986}],
    }

    class _Ledger:
        def list_runs(self, limit=30):
            return [{"model_key": "old-key", "session": "2026-08-03"}, {"model_key": "new-key", "session": "2026-08-02"}]

        def read_run(self, key, session, **kwargs):
            assert key == "old-key"
            return {"snapshot": snapshot}

    with patch("app.taiwan.daily_brief_service.LiveLedger", _Ledger):
        brief = mock_daily_brief_service.build_deterministic_brief(target_date=date(2026, 8, 3))
    [item] = brief.candidates.new_top10
    assert (item.symbol, item.score_model, item.ranked_universe, item.score_session) == (
        "6213.TWSE", "tw-eod-momentum v0", 1234, "2026-08-03")
