from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.portfolio_analytics import (
    BENCHMARK_SYMBOL,
    PortfolioAnalyticsService,
    series_metrics,
)


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
    assert perf["reason"] is None and perf["excluded_symbols"] == []
    assert perf["metrics"]["benchmark_return"] == pytest.approx(164.5 / 150 - 1)
    assert perf["metrics"]["benchmark_volatility"] > 0
    assert perf["metrics"]["beta"] is not None
    assert perf["series"][0] == {"date": "2026-06-01", "portfolio": pytest.approx(100), "benchmark": pytest.approx(100)}
    assert perf["metrics"]["max_drawdown"] == 0
    alloc = result["allocation"]
    assert alloc["status"] == "available"
    weights = {s["industry"]: s["weight"] for s in alloc["sectors"]}
    assert weights["半導體業"] == pytest.approx(1029 / 1229)


def test_missing_session_blocks_metrics_instead_of_disappearing(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path, missing=("2330.TWSE", 5)),
                                    actions=_Actions((date(2026, 1, 1), date(2026, 12, 31), ())),
                                    master=_MASTER)
    perf = svc.analyze({"2330.TWSE": 1, "2317.TWSE": 1})["performance"]
    assert perf["status"] == "unavailable"
    assert perf["metrics"] is None and perf["series"] == []
    assert perf["missing_sessions"] == [{"symbol": "2330.TWSE", "date": "2026-06-06"}]


def test_benchmark_suspension_gap_fails_closed_like_a_holding_gap(tmp_path):
    # ETF splits are not in any official event table; the 2025 0050 split showed up only as
    # a five-session trading halt, so a benchmark bar gap must not be spanned by a daily return.
    svc = PortfolioAnalyticsService(daily=_store(tmp_path, missing=(BENCHMARK_SYMBOL, 5)),
                                    actions=_Actions((date(2026, 1, 1), date(2026, 12, 31), ())),
                                    master=_MASTER)
    perf = svc.analyze({"2330.TWSE": 1, "2317.TWSE": 1})["performance"]
    assert perf["status"] == "unavailable" and perf["reason"] == "incomplete_sessions"
    assert perf["metrics"] is None and perf["series"] == []
    assert perf["missing_sessions"] == [{"symbol": BENCHMARK_SYMBOL, "date": "2026-06-06"}]


def test_benchmark_without_price_history_keeps_stock_metrics_partial(tmp_path):
    daily = _store(tmp_path)
    read = daily.read_range
    daily.read_range = lambda *args: read(*args).filter(pl.col("symbol") != BENCHMARK_SYMBOL)
    svc = PortfolioAnalyticsService(daily=daily, master=_MASTER,
                                    actions=_Actions((date(2026, 1, 1), date(2026, 12, 31), ())))
    perf = svc.analyze({"2330.TWSE": 1})["performance"]
    assert perf["status"] == "partial" and perf["reason"] == "benchmark_unavailable"
    assert perf["excluded_symbols"] == [{"symbol": BENCHMARK_SYMBOL, "reason": "no_price_history"}]
    assert perf["metrics"]["total_return"] == pytest.approx(1029 / 1000 - 1)
    assert perf["metrics"]["benchmark_return"] is None and perf["metrics"]["beta"] is None
    assert all(p["benchmark"] is None for p in perf["series"])


def test_missing_entire_weekday_is_not_treated_as_holiday(tmp_path):
    daily = _store(tmp_path)
    read = daily.read_range
    day = date(2026, 6, 8)
    daily.read_range = lambda *args: read(*args).filter(pl.col("date") != day)
    svc = PortfolioAnalyticsService(daily=daily, master=_MASTER,
                                    actions=_Actions((date(2026, 6, 1), date(2026, 6, 30), ())))
    perf = svc.analyze({"2330.TWSE": 1})["performance"]
    assert perf["reason"] == "incomplete_sessions" and perf["metrics"] is None
    assert perf["missing_sessions"] == [{"symbol": "2330.TWSE", "date": "2026-06-08"},
                                        {"symbol": BENCHMARK_SYMBOL, "date": "2026-06-08"}]
    svc.evidence = SimpleNamespace(day_evidence=lambda *args: SimpleNamespace(status="non_trading"))
    assert svc.analyze({"2330.TWSE": 1})["performance"]["status"] == "available"


def test_missing_crash_bar_cannot_hide_drawdown(tmp_path):
    daily = _store(tmp_path)
    read = daily.read_range
    crash = date(2026, 6, 8)
    daily.read_range = lambda *args: read(*args).with_columns(
        pl.when((pl.col("symbol") == "2330.TWSE") & (pl.col("date") == crash))
        .then(50.0).otherwise(pl.col("close")).alias("close"))
    svc = PortfolioAnalyticsService(daily=daily, master=_MASTER,
                                    actions=_Actions((date(2026, 6, 1), date(2026, 6, 30), ())))
    assert svc.analyze({"2330.TWSE": 1})["performance"]["metrics"]["max_drawdown"] < -0.9
    daily.read_range = lambda *args: read(*args).filter(
        ~((pl.col("symbol") == "2330.TWSE") & (pl.col("date") == crash)))
    assert svc.analyze({"2330.TWSE": 1})["performance"]["metrics"] is None


def test_etf_holdings_need_distribution_coverage_even_outside_january_and_july(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path), master=_MASTER,
                                    actions=_Actions((date(2026, 6, 1), date(2026, 6, 30), ())))
    result = svc.analyze({BENCHMARK_SYMBOL: 1})
    assert result["performance"]["reason"] == "asset_action_coverage_unavailable"
    assert result["performance"]["metrics"] is None
    assert result["allocation"]["status"] == "available"


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 0, -1])
def test_metrics_reject_invalid_prices(price):
    with pytest.raises(ValueError):
        series_metrics([100, price], None)


