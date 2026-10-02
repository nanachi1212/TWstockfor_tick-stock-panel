from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from test_taiwan_selection_forward import _clock, _FixedScreener, _seed

from app.api.strategy_lab import service as lab_dependency
from app.main import app
from app.taiwan.quant.live_contract import LIVE_CONTRACT, LiveModel, LiveSignalBatch, canonical_hash
from app.taiwan.quant.live_store import LiveLedger
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.selection_review_models import SaveSelectionSnapshotRequest
from app.taiwan.strategy_lab import LabFilters, Observation, StrategyLabService, identity, summarize


def _identity(**overrides):
    return identity(**{
        "strategy_id": "trend_liquidity_v1", "strategy_name": "Trend", "version": "v1",
        "source": "Selection", "definition_digest": None, "entry_basis": "next_open",
        "price_semantics": "raw", "cost_assumption": "no_cost", **overrides,
    })


def _row(index=0, **overrides):
    return Observation(**{
        "observation_id": str(index), "snapshot_id": f"s{index}", "symbol": "2330.TWSE",
        "name": "TSMC", "signal_date": "2026-08-03", "as_of": "2026-08-03T15:00:00+08:00",
        "horizon": 1, "status": "matured", "return_pct": 0.01,
        "benchmark_return_pct": 0.001, "excess_pct": 0.009, "identity": _identity(),
        "exchange": "TWSE", "risk_status": "clear", **overrides,
    })


def test_denominator_pending_unavailable_strict_positive_and_threshold():
    rows = [_row(i, return_pct=value) for i, value in enumerate([0, -1, 0.000001, 1, 2])]
    rows += [_row(5, status="pending", return_pct=100), _row(6, status="unavailable", return_pct=100)]
    stats = summarize(rows, 5)
    assert (stats.matured, stats.hit_rate_denominator, stats.pending, stats.unavailable) == (5, 5, 1, 1)
    assert stats.hit_count == 3
    assert stats.hit_rate_pct == 60
    assert stats.average_return_pct == pytest.approx(2.000001 / 5)
    assert summarize(rows, 6).hit_rate_pct is None
    assert summarize(rows, 6).average_return_pct is None
    assert summarize(rows[:2], 5).hit_rate_pct is None
    assert summarize([], 5).hit_rate_denominator == 0
    assert summarize([], 5).hit_rate_pct is None


def test_benchmark_pair_denominator_independent_from_return_n():
    rows = [_row(i) for i in range(5)] + [_row(5, benchmark_return_pct=None, excess_pct=None)]
    stats = summarize(rows, 5)
    assert stats.matured == 6
    assert stats.benchmark_n == stats.excess_n == 5
    assert stats.average_excess_pct == pytest.approx(0.009)
    assert stats.average_benchmark_return_pct == pytest.approx(0.001)
    rows[0] = _row(0, benchmark_return_pct=None, excess_pct=None)
    assert summarize(rows, 5).average_excess_pct is None


def test_identity_never_merges_sources_versions_configuration_or_entry_basis():
    base = _identity()
    assert base.key == _identity(strategy_name="Renamed").key
    others = [_identity(source="A13 Buy Point"), _identity(version="v2"),
              _identity(definition_digest="changed"), _identity(entry_basis="reference_close"),
              _identity(strategy_id="multi_factor_consensus_v1"), _identity(strategy_id="breakout_v1")]
    assert len({base.key, *(other.key for other in others)}) == 7


def _lab(tmp_path, monkeypatch):
    selection, source, sessions = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    snap = selection.lock_forward_batch(_FixedScreener(source))
    now = _clock(sessions[-1])
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: now)
    monkeypatch.setattr("app.taiwan.strategy_lab.taipei_now", lambda: now)
    return StrategyLabService(selection, LiveLedger(tmp_path / "live")), snap, sessions


def test_forward_drilldown_provenance_excess_actual_sessions_and_immutable_bytes(tmp_path, monkeypatch):
    lab, snap, sessions = _lab(tmp_path, monkeypatch)
    before = lab.selection.path.read_bytes()
    page = lab.drilldown(LabFilters())
    rows = {row.horizon: row for row in page.observations}
    assert rows[1].status == "matured"
    assert rows[1].return_pct == pytest.approx(0.004)
    assert rows[1].benchmark_return_pct == pytest.approx(0.008)
    assert rows[1].excess_pct == pytest.approx(-0.004)
    assert rows[5].outcome_date == sessions[4].isoformat()
    assert rows[20].outcome_date == sessions[19].isoformat()
    assert rows[1].entry_date == sessions[0].isoformat()
    assert rows[1].entry_price == 100
    assert rows[1].snapshot_id == snap.snapshot_id
    assert rows[1].provenance["selection_action_events_sha256"] == lab.selection.action_store.snapshot_digest()
    assert rows[1].provenance["snapshot_digest"] == canonical_hash(snap.model_dump())
    assert rows[1].provenance["evaluation_inputs_digest"]
    assert rows[1].identity.entry_basis == "next_open"
    assert rows[1].evidence_label == "Forward / OOS observed"
    assert lab.selection.path.read_bytes() == before
    assert not lab.ledger.root.exists()


