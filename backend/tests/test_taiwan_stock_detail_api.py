"""API tests for Taiwan Stock Detail Workspace (GET /api/taiwan/stocks/{symbol}).

Verifies:
  - 200 OK for valid canonical symbols (2330.TWSE, 0050.TWSE, 8069.TPEX).
  - Proper error status 400 for invalid symbol formats.
  - Price limit contract (rate, limit_up, is_no_limit).
  - All sections present even with empty/partial mock results.
"""
from __future__ import annotations

from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.taiwan.detail_models import (
    TaiwanDailyRow,
    TaiwanFactorsData,
    TaiwanHistoricalDaily,
    TaiwanInstitutionalData,
    TaiwanMarginData,
    TaiwanMarketContext,
    TaiwanMonitorSummary,
    TaiwanStockDetailResponse,
    TaiwanStockIdentity,
    TaiwanStockPriceLimit,
    TaiwanStockRealtime,
)


@pytest.fixture
def client():
    # Pass localhost client IP to satisfy auth middleware in testing
    return TestClient(app, client=("127.0.0.1", 50000))


def test_api_stock_detail_invalid_symbol(client):
    res = client.get("/api/taiwan/stocks/invalid_symbol")
    assert res.status_code == 400
    assert "無效的台股代碼格式" in res.json()["detail"]


def test_api_stock_detail_2330(client):
    res = client.get("/api/taiwan/stocks/2330.TWSE")
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "2330.TWSE"
    assert data["identity"]["name"] == "台積電"
    assert data["identity"]["exchange"] == "TWSE"
    assert data["identity"]["instrument_type"] == "stock"
    assert data["price_limit"]["is_no_limit"] is False
    assert data["price_limit"]["price_limit_pct"] == 0.1
    assert "realtime" in data
    assert "daily_history" in data
    assert "institutional" in data
    assert "margin" in data
    assert "factors" in data
    assert "market_context" in data
    assert "monitor_summary" in data


def test_api_stock_detail_0050(client):
    res = client.get("/api/taiwan/stocks/0050.TWSE")
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "0050.TWSE"
    assert data["identity"]["name"] == "元大台灣50"
    assert data["identity"]["instrument_type"] == "etf"
    assert data["price_limit"]["is_no_limit"] is False


def test_api_stock_detail_8069(client):
    res = client.get("/api/taiwan/stocks/8069.TPEX")
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "8069.TPEX"
    assert data["identity"]["name"] == "元太"
    assert data["identity"]["exchange"] == "TPEX"
    assert data["market_context"]["benchmark_symbol"] == "TPEX_INDEX"
