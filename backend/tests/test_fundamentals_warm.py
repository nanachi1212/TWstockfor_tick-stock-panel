from pathlib import Path
from types import SimpleNamespace

import pytest

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
        dataset = fundamentals_warm.DAILY_DATASETS.get(name)
        if dataset and status == "available" and self.cache.get(dataset, symbol) is None:
            self.cache.set(dataset, symbol, [{"date": "2026-09-01"}], data_date="2026-09-01")
        return SimpleNamespace(meta=SimpleNamespace(status=status))

    def get_valuation(self, symbol, exchange):
        assert exchange == symbol.rsplit(".", 1)[-1]
        return self._result("valuation", symbol)

    def get_monthly_revenue(self, symbol):
        return self._result("revenue", symbol)

    def get_financial_statements(self, symbol):
        return self._result("financials", symbol)

    def get_foreign_shareholding(self, symbol):
        return self._result("shareholding", symbol)

    def get_securities_lending(self, symbol):
        return self._result("lending", symbol)


_METHODS = {
    "valuation": "get_valuation",
    "revenue": "get_monthly_revenue",
    "financials": "get_financial_statements",
    "shareholding": "get_foreign_shareholding",
    "lending": "get_securities_lending",
}


def test_warm_counts_available_and_survives_one_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    fake = _FakeService()
    fake.get_financial_statements = lambda symbol: fake._result("financials", symbol, "unavailable")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake)

    result = fundamentals_warm.warm_fundamentals(["2330.TWSE", "BAD.TWSE", "6488.TPEX"])

    assert result["symbols"] == 3 and result["failed"] == 1
    assert result["available"] == {"valuation": 3, "revenue": 2, "financials": 0,
                                   "shareholding": 3, "lending": 3}
    # the failing dataset does not skip the rest of that symbol or later symbols
    assert ("lending", "BAD.TWSE") in fake.calls and ("valuation", "6488.TPEX") in fake.calls
    assert result["status"] == "partial"
    assert result["unavailable"]["financials"] == 3
    assert not (tmp_path / "fundamentals_warm.last").exists()


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

    class CachedService(_FakeService):
        def get_monthly_revenue(self, symbol):
            return self._result("revenue", symbol, revenue_status)

    fake = CachedService()
    fake.cache = cache
    fake.valuation_primed = []
    fake.prime_valuation_cache = lambda exchange: fake.valuation_primed.append(exchange) or 0
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


def test_lock_left_by_a_dead_process_is_reclaimed_immediately(monkeypatch, tmp_path):
    import json as _json

    lock_path = tmp_path / "warm.lock"
    lock_path.write_text(_json.dumps({"pid": 2_000_000_000, "token": "dead",
                                      "process_created_at": 1.0}), encoding="utf-8")
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: lock_path)
    fake = _FakeService()
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake)

    assert fundamentals_warm.warm_fundamentals(["2330.TWSE"])["status"] == "ok"
    assert not lock_path.exists()  # released after the run


@pytest.mark.parametrize("outcome", ["available", "unavailable", "raises"])
def test_refresh_keeps_readable_old_cache_until_validated_replacement(monkeypatch, tmp_path, outcome):
    from datetime import UTC, datetime, timedelta

    from app.taiwan.finmind_cache import FinMindCache

    original = FinMindCache(tmp_path / "cache")
    dataset, symbol = "TaiwanStockMonthRevenue", "2330.TWSE"
    original.set(dataset, symbol, [{"date": "2026-08-01"}], data_date="2026-08-01")
    _age(original, dataset, symbol, datetime.now(UTC) - timedelta(hours=30))
    path = original._file_path(dataset, symbol)
    before = path.read_bytes()
    reads_during_refresh = []

    class RefreshingService(_FakeService):
        def get_monthly_revenue(self, symbol):
            reads_during_refresh.append(path.exists() and path.read_bytes() == before)
            self.cache.set(dataset, symbol, [{"date": "2026-09-01"}],
                           data_date="2026-09-01", status=outcome)
            if outcome == "raises":
                raise RuntimeError("fixture fetch failed")
            return SimpleNamespace(meta=SimpleNamespace(status=outcome))

    fake = RefreshingService()
    fake.cache = original
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake,
    )
    result = fundamentals_warm.warm_fundamentals([symbol])

    assert reads_during_refresh == [True]
    if outcome == "available":
        assert original.get(dataset, symbol)["data_date"] == "2026-09-01"
    else:
        assert path.read_bytes() == before
        assert result["status"] != "ok"
        assert not (tmp_path / "fundamentals_warm.last").exists()


