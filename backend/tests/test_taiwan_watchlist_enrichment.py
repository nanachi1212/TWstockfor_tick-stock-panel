"""Taiwan Watchlist enrichment (Phase 8B-5.0.4) — 纯 unit test, 无即时网络。

覆盖 app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows 本身;
market dispatch (legacy A 股 vs Taiwan 分流) 的整合测试见
test_watchlist_market_dispatch.py。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl
import pytest

from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows


@dataclass
class _FakeInstrument:
    name: str
    instrument_type: str


class _FakeSecurityMaster:
    def __init__(self, instruments: dict[str, _FakeInstrument]) -> None:
        self._instruments = instruments

    def get_instrument(self, symbol: str):
        return self._instruments.get(symbol)


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


class _FakeRealtimeService:
    """模拟 TaiwanRealtimeService.get_quotes: 有 quote 直接给, 没有则尝试
    daily_kline_fallback_fn(与真实 service 的 tier-5 daily fallback 语义一致)。
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
    """模拟 TaiwanDailyStore: 固定 fixture, 不落盘不读盘。"""

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


def test_twse_symbol_with_realtime_quote():
    """2330.TWSE 有 realtime quote 时, 各字段正确落到 watchlist 行契约上。"""
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
    assert row["close"] == 1100.0
    assert row["prev_close"] == 1080.0
    assert row["open"] == 1085.0
    assert row["high"] == 1105.0
    assert row["low"] == 1082.0
    assert row["change_amount"] == 20.0
    # TaiwanRealtimeQuote.change_pct 约定是「已乘 100」(1.85 代表 1.85%), watchlist
    # 契约(与 fmtPct 对齐)要小数(0.0185) —— 见 watchlist_enrichment._to_fraction_pct。
    assert row["change_pct"] == pytest.approx(0.0185)
    assert row["amount"] == 27500000000.0


def test_tpex_symbol_with_realtime_quote():
    """6488.TPEX(上柜)同样正常 enrichment, 不是只有 TWSE work。"""
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
    """Taiwan ETF(如 0050.TWSE)必须走同一条 enrichment flow, 不是只对 stock 生效。"""
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
    """无 realtime quote 但本地有日K 时, 应 fallback 到最新收盘价并算出涨跌。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(quotes={})  # 全部 provider 都无法取得 -> 走 daily fallback
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
    # 最新一笔 (8/31) 收盘 1100, 前一笔 (8/28) 收盘 1080 作为 prev_close
    assert row["close"] == 1100.0
    assert row["prev_close"] == 1080.0
    assert row["change_amount"] == pytest.approx(20.0)
    # _taiwan_daily_fallback_map 内部按 Taiwan 既有 provider 惯例算成「已乘 100」,
    # 最终经 _to_fraction_pct 换回小数, 两次换算抵消后应等于最朴素的小数涨跌幅。
    assert row["change_pct"] == pytest.approx(20.0 / 1080.0)
    assert row["open"] == 1085.0


def test_missing_data_symbol_still_returns_row_with_nulls():
    """完全没有 current/daily 数据的台股 symbol: 仍返回一行, 市场字段为 null, 不 500。"""
    sec_master = _FakeSecurityMaster({})  # 连 security master 都查无此 symbol
    rt_service = _FakeRealtimeService(quotes={})
    rows = enrich_taiwan_watchlist_rows(
        ["9999.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "9999.TWSE"
    assert row["name"] is None
    assert row["asset_type"] == "stock"  # 默认值, 不报错
    assert row["close"] is None
    assert row["change_pct"] is None


def test_empty_symbol_list_returns_empty():
    rows = enrich_taiwan_watchlist_rows([])
    assert rows == []


def test_batch_call_avoids_n_plus_one_provider_calls():
    """多个 symbol 一次性传给 realtime_service.get_quotes, 不逐个调用 (N+1 防护)。"""
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
    assert len(calls) == 1, "应只调用一次 get_quotes (批次), 不逐 symbol 各打一次"
    assert sorted(calls[0]) == ["0050.TWSE", "2330.TWSE", "6488.TPEX"]


def test_realtime_provider_exception_does_not_raise():
    """provider 层整体抛异常时, enrich_taiwan_watchlist_rows 本身不应向上抛 —— 由
    调用方(api/watchlist.py)的 try/except 兜底; 这里只验证不是"半途崩溃丢数据"。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(raise_error=True)
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    # 内部已 catch 住 provider 异常, 仍返回一行(名称正常, 行情为 null)
    assert len(rows) == 1
    assert rows[0]["name"] == "台積電"
    assert rows[0]["close"] is None
