"""Hermetic wake/resume checks through actual registered APScheduler callbacks."""
import asyncio
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED

from app.jobs import daily_pipeline
from app.taiwan import daily_update, startup_catchup


def _result(day, overall="success"):
    return SimpleNamespace(overall_status=overall, target_latest_trading_date=day.isoformat(),
                           daily=SimpleNamespace(status="success", dates_fetched=1),
                           institutional=SimpleNamespace(status="success"), margin=SimpleNamespace(status="success"),
                           freshness=SimpleNamespace(daily_as_of=day.isoformat(), daily_status="current",
                                                     institutional_as_of=day.isoformat()))


@pytest.mark.asyncio
async def test_running_scheduler_resumes_three_hours_late_once(taiwan_data_env, monkeypatch):
    day = date(2026, 11, 27)
    now = datetime.fromisoformat("2026-11-27T19:30:00+08:00")
    monkeypatch.setattr(daily_pipeline, "taipei_now", lambda: now)
    events = []
    refresh = Mock(side_effect=lambda **args: events.append(args) or _result(day))
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", lambda: SimpleNamespace(run_update=refresh))
    monkeypatch.setattr("app.taiwan.universe.refresh_security_master", Mock())
    monkeypatch.setattr(daily_pipeline, "_refresh_after_close_research", Mock(return_value={}))
    monkeypatch.setattr("app.taiwan.chip_ranking_tracking.freeze_latest_ranking", Mock())
    monkeypatch.setattr("app.taiwan.quant.live_runner.run_live_after_refresh", Mock())
    monkeypatch.setattr("app.taiwan.auto_watch.sync_watchlist_plans", Mock())
    selection, explain, digest = Mock(), Mock(), Mock()
    monkeypatch.setattr("app.taiwan.selection_review_service.lock_daily_forward_batches", selection)
    monkeypatch.setattr("app.taiwan.auto_ai_explain.run_auto_explain", explain)
    monkeypatch.setattr("app.taiwan.advice_reflection.run_auto_reflections", Mock())
    monkeypatch.setattr("app.taiwan.push_digest.run", digest)
    monkeypatch.setattr(daily_pipeline, "notify_daily_data_updated", Mock())
    monkeypatch.setattr(startup_catchup, "pending_after_close", lambda: None)
    scheduler = daily_pipeline.start_scheduler(Mock(), Mock())
    scheduler.pause()
    done = asyncio.get_running_loop().create_future()
    try:
        job = scheduler.get_job("taiwan_daily_update")
        for other in scheduler.get_jobs():
            if other.id != job.id:
                other.remove()
        scheduler.add_listener(lambda event: done.set_result(event),
                               EVENT_JOB_EXECUTED | EVENT_JOB_MISSED | EVENT_JOB_ERROR)
        # APScheduler's real executor applies misfire_grace_time to this timestamp.
        job.modify(next_run_time=datetime.now(job.trigger.timezone) - timedelta(hours=3))
        scheduler.resume()
        event = await asyncio.wait_for(done, 5)
        assert event.code == EVENT_JOB_EXECUTED and event.retval == "ran"
        assert events == [{"refresh_daily": True, "target_date": day}]
        marker = taiwan_data_env["taiwan_root"] / "after_close_primary.json"
        assert json.loads(marker.read_text("utf-8"))["primary_daily_as_of"] == str(day)
        # The second callback (or a restarted backend using the same directory) reads the persisted result.
        assert daily_pipeline.run_due_after_close(trigger="startup_catchup") == "current"
        assert refresh.call_count == selection.call_count == explain.call_count == digest.call_count == 1
        # A late publication only reconciles inputs and outstanding locks; AI and push stay single.
        monkeypatch.setattr(startup_catchup, "pending_after_close", lambda: "selection_lock:2026-11-27")
        assert daily_pipeline.run_due_after_close(evening=True) == "ran"
        selection.assert_called_with(only_missing_for=day)
        assert refresh.call_count == 2 and explain.call_count == digest.call_count == 1
    finally:
        scheduler.shutdown(wait=True)
        await asyncio.sleep(0)


@pytest.mark.parametrize("instant,expected,target", [
    ("2026-11-28T10:20:00+08:00", "ran", date(2026, 11, 27)),
    ("2026-11-30T01:00:00+08:00", "ran", date(2026, 11, 27)),
    ("2026-11-30T10:00:00+08:00", "market_open", None),
    ("2026-11-30T16:15:00+08:00", "before_due", None),
])
def test_late_job_locks_only_for_the_last_eligible_session(taiwan_data_env, monkeypatch, instant, expected, target):
    monkeypatch.setattr(daily_pipeline, "taipei_now", lambda: datetime.fromisoformat(instant))
    monkeypatch.setattr(startup_catchup, "pending_after_close", lambda: "selection_lock")
    run = Mock(side_effect=lambda **args: _result(args["target_date"]))
    monkeypatch.setattr(daily_pipeline, "run_taiwan_update", run)
    assert daily_pipeline.run_due_after_close() == expected
    if target:
        run.assert_called_once_with(evening=True, trigger="scheduled", target_date=target, inputs_only=True)
    else:
        run.assert_not_called()
    assert not (taiwan_data_env["taiwan_root"] / "after_close_primary.json").exists()


