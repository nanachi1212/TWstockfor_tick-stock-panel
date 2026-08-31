"""Deterministic Unit and Integration Tests for Taiwan Daily Update Orchestration.

Covers:
  - Target trading date resolution (weekday post-market, weekday pre-market, weekend, confirmed holiday)
  - Missing date range resolution & catch-up calculation across multiple days
  - Orchestrator behavior with mocked services (all success, partial failure, all failure)
  - Idempotency: repeated execution does not refetch or duplicate
  - Closure handling (2026-07-10 typhoon closure not fabricated or treated as error)
  - Read-only data-status endpoint GET /api/taiwan/data-status
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.daily_update import (
    TaiwanDailyUpdateService,
    resolve_missing_date_range,
    resolve_target_latest_trading_date,
)
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar


def test_date_resolution_weekend():
    """Weekend (e.g. Sunday 2026-08-30) should resolve back to Friday 2026-08-28."""
    cal = TaiwanTradingCalendar()
    sun_dt = datetime(2026, 8, 30, 14, 0, tzinfo=TAIPEI_TZ)
    target = resolve_target_latest_trading_date(calendar=cal, as_of_dt=sun_dt)
    assert target == date(2026, 8, 28)


def test_date_resolution_weekday_before_cutoff():
    """Weekday before 16:00 (e.g. Monday 2026-08-31 10:00) resolves to previous trading day 2026-08-28."""
    cal = TaiwanTradingCalendar()
    mon_morning = datetime(2026, 8, 31, 10, 0, tzinfo=TAIPEI_TZ)
    target = resolve_target_latest_trading_date(calendar=cal, as_of_dt=mon_morning)
    assert target == date(2026, 8, 28)


def test_date_resolution_weekday_after_cutoff():
    """Weekday after 16:00 (e.g. Monday 2026-08-31 17:00) resolves to today 2026-08-31."""
    cal = TaiwanTradingCalendar()
    mon_evening = datetime(2026, 8, 31, 17, 0, tzinfo=TAIPEI_TZ)
    target = resolve_target_latest_trading_date(calendar=cal, as_of_dt=mon_evening)
    assert target == date(2026, 8, 31)


def test_date_resolution_confirmed_holiday():
    """Confirmed holiday resolves to previous available trading day."""
    holiday = date(2026, 9, 2)
    cal = TaiwanTradingCalendar(known_holidays={holiday})
    wed_evening = datetime(2026, 9, 2, 18, 0, tzinfo=TAIPEI_TZ)
    target = resolve_target_latest_trading_date(calendar=cal, as_of_dt=wed_evening)
    assert target == date(2026, 9, 1)


def test_catch_up_multiple_missing_trading_days():
    """If store is at 2026-08-28 and target is 2026-09-02, catch-up starts on 2026-08-31."""
    cal = TaiwanTradingCalendar()
    start_end = resolve_missing_date_range(
        earliest_available=date(2026, 8, 28),
        target_latest=date(2026, 9, 2),
        calendar=cal,
    )
    assert start_end is not None
    start_d, end_d = start_end
    # Aug 29 and Aug 30 are Sat/Sun, so next candidate trading day is Aug 31
    assert start_d == date(2026, 8, 31)
    assert end_d == date(2026, 9, 2)


def test_catch_up_already_current():
    """If store is at 2026-08-28 and target is 2026-08-28, no catch-up range needed."""
    cal = TaiwanTradingCalendar()
    assert resolve_missing_date_range(date(2026, 8, 28), date(2026, 8, 28), cal) is None
    assert resolve_missing_date_range(date(2026, 8, 29), date(2026, 8, 28), cal) is None


def test_orchestrator_all_success_mocked():
    """All 3 datasets succeed -> overall_status == 'success'."""
    mock_daily_store = MagicMock()
    mock_daily_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = [date(2026, 8, 28)]

    mock_daily_svc = MagicMock()
    mock_daily_svc.refresh_symbols.return_value = {"symbols_fetched": 10, "rows_written": 500}
    mock_daily_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 500, "failed_dates": []
    }
    mock_inst_svc = MagicMock()
    mock_inst_svc.refresh_dates.return_value = {"dates_requested": 1, "dates_fetched": 1, "total_rows_written": 2000, "failed_dates": []}
    mock_margin_svc = MagicMock()
    mock_margin_svc.refresh_dates.return_value = {"dates_requested": 1, "dates_fetched": 1, "total_rows_written": 1500, "failed_dates": []}

    svc = TaiwanDailyUpdateService(
        daily_store=mock_daily_store,
        inst_store=mock_inst_store,
        margin_store=mock_margin_store,
        daily_service=mock_daily_svc,
        inst_service=mock_inst_svc,
        margin_service=mock_margin_svc,
    )

    result = svc.run_update(target_date=date(2026, 8, 31), refresh_daily=True)
    assert result.overall_status == "success"
    assert result.daily.status == "success"
    assert result.institutional.status == "success"
    assert result.margin.status == "success"
    assert result.daily.rows_written == 500
    assert result.institutional.rows_written == 2000
    assert result.margin.rows_written == 1500


def test_orchestrator_partial_failure_mocked():
    """Institutional succeeds, Margin fails -> overall_status == 'partial'."""
    mock_daily_store = MagicMock()
    mock_daily_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = [date(2026, 8, 28)]

    mock_inst_svc = MagicMock()
    mock_inst_svc.refresh_dates.return_value = {"dates_requested": 1, "dates_fetched": 1, "total_rows_written": 2000, "failed_dates": []}
    mock_margin_svc = MagicMock()
    mock_margin_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 0, "total_rows_written": 0,
        "failed_dates": [{"date": "2026-08-31", "error": "HTTP 502 Gateway Error"}],
    }

    svc = TaiwanDailyUpdateService(
        daily_store=mock_daily_store,
        inst_store=mock_inst_store,
        margin_store=mock_margin_store,
        inst_service=mock_inst_svc,
        margin_service=mock_margin_svc,
    )

    result = svc.run_update(target_date=date(2026, 8, 31), refresh_daily=False)
    assert result.overall_status == "partial"
    assert result.daily.status == "skipped"
    assert result.institutional.status == "success"
    assert result.margin.status == "failed"


def test_orchestrator_all_failed_mocked():
    """All executed services fail -> overall_status == 'failed'."""
    mock_daily_store = MagicMock()
    mock_daily_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = [date(2026, 8, 28)]

    mock_inst_svc = MagicMock()
    mock_inst_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 0, "total_rows_written": 0,
        "failed_dates": [{"date": "2026-08-31", "error": "Network timeout"}],
    }
    mock_margin_svc = MagicMock()
    mock_margin_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 0, "total_rows_written": 0,
        "failed_dates": [{"date": "2026-08-31", "error": "Network timeout"}],
    }

    svc = TaiwanDailyUpdateService(
        daily_store=mock_daily_store,
        inst_store=mock_inst_store,
        margin_store=mock_margin_store,
        inst_service=mock_inst_svc,
        margin_service=mock_margin_svc,
    )

    result = svc.run_update(target_date=date(2026, 8, 31), refresh_daily=False)
    assert result.overall_status == "failed"
    assert result.institutional.status == "failed"
    assert result.margin.status == "failed"


def test_orchestrator_idempotency_already_current():
    """If all stores are already at target date, no provider refresh calls are dispatched."""
    mock_daily_store = MagicMock()
    mock_daily_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = [date(2026, 8, 28)]
    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = [date(2026, 8, 28)]

    mock_daily_svc = MagicMock()
    mock_inst_svc = MagicMock()
    mock_margin_svc = MagicMock()

    svc = TaiwanDailyUpdateService(
        daily_store=mock_daily_store,
        inst_store=mock_inst_store,
        margin_store=mock_margin_store,
        daily_service=mock_daily_svc,
        inst_service=mock_inst_svc,
        margin_service=mock_margin_svc,
    )

    result = svc.run_update(target_date=date(2026, 8, 28), refresh_daily=True)
    assert result.overall_status == "success"
    # Neither service was called with missing dates
    mock_daily_svc.refresh_symbols.assert_not_called()
    mock_inst_svc.refresh_dates.assert_not_called()
    mock_margin_svc.refresh_dates.assert_not_called()
    assert result.institutional.dates_skipped == 1
    assert result.margin.dates_skipped == 1


def test_api_taiwan_data_status_endpoint():
    """GET /api/taiwan/data-status returns typed freshness without external calls."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    resp = client.get("/api/taiwan/data-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "daily_as_of" in data
    assert "institutional_as_of" in data
    assert "margin_as_of" in data
    assert "target_latest_trading_date" in data
    assert "is_fully_current" in data
    assert data["daily_as_of"] == "2026-08-28"


def test_closure_2026_07_10_not_fabricated():
    """Known closure 2026-07-10 (typhoon closure) is not fabricated or treated as error."""
    closure_date = date(2026, 7, 10)
    cal = TaiwanTradingCalendar(known_holidays={closure_date})

    # Range covering closure date
    start_end = resolve_missing_date_range(
        earliest_available=date(2026, 7, 9),
        target_latest=date(2026, 7, 13),
        calendar=cal,
    )
    assert start_end is not None
    start_d, end_d = start_end
    # Since July 10 is closure and July 11-12 is weekend, next trading date is July 13
    assert start_d == date(2026, 7, 13)
    assert end_d == date(2026, 7, 13)

