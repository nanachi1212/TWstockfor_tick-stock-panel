from __future__ import annotations

from app.api.monitor_rules import (
    TaiwanMonitorRuleCreate,
    TaiwanMonitorRuleUpdate,
    create_taiwan_rule,
    update_taiwan_rule,
)
from app.taiwan.realtime.monitor_models import (
    TaiwanMonitorRule,
    TaiwanRuleType,
)


def test_taiwan_monitor_rule_notify_channels_defaults_and_serialization():
    rule = TaiwanMonitorRule(
        rule_id="tw_rule_test_01",
        name="台積電 突破",
        symbol="2330.TWSE",
        rule_type=TaiwanRuleType.PRICE_ABOVE,
        threshold=1000.0,
    )
    assert rule.notify_channels == []
    d = rule.to_dict()
    assert d["notify_channels"] == []

    restored = TaiwanMonitorRule.from_dict(d)
    assert restored.notify_channels == []

    # With channels
    rule_with_ch = TaiwanMonitorRule(
        rule_id="tw_rule_test_02",
        name="鴻海 跌幅",
        symbol="2317.TWSE",
        rule_type=TaiwanRuleType.CHANGE_PCT_BELOW,
        threshold=-3.0,
        notify_channels=["line", "telegram", "invalid_channel"],
    )
    d2 = rule_with_ch.to_dict()
    assert d2["notify_channels"] == ["line", "telegram", "invalid_channel"]

    # from_dict filters to valid channels ('line', 'telegram')
    restored2 = TaiwanMonitorRule.from_dict(d2)
    assert restored2.notify_channels == ["line", "telegram"]


def test_taiwan_rule_create_and_update_with_notify_channels(monkeypatch):
    stored_rules: dict[str, TaiwanMonitorRule] = {}

    class DummyEngine:
        def add_rule(self, rule: TaiwanMonitorRule):
            stored_rules[rule.rule_id] = rule

        def get_rule(self, rule_id: str) -> TaiwanMonitorRule | None:
            return stored_rules.get(rule_id)

    engine = DummyEngine()
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: engine)

    # 1. Create with channels
    req_create = TaiwanMonitorRuleCreate(
        rule_id="tw_rule_c1",
        name="台積電 漲停預警",
        symbol="2330.TWSE",
        rule_type="price_above",
        threshold=1100.0,
        notify_channels=["line", "telegram", "unknown"],
    )
    res = create_taiwan_rule(req_create)
    assert res["ok"] is True
    assert res["rule"]["notify_channels"] == ["line", "telegram"]
    assert stored_rules["tw_rule_c1"].notify_channels == ["line", "telegram"]

    # 2. Update channels
    req_update = TaiwanMonitorRuleUpdate(
        notify_channels=["line"],
    )
    res_update = update_taiwan_rule("tw_rule_c1", req_update)
    assert res_update["ok"] is True
    assert res_update["rule"]["notify_channels"] == ["line"]
    assert stored_rules["tw_rule_c1"].notify_channels == ["line"]


def test_four_core_conditions_supported(monkeypatch):
    stored_rules: dict[str, TaiwanMonitorRule] = {}

    class DummyEngine:
        def add_rule(self, rule: TaiwanMonitorRule):
            stored_rules[rule.rule_id] = rule

    engine = DummyEngine()
    monkeypatch.setattr("app.taiwan.realtime.monitor_engine.get_monitor_engine", lambda: engine)

    conditions = [
        ("price_above", 1000.0),
        ("price_below", 900.0),
        ("change_pct_above", 5.0),
        ("change_pct_below", -5.0),
    ]
    for idx, (rtype, thr) in enumerate(conditions):
        req = TaiwanMonitorRuleCreate(
            rule_id=f"rule_{idx}",
            name=f"Rule {rtype}",
            symbol="2330.TWSE",
            rule_type=rtype,
            threshold=thr,
            notify_channels=["line"],
        )
        res = create_taiwan_rule(req)
        assert res["ok"] is True
        assert res["rule"]["rule_type"] == rtype
        assert res["rule"]["threshold"] == thr


def test_quote_service_webhook_routing_for_taiwan_alerts(monkeypatch):
    from app.services.quote_service import QuoteService

    qs = QuoteService.__new__(QuoteService)

    # Mock preferences
    line_sent = []
    tg_sent = []

    monkeypatch.setattr("app.services.preferences.get_external_notification_channels", lambda: None)
    monkeypatch.setattr("app.services.preferences.get_line_channel_access_token", lambda: "line_tok")
    monkeypatch.setattr("app.services.preferences.get_line_target_id", lambda: "line_tgt")
    monkeypatch.setattr("app.services.preferences.get_telegram_bot_token", lambda: "tg_tok")
    monkeypatch.setattr("app.services.preferences.get_telegram_chat_id", lambda: "tg_chat")

    # Mock webhook executor & adapters
    monkeypatch.setattr("app.services.quote_service._claim_external_delivery", lambda event_id, ch: True)
    monkeypatch.setattr(
        "app.services.webhook_adapter.send_line",
        lambda token, target, title, body: line_sent.append((token, target, title, body)),
    )
    monkeypatch.setattr(
        "app.services.webhook_adapter.send_telegram",
        lambda token, chat, title, body: tg_sent.append((token, chat, title, body)),
    )
    monkeypatch.setattr("app.services.webhook_adapter.alert_message", lambda ev: f"Alert: {ev['symbol']}")

    class SyncExecutor:
        def submit(self, fn, *args):
            fn(*args)

    monkeypatch.setattr("app.services.quote_service._WEBHOOK_EXECUTOR", SyncExecutor())

    # Alert with both LINE and Telegram
    alert_events = [
        {
            "alert_id": "a1",
            "symbol": "2330.TWSE",
            "notify_channels": ["line", "telegram"],
            "source": "price",
            "type": "price_above",
        },
        {
            "alert_id": "a2",
            "symbol": "2317.TWSE",
            "notify_channels": ["line"],
            "source": "price",
            "type": "change_pct_below",
        },
        {
            "alert_id": "a3",
            "symbol": "2454.TWSE",
            "notify_channels": [],  # App-only
            "source": "price",
            "type": "price_below",
        },
    ]

    # Engine is None for Taiwan alerts
    qs._maybe_send_webhook(alert_events, None)

    # Check results
    assert len(line_sent) == 2  # a1 and a2
    assert len(tg_sent) == 1   # only a1
    assert "2330.TWSE" in line_sent[0][3]
    assert "2317.TWSE" in line_sent[1][3]
    assert "2330.TWSE" in tg_sent[0][3]