def test_unclosed_horizon_pending_not_counted_and_missing_benchmark(tmp_path, monkeypatch):
    lab, _, sessions = _lab(tmp_path, monkeypatch)
    now = _clock(sessions[0])
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: now)
    monkeypatch.setattr("app.taiwan.strategy_lab.taipei_now", lambda: now)
    stats = lab.overview(LabFilters()).strategies[0].horizons
    assert stats["1D"].matured == 1
    assert stats["5D"].pending == stats["20D"].pending == 1
    assert stats["5D"].hit_rate_denominator == 0

    now = now.replace(hour=12)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: now)
    monkeypatch.setattr("app.taiwan.strategy_lab.taipei_now", lambda: now)
    assert all(row.entry_price is None for row in lab.drilldown(LabFilters()).observations)


def test_duplicate_snapshots_and_conflicting_snapshot_ids_fail_closed(tmp_path, monkeypatch):
    lab, snap, _ = _lab(tmp_path, monkeypatch)
    lab.selection.path.write_text(json.dumps([snap.model_dump(), snap.model_dump()]), encoding="utf-8")
    overview = lab.overview(LabFilters())
    assert overview.duplicate_snapshots == 1
    assert overview.sample_count == 1
    conflict = snap.model_copy(update={"strategy_id": "breakout_v1"})
    lab.selection.path.write_text(json.dumps([snap.model_dump(), conflict.model_dump()]), encoding="utf-8")
    overview = lab.overview(LabFilters())
    assert overview.integrity_conflicts == 1
    assert overview.sample_count == 0


def test_research_excluded_legacy_a13_unavailable_and_future_server_a13_admitted(tmp_path, monkeypatch):
    lab, snap, _ = _lab(tmp_path, monkeypatch)
    request = SaveSelectionSnapshotRequest(strategy_id="buy_point:breakout", strategy_name="Breakout",
        source="Buy Point", as_of_date=snap.as_of_date, items=snap.items)
    old = lab.selection.save_snapshot(request).model_copy(update={"created_at": snap.created_at})
    lab.selection.path.write_text(json.dumps([snap.model_dump(), old.model_dump()]), encoding="utf-8")
    assert all(row.status == "unavailable" for row in lab.drilldown(LabFilters(source="A13 Buy Point")).observations)
    assert "a13_forward_provenance_not_persisted" in {
        row.reason for row in lab.drilldown(LabFilters(source="A13 Buy Point")).observations}
    # Fix only fixture timestamps, never mutate application snapshots.
    new = old.model_copy(update={"snapshot_id": "server-a13", "created_at": snap.created_at,
        "observation_origin": "a13_server_observed", "strategy_definition_digest": "definition"})
    research = old.model_copy(update={"snapshot_id": "research", "source": "Screener"})
    lab.selection.path.write_text(json.dumps([snap.model_dump(), new.model_dump(), research.model_dump()]), encoding="utf-8")
    overview = lab.overview(LabFilters())
    assert overview.excluded_research_snapshots == 1
    assert overview.sample_count == 2
    assert len(overview.strategies) == 2
    assert lab.drilldown(LabFilters(source="A13 Buy Point")).observations[0].status == "matured"
    saved = lab.selection.save_snapshot(request, buy_point_definition={"id": "breakout", "risk_filters": {}})
    assert saved.observation_origin == "a13_server_observed"
    assert saved.strategy_definition_digest == canonical_hash({"id": "breakout", "risk_filters": {}})
    with pytest.raises(PermissionError):
        lab.selection.delete_snapshot(saved.snapshot_id)


class _DailyLedger:
    def signal_outcomes(self, _key, _session, _signals, **_kwargs):
        return [{"symbol": "2330.TWSE", "horizon": h, "status": "verified", "value": 0.000001,
                 "end_session": "2026-08-04", "audit_status": "ok", "observations": []}
                for h in (1, 5, 20)]


