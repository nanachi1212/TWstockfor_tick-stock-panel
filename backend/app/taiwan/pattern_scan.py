"""Chart pattern scan (型態選股), ported from EquiLens 衡析 patterns.mjs (MIT).

Ten classic patterns are detected over the latest 90 research sessions on front-adjusted OHLC,
then scored (0-100) and given a conditional plan. Entry, stop and target are expressed in raw
price levels: the adjusted series is rescaled by raw_close / adjusted_close on the last session.
Volume and liquidity always use raw shares. Every pivot is confirmed inside the window, so the
scan reads no row after the target session.

A stock is listed only when all 90 raw and adjusted sessions line up. Missing sessions, invalid
OHLC, low liquidity and unverified corporate actions are counted by reason, never filled with 0.
Current-universe view, not a Historical PIT evaluation.
"""
# ruff: noqa: RUF001, RUF003 -- user-facing Traditional Chinese labels and rule text.
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from datetime import date
from typing import Any, NamedTuple

import polars as pl

from app.taiwan.adjust import adjust_prices_as_of, volume_window_status
from app.taiwan.corporate_actions import CorporateActionEvent, CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.market_rules import TickSizeModel
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.research_metrics import research_sessions
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

WINDOW = 90
LOT_SIZE = 1000
MIN_AVG_VOLUME_LOTS = 300
# Industries whose price structure does not follow these patterns; substring match as in 衡析.
EXCLUDED_INDUSTRIES = ("金融", "水泥", "造紙", "紡織", "運動休閒", "貿易百貨", "油電燃氣", "文化創意")
PRICE_COLUMNS = ("open", "high", "low", "close")
PATTERNS = (
    ("ihs", "頭肩底", "反轉型"), ("double", "雙底", "反轉型"), ("round", "圓底", "反轉型"),
    ("ascending", "上升三角形", "整理突破型"), ("wedge", "下降楔形", "反轉型"),
    ("flag", "旗形", "延續型"), ("pennant", "旗竿三角形", "延續型"), ("cup", "杯柄", "延續型"),
    ("triple", "三重底", "反轉型"), ("base", "突破平台", "整理突破型"),
)
PATTERN_NAMES = {pid: name for pid, name, _ in PATTERNS}
PATTERN_CATEGORIES = {pid: category for pid, _, category in PATTERNS}
RULES = {"window": WINDOW, "lot_size": LOT_SIZE, "min_avg_volume_lots": MIN_AVG_VOLUME_LOTS,
         "excluded_industries": list(EXCLUDED_INDUSTRIES), "rr_threshold": 1.5, "cost_rate": 0.006}


class Fit(NamedTuple):
    slope: float
    at: Any
    error: float


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _js_round(value: float) -> int:
    # JavaScript Math.round rounds halves upward; Python's round() rounds to even.
    return math.floor(value + 0.5)


def _fit(points: list[tuple[int, float]]) -> Fit:
    x = _mean([i for i, _ in points])
    y = _mean([v for _, v in points])
    den = sum((i - x) ** 2 for i, _ in points)
    slope = sum((i - x) * (v - y) for i, v in points) / den if den else 0.0
    error = math.sqrt(_mean([(v - y - slope * (i - x)) ** 2 for i, v in points]))
    return Fit(slope, lambda i: y + slope * (i - x), error)


def _slope(values: list[float]) -> float:
    return _fit(list(enumerate(values))).slope


def _pivots(bars: list[dict], key: str, low: bool = False) -> list[dict]:
    """Local extremes over ±3 bars; a later pivot must be at least 4 bars after the previous one."""
    out: list[dict] = []
    for i in range(3, len(bars) - 3):
        value = bars[i][key]
        neighbors = [bars[k][key] for k in range(i - 3, i + 4)]
        extreme = min(neighbors) if low else max(neighbors)
        if value == extreme and any(x != value for x in neighbors) and (not out or i - out[-1]["i"] >= 4):
            out.append({"i": i, "v": value})
    return out


