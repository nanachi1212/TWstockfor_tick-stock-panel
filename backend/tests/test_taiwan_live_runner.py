"""Hermetic EOD -> immutable ledger -> matured outcome acceptance."""
from __future__ import annotations

import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import taiwan_live
from app.taiwan.corporate_actions import CorporateActionEvent, event_market_open
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.quant import live_runner
from app.taiwan.quant.feature_manifest import FeatureManifest, training_matrix
from app.taiwan.quant.live_contract import LiveModel, canonical_hash, latest_completed_session
from app.taiwan.quant.live_outcomes import mature_live_outcomes
from app.taiwan.quant.live_runner import LiveInputs, build_live_batch, run_current_live
from app.taiwan.quant.live_store import LiveConflictError, LiveLedger
from app.taiwan.quant.live_universe import current_verified_universe
from app.taiwan.quant.panel import build_factor_panel
from app.taiwan.quant.storage import FactorPanelStore
from app.taiwan.quant_eligibility import EligibilityPolicy, market_truth_gate
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar

DAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 17, tzinfo=TAIPEI_TZ)


def test_live_quant_alerts_are_persisted_before_sse_and_external_dispatch(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import alert_store
    from app.taiwan.realtime import monitor_engine as monitor_engine_module

    event = {"alert_id": "quant-event", "symbol": "2330.TWSE", "message": "原始"}
    calls = []
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(live_runner, "LiveModel", lambda: SimpleNamespace(key="model"))

    class Ledger:
        def read_run(self, _model_key, _session):
            return {"audit_status": "ok", "session": "2026-09-21", "snapshot": {"signals": []}}

    class Engine:
        def evaluate_quant_top10(self, _signals, _session, *, persist_events):
            accepted = persist_events([event])
            assert accepted == [event["alert_id"]]
            return [event]

    monkeypatch.setattr(monitor_engine_module, "get_monitor_engine", lambda: Engine())
    monkeypatch.setattr(
        alert_store,
        "append_many",
        lambda _data_dir, events: calls.append(("persist", events)) or [event["alert_id"] for event in events],
    )
    quote_service = SimpleNamespace(
        _format_extension_notifications=lambda events: [
            {**item, "message": item["message"] + " [formatted]"} for item in events
        ],
        push_alerts=lambda events: calls.append(("push", events)),
        _maybe_send_webhook=lambda events, _engine: calls.append(("external", events)),
    )

    result = live_runner._evaluate_live_quant_alerts(
        {"status": "frozen", "session": "2026-09-21"}, Ledger(),
        SimpleNamespace(quote_service=quote_service),
    )

    assert result == {"status": "available", "appended": 1}
    assert [kind for kind, _events in calls] == ["persist", "push", "external"]
    formatted = [{**event, "message": "原始 [formatted]"}]
    assert all(events == formatted for _kind, events in calls)


def test_live_quant_duplicate_alert_is_not_dispatched(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import alert_store
    from app.taiwan.realtime import monitor_engine as monitor_engine_module

    event = {"alert_id": "duplicate-live-event", "symbol": "2330.TWSE"}
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(live_runner, "LiveModel", lambda: SimpleNamespace(key="model"))

    class Ledger:
        def read_run(self, _model_key, _session):
            return {"audit_status": "ok", "session": "2026-09-21", "snapshot": {"signals": []}}

    class Engine:
        def evaluate_quant_top10(self, _signals, _session, *, persist_events):
            accepted = persist_events([event])
            return [item for item in [event] if item["alert_id"] in accepted]

    calls = []
    monkeypatch.setattr(monitor_engine_module, "get_monitor_engine", lambda: Engine())
    monkeypatch.setattr(alert_store, "append_many", lambda _data_dir, _events: [])
    quote_service = SimpleNamespace(
        _format_extension_notifications=lambda events: events,
        push_alerts=lambda events: calls.append(("push", events)),
        _maybe_send_webhook=lambda events, _engine: calls.append(("external", events)),
    )

    result = live_runner._evaluate_live_quant_alerts(
        {"status": "frozen", "session": "2026-09-21"}, Ledger(),
        SimpleNamespace(quote_service=quote_service),
    )

    assert result == {"status": "available", "appended": 0}
    assert calls == []


def session_days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]


@pytest.fixture
def inputs():
    days = session_days(DAY - timedelta(days=105), DAY)
    securities = []
    observed = []
    bars = []
    for index, symbol in enumerate(("2330.TWSE", "8069.TPEX", "2881.TWSE")):
        exchange = symbol.split(".")[1]
        securities.append({"symbol": symbol, "code": symbol.split(".")[0],
                           "exchange": exchange, "name": symbol, "instrument_type": "stock",
                           "listing_status": "active", "is_supported": True, "cfi_code": None,
                           "source": f"{exchange}_OPENAPI", "updated_at": NOW.isoformat()})
        for n, day in enumerate(days):
            close = 100.0 + index * 10 + n
            bars.append({"symbol": symbol, "date": day, "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 100000.0, "amount": 20000000.0})
        observed.append({**bars[-1], "exchange": exchange, "observed": True,
                         "source": "twse:MI_INDEX:ALLBUT0999" if exchange == "TWSE" else "tpex:dailyQuotes",
                         "retrieved_at": NOW.isoformat()})
    calendar = TaiwanTradingCalendar(known_trading_days=set(days))
    evidence = tuple(calendar.day_evidence(days[0] + timedelta(days=i), exchange)
                     for i in range((DAY - days[0]).days + 1) for exchange in ("TWSE", "TPEX"))
    return LiveInputs(DAY, NOW, pl.DataFrame(securities), pl.DataFrame(observed), pl.DataFrame(bars), (),
                      {"status": "verified", "start": days[0], "end": DAY,
                       "sources": sorted(SOURCE_URLS), "retrieved_at": NOW}, evidence)


@pytest.fixture
def environment(tmp_path, inputs):
    clock = [NOW]
    calendar = TaiwanTradingCalendar(known_trading_days=set(session_days(DAY - timedelta(days=110), DAY + timedelta(days=50))))
    ledger = LiveLedger(tmp_path / "live", clock=lambda: clock[0], evidence=calendar.day_evidence)
    source = SimpleNamespace(load=lambda day: inputs, evidence=calendar.day_evidence)
    return ledger, source, clock


def test_eod_to_ledger_preserves_missing_and_reuses_snapshot(inputs, environment):
    ledger, source, clock = environment
    result = run_current_live(source=source, ledger=ledger)
    assert result["status"] == "frozen"
    run = ledger.runs()[0]
    snapshot = run["snapshot"]
    assert snapshot["model"]["historical_primary_oos"]["status"] == "blocked"
    assert snapshot["validation_state"] == "unvalidated"
    assert len(snapshot["signals"]) == 1
    assert snapshot["signals"][0]["symbol"] == "2330.TWSE"
    assert snapshot["signals"][0]["confidence"] is None
    assert all(row["margin_balance"] is None and row["foreign_net_1d"] is None
               and row["industry_rank"] is None for row in snapshot["features"])
    assert all(o["status"] == "pending" for o in ledger.outcomes(run["model_key"], run["session"]))
    source.load = Mock(side_effect=AssertionError("retry may not reconstruct the original input"))
    clock[0] += timedelta(hours=15)
    assert run_current_live(source=source, ledger=ledger)["status"] == "noop"
    source.load.assert_not_called()
    assert ledger.runs()[0]["snapshot_hash"] == run["snapshot_hash"]


def test_overlapping_runners_collect_one_snapshot_then_retry_is_noop(inputs, environment):
    ledger, source, _ = environment
    entered, release = Event(), Event()

    def load_first(day):
        entered.set()
        assert release.wait(15)
        return inputs

    source.load = load_first
    second = LiveLedger(ledger.root, clock=lambda: NOW + timedelta(seconds=1),
                        evidence=ledger.evidence)
    other_source = SimpleNamespace(load=Mock(return_value=replace(
        inputs, cutoff=NOW + timedelta(seconds=1))), evidence=ledger.evidence)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run_current_live, source=source, ledger=ledger)
        try:
            assert entered.wait(15)
            assert run_current_live(source=other_source, ledger=second) == {
                "status": "skipped", "reason": "live_run_in_progress"}
            other_source.load.assert_not_called()
        finally:
            release.set()
        assert first.result(timeout=15)["status"] == "frozen"
    assert run_current_live(source=other_source, ledger=second)["status"] == "noop"
    other_source.load.assert_not_called()
    assert len(ledger.runs()) == 1
    assert ledger.runs()[0]["audit_status"] == "ok"


def _hold_construction_lock(root, entered, release):
    with LiveLedger(root).construction_lock(LiveModel()):
        entered.set()
        release.wait(30)


@pytest.mark.parametrize("crash", [False, True])
def test_construction_lock_is_cross_process_and_recovers_on_exit(environment, crash):
    ledger, source, _ = environment
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    owner = context.Process(target=_hold_construction_lock,
                            args=(ledger.root, entered, release))
    owner.start()
    try:
        assert entered.wait(20)
        assert run_current_live(source=source, ledger=ledger) == {
            "status": "skipped", "reason": "live_run_in_progress"}
        assert ledger.models() == []  # No evidence collection or activation before claim.
        if crash:
            owner.terminate()
        else:
            release.set()
        owner.join(timeout=10)
        assert not owner.is_alive()
        assert run_current_live(source=source, ledger=ledger)["status"] == "frozen"
        assert ledger.runs()[0]["audit_status"] == "ok"
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(timeout=10)


def test_feature_and_snapshot_hash_ignore_row_column_order(inputs):
    a = build_live_batch(inputs, LiveModel())
    b = build_live_batch(replace(inputs, master=inputs.master.reverse(),
                                observed=inputs.observed.reverse(),
                                history=inputs.history.reverse().select(reversed(inputs.history.columns))),
                         LiveModel())
    assert canonical_hash(a.snapshot) == canonical_hash(b.snapshot)
    assert a.snapshot["feature_snapshot_hash"] == b.snapshot["feature_snapshot_hash"]


@pytest.mark.parametrize(("field", "value"), [
    ("listing_status", "inactive"), ("listing_status", "suspended"),
    ("instrument_type", "etf"), ("is_supported", False), ("source", "cache"),
    ("updated_at", "2026-09-18T17:00:00+08:00"),
    ("updated_at", "2026-09-22T17:00:00+08:00"),
])
def test_current_inactive_wrong_type_stale_are_excluded(inputs, field, value):
    master = inputs.master.with_columns(
        pl.when(pl.col("symbol") == "2330.TWSE").then(pl.lit(value)).otherwise(pl.col(field)).alias(field))
    universe = current_verified_universe(master, inputs.observed, session=DAY, cutoff=NOW)
    assert "2330.TWSE" not in universe["symbol"].to_list()
    assert universe.height == 2


def test_current_and_historical_cannot_cross_admission_or_storage(inputs, tmp_path):
    universe = current_verified_universe(inputs.master, inputs.observed, session=DAY, cutoff=NOW)
    with pytest.raises(ValueError, match="live universe"):
        market_truth_gate(universe, EligibilityPolicy())
    with pytest.raises(ValueError, match="historical"):
        current_verified_universe(inputs.master, inputs.observed.with_columns(
            pl.lit(DAY - timedelta(days=3)).alias("date")), session=DAY, cutoff=NOW)
    panel = build_factor_panel(inputs.history, events=(), policy_version="x",
                               universe_tier="current_live_verified", as_of=DAY)
    with pytest.raises(ValueError, match="live"):
        FactorPanelStore(tmp_path).save(panel)
    with pytest.raises(ValueError, match="live"):
        training_matrix(panel.values, FeatureManifest("x"), {})


def test_empty_selection_is_a_frozen_event_not_a_fabricated_top_ten(inputs, environment):
    ledger, source, _ = environment
    declining = inputs.history.with_columns(
        (400.0 - pl.col("close")).alias("close"),
        (400.0 - pl.col("open")).alias("open"),
        (400.0 - pl.col("low")).alias("high"),
        (400.0 - pl.col("high")).alias("low"))
    observed = inputs.observed.drop("open", "high", "low", "close").join(
        declining.filter(pl.col("date") == DAY).select("symbol", "open", "high", "low", "close"), on="symbol")
    source.load = lambda day: replace(inputs, history=declining, observed=observed)
    result = run_current_live(source=source, ledger=ledger)
    assert result["status"] == "frozen"
    assert result["signal_count"] == 0
    assert ledger.runs()[0]["snapshot"]["signals"] == []


def test_missing_liquidity_not_zero_and_short_gapped_history_excluded(inputs):
    missing = inputs.history.with_columns(
        pl.when(pl.col("symbol") == "2330.TWSE").then(None).otherwise(pl.col("amount")).alias("amount"))
    gapped = missing.filter(~((pl.col("symbol") == "8069.TPEX") &
                             (pl.col("date") == DAY - timedelta(days=3))))
    observed = inputs.observed.with_columns(
        pl.when(pl.col("symbol") == "2330.TWSE").then(None).otherwise(pl.col("amount")).alias("amount"))
    result = build_live_batch(replace(inputs, history=gapped, observed=observed), LiveModel())
    assert "2330.TWSE" not in [r["symbol"] for r in result.snapshot["eligible_universe"]]
    assert "8069.TPEX" not in result.snapshot["ranking_universe"]
    # Missing feature rows are never used as zeroes.
    assert all(r["score"] is not None for r in result.snapshot["ranking"])


def test_unproven_actions_block_even_if_event_store_is_empty(inputs):
    with pytest.raises(ValueError, match="corporate_action"):
        build_live_batch(replace(inputs, action_coverage={"status": "unavailable"}), LiveModel())


def test_unknown_calendar_gap_cannot_shorten_feature_horizon(inputs):
    evidence = tuple(replace(e, status="unresolved") if e.date == DAY - timedelta(days=3) else e
                     for e in inputs.session_evidence)
    with pytest.raises(ValueError, match="feature_session_evidence_unresolved"):
        build_live_batch(replace(inputs, session_evidence=evidence), LiveModel())


def test_live_model_identity_rejects_changed_policy_and_tier_spoof(inputs, environment):
    ledger, _, _ = environment
    model = LiveModel()
    ledger.activate(model)
    with pytest.raises(LiveConflictError, match="version"):
        ledger.activate(replace(model, min_rank=0.8))
    draft = build_live_batch(inputs, model)
    changed = {**draft.snapshot, "contract": "primary_verified"}
    with pytest.raises(ValueError, match="contract"):
        ledger.freeze(replace(draft, snapshot=changed))
    changed = {**draft.snapshot, "oos_prediction": [{"forward_return": 0.9}]}
    with pytest.raises(ValueError, match="OOS"):
        ledger.freeze(replace(draft, snapshot=changed))
    assert ledger.runs() == []


def test_publication_boundary_crossing_inside_evidence_is_blocked(tmp_path):
    clock = [datetime(2026, 9, 22, 15, 59, tzinfo=TAIPEI_TZ)]
    cal = TaiwanTradingCalendar(known_trading_days={DAY, DAY + timedelta(days=1)})
    def slow_evidence(day, exchange):
        clock[0] = datetime(2026, 9, 22, 16, 1, tzinfo=TAIPEI_TZ)
        return cal.day_evidence(day, exchange)
    ledger = LiveLedger(tmp_path, clock=lambda: clock[0], evidence=slow_evidence)
    with pytest.raises(ValueError, match="boundary_changed"):
        ledger.activate(LiveModel())
    assert ledger.models() == []


def test_source_failure_and_session_change_during_collection_leave_no_signal(environment, inputs):
    ledger, source, clock = environment
    source.load = Mock(side_effect=ValueError("official_source_unavailable"))
    assert run_current_live(source=source, ledger=ledger)["status"] == "blocked"
    assert ledger.runs() == []
    assert ledger.models()[0]["first_live_session"] == str(DAY)
    assert ledger.latest_operation()["reason"] == "official_source_unavailable"
    def crossed_boundary(day):
        clock[0] += timedelta(days=1)
        return inputs
    source.load = crossed_boundary
    assert run_current_live(source=source, ledger=ledger)["status"] == "blocked"
    assert ledger.runs() == []


def test_no_automatic_recalculation_after_all_horizons_verified(inputs, environment):
    ledger, source, clock = environment
    run_current_live(source=source, ledger=ledger)
    outcome_source(inputs, source)
    clock[0] = NOW + timedelta(days=28)
    mature_live_outcomes(ledger, source)
    source.outcome_prices = Mock(side_effect=AssertionError("finalized outcome should not be rebuilt"))
    source.actions = Mock(side_effect=AssertionError("finalized outcome should not fetch actions"))
    assert mature_live_outcomes(ledger, source)["appended"] == 0


def test_price_adjustment_failure_cannot_enter_ranking(inputs):
    event_day = DAY - timedelta(days=5)
    event = CorporateActionEvent(
        symbol="2330.TWSE", exchange="TWSE", effective_date=event_day,
        effective_at=event_market_open(event_day), event_type="stock_dividend",
        previous_close=None, reference_price=None, factor=None, cash_dividend=None,
        free_share_ratio=None, reduction_ratio=None, source="TWT49U",
        source_url="https://www.twse.com.tw/", retrieved_at=NOW,
        status="data_insufficient", reason="missing_reference")
    result = build_live_batch(replace(inputs, events=(event,)), LiveModel())
    assert "2330.TWSE" not in result.snapshot["ranking_universe"]


def test_session_resolution_next_morning_weekend_holiday_unknown():
    friday = date(2026, 9, 18)
    cal = TaiwanTradingCalendar(known_trading_days={friday, DAY})
    for stamp in (datetime(2026, 9, 19, 17, tzinfo=TAIPEI_TZ),
                  datetime(2026, 9, 21, 9, tzinfo=TAIPEI_TZ)):
        assert latest_completed_session(stamp, cal.day_evidence) == friday
    cal.add_holiday(DAY)
    with pytest.raises(ValueError, match="unresolved"):
        latest_completed_session(NOW, cal.day_evidence)  # conflicting facts
    cal.known_trading_days.remove(DAY)
    assert latest_completed_session(NOW, cal.day_evidence) == friday
    with pytest.raises(ValueError, match="unresolved"):
        latest_completed_session(NOW + timedelta(days=1), cal.day_evidence)
    # UTC clock must resolve in Taipei.
    assert latest_completed_session(NOW.astimezone(__import__("datetime").timezone.utc),
                                    TaiwanTradingCalendar(known_trading_days={DAY}).day_evidence) == DAY


def test_concurrent_identical_and_conflicting_writers(inputs, environment):
    ledger, _, _ = environment
    model = LiveModel()
    ledger.activate(model)
    draft = build_live_batch(inputs, model)
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: ledger.freeze(draft)["status"], range(4)))
    assert sorted(statuses) == ["frozen", "noop", "noop", "noop"]
    changed = replace(draft, snapshot={**draft.snapshot, "raw_history_hash": "different"})
    with pytest.raises(LiveConflictError):
        ledger.freeze(changed)
    assert ledger.runs()[0]["audit_status"] == "conflict"
    with sqlite3.connect(ledger.root / "signals.sqlite3") as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM runs")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE models SET first_live_session='2020-01-01'")


