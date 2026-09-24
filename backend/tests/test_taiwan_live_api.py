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


def test_live_models_marks_run_current_only_when_session_operation_and_audit_agree(monkeypatch):
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
