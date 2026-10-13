from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.taiwan.ai_advice import (
    _ADVICE_CACHE,
    _ADVICE_INFLIGHT,
    _CACHE_LOCK,
    _DEBATE_CACHE,
    _DEBATE_INFLIGHT,
    TaiwanAIAdviceService,
)
from app.taiwan.ai_debate import parse_verdict
from app.taiwan.ai_research import TaiwanAIResearchRequest
from app.taiwan.buy_point import BuyPointSignal, builtin_presets
from app.taiwan.buy_point_service import BuyPointCandidate

REF = "buy_point.status"


@pytest.fixture(autouse=True)
def _clear_caches():
    with _CACHE_LOCK:
        for cache in (_ADVICE_CACHE, _ADVICE_INFLIGHT, _DEBATE_CACHE, _DEBATE_INFLIGHT):
            cache.clear()


def _config(model: str):
    return SimpleNamespace(
        provider="test", model=model, api_key=f"key-{model}", base_url="https://example.test/v1",
        profile_name=None, user_agent=None, reasoning_effort=None, codex_reasoning_effort=None,
        max_output_tokens=None, context_window=None, codex_command="codex",
    )


QUICK, DEEP = _config("quick-model"), _config("deep-model")


def _snapshot(tier="quick"):
    return DEEP if tier == "deep" else QUICK


class _BuyPoints:
    def candidate(self, *args, **kwargs):
        return BuyPointCandidate(
            strategy=builtin_presets()[0],
            strategy_definition_digest="strategy-digest",
            signal=BuyPointSignal(
                strategy_id="quant_pullback", symbol="2330.TWSE",
                detected_at="2026-10-02T10:00:00+08:00", data_as_of="2026-10-01",
                status="waiting", price=100, explanation="test", freshness="daily_cached",
            ),
            plan_unavailable_reason="buy_point_waiting",
        )


ADVICE_JSON = json.dumps({
    "action": "wait", "summary": "等待", "rationale": [], "conditions": [],
    "invalidation": [], "data_gaps": [], "selected_plan_instance_id": None,
})
BULL = json.dumps({"points": [{"text": "法人買超", "evidence_refs": [REF, "fake"]}]}, ensure_ascii=False)
BEAR = json.dumps({
    "points": [{"text": "訊號未觸發", "evidence_refs": [REF]}],
    "rebuttals": [{"text": "買超只有一天", "evidence_refs": [REF]}],
}, ensure_ascii=False)


def test_verdict_rules_follow_judge_contract():
    allowed = {REF}
    point = [{"text": "決定點", "evidence_refs": [REF]}]
    win = parse_verdict({"stronger_side": "bear", "margin": "narrow", "decisive_points": point}, allowed)
    assert (win.stronger_side, win.margin, len(win.decisive_points)) == ("bear", "narrow", 1)
    # balanced never carries a margin, even if the model sends one
    assert parse_verdict({"stronger_side": "balanced", "margin": "decisive"}, allowed).margin is None
    with pytest.raises(ValueError, match="margin"):
        parse_verdict({"stronger_side": "bull", "decisive_points": point}, allowed)
    with pytest.raises(ValueError, match="decisive point"):
        parse_verdict({"stronger_side": "bull", "margin": "decisive",
                       "decisive_points": [{"text": "x", "evidence_refs": ["fake"]}]}, allowed)
    with pytest.raises(ValueError, match="prices"):
        parse_verdict({"stronger_side": "balanced", "target_price": 120}, allowed)


def test_debate_flag_is_advice_only():
    with pytest.raises(ValueError, match="debate"):
        TaiwanAIResearchRequest(purpose="research", debate=True)
    assert TaiwanAIResearchRequest(purpose="advice", strategy_id="s", debate=True).debate is True


@pytest.mark.asyncio
async def test_debate_uses_quick_sides_deep_judge_and_feeds_advice(taiwan_data_env, tmp_path):
    judge = json.dumps({
        "stronger_side": "bear", "margin": "decisive",
        "decisive_points": [{"text": "訊號未觸發", "evidence_refs": [REF]}],
        "what_would_change": ["買點訊號觸發"],
    }, ensure_ascii=False)
    svc = TaiwanAIAdviceService(tmp_path, buy_point_svc=_BuyPoints())
    with (
        patch("app.taiwan.ai_advice.snapshot_ai_provider_config", side_effect=_snapshot),
        patch("app.taiwan.ai_debate.generate_structured_ai_text", new_callable=AsyncMock,
              side_effect=[BULL, BEAR, judge]) as debate_llm,
        patch("app.taiwan.ai_advice._call_provider", new_callable=AsyncMock, return_value=ADVICE_JSON) as advice_llm,
    ):
        run, _ = await svc.generate("2330.TWSE", strategy_id="quant_pullback", debate=True)

    response = run.response
    assert response.status == "success"
    assert response.debate_status == "success"
    assert response.debate.verdict.stronger_side == "bear"
    assert response.debate.bull_points[0].evidence_refs == [REF]  # unknown key dropped
    assert [call.kwargs["config_snapshot"].model for call in debate_llm.await_args_list] == [
        "quick-model", "quick-model", "deep-model",
    ]
    assert advice_llm.await_args.args[1].model == "deep-model"
    assert response.model == "deep-model"
    assert "多空辯論裁判結論" in advice_llm.await_args.args[0][1]["content"]


@pytest.mark.asyncio
async def test_failed_debate_still_returns_advice_without_debate_context(taiwan_data_env, tmp_path):
    bad_judge = json.dumps({"stronger_side": "bull", "margin": "decisive", "decisive_points": []})
    svc = TaiwanAIAdviceService(tmp_path, buy_point_svc=_BuyPoints())
    with (
        patch("app.taiwan.ai_advice.snapshot_ai_provider_config", side_effect=_snapshot),
        patch("app.taiwan.ai_debate.generate_structured_ai_text", new_callable=AsyncMock,
              side_effect=[BULL, BEAR, bad_judge]),
        patch("app.taiwan.ai_advice._call_provider", new_callable=AsyncMock, return_value=ADVICE_JSON) as advice_llm,
    ):
        run, _ = await svc.generate("2330.TWSE", strategy_id="quant_pullback", debate=True)

    assert run.response.status == "success"
    assert run.response.debate_status == "unavailable"
    assert run.response.debate is None
    assert "多空辯論" not in advice_llm.await_args.args[0][1]["content"]
