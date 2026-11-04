"""Custom-provider daily-batch keeps stock and ETF caches separate."""
import datetime as _dt

import polars as pl
import pytest

from app.tickflow.repository import DataStore, KlineRepository


@pytest.fixture()
def repo(tmp_path):
    return KlineRepository(DataStore(tmp_path))


def test_custom_daily_batch_groups_taiwan_stock_and_etf_symbols(repo, monkeypatch):
    from app.api import kline as kline_api

    # Official history has its own canonical batch contract; this covers custom caches.
    monkeypatch.setattr("app.services.preferences.get_daily_data_provider", lambda: "custom:fixture")
    calls = {"stock_batch": [], "etf": []}

    def fake_stock_batch(symbols, start, end, columns=None):
        calls["stock_batch"].append(list(symbols))
        return pl.DataFrame()

    def fake_etf_daily(symbol, start, end, columns=None):
        calls["etf"].append(symbol)
        return pl.DataFrame({
            "symbol": [symbol], "date": [_dt.date(2026, 7, 24)],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1],
        })

    monkeypatch.setattr(repo, "get_daily_batch", fake_stock_batch)
    monkeypatch.setattr(repo, "get_etf_daily", fake_etf_daily)
    monkeypatch.setattr(repo, "get_index_symbol_set", lambda: set())
    monkeypatch.setattr(repo, "get_etf_symbol_set", lambda: {"0050.TWSE"})

    state = type("S", (), {"repo": repo})()
    req = type("R", (), {"app": type("A", (), {"state": state})()})()

    out = kline_api.get_daily_batch(req, {"symbols": ["2330.TWSE", "0050.TWSE"], "days": 12})
    assert calls["stock_batch"] == [["2330.TWSE"]]
    assert calls["etf"] == ["0050.TWSE"]
    assert out["data"]["0050.TWSE"][0]["close"] == 1.0
    assert "2330.TWSE" not in out["data"]