def outcome_source(inputs, source, *, split=False):
    entry = inputs.observed.filter(pl.col("symbol") == "2330.TWSE")["close"].item()
    source.outcome_prices = lambda symbol, days: pl.DataFrame({
        "symbol": [symbol] * len(days), "date": days,
        "close": [entry * (0.5 if split else 1.0) * 1.1] * len(days)})
    events = ()
    if split:
        events = (CorporateActionEvent(
            symbol="2330.TWSE", exchange="TWSE", effective_date=DAY + timedelta(days=1),
            effective_at=event_market_open(DAY + timedelta(days=1)), event_type="stock_dividend",
            previous_close=entry, reference_price=entry / 2, factor=0.5,
            cash_dividend=None, free_share_ratio=1.0, reduction_ratio=None, source="TWT49U",
            source_url="https://www.twse.com.tw/", retrieved_at=NOW, status="verified",
            precision_method="official_reference_ratio"),)
    source.actions = lambda start, end: (events, {
        "status": "verified", "start": start, "end": end, "sources": sorted(SOURCE_URLS)})
    return source


@pytest.mark.parametrize("split", [False, True])
def test_maturation_actual_sessions_and_adjustment_are_independent(inputs, environment, split):
    ledger, source, clock = environment
    assert run_current_live(source=source, ledger=ledger)["status"] == "frozen"
    frozen = ledger.runs()[0]
    outcome_source(inputs, source, split=split)
    # Next day BEFORE cutoff: no 1D outcome yet.
    clock[0] = NOW + timedelta(hours=15)
    mature_live_outcomes(ledger, source)
    assert all(o["status"] == "pending" for o in ledger.outcomes(frozen["model_key"], str(DAY)))
    clock[0] = NOW + timedelta(days=1)
    assert mature_live_outcomes(ledger, source)["appended"] == 3
    outcomes = ledger.outcomes(frozen["model_key"], str(DAY))
    assert outcomes[0]["value"] == pytest.approx(0.1)
    assert outcomes[0]["end_session"] == "2026-09-22"
    assert outcomes[1]["status"] == "pending"
    assert mature_live_outcomes(ledger, source, recheck_verified=True)["noop"] == 3
    assert ledger.runs()[0] == frozen
    # Fifth market session is Monday (weekend does not count).
    clock[0] = NOW + timedelta(days=7)
    mature_live_outcomes(ledger, source)
    outcomes = ledger.outcomes(frozen["model_key"], str(DAY))
    assert outcomes[1]["end_session"] == "2026-09-28"
    assert outcomes[1]["status"] == "verified"
    assert outcomes[2]["status"] == "pending"
    clock[0] = NOW + timedelta(days=28)
    mature_live_outcomes(ledger, source)
    assert ledger.outcomes(frozen["model_key"], str(DAY))[2]["status"] == "verified"
    assert (ledger.root / "outcomes.sqlite3").exists()
    with sqlite3.connect(ledger.root / "signals.sqlite3") as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='observations'").fetchone() is None


