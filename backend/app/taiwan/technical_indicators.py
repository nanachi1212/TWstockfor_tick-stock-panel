"""Market-neutral technical indicators computed from Taiwan daily OHLCV.

Phase 8B-5.0.5. Pure numerical formulas only — no A-share semantics baked in
(no ST/board/一字板/炸板/連板/limit-up logic, no SH/SZ/BJ exchange
assumptions, no A-share holiday/calendar assumptions). Input must come from
``TaiwanDailyStore`` (never the legacy A-share enriched cache), output never
gets written back anywhere — this module is pure compute.

Formulas deliberately mirror the app-wide convention already used for A-share
in ``backend/app/indicators/pipeline.py`` (and, for MACD, also
``backend/app/backtest/matrix.py``), so a single shared Watchlist column name
(``ma5``, ``rsi_14``, ``macd_dif``, ...) means the same thing regardless of
which market a row belongs to:

  - MA / volume MA: simple rolling mean (pipeline.py ``rolling_mean``).
  - Momentum:  ``close / close.shift(n) - 1`` — a fraction, e.g. 0.05 = 5%
    (pipeline.py: "momentum_5d: 5日动量(涨跌幅小数)").
  - RSI:  Wilder's smoothed RSI — EMA(gain)/EMA(loss) with alpha=1/14,
    adjust=False (pipeline.py ``rsi_14``). This is a *different* formula
    from the simple SMA-based RSI already used in
    ``app/taiwan/screener.py::_compute_batch_indicators`` — that is a
    pre-existing, separate divergence in this repo and is not touched here.
    Wilder's formula was chosen for this module because the Watchlist
    response merges Taiwan and legacy A-share rows under the *same*
    ``rsi_14`` column (Phase 8B-5.0.4's dispatcher) — using the same
    formula as pipeline.py keeps that shared column's meaning consistent
    across both markets in one table. See Phase 8B-5.0.5 report §F.
  - MACD: EMA(12) - EMA(26) = dif; EMA(dif, 9) = dea; (dif - dea) * 2 = hist
    (pipeline.py and backtest/matrix.py both use the same 12/26/9 spans).

Insufficient history -> ``null``, never ``0``/``NaN``/``Infinity``/a faked
warm-up value. EMA-based indicators (RSI, MACD) are additionally gated by a
minimum bar count so a 3-day-old symbol doesn't get an EMA number that's
still almost entirely warm-up bias, not because polars can't compute one.
"""
from __future__ import annotations

import polars as pl

# 需要 14 次涨跌 diff => 至少 15 根收盘价才不算"纯 warm-up"。
MIN_BARS_RSI_14 = 15
# 慢线 EMA(26) 的常规经验暖机长度 + 信号线 EMA(9) 再暖机一轮。
MIN_BARS_MACD = 35

_OUTPUT_COLS = (
    "symbol", "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma10",
    "rsi_14", "macd_dif", "macd_dea", "macd_hist", "momentum_5d", "momentum_20d",
)

_EMPTY_SCHEMA = {
    "symbol": pl.Utf8,
    **{c: pl.Float64 for c in _OUTPUT_COLS if c != "symbol"},
}


def _ema_alpha(span: int) -> float:
    return 2.0 / (span + 1.0)