@pytest.mark.parametrize("mode", ["unavailable", "raises"])
def test_unsuccessful_warm_does_not_mark_startup_catchup_current(monkeypatch, tmp_path, mode):
    from app.taiwan.startup_catchup import needs_fundamentals_catchup

    class UnavailableService(_FakeService):
        def _result(self, name, symbol, status="available"):
            if mode == "raises":
                raise RuntimeError("fixture outage")
            return SimpleNamespace(meta=SimpleNamespace(status="unavailable"))

    fake = UnavailableService()
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake,
    )
    result = fundamentals_warm.warm_fundamentals(["2330.TWSE"])

    assert result["status"] == "error"
    assert result["failed"] == (5 if mode == "raises" else 0)
    assert sum(result["unavailable"].values()) == (0 if mode == "raises" else 5)
    assert needs_fundamentals_catchup(tmp_path / "fundamentals_warm.last")[0] is True
    assert not (tmp_path / "warm.lock").exists()


@pytest.mark.parametrize("name,status,reason", [
    ("financials", "not_applicable", None),
    ("financials", "unsupported", None),
    ("financials", "unavailable", "FinMind provider disabled"),
    ("lending", "unavailable", "securities lending rows contain no positive transaction volume"),
    # Successful empty responses: TDRs, stocks with no lending trades (2026-11-04 warm).
    ("financials", "unavailable", "TaiwanStockFinancialStatements returned no rows"),
    ("lending", "unavailable", "TaiwanStockSecuritiesLending returned no rows"),
    ("revenue", "unavailable", "TaiwanStockMonthRevenue returned no rows"),
    ("valuation", "unavailable", "official exchangeReport/BWIBBU_ALL row not found for 9103.TWSE"),
])
def test_explicit_non_applicable_sections_do_not_block_completion(monkeypatch, tmp_path, name, status, reason):
    fake = _FakeService()
    method = _METHODS[name]
    setattr(fake, method, lambda *args: SimpleNamespace(meta=SimpleNamespace(
        status=status, fallback_reason=reason,
    )))
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake,
    )
    result = fundamentals_warm.warm_fundamentals(["2330.TWSE"])

    assert result["status"] == "ok"
    assert result["available"][name] == 0
    assert result["skipped"][name] == 1
    assert sum(result["unavailable"].values()) == 0
    assert (tmp_path / "fundamentals_warm.last").exists()


@pytest.mark.parametrize("name,reason", [
    ("lending", "TaiwanStockSecuritiesLending request failed: FinMind API 已達請求頻率上限"),
    ("revenue", "TaiwanStockMonthRevenue request failed: HTTP 錯誤 402"),
    ("revenue", "TaiwanStockMonthRevenue cached response is unavailable"),
    ("valuation", "ConnectError"),
])
def test_request_failures_still_block_completion(monkeypatch, tmp_path, name, reason):
    fake = _FakeService()
    setattr(fake, _METHODS[name], lambda *args: SimpleNamespace(meta=SimpleNamespace(
        status="unavailable", fallback_reason=reason,
    )))
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake,
    )
    result = fundamentals_warm.warm_fundamentals(["2330.TWSE"])

    assert result["status"] == "partial"
    assert result["unavailable"][name] == 1
    assert result["skipped"][name] == 0
    assert not (tmp_path / "fundamentals_warm.last").exists()


def test_monthly_revenue_separates_request_failure_from_empty_response(tmp_path):
    from app.taiwan.finmind_cache import FinMindCache
    from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService
    from app.taiwan.providers.finmind_provider import FinMindError

    def build(fetch):
        svc = TaiwanFundamentalChipsService(
            cache=FinMindCache(tmp_path / fetch.__name__),
            finmind_adapter=SimpleNamespace(fetch_month_revenue=fetch),
            official_fundamentals=SimpleNamespace(),
        )
        svc._is_finmind_enabled = lambda: True
        return svc

    def empty(*args, **kwargs):
        assert kwargs["raise_for_status"] is True
        return []

    def quota(*args, **kwargs):
        raise FinMindError("HTTP 錯誤 402")

    empty_svc, quota_svc = build(empty), build(quota)
    assert empty_svc.get_monthly_revenue("9103.TWSE").meta.fallback_reason == (
        "TaiwanStockMonthRevenue returned no rows")
    assert quota_svc.get_monthly_revenue("9103.TWSE").meta.fallback_reason.startswith(
        "TaiwanStockMonthRevenue request failed")
    # The negative cache keeps the same distinction for the next read.
    assert empty_svc.get_monthly_revenue("9103.TWSE").meta.fallback_reason.endswith("returned no rows")
    assert quota_svc.cache.get("TaiwanStockMonthRevenue", "9103.TWSE")["status"] == "error"


