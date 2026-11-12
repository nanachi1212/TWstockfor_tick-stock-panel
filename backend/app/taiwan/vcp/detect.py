"""VCP 偵測與 5 項加權評分（純計算，不讀檔、不連網）。

移植自 tradermonty/claude-trading-skills skills/vcp-screener（MIT，Copyright (c) 2026 TraderMonty，
見 NOTICE.md）。規則與門檻沿用原版 CLI 預設值；台股差異只做以下已確認的處理：

- 價格：呼叫端傳入 PIT 還原後的 OHLC（app.taiwan.adjust），本模組不自行還原。
- 成交量：本機日線單位是「股」；量能只用比值（量縮比、突破量倍數），與單位無關。
- 停損價向下、樞紐價向上對齊台股升降單位（沿用 app.taiwan.market_rules.TickSizeModel 的普通股級距）。
- RS：Minervini 加權報酬的全市場百分位（排序等同以加權指數為基準）；無法計算時不給分數，綜合分標為不可用。

內部序列沿用原版「最新在前」(index 0 = as_of 當日) 的慣例，避免改寫索引時出錯。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from itertools import pairwise
from typing import Any

from app.taiwan.market_rules import TickSizeModel

MIN_BARS = 260  # 原版抓 260 日歷史；不足就不產生訊號

# 原版 screen_vcp.py CLI 預設值
LOOKBACK_DAYS = 120
ATR_MULTIPLIER = 1.5
ATR_PERIOD = 14
MIN_CONTRACTION_DAYS = 5
MIN_CONTRACTIONS = 2
T1_DEPTH_MIN = 10.0
CONTRACTION_RATIO = 0.70
WIDE_AND_LOOSE_THRESHOLD = 15.0
BREAKOUT_VOLUME_RATIO = 1.5
EXT_THRESHOLD = 8.0
MAX_SMA200_EXTENSION = 50.0
TREND_MIN_SCORE = 85.0
STALE_RANGE_PCT = 1.0
MAX_ABOVE_PIVOT = 3.0
MAX_RISK = 15.0
# 原版 Pre-filter（美股）：股價 > 10、平均量 > 200,000。台股照原數值套用（元、股），是否合適 UNVERIFIED。
PREFILTER_MIN_PRICE = 10.0
PREFILTER_MIN_AVG_VOLUME = 200_000

RS_PERIODS = ((63, 0.40), (126, 0.20), (189, 0.20), (252, 0.20))
WEIGHTS = {"trend_template": 0.25, "contraction_quality": 0.25, "volume_pattern": 0.20,
           "pivot_proximity": 0.15, "relative_strength": 0.15}
RATINGS = ["No VCP", "Weak VCP", "Developing VCP", "Good VCP", "Strong VCP", "Textbook VCP"]
STATE_MAX_RATING = {"Invalid": "No VCP", "Damaged": "No VCP", "Overextended": "Weak VCP",
                    "Extended": "Developing VCP", "Early-post-breakout": "Strong VCP"}


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[:n]) / n if len(values) >= n else None


def _tick_floor(price: float) -> float:
    tick = TickSizeModel.get_tick_size(price)
    return round(math.floor(price / tick + 1e-9) * tick, 4)


def _tick_ceil(price: float) -> float:
    tick = TickSizeModel.get_tick_size(price)
    return round(math.ceil(price / tick - 1e-9) * tick, 4)


# ── Relative Strength（Minervini 加權報酬 → 全市場百分位） ───────────────
# 原版先算「個股 − SPY」的加權超額報酬，最後在候選股間做百分位重排（rank_relative_strength_universe）。
# 每檔都減同一個基準報酬不改變排序，所以直接對全市場個股的加權報酬排百分位，結果等同以加權指數為基準。

def weighted_return(closes: list[float]) -> float | None:
    """closes 為最新在前；需要 253 根（252 日報酬）才計算，不做原版的減權部分資料補算。"""
    if len(closes) <= RS_PERIODS[-1][0] or any(c <= 0 for c in closes[:RS_PERIODS[-1][0] + 1]):
        return None
    return sum((closes[0] - closes[p]) / closes[p] * 100 * w for p, w in RS_PERIODS)


def rs_percentiles(returns: dict[str, float]) -> dict[str, int]:
    """原版百分位算法：同值同名次，百分位 = 小於等於自己的檔數 / 總數；樣本少時封頂。"""
    ordered = sorted(returns.values())
    n = len(ordered)
    cap = 100 if n >= 20 else 94 if n >= 10 else 84 if n >= 5 else 74
    return {s: min(cap, round(bisect_right(ordered, v) / n * 100)) for s, v in returns.items()}


def relative_strength(percentile: int | None, weighted: float | None) -> dict[str, Any]:
    if percentile is None:
        return {"status": "BLOCKED", "score": None, "rs_percentile": None, "weighted_return_pct": None,
                "reason": "沒有全市場百分位（需由 run.py 以全市場加權報酬計算）"}
    score = next(sc for lo, sc in ((95, 100), (85, 90), (75, 80), (60, 70), (45, 60), (30, 50), (15, 40), (-1, 20))
                 if percentile >= lo)
    return {"status": "OK", "score": score, "rs_percentile": percentile,
            "weighted_return_pct": None if weighted is None else round(weighted, 2), "reason": None}


# ── Trend Template（7 點 Stage 2） ───────────────────────────────────────

def trend_template(closes: list[float], price: float, year_high: float, year_low: float,
                   rs_rank: int | None) -> dict[str, Any]:
    sma50, sma150, sma200 = _sma(closes, 50), _sma(closes, 150), _sma(closes, 200)
    sma200_prev = _sma(closes[22:], 200)
    criteria = {
        "c1_price_above_sma150_200": price > sma150 and price > sma200,
        "c2_sma150_above_sma200": sma150 > sma200,
        "c3_sma200_trending_up": sma200_prev is not None and sma200 > sma200_prev,
        "c4_price_above_sma50": price > sma50,
        "c5_25pct_above_52w_low": year_low > 0 and (price - year_low) / year_low * 100 >= 25,
        "c6_within_25pct_52w_high": year_high > 0 and (year_high - price) / year_high * 100 <= 25,
        # 原版以 RS 估計名次 > 70；這裡用全市場百分位（Minervini/IBD RS rating 的原始定義）
        "c7_rs_rank_above_70": rs_rank is not None and rs_rank > 70,
    }
    passed = sum(criteria.values())
    raw = min(100.0, round(passed * 14.3, 1))
    dist50 = (price - sma50) / sma50 * 100
    excess = dist50 - EXT_THRESHOLD
    penalty = 0 if excess < 0 else -20 if excess >= 17 else -15 if excess >= 10 else -10 if excess >= 4 else -5
    return {"score": max(0.0, raw + penalty), "raw_score": raw, "passed": raw >= TREND_MIN_SCORE,
            "criteria_passed": passed, "criteria": criteria, "extended_penalty": penalty,
            "sma50": round(sma50, 2), "sma150": round(sma150, 2), "sma200": round(sma200, 2)}


# ── VCP 收縮偵測 ─────────────────────────────────────────────────────────

def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    if len(highs) < period + 1:
        return 0.0
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(highs))]
    return sum(trs[-period:]) / period


def _zigzag(highs: list[float], lows: list[float], closes: list[float]) -> tuple[list, list]:
    """ATR*倍數的 ZigZag 轉折點；序列為時間正序。"""
    atr = _atr(highs, lows, closes, ATR_PERIOD)
    if atr <= 0:
        return [], []
    threshold = atr * ATR_MULTIPLIER
    ext_idx = max(range(min(ATR_PERIOD, len(highs))), key=lambda i: (highs[i], -i))
    ext_val, looking_high = highs[ext_idx], True
    sw_highs, sw_lows = [], []
    for i in range(ext_idx + 1, len(highs)):
        if looking_high:
            if highs[i] > ext_val:
                ext_idx, ext_val = i, highs[i]
            elif ext_val - lows[i] >= threshold:
                sw_highs.append((ext_idx, ext_val))
                looking_high, ext_idx, ext_val = False, i, lows[i]
        else:
            if lows[i] < ext_val:
                ext_idx, ext_val = i, lows[i]
            elif highs[i] - ext_val >= threshold:
                sw_lows.append((ext_idx, ext_val))
                looking_high, ext_idx, ext_val = True, i, highs[i]
    return sw_highs, sw_lows


def _window_swings(values: list[float], window: int, is_high: bool) -> list[tuple[int, float]]:
    """ZigZag 找不到轉折時的固定視窗備援。"""
    out = []
    for i in range(window, len(values) - window):
        neighbors = [values[i - j] for j in range(1, window + 1)] + [values[i + j] for j in range(1, window + 1)]
        if all(values[i] > v for v in neighbors) if is_high else all(values[i] < v for v in neighbors):
            out.append((i, values[i]))
    return out


def _contractions_from(start: tuple[int, float], sw_highs: list, sw_lows: list,
                       dates: list[str]) -> list[dict[str, Any]]:
    h1_val = start[1]
    cur_idx, cur_val = start
    out: list[dict[str, Any]] = []
    for _ in range(4):
        low = next(((i, v) for i, v in sw_lows if i > cur_idx), None)
        if low is None:
            break
        if low[0] - cur_idx < MIN_CONTRACTION_DAYS:
            # 太短就往後找夠長的低點，但不跨過下一個轉折高點
            boundary = next((i for i, _ in sw_highs if i > cur_idx), None)
            low = next(((i, v) for i, v in sw_lows
                        if i - cur_idx >= MIN_CONTRACTION_DAYS and (boundary is None or i <= boundary)
                        and i > cur_idx), None)
            if low is None:
                break
        if out and abs(cur_val - h1_val) / h1_val * 100 > 5:  # 後續高點須在 H1 的 5% 內
            break
        # 價格保留全精度：樞紐與停損要先依方向對齊升降單位，四捨五入只在輸出時做
        out.append({"label": f"T{len(out) + 1}", "high_idx": cur_idx, "high_price": cur_val,
                    "high_date": dates[cur_idx], "low_idx": low[0], "low_price": low[1],
                    "low_date": dates[low[0]], "depth_pct": round((cur_val - low[1]) / cur_val * 100, 2),
                    "duration_days": low[0] - cur_idx})
        nxt = next(((i, v) for i, v in sw_highs if i > low[0]), None)
        if nxt is None:
            break
        cur_idx, cur_val = nxt
    return out


def _validate(cs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(cs) < MIN_CONTRACTIONS:
        return {"valid": False, "issues": [f"收縮次數不足 {MIN_CONTRACTIONS}"], "contraction_ratios": []}
    issues, valid = [], True
    t1 = cs[0]["depth_pct"]
    if t1 < T1_DEPTH_MIN:
        issues.append(f"T1 深度 {t1:.1f}% < {T1_DEPTH_MIN}%")
        valid = False
    elif t1 > 35:
        issues.append(f"T1 深度 {t1:.1f}% > 35%（僅提示）")
    ratios = []
    for prev, cur in pairwise(cs):
        if prev["depth_pct"] > 0:
            r = cur["depth_pct"] / prev["depth_pct"]
            ratios.append(round(r, 3))
            if r > CONTRACTION_RATIO:
                issues.append(f"{cur['label']} 未遞減收縮：{cur['depth_pct']:.1f}% / {prev['depth_pct']:.1f}% = {r:.2f}")
                valid = False
    duration = cs[-1]["low_idx"] - cs[0]["high_idx"]
    if duration < 15:
        issues.append(f"型態僅 {duration} 日，需要至少 15 日")
        valid = False
    elif duration > 325:
        issues.append(f"型態 {duration} 日 > 325（僅提示）")
    return {"valid": valid, "issues": issues, "contraction_ratios": ratios, "t1_depth": t1}


def _score_vcp(cs: list[dict[str, Any]], v: dict[str, Any]) -> int:
    if not v["valid"]:
        return min(40, len(cs) * 15)
    score = 90 if len(cs) >= 4 else 80 if len(cs) == 3 else 60
    if cs[-1]["depth_pct"] < 5:
        score += 10
    if v["contraction_ratios"] and sum(v["contraction_ratios"]) / len(v["contraction_ratios"]) < 0.4:
        score += 10
    if v["t1_depth"] > 30:
        score -= 10
    return max(0, min(100, score))


def vcp_pattern(hist: list[dict[str, Any]]) -> dict[str, Any]:
    """hist 為最新在前；只看最近 LOOKBACK_DAYS 根。"""
    bars = list(reversed(hist[:LOOKBACK_DAYS]))
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    dates = [str(b["date"]) for b in bars]
    sw_highs, sw_lows = _zigzag(highs, lows, closes)
    if not (sw_highs and sw_lows):
        sw_highs, sw_lows = _window_swings(highs, 5, True), _window_swings(lows, 5, False)
    if not (sw_highs and sw_lows):
        return {"score": 0, "valid_vcp": False, "contractions": [], "num_contractions": 0,
                "pivot_price": None, "wide_and_loose": False, "validation": None,
                "error": "找不到足夠的轉折點"}
    best, best_key, best_v = [], (0, -1, 0), {"valid": False}
    for start in sorted(sw_highs, key=lambda x: x[1], reverse=True)[:3]:  # 多起點，取最佳
        cand = _contractions_from(start, sw_highs, sw_lows, dates)
        v = _validate(cand) if len(cand) >= MIN_CONTRACTIONS else {"valid": False}
        key = (int(v["valid"]), _score_vcp(cand, v) if len(cand) >= MIN_CONTRACTIONS else 0, len(cand))
        if key > best_key:
            best, best_key, best_v = cand, key, v
    pivot = best[-1]["high_price"] if best else sw_highs[-1][1]
    if len(best) < MIN_CONTRACTIONS:
        return {"score": 0, "valid_vcp": False, "contractions": best, "num_contractions": len(best),
                "pivot_price": pivot, "wide_and_loose": False, "validation": None,
                "error": f"收縮次數少於 {MIN_CONTRACTIONS}"}
    final = best[-1]
    return {"score": best_key[1], "valid_vcp": best_v["valid"], "contractions": best,
            "num_contractions": len(best), "pivot_price": pivot,
            "pattern_duration_days": final["low_idx"] - best[0]["high_idx"], "validation": best_v,
            "wide_and_loose": final["depth_pct"] > WIDE_AND_LOOSE_THRESHOLD and final["duration_days"] < 10,
            "error": None}


# ── 量能型態 ─────────────────────────────────────────────────────────────

def volume_pattern(hist: list[dict[str, Any]], pivot: float | None,
                   contractions: list[dict[str, Any]]) -> dict[str, Any]:
    """hist 為最新在前、長度等於 vcp_pattern 用的 lookback；收縮 idx 為時間正序。"""
    vols = [b["volume"] for b in hist]
    closes = [b["close"] for b in hist]
    n = len(vols)
    avg50 = sum(vols[:min(50, n)]) / min(50, n)
    if avg50 <= 0:
        return {"score": 0, "dry_up_ratio": None, "bar0_volume_ratio": None, "breakout_volume_detected": False,
                "error": "無成交量"}

    def span_avg(c: dict[str, Any]) -> float | None:
        a, b = sorted((n - 1 - c["low_idx"], n - 1 - c["high_idx"]))
        seg = vols[max(0, a):min(n, b + 1)]
        return sum(seg) / len(seg) if seg else None

    zone_b = vols[1:11]  # 排除 bar[0]（可能是突破日）
    recent = sum(zone_b) / len(zone_b)
    dry_up = recent / avg50
    score = 90 if dry_up < 0.30 else 75 if dry_up < 0.50 else 60 if dry_up < 0.70 else 40 if dry_up <= 1.0 else 20

    bar0_ratio = vols[0] / avg50
    breakout = bool(pivot and closes[0] > pivot and bar0_ratio >= BREAKOUT_VOLUME_RATIO)
    if breakout:
        score += 10
    up = down = 0
    for i in range(min(20, n - 1)):
        if vols[i] > avg50:
            up += closes[i] > closes[i + 1]
            down += closes[i] < closes[i + 1]
    net = up - down
    score += 10 if net > 3 else -10 if net < -3 else 0
    c_avgs = [a for a in (span_avg(c) for c in contractions) if a is not None]
    declining = len(c_avgs) >= 2 and all(x > y for x, y in pairwise(c_avgs))
    if declining:
        score += 10
    return {"score": max(0, min(100, score)), "dry_up_ratio": round(dry_up, 3),
            "avg_volume_50d_shares": int(avg50), "zone_b_avg_volume_shares": int(recent),
            "bar0_volume_ratio": round(bar0_ratio, 3), "breakout_volume_detected": breakout,
            "net_accumulation_20d": net, "contraction_volume_declining": declining, "error": None}


# ── 樞紐距離、停損、風險 ─────────────────────────────────────────────────

def pivot_proximity(price: float, pivot: float | None, last_low: float | None,
                    breakout_volume: bool) -> dict[str, Any]:
    if not pivot or pivot <= 0 or price <= 0:
        return {"score": 0, "pivot_price": None, "distance_from_pivot_pct": None,
                "stop_loss_price": None, "risk_pct": None, "trade_status": "NO PIVOT"}
    d =(price - pivot) / pivot * 100
    bands = ((20, 20, "OVEREXTENDED"), (10, 35, "EXTENDED - very high chase risk"),
             (5, 50, "EXTENDED - high chase risk"), (3, 65, "EXTENDED - moderate chase risk"),
             (0, 90, "ABOVE PIVOT (within 3%)"))
    score, status = next(((s, t) for lo, s, t in bands if d > lo), (None, None))
    if score is None:
        below = ((-2, 90, "AT PIVOT"), (-5, 75, "NEAR PIVOT"), (-8, 60, "APPROACHING"),
                 (-10, 45, "DEVELOPING"), (-15, 30, "EARLY"))
        score, status = next(((s, t) for lo, s, t in below if d >= lo), (10, "FAR FROM PIVOT"))
    if breakout_volume and 0 < d <= 5:
        score += 10
        status = "BREAKOUT CONFIRMED" if d <= 3 else status + " (vol confirmed)"
    stop = risk = None
    if last_low and last_low > 0:
        stop = _tick_floor(last_low * 0.99)  # 原版：最後收縮低點下方 1%
        if price > stop:
            risk = round((price - stop) / price * 100, 2)
        else:
            status, score = "BELOW STOP LEVEL", 0
    return {"score": score, "pivot_price": pivot, "distance_from_pivot_pct": round(d, 2),
            "stop_loss_price": stop, "risk_pct": risk, "trade_status": status}


# ── 執行狀態、型態分類、綜合分數 ─────────────────────────────────────────

def execution_state(dist: float | None, price: float, sma50: float, sma200: float,
                    last_low: float | None, breakout_volume: bool) -> tuple[str, str]:
    sma200_dist = (price - sma200) / sma200 * 100
    if price < sma50 < sma200:
        return "Invalid", "股價 < SMA50 < SMA200"
    if last_low and price < last_low:
        return "Damaged", "跌破最後收縮低點"
    if price < sma50:
        return "Damaged", "跌破 SMA50"
    if sma200_dist > MAX_SMA200_EXTENSION:
        return "Overextended", f"高於 SMA200 {sma200_dist:.1f}%"
    if dist is None:
        return "Pre-breakout", "無樞紐價"
    if dist > 10:
        return "Overextended", f"高於樞紐 {dist:.1f}%"
    if dist > 5:
        return "Extended", f"高於樞紐 {dist:.1f}%"
    if dist > 3:
        return "Early-post-breakout", f"高於樞紐 {dist:.1f}%"
    if dist >= 0:
        return ("Breakout", "突破且量能確認") if breakout_volume else ("Early-post-breakout", "突破但量能未確認")
    return "Pre-breakout", f"低於樞紐 {abs(dist):.1f}%"


def classify_pattern(valid: bool, n: int, final_depth: float | None, state: str,
                     dry_up: float | None, wide_and_loose: bool) -> str:
    if state in ("Invalid", "Damaged"):
        return "Damaged"
    if state in ("Overextended", "Extended"):
        return "Extended Leader" if valid else "VCP-adjacent"
    if state in ("Early-post-breakout", "Breakout"):
        return "Post-breakout" if valid else "VCP-adjacent"
    if not valid or wide_and_loose:
        return "VCP-adjacent"
    textbook = (n >= 3 and (final_depth is None or final_depth <= 10.0)
                and dry_up is not None and dry_up <= 0.7 and state == "Pre-breakout")
    return "Textbook VCP" if textbook else "VCP-adjacent"


def composite(scores: dict[str, float | None], valid: bool, state: str, wide_and_loose: bool) -> dict[str, Any]:
    if any(v is None for v in scores.values()):  # 缺分項不當 0 分，綜合分直接標不可用
        return {"composite_score": None, "quality_rating": None, "rating": None, "state_cap_applied": False,
                "cap_reason": None, "component_scores": scores}
    total = round(sum(scores[k] * w for k, w in WEIGHTS.items()), 1)
    rating = next(r for lo, r in ((90, "Textbook VCP"), (80, "Strong VCP"), (70, "Good VCP"),
                                  (60, "Developing VCP"), (50, "Weak VCP"), (-1, "No VCP")) if total >= lo)
    if not valid and total >= 70:
        rating = "Developing VCP"
    quality_rating, reasons = rating, []
    cap = STATE_MAX_RATING.get(state)
    if cap and RATINGS.index(rating) > RATINGS.index(cap):
        reasons.append(f"執行狀態 {state}：{rating} → {cap}")
        rating = cap
    if wide_and_loose and rating in ("Textbook VCP", "Strong VCP", "Good VCP"):
        reasons.append(f"寬鬆型態：{rating} → Developing VCP")
        rating = "Developing VCP"
    return {"composite_score": total, "quality_rating": quality_rating, "rating": rating,
            "state_cap_applied": bool(reasons), "cap_reason": " | ".join(reasons) or None,
            "component_scores": scores}


# ── 單檔分析入口 ─────────────────────────────────────────────────────────

def analyze(bars: list[dict[str, Any]], rs_percentile: int | None = None,
            data_issues: list[str] | None = None) -> dict[str, Any]:
    """bars：時間正序、有成交的日線（date/open/high/low/close/volume，價格已 PIT 還原）。

    rs_percentile 由呼叫端以全市場 weighted_return() 經 rs_percentiles() 算出；沒給時 RS 為 BLOCKED、
    綜合分不可用。signal 只有在資料無疑慮、RS 可用、通過 Pre-filter 與 Trend Template、非停滯股、
    且符合原版 entry_ready 條件時才為 True。
    """
    issues = list(data_issues or [])
    base: dict[str, Any] = {"bars": len(bars), "as_of": str(bars[-1]["date"]) if bars else None,
                            "signal": False, "data_issues": issues}
    if len(bars) < MIN_BARS:
        return {**base, "status": "insufficient_data",
                "reason": f"只有 {len(bars)} 根日線，需要至少 {MIN_BARS}"}
    if any(b[k] is None or not b[k] > 0 for b in bars for k in ("open", "high", "low", "close"))             or any(b["volume"] is None or b["volume"] < 0 for b in bars):
        return {**base, "status": "invalid_data", "reason": "視窗內有空值或非正數的價格/成交量"}

    hist = list(reversed(bars))
    closes = [b["close"] for b in hist]
    price = closes[0]
    year = hist[:252]
    year_high, year_low = max(b["high"] for b in year), min(b["low"] for b in year)
    avg_vol50 = sum(b["volume"] for b in hist[:50]) / 50

    prefilter = (price > PREFILTER_MIN_PRICE and avg_vol50 >= PREFILTER_MIN_AVG_VOLUME
                 and year_low > 0 and (price - year_low) / year_low >= 0.20
                 and year_high > 0 and (year_high - price) / year_high <= 0.30)
    ranges = [(b["high"] - b["low"]) / b["close"] * 100 for b in hist[:10] if b["close"] > 0]
    stale = bool(ranges) and sum(ranges) / len(ranges) < STALE_RANGE_PCT

    rs = relative_strength(rs_percentile, weighted_return(closes))
    tt = trend_template(closes, price, year_high, year_low, rs["rs_percentile"])
    vcp = vcp_pattern(hist)
    # 樞紐價向上對齊升降單位（還原後的歷史高點可能不在 tick 上）；量能與距離都用同一個值
    pivot = _tick_ceil(vcp["pivot_price"]) if vcp["pivot_price"] else None
    vol = volume_pattern(hist[:LOOKBACK_DAYS], pivot, vcp["contractions"])
    last_low = vcp["contractions"][-1]["low_price"] if vcp["contractions"] else None
    piv = pivot_proximity(price, pivot, last_low, vol["breakout_volume_detected"])
    state, state_reason = execution_state(piv["distance_from_pivot_pct"], price, tt["sma50"],
                                          tt["sma200"], last_low, vol["breakout_volume_detected"])
    final_depth = vcp["contractions"][-1]["depth_pct"] if vcp["contractions"] else None
    pattern = classify_pattern(vcp["valid_vcp"], vcp["num_contractions"], final_depth, state,
                               vol["dry_up_ratio"], vcp["wide_and_loose"])
    comp = composite({"trend_template": tt["score"], "contraction_quality": vcp["score"],
                      "volume_pattern": vol["score"], "pivot_proximity": piv["score"],
                      "relative_strength": rs["score"]}, vcp["valid_vcp"], state, vcp["wide_and_loose"])

    dist, risk = piv["distance_from_pivot_pct"], piv["risk_pct"]
    entry_ready = (state not in ("Invalid", "Damaged", "Overextended", "Extended", "Early-post-breakout")
                   and vcp["valid_vcp"] and dist is not None and -8.0 <= dist <= MAX_ABOVE_PIVOT
                   and vol["dry_up_ratio"] is not None and vol["dry_up_ratio"] <= 1.0
                   and piv["trade_status"] != "BELOW STOP LEVEL"
                   and risk is not None and 0 < risk <= MAX_RISK)
    gates = {"prefilter_passed": prefilter, "trend_template_passed": tt["passed"], "stale_price": stale}
    if rs["status"] != "OK":
        issues.append("RS 不可用：" + rs["reason"])
    signal = not issues and prefilter and tt["passed"] and not stale and entry_ready
    shown = {**vcp, "pivot_price": pivot, "contractions": [
        {**c, "high_price": round(c["high_price"], 2), "low_price": round(c["low_price"], 2)}
        for c in vcp["contractions"]]}
    return {**base, "status": "data_unverified" if issues else "ok", "signal": signal,
            "price": round(price, 2), "composite_score": comp["composite_score"], "rating": comp["rating"],
            "quality_rating": comp["quality_rating"], "state_cap_applied": comp["state_cap_applied"],
            "cap_reason": comp["cap_reason"], "execution_state": state, "execution_state_reason": state_reason,
            "pattern_type": pattern, "valid_vcp": vcp["valid_vcp"], "entry_ready": entry_ready,
            "pivot": piv["pivot_price"], "stop": piv["stop_loss_price"], "risk_pct": risk,
            "distance_from_pivot_pct": dist, "trade_status": piv["trade_status"], "gates": gates,
            "component_scores": comp["component_scores"], "trend_template": tt, "vcp_pattern": shown,
            "volume_pattern": vol, "relative_strength": rs,
            "year_high": round(year_high, 2), "year_low": round(year_low, 2),
            "avg_volume_50d_shares": int(avg_vol50)}
