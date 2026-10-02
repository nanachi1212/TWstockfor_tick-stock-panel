from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.taiwan.ai_advice import (
    _ADVICE_CACHE,
    _ADVICE_INFLIGHT,
    _CACHE_LOCK,
    _REVIEW_CACHE,
    _REVIEW_INFLIGHT,
    TaiwanAIAdviceResponse,
    TaiwanAIAdviceRun,
    TaiwanAIAdviceService,
    TaiwanAIReviewRun,
    _advice_cache_key,
    _parse_advice,
    _parse_review,
)
from app.taiwan.ai_research import TaiwanAIResearchRequest
from app.taiwan.ai_research_history import TaiwanAIResearchHistoryStore
from app.taiwan.buy_point import BuyPointSignal, builtin_presets
from app.taiwan.buy_point_service import BuyPointCandidate


def _signal(status="waiting") -> BuyPointSignal:
    return BuyPointSignal(
        strategy_id="quant_pullback",
        symbol="2330.TWSE",
        detected_at="2026-10-02T10:00:00+08:00",
        data_as_of="2026-10-01",
        status=status,
        price=100,
        explanation="test",
        freshness="daily_cached",
    )


def _candidate(status="waiting") -> BuyPointCandidate:
    return BuyPointCandidate(
        strategy=builtin_presets()[0],
        strategy_definition_digest="strategy-digest",
        signal=_signal(status),
        plan_unavailable_reason=f"buy_point_{status}",
    )


@pytest.fixture(autouse=True)
def _clear_advice_caches():
    with _CACHE_LOCK:
        _ADVICE_CACHE.clear()
        _REVIEW_CACHE.clear()
        _ADVICE_INFLIGHT.clear()
        _REVIEW_INFLIGHT.clear()


def _provider_config():
    return SimpleNamespace(
        provider="test",
        model="model",
        api_key="secret",
        base_url="https://example.test/v1",
        profile_name=None,
        user_agent=None,
        reasoning_effort=None,
        codex_reasoning_effort=None,
        max_output_tokens=None,
        context_window=None,
        codex_command="codex",
    )


def test_advice_cache_key_covers_prompt_visible_strategy_and_missing_items():
    common = {
        "config": _provider_config(),
        "generation_config": {"provider": "test", "model": "model"},
        "strategy_id": "quant_pullback",
        "strategy_definition_digest": "behavior",
        "evidence_digest": "evidence",
        "candidate_plan_ids": [],
    }
    original = _advice_cache_key(
        **common,
        strategy_payload={"name": "原名稱", "description": "原說明", "category": "價格"},
        missing_items=["fundamentals"],
    )
    renamed = _advice_cache_key(
        **common,
        strategy_payload={"name": "新名稱", "description": "原說明", "category": "價格"},
        missing_items=["fundamentals"],
    )
    different_missing = _advice_cache_key(
        **common,
        strategy_payload={"name": "原名稱", "description": "原說明", "category": "價格"},
        missing_items=["institutional"],
    )
    assert original != renamed
    assert original != different_missing


def test_request_requires_strategy_for_advice_and_review_only_applies_to_advice():
    with pytest.raises(ValueError, match="strategy_id"):
        TaiwanAIResearchRequest(purpose="advice")
    with pytest.raises(ValueError, match="review"):
        TaiwanAIResearchRequest(purpose="research", review=True)
    assert TaiwanAIResearchRequest(purpose="advice", strategy_id="quant_pullback").purpose == "advice"


def test_advice_rejects_buy_when_signal_is_not_triggered_and_filters_citations():
    advice = _parse_advice(
        {
            "action": "buy",
            "summary": "wait instead",
            "rationale": [
                {"kind": "fact", "text": "kept", "evidence_refs": ["price.close", "fake"]},
                {"kind": "inference", "text": "drop", "evidence_refs": ["fake"]},
                {"kind": "assumption", "text": "allowed without refs", "evidence_refs": []},
            ],
            "conditions": [{"text": "valid", "evidence_refs": ["price.close"]}],
            "invalidation": [{"text": "invalid", "evidence_refs": ["fake"]}],
            "data_gaps": [],
        },
        symbol="2330.TWSE",
        strategy_id="quant_pullback",
        evidence_as_of="2026-10-01",
        registry_keys={"price.close"},
        candidate=_candidate("waiting"),
        has_position=False,
    )
    assert advice.action == "wait"
    assert advice.selected_trade_plan is None
    assert [(item.kind, item.evidence_refs) for item in advice.rationale] == [
        ("fact", ["price.close"]),
        ("assumption", []),
    ]
    assert len(advice.conditions) == 1
    assert advice.invalidation == []


