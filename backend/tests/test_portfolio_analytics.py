from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.portfolio_analytics import BENCHMARK_SYMBOL, PortfolioAnalyticsService, series_metrics


def test_series_metrics_fixed_sample():
    m = series_metrics([100, 110, 99, 121], [100, 105, 100, 110])
    assert m["total_return"] == pytest.approx(0.21)
    assert m["benchmark_return"] == pytest.approx(0.10)
    assert m["max_drawdown"] == pytest.approx(-0.10)  # 110 -> 99
    assert m["beta"] > 1  # portfolio swings more than benchmark in the same direction


def test_series_metrics_flat_benchmark_has_no_beta():
    assert series_metrics([100, 101, 102], [100, 100, 100])["beta"] is None


class _Actions:
    def __init__(self, coverage):
        self.coverage = coverage

    def read_verified_coverage(self):
        return self.coverage


_MASTER = SimpleNamespace(get_instrument=lambda s: SimpleNamespace(
    instrument_type="etf" if s == BENCHMARK_SYMBOL else "stock",
    industry={"2330.TWSE": "半導體業", "2317.TWSE": "其他電子業"}.get(s)))


def _store(tmp_path, days=30, missing=None):
    store = TaiwanDailyStore(data_dir=tmp_path)
    start = date(2026, 6, 1)
    for i in range(days):
        d = start + timedelta(days=i)
        rows = [("2330.TWSE", 1000.0 + i), ("2317.TWSE", 200.0), (BENCHMARK_SYMBOL, 150 + i * 0.5)]
        rows = [r for r in rows if not (missing and r[0] == missing[0] and i == missing[1])]
        store.write_batch(pl.DataFrame({
            "symbol": [r[0] for r in rows], "date": [d] * len(rows),
            "open": [r[1] for r in rows], "high": [r[1] for r in rows], "low": [r[1] for r in rows],
            "close": [float(r[1]) for r in rows], "volume": [1e6] * len(rows),
            "amount": [1e8] * len(rows), "quote_ts": [0] * len(rows),
        }), partition_date=d)
    return store


def test_analyze_performance_and_allocation(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path),
                                    actions=_Actions((date(2026, 1, 1), date(2026, 12, 31), ())),
                                    master=_MASTER)
    result = svc.analyze({"2330.TWSE": 1000, "2317.TWSE": 1000})
    perf = result["performance"]
    assert perf["status"] == "available" and perf["sessions"] == 30
    assert perf["series"][0]["portfolio"] == pytest.approx(100)
    assert perf["metrics"]["max_drawdown"] == 0
    alloc = result["allocation"]
    assert alloc["status"] == "available"
    weights = {s["industry"]: s["weight"] for s in alloc["sectors"]}
    assert weights["半導體業"] == pytest.approx(1029 / 1229)


def test_missing_session_is_dropped_not_filled(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path, missing=("2330.TWSE", 5)),
                                    actions=_Actions((date(2026, 1, 1), date(2026, 12, 31), ())),
                                    master=_MASTER)
    assert svc.analyze({"2330.TWSE": 1})["performance"]["sessions"] == 29


def test_unverified_actions_fail_closed(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path), actions=_Actions(None), master=_MASTER)
    result = svc.analyze({"2330.TWSE": 1})
    assert result["performance"]["status"] == "unavailable"
    assert result["performance"]["reason"] == "corporate_action_coverage_unavailable"
    assert result["allocation"]["status"] == "available"  # allocation uses latest raw close only


def test_unknown_symbol_is_unpriced_and_blocks_performance(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path),
                                    actions=_Actions((date(2026, 1, 1), date(2026, 12, 31), ())),
                                    master=_MASTER)
    result = svc.analyze({"2330.TWSE": 1, "9999.TWSE": 1})
    assert result["allocation"]["status"] == "partial"
    assert result["allocation"]["unpriced_symbols"] == ["9999.TWSE"]
    assert result["performance"]["reason"] == "symbol_data_insufficient"
