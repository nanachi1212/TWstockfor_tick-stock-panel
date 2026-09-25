from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.monitor_rules import (
    TaiwanMonitorRuleCreate,
    TaiwanMonitorRuleUpdate,
    create_taiwan_rule,
    evaluate_taiwan_rules,
    update_taiwan_rule,
)
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
    quote_service = SimpleNamespace(
        push_alerts=lambda events: calls.append(("push", events)),
        _maybe_send_webhook=lambda events, _engine: calls.append(("external", events)),
    )

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

    assert [call[0] for call in calls] == ["persist", "push", "external"]
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


def test_create_quant_exit_rule_reports_baseline_persistence_failure(monkeypatch):
    from unittest.mock import Mock

    engine = Mock()
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: engine)

    def fail_baseline(*_args, **_kwargs):
        raise OSError("state file is unavailable")

    monkeypatch.setattr(
        "app.taiwan.quant.live_runner.seed_quant_exit_rule_from_latest_snapshot",
        fail_baseline,
    )
    request = TaiwanMonitorRuleCreate(
        name="2330 離開 Quant Top 10", symbol="2330.TWSE",
        rule_type="quant_top10_exit", threshold=0,
    )

    with pytest.raises(HTTPException) as exc_info:
        create_taiwan_rule(request)

    assert exc_info.value.status_code == 503
    engine.add_rule.assert_not_called()


def test_reenable_quant_exit_rule_reports_baseline_failure_without_mutating_rule(monkeypatch):
    from unittest.mock import Mock

    from app.taiwan.realtime.monitor_models import TaiwanMonitorRule, TaiwanRuleType

    existing = TaiwanMonitorRule(
        rule_id="quant-exit", name="2330 離開 Quant Top 10", symbol="2330.TWSE",
        rule_type=TaiwanRuleType.QUANT_TOP10_EXIT, threshold=0, enabled=False,
    )
    engine = Mock()
    engine.get_rule.return_value = existing
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: engine)

    def fail_baseline(*_args, **_kwargs):
        raise OSError("state file is unavailable")

    monkeypatch.setattr(
        "app.taiwan.quant.live_runner.seed_quant_exit_rule_from_latest_snapshot",
        fail_baseline,
    )

    with pytest.raises(HTTPException) as exc_info:
        update_taiwan_rule("quant-exit", TaiwanMonitorRuleUpdate(enabled=True))

    assert exc_info.value.status_code == 503
    assert existing.enabled is False
    engine.add_rule.assert_not_called()