def test_missing_or_partial_daily_evidence_does_not_mark_primary_complete(taiwan_data_env, monkeypatch):
    now = datetime.fromisoformat("2026-11-27T19:30:00+08:00")
    monkeypatch.setattr(daily_pipeline, "taipei_now", lambda: now)
    result = _result(now.date(), "partial")
    result.freshness.daily_status = "stale"
    monkeypatch.setattr(daily_pipeline, "run_taiwan_update", lambda **_: result)
    assert daily_pipeline.run_due_after_close() == "incomplete"
    marker = taiwan_data_env["taiwan_root"] / "after_close_primary.json"
    recorded = json.loads(marker.read_text("utf-8"))
    assert recorded["attempt_status"] == "incomplete" and "primary_daily_as_of" not in recorded
    result.freshness.daily_status = "current"
    monkeypatch.setattr(startup_catchup, "pending_after_close", lambda: "missing_inputs")
    assert daily_pipeline.run_due_after_close(evening=True, trigger="scheduled_1930") == "incomplete"
    assert json.loads(marker.read_text("utf-8")) == recorded


@pytest.mark.asyncio
async def test_registered_1930_recovers_a_failed_1630_without_replaying_external_steps(taiwan_data_env, monkeypatch):
    day = date(2026, 11, 27)
    clock = [datetime.fromisoformat("2026-11-27T16:30:00+08:00")]
    monkeypatch.setattr(daily_pipeline, "taipei_now", lambda: clock[0])
    refresh = Mock(side_effect=[OSError("official feed down"), _result(day)])
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", lambda: SimpleNamespace(run_update=refresh))
    monkeypatch.setattr("app.taiwan.universe.refresh_security_master", Mock())
    monkeypatch.setattr(daily_pipeline, "notify_daily_data_updated", Mock())
    monkeypatch.setattr("app.taiwan.chip_ranking_tracking.freeze_latest_ranking", Mock())
    selection, explain, digest = Mock(), Mock(), Mock()
    monkeypatch.setattr("app.taiwan.selection_review_service.lock_daily_forward_batches", selection)
    monkeypatch.setattr("app.taiwan.auto_ai_explain.run_auto_explain", explain)
    monkeypatch.setattr("app.taiwan.push_digest.run", digest)
    monkeypatch.setattr(startup_catchup, "pending_after_close", lambda: None if selection.called else "missing_inputs")
    scheduler = daily_pipeline.start_scheduler(Mock(), Mock())
    scheduler.pause()
    try:
        primary = scheduler.get_job("taiwan_daily_update")
        recovery = scheduler.get_job("taiwan_evening_update_1930")
        assert primary.func(**primary.kwargs) == "failed"
        receipt = json.loads((taiwan_data_env["taiwan_root"] / "after_close_primary.json").read_text("utf-8"))
        assert receipt["attempt_status"] == "failed" and "primary_daily_as_of" not in receipt
        clock[0] = datetime.fromisoformat("2026-11-27T19:30:00+08:00")
        assert recovery.trigger.get_next_fire_time(None, clock[0]) == clock[0]
        assert recovery.func(**recovery.kwargs) == "ran"
        selection.assert_called_once_with(only_missing_for=day)
        assert recovery.func(**recovery.kwargs) == "current"
        assert refresh.call_count == 2
        explain.assert_not_called()
        digest.assert_not_called()
    finally:
        scheduler.shutdown(wait=True)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_reversed_wake_order_runs_missing_primary_once_then_preserves_late_qualifying_chain(taiwan_data_env, monkeypatch):
    current = [datetime.fromisoformat("2026-11-27T19:30:00+08:00")]
    day = current[0].date()
    monkeypatch.setattr(daily_pipeline, "taipei_now", lambda: current[0])
    pending = [False]
    monkeypatch.setattr(startup_catchup, "pending_after_close", lambda: "missing_inputs" if pending[0] else None)
    refresh = Mock(return_value=_result(day))
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", lambda: SimpleNamespace(run_update=refresh))
    monkeypatch.setattr("app.taiwan.universe.refresh_security_master", Mock())
    monkeypatch.setattr(daily_pipeline, "_refresh_after_close_research", Mock(return_value={}))
    monkeypatch.setattr("app.taiwan.chip_ranking_tracking.freeze_latest_ranking", Mock())
    quant = Mock()
    monkeypatch.setattr("app.taiwan.quant.live_runner.run_live_after_refresh", quant)
    monkeypatch.setattr("app.taiwan.auto_watch.sync_watchlist_plans", Mock())
    monkeypatch.setattr("app.taiwan.selection_review_service.lock_daily_forward_batches", Mock())
    monkeypatch.setattr("app.taiwan.auto_ai_explain.run_auto_explain", Mock())
    monkeypatch.setattr("app.taiwan.advice_reflection.run_auto_reflections", Mock())
    monkeypatch.setattr("app.taiwan.push_digest.run", Mock())
    monkeypatch.setattr(daily_pipeline, "notify_daily_data_updated", Mock())
    scheduler = daily_pipeline.start_scheduler(Mock(), Mock())
    scheduler.pause()
    try:
        recovery = scheduler.get_job("taiwan_evening_update_1930")
        primary = scheduler.get_job("taiwan_daily_update")
        assert recovery.func(**recovery.kwargs) == "ran"
        assert primary.func(**primary.kwargs) == "current"
        assert quant.call_count == refresh.call_count == 1
        assert json.loads((taiwan_data_env["taiwan_root"] / "after_close_primary.json").read_text())["primary_daily_as_of"] == str(day)
        pending[0] = True
        for hour, minute in ((21, 30), (23, 0)):
            current[0] = current[0].replace(hour=hour, minute=minute)
            job = scheduler.get_job(f"taiwan_evening_update_{hour:02d}{minute:02d}")
            assert job.func(**job.kwargs) == "ran"
        # Each late refresh actually discovered new daily bars (dates_fetched=1).
        assert quant.call_count == refresh.call_count == 3
    finally:
        scheduler.shutdown(wait=True)
        await asyncio.sleep(0)
