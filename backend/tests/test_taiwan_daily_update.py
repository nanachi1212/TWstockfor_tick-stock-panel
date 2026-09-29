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

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_update import (
    TaiwanDailyUpdateService,
    resolve_missing_date_range,
    resolve_target_latest_trading_date,
)
from app.taiwan.institutional_margin_refresh import (
    TaiwanInstitutionalRefreshService,
    TaiwanMarginRefreshService,
)
from app.taiwan.observed_universe import ObservedUniverseStore, is_potential_market_session
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


def test_date_resolution_confirmed_holiday(tmp_path: Path):
    """Confirmed holiday resolves to previous available trading day."""
    holiday = date(2026, 9, 2)
    cal = TaiwanTradingCalendar(known_holidays={holiday})
    wed_evening = datetime(2026, 9, 2, 18, 0, tzinfo=TAIPEI_TZ)
    target = resolve_target_latest_trading_date(
        calendar=cal, as_of_dt=wed_evening,
        evidence_store=ObservedUniverseStore(tmp_path / "observed"),
    )
    assert target == date(2026, 9, 1)


def _september_closure_evidence(tmp_path: Path) -> ObservedUniverseStore:
    store = ObservedUniverseStore(tmp_path / "observed")
    for exchange in ("TWSE", "TPEX"):
        for day in (date(2026, 9, 25), date(2026, 9, 28)):
            store.write(exchange, day, [], confirmed_non_trading_source="twse:holidaySchedule")
    return store


def test_official_holiday_sequence_is_current_before_publication(tmp_path: Path):
    evidence = _september_closure_evidence(tmp_path)
    calendar = TaiwanTradingCalendar(known_trading_days={date(2026, 9, 24)})
    assert [is_potential_market_session(date(2026, 9, day), calendar, evidence)
            for day in (24, 25, 26, 27, 28)] == [True, False, False, False, False]
    now = datetime(2026, 9, 29, 10, 0, tzinfo=TAIPEI_TZ)
    assert resolve_target_latest_trading_date(calendar, now, evidence_store=evidence) == date(2026, 9, 24)
    assert resolve_missing_date_range(date(2026, 9, 24), date(2026, 9, 28),
                                      calendar, evidence) is None

    stores = [MagicMock() for _ in range(3)]
    for store in stores:
        store.available_dates.return_value = [date(2026, 9, 24)]
    svc = TaiwanDailyUpdateService(daily_store=stores[0], inst_store=stores[1],
                                   margin_store=stores[2], calendar=calendar,
                                   evidence_store=evidence)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.taiwan.daily_update.taipei_now", lambda: now)
        status = svc.get_freshness()
    assert status.target_latest_trading_date == "2026-09-24"
    assert status.is_fully_current
    assert (status.daily_status, status.institutional_status, status.margin_status) == (
        "current", "current", "current",
    )
    assert (status.daily_days_behind, status.institutional_days_behind,
            status.margin_days_behind) == (0, 0, 0)


def test_real_trading_day_missing_after_cutoff_is_stale(tmp_path: Path):
    evidence = _september_closure_evidence(tmp_path)
    calendar = TaiwanTradingCalendar()
    before = datetime(2026, 9, 29, 15, 59, tzinfo=TAIPEI_TZ)
    after = datetime(2026, 9, 29, 16, 0, tzinfo=TAIPEI_TZ)
    assert resolve_target_latest_trading_date(calendar, before, evidence_store=evidence) == date(2026, 9, 24)
    assert resolve_target_latest_trading_date(calendar, after, evidence_store=evidence) == date(2026, 9, 29)
    assert resolve_target_latest_trading_date(
        calendar, datetime(2026, 9, 29, 7, 59, tzinfo=UTC),
        evidence_store=evidence,
    ) == date(2026, 9, 24)
    assert resolve_target_latest_trading_date(
        calendar, datetime(2026, 9, 29, 8, 0, tzinfo=UTC),
        evidence_store=evidence,
    ) == date(2026, 9, 29)
    stores = [MagicMock() for _ in range(3)]
    for store in stores:
        store.available_dates.return_value = [date(2026, 9, 24)]
    svc = TaiwanDailyUpdateService(daily_store=stores[0], inst_store=stores[1],
                                   margin_store=stores[2], calendar=calendar,
                                   evidence_store=evidence)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.taiwan.daily_update.taipei_now", lambda: after)
        status = svc.get_freshness()
    assert status.target_latest_trading_date == "2026-09-29"
    assert status.daily_status == "stale"
    assert status.daily_days_behind == 1


def test_saturday_official_session_overrides_weekend(tmp_path: Path):
    evidence = ObservedUniverseStore(tmp_path / "observed")
    saturday = date(2026, 9, 26)
    evidence.write("TWSE", saturday, [{
        "date": saturday, "raw_code": "2330", "exchange": "TWSE", "observed": True,
        "raw_name": "test", "raw_source_category": "stock",
        "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
        "volume": 1.0, "amount": 100.0, "instrument_type": None,
        "instrument_type_status": "data_insufficient", "source": "test:official",
        "retrieved_at": "2026-09-26T16:30:00+08:00",
    }])
    calendar = TaiwanTradingCalendar()
    assert is_potential_market_session(saturday, calendar, evidence)
    assert resolve_target_latest_trading_date(
        calendar, datetime(2026, 9, 27, 10, 0, tzinfo=TAIPEI_TZ),
        evidence_store=evidence,
    ) == saturday


