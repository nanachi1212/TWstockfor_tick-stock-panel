from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from app.taiwan.selection_review_service import lock_daily_forward_batches
from app.taiwan.selection_v2 import STRATEGY_IDS


def test_locks_every_strategy_and_isolates_failures():
    calls = []

    class FakeService:
        def lock_forward_batch(self, strategy_id):
            calls.append(strategy_id)
            if strategy_id == STRATEGY_IDS[0]:
                raise ValueError("公司行動來源覆蓋不足")
            if strategy_id == STRATEGY_IDS[1]:
                raise RuntimeError("boom")
            return SimpleNamespace(snapshot_id=f"snap_{strategy_id}")

    result = lock_daily_forward_batches(FakeService(), refresh_events=False)

    assert calls == list(STRATEGY_IDS)
    assert result[STRATEGY_IDS[0]] == "skipped: 公司行動來源覆蓋不足"
    assert result[STRATEGY_IDS[1]] == "failed: RuntimeError"
    assert all(result[s] == f"snap_{s}" for s in STRATEGY_IDS[2:])


def test_evening_retry_checks_daily_and_weekly_completion(tmp_path):
    from app.taiwan.realtime.calendar import TaiwanTradingCalendar

    day = date(2026, 11, 13)
    lock = Mock(side_effect=lambda strategy_id: SimpleNamespace(snapshot_id=strategy_id))
    svc = SimpleNamespace(path=tmp_path / "snapshots.json", calendar=TaiwanTradingCalendar(),
                          census_store=None, daily_store=SimpleNamespace(latest_date=lambda: day),
                          lock_forward_batch=lock)
    from app.taiwan.weekly_selection_review import file_sha256

    root = tmp_path / "weekly_selection" / day.strftime("%G-W%V")
    root.mkdir(parents=True)
    for strategy in STRATEGY_IDS[:-2]:
        (root / f"{strategy}.jsonl").write_bytes(b"fixture")
    digest = file_sha256(root / f"{STRATEGY_IDS[0]}.jsonl")
    svc.read_snapshots = lambda: [SimpleNamespace(record_type="forward_batch", source_data_date=str(day),
                                                   strategy_id=s, snapshot_id=s, weekly_records_sha256=digest,
                                                   weekly_capture_error=None)
                                  for s in STRATEGY_IDS[:-1]]
    result = lock_daily_forward_batches(svc, only_missing_for=day, refresh_events=False)
    assert list(result) == list(STRATEGY_IDS)
    assert [c.kwargs["strategy_id"] for c in lock.call_args_list] == list(STRATEGY_IDS[-2:])
