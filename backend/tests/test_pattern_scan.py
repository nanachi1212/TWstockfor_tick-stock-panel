"""Fixed synthetic paths for 型態選股: detection states, false-breakout filter, plan grid and scan filters."""
# ruff: noqa: RUF001, RUF003 -- user-facing Traditional Chinese labels and rule text.
import json
from datetime import date, datetime, timedelta
from itertools import pairwise
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.corporate_actions import CorporateActionEvent, event_market_open
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.pattern_scan import WINDOW, TaiwanPatternScanService, _clean, detect_patterns
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

# Double bottom: lows at index 20 and 68 (≈79), neckline ≈93 at index 40, then a rebound into the window end.
ANCHORS = [(0, 100.0), (20, 80.0), (40, 92.0), (68, 80.5), (84, 88.0), (89, 89.0)]
BASE_VOLUME = 1_000_000.0


def _path(anchors=ANCHORS):
    closes = []
    for (i0, v0), (i1, v1) in pairwise(anchors):
        closes += [v0 + (v1 - v0) * (i - i0) / (i1 - i0) for i in range(i0, i1)]
    return [*closes, anchors[-1][1]]


def _bars(closes, volumes=None, spread=1.0):
    start = date(2026, 6, 10)
    volumes = volumes or [BASE_VOLUME] * len(closes)
    return [{"date": start + timedelta(days=i), "open": c, "high": c + spread, "low": c - spread,
             "close": c, "volume": v} for i, (c, v) in enumerate(zip(closes, volumes, strict=True))]


def _with(closes, **overrides):
    closes = list(closes)
    for index, value in overrides.items():
        closes[int(index)] = value
    return closes


def _patterns(bars, events=(), symbol="X.TWSE"):
    return {item["id"]: item for item in detect_patterns(bars, bars, events, symbol)}


def _share_event(day, symbol="X.TWSE"):
    return CorporateActionEvent(
        symbol=symbol, exchange="TWSE", effective_date=day, effective_at=event_market_open(day),
        event_type="stock_dividend", previous_close=100.0, reference_price=50.0, factor=0.5, cash_dividend=None,
        free_share_ratio=1.0, reduction_ratio=None, source="TWT49U", source_url="https://www.twse.com.tw/",
        retrieved_at=datetime(2026, 10, 2, tzinfo=TAIPEI), status="verified", precision_method="official_reference_ratio")


# Descending wedge over sessions 55..84: a triangle wave (period 8) whose peaks and troughs sit exactly on two
# converging down-sloping lines, so both regression fits are exact and the lower line falls ~0.28/session.
W_START, W_END = 55, 84


def _wedge_line(k, side):
    progress = (k - W_START) / (W_END - W_START)
    mid, amp = 100.0 - 10.0 * progress, 4.0 - 1.5 * progress
    return mid + amp * side


def _wedge_closes(bar_87, atr=1.8):
    # 85-86 drift along the channel middle, 87 is the probe session (too late to be a pivot), 88 closes above
    # the upper line so the end=88 candidate is ruled out by the pattern-end gate, 89 holds just above it.
    closes = []
    for k in range(WINDOW):
        if k < W_START:
            closes.append(92.0 + 8.0 * k / W_START)
        elif k <= W_END:
            phase = (k - W_START - 2) % 8
            closes.append(_wedge_line(k, 1 - abs(phase - 4) / 2))
        else:
            closes.append(_wedge_line(k, 0))
    closes[87] = bar_87
    # The fitted upper line runs through the highs (close + 0.3 spread), so clear that plus the 0.15 ATR margin.
    closes[88] = _wedge_line(88, 1) + 0.3 + 0.15 * atr + 0.5
    closes[89] = _wedge_line(WINDOW - 1, 1) + 0.5
    return closes


