from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.monitor_rules import (
    TaiwanMonitorRuleCreate,
    TaiwanMonitorRuleUpdate,
    create_taiwan_rule,
    update_taiwan_rule,
)


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