def test_outcome_missing_session_is_not_shifted_and_revision_is_conflict(inputs, environment):
    ledger, source, clock = environment
    run_current_live(source=source, ledger=ledger)
    run = ledger.runs()[0]
    outcome_source(inputs, source)
    clock[0] = NOW + timedelta(days=7)
    original = source.outcome_prices
    source.outcome_prices = lambda symbol, days: original(symbol, days).slice(1)
    mature_live_outcomes(ledger, source)
    assert ledger.outcomes(run["model_key"], str(DAY))[0]["status"] == "data_insufficient"
    source.outcome_prices = original
    mature_live_outcomes(ledger, source)
    assert ledger.outcomes(run["model_key"], str(DAY))[0]["status"] == "verified"
    source.outcome_prices = lambda symbol, days: original(symbol, days).slice(1)
    mature_live_outcomes(ledger, source, recheck_verified=True)
    # Preserve the published value while making a failed recheck visible.
    rechecked = ledger.outcomes(run["model_key"], str(DAY))[0]
    assert rechecked["value"] == pytest.approx(0.1)
    assert rechecked["audit_status"] == "recheck_unavailable"
    source.outcome_prices = original
    mature_live_outcomes(ledger, source, recheck_verified=True)
    assert ledger.outcomes(run["model_key"], str(DAY))[0]["audit_status"] == "ok"
    source.outcome_prices = lambda symbol, days: original(symbol, days).with_columns(pl.col("close") * 2)
    assert mature_live_outcomes(ledger, source, recheck_verified=True)["status"] == "conflict"
    assert ledger.outcomes(run["model_key"], str(DAY))[0]["status"] == "conflict"
    assert ledger.outcomes(run["model_key"], str(DAY))[0]["value"] == pytest.approx(0.1)


