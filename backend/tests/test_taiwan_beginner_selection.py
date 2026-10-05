"""Beginner Stock Picker v1: deterministic, explainable screening priority."""
# ruff: noqa: RUF001
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan import beginner_selection as bs
from app.taiwan.beginner_selection import (
    BEGINNER_SELECTION_VERSION,
    BeginnerFacts,
    BeginnerSelectionService,
    evaluate_candidate,
    rank_candidates,
    summarize_market,
)

AS_OF = "2026-09-30"
FAVORABLE = summarize_market(AS_OF, 1500, 500, [("半導體業", 0.02), ("航運業", -0.01)])


def _rows(close: float, *, prior_high: float = 100.0, low: float | None = None) -> list[dict]:
    """20 prior sessions with a 100 closing high, then today's close."""
    start = date(2026, 9, 1)
    closes = [90.0] * 19 + [prior_high, close]
    return [
        {
            "date": (start + timedelta(days=i)).isoformat() if i < 20 else AS_OF,
            "close": c,
            "low": low if low is not None else round(c * 0.98, 1),
        }
        for i, c in enumerate(closes)
    ]


def _facts(**overrides) -> BeginnerFacts:
    close = overrides.pop("close", 99.0)
    base = {
        "symbol": "2330.TWSE", "name": "台積電", "industry": "半導體業",
        "instrument_type": "stock", "quote_date": AS_OF, "eligible_date": AS_OF,
        "close": close, "amount": 1_000_000_000.0, "trend_status": "verified",
        "adjusted_close": close, "ma20": 95.0, "momentum_5d": 0.02,
        "flow_ratio_5d": 0.02, "revenue_yoy": 15.0, "revenue_status": "available",
        "latest_eps": 5.0, "pe": 20.0, "risk_status": "clear", "attention_recent": False,
        "recent_corporate_action": False,
        "social_status": "available", "social_mentions": 12, "dcard_status": "available",
    }
    base.update(overrides)
    base.setdefault("daily", _rows(close) if close is not None else [])
    return BeginnerFacts(**base)


def _dims(candidate):
    return {d.key: d for d in candidate.dimensions}


def test_version_is_frozen():
    assert BEGINNER_SELECTION_VERSION == "beginner-selection-v1"


def test_wait_pullback_uses_trade_plan_zone():
    candidate, score = evaluate_candidate(_facts(close=99.0), FAVORABLE)
    assert candidate.selection_state == "wait_pullback"
    assert candidate.signal_strength == "strong" and score >= bs.STRONG_MIN_PRIORITY
    plan = candidate.trade_plan
    assert plan is not None and plan.rule_version == "trade_plan_v1"
    assert (plan.entry_zone_low, plan.entry_zone_high) == (94.0, 97.0)
    assert "94～97" in candidate.action_summary
    assert "跌破" in (candidate.invalidation or "")
    assert candidate.evidence_status == "complete"


def test_watch_when_price_is_inside_pullback_zone():
    candidate, _ = evaluate_candidate(_facts(close=95.5, ma20=92.0), FAVORABLE)
    assert candidate.selection_state == "watch"
    assert candidate.trade_plan is not None
    assert _dims(candidate)["price_position"].status == "positive"


def test_wait_breakout_when_trend_positive_but_far_below_high():
    candidate, _ = evaluate_candidate(_facts(close=92.0, ma20=90.0, momentum_5d=0.01), FAVORABLE)
    assert candidate.selection_state == "wait_breakout"
    assert candidate.trade_plan is not None and candidate.trade_plan.breakout_trigger == 100.0
    assert "突破 100" in candidate.action_summary


def test_neutral_trend_near_high_waits_for_breakout():
    candidate, _ = evaluate_candidate(_facts(close=99.0, ma20=99.5, momentum_5d=0.01), FAVORABLE)
    assert _dims(candidate)["trend"].status == "neutral"
    assert candidate.selection_state == "wait_breakout"


def test_no_chase_when_far_above_ma20():
    facts = _facts(close=115.0, ma20=100.0, daily=_rows(115.0, prior_high=116.0))
    candidate, _ = evaluate_candidate(facts, FAVORABLE)
    assert candidate.selection_state == "no_chase"
    assert candidate.trade_plan is None
    assert _dims(candidate)["price_position"].status == "negative"
    assert any(r.evidence_key == "price_position" for r in candidate.risks)


