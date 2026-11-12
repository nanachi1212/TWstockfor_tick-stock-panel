"""VCP 偵測單元測試（合成序列，不讀開發機資料、不連網）。"""
from __future__ import annotations

from datetime import date, timedelta

from app.taiwan.vcp.detect import MIN_BARS, analyze, rs_percentiles, vcp_pattern


def _series(depths: list[float]) -> list[dict]:
    """220 根上升趨勢 → 依 depths 做收縮（每段回檔後反彈到前高附近）→ 最後小幅上攻。"""
    closes = [40 + 60 * i / 219 for i in range(220)]  # 40 → 100
    high = 100.0
    for k, depth in enumerate(depths):
        low = high * (1 - depth / 100)
        down = 10 - 2 * k  # 收縮期間逐段變短，但都 >= 5 日
        closes += [high + (low - high) * (i + 1) / down for i in range(down)]
        nxt = high - 0.5 if k < len(depths) - 1 else low + (high - low) * 0.8
        up = 8
        closes += [low + (nxt - low) * (i + 1) / up for i in range(up)]
        high = nxt
    closes += [closes[-1]] * max(0, MIN_BARS + 5 - len(closes))  # 補足長度（平盤）
    start = date(2025, 1, 1)
    return [{"date": start + timedelta(days=i), "open": c, "high": c * 1.01, "low": c * 0.99,
             "close": c, "volume": 1_000_000.0} for i, c in enumerate(closes)]


def test_less_than_260_bars_produces_no_signal():
    bars = _series([20, 9, 4])[-(MIN_BARS - 1):]
    result = analyze(bars, rs_percentile=99)
    assert result["status"] == "insufficient_data"
    assert result["signal"] is False
    assert "composite_score" not in result


def test_standard_vcp_is_valid():
    bars = _series([20, 9, 4])
    vcp = vcp_pattern(list(reversed(bars)))
    assert vcp["valid_vcp"] is True, vcp
    assert vcp["num_contractions"] == 3
    depths = [c["depth_pct"] for c in vcp["contractions"]]
    assert depths == sorted(depths, reverse=True)

    result = analyze(bars, rs_percentile=90)
    assert result["status"] == "ok"
    assert result["valid_vcp"] is True
    assert result["pivot"] and result["stop"] and result["stop"] < result["price"] < result["pivot"]
    assert result["relative_strength"]["score"] == 90
    assert result["composite_score"] is not None


def test_non_decreasing_contractions_are_invalid():
    vcp = vcp_pattern(list(reversed(_series([10, 15]))))
    assert vcp["valid_vcp"] is False


def test_missing_rs_makes_composite_unavailable_not_zero():
    result = analyze(_series([20, 9, 4]))
    assert result["relative_strength"]["status"] == "BLOCKED"
    assert result["composite_score"] is None and result["rating"] is None
    assert result["signal"] is False


def test_no_trade_bar_fails_closed_instead_of_crashing():
    bars = _series([20, 9, 4])
    bars[-30] = {**bars[-30], "open": None, "high": None, "low": None, "close": None, "volume": 0.0}
    result = analyze(bars, rs_percentile=90)
    assert result["status"] == "invalid_data"
    assert result["signal"] is False


def test_rs_percentile_ties_and_small_population_cap():
    pct = rs_percentiles({"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0})
    assert pct["b"] == pct["c"] == 74  # 同值同名次；4 檔樣本封頂 74
    assert pct["a"] == 25
    big = rs_percentiles({str(i): float(i) for i in range(100)})
    assert big["99"] == 100 and big["0"] == 1



def test_pivot_is_tick_ceiled_from_unrounded_contraction_high():
    bars = _series([20, 9, 4])
    raw_pivot = vcp_pattern(list(reversed(bars)))["pivot_price"]
    k = 100.004 / raw_pivot  # 還原價常有小數第三位；先四捨五入會把樞紐壓到 100.0
    scaled = [{**b, **{c: b[c] * k for c in ("open", "high", "low", "close")}} for b in bars]
    result = analyze(scaled, rs_percentile=90)
    assert result["pivot"] == 100.5  # 100~500 元升降單位 0.5，樞紐向上對齊
    assert result["vcp_pattern"]["contractions"][-1]["high_price"] == 100.0  # 顯示才四捨五入
