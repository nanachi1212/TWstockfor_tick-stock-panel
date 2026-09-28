"""Live events are not a backtest import surface."""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from app.taiwan.quant.live_contract import LiveModel, LiveSignalBatch, canonical_hash
from app.taiwan.quant.live_store import LiveConflictError, LiveLedger
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar


def test_hash_canonicalizes_rows_keys_numbers_and_timezone():
    assert canonical_hash({"rows": [{"symbol": "b", "v": 1.0}, {"v": None, "symbol": "a"}]}) == canonical_hash(
        {"rows": [{"symbol": "a", "v": None}, {"v": 1, "symbol": "b"}]})
    with pytest.raises(ValueError):
        canonical_hash({"v": float("nan")})


def test_horizon_summary_uses_strict_positive_hit_rate_and_excludes_pending_unavailable():
    rows = [
        {"horizon": 1, "status": "verified", "value": 0.01},
        {"horizon": 1, "status": "verified", "value": 0.0},
        {"horizon": 1, "status": "verified", "value": -0.02},
        {"horizon": 1, "status": "pending", "value": None},
        {"horizon": 1, "status": "data_insufficient", "value": None},
    ]

    summary = LiveLedger.horizon_summary(rows, 1)

    assert summary == {
        "horizon": "1D", "evaluated_count": 3, "pending_count": 1,
        "unavailable_count": 1, "hit_count": 1, "hit_rate_pct": pytest.approx(100 / 3),
        "average_return_pct": pytest.approx(-1 / 3),
    }
    assert LiveLedger.horizon_summary([{"horizon": 20, "status": "pending", "value": None}], 20)["hit_rate_pct"] is None


def test_old_snapshot_projection_is_read_only_and_missing_new_metadata_is_not_backfilled():
    run = {
        "model_key": "live-model", "session": "2026-09-23", "audit_status": "ok",
        "snapshot": {"signals": [{"symbol": "2330.TWSE", "reference_close": 100.0}]},
    }

    projection = LiveLedger.recommendation_projection(run)

    assert projection["recommendation_status"] == "formal_available"
    assert projection["candidate_count"] == 1
    assert "name" not in run["snapshot"]["signals"][0]
    assert "reason_summary" not in run["snapshot"]["signals"][0]


@pytest.fixture
def ledger(tmp_path):
    clock = [datetime(2026, 9, 21, 17, tzinfo=TAIPEI_TZ)]
    cal = TaiwanTradingCalendar(known_trading_days={date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22)})
    store = LiveLedger(tmp_path, clock=lambda: clock[0], evidence=cal.day_evidence)
    return store, clock


def batch(model, day, value=1):
    return LiveSignalBatch(model, day, {
        "contract": "current_live_verified_v1", "signal_session": day,
        "data_cutoff": datetime.combine(day, datetime.min.time(), tzinfo=TAIPEI_TZ) + timedelta(hours=17),
        "model": model.describe(), "usage_scope": "experimental_live", "validation_state": "unvalidated",
        "eligible_universe": [], "features": [], "feature_snapshot_hash": canonical_hash([]),
        "signals": [], "test_value": value,
    })


def test_immutable_retry_conflict_and_no_arbitrary_backfill(ledger):
    store, clock = ledger
    model = LiveModel()
    meta = store.activate(model)
    assert meta["first_live_session"] == "2026-09-21"
    draft = batch(model, date(2026, 9, 21))
    assert store.freeze(draft)["status"] == "frozen"
    clock[0] += timedelta(hours=15)  # next day before publication, legal retry
    assert store.freeze(draft)["status"] == "noop"
    with pytest.raises(LiveConflictError):
        store.freeze(batch(model, date(2026, 9, 21), 2))
    assert store.read_run(model.key, "2026-09-21")["snapshot"]["test_value"] == 1
    with pytest.raises(ValueError, match=r"latest|first"):
        store.freeze(batch(model, date(2026, 9, 18)))
    with pytest.raises(TypeError):
        store.freeze({"origin": "live", "oos_prediction": []})


def test_outcome_projection_keeps_frozen_signal_metadata_for_pending_rows(ledger):
    store, _ = ledger
    signal = {
        "symbol": "2330.TWSE", "name": "快照名稱", "reference_close": 100.0,
        "rank": 1, "score": 0.9, "reason_summary": "進入既有 Top 10。",
    }

    outcomes = store.signal_outcomes("live-model", "2026-09-21", [signal])

    assert outcomes[0]["name"] == "快照名稱"
    assert outcomes[0]["reference_close"] == 100.0
    assert outcomes[0]["rank"] == 1
    assert outcomes[0]["reason_summary"] == "進入既有 Top 10。"


def test_new_version_cannot_retroactively_start(ledger):
    store, clock = ledger
    store.activate(LiveModel())
    clock[0] = datetime(2026, 9, 22, 17, tzinfo=TAIPEI_TZ)
    model = LiveModel(version="v2")
    store.activate(model)
    with pytest.raises(ValueError, match=r"latest|first"):
        store.freeze(batch(model, date(2026, 9, 21)))


def test_session_network_evidence_runs_outside_sqlite_write_lock(ledger):
    store, _ = ledger
    calendar_reader = store.evidence
    def evidence(day, exchange):
        with sqlite3.connect(store.root / "signals.sqlite3", timeout=0.1) as db:
            db.execute("BEGIN IMMEDIATE")
        return calendar_reader(day, exchange)
    store.evidence = evidence
    assert store.activate(LiveModel())["first_live_session"] == "2026-09-21"
