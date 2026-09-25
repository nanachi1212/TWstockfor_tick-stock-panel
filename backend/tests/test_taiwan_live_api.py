from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import taiwan_live


def test_live_run_api_returns_frozen_live_snapshot_without_historical_oos(monkeypatch):
    snapshot = {
        "signal_session": "2026-09-23",
        "usage_scope": "experimental_live",
        "validation_state": "unvalidated",
        "signals": [{"symbol": "2330.TWSE", "rank": 1, "score": 0.9}],
        "features": [{"symbol": "2330.TWSE", "momentum_20d": 0.2}],
    }
    frozen = {
        "model_key": "live-model", "session": "2026-09-23", "snapshot_hash": "frozen-hash",
        "frozen_at": "2026-09-23T16:00:00+08:00", "snapshot": snapshot,
        "audit_status": "ok", "conflicts": [],
    }
    ledger = SimpleNamespace(
        read_run=lambda model_key, session: frozen if (model_key, session) == ("live-model", "2026-09-23") else None,
        outcomes=lambda model_key, session: [],
    )
    monkeypatch.setattr(taiwan_live, "LiveLedger", lambda: ledger)

    response = taiwan_live.live_run("live-model", date(2026, 9, 23))

    assert response["snapshot"] is snapshot
    assert response["snapshot"]["signals"][0] == {"symbol": "2330.TWSE", "rank": 1, "score": 0.9}
    assert "forward_return" not in response["snapshot"]
    assert "oos_prediction" not in response["snapshot"]
    assert response["outcomes"] == []


def test_live_run_api_returns_not_found_without_substituting_other_ranking(monkeypatch):
    ledger = SimpleNamespace(read_run=lambda model_key, session: None)
    monkeypatch.setattr(taiwan_live, "LiveLedger", lambda: ledger)

    with pytest.raises(HTTPException, match="live_run_not_found") as exc:
        taiwan_live.live_run("missing-model", date(2026, 9, 23))
    assert exc.value.status_code == 404


def test_manual_quant_alerts_are_persisted_before_sse_and_external_dispatch(monkeypatch, tmp_path):
    from app.services import alert_store
    from app.taiwan.realtime import monitor_engine as monitor_engine_module

    event = {"alert_id": "quant-api-event", "symbol": "2330.TWSE"}
    calls = []
    monkeypatch.setattr(taiwan_live, "_expected_session", lambda: "2026-09-24")
    monkeypatch.setattr(taiwan_live, "LiveModel", lambda: SimpleNamespace(key="model"))

    class Ledger:
        def read_run(self, _model_key, _session):
            return {"audit_status": "ok", "snapshot": {"signals": []}}

        @staticmethod
        def latest_operation():
            return {"freeze": {"status": "frozen", "session": "2026-09-24"}}

    class Engine:
        def evaluate_quant_top10(self, _signals, _session, *, available, persist_events):
            assert available is True
            persist_events([event])
            return [event]

    monkeypatch.setattr(taiwan_live, "LiveLedger", Ledger)
    monkeypatch.setattr(monitor_engine_module, "get_monitor_engine", lambda: Engine())
    monkeypatch.setattr(alert_store, "append_many", lambda _data_dir, events: calls.append(("persist", events)))
    quote_service = SimpleNamespace(
        push_alerts=lambda events: calls.append(("push", events)),
        _maybe_send_webhook=lambda events, _engine: calls.append(("external", events)),
    )
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, quote_service=quote_service)))

    response = taiwan_live.evaluate_quant_alerts(request)

    assert response["alerts"] == [event]
    assert [kind for kind, _events in calls] == ["persist", "push", "external"]


