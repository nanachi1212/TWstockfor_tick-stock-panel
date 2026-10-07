from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.monitor_rules import router
from app.jobs import daily_pipeline
from app.taiwan import daily_update
from app.taiwan.beginner_selection import BeginnerCandidate, PlanLevels
from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.models import TaiwanRealtimeQuote
from app.taiwan.realtime.monitor_engine import TaiwanMonitorEngine
from app.taiwan.realtime.monitor_models import EvaluationStatus, TaiwanMonitorRule


def candidate(symbol="2330.TWSE", semantics="pullback_limit", **updates):
    levels = PlanLevels(
        rule_version="v1", entry_semantics=semantics,
        entry_zone_low=1020, entry_zone_high=1050, breakout_trigger=1060,
        stop_price=985, evidence_as_of="2026-08-28", plan_identity="fixed-plan",
    ).model_copy(update=updates)
    return BeginnerCandidate(
        symbol=symbol, name="台積電", selection_state="wait_pullback",
        signal_strength="medium", action_summary="等待", evidence_status="complete",
        trade_plan=levels,
    )


def quote(price=1038, **meta_updates):
    now = datetime(2026, 8, 31, 10, tzinfo=TAIPEI_TZ)
    meta = replace(SourceMeta(
        source="twse:mis", source_url="https://mis.twse.com.tw", fetched_at=now.isoformat(),
        trade_date=now.date(), status="realtime", is_realtime=True, is_stale=False,
        source_type="first_party_web_endpoint", freshness_class="best_effort_near_realtime",
    ), **meta_updates)
    return TaiwanRealtimeQuote(
        symbol="2330.TWSE", name="台積電", exchange="TWSE", last_price=price,
        prev_close=1050, quote_time=now, market_status="open", source_meta=meta,
        open=None, high=None, low=None, change=None, change_pct=None,
        volume=None, amount=None, trade_date=now.date(),
    )


@pytest.fixture
def engine(tmp_path, monkeypatch):
    instruments = {
        symbol: SimpleNamespace(name=name, is_supported=True, instrument_type="stock")
        for symbol, name in (("2330.TWSE", "台積電"), ("8069.TPEX", "元太"))
    }
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_security_master",
                        lambda: SimpleNamespace(get_instrument=instruments.get))
    monkeypatch.setattr(daily_update, "resolve_target_latest_trading_date",
                        lambda **_kwargs: date(2026, 8, 28))
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.taipei_now",
                        lambda: datetime(2026, 8, 31, 10, tzinfo=TAIPEI_TZ))
    return TaiwanMonitorEngine(realtime_service=Mock(), storage_path=tmp_path / "rules.json")


def test_sync_thresholds_channels_and_severity(engine):
    no_plan = candidate().model_copy(update={"symbol": "2317.TWSE", "trade_plan": None})
    result = engine.sync_plan_rules([candidate(), candidate("8069.TPEX", "breakout_stop"), no_plan])
    assert result["created"] == 4
    assert result["removed"] == 0
    assert result["skipped"][0]["symbol"] == "2317.TWSE"
    rules = {(r.symbol, r.name): r for r in engine.list_rules()}
    entry = rules["2330.TWSE", "進入承接區"]
    breakout = rules["8069.TPEX", "突破"]
    assert (entry.rule_type.value, entry.threshold) == ("price_below", 1050)
    assert (breakout.rule_type.value, breakout.threshold) == ("price_above", 1060)
    for symbol in ("2330.TWSE", "8069.TPEX"):
        stop = rules[symbol, "跌破失效位"]
        assert (stop.rule_type.value, stop.threshold, stop.severity.value) == ("price_below", 985, "critical")
        assert stop.notify_channels == ["telegram", "line"]
    assert entry.severity.value == breakout.severity.value == "warning"
    assert entry.notify_channels == breakout.notify_channels == ["telegram"]
    assert entry.cooldown_seconds >= 4.5 * 3600
    assert entry.source == "trade_plan"
    assert entry.plan_as_of == "2026-08-28"
    assert entry.plan_identity == "fixed-plan"


def test_resync_keeps_manual_and_replaces_only_plan_rules(engine):
    manual = TaiwanMonitorRule("manual", "手動", "2330.TWSE", "price_above", 1100)
    engine.add_rule(manual)
    before = manual.to_dict()
    engine.sync_plan_rules([candidate()])
    result = engine.sync_plan_rules([candidate(stop_price=980, plan_identity="replacement")])
    assert result == {"created": 2, "removed": 2, "skipped": []}
    assert engine.get_rule("manual").to_dict() == before
    assert sorted(r.threshold for r in engine.list_rules() if r.source == "trade_plan") == [980, 1050]
    restarted = TaiwanMonitorEngine(realtime_service=Mock(), storage_path=engine.storage_path)
    assert {r.rule_id: r.to_dict() for r in restarted.list_rules()} == {
        r.rule_id: r.to_dict() for r in engine.list_rules()
    }
    assert engine.sync_plan_rules([]) == {"created": 0, "removed": 2, "skipped": []}
    assert engine.list_rules() == [manual]