def test_weak_trend_is_skipped_with_reason():
    candidate, _ = evaluate_candidate(_facts(close=90.0, ma20=95.0, momentum_5d=-0.03), FAVORABLE)
    assert candidate.selection_state == "skip"
    assert candidate.reasons == []
    assert candidate.exclusion_reasons[0].reason_code == "trend_not_ready"


@pytest.mark.parametrize(("overrides", "code"), [
    ({"amount": 10_000_000.0}, "illiquid"),
    ({"amount": None}, "liquidity_unavailable"),
    ({"quote_date": "2026-09-29"}, "quote_stale"),
    ({"eligible_date": None}, "quote_stale"),
    ({"close": None, "adjusted_close": 99.0}, "quote_unavailable"),
    ({"trend_status": "unverified"}, "price_integrity"),
    ({"trend_status": "unavailable"}, "corporate_action_unverified"),
    ({"risk_status": "unavailable"}, "risk_unverified"),
    ({"risk_status": "flagged", "risk_reason": "處置"}, "regulatory_risk"),
    ({"instrument_type": "etf"}, "instrument_unsupported"),
    ({"in_universe": False}, "instrument_unsupported"),
])
def test_hard_filter_skips_and_explains(overrides, code):
    candidate, _ = evaluate_candidate(_facts(**overrides), FAVORABLE)
    assert candidate.selection_state == "skip"
    assert candidate.evidence_status == "insufficient"
    assert code in {r.reason_code for r in candidate.exclusion_reasons}
    assert candidate.action_summary == candidate.exclusion_reasons[0].display_text
    assert candidate.trade_plan is None


def test_unavailable_is_not_zero():
    candidate, score = evaluate_candidate(_facts(flow_ratio_5d=None, revenue_yoy=None), FAVORABLE)
    _full, full_score = evaluate_candidate(_facts(), FAVORABLE)
    dims = _dims(candidate)
    assert dims["capital_flow"].status == "unavailable"
    assert dims["capital_flow"].evidence == {}
    assert dims["fundamental_context"].status == "unavailable"
    assert "資金：法人資料目前不可用。" in candidate.data_gaps
    assert candidate.evidence_status == "partial"
    assert candidate.selection_state != "skip"  # optional evidence never removes the card
    assert score < full_score and candidate.signal_strength != "strong"
    zero, _ = evaluate_candidate(_facts(flow_ratio_5d=0.0), FAVORABLE)
    assert _dims(zero)["capital_flow"].status == "neutral"


def test_missing_trade_plan_never_invents_prices():
    candidate, _ = evaluate_candidate(_facts(recent_corporate_action=True), FAVORABLE)
    assert candidate.trade_plan is None
    assert candidate.plan_unavailable_reason
    assert _dims(candidate)["price_position"].status == "unavailable"
    assert re.search(r"\d", candidate.action_summary) is None
    assert re.search(r"\d", (candidate.invalidation or "").replace("20 日均線", "")) is None

    invalid_stop, _ = evaluate_candidate(_facts(daily=_rows(99.0, low=99.0)), FAVORABLE)
    assert invalid_stop.selection_state == "wait_pullback"
    assert invalid_stop.trade_plan is None
    assert re.search(r"\d", invalid_stop.action_summary) is None


def test_reasons_and_risks_trace_to_structured_evidence():
    facts = _facts(attention_recent=True)
    candidate, _ = evaluate_candidate(facts, FAVORABLE)
    dims = _dims(candidate)
    assert candidate.reasons and candidate.risks
    for item in [*candidate.reasons, *candidate.risks]:
        assert item.evidence_key in dims
        assert dims[item.evidence_key].evidence, item
        assert item.display_text == dims[item.evidence_key].explanation
        assert item.reason_code.startswith(item.evidence_key)
    assert len(candidate.reasons) <= 3 and len(candidate.risks) <= 3


