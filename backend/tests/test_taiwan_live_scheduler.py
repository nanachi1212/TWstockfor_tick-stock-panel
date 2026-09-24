from types import SimpleNamespace
from unittest.mock import Mock

from app.jobs import daily_pipeline
from app.taiwan import daily_update
from app.taiwan.quant import live_runner


def test_scheduler_market_refresh_is_complete_before_quant_failure(monkeypatch, taiwan_data_env):
    scheduler = Mock()
    monkeypatch.setattr(daily_pipeline, "AsyncIOScheduler", lambda **kwargs: scheduler)
    events = []
    result = SimpleNamespace(overall_status="success", daily=SimpleNamespace(status="success"),
                             institutional=SimpleNamespace(status="success"),
                             margin=SimpleNamespace(status="success"))

    def refresh(**kwargs):
        events.append("refresh_finished")
        return result

    def quant(value, *, app_state=None):
        assert value is result
        assert app_state is daily_pipeline._app_state_ref
        assert events == ["refresh_finished"]
        events.append("quant_failed")
        raise RuntimeError("quant disk full")

    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService",
                        lambda: SimpleNamespace(run_update=refresh))
    monkeypatch.setattr(live_runner, "run_live_after_refresh", quant)
    daily_pipeline.start_scheduler(Mock(), Mock())
    job = next(call for call in scheduler.add_job.call_args_list if call.kwargs["id"] == "taiwan_daily_update")
    job.args[0]()
    assert events == ["refresh_finished", "quant_failed"]
    assert result.overall_status == "success"
    assert result.daily.status == "success"
    assert job.kwargs["max_instances"] == 1


def test_failed_daily_refresh_skips_quant_with_reason(monkeypatch, taiwan_data_env):
    run = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(live_runner, "run_live_cycle", run)
    result = live_runner.run_live_after_refresh(SimpleNamespace(daily=SimpleNamespace(status="partial")))
    assert result == {"status": "skipped", "reason": "daily_refresh_not_ready"}
    run.assert_not_called()


def test_run_live_after_refresh_passes_app_state_to_backend_quant_cycle(monkeypatch):
    app_state = object()
    run = Mock(return_value={"freeze": {"status": "noop"}})
    monkeypatch.setattr(live_runner, "run_live_cycle", run)
    result = live_runner.run_live_after_refresh(SimpleNamespace(daily=SimpleNamespace(status="success")),
                                                app_state=app_state)
    assert result == {"freeze": {"status": "noop"}}
    run.assert_called_once_with(app_state=app_state)


def test_freeze_blocker_does_not_block_independent_maturation(monkeypatch):
    from app.taiwan.quant import live_outcomes

    ledger = Mock()
    source = Mock()
    monkeypatch.setattr(live_runner, "LiveLedger", lambda **kw: ledger)
    monkeypatch.setattr(live_runner, "CurrentLiveSource", lambda: source)
    monkeypatch.setattr(live_runner, "run_current_live", lambda **kw: {"status": "blocked"})
    mature = Mock(return_value={"status": "success", "appended": 1})
    monkeypatch.setattr(live_outcomes, "mature_live_outcomes", mature)
    result = live_runner.run_live_cycle()
    assert result["maturation"]["appended"] == 1
    mature.assert_called_once_with(ledger, source)
    source.close.assert_called_once()


