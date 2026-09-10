"""Phase 8B-5.0.5 — Taiwan market-neutral technical indicator unit tests.

Deterministic fixed close/volume sequences; reference values computed by an
independent, straightforward Python implementation of the exact same
documented formula (Wilder RSI, MACD 12/26/9, plain rolling mean), not by
calling the module under test twice. Tolerance-based float comparison, never
"just assert not null".
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.technical_indicators import (
    MIN_BARS_MACD,
    MIN_BARS_RSI_14,
    compute_taiwan_daily_indicators,
)

N_DAYS = 70


def _make_closes(n: int) -> list[float]:
    """确定性但非单调的收盘价序列 (无随机种子, 结果可重现)。"""
    return [round(100.0 + i * 0.5 + 3.0 * math.sin(i / 3.0), 4) for i in range(n)]


def _make_volumes(n: int) -> list[float]:
    return [round(1_000_000 + i * 5_000 + 20_000 * math.sin(i / 4.0), 1) for i in range(n)]


def _make_history(symbol: str, closes: list[float], volumes: list[float]) -> pl.DataFrame:
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(len(closes))]
    return pl.DataFrame({
        "symbol": [symbol] * len(closes),
        "date": dates,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": volumes,
        "amount": [c * v for c, v in zip(closes, volumes)],
    })


# ===== 独立参考实现 (与 technical_indicators.py 的公式文档一一对应) =====

def _ref_ema(values: list[float], alpha: float) -> list[float]:
    out: list[float] = []
    prev: float | None = None
    for v in values:
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def _ref_rsi14(closes: list[float]) -> list[float]:
    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = _ref_ema(gains, 1.0 / 14.0)
    avg_loss = _ref_ema(losses, 1.0 / 14.0)
    rsi = []
    for g, l in zip(avg_gain, avg_loss):
        l = l if l != 0 else 1e-12
        rsi.append(100.0 - 100.0 / (1.0 + g / l))
    return rsi


def _ref_macd(closes: list[float]) -> tuple[list[float], list[float], list[float]]:
    ema12 = _ref_ema(closes, 2.0 / 13.0)
    ema26 = _ref_ema(closes, 2.0 / 27.0)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ref_ema(dif, 2.0 / 10.0)
    hist = [(d - s) * 2.0 for d, s in zip(dif, dea)]
    return dif, dea, hist


def _ref_rolling_mean(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


# ===== MA =====

def test_ma5_ma10_ma20_ma60_match_reference():
    closes = _make_closes(N_DAYS)
    volumes = _make_volumes(N_DAYS)
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]

    assert out["ma5"] == pytest.approx(_ref_rolling_mean(closes, 5))
    assert out["ma10"] == pytest.approx(_ref_rolling_mean(closes, 10))
    assert out["ma20"] == pytest.approx(_ref_rolling_mean(closes, 20))
    assert out["ma60"] == pytest.approx(_ref_rolling_mean(closes, 60))


def test_volume_ma5_ma10_match_reference():
    closes = _make_closes(N_DAYS)
    volumes = _make_volumes(N_DAYS)
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]

    assert out["vol_ma5"] == pytest.approx(_ref_rolling_mean(volumes, 5))
    assert out["vol_ma10"] == pytest.approx(_ref_rolling_mean(volumes, 10))


# ===== RSI14 (Wilder) =====

def test_rsi14_matches_wilder_reference():
    closes = _make_closes(N_DAYS)
    volumes = _make_volumes(N_DAYS)
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]

    expected_rsi = _ref_rsi14(closes)[-1]
    assert out["rsi_14"] == pytest.approx(expected_rsi, abs=1e-6)
    # sanity: RSI 必须落在 [0, 100]
    assert 0.0 <= out["rsi_14"] <= 100.0


# ===== MACD(12, 26, 9) =====

def test_macd_dif_dea_hist_match_reference():
    closes = _make_closes(N_DAYS)
    volumes = _make_volumes(N_DAYS)
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]

    dif, dea, hist = _ref_macd(closes)
    assert out["macd_dif"] == pytest.approx(dif[-1], abs=1e-6)
    assert out["macd_dea"] == pytest.approx(dea[-1], abs=1e-6)
    assert out["macd_hist"] == pytest.approx(hist[-1], abs=1e-6)
    # hist 定义恒等式: hist == (dif - dea) * 2
    assert out["macd_hist"] == pytest.approx((out["macd_dif"] - out["macd_dea"]) * 2.0)


# ===== Momentum =====

def test_momentum_5d_20d_are_fractions_not_percentages():
    closes = _make_closes(N_DAYS)
    volumes = _make_volumes(N_DAYS)
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]

    expected_5d = closes[-1] / closes[-6] - 1.0
    expected_20d = closes[-1] / closes[-21] - 1.0
    assert out["momentum_5d"] == pytest.approx(expected_5d)
    assert out["momentum_20d"] == pytest.approx(expected_20d)
    # 契约是小数 (0.0x), 不是百分比数值(x.x) —— 与 pipeline.py momentum_5d 一致,
    # 用量级粗略断言防止再犯 Phase 8B-5.0.4 抓到的 change_pct 单位错误。
    assert abs(out["momentum_5d"]) < 1.0
    assert abs(out["momentum_20d"]) < 1.0


# ===== Insufficient history =====

def test_insufficient_history_returns_null_not_zero_or_nan():
    """只有 3 天数据: 所有指标都应是 None, 不是 0 / NaN / 假暖机值。"""
    closes = _make_closes(3)
    volumes = _make_volumes(3)
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]

    for field in ("ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma10",
                  "rsi_14", "macd_dif", "macd_dea", "macd_hist",
                  "momentum_5d", "momentum_20d"):
        assert out[field] is None, f"{field} 应为 None (仅 3 天历史), 实际: {out[field]}"


def test_partial_history_only_fills_supported_indicators():
    """12 天数据: ma5/ma10 应有值, ma20/ma60/rsi14/macd 仍应为 None。"""
    closes = _make_closes(12)
    volumes = _make_volumes(12)
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]

    assert out["ma5"] is not None
    assert out["ma10"] is not None
    assert out["ma20"] is None
    assert out["ma60"] is None
    assert out["rsi_14"] is None, "12 天 < MIN_BARS_RSI_14, 不该给 RSI"
    assert out["macd_dif"] is None, "12 天 < MIN_BARS_MACD, 不该给 MACD"
    assert out["momentum_5d"] is not None
    assert out["momentum_20d"] is None, "只有 12 天, close.shift(20) 必为 null"


def test_min_bars_thresholds_are_as_documented():
    """RSI/MACD 的最小根数门槛应与模块常量一致(回归保护, 避免悄悄改动)。"""
    assert MIN_BARS_RSI_14 == 15
    assert MIN_BARS_MACD == 35


# ===== ETF / TPEx: 同一条 flow, 不因 instrument_type / exchange 分叉 =====

def test_etf_symbol_goes_through_same_indicator_flow():
    closes = _make_closes(N_DAYS)
    volumes = _make_volumes(N_DAYS)
    history = _make_history("0050.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]
    assert out["symbol"] == "0050.TWSE"
    assert out["ma20"] == pytest.approx(_ref_rolling_mean(closes, 20))
    assert out["rsi_14"] == pytest.approx(_ref_rsi14(closes)[-1], abs=1e-6)


def test_tpex_symbol_goes_through_same_indicator_flow():
    closes = _make_closes(N_DAYS)
    volumes = _make_volumes(N_DAYS)
    history = _make_history("6488.TPEX", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]
    assert out["symbol"] == "6488.TPEX"
    dif, dea, hist = _ref_macd(closes)
    assert out["macd_dif"] == pytest.approx(dif[-1], abs=1e-6)


# ===== 多 symbol 批次: 互不串号, 一次计算 =====

def test_batch_multiple_symbols_do_not_cross_contaminate():
    closes_a = _make_closes(N_DAYS)
    closes_b = [c * 1.5 for c in closes_a]
    volumes = _make_volumes(N_DAYS)
    hist_a = _make_history("2330.TWSE", closes_a, volumes)
    hist_b = _make_history("6488.TPEX", closes_b, volumes)
    combined = pl.concat([hist_a, hist_b], how="vertical")

    out = {row["symbol"]: row for row in compute_taiwan_daily_indicators(combined).to_dicts()}
    assert set(out) == {"2330.TWSE", "6488.TPEX"}
    assert out["2330.TWSE"]["ma20"] == pytest.approx(_ref_rolling_mean(closes_a, 20))
    assert out["6488.TPEX"]["ma20"] == pytest.approx(_ref_rolling_mean(closes_b, 20))
    assert out["2330.TWSE"]["ma20"] != out["6488.TPEX"]["ma20"]


def test_empty_history_returns_empty_dataframe_not_error():
    out = compute_taiwan_daily_indicators(pl.DataFrame(schema={
        "symbol": pl.Utf8, "date": pl.Date, "close": pl.Float64, "volume": pl.Float64,
    }))
    assert out.is_empty()


def test_no_nan_or_infinite_values_in_output():
    """公式的除 0 保护 (1e-12) 理论上不该产生 NaN/Inf, 这里做防御性回归。"""
    closes = _make_closes(N_DAYS)
    volumes = [0.0] * N_DAYS  # 零成交量边界情况
    history = _make_history("2330.TWSE", closes, volumes)
    out = compute_taiwan_daily_indicators(history).to_dicts()[0]
    for key, value in out.items():
        if isinstance(value, float):
            assert not math.isnan(value), f"{key} is NaN"
            assert not math.isinf(value), f"{key} is Infinite"