@pytest.mark.parametrize("fresh,publish_fails", [(True, False), (False, False), (False, True)])
def test_refresh_uses_real_service_cache_contract(monkeypatch, tmp_path, fresh, publish_fails):
    from datetime import UTC, datetime, timedelta

    from app.taiwan.finmind_cache import FinMindCache
    from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService

    cache = FinMindCache(tmp_path / "cache")
    dataset, symbol = "TaiwanStockMonthRevenue", "2330.TWSE"
    old_row = {"date": "2026-08-10", "revenue_year": 2026, "revenue_month": 7, "revenue": 80}
    new_row = {"date": "2026-10-10", "revenue_year": 2026, "revenue_month": 9, "revenue": 100}
    cache.set(dataset, symbol, [old_row], data_date=old_row["date"])
    _age(cache, dataset, symbol, datetime.now(UTC) - timedelta(hours=1 if fresh else 30))
    path = cache._file_path(dataset, symbol)
    before = path.read_bytes()
    calls = []

    def fetch_revenue(*args, **kwargs):
        assert path.read_bytes() == before
        calls.append(args)
        return [new_row]

    service = TaiwanFundamentalChipsService(
        cache=cache, finmind_adapter=SimpleNamespace(fetch_month_revenue=fetch_revenue),
        official_fundamentals=SimpleNamespace(),
    )
    monkeypatch.setattr(service, "_is_finmind_enabled", lambda: True)
    if publish_fails:
        replace = Path.replace

        def fail_publication(source, target):
            if target == path:
                raise PermissionError("fixture publish denied")
            return replace(source, target)

        monkeypatch.setattr(Path, "replace", fail_publication)
        with pytest.raises(PermissionError):
            fundamentals_warm._refresh(service, dataset, symbol, lambda svc: svc.get_monthly_revenue(symbol))
        assert path.read_bytes() == before
    else:
        result = fundamentals_warm._refresh(
            service, dataset, symbol, lambda svc: svc.get_monthly_revenue(symbol),
        )
        assert result.meta.status == "available"
        assert result.latest_revenue == (80 if fresh else 100)
        assert cache.get(dataset, symbol)["data_date"] == (old_row if fresh else new_row)["date"]
    assert len(calls) == (0 if fresh else 1)
    assert service.cache is cache
    assert not list(path.parent.glob(".warm-*"))


def test_partial_warm_preserves_old_completion_marker(monkeypatch, tmp_path):
    import os
    import time

    from app.taiwan.startup_catchup import needs_fundamentals_catchup

    fake = _FakeService()
    fake.get_financial_statements = lambda symbol: SimpleNamespace(meta=None)
    marker = tmp_path / "fundamentals_warm.last"
    marker.touch()
    old_time = time.time() - 30 * 3600
    os.utime(marker, (old_time, old_time))
    before = marker.stat().st_mtime_ns
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake,
    )
    result = fundamentals_warm.warm_fundamentals(["2330.TWSE"])

    assert result["status"] == "partial"
    assert marker.stat().st_mtime_ns == before
    assert needs_fundamentals_catchup(marker)[0] is True


def test_empty_universe_does_not_write_success_marker(monkeypatch, tmp_path):
    from unittest.mock import Mock

    fake = _FakeService()
    fake.prime_valuation_cache = Mock()
    monkeypatch.setattr(fundamentals_warm, "_lock_path", lambda: tmp_path / "warm.lock")
    monkeypatch.setattr(
        "app.taiwan.fundamental_chips_service.get_fundamental_chips_service", lambda: fake,
    )
    result = fundamentals_warm.warm_fundamentals([])

    assert result["status"] == "skipped"
    assert not (tmp_path / "fundamentals_warm.last").exists()
    fake.prime_valuation_cache.assert_not_called()
