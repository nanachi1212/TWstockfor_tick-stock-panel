from __future__ import annotations

import json

import httpx

from app import secrets_store
from app.api import settings as settings_api
from app.services import preferences, webhook_adapter
from app.services.quote_service import QuoteService
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


def test_global_external_channels_are_persisted_and_app_only_is_explicit(monkeypatch, tmp_path):
    preferences_path, _ = _isolated_stores(monkeypatch, tmp_path)
    assert preferences.get_external_notification_channels() is None
    assert settings_api.update_external_notification_channels(
        settings_api.ExternalNotificationChannelsIn(channels=["telegram", "telegram"])
    ) == {"external_notification_channels": ["telegram"]}
    preferences._invalidate_cache()
    assert preferences.get_external_notification_channels() == ["telegram"]
    assert settings_api.update_external_notification_channels(
        settings_api.ExternalNotificationChannelsIn(channels=[])
    ) == {"external_notification_channels": []}
    preferences._invalidate_cache()
    assert preferences.get_external_notification_channels() == []
    assert "external_notification_channels" in json.loads(preferences_path.read_text(encoding="utf-8"))


def test_alert_message_includes_stock_and_trigger_but_omits_missing_quote():
    message = webhook_adapter.alert_message({
        "alert_id": "a1",
        "symbol": "8358.TWSE",
        "name": "金居",
        "message": "跌破價格提醒",
        "price": None,
        "threshold": 450,
        "triggered_at": "2026-09-25T13:42:00+08:00",
        "quant_score": None,
    })

    assert "8358.TWSE 金居" in message
    assert "跌破價格提醒" in message
    assert "設定門檻: 450" in message
    assert "觸發時間: 2026-09-25 13:42" in message
    assert "目前價格" not in message
    assert "Quant" not in message


def test_global_alert_dispatch_reaches_once_and_failure_does_not_escape(monkeypatch):
    from app.services import quote_service

    calls = []

    class InlineExecutor:
        def submit(self, fn, *args):
            calls.append(args)
            fn(*args)

    monkeypatch.setattr(quote_service, "_WEBHOOK_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(quote_service, "_WEBHOOK_DISPATCHED", set())
    monkeypatch.setattr(preferences, "get_external_notification_channels", lambda: ["telegram"])
    monkeypatch.setattr(preferences, "get_telegram_bot_token", lambda: "fake-token")
    monkeypatch.setattr(preferences, "get_telegram_chat_id", lambda: "-1001")
    monkeypatch.setattr(preferences, "get_line_channel_access_token", lambda: "")
    monkeypatch.setattr(preferences, "get_line_target_id", lambda: "")

    def fake_send(token, chat, title, body):
        assert token == "fake-token"
        assert chat == "-1001"
        assert "2330.TWSE 台積電" in body
        raise TimeoutError("timed out")

    monkeypatch.setattr(webhook_adapter, "send_telegram", fake_send)
    service = object.__new__(QuoteService)
    event = {
        "alert_id": "event-1", "symbol": "2330.TWSE", "name": "台積電",
        "message": "高於價格提醒", "price": 1000, "threshold": 990,
    }
    service._maybe_send_webhook([event], None)
    service._maybe_send_webhook([event], None)

    assert len(calls) == 1


def test_adapter_failure_status_and_logs_never_include_token(monkeypatch, caplog):
    token = "123456:secret-bot-token"

    class Response:
        status_code = 429

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: Response())
    assert not webhook_adapter.send_telegram(token, "-1001", "提醒", "內容")
    assert webhook_adapter.delivery_status()["telegram"] == "failed"
    assert token not in caplog.text


def test_provider_timeout_returns_failure_without_logging_token(monkeypatch, caplog):
    token = "line-sensitive-token"

    def timeout(*_args, **_kwargs):
        raise httpx.TimeoutException(token)

    monkeypatch.setattr(httpx, "post", timeout)
    assert not webhook_adapter.send_line(token, "U123", "提醒", "內容")
    assert webhook_adapter.delivery_status()["line"] == "failed"
    assert token not in caplog.text


def test_rate_limit_retries_once_and_invalid_credentials_do_not_retry(monkeypatch):
    responses = [429, 200]
    calls = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers = {"Retry-After": "0"}

        @staticmethod
        def json():
            return {"ok": True}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: calls.append((args, kwargs)) or Response(responses.pop(0)))
    monkeypatch.setattr(webhook_adapter.time, "sleep", lambda _seconds: None)
    assert webhook_adapter.send_telegram("fake-token", "-1001", "標題", "內容")
    assert len(calls) == 2

    calls.clear()
    responses[:] = [401]
    assert not webhook_adapter.send_telegram("fake-token", "-1001", "標題", "內容")
    assert len(calls) == 1


def test_telegram_rate_limit_uses_retry_after_from_response_body(monkeypatch):
    responses = [429, 200]
    calls = []
    delays = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers = {}

        def json(self):
            if self.status_code == 429:
                return {"ok": False, "parameters": {"retry_after": 1.5}}
            return {"ok": True}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: calls.append((args, kwargs)) or Response(responses.pop(0)))
    monkeypatch.setattr(webhook_adapter.time, "sleep", delays.append)

    assert webhook_adapter.send_telegram("fake-token", "-1001", "標題", "內容")
    assert len(calls) == 2
    assert delays == [1.5]


def test_telegram_rate_limit_skips_retry_when_requested_delay_exceeds_bound(monkeypatch):
    calls = []
    delays = []

    class Response:
        status_code = 429

        def __init__(self):
            self.headers = {}

        @staticmethod
        def json():
            return {"ok": False, "parameters": {"retry_after": 5}}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: calls.append((args, kwargs)) or Response())
    monkeypatch.setattr(webhook_adapter.time, "sleep", delays.append)

    assert not webhook_adapter.send_telegram("fake-token", "-1001", "標題", "內容")
    assert len(calls) == 1
    assert delays == []


def test_disabled_global_channel_does_not_dispatch(monkeypatch):
    from app.services import quote_service

    class NoDispatchExecutor:
        def submit(self, *_args):
            raise AssertionError("disabled channels must not dispatch")

    monkeypatch.setattr(quote_service, "_WEBHOOK_EXECUTOR", NoDispatchExecutor())
    monkeypatch.setattr(quote_service, "_WEBHOOK_DISPATCHED", set())
    monkeypatch.setattr(preferences, "get_external_notification_channels", lambda: [])
    service = object.__new__(QuoteService)

    service._maybe_send_webhook([{"alert_id": "disabled-1", "symbol": "2330"}], None)