def test_valid_stop_inside_zone_uses_authoritative_levels(engine):
    assert engine.sync_plan_rules([candidate(stop_price=1030)])["created"] == 2
    assert sorted(r.threshold for r in engine.list_rules()) == [1030, 1050]


def test_manual_id_collision_preserves_manual_rule(engine):
    engine.sync_plan_rules([candidate()])
    rule_id = engine.list_rules()[0].rule_id
    manual = TaiwanMonitorRule(rule_id, "手動", "2330.TWSE", "price_above", 1100)
    engine.add_rule(manual)
    before_disk = engine.storage_path.read_bytes()
    with pytest.raises(ValueError, match="conflicts with a manual rule"):
        engine.sync_plan_rules([candidate()])
    assert engine.get_rule(rule_id) is manual
    assert engine.storage_path.read_bytes() == before_disk


def test_old_json_defaults_to_manual(engine):
    engine.storage_path.write_text(json.dumps([{
        "rule_id": "legacy", "name": "舊規則", "symbol": "2330.TWSE",
        "rule_type": "price_above", "threshold": 1050,
    }]), encoding="utf-8")
    assert engine.load_rules() == 1
    rule = engine.get_rule("legacy")
    assert (rule.source, rule.plan_identity, rule.plan_as_of) == ("manual", None, None)
    engine.sync_plan_rules([])
    assert engine.get_rule("legacy") is rule


def test_sync_write_failure_keeps_disk_and_memory(engine, monkeypatch):
    engine.sync_plan_rules([candidate()])
    before_disk = engine.storage_path.read_bytes()
    before_rules = [r.to_dict() for r in engine.list_rules()]
    original_replace = type(engine.storage_path).replace

    def fail_replace(path, target):
        if target == engine.storage_path:
            raise OSError("disk full")
        return original_replace(path, target)

    monkeypatch.setattr(type(engine.storage_path), "replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        engine.sync_plan_rules([])
    assert engine.storage_path.read_bytes() == before_disk
    assert [r.to_dict() for r in engine.list_rules()] == before_rules


@pytest.mark.parametrize("as_of", [None, "2026-08-27", "2026-08-31"])
def test_stale_or_missing_plan_date_cannot_fire(engine, as_of):
    engine.sync_plan_rules([candidate()])
    rule = next(r for r in engine.list_rules() if r.name == "進入承接區")
    rule.plan_as_of = as_of
    alert, status, _ = engine.evaluate_single_rule(rule, quote())
    assert alert is None
    assert status == EvaluationStatus.SKIPPED_STALE_DATA


def test_calendar_failure_cannot_fire(engine, monkeypatch):
    engine.sync_plan_rules([candidate()])
    monkeypatch.setattr(daily_update, "resolve_target_latest_trading_date",
                        Mock(side_effect=RuntimeError("calendar unavailable")))
    assert engine.evaluate_all(force_quotes={"2330.TWSE": quote()}) == []


def test_beginner_message_and_once_per_session_survives_resync_restart(engine, monkeypatch):
    engine.sync_plan_rules([candidate()])
    alerts = engine.evaluate_all(force_quotes={"2330.TWSE": quote()}, now_mono=1)
    assert len(alerts) == 1
    assert alerts[0].message == "2330 台積電 進入承接區 1,020～1,050，現價 1,038；跌破 985 理由失效。（觀察提醒，不是下單）"  # noqa: RUF001
    for forbidden in ("PRICE_BELOW", "price_below", "pullback_limit", "成本", "股數", "損益", "shares", "cost"):
        assert forbidden not in alerts[0].message
    assert engine.evaluate_all(force_quotes={"2330.TWSE": quote(1100)}, now_mono=2) == []
    engine.sync_plan_rules([candidate(plan_identity="new-plan", entry_zone_high=1055)])
    restarted = TaiwanMonitorEngine(realtime_service=Mock(), storage_path=engine.storage_path)
    assert restarted.evaluate_all(force_quotes={"2330.TWSE": quote()}, now_mono=30000) == []
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.taipei_now",
                        lambda: datetime(2026, 9, 1, 10, tzinfo=TAIPEI_TZ))
    monkeypatch.setattr(daily_update, "resolve_target_latest_trading_date",
                        lambda **_kwargs: date(2026, 8, 31))
    restarted.sync_plan_rules([candidate(evidence_as_of="2026-08-31")])
    assert len(restarted.evaluate_all(force_quotes={"2330.TWSE": quote()}, now_mono=30001)) == 1


def test_plan_rules_still_use_quote_gate(engine):
    engine.sync_plan_rules([candidate()])
    assert engine.evaluate_all(force_quotes={"2330.TWSE": quote(is_stale=True)}) == []


@pytest.mark.parametrize("updates", [
    {"entry_zone_low": None}, {"entry_zone_high": float("nan")},
    {"breakout_trigger": None, "entry_semantics": "breakout_stop"},
    {"stop_price": float("inf")}, {"stop_price": 1060},
    {"entry_semantics": "unknown"}, {"evidence_as_of": "invalid"},
])
def test_incomplete_plan_skips_entire_pair(engine, updates):
    result = engine.sync_plan_rules([candidate(**updates)])
    assert result["created"] == 0
    assert result["skipped"] == [{
        "symbol": "2330.TWSE", "reason": "計畫價位或個股資料不完整，無法建立提醒",  # noqa: RUF001
    }]
    assert engine.list_rules() == []


