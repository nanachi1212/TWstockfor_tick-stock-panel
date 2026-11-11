"""Taiwan Watchlist Enrichment (Phase 8B-5.0.4, extended in Phase 8B-5.0.5).

Thin adapter that fills in `/api/watchlist/enriched` rows for canonical Taiwan
symbols (``2330.TWSE`` / ``6488.TPEX``) by reusing the *existing* Taiwan
realtime/daily stack — the same one already powering TaiwanStockDetail,
TaiwanScreener and Monitor. This module does not fetch anything from the
network itself; it only orchestrates already-built services:

  - ``app.taiwan.universe.get_security_master()``   — name / instrument_type
  - ``app.taiwan.realtime.get_realtime_service()``  — realtime/snapshot quote
    with its own fallback chain (cache -> MIS -> Yahoo), extended here with
    the latest Taiwan daily close (``TaiwanDailyStore``), so a closed market /
    provider outage still shows
    the last known price instead of turning the whole row blank.
  - ``app.taiwan.technical_indicators.compute_taiwan_daily_indicators()``
    (Phase 8B-5.0.5) — MA5/10/20/60, vol_ma5/10, RSI14, MACD, momentum_5d/20d,
    computed from a single batched ``TaiwanDailyStore.read_range()`` call for
    the whole watchlist, never per symbol.

Deliberately still NOT implemented here (Phase 8B-5.0.5 report §S):
KDJ, Bollinger, ATR, turnover_rate, amplitude, annual_vol_20d, RPS, deviate_*,
signal_* (limit-up/board-related) — those stay ``None``; faking them would be
worse than showing "—".
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)


def _sanitize_float(value: Any) -> float | None:
    """NaN/Inf -> None; 其餘原樣回傳 (與 api/watchlist.py 對舊版 enriched 列的處理方式一致)。"""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _to_fraction_pct(value: Any) -> float | None:
    """把 Taiwan 的「已乘 100」漲跌幅換成 watchlist 契約的小數漲跌幅。

    TaiwanRealtimeQuote.change_pct 在 mis_provider / yahoo_provider 兩處
    provider 裡統一是 round(change/prev_close*100, N)
    (即 0.21 代表 0.21%); 但舊版 enriched 管線的 change_pct 欄是純小數
    (pipeline.py: change_pct = close/prev_close - 1, 0.05 代表 5%), 前端
    fmtPct() 也是按小數 * 100 來顯示 (frontend/src/lib/format.ts)。兩邊單位
    不同, 直接傳遞會讓 Watchlist 顯示成 100 倍的漲跌幅 —— 這裡做單位換算,
    不改 Taiwan 側任何既有 provider 的既定 contract。
    """
    value = _sanitize_float(value)
    if value is None:
        return None
    return value / 100.0


def _taiwan_daily_fallback_map(symbols: list[str], daily_store: Any) -> dict[str, dict]:
    """一次性批次讀取最近視窗的台股日K, 算出「最新收盤 + 前收盤 + 漲跌」映射。

    只讀一次 (最近 14 個日曆天的分區, 足夠跨過連假拿到 >= 2 個交易日), 不對每個
    symbol 各自打開 parquet —— 避免 N+1。回傳的 dict 直接餵給
    TaiwanRealtimeService.get_quotes(..., daily_kline_fallback_fn=...), 該函式
    對每個 symbol 只是一次 dict.get, 沒有額外 IO。
    """
    latest = daily_store.latest_date()
    if latest is None:
        return {}
    start = latest - timedelta(days=14)
    df = daily_store.read_range(symbols, start, latest)
    if df.is_empty():
        return {}

    df = df.sort(["symbol", "date"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close")
    )
    latest_rows = (
        df.with_columns(pl.col("date").max().over("symbol").alias("_max_date"))
        .filter(pl.col("date") == pl.col("_max_date"))
        .drop("_max_date")
    )

    result: dict[str, dict] = {}
    for row in latest_rows.to_dicts():
        close = row.get("close")
        prev_close = row.get("prev_close")
        change = None
        change_pct = None
        if close is not None and prev_close not in (None, 0):
            change = close - prev_close
            change_pct = change / prev_close * 100
        result[row["symbol"]] = {**row, "change": change, "change_pct": change_pct}
    return result


def _taiwan_indicator_map(symbols: list[str], daily_store: Any) -> dict[str, dict]:
    """一次性批次讀取足夠回看視窗的台股日K, 算出技術指標映射 (symbol -> 指標 dict)。

    只讀一次 (最近 150 個可用交易日分區, 足夠 MA60/MACD(12,26,9) 穩定暖機),
    不對每個 symbol 各自打開 parquet。與 _taiwan_daily_fallback_map 是各自
    獨立的批次讀取 (視窗大小不同: 14 天 vs 150 個交易日), 兩者都是 O(1) 次
    IO, 不隨 symbol 數量增長 —— 沒有合併成一次讀取, 是刻意的最小改動: 價格
    fallback 的視窗需求(2 個交易日)遠小於指標需求, 合併會讓 fallback 路徑
    平白多掃一個大得多的視窗。
    """
    from app.taiwan.technical_indicators import compute_taiwan_daily_indicators

    available = daily_store.available_dates()
    if not available:
        return {}
    lookback = available[-150:]
    start, end = lookback[0], lookback[-1]
    history = daily_store.read_range(symbols, start, end)
    if history.is_empty():
        return {}

    indicators = compute_taiwan_daily_indicators(history)
    return {row["symbol"]: row for row in indicators.to_dicts()}


def enrich_taiwan_watchlist_rows(
    symbols: list[str],
    *,
    security_master: Any | None = None,
    realtime_service: Any | None = None,
    daily_store: Any | None = None,
) -> list[dict]:
    """回傳 canonical Taiwan symbol 的自選股 enriched 列, 欄位對齊舊版 enriched 定義。

    單一批次呼叫 realtime_service.get_quotes(symbols, ...) —— 不逐 symbol 各打
    一次 provider, 由既有 TaiwanRealtimeService 自己的批次/快取機制處理。
    某個 symbol 若查無任何來源(未知/下市/尚無本機資料), 該列仍會回傳, 除
    symbol/name/asset_type 外全部欄位為 None —— 與既有 LEFT JOIN 語意
    (find nothing -> null, 不丟列) 一致, 不讓整個端點因單一標的失敗而 500。
    """
    if not symbols:
        return []

    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.realtime import get_realtime_service
    from app.taiwan.universe import get_security_master

    sec_master = security_master or get_security_master()
    rt_service = realtime_service or get_realtime_service()
    store = daily_store or TaiwanDailyStore()

    fallback_map = _taiwan_daily_fallback_map(symbols, store)

    def _daily_fallback(canonical: str) -> dict | None:
        return fallback_map.get(canonical)

    try:
        quote_map = rt_service.get_quotes(symbols, daily_kline_fallback_fn=_daily_fallback)
    except Exception as e:
        # 任何 provider 層異常都不該讓整個 /enriched 端點 500 —— 降級為全部標的
        # 缺行情, 名稱/資產類型仍照常回傳 (見下方 inst 查詢, 與行情來源獨立)。
        logger.warning("taiwan watchlist realtime quote fetch failed: %s", e)
        quote_map = {}

    try:
        indicator_map = _taiwan_indicator_map(symbols, store)
    except Exception as e:
        # 技術指標計算失敗不該連累價格/漲跌欄位 —— 退化為全部指標 null。
        logger.warning("taiwan watchlist indicator computation failed: %s", e)
        indicator_map = {}

    rows: list[dict] = []
    for symbol in symbols:
        inst = None
        try:
            inst = sec_master.get_instrument(symbol)
        except Exception as e:
            logger.debug("taiwan security master lookup failed for %s: %s", symbol, e)

        q = quote_map.get(symbol)
        ind = indicator_map.get(symbol) or {}
        asset_type = (
            inst.instrument_type if inst and inst.instrument_type in ("stock", "etf") else "stock"
        )
        rows.append({
            "symbol": symbol,
            "name": inst.name if inst else None,
            "instrument_type": inst.instrument_type if inst else None,
            "is_supported": bool(inst and inst.is_supported and inst.instrument_type in ("stock", "etf")),
            "asset_type": asset_type,
            "close": _sanitize_float(q.last_price) if q else None,
            "open": _sanitize_float(q.open) if q else None,
            "high": _sanitize_float(q.high) if q else None,
            "low": _sanitize_float(q.low) if q else None,
            "prev_close": _sanitize_float(q.prev_close) if q else None,
            "change_amount": _sanitize_float(q.change) if q else None,
            "change_pct": _to_fraction_pct(q.change_pct) if q else None,
            "amount": _sanitize_float(q.amount) if q else None,
            # Data Freshness & Source Labels batch (Post-8C follow-up): 直接傳遞既有
            # TaiwanRealtimeQuote 的 provenance/staleness metadata, 不新造 schema —
            # source_meta 直接重用 SourceMeta.to_dict()（Monitor /api/intraday/quotes
            # 與 StockDetail 已經在用同一份資料, 這裡之前漏掉沒傳）。quote_time 額外
            # 攤平出來方便前端直接讀, 不需要為了一個欄位解一層 source_meta。
            # quote 查無(q is None)時兩者都是 None, 不偽造新鮮度狀態。
            "quote_time": q.quote_time.isoformat() if q and q.quote_time else None,
            "source_meta": q.source_meta.to_dict() if q and q.source_meta else None,
            "ma5": _sanitize_float(ind.get("ma5")),
            "ma10": _sanitize_float(ind.get("ma10")),
            "ma20": _sanitize_float(ind.get("ma20")),
            "ma60": _sanitize_float(ind.get("ma60")),
            "vol_ma5": _sanitize_float(ind.get("vol_ma5")),
            "vol_ma10": _sanitize_float(ind.get("vol_ma10")),
            "rsi_14": _sanitize_float(ind.get("rsi_14")),
            "macd_dif": _sanitize_float(ind.get("macd_dif")),
            "macd_dea": _sanitize_float(ind.get("macd_dea")),
            "macd_hist": _sanitize_float(ind.get("macd_hist")),
            "momentum_5d": _sanitize_float(ind.get("momentum_5d")),
            "momentum_20d": _sanitize_float(ind.get("momentum_20d")),
        })
    return rows