def test_overflowed_portfolio_value_is_unavailable(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path), master=_MASTER,
                                    actions=_Actions((date(2026, 6, 1), date(2026, 6, 30), ())))
    perf = svc.analyze({"2330.TWSE": 1e308})["performance"]
    assert perf["reason"] == "invalid_portfolio_value" and perf["metrics"] is None


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


def test_concentration_uses_market_value_and_keeps_etfs_out_of_industry_summary():
    rows = pl.DataFrame({
        "symbol": ["2330.TWSE", "2317.TWSE", "2454.TWSE", BENCHMARK_SYMBOL],
        "close": [1000.0, 200.0, 800.0, 100.0],
        "date": [date(2026, 10, 14)] * 4,
    })
    master = SimpleNamespace(get_instrument=lambda s: SimpleNamespace(
        instrument_type="etf" if s == BENCHMARK_SYMBOL else "stock",
        industry={"2330.TWSE": "半導體業", "2454.TWSE": "半導體業", "2317.TWSE": "其他電子業"}.get(s)))
    svc = PortfolioAnalyticsService(daily=SimpleNamespace(
        read_latest_per_symbol=lambda symbols: rows, latest_date=lambda: date(2026, 10, 14)),
        actions=_Actions(None), master=master)
    allocation = svc._allocation({"2330.TWSE": 1, "2317.TWSE": 1, "2454.TWSE": 1, BENCHMARK_SYMBOL: 30})
    assert allocation["total_market_value"] == 5000
    positions = {p["symbol"]: p["weight"] for p in allocation["positions"]}
    assert positions == pytest.approx({"2330.TWSE": 0.2, "2317.TWSE": 0.04, "2454.TWSE": 0.16, BENCHMARK_SYMBOL: 0.6})
    summary = allocation["concentration"]
    assert summary["largest_position"]["symbol"] == BENCHMARK_SYMBOL
    assert summary["top3_weight"] == pytest.approx(0.96)
    assert summary["largest_industry"]["industry"] == "半導體業"
    assert summary["largest_industry"]["weight"] == pytest.approx(0.36)


@pytest.mark.parametrize("holdings, missing", [
    ({"2330.TWSE": 1, "9999.TWSE": 1}, None),
    ({"2330.TWSE": 1, "2317.TWSE": 1}, ("2317.TWSE", 29)),
    ({"2317.TWSE": 1}, ("2317.TWSE", 29)),
    ({}, None),
])
def test_incomplete_allocation_never_renormalizes_subset(tmp_path, holdings, missing):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path, missing=missing), actions=_Actions(None), master=_MASTER)
    allocation = svc._allocation(holdings)
    assert allocation["status"] != "available"
    assert allocation["positions"] == []
    assert allocation["sectors"] == []
    assert allocation["total_market_value"] is None
    assert allocation["concentration"] is None
    if missing:
        assert allocation["as_of"] == "2026-06-30"
        assert allocation["stale_symbols"] == ["2317.TWSE"]
        assert allocation["unpriced_symbols"] == []
    elif holdings:
        assert allocation["unpriced_symbols"] == ["9999.TWSE"]


@pytest.mark.parametrize("close", [None, 0, -1, float("nan"), float("inf")])
def test_invalid_close_blocks_concentration(close):
    rows = pl.DataFrame({"symbol": ["2330.TWSE"], "close": [close], "date": [date(2026, 10, 14)]})
    svc = PortfolioAnalyticsService(daily=SimpleNamespace(
        read_latest_per_symbol=lambda symbols: rows, latest_date=lambda: date(2026, 10, 14)),
        actions=_Actions(None), master=_MASTER)
    allocation = svc._allocation({"2330.TWSE": 1})
    assert allocation["status"] == "unavailable"
    assert allocation["concentration"] is None
    assert allocation["unpriced_symbols"] == ["2330.TWSE"]


def test_unknown_industry_and_etf_only_do_not_claim_largest_industry(tmp_path):
    svc = PortfolioAnalyticsService(daily=_store(tmp_path), actions=_Actions(None), master=_MASTER)
    assert svc._allocation({BENCHMARK_SYMBOL: 1})["concentration"]["largest_industry"] is None
    svc.master = SimpleNamespace(get_instrument=lambda symbol: None)
    allocation = svc._allocation({"2330.TWSE": 1})
    assert allocation["concentration"]["largest_position"]["weight"] == 1
    assert allocation["concentration"]["largest_industry"] is None
    assert allocation["sectors"][0]["industry"] == "未分類"


def test_analytics_api_combines_duplicate_symbols_and_rejects_invalid_shares(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.taiwan import get_taiwan_portfolio_analytics
    from app.taiwan import portfolio_analytics

    svc = PortfolioAnalyticsService(daily=_store(tmp_path), actions=_Actions(None), master=_MASTER)
    monkeypatch.setattr(portfolio_analytics, "PortfolioAnalyticsService", lambda: svc)
    app = FastAPI()
    app.post("/analytics")(get_taiwan_portfolio_analytics)
    client = TestClient(app)
    response = client.post("/analytics", json={"holdings": [
        {"symbol": "2330.TWSE", "shares": 2}, {"symbol": "2330.TWSE", "shares": 3},
    ]})
    assert response.status_code == 200
    allocation = response.json()["allocation"]
    assert allocation["total_market_value"] == 1029 * 5
    assert allocation["concentration"]["top3_weight"] == 1
    for shares in (0, -1, "Infinity", "NaN"):
        assert client.post("/analytics", json={"holdings": [{"symbol": "2330.TWSE", "shares": shares}]}).status_code == 422
