"""Live events are not a backtest import surface."""
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


def test_new_version_cannot_retroactively_start(ledger):
    store, clock = ledger
    store.activate(LiveModel())
    clock[0] = datetime(2026, 9, 22, 17, tzinfo=TAIPEI_TZ)
    model = LiveModel(version="v2")
    store.activate(model)
    with pytest.raises(ValueError, match=r"latest|first"):
        store.freeze(batch(model, date(2026, 9, 21)))
