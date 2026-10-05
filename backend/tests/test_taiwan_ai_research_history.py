from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import device_transfer
from app.services.ai_provider import AIProviderConfigSnapshot
from app.taiwan.ai_research import (
    _REPORT_CACHE,
    _REPORT_CACHE_LOCK,
    SYSTEM_PROMPT,
    TaiwanAIResearchResponse,
    TaiwanAIResearchRun,
    TaiwanAIResearchService,
    TaiwanAIStockResearchReport,
    _evidence_digest,
    _generation_config_metadata,
    _sanitize_personal_context,
    build_evidence_registry,
)
from app.taiwan.ai_research_history import (
    AIResearchHistoryError,
    TaiwanAIResearchHistoryStore,
)


def _run(
    run_id: str,
    *,
    symbol: str = "2330.TWSE",
    purpose: str = "research",
    close: float = 100.0,
    overview: str = "客觀摘要",
    model: str = "model-a",
) -> TaiwanAIResearchRun:
    completed = "2026-10-02T10:01:00+08:00"
    report = TaiwanAIStockResearchReport(
        symbol=symbol,
        code=symbol.split(".")[0],
        name="台積電",
        evidence_as_of="2026-10-01",
        started_at="2026-10-02T10:00:00+08:00",
        completed_at=completed,
        generated_at=completed,
        overview=overview,
    )
    response = TaiwanAIResearchResponse(
        status="success",
        report=report,
        provider="openai_compat",
        model=model,
        evidence_as_of="2026-10-01",
        run_id=run_id,
        started_at="2026-10-02T10:00:00+08:00",
        completed_at=completed,
        generated_at=completed,
        evidence_registry_keys=["price_context.close"],
    )
    evidence = {"identity": {"symbol": symbol}, "price_context": {"close": close}}
    return TaiwanAIResearchRun(
        run_id=run_id,
        purpose=purpose,
        response=response,
        evidence_payload=evidence,
        evidence_registry_keys=["price_context.close"],
        evidence_digest=_evidence_digest(evidence),
        generation_config={"provider": "openai_compat", "model": model, "reasoning_effort": "medium"},
    )


def test_history_save_restart_read_and_idempotent_ensure_report(tmp_path):
    path = tmp_path / "user_data" / "taiwan_ai_research_history.jsonl"
    store = TaiwanAIResearchHistoryStore(path)
    run = _run("run-one")

    first = store.ensure_report(run)
    repeated = store.ensure_report(run)
    restarted = TaiwanAIResearchHistoryStore(path)

    assert repeated == first
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert restarted.get(first["id"])["evidence_payload"] == run.evidence_payload
    assert restarted.get(first["id"])["evidence_digest"] == run.evidence_digest
    link = restarted.append_link(first["id"], {"type": "future_review", "id": "review-1"})
    assert link["parent_id"] == first["id"]
    assert link["run_id"] == run.run_id


def test_history_ignores_only_truncated_final_line(tmp_path):
    path = tmp_path / "history.jsonl"
    store = TaiwanAIResearchHistoryStore(path)
    saved = store.ensure_report(_run("run-one"))
    with path.open("ab") as stream:
        stream.write(b'{"id":"truncated"')

    assert TaiwanAIResearchHistoryStore(path).get(saved["id"])["run_id"] == "run-one"

    recovered = TaiwanAIResearchHistoryStore(path).ensure_report(_run("run-two"))
    assert TaiwanAIResearchHistoryStore(path).get(recovered["id"])["run_id"] == "run-two"
    assert "truncated" not in path.read_text(encoding="utf-8")

    path.write_bytes(b'{"id":bad}\n' + path.read_bytes())
    with pytest.raises(AIResearchHistoryError, match="line 1"):
        TaiwanAIResearchHistoryStore(path).list()


def test_compare_separates_evidence_generation_and_interpretation_without_ai(tmp_path):
    store = TaiwanAIResearchHistoryStore(tmp_path / "history.jsonl")
    left = store.ensure_report(_run("left", close=100, overview="摘要 A", model="model-a"))
    right = store.ensure_report(_run("right", close=101, overview="摘要 B", model="model-b"))

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as ai:
        result = store.compare(left["id"], right["id"])

    assert ai.await_count == 0
    evidence = result["evidence_data_changes"]
    assert evidence["changed"] is True and evidence["digest_changed"] is True
    assert [item["path"] for item in evidence["field_changes"]] == ["price_context.close"]
    assert result["model_prompt_config_changes"]["changed"] is True
    assert result["interpretation_report_changes"]["changed"] is True