def compute_taiwan_daily_indicators(history: pl.DataFrame) -> pl.DataFrame:
    """算出每个 symbol 最新一天的技术指标快照。

    history: 至少含 symbol/date/close/volume 列的日线历史 (通常是
    TaiwanDailyStore.read_range() 一次批量取回的多 symbol 数据, 由调用方
    决定回看窗口 —— 本函数不读盘, 不打网络, 纯计算)。

    返回: 每个 symbol 一行 (该 symbol 历史中日期最大的一天), 含
    ma5/ma10/ma20/ma60/vol_ma5/vol_ma10/rsi_14/macd_dif/macd_dea/macd_hist/
    momentum_5d/momentum_20d。某 symbol 历史不足以支撑某项指标时, 该欄位为
    None —— 不是 0, 不是 NaN, 不是提前暖机的假数字。
    """
    if history.is_empty():
        return pl.DataFrame(schema=_EMPTY_SCHEMA)

    df = history.sort(["symbol", "date"])
    df = df.with_columns(pl.len().over("symbol").alias("_bar_count"))

    df = df.with_columns([
        pl.col("close").rolling_mean(5, min_samples=5).over("symbol").alias("ma5"),
        pl.col("close").rolling_mean(10, min_samples=10).over("symbol").alias("ma10"),
        pl.col("close").rolling_mean(20, min_samples=20).over("symbol").alias("ma20"),
        pl.col("close").rolling_mean(60, min_samples=60).over("symbol").alias("ma60"),
        pl.col("volume").rolling_mean(5, min_samples=5).over("symbol").alias("vol_ma5"),
        pl.col("volume").rolling_mean(10, min_samples=10).over("symbol").alias("vol_ma10"),
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0).alias("momentum_5d"),
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).alias("momentum_20d"),
    ])

    # RSI 14 (Wilder smoothing) — 与 pipeline.py 的 rsi_14 公式一致。
    delta = pl.col("close").diff().over("symbol")
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    avg_gain = gain.ewm_mean(alpha=1.0 / 14.0, adjust=False).over("symbol")
    avg_loss = loss.ewm_mean(alpha=1.0 / 14.0, adjust=False).over("symbol")
    rsi_raw = 100.0 - 100.0 / (
        1.0 + avg_gain / pl.when(avg_loss == 0).then(1e-12).otherwise(avg_loss)
    )
    df = df.with_columns(
        pl.when(pl.col("_bar_count") >= MIN_BARS_RSI_14)
        .then(rsi_raw)
        .otherwise(None)
        .alias("rsi_14")
    )

    # MACD(12, 26, 9) — 与 pipeline.py / backtest/matrix.py 一致的参数。
    ema12 = pl.col("close").ewm_mean(alpha=_ema_alpha(12), adjust=False).over("symbol")
    ema26 = pl.col("close").ewm_mean(alpha=_ema_alpha(26), adjust=False).over("symbol")
    df = df.with_columns((ema12 - ema26).alias("_macd_dif_raw"))
    dea_raw = pl.col("_macd_dif_raw").ewm_mean(alpha=_ema_alpha(9), adjust=False).over("symbol")
    df = df.with_columns(dea_raw.alias("_macd_dea_raw"))
    df = df.with_columns([
        pl.when(pl.col("_bar_count") >= MIN_BARS_MACD)
        .then(pl.col("_macd_dif_raw")).otherwise(None).alias("macd_dif"),
        pl.when(pl.col("_bar_count") >= MIN_BARS_MACD)
        .then(pl.col("_macd_dea_raw")).otherwise(None).alias("macd_dea"),
        pl.when(pl.col("_bar_count") >= MIN_BARS_MACD)
        .then((pl.col("_macd_dif_raw") - pl.col("_macd_dea_raw")) * 2.0)
        .otherwise(None).alias("macd_hist"),
    ])

    latest = (
        df.with_columns(pl.col("date").max().over("symbol").alias("_max_date"))
        .filter(pl.col("date") == pl.col("_max_date"))
        .select(list(_OUTPUT_COLS))
    )

    # sanitize NaN/Inf -> null (防御性; 上面的除法已用 1e-12 挡掉除 0, 理论上
    # 不该再产生 NaN/Inf, 但 API 响应绝不能带 JSON 不合法的 NaN/Infinity)。
    float_cols = [c for c in latest.columns if c != "symbol"]
    latest = latest.with_columns([
        pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite())
        .then(None)
        .otherwise(pl.col(c))
        .alias(c)
        for c in float_cols
    ])
    return latest