def test_dcard_unavailable_is_shown_not_zero_and_social_never_ranks():
    partial, score = evaluate_candidate(
        _facts(social_status="partial", dcard_status="unavailable", social_mentions=3), FAVORABLE
    )
    social = _dims(partial)["social_attention"]
    assert "Dcard 來源目前不可用" in social.explanation
    _, base_score = evaluate_candidate(_facts(), FAVORABLE)
    assert score == base_score

    down, _ = evaluate_candidate(_facts(social_status="unavailable", social_mentions=None), FAVORABLE)
    social = _dims(down)["social_attention"]
    assert social.status == "unavailable" and social.explanation == "來源目前不可用。"
    assert "0" not in social.explanation
    assert down.selection_state != "skip"
    assert all(r.evidence_key != "social_attention" for r in down.reasons)

    weak, _ = evaluate_candidate(
        _facts(close=90.0, ma20=95.0, momentum_5d=-0.03, social_mentions=500), FAVORABLE
    )
    assert weak.selection_state == "skip"


def test_ranking_is_deterministic():
    facts = [
        _facts(symbol="B.TWSE", amount=5e8),
        _facts(symbol="A.TWSE", amount=5e8),
        _facts(symbol="C.TWSE", amount=9e8),
        _facts(symbol="D.TWSE", flow_ratio_5d=None),
        _facts(symbol="E.TWSE", close=90.0, ma20=95.0, momentum_5d=-0.03),
    ]
    amounts = {f.symbol: f.amount for f in facts}
    first = rank_candidates([evaluate_candidate(f, FAVORABLE) for f in facts], amounts)
    second = rank_candidates([evaluate_candidate(f, FAVORABLE) for f in reversed(facts)], amounts)
    assert [c.symbol for c in first] == ["C.TWSE", "A.TWSE", "B.TWSE", "D.TWSE"]
    assert [c.symbol for c in first] == [c.symbol for c in second]
    assert [c.rank for c in first] == [1, 2, 3, 4]
    assert "score" not in first[0].model_dump() and "priority" not in first[0].model_dump()


def test_market_summary_states():
    assert FAVORABLE.state == "favorable" and FAVORABLE.strongest_industries == ["半導體業"]
    assert summarize_market(AS_OF, 500, 500).state == "neutral"
    assert summarize_market(AS_OF, 300, 900).state == "cautious"
    unavailable = summarize_market(AS_OF, None, None)
    assert unavailable.state == "unavailable"
    candidate, _ = evaluate_candidate(_facts(), unavailable)
    assert _dims(candidate)["market_context"].status == "unavailable"
    assert candidate.selection_state != "skip"


class _FakeScreener:
    def __init__(self, items):
        self.items = items
        self.daily_store = SimpleNamespace(available_dates=lambda: [], read_range=lambda *a: None)
        self.action_store = SimpleNamespace(read_verified_window=lambda *a: ())

    def run(self, req):
        assert req.extended_factors is True
        items = [i for i in self.items if req.symbol_scope is None or i.symbol in req.symbol_scope]
        return SimpleNamespace(items=items, data_dates=SimpleNamespace(daily_as_of=AS_OF if items else None))

    def _compute_trend_indicators(self, symbols, as_of):
        return None, "unavailable", "corporate_actions"