@pytest.mark.asyncio
async def test_cache_hit_preserves_run_and_completion_while_refresh_creates_new_run():
    from tests.test_taiwan_stock_comparison import build_context

    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE.clear()
    research_svc = MagicMock()
    research_svc.get_research_context.return_value = build_context("2330.TWSE", "2330", "台積電")
    diag_svc = MagicMock()
    diag_svc.get_diagnostics.return_value = SimpleNamespace(items=[])
    service = TaiwanAIResearchService(research_svc=research_svc, diag_svc=diag_svc)
    response_text = json.dumps({
        "overview": "快取測試", "key_observations": [], "risk_factors": [], "watch_next": [],
    })

    with patch(
        "app.taiwan.ai_research.generate_ai_text",
        new_callable=AsyncMock,
        return_value=response_text,
    ) as ai:
        first = await service.generate_run("2330.TWSE")
        cached = await service.generate_run("2330.TWSE")
        refreshed = await service.generate_run("2330.TWSE", refresh=True)

    assert ai.await_count == 2
    assert cached.run_id == first.run_id
    assert cached.response.completed_at == first.response.completed_at
    assert cached.evidence_payload == first.evidence_payload
    assert cached.evidence_digest == first.evidence_digest
    assert refreshed.run_id != first.run_id
    assert first.response.generated_at == first.response.completed_at
    assert first.response.report.generated_at == first.response.report.completed_at


def test_portfolio_summary_sanitizer_enforces_coverage_and_asset_boundary():
    complete = _sanitize_personal_context({
        "portfolio": {
            "registered_positions_count": 3,
            "quote_coverage": "complete",
            "registered_market_value": 250_000,
            "weight_of_registered_pct": 40.5,
            "cash": 900_000,
            "bank_balance": 1_000_000,
            "chat": "secret",
            "memory": "secret",
            "total_assets": 1_250_000,
        }
    })
    assert complete == {
        "portfolio": {
            "registered_positions_count": 3,
            "quote_coverage": "complete",
            "registered_market_value": 250_000.0,
            "weight_of_registered_pct": 40.5,
        }
    }

    partial = _sanitize_personal_context({
        "portfolio": {
            "registered_positions_count": 3,
            "quote_coverage": "partial",
            "registered_market_value": 200_000,
            "weight_of_registered_pct": 99,
        }
    })
    assert partial == {
        "portfolio": {
            "registered_positions_count": 3,
            "quote_coverage": "partial",
            "registered_market_value": 200_000.0,
        }
    }

    missing_coverage = _sanitize_personal_context({
        "portfolio": {
            "registered_positions_count": 3,
            "registered_market_value": 200_000,
            "weight_of_registered_pct": 40,
            "cash": 800_000,
        }
    })
    assert missing_coverage == {
        "portfolio": {
            "registered_positions_count": 3,
            "registered_market_value": 200_000.0,
        }
    }


def test_portfolio_summary_sanitizer_rejects_invalid_values():
    result = _sanitize_personal_context({
        "portfolio": {
            "registered_positions_count": -1,
            "quote_coverage": "complete",
            "registered_market_value": -0.01,
            "weight_of_registered_pct": float("inf"),
        }
    })
    assert result == {"portfolio": {"quote_coverage": "complete"}}

    out_of_range = _sanitize_personal_context({
        "portfolio": {
            "quote_coverage": "complete",
            "weight_of_registered_pct": 100.01,
        }
    })
    assert out_of_range == {"portfolio": {"quote_coverage": "complete"}}


