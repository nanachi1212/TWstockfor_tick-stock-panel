"""Taiwan Watchlist enrichment (Phase 8B-5.0.4) — 純 unit test, 無即時網路。

覆蓋 app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows 本身;
market dispatch (舊版 vs Taiwan 分流) 的整合測試見
test_watchlist_market_dispatch.py。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows


@dataclass
class _FakeInstrument:
    name: str
    instrument_type: str
    is_supported: bool = True


class _FakeSecurityMaster:
    def __init__(self, instruments: dict[str, _FakeInstrument]) -> None:
        self._instruments = instruments

    def get_instrument(self, symbol: str):
        return self._instruments.get(symbol)


@dataclass
class _FakeSourceMeta:
    """鏡像 app.taiwan.enrichment.models.SourceMeta 的最小 fake, 只為測試
    watchlist_enrichment 對 source_meta 的透傳, 不依賴真實 provider 模型。"""
    source: str = "twse:mis"
    is_stale: bool = False
    freshness_class: str = "best_effort_near_realtime"
    fallback_reason: str | None = None
    trade_date: date | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "is_stale": self.is_stale,
            "freshness_class": self.freshness_class,
            "fallback_reason": self.fallback_reason,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
        }


@dataclass
class _FakeQuote:
    last_price: float | None
    prev_close: float | None
    open: float | None
    high: float | None
    low: float | None
    change: float | None
    change_pct: float | None
    volume: int | None
    amount: float | None
    quote_time: object | None = None
    source_meta: _FakeSourceMeta | None = None


class _FakeRealtimeService:
    """模擬 TaiwanRealtimeService.get_quotes: 有 quote 直接給, 沒有則嘗試
    daily_kline_fallback_fn(與真實 service 的 tier-5 daily fallback 語意一致)。
    """

    def __init__(self, quotes: dict[str, _FakeQuote] | None = None, raise_error: bool = False) -> None:
        self._quotes = quotes or {}
        self._raise_error = raise_error

    def get_quotes(self, symbols, daily_kline_fallback_fn=None):
        if self._raise_error:
            raise RuntimeError("simulated provider outage")
        result: dict[str, _FakeQuote] = {}
        for s in symbols:
            if s in self._quotes:
                result[s] = self._quotes[s]
            elif daily_kline_fallback_fn is not None:
                d = daily_kline_fallback_fn(s)
                if d:
                    result[s] = _FakeQuote(
                        last_price=d.get("close"),
                        prev_close=d.get("prev_close"),
                        open=d.get("open"),
                        high=d.get("high"),
                        low=d.get("low"),
                        change=d.get("change"),
                        change_pct=d.get("change_pct"),
                        volume=d.get("volume"),
                        amount=d.get("amount"),
                    )
        return result


class _FakeDailyStore:
    """模擬 TaiwanDailyStore: 固定 fixture, 不寫入磁碟不讀盤。"""

    def __init__(self, df: pl.DataFrame | None = None, latest: date | None = None) -> None:
        self._df = df if df is not None else pl.DataFrame(schema={
            "symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64,
        })
        self._latest = latest

    def latest_date(self):
        return self._latest

    def read_range(self, symbols, start, end):
        df = self._df
        if symbols is not None:
            df = df.filter(pl.col("symbol").is_in(symbols))
        return df

    def available_dates(self):
        if self._df.is_empty():
            return []
        return sorted(self._df["date"].unique().to_list())


def test_twse_symbol_with_realtime_quote():
    """2330.TWSE 有 realtime quote 時, 各欄位正確落到 watchlist 行契約上。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            last_price=1100.0, prev_close=1080.0, open=1085.0, high=1105.0, low=1082.0,
            change=20.0, change_pct=1.85, volume=25000000, amount=27500000000.0,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "2330.TWSE"
    assert row["name"] == "台積電"
    assert row["asset_type"] == "stock"
    assert row["instrument_type"] == "stock"
    assert row["is_supported"] is True
    assert row["close"] == 1100.0
    assert row["prev_close"] == 1080.0
    assert row["open"] == 1085.0
    assert row["high"] == 1105.0
    assert row["low"] == 1082.0
    assert row["change_amount"] == 20.0
    # TaiwanRealtimeQuote.change_pct 約定是「已乘 100」(1.85 代表 1.85%), watchlist
    # 契約(與 fmtPct 對齊)要小數(0.0185) —— 見 watchlist_enrichment._to_fraction_pct。
    assert row["change_pct"] == pytest.approx(0.0185)
    assert row["amount"] == 27500000000.0


