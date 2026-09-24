from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.monitor_rules import evaluate_taiwan_rules
from app.services import alert_store


class _Alert:
    def to_dict(self):
        return {"alert_id": "alert-1", "symbol": "2330.TWSE"}


def _request(tmp_path, quote_service=None):
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    state = SimpleNamespace(repo=repo, quote_service=quote_service)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_manual_evaluation_persists_before_sse_push(tmp_path, monkeypatch):
    alert = _Alert()
    calls = []
    quote_service = SimpleNamespace(push_alerts=lambda events: calls.append(("push", events)))

    class Engine:
        def evaluate_all(self, *, persist_events):
            persist_events([alert])
            return [alert]

        def list_rules(self):
            return [object()]

    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: Engine())
    monkeypatch.setattr(
        alert_store,
        "append_many",
        lambda data_dir, events: calls.append(("persist", data_dir, events)),
    )

    result = evaluate_taiwan_rules(_request(tmp_path, quote_service))

    assert [call[0] for call in calls] == ["persist", "push"]
    assert calls[0][1:] == (tmp_path, [{"alert_id": "alert-1", "symbol": "2330.TWSE"}])
    assert result["alerts_count"] == 1


def test_manual_evaluation_does_not_push_when_alert_persistence_fails(tmp_path, monkeypatch):
    alert = _Alert()
    pushes = []

    class Engine:
        def evaluate_all(self, *, persist_events):
            persist_events([alert])
            return [alert]

    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: Engine())

    def fail_append(*_args):
        raise OSError("alerts log is unavailable")

    monkeypatch.setattr(alert_store, "append_many", fail_append)

    with pytest.raises(OSError, match="alerts log is unavailable"):
        evaluate_taiwan_rules(_request(tmp_path, SimpleNamespace(push_alerts=pushes.append)))

    assert pushes == []


def test_manual_evaluation_requires_alert_storage(tmp_path, monkeypatch):
    class Engine:
        def evaluate_all(self, *, persist_events):
            persist_events([_Alert()])
            return []

    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: Engine())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=None)))

    with pytest.raises(HTTPException) as exc_info:
        evaluate_taiwan_rules(request)

    assert exc_info.value.status_code == 503


def test_manual_evaluation_keeps_durable_alert_when_sse_push_fails(tmp_path, monkeypatch):
    alert = _Alert()

    class Engine:
        def evaluate_all(self, *, persist_events):
            persist_events([alert])
            return [alert]

        def list_rules(self):
            return [object()]

    class QuoteService:
        def push_alerts(self, _events):
            raise RuntimeError("SSE unavailable")

    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: Engine())
    monkeypatch.setattr(alert_store, "append_many", lambda *_args: None)

    result = evaluate_taiwan_rules(_request(tmp_path, QuoteService()))

    assert result["alerts_count"] == 1