def test_portfolio_summary_is_frozen_in_evidence_only_after_sanitizing():
    from tests.test_taiwan_stock_comparison import build_context

    context = build_context("2330.TWSE", "2330", "台積電")
    payload, registry_keys, _ = build_evidence_registry(
        context,
        None,
        {
            "portfolio": {
                "registered_positions_count": 2,
                "quote_coverage": "partial",
                "registered_market_value": 125_000,
                "weight_of_registered_pct": 88,
                "cash": 500_000,
            }
        },
    )

    assert payload["personal_context"]["portfolio"] == {
        "registered_positions_count": 2,
        "quote_coverage": "partial",
        "registered_market_value": 125_000.0,
    }
    assert "personal.portfolio.registered_market_value" in registry_keys
    assert "personal.portfolio.weight_of_registered_pct" not in registry_keys
    assert "personal.portfolio.cash" not in registry_keys


@pytest.mark.asyncio
async def test_research_prompts_define_registered_weight_without_total_asset_inference():
    from tests.test_taiwan_stock_comparison import build_context

    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE.clear()
    research_svc = MagicMock()
    research_svc.get_research_context.return_value = build_context(
        "2330.TWSE", "2330", "台積電"
    )
    diag_svc = MagicMock()
    diag_svc.get_diagnostics.return_value = SimpleNamespace(items=[])
    service = TaiwanAIResearchService(research_svc=research_svc, diag_svc=diag_svc)
    response_text = json.dumps({
        "overview": "持股占比測試",
        "key_observations": [],
        "risk_factors": [],
        "watch_next": [],
    })
    personal_context = {
        "portfolio": {
            "registered_positions_count": 2,
            "quote_coverage": "complete",
            "registered_market_value": 250_000,
            "weight_of_registered_pct": 40,
            "cash": 500_000,
        }
    }

    with patch(
        "app.taiwan.ai_research.generate_ai_text",
        new_callable=AsyncMock,
        return_value=response_text,
    ) as ai:
        run = await service.generate_run(
            "2330.TWSE",
            personal_context=personal_context,
            refresh=True,
        )

    assert run.evidence_payload["personal_context"]["portfolio"] == {
        "registered_positions_count": 2,
        "quote_coverage": "complete",
        "registered_market_value": 250_000.0,
        "weight_of_registered_pct": 40.0,
    }
    messages = ai.await_args.args[0]
    assert "占已登錄持股市值" in SYSTEM_PROMPT
    assert "占已登錄持股市值" in messages[1]["content"]
    assert "不是總資產" in messages[1]["content"]
    assert "現金、銀行存款、對話、記憶" in messages[1]["content"]


@pytest.mark.asyncio
async def test_concurrent_miss_shares_one_artifact_identity():
    from tests.test_taiwan_stock_comparison import build_context

    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE.clear()
    research_svc = MagicMock()
    research_svc.get_research_context.return_value = build_context("2330.TWSE", "2330", "台積電")
    diag_svc = MagicMock()
    diag_svc.get_diagnostics.return_value = SimpleNamespace(items=[])
    service = TaiwanAIResearchService(research_svc=research_svc, diag_svc=diag_svc)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return json.dumps({
            "overview": "並行 artifact", "key_observations": [], "risk_factors": [], "watch_next": [],
        })

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock, side_effect=delayed) as ai:
        leader = asyncio.create_task(service.generate_run("2330.TWSE"))
        await entered.wait()
        follower = asyncio.create_task(service.generate_run("2330.TWSE"))
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(leader, follower)

    assert ai.await_count == 1
    assert first.run_id == second.run_id
    assert first.response.completed_at == second.response.completed_at