def test_holiday_scheduler_does_not_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    evidence = _september_closure_evidence(tmp_path)
    stores = [MagicMock() for _ in range(3)]
    services = [MagicMock() for _ in range(3)]
    for store in stores:
        store.available_dates.return_value = [date(2026, 9, 24)]
    svc = TaiwanDailyUpdateService(
        daily_store=stores[0], inst_store=stores[1], margin_store=stores[2],
        daily_service=services[0], inst_service=services[1], margin_service=services[2],
        evidence_store=evidence,
    )
    monkeypatch.setattr("app.taiwan.daily_update.taipei_now",
                        lambda: datetime(2026, 9, 28, 16, 30, tzinfo=TAIPEI_TZ))
    result = svc.run_update()
    assert result.target_latest_trading_date == "2026-09-24"
    assert result.overall_status == "success"
    for service in services:
        service.refresh_dates.assert_not_called()


def test_official_closure_is_skipped_by_all_incremental_fetchers(tmp_path: Path):
    evidence = _september_closure_evidence(tmp_path)
    calendar = TaiwanTradingCalendar()
    services = (
        TaiwanDailyRefreshService(store=MagicMock(), snapshot_adapter=MagicMock(),
                                  calendar=calendar, evidence_store=evidence),
        TaiwanInstitutionalRefreshService(store=MagicMock(), provider=MagicMock(),
                                          calendar=calendar, evidence_store=evidence),
        TaiwanMarginRefreshService(store=MagicMock(), provider=MagicMock(),
                                   calendar=calendar, evidence_store=evidence),
    )
    for service in services:
        service._store.available_dates.return_value = []
        result = service.refresh_dates(date(2026, 9, 25), date(2026, 9, 28))
        assert result["dates_requested"] == 0
        assert result["failed_dates"] == []


def test_data_status_api_uses_official_closures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    evidence = _september_closure_evidence(tmp_path)
    stores = [MagicMock() for _ in range(3)]
    for store in stores:
        store.available_dates.return_value = [date(2026, 9, 24)]
    svc = TaiwanDailyUpdateService(daily_store=stores[0], inst_store=stores[1],
                                   margin_store=stores[2], evidence_store=evidence)
    monkeypatch.setattr("app.taiwan.daily_update.taipei_now",
                        lambda: datetime(2026, 9, 29, 10, 0, tzinfo=TAIPEI_TZ))
    monkeypatch.setattr("app.api.taiwan.TaiwanDailyUpdateService", lambda: svc)
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get("/api/taiwan/data-status")
    assert response.status_code == 200
    body = response.json()
    assert body["daily_as_of"] == body["target_latest_trading_date"] == "2026-09-24"
    assert body["daily_status"] == "current"
    assert body["daily_days_behind"] == 0


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


def test_api_taiwan_data_status_endpoint(taiwan_data_env):
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
    assert "daily_status" in data
    assert "institutional_status" in data
    assert "margin_status" in data
    assert "scheduler_enabled" in data
    assert "scheduled_update_time" in data
    assert "scheduled_timezone" in data
    assert data["daily_as_of"] == "2026-08-28"
    assert data["scheduler_enabled"] is True
    assert data["scheduled_update_time"] == "16:30"
    assert data["scheduled_timezone"] == "Asia/Taipei"


def test_status_scenarios_deterministic():
    """Test all status calculation combinations: all current, stale variants, all unavailable."""
    cal = TaiwanTradingCalendar()
    target = date(2026, 8, 31)

    mock_d = MagicMock()
    mock_i = MagicMock()
    mock_m = MagicMock()
    svc = TaiwanDailyUpdateService(
        daily_store=mock_d, inst_store=mock_i, margin_store=mock_m, calendar=cal
    )

    # A. All datasets current
    mock_d.available_dates.return_value = [target]
    mock_i.available_dates.return_value = [target]
    mock_m.available_dates.return_value = [target]
    st = svc.get_freshness(target_date=target)
    assert st.is_fully_current is True
    assert st.daily_status == "current"
    assert st.institutional_status == "current"
    assert st.margin_status == "current"

    # B. Daily stale
    mock_d.available_dates.return_value = [date(2026, 8, 28)]
    mock_i.available_dates.return_value = [target]
    mock_m.available_dates.return_value = [target]
    st = svc.get_freshness(target_date=target)
    assert st.is_fully_current is False
    assert st.daily_status == "stale"
    assert st.daily_days_behind == 1
    assert st.institutional_status == "current"
    assert st.margin_status == "current"

    # C. Institutional stale
    mock_d.available_dates.return_value = [target]
    mock_i.available_dates.return_value = [date(2026, 8, 28)]
    mock_m.available_dates.return_value = [target]
    st = svc.get_freshness(target_date=target)
    assert st.is_fully_current is False
    assert st.daily_status == "current"
    assert st.institutional_status == "stale"
    assert st.margin_status == "current"

    # D. Margin stale
    mock_d.available_dates.return_value = [target]
    mock_i.available_dates.return_value = [target]
    mock_m.available_dates.return_value = [date(2026, 8, 28)]
    st = svc.get_freshness(target_date=target)
    assert st.is_fully_current is False
    assert st.daily_status == "current"
    assert st.institutional_status == "current"
    assert st.margin_status == "stale"

    # E. All unavailable
    mock_d.available_dates.return_value = []
    mock_i.available_dates.return_value = []
    mock_m.available_dates.return_value = []
    st = svc.get_freshness(target_date=target)
    assert st.is_fully_current is False
    assert st.daily_status == "unavailable"
    assert st.institutional_status == "unavailable"
    assert st.margin_status == "unavailable"


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

