"""GET /api/intraday/quotes — canonical company name resolution.

TAIWAN_LOCALIZATION_POLISH: root cause of Monitor 顯示英文公司全名
(如 "TAIWAN SEMICONDUCTOR MANUFACTUR") 而非 Screener/Watchlist 顯示的
"台積電" —— 見 app/api/intraday.py:get_quotes()。該端點原本把
TaiwanRealtimeQuote.to_dict() 的 name 原樣回傳; 該欄位來自實際命中的
provider (yahoo_provider.py: name=meta.get("shortName"), 通常是英文公司
全名), 從未被 TaiwanSecurityMaster 的正體中文簡稱覆蓋 —— 即便 inst 早已
在同一段程式碼裡為了漲跌停試算被查出來。

本檔案只驗證這個小範圍修正: 優先序 canonical Taiwan 中文簡稱 → provider
name → symbol, 不涉及 realtime fallback chain / provider priority 本身。
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


def _fake_quote(symbol: str, provider_name: str, prev_close: float = 2470.0):
    """模拟 TaiwanRealtimeQuote: 只需要 .symbol / .prev_close / .to_dict()。"""
    q = MagicMock()
    q.symbol = symbol
    q.prev_close = prev_close
    q.to_dict.return_value = {
        "symbol": symbol,
        "name": provider_name,
        "last_price": 2485.0,
        "prev_close": prev_close,
        "change_pct": 0.0061,
        "quote_time": datetime(2026, 9, 9, 9, 54, 40).isoformat(),
        "trade_date": date(2026, 9, 9).isoformat(),
        "source_meta": {"source": "yahoo:chart", "is_stale": True},
    }
    return q


def _fake_instrument(symbol: str, name: str):
    inst = MagicMock()
    inst.symbol = symbol
    inst.name = name
    inst.instrument_type = "stock"
    return inst


client = TestClient(app, client=("127.0.0.1", 50000))


def test_canonical_name_overrides_english_provider_name():
    """SecurityMaster 有中文簡稱時, 回傳的 name 必須是中文簡稱, 不是 provider 原文。"""
    fake_rt_service = MagicMock()
    fake_rt_service.get_quotes.return_value = {
        "2330.TWSE": _fake_quote("2330.TWSE", "TAIWAN SEMICONDUCTOR MANUFACTUR..."),
    }
    fake_sec_master = MagicMock()
    fake_sec_master.get_instrument.return_value = _fake_instrument("2330.TWSE", "台積電")

    with patch("app.api.intraday.get_realtime_service", return_value=fake_rt_service), \
         patch("app.taiwan.universe.get_security_master", return_value=fake_sec_master), \
         patch("app.taiwan.universe.models.MarketProfileBridge.get_price_limit_pct", return_value=None):
        res = client.get("/api/intraday/quotes", params={"symbols": "2330.TWSE"})

    assert res.status_code == 200
    quotes = res.json()["quotes"]
    assert len(quotes) == 1
    assert quotes[0]["name"] == "台積電"
    assert quotes[0]["symbol"] == "2330.TWSE"


def test_falls_back_to_provider_name_when_canonical_unavailable():
    """SecurityMaster 查無該 symbol 時 (inst is None), 退回 provider name, 不留白也不 crash。"""
    fake_rt_service = MagicMock()
    fake_rt_service.get_quotes.return_value = {
        "9999.TWSE": _fake_quote("9999.TWSE", "SOME PROVIDER NAME"),
    }
    fake_sec_master = MagicMock()
    fake_sec_master.get_instrument.return_value = None

    with patch("app.api.intraday.get_realtime_service", return_value=fake_rt_service), \
         patch("app.taiwan.universe.get_security_master", return_value=fake_sec_master):
        res = client.get("/api/intraday/quotes", params={"symbols": "9999.TWSE"})

    assert res.status_code == 200
    quotes = res.json()["quotes"]
    assert quotes[0]["name"] == "SOME PROVIDER NAME"


def test_falls_back_to_symbol_when_both_canonical_and_provider_name_missing():
    """canonical 與 provider name 都沒有時, 退回 symbol, 不留 None/空字串誤導使用者。"""
    fake_rt_service = MagicMock()
    q = _fake_quote("8888.TWSE", "")
    q.to_dict.return_value["name"] = None
    fake_rt_service.get_quotes.return_value = {"8888.TWSE": q}
    fake_sec_master = MagicMock()
    fake_sec_master.get_instrument.return_value = None

    with patch("app.api.intraday.get_realtime_service", return_value=fake_rt_service), \
         patch("app.taiwan.universe.get_security_master", return_value=fake_sec_master):
        res = client.get("/api/intraday/quotes", params={"symbols": "8888.TWSE"})

    assert res.status_code == 200
    quotes = res.json()["quotes"]
    assert quotes[0]["name"] == "8888.TWSE"


def test_missing_security_master_does_not_crash_the_endpoint():
    """sec_master.get_instrument 本身丟例外也不該讓整個端點壞掉 —— 呼應既有
    「單一標的失敗不影響整批回應」的既定容錯口徑 (與 watchlist enrichment 一致)。
    這裡驗證的是既有行為未被本次改動破壞, 不是新增容錯邏輯。"""
    fake_rt_service = MagicMock()
    fake_rt_service.get_quotes.return_value = {
        "2330.TWSE": _fake_quote("2330.TWSE", "TAIWAN SEMICONDUCTOR MANUFACTUR..."),
    }
    fake_sec_master = MagicMock()
    fake_sec_master.get_instrument.return_value = None  # 查無資料, 而非拋例外

    with patch("app.api.intraday.get_realtime_service", return_value=fake_rt_service), \
         patch("app.taiwan.universe.get_security_master", return_value=fake_sec_master):
        res = client.get("/api/intraday/quotes", params={"symbols": "2330.TWSE"})

    assert res.status_code == 200
    assert res.json()["quotes"][0]["name"] == "TAIWAN SEMICONDUCTOR MANUFACTUR..."