def test_tpex_symbol_with_realtime_quote():
    """6488.TPEX(上櫃)同樣正常 enrichment, 不是只有 TWSE work。"""
    sec_master = _FakeSecurityMaster({
        "6488.TPEX": _FakeInstrument(name="環球晶", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "6488.TPEX": _FakeQuote(
            last_price=680.0, prev_close=675.0, open=676.0, high=685.0, low=674.0,
            change=5.0, change_pct=0.74, volume=3000000, amount=2040000000.0,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["6488.TPEX"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "6488.TPEX"
    assert row["name"] == "環球晶"
    assert row["close"] == 680.0
    assert row["change_pct"] == pytest.approx(0.0074)


def test_taiwan_etf_goes_through_same_flow():
    """Taiwan ETF(如 0050.TWSE)必須走同一條 enrichment flow, 不是只對 stock 生效。"""
    sec_master = _FakeSecurityMaster({
        "0050.TWSE": _FakeInstrument(name="元大台灣50", instrument_type="etf"),
    })
    rt_service = _FakeRealtimeService({
        "0050.TWSE": _FakeQuote(
            last_price=185.5, prev_close=184.0, open=184.2, high=186.0, low=184.0,
            change=1.5, change_pct=0.82, volume=8000000, amount=1480000000.0,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["0050.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["asset_type"] == "etf"
    assert row["close"] == 185.5
    assert row["name"] == "元大台灣50"


def test_closed_market_falls_back_to_latest_daily_close():
    """無 realtime quote 但本地有日K 時, 應 fallback 到最新收盤價並算出漲跌。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(quotes={})  # 全部 provider 都無法取得 -> 走 daily fallback
    daily_df = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "date": [date(2026, 8, 28), date(2026, 8, 31)],
        "open": [1070.0, 1085.0],
        "high": [1082.0, 1105.0],
        "low": [1065.0, 1082.0],
        "close": [1080.0, 1100.0],
        "volume": [20000000.0, 25000000.0],
        "amount": [21600000000.0, 27500000000.0],
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(df=daily_df, latest=date(2026, 8, 31)),
    )
    assert len(rows) == 1
    row = rows[0]
    # 最新一筆 (8/31) 收盤 1100, 前一筆 (8/28) 收盤 1080 作為 prev_close
    assert row["close"] == 1100.0
    assert row["prev_close"] == 1080.0
    assert row["change_amount"] == pytest.approx(20.0)
    # _taiwan_daily_fallback_map 內部按 Taiwan 既有 provider 慣例算成「已乘 100」,
    # 最終經 _to_fraction_pct 換回小數, 兩次換算抵消後應等於最樸素的小數漲跌幅。
    assert row["change_pct"] == pytest.approx(20.0 / 1080.0)
    assert row["open"] == 1085.0


def test_missing_data_symbol_still_returns_row_with_nulls():
    """完全沒有 current/daily 資料的台股 symbol: 仍回傳一行, 市場欄位為 null, 不 500。"""
    sec_master = _FakeSecurityMaster({})  # 連 security master 都查無此 symbol
    rt_service = _FakeRealtimeService(quotes={})
    rows = enrich_taiwan_watchlist_rows(
        ["9999.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "9999.TWSE"
    assert row["name"] is None
    assert row["asset_type"] == "stock"  # 預設值, 不出錯
    assert row["close"] is None
    assert row["change_pct"] is None


def test_empty_symbol_list_returns_empty():
    rows = enrich_taiwan_watchlist_rows([])
    assert rows == []


def test_batch_call_avoids_n_plus_one_provider_calls():
    """多個 symbol 一次性傳給 realtime_service.get_quotes, 不逐個呼叫 (N+1 防護)。"""
    calls: list[list[str]] = []

    class _CountingRealtimeService(_FakeRealtimeService):
        def get_quotes(self, symbols, daily_kline_fallback_fn=None):
            calls.append(list(symbols))
            return super().get_quotes(symbols, daily_kline_fallback_fn)

    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
        "6488.TPEX": _FakeInstrument(name="環球晶", instrument_type="stock"),
        "0050.TWSE": _FakeInstrument(name="元大台灣50", instrument_type="etf"),
    })
    rt_service = _CountingRealtimeService({
        "2330.TWSE": _FakeQuote(1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0),
        "6488.TPEX": _FakeQuote(680.0, 675.0, 676.0, 685.0, 674.0, 5.0, 0.74, 1, 1.0),
        "0050.TWSE": _FakeQuote(185.5, 184.0, 184.2, 186.0, 184.0, 1.5, 0.82, 1, 1.0),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE", "6488.TPEX", "0050.TWSE"],
        security_master=sec_master, realtime_service=rt_service, daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 3
    assert len(calls) == 1, "應只呼叫一次 get_quotes (批次), 不逐 symbol 各打一次"
    assert sorted(calls[0]) == ["0050.TWSE", "2330.TWSE", "6488.TPEX"]


def test_realtime_provider_exception_does_not_raise():
    """provider 層整體拋異常時, enrich_taiwan_watchlist_rows 本身不應向上拋 —— 由
    呼叫方(api/watchlist.py)的 try/except 保底; 這裡只驗證不是"半途崩潰丟資料"。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(raise_error=True)
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    # 內部已 catch 住 provider 異常, 仍回傳一行(名稱正常, 行情為 null)
    assert len(rows) == 1
    assert rows[0]["name"] == "台積電"
    assert rows[0]["close"] is None


# ===== Phase 8B-5.0.5: 技術指標透過 enrich_taiwan_watchlist_rows 落到 watchlist 行契約 =====

def _make_indicator_history(symbol: str, n: int = 70) -> pl.DataFrame:
    """足夠 MA60/MACD 穩定暖機的確定性歷史(與 test_taiwan_technical_indicators.py
    的 fixture 生成方式一致, 各自獨立以避免測試間耦合)。"""
    closes = [round(100.0 + i * 0.5 + 3.0 * math.sin(i / 3.0), 4) for i in range(n)]
    volumes = [round(1_000_000 + i * 5_000, 1) for i in range(n)]
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n)]
    return pl.DataFrame({
        "symbol": [symbol] * n, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes, "amount": [c * v for c, v in zip(closes, volumes, strict=False)],
    })


def test_twse_stock_gets_technical_indicators_populated():
    """2330.TWSE 有足夠日線歷史時, ma5/10/20/60、rsi_14、macd_*、momentum_* 都應有值。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0),
    })
    history = _make_indicator_history("2330.TWSE")
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(df=history, latest=history["date"].max()),
    )
    row = rows[0]
    for field in ("ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma10",
                  "rsi_14", "macd_dif", "macd_dea", "macd_hist",
                  "momentum_5d", "momentum_20d"):
        assert row[field] is not None, f"{field} 應有值(70 天歷史足夠)"
    assert 0.0 <= row["rsi_14"] <= 100.0


def test_tpex_stock_gets_technical_indicators_populated():
    """6488.TPEX 同樣應正常算出技術指標, 不因交易所不同走錯分支。"""
    sec_master = _FakeSecurityMaster({
        "6488.TPEX": _FakeInstrument(name="環球晶", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "6488.TPEX": _FakeQuote(680.0, 675.0, 676.0, 685.0, 674.0, 5.0, 0.74, 1, 1.0),
    })
    history = _make_indicator_history("6488.TPEX")
    rows = enrich_taiwan_watchlist_rows(
        ["6488.TPEX"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(df=history, latest=history["date"].max()),
    )
    row = rows[0]
    assert row["ma20"] is not None
    assert row["macd_dif"] is not None


def test_etf_gets_technical_indicators_populated():
    """Taiwan ETF(0050.TWSE)必須走同一技術指標 flow, 不因 instrument_type==etf 被排除。"""
    sec_master = _FakeSecurityMaster({
        "0050.TWSE": _FakeInstrument(name="元大台灣50", instrument_type="etf"),
    })
    rt_service = _FakeRealtimeService({
        "0050.TWSE": _FakeQuote(185.5, 184.0, 184.2, 186.0, 184.0, 1.5, 0.82, 1, 1.0),
    })
    history = _make_indicator_history("0050.TWSE")
    rows = enrich_taiwan_watchlist_rows(
        ["0050.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(df=history, latest=history["date"].max()),
    )
    row = rows[0]
    assert row["asset_type"] == "etf"
    assert row["ma5"] is not None
    assert row["rsi_14"] is not None


def test_insufficient_history_leaves_technical_fields_null():
    """只有 3 天歷史: 技術指標應全為 None, 不影響價格/漲跌欄位。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0),
    })
    history = _make_indicator_history("2330.TWSE", n=3)
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(df=history, latest=history["date"].max()),
    )
    row = rows[0]
    # 價格/漲跌不受歷史不足影響 (來自 realtime quote, 與技術指標是獨立資料來源)
    assert row["close"] == 1100.0
    assert row["change_pct"] == pytest.approx(0.0185)
    for field in ("ma5", "ma20", "ma60", "rsi_14", "macd_dif", "momentum_20d"):
        assert row[field] is None, f"{field} 應為 None(僅 3 天歷史)"


def test_indicator_computation_uses_single_batch_read(monkeypatch):
    """技術指標計算應對整批 symbol 只呼叫一次 daily_store.read_range(), 不逐 symbol 各讀一次。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
        "6488.TPEX": _FakeInstrument(name="環球晶", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0),
        "6488.TPEX": _FakeQuote(680.0, 675.0, 676.0, 685.0, 674.0, 5.0, 0.74, 1, 1.0),
    })
    h1 = _make_indicator_history("2330.TWSE")
    h2 = _make_indicator_history("6488.TPEX")
    combined = pl.concat([h1, h2], how="vertical")

    read_range_calls: list[list[str]] = []

    class _CountingDailyStore(_FakeDailyStore):
        def read_range(self, symbols, start, end):
            read_range_calls.append(list(symbols) if symbols is not None else [])
            return super().read_range(symbols, start, end)

    store = _CountingDailyStore(df=combined, latest=combined["date"].max())
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE", "6488.TPEX"], security_master=sec_master, realtime_service=rt_service,
        daily_store=store,
    )
    assert len(rows) == 2
    assert all(r["ma20"] is not None for r in rows)
    # 一次是價格 fallback 視窗的 read_range, 一次是技術指標視窗的 read_range ——
    # 兩者都是與 symbol 數量無關的常數次數(各恰好一次), 不隨 watchlist 大小增長。
    assert len(read_range_calls) == 2, (
        f"預期恰好 2 次 read_range 呼叫(fallback + indicators), 實際 {len(read_range_calls)}"
    )
    for call_symbols in read_range_calls:
        assert sorted(call_symbols) == ["2330.TWSE", "6488.TPEX"], "每次呼叫都應是整批 symbol, 不是逐個"


# ===== Data Freshness & Source Labels batch (Post-8C follow-up): =====
# Monitor (/api/intraday/quotes) 與 StockDetail 一直都完整透傳
# TaiwanRealtimeQuote.source_meta / quote_time; enrich_taiwan_watchlist_rows
# 之前組 row dict 時把這兩個欄位漏掉了, 導致 Watchlist 前端完全拿不到新鮮度/
# 來源資訊(見 Data Source Consistency & Freshness Audit report §D)。以下測試
# 覆蓋補上的透傳邏輯本身, 不重新驗證 realtime_service 的 fallback chain(那部分
# 已由 test_taiwan_realtime_service.py 等既有測試覆蓋)。

def test_watchlist_row_preserves_source_meta():
    """有 quote 時, row["source_meta"] 應是 q.source_meta.to_dict() 的原樣結果。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(source="twse:mis", is_stale=False, freshness_class="best_effort_near_realtime")
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0,
            source_meta=meta,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["source_meta"] == meta.to_dict()
    assert row["source_meta"]["source"] == "twse:mis"
    assert row["source_meta"]["freshness_class"] == "best_effort_near_realtime"


def test_watchlist_row_is_stale_survives_serialization():
    """is_stale=True 的 quote, row["source_meta"]["is_stale"] 必須原樣是 True
    (不能因為序列化被靜默丟掉或換成假的 False)。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(source="yahoo:chart", is_stale=True, freshness_class="delayed_15m")
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            source_meta=meta,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["source_meta"]["is_stale"] is True
    assert row["source_meta"]["freshness_class"] == "delayed_15m"


def test_watchlist_row_quote_time_survives_serialization():
    """quote_time 應以 ISO 字串落到 row["quote_time"], 與 q.quote_time.isoformat() 一致。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    qt = datetime(2026, 9, 9, 9, 53, 58)
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            quote_time=qt, source_meta=_FakeSourceMeta(),
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["quote_time"] == qt.isoformat()


def test_watchlist_row_fallback_reason_survives_serialization():
    """降級 fallback 時的 fallback_reason 應保留在 source_meta 內, 供前端 tooltip 使用。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(
        source="yahoo:chart", is_stale=True, freshness_class="delayed_15m",
        fallback_reason="Primary MIS unavailable or missing symbol",
    )
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            source_meta=meta,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["source_meta"]["fallback_reason"] == "Primary MIS unavailable or missing symbol"


def test_watchlist_row_missing_metadata_does_not_crash():
    """quote 存在但 quote_time/source_meta 都是 None (舊 fixture / mock 不完整) 時,
    row 仍正常回傳, 不因缺 metadata 而拋異常; 價格欄位不受影響。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            quote_time=None, source_meta=None,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["quote_time"] is None
    assert row["source_meta"] is None
    assert row["close"] == 2480.0  # 價格欄位不受 metadata 缺失影響


def test_watchlist_row_no_quote_at_all_has_no_fake_metadata():
    """完全查無 quote 的 symbol (q is None): quote_time/source_meta 應是 None,
    不得偽造出「即時」之類假新鮮度狀態。"""
    sec_master = _FakeSecurityMaster({
        "9999.TWSE": _FakeInstrument(name="測試無資料股", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(quotes={})
    rows = enrich_taiwan_watchlist_rows(
        ["9999.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["quote_time"] is None
    assert row["source_meta"] is None