def test_live_models_marks_run_current_only_when_session_operation_and_audit_agree(monkeypatch):
    monkeypatch.setattr(taiwan_live, "_SESSION_CACHE", None)
    expected = date(2026, 9, 24)
    closed = []

    class Source:
        evidence = object()

        def close(self):
            closed.append(True)

    ledger = SimpleNamespace(
        current_session=lambda: expected,
        latest_operation=lambda: {"freeze": {"status": "noop", "session": expected.isoformat()}},
        read_run=lambda model_key, session: {"audit_status": "ok"} if session == expected.isoformat() else None,
        models=lambda: [],
    )
    monkeypatch.setattr(taiwan_live, "CurrentLiveSource", Source)
    monkeypatch.setattr(taiwan_live, "LiveLedger", lambda evidence=None: ledger)

    response = taiwan_live.live_models()

    assert response["expected_session"] == expected.isoformat()
    assert response["current_run_valid"] is True
    assert response["current_run_reason"] == "current"
    assert closed == [True]


@pytest.mark.parametrize(
    ("audit_status", "operation", "reason"),
    [
        ("conflict", {"status": "noop", "session": "2026-09-24"}, "audit_conflict"),
        ("ok", {"status": "blocked", "session": "2026-09-23"}, "operation_not_current_success"),
    ],
)
def test_live_models_fails_closed_for_conflict_or_stale_operation(monkeypatch, audit_status, operation, reason):
    monkeypatch.setattr(taiwan_live, "_SESSION_CACHE", None)
    expected = date(2026, 9, 24)

    class Source:
        evidence = object()

        def close(self):
            pass

    ledger = SimpleNamespace(
        current_session=lambda: expected,
        latest_operation=lambda: operation,
        read_run=lambda model_key, session: {"audit_status": audit_status},
        models=lambda: [],
    )
    monkeypatch.setattr(taiwan_live, "CurrentLiveSource", Source)
    monkeypatch.setattr(taiwan_live, "LiveLedger", lambda evidence=None: ledger)

    response = taiwan_live.live_models()

    assert response["current_run_valid"] is False
    assert response["current_run_reason"] == reason


def test_live_models_fails_closed_when_expected_session_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(taiwan_live, "_SESSION_CACHE", None)
    class Source:
        evidence = object()

        def close(self):
            pass

    ledger = SimpleNamespace(
        current_session=lambda: (_ for _ in ()).throw(ValueError("unresolved evidence")),
        latest_operation=lambda: {"status": "frozen", "session": "2026-09-24"},
        read_run=lambda model_key, session: (_ for _ in ()).throw(AssertionError("must not read an unknown session")),
        models=lambda: [],
    )
    monkeypatch.setattr(taiwan_live, "CurrentLiveSource", Source)
    monkeypatch.setattr(taiwan_live, "LiveLedger", lambda evidence=None: ledger)

    response = taiwan_live.live_models()

    assert response["expected_session"] is None
    assert response["current_run_valid"] is False
    assert response["current_run_reason"] == "session_unavailable"


def test_live_models_reuses_bounded_expected_session_evidence(monkeypatch):
    monkeypatch.setattr(taiwan_live, "_SESSION_CACHE", None)
    now = [100.0]
    calls = {"source": 0, "session": 0}

    class Source:
        evidence = object()

        def __init__(self):
            calls["source"] += 1

        def close(self):
            pass

    class Ledger:
        def __init__(self, evidence=None):
            pass

        def current_session(self):
            calls["session"] += 1
            return date(2026, 9, 24)

        @staticmethod
        def latest_operation():
            return {"status": "blocked"}

        @staticmethod
        def read_run(model_key, session):
            return None

        @staticmethod
        def models():
            return []

    monkeypatch.setattr(taiwan_live, "CurrentLiveSource", Source)
    monkeypatch.setattr(taiwan_live, "LiveLedger", Ledger)
    monkeypatch.setattr(taiwan_live.time, "monotonic", lambda: now[0])

    first = taiwan_live.live_models()
    now[0] += taiwan_live._SESSION_CACHE_TTL - 1
    second = taiwan_live.live_models()
    assert first["expected_session"] == second["expected_session"] == "2026-09-24"
    assert calls == {"source": 1, "session": 1}

    now[0] += 2
    third = taiwan_live.live_models()
    assert third["expected_session"] == "2026-09-24"
    assert calls == {"source": 2, "session": 2}
