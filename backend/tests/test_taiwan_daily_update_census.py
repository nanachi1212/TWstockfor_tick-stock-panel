"""The daily update writes the target session's official census once its daily snapshot is stored."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

PREV = date(2026, 8, 28)
TARGET = date(2026, 8, 31)


class FakeDailyStore:
    def __init__(self, dates: list[date]) -> None:
        self.dates = list(dates)

    def available_dates(self) -> list[date]:
        return sorted(self.dates)


class FakeCensus:
    """Stands in for the TWSE/TPEx HTTP layer of ObservedUniverseCensus."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, date]] = []

    def _fetch(self, exchange: str, day: date) -> list[dict]:
        self.calls.append((exchange, day))
        if self.error:
            raise self.error
        return [{"date": day, "raw_code": "2330", "exchange": exchange, "observed": True}]

    def fetch_twse(self, day: date) -> list[dict]:
        return self._fetch("TWSE", day)

    def fetch_tpex(self, day: date) -> list[dict]:
        return self._fetch("TPEX", day)


def _service(tmp_path: Path, daily: FakeDailyStore, *, publishes: bool, census_worker=None,
             census: FakeCensus | None = None):
    evidence = ObservedUniverseStore(tmp_path / "observed")
    calendar = TaiwanTradingCalendar()
    daily_service = MagicMock()

    def refresh_dates(start_date, end_date, force=False):
        if not publishes:
            return {"dates_requested": 1, "dates_fetched": 0, "total_rows_written": 0,
                    "failed_dates": [{"date": str(TARGET), "status": "unpublished"}]}
        daily.dates.append(TARGET)
        return {"dates_requested": 1, "dates_fetched": 1, "total_rows_written": 1800, "failed_dates": []}

    daily_service.refresh_dates.side_effect = refresh_dates
    current = [MagicMock(), MagicMock()]
    for store in current:
        store.available_dates.return_value = [TARGET]
    if census is not None:
        census_worker = TaiwanHistoricalBackfillWorker(
            data_dir=tmp_path, census_store=evidence, calendar=calendar, census=census,
        )
    svc = TaiwanDailyUpdateService(
        daily_store=daily, inst_store=current[0], margin_store=current[1],
        daily_service=daily_service, inst_service=MagicMock(), margin_service=MagicMock(),
        calendar=calendar, evidence_store=evidence, census_worker=census_worker,
    )
    return svc, evidence


def _observed(evidence: ObservedUniverseStore, day: date) -> bool:
    return all(evidence.has(ex, day) and evidence.partition_status(ex, day) == "observed"
               for ex in ("TWSE", "TPEX"))


def test_new_daily_session_writes_target_census(tmp_path: Path):
    census = FakeCensus()
    svc, evidence = _service(tmp_path, FakeDailyStore([PREV]), publishes=True, census=census)

    result = svc.run_update(target_date=TARGET)

    assert result.overall_status == "success"
    assert sorted(census.calls) == [("TPEX", TARGET), ("TWSE", TARGET)]
    assert _observed(evidence, TARGET)
    assert result.census["status"] == "success"
    assert {ex: stats["sessions"] for ex, stats in result.census["exchanges"].items()} == {"TWSE": 1, "TPEX": 1}


@pytest.mark.parametrize("failure", ["fetch", "worker"])
def test_census_failure_keeps_daily_update_successful(tmp_path: Path, failure: str):
    daily = FakeDailyStore([PREV])
    census = FakeCensus(error=RuntimeError("TWSE 503")) if failure == "fetch" else None
    broken = SimpleNamespace(run_census=MagicMock(side_effect=OSError("disk full")))
    svc, evidence = _service(tmp_path, daily, publishes=True, census=census,
                             census_worker=None if census else broken)

    result = svc.run_update(target_date=TARGET)

    assert result.overall_status == "success"
    assert result.daily.status == "success" and result.daily.rows_written == 1800
    assert daily.available_dates() == [PREV, TARGET]
    assert result.census["status"] == "error"
    assert not _observed(evidence, TARGET)


def test_unpublished_target_does_not_fetch_census(tmp_path: Path):
    census = FakeCensus()
    svc, evidence = _service(tmp_path, FakeDailyStore([PREV]), publishes=False, census=census)

    result = svc.run_update(target_date=TARGET)

    assert result.census is None
    assert census.calls == []
    assert not evidence.has("TWSE", TARGET) and not evidence.has("TPEX", TARGET)


def test_observed_target_census_is_not_refetched(tmp_path: Path):
    census = FakeCensus()
    svc, evidence = _service(tmp_path, FakeDailyStore([PREV, TARGET]), publishes=True, census=census)
    for exchange in ("TWSE", "TPEX"):
        evidence.write(exchange, TARGET, [{"date": TARGET, "raw_code": "1101", "exchange": exchange,
                                           "observed": True}])

    result = svc.run_update(target_date=TARGET)

    assert census.calls == []
    assert result.census["status"] == "success"
    assert {ex: stats["sessions"] for ex, stats in result.census["exchanges"].items()} == {"TWSE": 0, "TPEX": 0}


def test_injected_stores_without_worker_skip_census(tmp_path: Path):
    """Bootstrap's readiness refresh injects its stores and runs its own census call."""
    svc, _ = _service(tmp_path, FakeDailyStore([PREV, TARGET]), publishes=True)

    assert svc.run_update(target_date=TARGET, refresh_daily=False).census is None


def test_empty_target_census_is_rechecked_once_daily_is_published(tmp_path: Path):
    """An empty response saved before publication must not block the census forever."""
    census = FakeCensus()
    svc, evidence = _service(tmp_path, FakeDailyStore([PREV, TARGET]), publishes=True, census=census)
    for exchange in ("TWSE", "TPEX"):
        evidence.write(exchange, TARGET, [])

    result = svc.run_update(target_date=TARGET)

    assert sorted(census.calls) == [("TPEX", TARGET), ("TWSE", TARGET)]
    assert _observed(evidence, TARGET)
    assert result.census["status"] == "success"