def _item(symbol: str, **kw):
    base = dict(
        symbol=symbol, name=symbol, industry=None, instrument_type="stock", quote_date=AS_OF,
        close=99.0, amount=1e9, institutional_flow_ratio_5d=None, revenue_yoy=None,
        revenue_status="unavailable", latest_eps=None, pe=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_service_degrades_when_critical_evidence_missing(monkeypatch):
    monkeypatch.setattr(BeginnerSelectionService, "_market", lambda self, as_of: FAVORABLE)
    monkeypatch.setattr(BeginnerSelectionService, "_eligible_date", staticmethod(lambda: AS_OF))
    monkeypatch.setattr(BeginnerSelectionService, "_risk_context", staticmethod(lambda: None))
    svc = BeginnerSelectionService(screener=_FakeScreener([_item("1101.TWSE"), _item("2330.TWSE")]))
    result = svc.build(limit=5)
    assert result.status == "degraded" and result.candidates == []
    assert {c.symbol for c in result.not_selected} == {"1101.TWSE", "2330.TWSE"}
    assert "事件風險資料不可用" in result.data_gaps
    one = svc.evaluate_symbol("9999.TWSE").candidate
    assert one.selection_state == "skip"
    assert "instrument_unsupported" in {r.reason_code for r in one.exclusion_reasons}


def test_selection_snapshot_reuses_full_and_symbol_results(monkeypatch):
    bs.clear_beginner_selection_snapshot()
    key = (BEGINNER_SELECTION_VERSION, AS_OF, ("test",))
    monkeypatch.setattr(BeginnerSelectionService, "_cache_key", lambda self: key)
    calls = 0
    original = BeginnerSelectionService._build_snapshot

    def counted(self, snapshot_key):
        nonlocal calls
        calls += 1
        return original(self, snapshot_key)

    monkeypatch.setattr(BeginnerSelectionService, "_build_snapshot", counted)
    try:
        first = BeginnerSelectionService(screener=_FakeScreener([_item("2330.TWSE")])).build(limit=5)
        second = BeginnerSelectionService(screener=_FakeScreener([_item("2330.TWSE")])).build(limit=5)
        single = BeginnerSelectionService(screener=_FakeScreener([_item("2330.TWSE")])).evaluate_symbol("2330.TWSE")
    finally:
        bs.clear_beginner_selection_snapshot()

    assert calls == 1
    assert single.candidate.model_dump() == first.not_selected[0].model_dump()
    assert second.not_selected[0].model_dump() == first.not_selected[0].model_dump()


def test_selection_snapshot_single_flight(monkeypatch):
    bs.clear_beginner_selection_snapshot()
    key = (BEGINNER_SELECTION_VERSION, AS_OF, ("single-flight",))
    monkeypatch.setattr(BeginnerSelectionService, "_cache_key", lambda self: key)
    calls = 0
    original = BeginnerSelectionService._build_snapshot

    def slow_counted(self, snapshot_key):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return original(self, snapshot_key)

    monkeypatch.setattr(BeginnerSelectionService, "_build_snapshot", slow_counted)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(
                lambda _: BeginnerSelectionService(
                    screener=_FakeScreener([_item("2330.TWSE")])
                ).build(limit=5),
                range(3),
            ))
    finally:
        bs.clear_beginner_selection_snapshot()

    assert calls == 1
    assert [result.not_selected[0].symbol for result in results] == ["2330.TWSE"] * 3


def test_selection_snapshot_invalidates_when_source_identity_changes(monkeypatch, tmp_path):
    bs.clear_beginner_selection_snapshot()
    monkeypatch.setattr(BeginnerSelectionService, "_eligible_date", staticmethod(lambda: AS_OF))
    svc = BeginnerSelectionService(screener=_FakeScreener([_item("2330.TWSE")]))
    source_dir = tmp_path / "daily"
    source_dir.mkdir()
    source = source_dir / "source.marker"
    source.write_text("before", encoding="utf-8")
    svc.screener.daily_store._data_dir = source_dir
    calls = 0
    original = BeginnerSelectionService._build_snapshot

    def counted(self, key):
        nonlocal calls
        calls += 1
        return original(self, key)

    monkeypatch.setattr(BeginnerSelectionService, "_build_snapshot", counted)
    try:
        svc.build(limit=5)
        source.write_text("after", encoding="utf-8")
        svc.build(limit=5)
    finally:
        bs.clear_beginner_selection_snapshot()

    assert calls == 2


def test_selection_evidence_is_compact_and_frozen(monkeypatch):
    monkeypatch.setattr(BeginnerSelectionService, "_market", lambda self, as_of: FAVORABLE)
    svc = BeginnerSelectionService(screener=_FakeScreener([_item("2330.TWSE")]))
    payload = bs.selection_evidence("2330.TWSE", service=svc)
    assert payload is not None
    assert payload["version"] == BEGINNER_SELECTION_VERSION
    assert payload["semantics"] == "deterministic_screening_priority_not_return_forecast"
    assert payload["selection_state"] == "skip"
    assert {f"beginner_selection.{k}" for k in ("selection_state", "signal_strength")} <= bs.BEGINNER_EVIDENCE_REGISTRY_KEYS


