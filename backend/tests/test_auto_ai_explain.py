from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.taiwan import auto_ai_explain as mod
from app.taiwan.ai_research_history import TaiwanAIResearchHistoryStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    prefs: dict = {}
    monkeypatch.setattr(mod.preferences, "load", lambda: dict(prefs))
    monkeypatch.setattr(mod.preferences, "save", lambda updates: prefs.update(updates))
    store = TaiwanAIResearchHistoryStore(tmp_path / "history.jsonl")
    monkeypatch.setattr(mod, "get_ai_research_history_store", lambda: store)
    monkeypatch.setattr(mod, "snapshot_ai_provider_config", lambda: SimpleNamespace(model="m1"))
    return SimpleNamespace(prefs=prefs, store=store)


def _record(store, symbol, as_of, status="success", model="m1", prompt=mod.RESEARCH_PROMPT_VERSION):
    store._append_unlocked({
        "id": f"r_{symbol}_{as_of}", "kind": "report", "run_id": f"run_{symbol}_{as_of}",
        "saved_at": "2026-10-07T17:00:00+08:00", "symbol": symbol, "purpose": "research",
        "evidence_as_of": as_of, "model": model, "prompt_versions": {"research": prompt},
        "response": {"status": status, "report": {"overview": "x"}},
    })


def _select(monkeypatch, symbols, as_of="2026-10-07", status="ready"):
    cands = [SimpleNamespace(symbol=s, as_of=as_of) for s in symbols]
    svc = SimpleNamespace(build=lambda limit: SimpleNamespace(status=status, candidates=cands))
    monkeypatch.setattr(mod, "BeginnerSelectionService", lambda: svc)


def test_disabled_by_default_does_nothing(env, monkeypatch):
    monkeypatch.setattr(mod, "BeginnerSelectionService", lambda: pytest.fail("must not run"))
    assert mod.run_auto_explain() == {"status": "disabled"}


def test_stored_explanation_requires_success_and_fresh_data(env):
    _record(env.store, "2330.TWSE", "2026-10-06")
    _record(env.store, "2317.TWSE", "2026-10-07", status="unavailable")
    assert mod.stored_explanation("2330.TWSE", "2026-10-06") is not None
    assert mod.stored_explanation("2330.TWSE", "2026-10-07") is None  # stored data is older
    assert mod.stored_explanation("2317.TWSE", None) is None  # failures never count as stored


def test_stored_explanation_ignores_other_model_or_prompt_version(env):
    _record(env.store, "2330.TWSE", "2026-10-07", model="other")
    _record(env.store, "2317.TWSE", "2026-10-07", prompt="old_prompt")
    assert mod.stored_explanation("2330.TWSE", None) is None
    assert mod.stored_explanation("2317.TWSE", None) is None


def test_run_skips_stored_records_failures_and_continues(env, monkeypatch):
    mod.set_enabled(True)
    _select(monkeypatch, ["2330.TWSE", "2317.TWSE", "2454.TWSE"])
    _record(env.store, "2330.TWSE", "2026-10-07")

    async def fake_explain(symbol):
        if symbol == "2317.TWSE":
            raise RuntimeError("llm down")
        return "generated"

    monkeypatch.setattr(mod, "_explain", fake_explain)
    result = mod.run_auto_explain()
    assert result["skipped"] == ["2330.TWSE"]
    assert result["generated"] == ["2454.TWSE"]
    assert result["failed"] == {"2317.TWSE": "RuntimeError"}
    assert mod.last_run()["failed"] == {"2317.TWSE": "RuntimeError"}


def test_degraded_selection_with_candidates_still_runs(env, monkeypatch):
    mod.set_enabled(True)
    _select(monkeypatch, ["2330.TWSE"], status="degraded")

    async def fake_explain(symbol):
        return "generated"

    monkeypatch.setattr(mod, "_explain", fake_explain)
    assert mod.run_auto_explain()["generated"] == ["2330.TWSE"]


def test_not_ready_selection_is_reported_not_faked(env, monkeypatch):
    mod.set_enabled(True)
    _select(monkeypatch, [], status="data_unavailable")
    result = mod.run_auto_explain()
    assert result["status"] == "no_candidates" and result["generated"] == []
