# ruff: noqa: RUF001, RUF002, RUF003 -- user-facing Traditional Chinese verdict text.
"""日／週／月 K 的規則式多空判定，每個週期一句白話。

純計算：輸入一檔股票的原始日 K（未復權，與圖表同一份），週 K、月 K 由日 K 重組。
均線、RSI、MACD 一律走 ``compute_taiwan_indicator_panel``（台股唯一指標實作）；
KD(9,3,3) 公式與 ``app/indicators/pipeline.py`` 相同。指標暖機不足就不計該條規則，
並列入 ``missing``，不會補 0。分數只代表目前條件的多空傾向，不是上漲機率。
"""
from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.technical_indicators import compute_taiwan_indicator_panel

Timeframe = Literal["daily", "weekly", "monthly"]
Bias = Literal["bullish", "bearish", "neutral"]

_LABEL: dict[Timeframe, str] = {"daily": "日線", "weekly": "週線", "monthly": "月線"}
_EVERY: dict[Timeframe, str | None] = {"daily": None, "weekly": "1w", "monthly": "1mo"}
# 乖離過大門檻：週期越長，正常波動越大。
_BIAS_LIMIT: dict[Timeframe, float] = {"daily": 0.08, "weekly": 0.15, "monthly": 0.25}
# 分數絕對值到這裡才判偏多／偏空，否則中性。
_VERDICT_THRESHOLD = 10
# 均線、KD、MACD、RSI 四組裡缺超過兩組就不下判定。
_MAX_MISSING = 2


class TimeframeVerdict(BaseModel):
    timeframe: Timeframe
    label: str = Field(..., description="日線／週線／月線")
    status: Literal["available", "data_insufficient"]
    bias: Bias | None = None
    score: int | None = Field(None, description="規則分數，正為偏多、負為偏空")
    summary: str = Field(..., description="一句話白話判定")
    bars: int = Field(0, description="該週期的 K 棒數")
    missing: list[str] = Field(default_factory=list, description="暖機不足而未計分的指標")
    as_of: str | None = Field(None, description="最後一根日 K 的日期")
    is_stale: bool = Field(False, description="最後一根日 K 早於最新確認交易日")


