"""Taiwan Watchlist enrichment (Phase 8B-5.0.4) — 纯 unit test, 无即时网络。

覆盖 app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows 本身;
market dispatch (legacy A 股 vs Taiwan 分流) 的整合测试见
test_watchlist_market_dispatch.py。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

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

    def available_dates(self):
        if self._df.is_empty():
            return []
        return sorted(self._df["date"].unique().to_list())


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


# ===== Phase 8B-5.0.5: 技术指标透过 enrich_taiwan_watchlist_rows 落到 watchlist 行契约 =====

def _make_indicator_history(symbol: str, n: int = 70) -> pl.DataFrame:
    """足够 MA60/MACD 稳定暖机的确定性历史(与 test_taiwan_technical_indicators.py
    的 fixture 生成方式一致, 各自独立以避免测试间耦合)。"""
    closes = [round(100.0 + i * 0.5 + 3.0 * math.sin(i / 3.0), 4) for i in range(n)]
    volumes = [round(1_000_000 + i * 5_000, 1) for i in range(n)]
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n)]
    return pl.DataFrame({
        "symbol": [symbol] * n, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes, "amount": [c * v for c, v in zip(closes, volumes)],
    })


def test_twse_stock_gets_technical_indicators_populated():
    """2330.TWSE 有足够日线历史时, ma5/10/20/60、rsi_14、macd_*、momentum_* 都应有值。"""
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
        assert row[field] is not None, f"{field} 应有值(70 天历史足够)"
    assert 0.0 <= row["rsi_14"] <= 100.0


def test_tpex_stock_gets_technical_indicators_populated():
    """6488.TPEX 同样应正常算出技术指标, 不因交易所不同走错分支。"""
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
    """Taiwan ETF(0050.TWSE)必须走同一技术指标 flow, 不因 instrument_type==etf 被排除。"""
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
    """只有 3 天历史: 技术指标应全为 None, 不影响价格/涨跌欄位。"""
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
    # 价格/涨跌不受历史不足影响 (来自 realtime quote, 与技术指标是独立数据源)
    assert row["close"] == 1100.0
    assert row["change_pct"] == pytest.approx(0.0185)
    for field in ("ma5", "ma20", "ma60", "rsi_14", "macd_dif", "momentum_20d"):
        assert row[field] is None, f"{field} 应为 None(仅 3 天历史)"


def test_indicator_computation_uses_single_batch_read(monkeypatch):
    """技术指标计算应对整批 symbol 只调用一次 daily_store.read_range(), 不逐 symbol 各读一次。"""
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
    # 一次是价格 fallback 窗口的 read_range, 一次是技术指标窗口的 read_range ——
    # 两者都是与 symbol 数量无关的常数次数(各恰好一次), 不随 watchlist 大小增长。
    assert len(read_range_calls) == 2, (
        f"预期恰好 2 次 read_range 调用(fallback + indicators), 实际 {len(read_range_calls)}"
    )
    for call_symbols in read_range_calls:
        assert sorted(call_symbols) == ["2330.TWSE", "6488.TPEX"], "每次调用都应是整批 symbol, 不是逐个"
