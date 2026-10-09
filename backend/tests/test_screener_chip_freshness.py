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