def test_break_below_the_contemporaneous_lower_line_invalidates_a_wedge():
    benign = _patterns(_bars(_wedge_closes(_wedge_line(87, -1) + 0.5), spread=0.3))
    assert "wedge" in benign
    atr = benign["wedge"]["atr"]
    # Use the fitted lower trend line the detector itself reports (it runs through the lows, not the closes).
    line = benign["wedge"]["lines"][1]
    assert line["label"] == "支撐趨勢線"
    fitted = lambda k: line["a"] + (line["b"] - line["a"]) * (k - line["from"]) / (line["to"] - line["from"])  # noqa: E731
    lower_87, lower_89 = fitted(87) - 0.25 * atr, fitted(WINDOW - 1) - 0.25 * atr
    assert lower_89 < lower_87  # the line is falling, so the session-89 value is the looser test
    assert benign["wedge"]["support"] == pytest.approx(fitted(WINDOW - 1), abs=0.01)
    # Session 87 closes below the support in force that day, but above the session-89 projection.
    breaking = _patterns(_bars(_wedge_closes((lower_87 + lower_89) / 2, atr), spread=0.3))
    assert "wedge" not in breaking


def test_share_count_event_inside_the_volume_window_withholds_volume_confirmation():
    closes = _with(_path(), **{"85": 88.5, "86": 89.0, "87": 95.0, "88": 94.5, "89": 94.5})
    volumes = [BASE_VOLUME] * WINDOW
    volumes[87] = 2 * BASE_VOLUME
    bars = _bars(closes, volumes)
    # Stock dividend effective on session 80: inside the 20-session baseline (67..86) before the breakout on 87.
    found = _patterns(bars, events=[_share_event(bars[80]["date"])])["double"]
    assert found["volume_status"] == "data_insufficient"
    assert found["volume_ratio"] is None
    assert found["status"] == "突破待量確認"
    assert found["score_parts"]["volume"] == 0
    assert found["score_parts"]["confirmation"] == 18
    assert "量能視窗跨越股數變動事件，成交量不可比，不計量能確認與量能分數" in found["warnings"]
    # The same event for another symbol, or outside the window, leaves the confirmation intact.
    assert _patterns(bars, events=[_share_event(bars[80]["date"], symbol="Y.TWSE")])["double"]["status"] == "量價確認"
    assert _patterns(bars, events=[_share_event(bars[60]["date"])])["double"]["status"] == "量價確認"


def test_breakout_is_not_confirmed_when_the_next_session_closes_below_the_neckline():
    # Breakout on 87 with volume, 88 closes under the neckline (93) but inside the 0.35 ATR false-break tolerance,
    # 89 recovers above the threshold. The plan text requires the next session to hold, so this is not confirmed.
    closes = _with(_path(), **{"85": 88.5, "86": 89.0, "87": 95.0, "88": 92.5, "89": 94.5})
    volumes = [BASE_VOLUME] * WINDOW
    volumes[87] = 2 * BASE_VOLUME
    found = _patterns(_bars(closes, volumes))["double"]
    assert found["volume_ratio"] == 2.0
    assert found["status"] == "突破待量確認"
    assert found["score_parts"]["confirmation"] == 18
    assert "突破次日收盤未守住頸線，尚未確認" in found["warnings"]


def test_double_bottom_is_listed_as_approaching_breakout_with_tick_grid_plan():
    closes = _path()
    found = _patterns(_bars(closes))["double"]
    assert found["status"] == "接近突破"
    assert found["breakout_date"] is None
    assert found["resistance"] == pytest.approx(93.0)
    assert found["plan"]["action"] == "等待收盤突破與量能確認"
    plan = found["plan"]
    assert plan["stop"] < 79.0 < plan["entry"] < plan["entry_max"]
    assert plan["target"] == pytest.approx(107.0, abs=0.5)  # neckline 93 + height 14, rounded down to tick
    # Ordinary stock tick at 50–100 is 0.10: every plan level sits on that grid.
    for level in (plan["entry"], plan["entry_max"], plan["stop"]):
        assert level * 10 == pytest.approx(round(level * 10), abs=1e-6)
    # geometry ≈ 38.92 (similar lows), no breakout, volume 1.0x → 6.67, above 20-day mean → 10.
    assert found["score"] == 64