def test_live_quant_alerts_use_only_audited_frozen_snapshot(monkeypatch, taiwan_data_env):
    from app.services import alert_store
    from app.taiwan.realtime import monitor_engine

    ledger = Mock()
    ledger.read_run.return_value = {
        "audit_status": "ok",
        "session": "2026-09-24",
        "snapshot": {"signals": [{"symbol": "2330.TWSE", "rank": 1}]},
    }
    events = [{"alert_id": "event-1", "symbol": "2330.TWSE"}]
    engine = Mock()
    append = Mock()

    def evaluate(_signals, _session, *, persist_events):
        persist_events(events)
        return events

    engine.evaluate_quant_top10.side_effect = evaluate
    push = Mock()
    monkeypatch.setattr(monitor_engine, "get_monitor_engine", lambda: engine)
    monkeypatch.setattr(alert_store, "append_many", append)
    app_state = SimpleNamespace(quote_service=SimpleNamespace(push_alerts=push))

    result = live_runner._evaluate_live_quant_alerts(
        {"status": "frozen", "session": "2026-09-24"}, ledger, app_state,
    )

    assert result == {"status": "available", "appended": 1}
    engine.evaluate_quant_top10.assert_called_once()
    assert engine.evaluate_quant_top10.call_args.args == ([{"symbol": "2330.TWSE", "rank": 1}], "2026-09-24")
    assert callable(engine.evaluate_quant_top10.call_args.kwargs["persist_events"])
    append.assert_called_once_with(taiwan_data_env["data_dir"], events)
    push.assert_called_once_with(events)


def test_new_quant_exit_rule_is_seeded_from_latest_audited_snapshot(monkeypatch):
    run = {
        "audit_status": "ok",
        "snapshot": {"signals": [{"symbol": "2330.TWSE", "rank": 2}]},
    }
    ledger = Mock()
    ledger.latest_run.return_value = run
    seeded = Mock(return_value=True)
    engine = SimpleNamespace(seed_quant_exit_rule=seeded)
    monkeypatch.setattr(live_runner, "LiveLedger", lambda: ledger)
    monkeypatch.setattr(live_runner, "LiveModel", lambda: SimpleNamespace(key="model"))

    assert live_runner.seed_quant_exit_rule_from_latest_snapshot("rule-1", engine)

    ledger.latest_run.assert_called_once_with("model")
    seeded.assert_called_once_with(
        "rule-1", [{"symbol": "2330.TWSE", "rank": 2}], force=False,
    )


def test_new_quant_exit_rule_is_not_seeded_from_conflicted_snapshot(monkeypatch):
    ledger = Mock()
    ledger.latest_run.return_value = {
        "audit_status": "conflict",
        "snapshot": {"signals": [{"symbol": "2330.TWSE", "rank": 2}]},
    }
    engine = SimpleNamespace(seed_quant_exit_rule=Mock())
    monkeypatch.setattr(live_runner, "LiveLedger", lambda: ledger)
    monkeypatch.setattr(live_runner, "LiveModel", lambda: SimpleNamespace(key="model"))

    assert not live_runner.seed_quant_exit_rule_from_latest_snapshot("rule-1", engine)

    engine.seed_quant_exit_rule.assert_not_called()


def test_manual_quant_alert_evaluation_persists_before_committing_edges(monkeypatch):
    from app.api import taiwan_live
    from app.services import alert_store
    from app.taiwan.realtime import monitor_engine

    session = "2026-09-24"
    ledger = Mock()
    ledger.read_run.return_value = {
        "audit_status": "ok",
        "snapshot": {"signals": [{"symbol": "2330.TWSE", "rank": 1}]},
    }
    ledger.latest_operation.return_value = {
        "freeze": {"status": "frozen", "session": session},
    }
    events = [{"alert_id": "manual-event", "symbol": "2330.TWSE"}]
    engine = Mock()

    def evaluate(_signals, _session, *, available, persist_events):
        assert available is True
        persist_events(events)
        return events

    engine.evaluate_quant_top10.side_effect = evaluate
    data_dir = object()
    append = Mock()
    push = Mock()
    monkeypatch.setattr(taiwan_live, "_expected_session", lambda: session)
    monkeypatch.setattr(taiwan_live, "LiveLedger", lambda: ledger)
    monkeypatch.setattr(taiwan_live, "LiveModel", lambda: SimpleNamespace(key="model"))
    monkeypatch.setattr(monitor_engine, "get_monitor_engine", lambda: engine)
    monkeypatch.setattr(alert_store, "append_many", append)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=data_dir)),
        quote_service=SimpleNamespace(push_alerts=push),
    )))

    result = taiwan_live.evaluate_quant_alerts(request)

    assert result == {"ok": True, "status": "available", "alerts": events}
    append.assert_called_once_with(data_dir, events)
    push.assert_called_once_with(events)