def test_outcome_writer_rejects_premature_label(inputs, environment):
    ledger, source, _ = environment
    run_current_live(source=source, ledger=ledger)
    run = ledger.runs()[0]
    with pytest.raises(ValueError, match="mature"):
        ledger.observe_outcome(run["model_key"], str(DAY), "2330.TWSE", 1,
                               {"status": "verified", "value": 0.1, "end_session": DAY + timedelta(days=1)})


def test_read_api_empty_real_outcomes_404_and_no_write_route(environment, monkeypatch):
    ledger, source, _ = environment
    monkeypatch.setattr(taiwan_live, "CurrentLiveSource", lambda: SimpleNamespace(
        evidence=source.evidence, close=lambda: None,
    ))
    monkeypatch.setattr(taiwan_live, "LiveLedger", lambda evidence=None: ledger)
    app = FastAPI()
    app.include_router(taiwan_live.router)
    client = TestClient(app)
    assert client.get("/api/taiwan/quant/live/runs").json() == {"runs": []}
    assert client.get("/api/taiwan/quant/live/models").json()["activations"] == []
    assert client.get("/api/taiwan/quant/live/runs/no/2026-09-21").status_code == 404
    assert client.post("/api/taiwan/quant/live/runs", json={"session": "2020-01-01"}).status_code == 405
    run_current_live(source=source, ledger=ledger)
    response = client.get(f"/api/taiwan/quant/live/runs/{LiveModel().key}/2026-09-21")
    assert response.status_code == 200
    assert response.json()["snapshot"]["validation_state"] == "unvalidated"
    assert response.json()["outcomes"][0]["status"] == "pending"