def test_api_routes(monkeypatch):
    monkeypatch.setattr(BeginnerSelectionService, "__init__", lambda self, screener=None: setattr(
        self, "screener", _FakeScreener([_item("2330.TWSE")])
    ))
    monkeypatch.setattr(BeginnerSelectionService, "_market", lambda self, as_of: FAVORABLE)
    monkeypatch.setattr(BeginnerSelectionService, "_eligible_date", staticmethod(lambda: AS_OF))
    client = TestClient(app, client=("127.0.0.1", 50000))
    res = client.get("/api/taiwan/beginner-selection?limit=5")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == BEGINNER_SELECTION_VERSION
    assert body["disclaimer"] == "訊號強度代表目前條件符合程度，不代表上漲機率。"
    assert body["market"]["state"] == "favorable"
    assert client.get("/api/taiwan/beginner-selection?limit=21").status_code == 422
    one = client.get("/api/taiwan/beginner-selection/stocks/2330.TWSE")
    assert one.status_code == 200 and one.json()["candidate"]["symbol"] == "2330.TWSE"
    assert client.get("/api/taiwan/beginner-selection/stocks/not-a-symbol").status_code == 400


@pytest.mark.asyncio
async def test_ai_research_freezes_selection_evidence_without_reranking(taiwan_data_env):
    from app.taiwan.ai_research import _REPORT_CACHE, _REPORT_CACHE_LOCK, TaiwanAIResearchService

    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE.clear()
    selection = {"selection_state": "wait_pullback", "signal_strength": "medium", "reasons": []}
    report = {
        "overview": "x", "market_interpretation": "x", "industry_interpretation": "x",
        "price_technical_interpretation": "x", "institutional_interpretation": "x",
        "margin_interpretation": "x", "fundamentals_interpretation": "x",
        "abnormal_diagnostics_interpretation": "x",
        "key_observations": [{"text": "補充解讀", "evidence_refs": ["beginner_selection.selection_state"]}],
        "risk_factors": [], "missing_information": [],
    }
    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(report, ensure_ascii=False)
        run = await TaiwanAIResearchService().generate_run(
            "2330.TWSE", selection_evidence=selection, bypass_cache=True
        )
        prompt = mock_ai.call_args.args[0][-1]["content"]
    assert run.evidence_payload["beginner_selection"] == selection
    assert "beginner_selection.selection_state" in run.evidence_registry_keys
    assert '"wait_pullback"' in prompt
    assert selection == {"selection_state": "wait_pullback", "signal_strength": "medium", "reasons": []}

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(report, ensure_ascii=False)
        historical = await TaiwanAIResearchService().generate_run(
            "2330.TWSE", target_date=date(2026, 8, 28), selection_evidence=selection, bypass_cache=True
        )
    assert "beginner_selection" not in historical.evidence_payload


def test_stale_market_is_not_described_as_today():
    stale = bs.mark_stale(FAVORABLE, "2026-10-02")
    assert stale.state == "unavailable" and stale.headline == "資料尚未更新"
    assert AS_OF in stale.explanation and "先更新資料" in stale.guidance
    assert bs.mark_stale(FAVORABLE, AS_OF) is FAVORABLE


def test_service_reports_stale_quotes_as_global_gap(monkeypatch):
    monkeypatch.setattr(BeginnerSelectionService, "_market", lambda self, as_of: FAVORABLE)
    monkeypatch.setattr(BeginnerSelectionService, "_eligible_date", staticmethod(lambda: "2026-10-02"))
    result = BeginnerSelectionService(screener=_FakeScreener([_item("2330.TWSE")])).build(limit=5)
    assert result.market.state == "unavailable"
    assert f"行情停在 {AS_OF}，尚未更新到最新交易日" in result.data_gaps
    assert result.candidates == []


def test_no_chase_risk_survives_recent_corporate_action():
    facts = _facts(close=115.0, ma20=100.0, recent_corporate_action=True, daily=_rows(115.0, prior_high=116.0))
    candidate, _ = evaluate_candidate(facts, FAVORABLE)
    assert candidate.selection_state == "no_chase"
    assert _dims(candidate)["price_position"].status == "negative"
    assert any(r.evidence_key == "price_position" for r in candidate.risks)


def test_partial_social_names_the_source_it_counted():
    candidate, _ = evaluate_candidate(
        _facts(social_status="partial", dcard_status="unavailable", social_mentions=0), FAVORABLE
    )
    text = _dims(candidate)["social_attention"].explanation
    assert text.startswith("PTT 近期提及 0 次") and "Dcard 來源目前不可用" in text
