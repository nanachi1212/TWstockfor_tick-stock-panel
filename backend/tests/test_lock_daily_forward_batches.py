from types import SimpleNamespace

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
