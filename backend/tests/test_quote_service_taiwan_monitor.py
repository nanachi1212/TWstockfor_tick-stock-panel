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
