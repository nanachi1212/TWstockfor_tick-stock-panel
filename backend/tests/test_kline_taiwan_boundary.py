"""Retired market requests never touch storage/network; Taiwan session routing remains usable."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import kline as api
from app.capabilities import CapabilitySet
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar


class _ExplodingRepo:
    def __getattr__(self, name):
        raise AssertionError(f"rejected request must not read repository: {name}")


@pytest.fixture
def client(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("rejected request must not fetch data")

    for name in ("sync_daily_batch", "sync_minute_batch", "fetch_minute_single"):
        monkeypatch.setattr(api.kline_sync, name, fail)
    app = FastAPI()
    app.state.repo = _ExplodingRepo()
    app.state.capabilities = CapabilitySet()
    app.include_router(api.router)
    return TestClient(app)


@pytest.mark.parametrize("method,path,params,body", [
    ("get", "/daily", {"symbol": "600519.SH"}, None),
    ("get", "/minute", {"symbol": "000001.SZ"}, None),
    ("post", "/daily-batch", None, {"symbols": ["2330.TWSE", "600519.SH"]}),
    ("post", "/minute-batch", None, {"symbols": ["000001.SZ"]}),
])
def test_retired_symbols_rejected_before_storage_or_fetch(client, method, path, params, body):
    response = client.request(method, "/api/kline" + path, params=params, json=body)
    assert response.status_code == 422


@pytest.mark.parametrize("path,method", [("/minute", "get"), ("/minute-batch", "post")])
def test_missing_custom_minute_is_explicitly_unavailable(client, monkeypatch, path, method):
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "taiwan")
    if method == "get":
        response = client.get("/api/kline" + path, params={"symbol": "2330.TWSE"})
    else:
        response = client.post("/api/kline" + path, json={"symbols": ["2330.TWSE"]})
    assert response.status_code == 403
    assert "Pro" not in response.json()["detail"]


@pytest.mark.parametrize("hour,minute,expected", [
    (8, 59, 0), (9, 0, 0), (9, 15, 15), (12, 30, 210), (13, 30, 270), (15, 0, 270),
])
def test_taiwan_minutes_include_no_lunch_break(monkeypatch, hour, minute, expected):
    day = date(2026, 11, 3)
    monkeypatch.setattr(api, "taipei_now", lambda: datetime(2026, 11, 3, hour, minute, tzinfo=TAIPEI_TZ))
    assert api._expected_minutes(day) == expected
    assert api._expected_minutes(day - timedelta(days=1)) == 270
    assert api._expected_minutes(day + timedelta(days=1)) == 0


def test_incomplete_batch_fetches_taiwan_session(monkeypatch):
    day = date(2026, 11, 3)
    repo = MagicMock()
    repo.get_etf_symbol_set.return_value = {"0050.TWSE"}
    repo.get_minute_batch.return_value = pl.DataFrame()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, capabilities=CapabilitySet())))
    monkeypatch.setattr(api, "_minute_allowed", lambda capset: True)
    monkeypatch.setattr(api, "taipei_now", lambda: datetime(2026, 11, 3, 12, 30, tzinfo=TAIPEI_TZ))
    calls = []

    def fetch(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return pl.DataFrame({"symbol": symbols, "datetime": [datetime(2026, 11, 3, 9)] * len(symbols), "close": [1.0] * len(symbols)})

    monkeypatch.setattr(api.kline_sync, "sync_minute_batch", fetch)
    result = api.get_minute_batch(request, {"symbols": ["2330.TWSE", "0050.TWSE"], "date": str(day)})
    assert set(result["data"]) == {"2330.TWSE", "0050.TWSE"}
    assert [kwargs["asset_type"] for _, kwargs in calls] == ["stock", "etf"]
    for _, kwargs in calls:
        assert kwargs["start_time"] == datetime(2026, 11, 3, 9, tzinfo=TAIPEI_TZ)
        assert kwargs["end_time"] == datetime(2026, 11, 3, 13, 30, tzinfo=TAIPEI_TZ)


def test_weekday_missing_daily_does_not_masquerade_as_holiday(monkeypatch):
    today = date(2026, 11, 3)
    monkeypatch.setattr(api, "taipei_today", lambda: today)
    monkeypatch.setattr("app.taiwan.realtime.get_realtime_service",
                        lambda: SimpleNamespace(trading_calendar=TaiwanTradingCalendar()))
    assert api._default_minute_date(_ExplodingRepo()) == today


def test_confirmed_holiday_uses_existing_cached_session(monkeypatch):
    today = date(2026, 11, 3)
    previous = today - timedelta(days=1)
    monkeypatch.setattr(api, "taipei_today", lambda: today)
    monkeypatch.setattr("app.taiwan.realtime.get_realtime_service",
                        lambda: SimpleNamespace(trading_calendar=TaiwanTradingCalendar(known_holidays={today})))
    repo = MagicMock()
    repo.latest_minute_date_global.return_value = previous
    assert api._default_minute_date(repo) == previous
    repo.latest_daily_date.assert_not_called()