def test_daily_decimal_conversion_benchmark_unavailable_and_recheck_conflict(tmp_path, monkeypatch):
    lab, _, _ = _lab(tmp_path, monkeypatch)
    snapshot = {"model": {"model_id": "momentum", "version": "v1"}, "contract": LIVE_CONTRACT,
                "signal_session": "2026-08-03", "signals": [{"symbol": "2330.TWSE", "reference_close": 100}],
                "regime": {"status": "partial", "regime": "neutral", "as_of": "2026-08-03"}}
    run = {"snapshot": snapshot, "model_key": "key", "session": "2026-08-03", "audit_status": "ok",
           "frozen_at": "2026-08-03T17:00:00+08:00", "snapshot_hash": canonical_hash(snapshot)}
    lab.ledger = _DailyLedger()
    rows = lab._daily_rows(run)
    assert rows[0].return_pct == pytest.approx(0.0001)
    assert rows[0].excess_pct is None
    assert rows[0].benchmark_reason == "benchmark_not_persisted_in_live_ledger"
    assert rows[0].market_regime is None
    run["audit_status"] = "conflict"
    assert all(row.status == "unavailable" for row in lab._daily_rows(run))


def test_filters_api_pagination_and_storage_error(tmp_path, monkeypatch):
    lab, _, _ = _lab(tmp_path, monkeypatch)
    app.dependency_overrides[lab_dependency] = lambda: lab
    try:
        client = TestClient(app)
        response = client.get("/api/taiwan/strategy-lab")
        assert response.status_code == 200
        payload = response.json()
        assert payload["historical_pit_status"] == "未完全可用"
        key = payload["strategies"][0]["identity"]["key"]
        assert client.get("/api/taiwan/strategy-lab?exchange=TPEX").json()["sample_count"] == 0
        assert client.get("/api/taiwan/strategy-lab?minimum_sample=2").status_code == 422
        assert client.get("/api/taiwan/strategy-lab?source=invalid").status_code == 422
        page = client.get("/api/taiwan/strategy-lab/observations", params={"strategy_key": key, "limit": 1, "offset": 1}).json()
        assert page["total"] == 3
        assert len(page["observations"]) == 1
        assert page["observations"][0]["identity"]["key"] == key
        assert payload["slice_availability"]["industry"] != "persisted"
        lab.selection.path.write_text("corrupt", encoding="utf-8")
        assert client.get("/api/taiwan/strategy-lab").status_code == 503
    finally:
        app.dependency_overrides.pop(lab_dependency, None)


def test_readonly_live_connection_does_not_create_database(tmp_path):
    ledger = LiveLedger(tmp_path / "live")
    rows = ledger.signal_outcomes("key", "2026-08-03", [{"symbol": "2330.TWSE"}], read_only=True)
    assert len(rows) == 3
    assert {row["status"] for row in rows} == {"pending"}
    assert not ledger.root.exists()


def test_semantic_duplicate_snapshots_do_not_reweight_a_strategy(tmp_path, monkeypatch):
    lab, snap, _ = _lab(tmp_path, monkeypatch)
    retry = snap.model_copy(update={"snapshot_id": "retry-id"})
    lab.selection.path.write_text(json.dumps([snap.model_dump(), retry.model_dump()]), encoding="utf-8")
    overview = lab.overview(LabFilters())
    assert overview.sample_count == 1
    assert overview.duplicate_samples == 1
    assert lab.drilldown(LabFilters()).total == 3
    assert len(lab.drilldown(LabFilters()).observations[0].provenance["duplicate_snapshot_ids"]) == 2


def test_unavailable_stock_and_benchmark_exclusion(tmp_path, monkeypatch):
    selection, source, sessions = _seed(tmp_path, missing_stock_5d=True, missing_bm_20d=True)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    selection.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(sessions[-1]))
    monkeypatch.setattr("app.taiwan.strategy_lab.taipei_now", lambda: _clock(sessions[-1]))
    lab = StrategyLabService(selection, LiveLedger(tmp_path / "live"))
    stats = lab.overview(LabFilters()).strategies[0].horizons
    assert stats["5D"].unavailable == 1
    assert stats["5D"].matured == stats["5D"].hit_rate_denominator == 0
    assert stats["20D"].matured == 1
    assert stats["20D"].benchmark_n == stats["20D"].excess_n == 0
    row = next(row for row in lab.drilldown(LabFilters()).observations if row.horizon == 20)
    assert row.return_pct is not None
    assert row.excess_pct is None