def test_history_api_list_detail_compare_and_filters(tmp_path, monkeypatch):
    store = TaiwanAIResearchHistoryStore(tmp_path / "history.jsonl")
    times = iter([
        datetime.fromisoformat("2026-10-01T10:00:00+08:00"),
        datetime.fromisoformat("2026-10-02T10:00:00+08:00"),
    ])
    with patch("app.taiwan.ai_research_history.taipei_now", side_effect=lambda: next(times)):
        first = store.ensure_report(_run("one", purpose="alert"))
        second = store.ensure_report(_run("two", symbol="0050.TWSE", purpose="research", close=101))
    monkeypatch.setattr("app.api.taiwan.get_ai_research_history_store", lambda: store)
    client = TestClient(app, client=("127.0.0.1", 50000))

    filtered = client.get(
        "/api/taiwan/ai-research/history",
        params={"symbol": "2330.TWSE", "purpose": "alert", "from": "2026-10-01", "to": "2026-10-01", "limit": 1},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [first["id"]]
    detail = client.get(f"/api/taiwan/ai-research/history/{second['id']}")
    assert detail.status_code == 200 and detail.json()["run_id"] == "two"
    compared = client.get(
        f"/api/taiwan/ai-research/history/{first['id']}/compare/{second['id']}"
    )
    assert compared.status_code == 200
    assert compared.json()["evidence_data_changes"]["changed"] is True
    assert client.get("/api/taiwan/ai-research/history/missing").status_code == 404


def test_generation_api_persists_the_frozen_run_and_forwards_refresh(tmp_path, monkeypatch):
    store = TaiwanAIResearchHistoryStore(tmp_path / "history.jsonl")
    frozen = _run("api-run", purpose="alert", close=123.0)
    calls = []

    class FakeService:
        async def generate_run(self, symbol, **kwargs):
            calls.append((symbol, kwargs))
            return frozen

    monkeypatch.setattr("app.api.taiwan.TaiwanAIResearchService", FakeService)
    monkeypatch.setattr("app.api.taiwan.get_ai_research_history_store", lambda: store)
    selection = {"selection_state": "watch"}
    monkeypatch.setattr(
        "app.taiwan.beginner_selection.selection_evidence", lambda symbol: selection
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/taiwan/stocks/2330.TWSE/ai-research",
        json={"purpose": "alert", "refresh": True},
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "api-run"
    assert calls == [("2330.TWSE", {
        "target_date": None,
        "personal_context": None,
        "purpose": "alert",
        "refresh": True,
        "selection_evidence": selection,
    })]
    [saved] = store.list()
    assert saved["evidence_payload"] == frozen.evidence_payload
    assert saved["evidence_digest"] == frozen.evidence_digest


def test_legacy_missing_metadata_is_readable_and_compare_is_explicitly_unavailable(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text(json.dumps({"id": "legacy", "report": {"overview": "old"}}) + "\n", encoding="utf-8")
    store = TaiwanAIResearchHistoryStore(path)

    legacy = store.get("legacy")
    assert legacy["compatibility"]["missing_fields"] == ["run_id", "saved_at"]
    current = store.ensure_report(_run("current"))
    compared = store.compare("legacy", current["id"])
    assert compared["evidence_data_changes"]["available"] is False


def test_legacy_response_models_keep_generated_at_compatibility():
    legacy = TaiwanAIResearchResponse.model_validate({
        "status": "unavailable",
        "generated_at": "2026-09-30T10:00:00+08:00",
    })

    assert legacy.generated_at == "2026-09-30T10:00:00+08:00"
    assert legacy.run_id is None
    assert legacy.started_at is None
    assert legacy.completed_at is None


def test_persisted_generation_config_and_backup_exclude_credentials(tmp_path):
    api_key = "sk-DistinctiveResearchHistorySecret123456"
    key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    config = AIProviderConfigSnapshot(
        provider="openai_compat",
        model="model-private-test",
        api_key=api_key,
        profile_name="test-profile",
        base_url=(
            "https://history-user:history-pass@example.invalid:8443/v1"
            "?token=query-secret#fragment-secret"
        ),
        user_agent="test-agent",
        max_output_tokens=4096,
        context_window=8192,
    )
    metadata = _generation_config_metadata(config)
    assert metadata["base_url"] == "https://example.invalid:8443/v1"
    assert "credential_fingerprint" not in metadata

    user_dir = tmp_path / "user_data"
    run = _run("private-config")
    run.generation_config = metadata
    store = TaiwanAIResearchHistoryStore(user_dir / "taiwan_ai_research_history.jsonl")
    store.ensure_report(run)
    history_text = store.path.read_text(encoding="utf-8")

    archive = device_transfer.build_backup(["history"], browser_storage={}, user_dir=user_dir)
    with zipfile.ZipFile(io.BytesIO(archive)) as backup:
        backup_history = backup.read(
            "files/history/taiwan_ai_research_history.jsonl"
        ).decode("utf-8")

    serialized = json.dumps(metadata, sort_keys=True) + history_text + backup_history
    for forbidden in (
        api_key,
        key_digest,
        "history-user",
        "history-pass",
        "query-secret",
        "fragment-secret",
    ):
        assert forbidden not in serialized
