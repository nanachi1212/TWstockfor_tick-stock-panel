from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.taiwan import event_ai_explain as mod

EVENT = {"alert_id": "a1", "rule_id": "r1", "symbol": "2330.TWSE", "name": "台積電",
         "rule_name": "進入承接區", "message": "現價進入承接區", "notify_channels": ["telegram"]}
PARTS = {"what_happened": "價格回到承接區", "why_it_matters": "符合原計畫", "watch_next": "留意失效位"}


@pytest.fixture
def env(monkeypatch):
    prefs: dict = {}
    sent: list = []
    monkeypatch.setattr(mod.preferences, "load", lambda: dict(prefs))
    monkeypatch.setattr(mod.preferences, "save", lambda u: prefs.update(u))
    monkeypatch.setattr(mod.preferences, "get_external_notification_channels", lambda: None)
    for name in ("get_line_channel_access_token", "get_line_target_id", "get_telegram_bot_token", "get_telegram_chat_id"):
        monkeypatch.setattr(mod.preferences, name, lambda n=name: n)
    monkeypatch.setattr(mod.webhook_adapter, "send_telegram", lambda *a: sent.append(("telegram", a[-1])) or True)
    monkeypatch.setattr(mod.webhook_adapter, "send_line", lambda *a: sent.append(("line", a[-1])) or True)
    monkeypatch.setattr("app.services.quote_service._claim_external_delivery", _once())
    return SimpleNamespace(prefs=prefs, sent=sent)


def _once():
    seen: set = set()

    def claim(event_id, channel):
        if (event_id, channel) in seen:
            return False
        seen.add((event_id, channel))
        return True
    return claim


def _explain_ok(monkeypatch):
    async def fake(event):
        return PARTS
    monkeypatch.setattr(mod, "_explain", fake)


def test_disabled_by_default_queues_nothing(env, monkeypatch):
    monkeypatch.setattr(mod._EXECUTOR, "submit", lambda *a: pytest.fail("must not run"))
    mod.submit([EVENT])


def test_followup_is_labeled_ai_and_sent_to_the_rule_channels(env, monkeypatch):
    _explain_ok(monkeypatch)
    assert mod._send(EVENT) == "sent:telegram"
    channel, body = env.sent[0]
    assert channel == "telegram" and body.startswith("【AI 解讀】2330.TWSE 台積電")
    assert "價格回到承接區" in body and "不是買賣指示" in body


def test_global_channels_override_and_duplicate_is_not_resent(env, monkeypatch):
    _explain_ok(monkeypatch)
    monkeypatch.setattr(mod.preferences, "get_external_notification_channels", lambda: ["telegram", "line"])
    assert mod._send(EVENT) == "sent:line,telegram"
    assert mod._send(EVENT) == "duplicate"
    assert len(env.sent) == 2


def test_llm_failure_sends_nothing_and_does_not_raise(env, monkeypatch):
    async def boom(event):
        raise RuntimeError("llm down")
    monkeypatch.setattr(mod, "_explain", boom)
    assert mod._send(EVENT) == "failed:RuntimeError"
    assert env.sent == []


def test_empty_ai_output_is_not_sent(env, monkeypatch):
    async def empty(event):
        return {"what_happened": "", "why_it_matters": "", "watch_next": ""}
    monkeypatch.setattr(mod, "_explain", empty)
    assert mod._send(EVENT) == "empty" and env.sent == []


def test_only_plan_alerts_are_explained(env, monkeypatch):
    mod.set_enabled(True)
    queued: list = []
    monkeypatch.setattr(mod._EXECUTOR, "submit", lambda fn, ev: queued.append(ev))
    monkeypatch.setattr(mod, "_plan_alert", lambda ev: ev["rule_id"] == "plan")
    mod.submit([{**EVENT, "rule_id": "plan"}, {**EVENT, "rule_id": "manual"}])
    assert [e["rule_id"] for e in queued] == ["plan"]