def test_rules_replaced_during_quote_request_cannot_fire(engine):
    engine.sync_plan_rules([candidate()])

    def get_quotes(_symbols):
        engine.sync_plan_rules([])
        return {"2330.TWSE": quote()}

    engine.realtime_service.get_quotes.side_effect = get_quotes
    assert engine.evaluate_all() == []
    assert engine._trigger_states == {}


def test_alert_storage_failure_rolls_back_session_latch(engine):
    engine.sync_plan_rules([candidate()])
    with pytest.raises(OSError, match="alert storage failed"):
        engine.evaluate_all(force_quotes={"2330.TWSE": quote()},
                            persist_events=Mock(side_effect=OSError("alert storage failed")))
    assert engine._trigger_states == {}
    assert len(engine.evaluate_all(force_quotes={"2330.TWSE": quote()})) == 1


def test_breakout_and_stop_messages(engine):
    engine.sync_plan_rules([candidate(semantics="breakout_stop")])
    breakout = engine.evaluate_all(force_quotes={"2330.TWSE": quote(1060)})
    assert len(breakout) == 1
    assert "突破 1,060" in breakout[0].message
    stop = engine.evaluate_all(force_quotes={"2330.TWSE": quote(985)})
    assert len(stop) == 1
    assert "跌破失效位 985" in stop[0].message
    assert stop[0].severity == "critical"
    assert stop[0].notify_channels == ("telegram", "line")


def test_sync_api_failure_retains_existing_rules(engine, monkeypatch):
    from app.services import watchlist
    from app.taiwan.beginner_selection import BeginnerSelectionService

    engine.sync_plan_rules([candidate()])
    previous = [r.to_dict() for r in engine.list_rules()]
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: engine)
    monkeypatch.setattr(watchlist, "list_symbols", lambda: [{"symbol": "2330.TWSE"}])
    monkeypatch.setattr(BeginnerSelectionService, "evaluate_symbols", Mock(side_effect=RuntimeError("unavailable")))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/monitor-rules/taiwan/sync-plans")
        assert response.status_code == 503
    assert [r.to_dict() for r in engine.list_rules()] == previous


def test_sync_api_and_get_contract_with_empty_watchlist(engine, monkeypatch):
    from app.services import watchlist
    from app.taiwan.beginner_selection import BeginnerSelectionService

    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: engine)
    monkeypatch.setattr(watchlist, "list_symbols", lambda: [{"symbol": "2330.TWSE"}, {"symbol": "AAPL"}])
    calls = []

    def evaluate(_self, symbols):
        calls.append(symbols)
        return SimpleNamespace(candidates=[candidate()])

    monkeypatch.setattr(BeginnerSelectionService, "evaluate_symbols", evaluate)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/monitor-rules/taiwan/sync-plans")
        assert response.status_code == 200
        assert response.json() == {"created": 2, "removed": 0, "skipped": []}
        assert calls == [["2330.TWSE"]]
        listing = client.get("/api/monitor-rules/taiwan").json()
        assert listing["total"] == 2
        assert all(r["source"] == "trade_plan" and r["plan_as_of"] == "2026-08-28"
                   and r["plan_identity"] == "fixed-plan" for r in listing["rules"])
        monkeypatch.setattr(watchlist, "list_symbols", lambda: [])
        assert client.post("/api/monitor-rules/taiwan/sync-plans").json() == {
            "created": 0, "removed": 2, "skipped": [],
        }
        assert calls == [["2330.TWSE"]]


@pytest.mark.parametrize("status", ["success", "partial", "failed"])
def test_scheduler_sync_only_after_success_and_quant_failure_is_isolated(monkeypatch, status):
    from app.taiwan import auto_watch
    from app.taiwan.quant import live_runner

    scheduler = Mock()
    monkeypatch.setattr(daily_pipeline, "AsyncIOScheduler", lambda **_kwargs: scheduler)
    events = []
    result = SimpleNamespace(overall_status=status, daily=SimpleNamespace(status=status),
                             institutional=SimpleNamespace(status=status), margin=SimpleNamespace(status=status))
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", lambda: SimpleNamespace(
        run_update=lambda **_kwargs: events.append("update") or result,
    ))
    monkeypatch.setattr(daily_pipeline, "_refresh_after_close_research", lambda: {})
    monkeypatch.setattr(live_runner, "run_live_after_refresh", Mock(side_effect=RuntimeError("quant failed")))
    monkeypatch.setattr(auto_watch, "sync_watchlist_plans", lambda: events.append("sync") or {})
    daily_pipeline.start_scheduler(Mock(), Mock())
    job = next(call for call in scheduler.add_job.call_args_list if call.kwargs["id"] == "taiwan_daily_update")
    job.args[0]()
    assert events == (["update", "sync"] if status == "success" else ["update"])
