from pathlib import Path
from types import SimpleNamespace

from app.taiwan import fundamentals_warm


class _FakeService:
    def __init__(self):
        import tempfile

        from app.taiwan.finmind_cache import FinMindCache

        self.calls = []
        self.cache = FinMindCache(Path(tempfile.mkdtemp()))

    def prime_valuation_cache(self, exchange):
        return 0

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


def _service_with_cache(tmp_path, revenue_status="available"):
    from datetime import UTC, datetime, timedelta

    from app.taiwan.finmind_cache import FinMindCache

    cache = FinMindCache(tmp_path / "cache")
    fake = _FakeService()
    fake.cache = cache
    fake.valuation_primed = []
    fake.prime_valuation_cache = lambda exchange: fake.valuation_primed.append(exchange) or 0

    def revenue(symbol):
        fake.calls.append(("revenue", symbol))
        if revenue_status == "available":
            cache.set("TaiwanStockMonthRevenue", symbol, [{"date": "2026-09-01"}], data_date="2026-09-01")
        return SimpleNamespace(meta=SimpleNamespace(status=revenue_status))

    fake.get_monthly_revenue = revenue
    old = datetime.now(UTC) - timedelta(hours=30)
    return fake, cache, old


def _age(cache, dataset, symbol, fetched_at):
    import json as _json

    path = cache._file_path(dataset, symbol)
    raw = _json.loads(path.read_text(encoding="utf-8"))
    raw["fetched_at"] = fetched_at.isoformat()
    path.write_text(_json.dumps(raw), encoding="utf-8")


def test_warm_refetches_daily_cache_older_than_a_day_and_primes_valuation_once(monkeypatch, tmp_path):
    fake, cache, old = _service_with_cache(tmp_path)
    cache.set("TaiwanStockMonthRevenue", "2330.TWSE", [{"date": "2026-08-01"}], data_date="2026-08-01")
    _age(cache, "TaiwanStockMonthRevenue", "2330.TWSE", old)
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake)

    fundamentals_warm.warm_fundamentals(["2330.TWSE", "2317.TWSE"])

    assert fake.valuation_primed == ["TWSE", "TPEX"]  # one snapshot per exchange, not per symbol
    assert ("revenue", "2330.TWSE") in fake.calls
    assert cache.get("TaiwanStockMonthRevenue", "2330.TWSE")["data_date"] == "2026-09-01"


def test_failed_refetch_keeps_last_good_copy_with_its_real_date(monkeypatch, tmp_path):
    fake, cache, old = _service_with_cache(tmp_path, revenue_status="unavailable")
    cache.set("TaiwanStockMonthRevenue", "2330.TWSE", [{"date": "2026-08-01"}], data_date="2026-08-01")
    _age(cache, "TaiwanStockMonthRevenue", "2330.TWSE", old)
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake)

    fundamentals_warm.warm_fundamentals(["2330.TWSE"])

    kept = cache.get("TaiwanStockMonthRevenue", "2330.TWSE")
    assert kept is not None and kept["data_date"] == "2026-08-01"  # not relabelled as fresh data


def test_warm_lock_reclaims_a_dead_owner_before_the_next_daily_run():
    assert fundamentals_warm.LOCK_MAX_AGE.total_seconds() < 24 * 3600
