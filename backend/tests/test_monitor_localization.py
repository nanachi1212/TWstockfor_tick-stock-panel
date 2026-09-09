"""TAIWAN_LOCALIZATION_POLISH follow-up — Monitor / notification 使用者可見文案回歸測試。

只驗證兩件事:
  1. 內部識別碼 (signal id / 字段 id / event type / dict key) 完全未變動
     (rule.conditions 既有 saved 值、event contract、規則判定邏輯不受影響)。
  2. 真正會送到使用者的顯示文案 (訊息標題、告警內容、條件摘要) 為正體中文,
     不再殘留簡體中文用字。

不驗證: docstring / logger 訊息 (僅開發者可見, 不在本次任務範圍)。
"""
from __future__ import annotations

from app.services import quote_service
from app.strategy.intraday_signals import INTRADAY_SIGNAL_LABELS
from app.strategy.monitor import _SIGNAL_CN, MonitorRuleEngine

# 常見簡體專用字 (與對應正體字不同形), 若出現在使用者可見文案中即代表殘留簡體。
_SIMPLIFIED_MARKERS = (
    "号", "现", "选", "进", "涨", "场", "软", "强", "势", "趋",
    "买", "卖", "触", "价", "额", "换", "动", "态", "复", "声", "层",
)


def _assert_no_simplified(text: str) -> None:
    hit = [ch for ch in _SIMPLIFIED_MARKERS if ch in text]
    assert not hit, f"發現簡體殘留字 {hit} in {text!r}"


# ── _SIGNAL_CN: key 不變, value 正體中文 ───────────────────────────────
def test_signal_cn_internal_keys_unchanged():
    """rule.conditions 引用的 field id 是持久化資料, key 集合不得變動。"""
    expected_keys = {
        "signal_ma_golden_5_20", "signal_ma_dead_5_20", "signal_ma_golden_20_60",
        "signal_macd_golden", "signal_macd_dead", "signal_ma20_breakout",
        "signal_ma20_breakdown", "signal_ma5_breakout", "signal_ma5_breakdown",
        "signal_ma10_breakout", "signal_ma10_breakdown", "signal_n_day_high",
        "signal_n_day_low", "signal_boll_breakout_upper", "signal_boll_breakdown_lower",
        "signal_volume_surge", "signal_limit_up", "signal_limit_down",
        "signal_limit_down_recovery", "signal_broken_limit_up",
        "close", "open", "high", "low", "change_pct", "change_amount", "amplitude",
        "turnover_rate", "volume", "amount",
        "ma5", "ma10", "ma20", "ma30", "ma60", "ema5", "ema10", "ema20",
        "macd_dif", "macd_dea", "macd_hist", "boll_upper", "boll_lower",
        "kdj_k", "kdj_d", "kdj_j", "rsi_6", "rsi_14", "rsi_24",
        "vol_ratio_5d", "vol_ratio_20d", "vol_ma5", "vol_ma10",
        "high_60d", "low_60d", "momentum_5d", "momentum_20d", "momentum_60d",
        "atr_14", "annual_vol_20d", "consecutive_limit_ups", "consecutive_limit_downs",
        *INTRADAY_SIGNAL_LABELS.keys(),
    }
    assert set(_SIGNAL_CN.keys()) == expected_keys


def test_signal_cn_values_are_traditional():
    for value in _SIGNAL_CN.values():
        _assert_no_simplified(value)
    # 抽查幾個代表性 value 確實已改成正體
    assert _SIGNAL_CN["signal_boll_breakout_upper"] == "突破布林上軌"
    assert _SIGNAL_CN["change_pct"] == "漲跌幅"
    assert _SIGNAL_CN["consecutive_limit_ups"] == "連續漲停"


def test_intraday_signal_labels_traditional_and_keys_unchanged():
    assert set(INTRADAY_SIGNAL_LABELS.keys()) == {
        "signal_intraday_avg_cross_up", "signal_intraday_avg_cross_down",
        "signal_intraday_zero_cross_up", "signal_intraday_zero_cross_down",
    }
    for value in INTRADAY_SIGNAL_LABELS.values():
        _assert_no_simplified(value)


# ── _format_conditions_text / _default_message: 顯示文案正體, event type 不變 ──
def test_format_conditions_text_traditional():
    rule = {"logic": "and", "conditions": []}
    conditions = [
        {"field": "signal_limit_up", "op": "truth"},
        {"field": "change_pct", "op": "gte", "value": 5},
    ]
    text = MonitorRuleEngine._format_conditions_text(rule, conditions)
    assert text == "漲停 且 漲跌幅≥5"
    _assert_no_simplified(text)


def test_default_message_strategy_action_labels_traditional_and_event_type_unchanged():
    engine = MonitorRuleEngine()
    rule = {"type": "strategy", "name": "策略A", "strategy_id": None}

    # event type (dict key) 保持原樣, 只有顯示 label (dict value) 改成正體
    for ev_type, expected_label in (
        ("buy_signal", "買入訊號"),
        ("sell_signal", "賣出訊號"),
        ("pool_entry", "進入選股結果"),
        ("pool_exit", "移出選股結果"),
    ):
        message = engine._default_message(rule, ev_type=ev_type, name="台積電", pct=0.05)
        assert expected_label in message
        assert ev_type in ("buy_signal", "sell_signal", "pool_entry", "pool_exit")  # 內部 id 未變
        _assert_no_simplified(message)


def test_default_message_signal_fallback_traditional():
    engine = MonitorRuleEngine()
    rule = {"type": "signal", "conditions": []}
    message = engine._default_message(rule, price=100.5, pct=0.012)
    assert "現價 100.5" in message
    _assert_no_simplified(message)


# ── _sector_message: 板塊告警文案正體 ───────────────────────────────────
def test_sector_message_traditional():
    snapshot = {
        "kind": "concept", "name": "AI伺服器", "change_pct": 0.032,
        "up_count": 12, "valid_count": 20, "coverage_ratio": 0.8,
        "leader": {"name": "台積電", "symbol": "2330.TWSE", "change_pct": 0.07},
    }
    message = MonitorRuleEngine._sector_message(
        snapshot, trigger="change_pct", direction="up", threshold=0.01,
        window=5, value=None,
    )
    _assert_no_simplified(message)
    assert "概念" in message
    assert "領漲" in message
    assert "覆蓋" in message


# ── quote_service: webhook / 系統通知 source_labels dict ──────────────────
def test_quote_service_source_labels_keys_unchanged_values_traditional():
    import inspect

    src = inspect.getsource(quote_service.QuoteService._maybe_send_webhook)
    assert '"ladder": "連續漲停梯隊"' in src
    assert '"signal": "訊號"' in src
    assert '"price": "價格"' in src
    assert '"market": "異動"' in src
    assert '"sector": "板塊"' in src
    # source 這一側 (dict key) 完全未變, 供規則反查
    for key in ("strategy", "signal", "price", "market", "ladder", "sector"):
        assert f'"{key}":' in src

    src2 = inspect.getsource(quote_service.QuoteService._maybe_send_system_notifications)
    assert '"signal": "訊號"' in src2
    assert '"price": "價格"' in src2
    assert '"market": "異動"' in src2
    assert '"sector": "板塊"' in src2
