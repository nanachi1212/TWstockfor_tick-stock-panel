"""Canonical Taiwan history stays local, batched, raw and bounded by the requested dates."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import kline
from app.services import kline_sync
from app.taiwan.daily_store import TaiwanDailyStore


@pytest.fixture
def history_client(taiwan_data_env, monkeypatch):
    monkeypatch.setattr("app.services.preferences.get_daily_data_provider", lambda: "taiwan")
    monkeypatch.setattr(kline, "taipei_today", lambda: taiwan_data_env["target"])
    forbidden = Mock(side_effect=AssertionError("chart reads must not fetch or read the legacy repository"))
    monkeypatch.setattr(kline_sync, "sync_daily_batch", forbidden)
    app = FastAPI()
    app.state.repo = SimpleNamespace(
        resolve_asset_type=lambda symbol: "stock",
        get_instruments=lambda: pl.DataFrame({
            "symbol": ["2330.TWSE"], "name": ["台積電"],
            "total_shares": [1000], "float_shares": [800],
        }),
        get_daily_asset=forbidden,
    )
    app.include_router(kline.router)
    return TestClient(app), taiwan_data_env


def test_batch_reads_official_stock_tpex_etf_in_one_read(history_client, monkeypatch):
    client, env = history_client
    calls = []
    original = TaiwanDailyStore.read_range

    def read(self, symbols, start, end):
        calls.append((symbols, start, end))
        return original(self, symbols, start, end)

    monkeypatch.setattr(TaiwanDailyStore, "read_range", read)
    symbols = ["2330.TWSE", "8069.TPEX", "0050.TWSE"]
    response = client.post("/api/kline/daily-batch", json={"symbols": symbols, "days": 12})
    assert response.status_code == 200
    body = response.json()
    assert len(calls) == 1 and set(calls[0][0]) == set(symbols)
    for symbol in symbols:
        assert len(body["data"][symbol]) == 12
        assert body["data"][symbol][-1]["date"] == str(env["target"])
        assert body["meta"][symbol]["price_semantics"] == "raw"
        assert body["meta"][symbol]["volume_unit"] == "shares"
    assert body["data"]["2330.TWSE"][-1]["volume"] == 12_400_000
    assert not (env["data_dir"] / "kline_daily_enriched").exists()


def test_single_uses_trading_sessions_and_preserves_raw_indicator_basis(history_client):
    client, env = history_client
    response = client.get("/api/kline/daily", params={"symbol": "2330.TWSE", "days": 10})
    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 10
    assert body["rows"][0]["date"] == str(env["trading_days"][-10])
    assert body["source"] == "taiwan_daily_store"
    assert body["name"] == "台積電"
    assert body["stock_info"] == {"name": "台積電", "total_shares": 1000, "float_shares": 800}
    assert body["price_semantics"] == "raw"
    assert body["rows"][-1]["ma20"] is not None
    assert body["rows"][-1]["ma60"] is None
    assert body["rows"][-1]["volume"] == 12_400_000


def test_explicit_historical_range_never_injects_today(history_client):
    client, env = history_client
    start, end = env["trading_days"][5], env["trading_days"][12]
    response = client.get("/api/kline/daily", params={
        "symbol": "2330.TWSE", "start_date": str(start), "end_date": str(end), "days": 10,
    })
    body = response.json()
    assert response.status_code == 200
    assert [row["date"] for row in body["rows"]] == [str(d) for d in env["trading_days"][5:13]]
    assert body["as_of"] == str(end)


def test_missing_history_is_explicitly_unavailable(history_client):
    client, _ = history_client
    body = client.get("/api/kline/daily", params={"symbol": "9999.TWSE", "days": 10}).json()
    assert body["rows"] == []
    assert body["status"] == "unavailable" and body["as_of"] is None


def test_store_updates_are_visible_without_a_second_cache(history_client):
    client, env = history_client
    store = TaiwanDailyStore()
    newest = store.read_range(["2330.TWSE"], env["target"], env["target"])
    first = client.post("/api/kline/daily-batch", json={"symbols": ["2330.TWSE"]}).json()
    store.write_batch(newest.with_columns(pl.lit(1234.5).alias("close")))
    second = client.post("/api/kline/daily-batch", json={"symbols": ["2330.TWSE"]}).json()
    assert first["data"]["2330.TWSE"][-1]["close"] != second["data"]["2330.TWSE"][-1]["close"]
    assert second["data"]["2330.TWSE"][-1]["close"] == 1234.5


@pytest.mark.parametrize("params", [
    {"start_date": "2026-09-01", "end_date": "2026-08-01"},
    {"end_date": "invalid"},
])
def test_invalid_ranges_fail_closed(history_client, params):
    client, _ = history_client
    assert client.get("/api/kline/daily", params={"symbol": "2330.TWSE", **params}).status_code == 422


def test_custom_history_fallback_is_bounded_and_does_not_inject_today(history_client, monkeypatch):
    client, _ = history_client
    monkeypatch.setattr("app.services.preferences.get_daily_data_provider", lambda: "custom:sample")
    client.app.state.repo.get_daily_asset = lambda *args: pl.DataFrame()
    fetch = Mock(return_value=pl.DataFrame({
        "symbol": ["2330.TWSE"] * 3,
        "date": [date(2026, 7, 31), date(2026, 8, 3), date(2026, 8, 28)],
        "open": [100.0] * 3, "high": [102.0] * 3, "low": [99.0] * 3, "close": [101.0] * 3,
        "volume": [1000.0] * 3,
    }))
    monkeypatch.setattr(kline_sync, "sync_daily_batch", fetch)
    monkeypatch.setattr(kline, "compute_enriched", lambda raw, **kwargs: raw)
    inject = Mock(side_effect=AssertionError("historical ranges must not acquire today's candle"))
    monkeypatch.setattr(kline, "_maybe_inject_live_candle", inject)
    body = client.get("/api/kline/daily", params={
        "symbol": "2330.TWSE", "start_date": "2026-08-01", "end_date": "2026-08-10",
    }).json()
    assert [row["date"] for row in body["rows"]] == ["2026-08-03"]
    assert fetch.call_args.kwargs["start_time"].date() <= date(2026, 8, 1)
    assert fetch.call_args.kwargs["end_time"].date() == date(2026, 8, 10)
    assert body["stock_info"]["name"] == "台積電"
    inject.assert_not_called()


def test_history_metadata_distinguishes_stale_and_short_windows(history_client, monkeypatch):
    client, env = history_client
    original = TaiwanDailyStore.read_range

    def sparse(self, symbols, start, end):
        return original(self, symbols, start, end).filter(
            ((pl.col("symbol") == "2330.TWSE") & (pl.col("date") < env["target"]))
            | ((pl.col("symbol") == "0050.TWSE") & (pl.col("date") == env["target"]))
        )

    monkeypatch.setattr(TaiwanDailyStore, "read_range", sparse)
    body = client.post("/api/kline/daily-batch", json={"symbols": ["2330.TWSE", "0050.TWSE"]}).json()
    assert body["meta"]["2330.TWSE"]["status"] == "stale"
    assert body["meta"]["0050.TWSE"]["status"] == "partial"
