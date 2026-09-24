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