def test_advice_downgrades_position_action_without_holdings_and_rejects_ai_prices():
    advice = _parse_advice(
        {"action": "exit", "summary": "x", "rationale": [], "data_gaps": []},
        symbol="2330.TWSE",
        strategy_id="quant_pullback",
        evidence_as_of="2026-10-01",
        registry_keys=set(),
        candidate=_candidate(),
        has_position=False,
    )
    assert advice.action == "no_view"
    assert any("no registered holding" in gap for gap in advice.data_gaps)
    with pytest.raises(ValueError, match="prices"):
        _parse_advice(
            {"action": "wait", "summary": "x", "target_price": 120},
            symbol="2330.TWSE",
            strategy_id="quant_pullback",
            evidence_as_of="2026-10-01",
            registry_keys=set(),
            candidate=_candidate(),
            has_position=False,
        )


def test_review_is_annotation_only_and_empty_review_requires_explicit_no_material_issues():
    review = _parse_review(
        {"issues": [], "no_material_issues": True}, "advice-run", {"price.close"}
    )
    assert review.advice_run_id == "advice-run"
    assert review.no_material_issues is True
    with pytest.raises(ValueError, match="explicitly"):
        _parse_review({"issues": [], "no_material_issues": False}, "run", set())


@pytest.mark.asyncio
async def test_historical_advice_fails_closed_before_provider_call(tmp_path):
    svc = TaiwanAIAdviceService(tmp_path)
    with patch("app.taiwan.ai_advice._call_provider", new_callable=AsyncMock) as provider:
        run, review = await svc.generate(
            "2330.TWSE",
            strategy_id="quant_pullback",
            target_date=date(2026, 9, 30),
            review=True,
        )
    assert run.response.status == "unavailable"
    assert run.response.error_code == "historical_advice_unsupported"
    assert review is None
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_reuses_cached_base_and_limits_provider_to_two_calls(taiwan_data_env, tmp_path):
    class FakeBuyPointService:
        def candidate(self, *args, **kwargs):
            return _candidate("waiting")

    svc = TaiwanAIAdviceService(tmp_path, buy_point_svc=FakeBuyPointService())
    outputs = [
        json.dumps(
            {
                "action": "wait",
                "summary": "等待條件成立",
                "rationale": [],
                "conditions": [],
                "invalidation": [],
                "data_gaps": [],
                "selected_plan_instance_id": None,
            },
            ensure_ascii=False,
        ),
        json.dumps({"issues": [], "no_material_issues": True}),
    ]
    config = _provider_config()
    with (
        patch("app.taiwan.ai_advice.snapshot_ai_provider_config", return_value=config),
        patch("app.taiwan.ai_advice._call_provider", new_callable=AsyncMock, side_effect=outputs) as provider,
    ):
        base, no_review = await svc.generate("2330.TWSE", strategy_id="quant_pullback")
        reviewed, review_run = await svc.generate(
            "2330.TWSE", strategy_id="quant_pullback", review=True
        )

    assert no_review is None
    assert base.run_id == reviewed.run_id
    assert base.response.started_at == reviewed.response.started_at
    assert base.response.completed_at == reviewed.response.completed_at
    assert reviewed.response.review_status == "success"
    assert review_run is not None
    assert provider.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_advice_review_coalesces_runs_and_history(
    taiwan_data_env, tmp_path
):
    class FakeBuyPointService:
        def candidate(self, *args, **kwargs):
            return _candidate("waiting")

    svc = TaiwanAIAdviceService(tmp_path, buy_point_svc=FakeBuyPointService())
    provider_calls = 0

    async def provider_output(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        call_number = provider_calls
        await asyncio.sleep(0.05)
        if call_number == 1:
            return json.dumps(
                {
                    "action": "wait",
                    "summary": "等待條件成立",
                    "rationale": [],
                    "conditions": [],
                    "invalidation": [],
                    "data_gaps": [],
                    "selected_plan_instance_id": None,
                },
                ensure_ascii=False,
            )
        return json.dumps({"issues": [], "no_material_issues": True})

    with (
        patch(
            "app.taiwan.ai_advice.snapshot_ai_provider_config",
            return_value=_provider_config(),
        ),
        patch("app.taiwan.ai_advice._call_provider", side_effect=provider_output) as provider,
    ):
        results = await asyncio.gather(
            *(
                svc.generate("2330.TWSE", strategy_id="quant_pullback", review=True)
                for _ in range(4)
            )
        )

    advice_ids = {advice_run.run_id for advice_run, _review_run in results}
    review_ids = {
        review_run.run_id
        for _advice_run, review_run in results
        if review_run is not None
    }
    assert provider.call_count == provider_calls == 2
    assert len(advice_ids) == 1
    assert len(review_ids) == 1
    assert {
        advice_run.response.review_run_id for advice_run, _review_run in results
    } == review_ids

    first_advice, first_review = results[0]
    assert first_review is not None
    assert isinstance(first_advice.evidence_payload["missing_items"], list)
    assert "buy_point.freshness" in first_advice.evidence_registry_keys
    review_messages = provider.call_args_list[1].args[0]
    assert '"missing_items"' in review_messages[1]["content"]
    assert "buy_point.freshness" in review_messages[1]["content"]

    store = TaiwanAIResearchHistoryStore(tmp_path / "coalesced-history.jsonl")
    for advice_run, review_run in results:
        assert review_run is not None
        store.ensure_advice(advice_run)
        store.ensure_review(advice_run, review_run)
    records = store.list(limit=10)
    assert [record["kind"] for record in records].count("advice") == 1
    assert [record["kind"] for record in records].count("review") == 1


def test_history_persists_advice_and_review_as_idempotent_supported_kinds(tmp_path):
    advice = _parse_advice(
        {"action": "wait", "summary": "wait", "rationale": [], "data_gaps": []},
        symbol="2330.TWSE",
        strategy_id="quant_pullback",
        evidence_as_of="2026-10-01",
        registry_keys=set(),
        candidate=_candidate(),
        has_position=False,
    )
    response = TaiwanAIAdviceResponse(
        status="success",
        advice=advice,
        run_id="advice-run",
        started_at="2026-10-02T10:00:00+08:00",
        completed_at="2026-10-02T10:01:00+08:00",
        generated_at="2026-10-02T10:01:00+08:00",
        evidence_as_of="2026-10-01",
    )
    advice_run = TaiwanAIAdviceRun(
        run_id="advice-run",
        response=response,
        evidence_payload={"price": {"close": 100}},
        evidence_digest="digest",
        evidence_cutoff="2026-10-01T13:30:00+08:00",
        source_snapshot_id="snapshot",
        source_snapshot_digest="digest",
        strategy_definition_digest="strategy",
    )
    review = _parse_review(
        {"issues": [], "no_material_issues": True}, "advice-run", set()
    )
    review_run = TaiwanAIReviewRun(
        run_id="review-run",
        advice_run_id="advice-run",
        review=review,
        started_at="2026-10-02T10:01:01+08:00",
        completed_at="2026-10-02T10:02:00+08:00",
    )
    store = TaiwanAIResearchHistoryStore(tmp_path / "history.jsonl")

    advice_record = store.ensure_advice(advice_run)
    assert store.ensure_advice(advice_run) == advice_record
    review_record = store.ensure_review(advice_run, review_run)
    assert store.ensure_review(advice_run, review_run) == review_record

    records = store.list(limit=10)
    assert {record["kind"] for record in records} == {"advice", "review"}
    assert all("unsupported_kind" not in record.get("compatibility", {}) for record in records)
    assert review_record["parent_id"] == advice_record["id"]
    assert "eligible" not in advice_record