def test_confirmed_breakout_on_earlier_session_scores_full_confirmation():
    closes = _with(_path(), **{"85": 88.5, "86": 89.0, "87": 95.0, "88": 94.5, "89": 94.5})
    volumes = [BASE_VOLUME] * WINDOW
    volumes[87] = 2 * BASE_VOLUME
    found = _patterns(_bars(closes, volumes))["double"]
    assert found["status"] == "量價確認"
    assert found["breakout_date"] == _bars(closes)[87]["date"]
    assert found["volume_ratio"] == 2.0
    assert found["score_parts"]["confirmation"] == 30
    assert found["score_parts"]["volume"] == pytest.approx(20.0)
    assert found["score"] == 99
    # Reward-to-risk is about 0.7 on this geometry, so the plan refuses entry instead of proposing one.
    assert found["plan"]["rr"] < 1.5
    assert found["plan"]["action"] == "報酬風險比不足，暫不進場"


def test_breakout_on_last_session_waits_for_next_day_confirmation():
    closes = _with(_path(), **{"84": 88.0, "88": 88.5, "89": 94.0})
    volumes = [BASE_VOLUME] * WINDOW
    volumes[89] = 2 * BASE_VOLUME
    found = _patterns(_bars(closes, volumes))["double"]
    assert found["status"] == "突破待次日確認"
    assert found["plan"]["action"] == "等待次日守穩頸線"


def test_false_breakout_closing_back_below_neckline_is_not_listed():
    # Break above the neckline on session 86, then close 0.35 ATR+ below it on session 88.
    closes = _with(_path(), **{"85": 95.0, "86": 96.0, "87": 91.0, "88": 90.0, "89": 90.0})
    assert "double" not in _patterns(_bars(closes))


def test_window_length_mismatch_returns_nothing():
    closes = _path()[:89]
    assert detect_patterns(_bars(closes), _bars(closes)) == []


def test_clean_replaces_non_finite_and_keeps_plain_json():
    payload = _clean({"a": float("nan"), "b": [float("inf"), 1.5], "d": date(2026, 10, 20)})
    assert payload == {"a": None, "b": [None, 1.5], "d": "2026-10-20"}
    json.dumps(payload, allow_nan=False)


# ── Service: liquidity, sessions, industry and corporate-action gates ──

TARGET = date(2026, 10, 23)


def _sessions(count=WINDOW, end=TARGET):
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def _rows(symbol, closes, volume=BASE_VOLUME, days=None):
    days = days or _sessions()
    return [{"symbol": symbol, "date": d, "open": c, "high": c + 1.0, "low": c - 1.0, "close": c,
             "volume": volume, "amount": c * volume, "quote_ts": None} for d, c in zip(days, closes, strict=True)]


def _master():
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["2330.TWSE", "1101.TWSE", "2881.TWSE", "1102.TWSE", "0050.TWSE"],
        "code": ["2330", "1101", "2881", "1102", "0050"],
        "exchange": ["TWSE"] * 5,
        "name": ["甲", "乙", "丙", "丁", "戊"],
        "instrument_type": ["stock", "stock", "stock", "stock", "etf"],
        "listing_status": ["active"] * 5,
        "industry": ["半導體業", "電子零組件業", "金融保險業", "電子零組件業", None],
    })
    return master


def _calendar(sessions):
    return TaiwanTradingCalendar(known_trading_days=set(sessions))


def _service(tmp_path, rows, *, coverage=True, sessions=None, events=(), coverage_span=None):
    sessions = sessions or _sessions()
    daily = TaiwanDailyStore(tmp_path / "daily")
    daily.write_batch(pl.DataFrame(rows, schema_overrides={"amount": pl.Float64, "quote_ts": pl.Int64}))
    actions = MagicMock()
    if not coverage:
        actions.read_verified_coverage.return_value = None
    else:
        span = coverage_span or (date(2026, 1, 1), TARGET)
        actions.read_verified_coverage.return_value = (*span, tuple(events))
    return TaiwanPatternScanService(daily, _calendar(sessions), _master(),
                                    ObservedUniverseStore(tmp_path / "census"), actions)