def test_actual_daily_ledger_readonly_projection_and_recheck_unavailable(tmp_path, monkeypatch):
    lab, _, _ = _lab(tmp_path, monkeypatch)
    clock = [datetime(2026, 9, 21, 17, tzinfo=TAIPEI_TZ)]
    calendar = TaiwanTradingCalendar(known_trading_days={date(2026, 9, 21), date(2026, 9, 22)})
    ledger = LiveLedger(tmp_path / "real-live", clock=lambda: clock[0], evidence=calendar.day_evidence)
    model = LiveModel()
    ledger.activate(model)
    features = [{"symbol": "2330.TWSE"}]
    snapshot = {
        "contract": LIVE_CONTRACT, "signal_session": date(2026, 9, 21), "data_cutoff": clock[0],
        "model": model.describe(), "usage_scope": "experimental_live", "validation_state": "unvalidated",
        "eligible_universe": [{"symbol": "2330.TWSE", "universe_contract": LIVE_CONTRACT}],
        "features": features, "feature_snapshot_hash": canonical_hash(features),
        "signals": [{"symbol": "2330.TWSE", "name": "TSMC", "reference_close": 100}],
    }
    ledger.freeze(LiveSignalBatch(model, date(2026, 9, 21), snapshot))
    clock[0] = datetime(2026, 9, 22, 17, tzinfo=TAIPEI_TZ)
    ledger.observe_outcome(model.key, "2026-09-21", "2330.TWSE", 1,
                           {"status": "verified", "value": 0.01, "end_session": "2026-09-22"})
    monkeypatch.setattr("app.taiwan.strategy_lab.taipei_now", lambda: clock[0])
    lab.ledger = ledger
    before = {path: path.read_bytes() for path in ledger.root.glob("*.sqlite3")}
    rows = lab.drilldown(LabFilters(source="Daily recommendation")).observations
    matured = next(row for row in rows if row.horizon == 1)
    assert matured.status == "matured"
    assert matured.return_pct == 1
    assert matured.provenance["snapshot_hash"] == canonical_hash(snapshot)
    assert before == {path: path.read_bytes() for path in ledger.root.glob("*.sqlite3")}
    ledger.observe_outcome(model.key, "2026-09-21", "2330.TWSE", 1,
                           {"status": "data_insufficient", "value": None, "reason": "missing_price"})
    rows = lab.drilldown(LabFilters(source="Daily recommendation")).observations
    row = next(row for row in rows if row.horizon == 1)
    assert row.status == "unavailable"
    assert row.reason == "recheck_unavailable"
    assert row.return_pct is None


def test_zero_candidate_formal_strategy_keeps_identity_without_zero_percent(tmp_path, monkeypatch):
    lab, snap, _ = _lab(tmp_path, monkeypatch)
    empty = snap.model_copy(update={"snapshot_id": "zero-batch", "items": [], "selected_symbols": []})
    lab.selection.path.write_text(json.dumps([empty.model_dump()]), encoding="utf-8")
    stats = lab.overview(LabFilters()).strategies[0]
    assert stats.sample_count == 0
    assert stats.snapshot_count == 1
    assert stats.identity.strategy_id == snap.strategy_id
    assert stats.horizons["1D"].hit_rate_pct is None


def test_pending_cache_expires_at_market_close_and_action_cache_on_evidence_change(tmp_path, monkeypatch):
    lab, snap, sessions = _lab(tmp_path, monkeypatch)
    now = [_clock(sessions[0]).replace(hour=12)]
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: now[0])
    count = [0]
    original = lab.selection._get_forward_batch_review

    def counted(snapshot):
        count[0] += 1
        return original(snapshot)

    monkeypatch.setattr(lab.selection, "_get_forward_batch_review", counted)
    assert lab.selection.review_snapshot(snap).h1d_pending_count == 1
    assert lab.selection.review_snapshot(snap).h1d_pending_count == 1
    assert count[0] == 1
    now[0] = now[0].replace(hour=15)
    assert lab.selection.review_snapshot(snap).h1d_evaluated_count == 1
    assert count[0] == 2
    lab.selection.action_store.path.with_name("coverage.json").write_text("{}", encoding="utf-8")
    assert lab.selection.review_snapshot(snap).h1d_unavailable_count == 1
    assert count[0] == 3


def test_a13_never_replaces_invalid_saved_entry_with_a_later_store_price(tmp_path, monkeypatch):
    lab, snap, _ = _lab(tmp_path, monkeypatch)
    invalid = snap.model_copy(update={"source": "Buy Point", "record_type": "research",
        "observation_origin": "a13_server_observed", "strategy_definition_digest": "config",
        "evaluation_basis": "reference_close", "items": [snap.items[0].model_copy(update={"price": 0})]})
    lab.selection.path.write_text(json.dumps([invalid.model_dump()]), encoding="utf-8")
    rows = lab.drilldown(LabFilters()).observations
    assert {row.status for row in rows} == {"unavailable"}
    assert {row.reason for row in rows} == {"invalid_saved_reference_price"}
    assert all(row.return_pct is None for row in rows)
