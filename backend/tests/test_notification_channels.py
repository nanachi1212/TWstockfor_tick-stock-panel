from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import secrets_store
from app.api import settings as settings_api
from app.services import preferences, webhook_adapter
from app.services.quote_service import QuoteService

_SYSTEM_SIMPLIFIED_TERMS = (
    "测试", "买入", "卖出", "信号", "触发", "当前", "价格", "规则", "监控",
    "数据", "状态", "设置", "删除", "新增", "默认", "刷新",
)


def _assert_system_zh_tw(text: str) -> None:
    assert all(term not in text for term in _SYSTEM_SIMPLIFIED_TERMS)


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
    persisted = json.loads(preferences_path.read_text(encoding="utf-8"))
    assert persisted["feishu_webhook_url"] == "legacy-value"
    assert persisted["nav_order"] == ["market", "watchlist"]


def test_notification_tokens_are_masked_and_stored_in_secrets(monkeypatch, tmp_path):
    _, secrets_path = _isolated_stores(monkeypatch, tmp_path)
    webhook_adapter.record_delivery_status("line", "not_configured")
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
    assert "line" not in webhook_adapter.delivery_status()

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
    fixed_event = webhook_adapter.build_test_alert_event(
        datetime(2026, 9, 29, 10, 15, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(webhook_adapter, "build_test_alert_event", lambda: fixed_event)

    assert settings_api.test_line_messaging() == {"ok": True}
    assert settings_api.test_telegram_bot() == {"ok": True}
    assert line_call[0][:2] == ("line-token", "U123")
    assert telegram_call[0][:2] == ("telegram-token", "-1001")
    expected_title = ""
    expected_body = (
        "【TWStock 策略提醒】\n\n"
        "測試股票（A）\n\n"
        "策略：測試買入策略\n"
        "目前價格：10 元\n\n"
        "觸發時間：2026-09-29 10:15"
    )
    assert line_call[0][2:] == (expected_title, expected_body)
    assert telegram_call[0][2:] == (expected_title, expected_body)
    for call in (line_call[0], telegram_call[0]):
        _assert_system_zh_tw("\n".join(call[2:]))


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


def test_preference_merge_writes_are_serialized(monkeypatch, tmp_path):
    preferences_path, _ = _isolated_stores(monkeypatch, tmp_path)
    original_load = preferences.load
    counter_lock = threading.Lock()
    active_loads = 0
    max_active_loads = 0

    def tracked_load():
        nonlocal active_loads, max_active_loads
        with counter_lock:
            active_loads += 1
            max_active_loads = max(max_active_loads, active_loads)
        try:
            time.sleep(0.02)
            return original_load()
        finally:
            with counter_lock:
                active_loads -= 1

    monkeypatch.setattr(preferences, "load", tracked_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(preferences.save, {"external_notification_channels": ["line"]}),
            pool.submit(preferences.save, {"minute_intraday_refresh_interval": 9}),
        ]
        for future in futures:
            future.result()

    assert max_active_loads == 1
    saved = json.loads(preferences_path.read_text(encoding="utf-8"))
    assert saved["external_notification_channels"] == ["line"]
    assert saved["minute_intraday_refresh_interval"] == 9


def test_preference_reads_wait_until_merge_write_finishes(monkeypatch, tmp_path):
    _isolated_stores(monkeypatch, tmp_path)
    started = threading.Event()

    def read_preferences():
        started.set()
        return preferences.load()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with preferences._SAVE_LOCK:
            future = pool.submit(read_preferences)
            assert started.wait(timeout=1)
            time.sleep(0.02)
            assert not future.done()
        assert future.result(timeout=1) == {}


def test_quote_interval_setter_preserves_other_preferences(monkeypatch, tmp_path):
    _isolated_stores(monkeypatch, tmp_path)
    preferences.set_external_notification_channels(["line"])

    assert preferences.set_realtime_quote_interval(12) == 12
    assert preferences.get_external_notification_channels() == ["line"]


def test_notification_status_endpoint_returns_latest_delivery_state(monkeypatch):
    monkeypatch.setattr(webhook_adapter, "delivery_status", lambda: {"line": "sent"})

    assert settings_api.get_notification_status() == {"external_notification_status": {"line": "sent"}}


def test_alert_message_includes_stock_and_trigger_but_omits_missing_quote():
    message = webhook_adapter.alert_message({
        "alert_id": "a1",
        "symbol": "8358.TPEX",
        "name": "金居",
        "message": "跌破價格提醒",
        "price": None,
        "rule_type": "price_below",
        "threshold": 450,
        "triggered_at": "2026-09-25T13:42:00+08:00",
        "quant_score": None,
    })

    assert message.startswith("【TWStock 價格提醒】")
    assert "金居（8358.TPEX）" in message
    assert "跌破價格提醒" in message
    assert "設定門檻：450 元" in message
    assert "觸發時間：2026-09-25 13:42" in message
    assert "目前價格" not in message


@pytest.mark.parametrize(
    ("rule_type", "threshold", "expected"),
    [
        ("change_pct_above", 5, "設定門檻：5%"),
        ("volume_above", 1000, "設定門檻：1,000 股"),
        ("volume_spike", 2.5, "設定門檻：2.5 倍"),
        ("near_upper_limit", 3, "設定門檻：3%"),
    ],
)
def test_alert_message_preserves_taiwan_threshold_units(rule_type, threshold, expected):
    message = webhook_adapter.alert_message({"rule_type": rule_type, "threshold": threshold})

    assert expected in message


def test_alert_message_omits_threshold_when_rule_unit_is_unknown():
    message = webhook_adapter.alert_message({"rule_type": "unknown", "threshold": 5})

    assert "設定門檻" not in message


def test_alert_message_formats_quant_score_as_percentage():
    message = webhook_adapter.alert_message({
        "symbol": "2330.TWSE",
        "message": "進入 Quant Top 10",
        "quant_score": 0.91,
    })

    assert "Quant 分數：91.0%" in message
    assert "Quant: 0.91" not in message

    message_with_score = webhook_adapter.alert_message({
        "message": "Quant 分數 91.0%",
        "quant_score": 0.91,
    })
    assert message_with_score.count("91.0%") == 1


def test_alert_message_prefers_iso_timestamp_and_converts_to_taipei():
    message = webhook_adapter.alert_message({
        "source": "signal",
        "triggered_at": "2026-09-25T05:42:00Z",
        "ts": 1_000_000_000,
    })

    assert "觸發時間：2026-09-25 13:42" in message
    assert "2001-09" not in message


def test_alert_message_falls_back_to_ts_when_iso_timestamp_is_invalid():
    instant = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    message = webhook_adapter.alert_message({
        "source": "signal",
        "triggered_at": "1970-01-01T00:00:00Z",
        "ts": int(instant.timestamp()),
    })

    expected = instant.astimezone(ZoneInfo("Asia/Taipei")).strftime("觸發時間：%Y-%m-%d %H:%M")
    assert expected in message
    assert "1970" not in message


def test_alert_message_supports_unix_seconds_and_milliseconds_consistently():
    instant = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    seconds_message = webhook_adapter.alert_message({"source": "signal", "ts": int(instant.timestamp())})
    millis_message = webhook_adapter.alert_message({"source": "signal", "ts": int(instant.timestamp() * 1000)})
    expected = instant.astimezone(ZoneInfo("Asia/Taipei")).strftime("觸發時間：%Y-%m-%d %H:%M")

    assert expected in seconds_message
    assert expected in millis_message
    assert "1970" not in seconds_message
    assert "1970" not in millis_message


@pytest.mark.parametrize("timestamp", [0, 1, -1, 999_999_999])
def test_alert_message_omits_zero_or_invalid_timestamp(timestamp):
    message = webhook_adapter.alert_message({"source": "signal", "ts": timestamp})

    assert "觸發時間" not in message
    assert "1970" not in message


def test_alert_message_omits_missing_or_implausible_future_timestamp():
    no_timestamp = webhook_adapter.alert_message({"source": "signal"})
    future = datetime.now(UTC) + timedelta(days=2)
    future_timestamp = webhook_adapter.alert_message({"source": "signal", "ts": future.timestamp() * 1000})

    assert "觸發時間" not in no_timestamp
    assert "觸發時間" not in future_timestamp


def test_strategy_alert_uses_rule_name_and_deduplicates_same_reason():
    message = webhook_adapter.alert_message({
        "source": "strategy",
        "symbol": "2330.TWSE",
        "name": "台積電",
        "rule_name": "測試買入策略",
        "strategy_name": "備用策略名稱",
        "message": "測試買入策略",
        "price": 168.5000000000,
        "threshold": 160.0,
        "triggered_at": "2026-09-25T13:42:00+08:00",
    })

    assert message.count("【TWStock 策略提醒】") == 1
    assert "台積電（2330.TWSE）" in message
    assert "策略：測試買入策略" in message
    assert "原因：測試買入策略" not in message
    assert "目前價格：168.5 元" in message
    assert "設定門檻：160 元" in message


@pytest.mark.parametrize(
    ("event", "expected_stock"),
    [
        ({"symbol": "8358.TPEX", "name": ""}, "8358.TPEX"),
        ({"symbol": "", "name": "金居"}, "金居"),
        ({"symbol": "A", "name": "測試股票"}, "測試股票（A）"),
    ],
)
def test_alert_message_stock_label_handles_missing_fields(event, expected_stock):
    message = webhook_adapter.alert_message({"source": "price", **event})

    assert expected_stock in message


def test_buy_point_alert_is_concise_and_lists_reasons_and_risks():
    message = webhook_adapter.alert_message({
        "type": "buy_point",
        "source": "strategy",
        "symbol": "8358.TPEX",
        "name": "金居",
        "strategy_name": "趨勢買點",
        "price": 498.0,
        "trigger_reasons": ["站上 20 日線", "量能擴大", "價格突破"],
        "risk_flags": ["近期波動偏高"],
        "triggered_at": "2026-09-25T13:42:00+08:00",
    })

    assert message.startswith("【TWStock 買點提醒】")
    assert "金居（8358.TPEX）" in message
    assert "策略：趨勢買點\n狀態：已觸發" in message
    assert "目前價格：498 元" in message
    assert "觸發原因：\n• 站上 20 日線\n• 量能擴大\n• 價格突破" in message
    assert "風險提示：\n• 近期波動偏高" in message
    _assert_system_zh_tw(message)


def test_user_authored_message_is_not_force_converted():
    custom_message = "用户自定义买入信号"
    message = webhook_adapter.alert_message({
        "source": "signal",
        "symbol": "A",
        "message": custom_message,
    })

    assert custom_message in message


def test_strategy_with_buy_point_id_uses_buy_point_format():
    message = webhook_adapter.alert_message({
        "source": "strategy",
        "buy_point_strategy_id": "bp-1",
        "rule_name": "回檔買點",
    })

    assert message.startswith("【TWStock 買點提醒】")
    assert "狀態：已觸發" in message


def test_price_alert_formats_price_percentage_and_canonical_volume_without_raw_floats():
    message = webhook_adapter.alert_message({
        "source": "price",
        "symbol": "2330.TWSE",
        "name": "台積電",
        "message": "突破價格門檻",
        "price": 10.00000000001,
        "change_pct": 1.25000000001,
        "volume": 1234567.0,
        "volume_unit": "shares",
    })

    assert message.startswith("【TWStock 價格提醒】")
    assert "目前價格：10 元" in message
    assert "漲跌幅：1.25%" in message
    assert "成交量：1,234,567 股" in message
    assert "10.000000" not in message


def test_alert_message_strips_embedded_twstock_title():
    message = webhook_adapter.alert_message({
        "source": "signal",
        "message": "【TWStock 提醒】\n量價訊號已觸發",
    })

    assert message.count("【TWStock") == 1
    assert "原因：量價訊號已觸發" in message


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
        assert title == ""
        assert "台積電（2330.TWSE）" in body
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


def test_sector_alert_targets_have_distinct_delivery_dedupe_keys(monkeypatch):
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
    monkeypatch.setattr(webhook_adapter, "send_telegram", lambda *_args: True)
    service = object.__new__(QuoteService)
    events = [
        {"rule_id": "sector-rule", "symbol": "", "sector_key": key,
         "type": "sector_change_pct_up", "ts": 1000, "source": "sector", "name": key}
        for key in ("industry:semiconductor", "concept:ai")
    ]

    service._maybe_send_webhook(events, None)

    assert len(calls) == 2


def test_global_alert_dispatch_reads_credentials_once_per_channel_batch(monkeypatch):
    from app.services import quote_service

    reads = {"token": 0, "target": 0}

    def token():
        reads["token"] += 1
        return "fake-token"

    def chat():
        reads["target"] += 1
        return "-1001"

    class InlineExecutor:
        def submit(self, _fn, *_args):
            return None

    monkeypatch.setattr(quote_service, "_WEBHOOK_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(quote_service, "_WEBHOOK_DISPATCHED", set())
    monkeypatch.setattr(preferences, "get_external_notification_channels", lambda: ["telegram"])
    monkeypatch.setattr(preferences, "get_telegram_bot_token", token)
    monkeypatch.setattr(preferences, "get_telegram_chat_id", chat)
    monkeypatch.setattr(webhook_adapter, "send_telegram", lambda *_args: True)
    service = object.__new__(QuoteService)
    events = [
        {"alert_id": f"batch-{index}", "symbol": str(index), "message": "告警"}
        for index in range(10)
    ]

    service._maybe_send_webhook(events, None)

    assert reads == {"token": 1, "target": 1}


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