def _healthy_rows(closes=None):
    closes = closes or _path()
    rows = _rows("2330.TWSE", closes)
    rows += _rows("1101.TWSE", closes)[:40] + _rows("1101.TWSE", closes)[41:]  # one missing session
    rows += _rows("2881.TWSE", closes) + _rows("1102.TWSE", closes, volume=100_000.0)
    rows += _rows("0050.TWSE", closes)
    return rows


def test_no_trade_sessions_are_counted_apart_from_invalid_prices(tmp_path):
    closes = _path()
    no_trade = _rows("2330.TWSE", closes)
    # 正常盤無成交: 交易所不給開高低收, 成交量可能只有零股。
    no_trade[30].update(open=None, high=None, low=None, close=None, volume=278.0)
    broken = _rows("1101.TWSE", closes)
    broken[30]["high"] = broken[30]["close"] - 0.5
    rows = no_trade + broken + _rows("2881.TWSE", closes) + _rows("1102.TWSE", closes)
    snap = _service(tmp_path, rows).snapshot(TARGET)
    assert snap["excluded"] == {"no_trade_sessions": 1, "invalid_ohlc": 1, "industry_excluded": 1}
    assert snap["eligible"] == 1


def test_scan_lists_only_clean_ordinary_stocks_and_counts_each_exclusion(tmp_path):
    snap = _service(tmp_path, _healthy_rows()).snapshot(TARGET)
    assert (snap["status"], snap["reason"], snap["date"], snap["from"]) == ("available", None, "2026-10-23", _sessions()[0].isoformat())
    assert snap["universe"] == 4
    assert snap["excluded"] == {"incomplete_sessions": 1, "industry_excluded": 1, "low_liquidity": 1}
    assert snap["eligible"] == 1
    # 2330's 90-day bowl also satisfies the round rule; the double bottom must be among its matches.
    assert {(i["symbol"], i["pattern"]["id"]) for i in snap["items"]} >= {("2330.TWSE", "double")}
    assert {i["symbol"] for i in snap["items"]} == {"2330.TWSE"}
    assert snap["matched"] == 1 and snap["pattern_count"] == len(snap["items"])
    json.dumps(snap, allow_nan=False)


def test_missing_verified_corporate_actions_fails_closed(tmp_path):
    snap = _service(tmp_path, _healthy_rows(), coverage=False).snapshot(TARGET)
    assert (snap["status"], snap["reason"], snap["items"]) == ("unavailable", "corporate_action_coverage_unavailable", [])


def test_coverage_that_starts_after_window_is_not_used(tmp_path):
    snap = _service(tmp_path, _healthy_rows(), coverage_span=(date(2026, 10, 1), TARGET)).snapshot(TARGET)
    assert snap["reason"] == "corporate_action_coverage_unavailable"


def test_unverified_corporate_action_in_window_excludes_that_stock_only(tmp_path):
    sessions = _sessions()
    bad = CorporateActionEvent(
        symbol="2330.TWSE", exchange="TWSE", effective_date=sessions[50], effective_at=event_market_open(sessions[50]),
        event_type="cash_dividend", previous_close=100.0, reference_price=None, factor=None, cash_dividend=None,
        free_share_ratio=None, reduction_ratio=None, source="TWT49U", source_url="https://www.twse.com.tw/",
        retrieved_at=datetime(2026, 10, 2, tzinfo=TAIPEI), status="data_insufficient")
    snap = _service(tmp_path, _healthy_rows(), events=[bad]).snapshot(TARGET)
    assert snap["items"] == []
    assert snap["excluded"]["corporate_action_unverified"] == 1


def test_target_without_partition_is_unavailable(tmp_path):
    snap = _service(tmp_path, _healthy_rows()).snapshot(TARGET + timedelta(days=7))
    assert (snap["status"], snap["reason"]) == ("unavailable", "no_daily_data")
