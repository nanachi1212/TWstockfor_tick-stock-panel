from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.market_rules import TaxClass


class FakeSecurityMaster:
    def get_instrument(self, symbol: str):
        return SimpleNamespace(symbol=symbol, instrument_type="stock", is_supported=True)


def test_transaction_tax_uses_market_rules(monkeypatch):
    monkeypatch.setattr("app.api.taiwan.taipei_today", lambda: date(2026, 9, 24))
    monkeypatch.setattr("app.api.taiwan.TaiwanDailyStore.has_symbol_date", lambda *_: True)
    monkeypatch.setattr("app.api.taiwan.get_security_master", FakeSecurityMaster)
    monkeypatch.setattr("app.api.taiwan.MarketProfileBridge.get_tax_class", lambda _: TaxClass.ORDINARY_STOCK)
    client = TestClient(app, client=("127.0.0.1", 50000))

    regular = client.get("/api/taiwan/transaction-tax", params={
        "symbol": "2330.TWSE", "trade_value": 10_000, "trade_date": "2026-09-24",
    })
    day_trade = client.get("/api/taiwan/transaction-tax", params={
        "symbol": "2330.TWSE", "trade_value": 10_000, "trade_date": "2026-09-24", "is_day_trade": "true",
    })

    assert regular.status_code == 200
    assert regular.json() == {
        "symbol": "2330.TWSE", "tax_class": "ordinary_stock", "tax_rate": 0.003, "tax_amount": 30,
    }
    assert day_trade.status_code == 200
    assert day_trade.json()["tax_amount"] == 15


def test_transaction_tax_rejects_unknown_symbol_and_day_trading_etf(monkeypatch):
    monkeypatch.setattr("app.api.taiwan.taipei_today", lambda: date(2026, 9, 24))
    monkeypatch.setattr("app.api.taiwan.TaiwanDailyStore.has_symbol_date", lambda *_: True)
    class EtfSecurityMaster:
        def get_instrument(self, symbol: str):
            return SimpleNamespace(symbol=symbol, instrument_type="etf", is_supported=True)

    monkeypatch.setattr("app.api.taiwan.get_security_master", EtfSecurityMaster)
    monkeypatch.setattr("app.api.taiwan.MarketProfileBridge.get_tax_class", lambda _: TaxClass.DOMESTIC_ETF)
    client = TestClient(app, client=("127.0.0.1", 50000))

    etf = client.get("/api/taiwan/transaction-tax", params={
        "symbol": "0050.TWSE", "trade_value": 10_000, "trade_date": "2026-09-24", "is_day_trade": "true",
    })
    unknown = client.get("/api/taiwan/transaction-tax", params={
        "symbol": "not-a-symbol", "trade_value": 10_000, "trade_date": "2026-09-24",
    })

    assert etf.status_code == 422
    assert "當沖稅率" in etf.json()["detail"]
    assert unknown.status_code == 400


def test_portfolio_instrument_validates_symbol_and_trading_date(monkeypatch):
    monkeypatch.setattr("app.api.taiwan.taipei_today", lambda: date(2026, 9, 24))
    monkeypatch.setattr("app.api.taiwan.TaiwanDailyStore.has_symbol_date", lambda *_: True)
    monkeypatch.setattr("app.api.taiwan.get_security_master", FakeSecurityMaster)
    monkeypatch.setattr("app.api.taiwan.MarketProfileBridge.get_tax_class", lambda _: TaxClass.ORDINARY_STOCK)
    client = TestClient(app, client=("127.0.0.1", 50000))

    profile = client.get("/api/taiwan/portfolio-instrument", params={
        "symbol": "2330.TWSE", "trade_date": "2026-09-24",
    })
    saturday = client.get("/api/taiwan/portfolio-instrument", params={
        "symbol": "2330.TWSE", "trade_date": "2026-09-19",
    })
    future = client.get("/api/taiwan/portfolio-instrument", params={
        "symbol": "2330.TWSE", "trade_date": (date(2026, 9, 24) + timedelta(days=1)).isoformat(),
    })

    assert profile.status_code == 200
    assert profile.json()["is_supported"] is True
    assert profile.json()["trading_day_status"] == "verified"
    assert saturday.status_code == 422
    assert "休市日" in saturday.json()["detail"]
    assert future.status_code == 422
    assert "晚於今日" in future.json()["detail"]


def test_portfolio_instrument_rejects_unverified_weekday(monkeypatch):
    monkeypatch.setattr("app.api.taiwan.taipei_today", lambda: date(2026, 9, 24))
    monkeypatch.setattr("app.api.taiwan.get_security_master", FakeSecurityMaster)
    monkeypatch.setattr("app.api.taiwan.TaiwanDailyStore.has_symbol_date", lambda *_: False)
    monkeypatch.setattr("app.api.taiwan.MarketProfileBridge.get_tax_class", lambda _: TaxClass.ORDINARY_STOCK)
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get("/api/taiwan/portfolio-instrument", params={
        "symbol": "2330.TWSE", "trade_date": "2026-09-24",
    })

    assert response.status_code == 422
    assert "確認成交日期" in response.json()["detail"]