def _span(bars: list[dict], start: int, end: int, key: str) -> list[float]:
    return [bar[key] for bar in bars[start:end + 1]]


def _tick_up(value: float) -> float:
    return TickSizeModel.round_order_price(value, mode="ceil")


def _tick_down(value: float) -> float:
    return TickSizeModel.round_order_price(value, mode="floor")


def detect_patterns(adjusted: list[dict], raw: list[dict], events: Iterable[CorporateActionEvent] = (),
                    symbol: str = "") -> list[dict]:
    """Patterns in the 90-session window ending on the last row. ``adjusted`` drives geometry; ``raw`` drives volume and plan levels.

    ``events`` are the symbol's corporate actions inside the window; share-count events gate the volume ratio.
    """
    events = tuple(events)
    if len(adjusted) != WINDOW or len(raw) != WINDOW:
        return []
    closes = [bar["close"] for bar in adjusted]
    last = adjusted[-1]
    lows = _pivots(adjusted, "low", low=True)
    highs = _pivots(adjusted, "high")
    atr = _mean([max(adjusted[i]["high"] - adjusted[i]["low"],
                     abs(adjusted[i]["high"] - adjusted[i - 1]["close"]),
                     abs(adjusted[i]["low"] - adjusted[i - 1]["close"])) for i in range(WINDOW - 14, WINDOW)])
    if not atr > 0:
        return []
    scale = raw[-1]["close"] / last["close"]
    candidates: list[dict] = []

    def add(pid: str, start: int, end: int, resistance: float, support: float, height: float, quality: float,
            description: str, anchors: list[dict] = (), line: Any = None, lower_line: Any = None) -> None:
        up = line or (lambda _i: resistance)
        if adjusted[end]["close"] > up(end) + atr * 0.15:
            return
        if not (resistance > support and height > 0) or start < 0 or end >= WINDOW - 1 or end - start < 10:
            return
        threshold = resistance + atr * 0.15
        # Invalidation is judged against the support in force on session k: the lower trend line
        # where one exists, otherwise the constant structural support.
        low = lower_line or (lambda _i: support)
        if any(adjusted[k]["close"] < low(k) - atr * 0.25 for k in range(end + 1, WINDOW)):
            return
        if last["close"] < resistance - atr * 2.5 or last["close"] > resistance + atr * 4:
            return
        cross = next((i for i in range(end + 1, WINDOW) if adjusted[i]["close"] > up(i) + atr * 0.15), -1)
        # False breakout: a close back below the neckline by 0.35 ATR after the first breakout removes the setup.
        # The search starts at the last pivot, so a breakout before `end` that failed is caught too.
        floor = max((a["i"] for a in anchors), default=start)
        first_break = next((i for i in range(floor, WINDOW) if adjusted[i]["close"] > up(i) + atr * 0.15), -1)
        if first_break >= 0 and any(adjusted[k]["close"] < up(k) - atr * 0.35 for k in range(first_break, WINDOW)):
            return
        index = cross if cross >= 0 else WINDOW - 1
        # Raw share volume is only comparable while the share count is unchanged. A stock dividend,
        # split or capital reduction inside the baseline->breakout window makes the ratio meaningless,
        # so volume confirmation and volume points are withheld rather than computed from mixed regimes.
        volume_status = volume_window_status(
            events, symbol=symbol, start_session=raw[max(0, index - 20)]["date"],
            end_session=raw[index]["date"], measure="relative_volume")
        ratio: float | None = None
        if volume_status == "verified":
            base_volume = _mean([bar["volume"] for bar in raw[max(0, index - 20):index]])
            ratio = raw[index]["volume"] / base_volume if base_volume > 0 else 0.0
        volume_ok = ratio is not None and ratio >= 1.3
        # The session after a breakout must hold the neckline; a last-session breakout has no such
        # session yet and stays pending instead.
        held_next = cross < 0 or cross >= WINDOW - 1 or adjusted[cross + 1]["close"] >= up(cross + 1)
        confirmed = cross >= 0 and cross < WINDOW - 1 and volume_ok and held_next and last["close"] > threshold
        pending_next = cross == WINDOW - 1 and volume_ok and last["close"] > threshold
        if confirmed:
            status = "量價確認"
        elif pending_next:
            status = "突破待次日確認"
        elif cross >= 0:
            status = "突破待量確認"
        else:
            status = "接近突破"
        volume_points = _clamp((ratio - 0.5) / 1.5 * 20, 0, 20) if ratio is not None else 0.0
        trend_points = 10 if last["close"] > _mean(closes[-20:]) else 0
        confirmation_points = 30 if confirmed else 18 if cross >= 0 else 8
        score = _js_round(_clamp(quality, 0, 40) + confirmation_points + volume_points + trend_points)

        entry = _tick_up((resistance + atr * 0.2) * scale)
        entry_max = _tick_up((resistance + atr * 0.75) * scale)
        stop = _tick_down((support - atr * 0.25) * scale)
        target = _tick_down((resistance + height) * scale)
        costs = entry_max * 0.006
        rr = (target - entry_max - costs) / (entry_max - stop + costs)
        chased = raw[-1]["close"] > entry_max
        if chased:
            action = "已超過進場帶，等待回測"
        elif pending_next:
            action = "等待次日守穩頸線"
        elif not confirmed:
            action = "等待收盤突破與量能確認"
        elif rr < RULES["rr_threshold"]:
            action = "報酬風險比不足，暫不進場"
        else:
            action = "量價已確認，仍需事件檢查"
        candidates.append({
            "id": pid, "name": PATTERN_NAMES[pid], "category": PATTERN_CATEGORIES[pid],
            "score": score, "status": status,
            "start": adjusted[start]["date"], "end": adjusted[end]["date"],
            "breakout_date": adjusted[cross]["date"] if cross >= 0 else None,
            "description": description,
            "anchors": [{"date": adjusted[a["i"]]["date"], "label": a["label"],
                         "price": round(a["v"] * scale, 2), "index": a["i"]} for a in anchors],
            "resistance": round(resistance * scale, 2), "support": round(support * scale, 2),
            "atr": round(atr * scale, 2),
            "volume_ratio": round(ratio, 2) if ratio is not None else None, "volume_status": volume_status,
            "score_parts": {"geometry": round(quality, 2), "confirmation": confirmation_points,
                            "volume": round(volume_points, 2), "trend": trend_points},
            "lines": [
                {"label": "壓力／頸線", "from": start, "to": WINDOW - 1,
                 "a": up(start) * scale, "b": resistance * scale},
                {"label": "支撐趨勢線" if lower_line else "結構支撐", "from": start, "to": WINDOW - 1,
                 "a": (lower_line(start) if lower_line else support) * scale, "b": support * scale},
            ],
            "plan": {
                "entry": entry, "entry_max": entry_max, "stop": stop, "target": target,
                "rr": round(rr, 2), "risk_percent": round((entry_max - stop) / entry_max * 100, 2),
                "chased": chased, "action": action,
                "confirmation": f"收盤高於 {round(threshold * scale, 2)}，突破日成交量至少為前 20 日均量 1.3 倍；次日須守住頸線。",
                "defense": f"收盤跌回頸線下方 0.35 ATR 視為假突破；結構防守 {stop} 為支撐下方 0.25 ATR。跳空可能超過預設損失。",
                "timing": "確認後先觀察首筆條件；回測不破再評估第二筆，總風險不增加。進場後 10 個交易日未延伸則重新評估，20 日仍盤整退出此計畫。",
                "target_basis": "頸線加型態高度的量度觀察位，非合理價或報酬保證；先核對前高賣壓，再考慮分段減碼。",
                "missing": "尚未核對法說、財報、除權息及重大事件；上述是條件式技術計畫。未提供帳戶風險額度，因此不推算適合張數。",
            },
            "warnings": [
                "量能視窗跨越股數變動事件，成交量不可比，不計量能確認與量能分數" if ratio is None
                else "突破量未達 1.3 倍，存在假突破風險" if ratio < 1.3 else "量能確認不保證延續",
                *(["突破次日收盤未守住頸線，尚未確認"] if not held_next else []),
                "價格已離開進場帶，不追價" if chased else "進場需等條件成立",
                "90 日只能辨識局部圓弧，不代表數月以上完整長底" if pid == "round" else "型態由數值規則辨識，請覆核圖形",
                "大盤趨勢、重大事件與缺口可能使型態失效",
            ],
        })

    for end in (84, 86, 88):
        ls = [x for x in lows if 8 <= x["i"] <= end - 3]
        hs = [x for x in highs if x["i"] <= end - 3]
        # Double and triple bottoms, plus head-and-shoulders built from three troughs.
        for count in (2, 3):
            for j in range(len(ls) - count + 1):
                pts = ls[j:j + count]
                first, last_low = pts[0], pts[-1]
                if any(pts[k]["i"] - pts[k - 1]["i"] < 10 for k in range(1, count)):
                    continue
                if last_low["i"] - first["i"] < 15 or end - last_low["i"] > 20:
                    continue
                necks = [max(_span(adjusted, pts[k]["i"], pts[k + 1]["i"], "high")) for k in range(count - 1)]
                neck = _mean(necks)
                bottom = min(p["v"] for p in pts)
                depth = neck - bottom
                similar = (max(p["v"] for p in pts) - bottom) / neck
                prior = max(_span(adjusted, max(0, first["i"] - 15), first["i"], "close"))
                if depth < atr * 2 or depth / neck > 0.45 or prior < first["v"] * 1.04:
                    continue
                if any(x - bottom < atr * 2 for x in necks):
                    continue
                if similar <= 0.035 and abs(necks[0] - necks[-1]) / neck < 0.04:
                    labels = [f"底 {k + 1}" for k in range(count)]
                    add("double" if count == 2 else "triple", first["i"] - 5, end, neck, bottom, depth,
                        40 - similar * 200,
                        f"{count} 個相隔至少 10 日的低點差距 {round(similar * 100, 1)}%；中間反彈形成頸線，底部深度 {round(depth / neck * 100, 1)}%。",
                        [{**p, "label": labels[k]} for k, p in enumerate(pts)])
                if count == 3:
                    a, b, c = pts
                    shoulder = abs(a["v"] - c["v"]) / neck
                    head = (min(a["v"], c["v"]) - b["v"]) / neck
                    if (shoulder < 0.045 and 0.025 < head < 0.22 and abs((b["i"] - a["i"]) - (c["i"] - b["i"])) < 20
                            and abs(necks[0] - necks[1]) / neck < 0.05):
                        add("ihs", a["i"] - 5, end, neck, c["v"], neck - b["v"], 38 - shoulder * 100,
                            "左肩、較低的頭部及右肩依序形成；兩次反彈高點近似頸線，右肩低點作為結構防守。",
                            [{**p, "label": label} for p, label in zip(pts, ["左肩", "頭", "右肩"], strict=True)])
        # Triangles, wedges, flags, pennants and rectangular bases over several window lengths.
        for length in (20, 30, 45, 60, 80):
            start = end - length + 1
            if start < 0:
                continue
            hp = [x for x in hs if x["i"] >= start]
            lp = [x for x in ls if x["i"] >= start]
            if (len(hp) >= 2 and len(lp) >= 2 and hp[-1]["i"] - hp[0]["i"] >= length * 0.4
                    and lp[-1]["i"] - lp[0]["i"] >= length * 0.4):
                upper = _fit([(x["i"], x["v"]) for x in hp])
                lower = _fit([(x["i"], x["v"]) for x in lp])
                u, l_ = upper.at(WINDOW - 1), lower.at(WINDOW - 1)
                width = upper.at(start) - lower.at(start)
                ending = upper.at(end) - lower.at(end)
                n = _mean(_span(adjusted, start, end, "close"))
                us = upper.slope * length / n
                lslope = lower.slope * length / n
                contained = sum(
                    1 for k in range(start, end + 1)
                    if adjusted[k]["close"] <= upper.at(k) + atr * 0.5 and adjusted[k]["close"] >= lower.at(k) - atr * 0.5
                ) / length
                if (width > atr * 2 and ending > atr * 0.7 and u > l_ and contained >= 0.85
                        and upper.error < atr and lower.error < atr):
                    anchors = [{**x, "label": "壓力觸點"} for x in hp] + [{**x, "label": "支撐觸點"} for x in lp]
                    common = (f"{length} 日整理，上緣變化 {round(us * 100, 1)}%、下緣變化 {round(lslope * 100, 1)}%；"
                              f"{round(contained * 100)}% 收盤落在通道內。")
                    if abs(us) < 0.025 and lslope > 0.03 and ending / width < 0.8:
                        add("ascending", start, end, u, l_, width, 36,
                            common + "水平壓力與抬高低點構成上升三角形。", anchors, upper.at, lower.at)
                    if us < -0.035 and lslope < -0.008 and upper.slope < lower.slope and ending / width < 0.8:
                        add("wedge", start, end, u, l_, width, 35,
                            common + "上下緣均下移，上緣下降較快，通道收斂。", anchors, upper.at, lower.at)
                    pole_start = max(0, start - 15)
                    pole = adjusted[start]["close"] - adjusted[pole_start]["close"]
                    pole_return = pole / adjusted[pole_start]["close"]
                    if (length <= 30 and start >= 12 and pole_return >= 0.12 and pole > atr * 4
                            and (max(_span(adjusted, start, end, "high")) - min(_span(adjusted, start, end, "low")))
                            < pole * 0.65):
                        if us < -0.008 and lslope < 0 and abs(us - lslope) < 0.035 and ending / width > 0.65:
                            add("flag", start, end, u, l_, pole, 37,
                                common + f"前段旗竿上漲 {round(pole_return * 100, 1)}%，回撤小於旗竿 65%，近似平行下降整理。",
                                anchors, upper.at, lower.at)
                        if us < -0.01 and lslope > 0.01 and ending / width < 0.75:
                            add("pennant", start, end, u, l_, pole, 37,
                                common + f"前段旗竿上漲 {round(pole_return * 100, 1)}%，上下緣向內收斂。",
                                anchors, upper.at, lower.at)
                    if length >= 30 and abs(us) < 0.025 and abs(lslope) < 0.025 and width / n < 0.18:
                        add("base", start, end, u, l_, width, 34,
                            common + "上下緣近似水平，至少 30 日箱型整理。", anchors, upper.at, lower.at)
            if length >= 45:
                # Five segment means enforce a broad bowl instead of a one-day V reversal.
                segment = length // 5
                means = [_mean(_span(adjusted, start + k * segment,
                                     end if k == 4 else start + (k + 1) * segment - 1, "close")) for k in range(5)]
                bottom = min(_span(adjusted, start, end, "low"))
                bottom_index = next(i for i in range(start, end + 1) if adjusted[i]["low"] == bottom)
                rim = max(means[0], means[4])
                depth = rim - bottom
                bowl = (means[0] > means[1] > means[2] and means[2] < means[3] < means[4]
                        and abs(means[0] - means[4]) / rim < 0.07 and 0.07 <= depth / rim <= 0.4
                        and start + length * 0.25 <= bottom_index <= start + length * 0.7)
                if (bowl and _slope(_span(adjusted, start, start + segment, "close")) < 0
                        and _slope(_span(adjusted, end - segment, end, "close")) > 0):
                    resistance = max(_span(adjusted, start, start + segment, "high")
                                     + _span(adjusted, end - segment, end, "high"))
                    add("round", start, end, resistance, min(_span(adjusted, end - segment, end, "low")),
                        resistance - bottom, 35,
                        f"{length} 日分成五段，均價先降後升；底部位於區間中段，深度 {round(depth / rim * 100, 1)}%，排除短促 V 形。",
                        [{"i": bottom_index, "v": bottom, "label": "圓弧底"}])
        # Cup ends before a separate 5–15-session shallow handle.
        for handle in (7, 12):
            for cup_length in (45, 60):
                rim_index = end - handle
                start = rim_index - cup_length
                if start < 0:
                    continue
                rim = _mean([adjusted[start]["close"], adjusted[rim_index]["close"]])
                bottom = min(_span(adjusted, start, rim_index, "low"))
                depth = rim - bottom
                q = cup_length // 4
                means = [_mean(_span(adjusted, start + k * q, start + (k + 1) * q - 1, "close")) for k in range(4)]
                handle_low = min(_span(adjusted, rim_index + 1, end, "low"))
                handle_high = max(_span(adjusted, rim_index + 1, end, "high"))
                if (abs(adjusted[start]["close"] - adjusted[rim_index]["close"]) / rim < 0.06
                        and 0.08 < depth / rim < 0.4 and means[0] > means[1] and means[3] > means[2]
                        and abs(means[1] - means[2]) < depth * 0.4 and handle_low > bottom + depth * 0.5
                        and atr * 0.4 < rim - handle_low < depth * 0.45 and handle_high < rim * 1.04
                        and _slope(_span(adjusted, rim_index + 1, end, "close")) < 0):
                    add("cup", start, end, handle_high, handle_low, depth, 38,
                        f"{cup_length} 日杯身兩端價差小於 6%；其後 {handle} 日把手回檔不超過杯深 45%，且位於杯身上半部。",
                        [{"i": start, "v": adjusted[start]["close"], "label": "左杯緣"},
                         {"i": rim_index, "v": adjusted[rim_index]["close"], "label": "右杯緣"},
                         {"i": end, "v": handle_low, "label": "把手支撐"}])

    best: list[dict] = []
    for pid, _, _ in PATTERNS:
        group = [c for c in candidates if c["id"] == pid]
        # Highest score wins; ties go to the most recent start, as in 衡析.
        group.sort(key=lambda c: c["start"], reverse=True)
        group.sort(key=lambda c: c["score"], reverse=True)
        best.extend(group[:1])
    best.sort(key=lambda c: c["score"], reverse=True)
    return best


