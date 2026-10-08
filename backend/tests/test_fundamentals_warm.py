from types import SimpleNamespace

from app.taiwan import fundamentals_warm


class _FakeService:
    def __init__(self):
        self.calls = []

    def _result(self, name, symbol, status="available"):
        self.calls.append((name, symbol))
        if symbol == "BAD.TWSE" and name == "revenue":
            raise RuntimeError("rate limited")
        return SimpleNamespace(meta=SimpleNamespace(status=status))

    def get_valuation(self, symbol, exchange):
        assert exchange == symbol.rsplit(".", 1)[-1]
        return self._result("valuation", symbol)

    def get_monthly_revenue(self, symbol):
        return self._result("revenue", symbol)

    def get_financial_statements(self, symbol):
        return self._result("financials", symbol, status="unavailable")

    def get_foreign_shareholding(self, symbol):
        return self._result("shareholding", symbol)

    def get_securities_lending(self, symbol):
        return self._result("lending", symbol)


def test_warm_counts_available_and_survives_one_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    fake = _FakeService()
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake)

    result = fundamentals_warm.warm_fundamentals(["2330.TWSE", "BAD.TWSE", "6488.TPEX"])

    assert result["symbols"] == 3 and result["failed"] == 1
    assert result["available"] == {"valuation": 3, "revenue": 2, "financials": 0,
                                   "shareholding": 3, "lending": 3}
    # the failing dataset does not skip the rest of that symbol or later symbols
    assert ("lending", "BAD.TWSE") in fake.calls and ("valuation", "6488.TPEX") in fake.calls


def test_second_warm_is_refused_while_one_holds_the_lock(monkeypatch, tmp_path):
    from app.taiwan.backfill_worker import WorkerLock

    lock_path = tmp_path / "warm.lock"
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: lock_path)
    fake = _FakeService()
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake)

    holder = WorkerLock(lock_path)
    holder.acquire()
    try:
        assert fundamentals_warm.warm_fundamentals(["2330.TWSE"])["status"] == "busy"
        assert fake.calls == []  # no FinMind quota spent by the refused run
    finally:
        holder.release()
    assert fundamentals_warm.warm_fundamentals(["2330.TWSE"])["status"] == "ok"
