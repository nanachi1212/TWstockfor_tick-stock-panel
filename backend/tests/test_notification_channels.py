from __future__ import annotations

import json

import httpx

from app import secrets_store
from app.api import settings as settings_api
from app.services import preferences, webhook_adapter
from app.strategy import monitor_rules


def _isolated_stores(monkeypatch, tmp_path):
    preferences_path = tmp_path / "preferences.json"
    secrets_path = tmp_path / "secrets.json"
    monkeypatch.setattr(preferences, "_path", lambda: preferences_path)
    monkeypatch.setattr(secrets_store, "_path", lambda: secrets_path)
    preferences._invalidate_cache()
    return preferences_path, secrets_path


def test_legacy_channels_are_filtered_without_deleting_other_preferences(monkeypatch, tmp_path):
    preferences_path, _ = _isolated_stores(monkeypatch, tmp_path)
    preferences.save({
        "webhook_default_channels": ["feishu", "line", "wecom", "telegram"],
        "feishu_webhook_url": "legacy-value",
        "nav_order": ["market", "watchlist"],
    })

    assert preferences.get_webhook_default_channels() == ["line", "telegram"]
    assert monitor_rules.normalize({"webhook_channels": ["wecom", "telegram"]})["webhook_channels"] == ["telegram"]
    persisted = json.loads(preferences_path.read_text(encoding="utf-8"))
    assert persisted["feishu_webhook_url"] == "legacy-value"
    assert persisted["nav_order"] == ["market", "watchlist"]


def test_notification_tokens_are_masked_and_stored_in_secrets(monkeypatch, tmp_path):
    _, secrets_path = _isolated_stores(monkeypatch, tmp_path)
    response = settings_api.update_line_messaging(settings_api.NotificationChannelPrefsIn(
        recipient="U123",
        token="line-secret-token",
    ))

    assert response == {
        "line_target_id": "U123",
        "line_channel_access_token_masked": secrets_store.mask("line-secret-token"),
        "line_configured": True,
    }
    assert "line-secret-token" not in str(response)
    assert json.loads(secrets_path.read_text(encoding="utf-8"))["line_channel_access_token"] == "line-secret-token"

    settings_api.update_line_messaging(settings_api.NotificationChannelPrefsIn(recipient="U456", token="   "))
    assert preferences.get_line_channel_access_token() == "line-secret-token"


def test_notification_adapters_use_official_request_shapes(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)

    assert webhook_adapter.send_line("line-token", "U123", "標題", "內容")
    assert calls[0] == (
        "https://api.line.me/v2/bot/message/push",
        {
            "headers": {"Authorization": "Bearer line-token"},
            "json": {"to": "U123", "messages": [{"type": "text", "text": "標題\n內容"}]},
            "timeout": 5.0,
        },
    )

    assert webhook_adapter.send_telegram("bot-token", "-1001", "標題", "x" * 5000)
    assert calls[1][0] == "https://api.telegram.org/botbot-token/sendMessage"
    assert calls[1][1]["json"]["chat_id"] == "-1001"
    assert len(calls[1][1]["json"]["text"]) == 4096


def test_test_notification_uses_stored_credentials(monkeypatch):
    line_call = []
    telegram_call = []
    monkeypatch.setattr(preferences, "get_line_channel_access_token", lambda: "line-token")
    monkeypatch.setattr(preferences, "get_line_target_id", lambda: "U123")
    monkeypatch.setattr(preferences, "get_telegram_bot_token", lambda: "telegram-token")
    monkeypatch.setattr(preferences, "get_telegram_chat_id", lambda: "-1001")
    monkeypatch.setattr(webhook_adapter, "send_line", lambda *args: line_call.append(args) or True)
    monkeypatch.setattr(webhook_adapter, "send_telegram", lambda *args: telegram_call.append(args) or True)

    assert settings_api.test_line_messaging() == {"ok": True}
    assert settings_api.test_telegram_bot() == {"ok": True}
    assert line_call[0][:2] == ("line-token", "U123")
    assert telegram_call[0][:2] == ("telegram-token", "-1001")