def _valid_bar(bar: dict) -> bool:
    if any(bar[c] is None or not math.isfinite(bar[c]) for c in (*PRICE_COLUMNS, "volume")):
        return False
    return (all(bar[c] > 0 for c in PRICE_COLUMNS)
            and bar["high"] >= max(bar["open"], bar["close"]) and bar["low"] <= min(bar["open"], bar["close"])
            and bar["volume"] >= 0)


def _clean(value: Any) -> Any:
    """Plain JSON values only: no NaN or Infinity, dates as ISO strings."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


class TaiwanPatternScanService:
    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        evidence_store: ObservedUniverseStore | None = None,
        actions: CorporateActionStore | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.security_master = security_master or get_security_master()
        self.evidence_store = evidence_store or ObservedUniverseStore()
        self.actions = actions or CorporateActionStore()

    def snapshot(self, target_date: date | None = None) -> dict[str, Any]:
        target = target_date or resolve_target_latest_trading_date(self.calendar, evidence_store=self.evidence_store)
        available = set(self.daily_store.available_dates())
        out: dict[str, Any] = {
            "date": str(target), "from": None, "status": "unavailable", "reason": "no_daily_data",
            "price_semantics": "adjusted_for_pattern_raw_for_levels", "rules": RULES,
            "universe": 0, "eligible": 0, "matched": 0, "pattern_count": 0,
            "excluded": {}, "items": [],
        }
        if target not in available:
            return out
        sessions = research_sessions(target, WINDOW, available, self.calendar, self.evidence_store)
        if len(sessions) != WINDOW or sessions[-1] != target:
            out["reason"] = "insufficient_sessions"
            return out
        out["from"] = str(sessions[0])
        coverage = self.actions.read_verified_coverage()
        if coverage is None or coverage[0] > sessions[0] or coverage[1] < target:
            # Without verified corporate actions the adjusted window is unknown, so no pattern is trustworthy.
            out["reason"] = "corporate_action_coverage_unavailable"
            return out
        events_by_symbol: dict[str, list[CorporateActionEvent]] = {}
        for event in coverage[2]:
            if sessions[0] < event.effective_date <= target:
                events_by_symbol.setdefault(event.symbol, []).append(event)

        universe = self.security_master.to_dataframe(supported_only=True).filter(
            (pl.col("instrument_type") == "stock") & (pl.col("listing_status") == "active")
            & pl.col("exchange").is_in(["TWSE", "TPEX"]) & pl.col("code").str.contains(r"^[1-9]\d{3}$")
        )
        meta: dict[str, dict] = {}
        excluded: Counter[str] = Counter()
        universe_rows = universe.to_dicts()
        out["universe"] = len(universe_rows)
        for row in universe_rows:
            industry = (row["industry"] or "").strip()
            if not industry or industry == "UNCLASSIFIED":
                excluded["unclassified"] += 1
            elif any(name in industry for name in EXCLUDED_INDUSTRIES):
                excluded["industry_excluded"] += 1
            else:
                meta[row["symbol"]] = row
        bars = self.daily_store.read_range(sorted(meta), sessions[0], target)
        by_symbol: dict[str, dict[date, dict]] = {}
        for row in bars.iter_rows(named=True):
            by_symbol.setdefault(row["symbol"], {})[row["date"]] = row

        items: list[dict] = []
        eligible = 0
        for symbol in sorted(meta):
            rows = by_symbol.get(symbol, {})
            raw = [rows.get(d) for d in sessions]
            if any(row is None for row in raw):
                excluded["incomplete_sessions"] += 1
                continue
            raw = [{k: row[k] for k in ("date", "open", "high", "low", "close", "volume")} for row in raw]
            if not all(_valid_bar(bar) for bar in raw):
                excluded["invalid_ohlc"] += 1
                continue
            avg_lots = _mean([bar["volume"] for bar in raw[-5:]]) / LOT_SIZE
            if avg_lots < MIN_AVG_VOLUME_LOTS:
                excluded["low_liquidity"] += 1
                continue
            events = events_by_symbol.get(symbol)
            if events:
                frame = pl.DataFrame([{"symbol": symbol, **bar} for bar in raw], schema_overrides={"volume": pl.Float64})
                adjusted_frame = adjust_prices_as_of(frame, as_of=target, events=events, price_columns=PRICE_COLUMNS)
                if adjusted_frame.status != "verified":
                    excluded["corporate_action_unverified"] += 1
                    continue
                adjusted = adjusted_frame.to_frame().sort("date").to_dicts()
            else:
                # Verified coverage with no event inside the window means the raw series is already consistent.
                adjusted = [dict(bar) for bar in raw]
            eligible += 1
            matches = detect_patterns(adjusted, raw, events or (), symbol)
            if not matches:
                continue
            for match in matches:
                items.append({
                    "symbol": symbol, "code": meta[symbol]["code"], "name": meta[symbol]["name"],
                    "industry": meta[symbol]["industry"], "price": raw[-1]["close"],
                    "avg_volume_lots": round(avg_lots, 2), "pattern": match,
                })

        items.sort(key=lambda item: (-item["pattern"]["score"], item["symbol"]))
        out.update(
            status="available", reason=None, eligible=eligible,
            matched=len({item["symbol"] for item in items}), pattern_count=len(items),
            excluded=dict(sorted(excluded.items())), items=_clean(items),
        )
        return out
