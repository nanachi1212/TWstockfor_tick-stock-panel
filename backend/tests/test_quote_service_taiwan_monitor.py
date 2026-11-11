"""台股模式輪詢: 只在 09:00-13:30 觸發 monitor_engine, 不抓盤後日資料、不寫 kline_daily。"""
import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.quote_service import QuoteService
from app.taiwan.realtime.calendar import TAIPEI_TZ


def _at(y, mo, d, h, mi, s=0):
    return patch(
        "app.services.quote_service.taipei_now",
        return_value=datetime(y, mo, d, h, mi, s, tzinfo=TAIPEI_TZ),
    )


@pytest.mark.parametrize(("hms", "expected"), [
    ((8, 59, 59), False),   # 試撮, 非成交價
    ((9, 0, 0), True),
    ((13, 30, 0), True),
    ((13, 30, 1), False),   # 盤後零股/定價
])
def test_continuous_trading_follows_taiwan_session(hms, expected):
    with _at(2026, 10, 19, *hms):  # 週一
        assert QuoteService._is_continuous_trading() is expected


def test_weekend_is_not_trading():
    with _at(2026, 10, 17, 10, 0):  # 週六
        assert QuoteService._is_continuous_trading() is False


def _svc() -> QuoteService:
    svc = QuoteService.__new__(QuoteService)
    svc._fetch_lock = threading.Lock()
    svc._lock = threading.Lock()
    svc._repo = MagicMock()
    svc._app_state = MagicMock()
    return svc


def test_taiwan_poll_triggers_evaluate_all_without_writing_daily():
    svc = _svc()
    engine = MagicMock()
    engine.list_rules.return_value = [object()]
    engine.evaluate_all.return_value = []
    with (
        _at(2026, 10, 19, 10, 0),
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider") as get_provider,
        patch("app.taiwan.realtime.monitor_engine.get_monitor_engine", return_value=engine),
    ):
        svc._fetch_quotes()

    engine.evaluate_all.assert_called_once()
    get_provider.assert_not_called()          # 不再抓 STOCK_DAY_ALL
    svc._repo.flush_live_daily.assert_not_called()
    svc._repo.flush_live_daily_asset.assert_not_called()


def test_taiwan_poll_outside_session_skips_evaluation():
    svc = _svc()
    engine = MagicMock()
    with (
        _at(2026, 10, 19, 14, 0),
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.taiwan.realtime.monitor_engine.get_monitor_engine", return_value=engine),
    ):
        svc._fetch_quotes()
    engine.evaluate_all.assert_not_called()


def test_custom_provider_without_realtime_does_not_enter_legacy_sdk(caplog):
    svc = _svc()
    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="daily_only"),
        patch("app.data_providers.custom.provider_has_dataset", return_value=False),
        patch("app.data_providers.custom.get_provider") as get_provider,
    ):
        svc._fetch_quotes()

    get_provider.assert_not_called()
    svc._repo.get_index_symbol_set.assert_not_called()
    svc._repo.flush_live_daily.assert_not_called()
    assert "daily_only" in caplog.text
    assert "realtime" in caplog.text
    assert "not defined" not in caplog.text


def test_taiwan_alert_reaches_sse_and_delivery_without_legacy_signals(tmp_path):
    from app.services import alert_store
    from app.taiwan.realtime.monitor_models import TaiwanAlertEvent

    svc = _svc()
    svc._app_state.repo.store.data_dir = tmp_path
    svc._app_state.extension_registry = None
    svc._broadcast_alerts = MagicMock()
    svc._maybe_send_system_notifications = MagicMock()
    svc._maybe_send_webhook = MagicMock()
    event = TaiwanAlertEvent(
        alert_id="tw_live_event", rule_id="price_rule", rule_name="Price", symbol="2330.TWSE",
        triggered_at=datetime(2026, 11, 4, 10, 14, tzinfo=TAIPEI_TZ), quote_time=None,
        source="taiwan_monitor", source_status="realtime", market_status="open",
        name="TSMC", rule_type="price_above", severity="warning",
        message="Price crossed threshold", trigger_value=1000, threshold=999,
        field_name="last_price", dedup_key="price_rule:2330.TWSE", ts=1793758444000,
    )
    engine = MagicMock()
    engine.list_rules.return_value = [object()]
    def evaluate(*, persist_events):
        persist_events([event])
        return [event]
    engine.evaluate_all.side_effect = evaluate
    with (
        _at(2026, 11, 4, 10, 14),
        patch("app.taiwan.realtime.monitor_engine.get_monitor_engine", return_value=engine),
        patch("app.taiwan.event_ai_explain.submit") as ai_submit,
    ):
        svc._evaluate_monitors()
    assert alert_store.count(tmp_path) == 1
    svc._broadcast_alerts.assert_called_once()
    assert svc._broadcast_alerts.call_args.args[0][0]["signals"] == []
    svc._maybe_send_webhook.assert_called_once_with([event.to_dict()], None)
    ai_submit.assert_called_once()