def _bars(daily: pl.DataFrame, timeframe: Timeframe) -> pl.DataFrame:
    frame = (
        daily.select(
            pl.col("date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("date"),
            *(pl.col(c).cast(pl.Float64) for c in ("open", "high", "low", "close", "volume")),
        )
        .filter(pl.col("close") > 0)
        .sort("date")
    )
    every = _EVERY[timeframe]
    if every is not None:
        # 最後一根週／月 K 含本週（本月）至今，尚未收完。
        frame = frame.group_by_dynamic("date", every=every).agg(
            pl.col("open").first(), pl.col("high").max(), pl.col("low").min(),
            pl.col("close").last(), pl.col("volume").sum(),
        )
    return frame.with_columns(pl.lit("_").alias("symbol"))


def _with_kd(bars: pl.DataFrame) -> pl.DataFrame:
    low = pl.col("low").rolling_min(9, min_samples=9)
    high = pl.col("high").rolling_max(9, min_samples=9)
    rsv = 100 * (pl.col("close") - low) / (high - low).clip(lower_bound=1e-12)
    return bars.with_columns(rsv.ewm_mean(alpha=1.0 / 3, adjust=False, ignore_nulls=True).alias("kd_k")) \
        .with_columns(pl.col("kd_k").ewm_mean(alpha=1.0 / 3, adjust=False, ignore_nulls=True).alias("kd_d"))


def _rules(now: dict, prev: dict, timeframe: Timeframe) -> tuple[list[tuple[int, str]], list[str]]:
    """回傳 (分數, 白話) 清單與暖機不足的指標名。"""
    hits: list[tuple[int, str]] = []
    missing: list[str] = []
    close = now["close"]
    ma5, ma20 = now.get("ma5"), now.get("ma20")

    if ma5 is None or ma20 is None:
        missing.append("均線")
    else:
        if close > ma5 and close > ma20:
            hits.append((10, "股價站上 MA5/MA20"))
        elif close < ma5 and close < ma20:
            hits.append((-10, "股價低於 MA5/MA20"))
        ma20_prev = prev.get("ma20")
        if ma20_prev is not None:
            if ma5 > ma20 and ma20 >= ma20_prev:
                hits.append((10, "均線多排上彎"))
            elif ma5 < ma20 and ma20 <= ma20_prev:
                hits.append((-10, "均線空排下彎"))
        bias = close / ma20 - 1
        if bias >= _BIAS_LIMIT[timeframe]:
            hits.append((-4, "正乖離過大"))
        elif bias <= -_BIAS_LIMIT[timeframe]:
            hits.append((4, "負乖離過大"))

    k, d, k_prev, d_prev = now.get("kd_k"), now.get("kd_d"), prev.get("kd_k"), prev.get("kd_d")
    if k is None or d is None or k_prev is None or d_prev is None:
        missing.append("KD")
    else:
        if k > d and k_prev <= d_prev:
            hits.append((8, "KD 金叉"))
        elif k < d and k_prev >= d_prev:
            hits.append((-8, "KD 死叉"))
        elif k > d:
            hits.append((4, "KD 多頭排列"))
        else:
            hits.append((-4, "KD 空頭排列"))
        if k >= 80:
            hits.append((-3, "KD 高檔過熱"))
        elif k <= 20:
            hits.append((3, "KD 低檔"))

    hist, hist_prev = now.get("macd_hist"), prev.get("macd_hist")
    if hist is None or hist_prev is None:
        missing.append("MACD")
    elif hist >= 0:
        hits.append((8, "MACD 紅柱放大") if hist >= hist_prev else (3, "MACD 紅柱收斂"))
    else:
        hits.append((-8, "MACD 綠柱放大") if hist <= hist_prev else (-3, "MACD 綠柱收斂"))

    rsi = now.get("rsi_14")
    if rsi is None:
        missing.append("RSI")
    elif rsi >= 70:
        hits.append((-4, "RSI 過熱"))
    elif rsi <= 30:
        hits.append((4, "RSI 超賣"))
    elif rsi >= 50:
        hits.append((3, "RSI 守 50 上方"))
    else:
        hits.append((-3, "RSI 落在 50 下方"))
    return hits, missing


def _sentence(bias: Bias, hits: list[tuple[int, str]]) -> str:
    pros = [t for s, t in sorted(hits, key=lambda h: -abs(h[0])) if s > 0][:3]
    cons = [t for s, t in sorted(hits, key=lambda h: -abs(h[0])) if s < 0][:3]
    def but(items: list[str]) -> str:
        joined = "、".join(items)
        return f"，但{' ' if joined[0].isascii() else ''}{joined}"

    if bias == "bullish":
        return "、".join(pros) + (f"{but(cons)}，留意回檔" if cons else "，多方條件一致")
    if bias == "bearish":
        return "、".join(cons) + (f"{but(pros)}，留意反彈" if pros else "，空方條件一致")
    parts = [f"多方：{'、'.join(pros)}" if pros else "", f"空方：{'、'.join(cons)}" if cons else ""]
    return "多空互見，" + "；".join(p for p in parts if p) if any(parts) else "訊號不明顯，方向未定"


def _verdict(daily: pl.DataFrame, timeframe: Timeframe, as_of: str | None) -> TimeframeVerdict:
    bars = _bars(daily, timeframe)
    base = {"timeframe": timeframe, "label": _LABEL[timeframe], "bars": bars.height, "as_of": as_of}
    if bars.height < 2:
        return TimeframeVerdict(**base, status="data_insufficient", summary=f"{_LABEL[timeframe]}只有 {bars.height} 根 K 棒，資料不足以判斷。")
    panel = compute_taiwan_indicator_panel(bars).select("date", "ma5", "ma20", "rsi_14", "macd_hist")
    rows = _with_kd(bars).join(panel, on="date", how="left").sort("date").tail(2).to_dicts()
    hits, missing = _rules(rows[-1], rows[0], timeframe)
    if len(missing) > _MAX_MISSING:
        return TimeframeVerdict(**base, status="data_insufficient", missing=missing,
                                summary=f"{_LABEL[timeframe]}只有 {bars.height} 根 K 棒，指標尚未暖機，資料不足以判斷。")
    score = sum(s for s, _ in hits)
    bias: Bias = "bullish" if score >= _VERDICT_THRESHOLD else "bearish" if score <= -_VERDICT_THRESHOLD else "neutral"
    return TimeframeVerdict(**base, status="available", bias=bias, score=score,
                            summary=_sentence(bias, hits), missing=missing)


def build_timeframe_verdicts(daily: pl.DataFrame) -> list[TimeframeVerdict]:
    """daily 需含 date/open/high/low/close/volume，一檔股票的日 K。"""
    if daily.is_empty():
        return []
    as_of = str(daily.get_column("date").cast(pl.Utf8).max())[:10]
    return [_verdict(daily, tf, as_of) for tf in ("daily", "weekly", "monthly")]
