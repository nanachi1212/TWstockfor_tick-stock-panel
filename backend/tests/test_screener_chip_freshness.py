from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService
from app.taiwan.screener import TaiwanScreenerService


def _rows(last: str) -> list[dict]:
    return [{"date": last, "stock_id": "x", "ForeignInvestmentSharesRatio": 30.0}]


def test_daily_chip_values_older_than_previous_session_are_not_filtered_on(tmp_path):
    cache = FinMindCache(tmp_path)
    for symbol, last in (("2330.TWSE", "2026-10-07"), ("2317.TWSE", "2026-10-06"),
                         ("2454.TWSE", "2026-10-02")):
        cache.set("TaiwanStockShareholding", symbol, _rows(last), data_date=last)

    svc = TaiwanScreenerService.__new__(TaiwanScreenerService)
    svc.cache = cache
    svc._fundamental_chips_service = TaiwanFundamentalChipsService(cache=cache)
    svc.daily_store = SimpleNamespace(
        available_dates=lambda: [date(2026, 10, 2), date(2026, 10, 6), date(2026, 10, 7)])
    symbols = ["2330.TWSE", "2317.TWSE", "2454.TWSE"]

    out, _fund_count, chips_count = svc._join_cached_fundamentals_chips(
        pl.DataFrame({"symbol": symbols}), symbols)

    ratios = dict(zip(out["symbol"], out["foreign_shareholding_ratio"], strict=True))
    assert ratios["2330.TWSE"] is not None  # latest session
    assert ratios["2317.TWSE"] is not None  # one-session publication lag
    assert ratios["2454.TWSE"] is None      # cached but stale: never reused as current
    assert chips_count == 2


def test_live_quant_rank_is_exposed_on_the_0_to_100_scale(tmp_path, monkeypatch):
    from app.taiwan.quant import live_store

    class FakeLedger:
        def runs(self, limit):
            return [{"snapshot": {"signals": [{"symbol": "2330.TWSE", "score": 0.975}]}}]

    monkeypatch.setattr(live_store, "LiveLedger", FakeLedger)
    cache = FinMindCache(tmp_path)
    svc = TaiwanScreenerService.__new__(TaiwanScreenerService)
    svc.cache = cache
    svc._fundamental_chips_service = TaiwanFundamentalChipsService(cache=cache)
    svc.daily_store = SimpleNamespace(available_dates=lambda: [date(2026, 10, 7)])

    out, _, _ = svc._join_cached_fundamentals_chips(
        pl.DataFrame({"symbol": ["2330.TWSE", "2317.TWSE"]}), ["2330.TWSE", "2317.TWSE"])

    scores = dict(zip(out["symbol"], out["quant_score"], strict=True))
    assert scores["2330.TWSE"] == pytest.approx(97.5)  # passes a template quant_score_min of 70
    assert scores["2317.TWSE"] is None


@pytest.mark.parametrize("canonical", [False, True])
def test_join_reads_each_payload_once_and_next_request_observes_changes(tmp_path, monkeypatch, canonical):
    cache = FinMindCache(tmp_path)
    key = "2330.TWSE" if canonical else "2330"
    cache.set("TaiwanValuation", key, {"pe": 12.5, "pb": 2.0, "dividend_yield": 3.0}, data_date="2026-10-07")
    svc = TaiwanScreenerService.__new__(TaiwanScreenerService)
    svc.cache = cache
    svc._fundamental_chips_service = TaiwanFundamentalChipsService(cache=cache)
    svc.daily_store = SimpleNamespace(available_dates=lambda: [date(2026, 10, 7)])
    reads = []
    original_get = cache.get

    def get(dataset, symbol):
        reads.append((dataset, symbol))
        return original_get(dataset, symbol)

    monkeypatch.setattr(cache, "get", get)
    symbols = ["2330.TWSE"]
    frame = pl.DataFrame({"symbol": symbols})
    first, fundamental_count, chips_count = svc._join_cached_fundamentals_chips(frame, symbols)
    assert reads == [("TaiwanValuation", key)]
    assert first.select("pe", "pb", "dividend_yield", "valuation_as_of").to_dicts() == [{
        "pe": 12.5, "pb": 2.0, "dividend_yield": 3.0, "valuation_as_of": "2026-10-07",
    }]
    assert (fundamental_count, chips_count) == (1, 0)

    cache.set("TaiwanValuation", key, {"pe": 18.0}, data_date="2026-10-08")
    second, _, _ = svc._join_cached_fundamentals_chips(frame, symbols)
    assert reads == [("TaiwanValuation", key)] * 2
    assert second["pe"][0] == 18.0 and second["valuation_as_of"][0] == "2026-10-08"
    assert second["pb"][0] is None


def test_cached_payloads_preserve_canonical_priority_and_raw_exchange_ambiguity(tmp_path):
    cache = FinMindCache(tmp_path)
    for key, pe in (("2330", 10), ("2330.TWSE", 20), ("9999", 30), ("2317", 40)):
        cache.set("TaiwanValuation", key, {"pe": pe})
    svc = TaiwanScreenerService.__new__(TaiwanScreenerService)
    svc.cache = cache
    out = svc._cached_symbol_payloads("TaiwanValuation", [
        "2330.TWSE", "9999.TWSE", "9999.TPEX", "2317.TWSE",
    ])
    assert {symbol: payload["data"]["pe"] for symbol, payload in out.items()} == {
        "2330.TWSE": 20, "2317.TWSE": 40,
    }


def test_cached_payloads_recheck_ttl_and_reject_error_entries(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta

    cache = FinMindCache(tmp_path)
    cache.set("TaiwanValuation", "2330.TWSE", {"pe": 20})
    cache.set("TaiwanValuation", "2317.TWSE", {"pe": 10}, status="error")
    svc = TaiwanScreenerService.__new__(TaiwanScreenerService)
    svc.cache = cache
    symbols = ["2330.TWSE", "2317.TWSE"]
    assert set(svc._cached_symbol_payloads("TaiwanValuation", symbols)) == {"2330.TWSE"}
    path = cache._file_path("TaiwanValuation", "2330.TWSE")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fetched_at"] = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert svc._cached_symbol_payloads("TaiwanValuation", symbols) == {}
