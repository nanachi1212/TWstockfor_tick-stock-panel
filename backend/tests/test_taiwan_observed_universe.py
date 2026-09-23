# ruff: noqa: RUF001 -- fixtures mirror official Chinese payload text exactly.
"""A2a/A2b regression tests — historical census, classification, worker runtime.

Every test runs offline: official payloads are inline fixtures shaped exactly
like the real responses recorded in docs/taiwan-historical-universe-probe.md.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.taiwan.backfill_worker import (
    MAX_ATTEMPTS,
    StopSignal,
    TaiwanHistoricalBackfillWorker,
    WorkerBusyError,
    WorkerLock,
    WorkerState,
)
from app.taiwan.historical_classification import (
    REQUESTS_PER_DATE,
    TWSE_INDUSTRY_TYPES,
    TWSE_UNSUPPORTED_TYPES,
    HistoricalClassificationStore,
    TwseHistoricalClassifier,
    classification_queue,
    verified_stock_codes,
)
from app.taiwan.observed_universe import (
    ObservedUniverseCensus,
    ObservedUniverseStore,
    first_observed_dates,
    parse_tpex_census,
    parse_twse_census,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

PROBE_DATE = date(2024, 6, 3)

# 2024-06-03 samples from the probe: all traded that day, all delisted later.
TWSE_DELISTED_SAMPLES = ("1701", "2888", "2809")
TPEX_DELISTED_SAMPLES = ("5371", "4130", "3426", "4987", "5236")
# Listed on 2024-06-03 but already suspended from trading — absent from the
# official snapshot months before their 2024-11-19 delisting.
TWSE_SUSPENDED_SAMPLES = ("2358", "2443")


# ── Fixtures shaped like the real official payloads ────────────

def _twse_payload(codes: dict[str, str]) -> dict:
    return {
        "stat": "OK",
        "tables": [
            {"title": "113年06月03日 大盤統計資訊", "fields": ["成交統計"], "data": []},
            {
                "title": "113年06月03日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))",
                "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
                           "開盤價", "最高價", "最低價", "收盤價"],
                "data": [
                    [code, name, "1,000", "10", "22,050", "22.00", "22.10", "21.95", "22.05"]
                    for code, name in codes.items()
                ],
            },
        ],
    }


def _tpex_payload(codes: dict[str, str]) -> dict:
    return {
        "tables": [
            {
                "title": "上櫃股票行情",
                "fields": ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價",
                           "成交股數", "成交金額(元)"],
                "data": [
                    [code, name, "111.00", "0.50", "110.00", "112.00", "109.50", "111.20",
                     "5,000", "555,000"]
                    for code, name in codes.items()
                ],
            },
            {"title": "管理股票", "fields": ["代號", "名稱", "收盤", "漲跌", "開盤",
                                             "最高", "最低", "均價", "成交股數",
                                             "成交金額(元)"], "data": []},
        ],
    }


TWSE_FIXTURE = _twse_payload({
    "2330": "台積電", "0050": "元大台灣50",
    "1701": "中化", "2888": "新光金", "2809": "京城銀",
})
TPEX_FIXTURE = _tpex_payload({
    "8069": "元太", "006201": "寶富櫃",
    **{c: n for c, n in zip(TPEX_DELISTED_SAMPLES,
                            ("中強光電", "健亞", "台興電子", "科誠", "凌陽創新"), strict=True)},
})

TWSE_HOLIDAY = {"stat": "很抱歉，沒有符合條件的資料!", "tables": []}
TPEX_HOLIDAY = {"tables": [{"title": "上櫃股票行情", "fields": [], "data": []}]}


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def raise_for_status(self) -> None:
        return None


class _StubClient:
    """Serves canned official payloads; records every URL it was asked for."""

    def __init__(self, router) -> None:
        self.router = router
        self.calls: list[str] = []

    def get(self, url: str) -> _StubResponse:
        self.calls.append(url)
        return _StubResponse(self.router(url))

    def close(self) -> None:
        return None


def _census_router(url: str) -> dict:
    if "twse.com.tw" in url:
        return TWSE_FIXTURE if "20240603" in url else TWSE_HOLIDAY
    return TPEX_FIXTURE if "113/06/03" in url else TPEX_HOLIDAY


def _census(tmp_path: Path) -> tuple[ObservedUniverseCensus, ObservedUniverseStore, _StubClient]:
    store = ObservedUniverseStore(tmp_path / "observed_universe")
    client = _StubClient(_census_router)
    return ObservedUniverseCensus(store=store, client=client), store, client


# ── Historical mode ignores the current allowlist ──────────────

def test_historical_census_keeps_securities_absent_from_current_master(tmp_path: Path) -> None:
    """The 8 probe samples are all delisted today; the census must still see them."""
    census, store, _ = _census(tmp_path)
    store.write("TWSE", PROBE_DATE, census.fetch_twse(PROBE_DATE))
    store.write("TPEX", PROBE_DATE, census.fetch_tpex(PROBE_DATE))

    observed = store.read()
    seen = set(observed["raw_code"].to_list())
    for code in TWSE_DELISTED_SAMPLES + TPEX_DELISTED_SAMPLES:
        assert code in seen, code
    assert observed.filter(observed["observed"].not_())["raw_code"].len() == 0


def test_census_never_consults_the_security_master(tmp_path: Path, monkeypatch) -> None:
    """Hard guard: touching get_security_master from the census path is a bug."""
    def explode(*args, **kwargs):
        raise AssertionError("historical census must not use the current Security Master")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", explode)
    census, _store, _ = _census(tmp_path)
    rows = census.fetch_twse(PROBE_DATE)
    assert len(rows) == 5


def test_current_product_snapshot_adapter_is_untouched() -> None:
    """The live product path still filters by the current allowlist."""
    import inspect

    from app.taiwan.providers import snapshot_provider

    source = inspect.getsource(snapshot_provider)
    assert "_twse_allowlist" in source and "_tpex_allowlist" in source
    assert "if code not in self._twse_allowlist:" in source
    # and the census never reaches for the current master (docstrings aside)
    from app.taiwan import observed_universe

    code = "".join(
        line for line in inspect.getsource(observed_universe).splitlines(keepends=True)
        if not line.lstrip().startswith("#")
    )
    for forbidden in ("get_security_master", "TaiwanSecurityMaster",
                      "snapshot_provider", "_twse_allowlist", "_tpex_allowlist"):
        assert forbidden not in code, forbidden


# ── Absence is not a delisting ─────────────────────────────────

def test_absence_from_snapshot_is_not_a_delisting(tmp_path: Path) -> None:
    """2358 / 2443 were listed but suspended; the census must not imply status."""
    census, store, _ = _census(tmp_path)
    store.write("TWSE", PROBE_DATE, census.fetch_twse(PROBE_DATE))

    frame = store.read("TWSE")
    seen = set(frame["raw_code"].to_list())
    for code in TWSE_SUSPENDED_SAMPLES:
        assert code not in seen

    # The schema offers no way to record a listing status at all.
    for forbidden in ("listing_status", "delisting_date", "transition_type", "delisted"):
        assert forbidden not in frame.columns


def test_census_schema_never_guesses_instrument_type(tmp_path: Path) -> None:
    """A 4-digit code is not evidence of being a stock."""
    census, store, _ = _census(tmp_path)
    store.write("TPEX", PROBE_DATE, census.fetch_tpex(PROBE_DATE))
    frame = store.read("TPEX")
    assert frame["instrument_type"].null_count() == frame.height
    assert set(frame["instrument_type_status"].to_list()) == {"data_insufficient"}


# ── Storage: resume, idempotency, retry ────────────────────────

def test_official_no_data_writes_an_unverified_empty_partition(tmp_path: Path) -> None:
    census, store, _client = _census(tmp_path)
    empty_day = date(2024, 6, 4)  # router serves an ambiguous "no data" payload
    assert store.write("TWSE", empty_day, census.fetch_twse(empty_day)) == 0
    assert store.has("TWSE", empty_day)  # terminal for processing
    assert store.partition_status("TWSE", empty_day) == "empty_unknown"


def test_census_is_idempotent_and_resumable(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    first = worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=5)
    second = worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=5)

    assert first["TWSE"]["sessions"] == 1
    assert second["TWSE"]["sessions"] == 0, "completed session must not be refetched"
    assert worker.census_store.read("TWSE").height == 5


def test_failed_date_is_retried_on_the_next_run(tmp_path: Path) -> None:
    state = {"fail": True}

    def flaky(url: str) -> dict:
        if "twse.com.tw" in url and state["fail"]:
            state["fail"] = False
            raise ConnectionError("boom")
        return _census_router(url)

    worker = _worker(tmp_path, router=flaky)
    first = worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=5)
    assert first["TWSE"]["failed"] == [PROBE_DATE.isoformat()]
    assert not worker.census_store.has("TWSE", PROBE_DATE)

    second = worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=5)
    assert second["TWSE"]["sessions"] == 1
    assert worker.census_store.has("TWSE", PROBE_DATE)


def test_repeatedly_failing_date_is_parked_after_max_attempts(tmp_path: Path) -> None:
    def always_fail(url: str) -> dict:
        if "twse.com.tw" in url:
            raise ConnectionError("permanently broken")
        return _census_router(url)

    worker = _worker(tmp_path, router=always_fail)
    for _ in range(MAX_ATTEMPTS):
        worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=5)
    assert worker.state.exhausted("census:TWSE", PROBE_DATE)

    after = worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=5)
    assert after["TWSE"]["failed"] == [], "parked dates must stop consuming requests"
    assert PROBE_DATE.isoformat() in worker.state.parked("census:TWSE")


def test_daily_session_budget_bounds_the_run(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    end = PROBE_DATE + timedelta(days=10)
    result = worker.run_census(PROBE_DATE, end, session_budget=3)
    assert worker.census_store.completed_dates("TWSE") == {
        PROBE_DATE, PROBE_DATE + timedelta(days=1), PROBE_DATE + timedelta(days=2)
    }
    assert result["TWSE"]["sessions"] + result["TWSE"]["empty"] == 3


def test_stop_signal_ends_the_run_cleanly(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    stop = StopSignal()
    stop.set()
    result = worker.run_census(PROBE_DATE, PROBE_DATE + timedelta(days=10),
                               session_budget=10, should_stop=stop)
    assert result["TWSE"]["stopped_early"] is True
    assert worker.census_store.completed_dates("TWSE") == set()


# ── Worker lock and checkpoint ─────────────────────────────────

def test_single_instance_lock_blocks_a_second_worker(tmp_path: Path) -> None:
    lock = WorkerLock(tmp_path / "w.lock")
    other = WorkerLock(tmp_path / "w.lock")
    lock.acquire()
    try:
        with pytest.raises(WorkerBusyError):
            other.acquire()
    finally:
        lock.release()
    other.acquire()  # released, so now free
    other.release()


def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    # A reused PID's creation time cannot match this impossible value; the
    # prior lock owner is dead even though this PID currently exists.
    path = tmp_path / "w.lock"
    path.write_text(json.dumps({"pid": os.getpid(), "process_created_at": -1}))
    reclaimed = WorkerLock(tmp_path / "w.lock", max_age=timedelta(seconds=0))
    reclaimed.acquire()  # must not raise
    reclaimed.release()


@pytest.mark.parametrize("force", [False, True])
def test_active_long_run_is_never_reclaimed_by_age(tmp_path: Path, force: bool) -> None:
    lock = WorkerLock(tmp_path / "w.lock", max_age=timedelta(seconds=0))
    lock.acquire()
    try:
        with pytest.raises(WorkerBusyError):
            WorkerLock(lock.path, max_age=timedelta(seconds=0)).acquire(force=force)
    finally:
        lock.release()


def test_releasing_old_identity_preserves_replacement_lock(tmp_path: Path) -> None:
    lock = WorkerLock(tmp_path / "w.lock")
    lock.acquire()
    replacement = {"pid": os.getpid(), "token": "another-owner"}
    lock.path.write_text(json.dumps(replacement))
    lock.release()
    assert json.loads(lock.path.read_text()) == replacement


def test_concurrent_stale_reclamation_admits_one_owner(tmp_path: Path) -> None:
    path = tmp_path / "w.lock"
    path.write_text(json.dumps({"pid": os.getpid(), "process_created_at": -1}))
    locks = [WorkerLock(path, max_age=timedelta(seconds=0)) for _ in range(8)]
    def acquire(lock):
        try:
            lock.acquire()
            return lock
        except WorkerBusyError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [lock for lock in pool.map(acquire, locks) if lock is not None]
    assert len(winners) == 1
    winners[0].release()
    assert not path.exists()


def test_legacy_live_pid_lock_is_preserved_without_birth_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "w.lock"
    path.write_text(json.dumps({"pid": os.getpid(), "started_at": "2000-01-01T00:00:00+08:00"}))
    with pytest.raises(WorkerBusyError):
        WorkerLock(path, max_age=timedelta(seconds=0)).acquire()


def test_successful_classification_retry_clears_old_attempts(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=1)
    worker._classifier = TwseHistoricalClassifier(
        store=worker.classification_store, client=_StubClient(_classification_router))
    for _ in range(MAX_ATTEMPTS - 1):
        worker.state.record_failure("classify:TWSE", PROBE_DATE, "temporary")
    result = worker.run_classification(request_budget=REQUESTS_PER_DATE)
    assert result["completed_dates"] == [PROBE_DATE.isoformat()]
    assert worker.state.attempts("classify:TWSE", PROBE_DATE) == 0
    worker.state.record_failure("classify:TWSE", PROBE_DATE, "upgrade temporarily failed")
    assert not worker.state.exhausted("classify:TWSE", PROBE_DATE)


def test_worker_state_survives_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = WorkerState(path)
    state.record_failure("census:TWSE", PROBE_DATE, "network down")
    state.save()

    reloaded = WorkerState(path)
    assert reloaded.attempts("census:TWSE", PROBE_DATE) == 1
    assert reloaded.data["providers"]["census:TWSE"]["failures"] == 1

    reloaded.record_success("census:TWSE", PROBE_DATE)
    assert reloaded.attempts("census:TWSE", PROBE_DATE) == 0
    assert reloaded.data["last_success_at"] is not None


def test_corrupt_state_file_does_not_crash_the_worker(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = WorkerState(path)
    assert state.data["attempts"] == {}


# ── A2b classification ─────────────────────────────────────────

def _classification_router(url: str) -> dict:
    """2330 sits in an industry table, 0050 in the ETF table, 9999 in neither."""
    def table(codes):
        return {"stat": "OK", "tables": [{
            "title": "每日收盤行情(測試)",
            "fields": ["證券代號", "證券名稱"],
            "data": [[c, c] for c in codes],
        }]}

    if "type=24" in url:
        return table(["2330"])
    if "type=07" in url or "type=22" in url:
        return table(["1701"])          # legacy umbrella + split category
    if f"type={'0099P'}" in url:
        return table(["0050"])
    return {"stat": "OK", "tables": [{"title": "每日收盤行情(空)",
                                      "fields": ["證券代號", "證券名稱"], "data": []}]}


def test_classification_uses_only_that_dates_official_response(tmp_path: Path) -> None:
    store = HistoricalClassificationStore(tmp_path / "cls")
    client = _StubClient(_classification_router)
    classifier = TwseHistoricalClassifier(store=store, client=client)
    rows = classifier.classify_date(PROBE_DATE)

    assert len(client.calls) == REQUESTS_PER_DATE == len(TWSE_INDUSTRY_TYPES) + 1 + len(TWSE_UNSUPPORTED_TYPES)
    for call in client.calls:
        assert "date=20240603" in call, "must query the historical date, not today"

    by_code = {r["code"]: r for r in rows}
    assert by_code["2330"]["instrument_type"] is None  # industry is not common-share evidence
    assert by_code["1701"]["instrument_type"] is None  # dedup across 07/22
    assert by_code["2330"]["classification_status"] == "data_insufficient"
    assert by_code["0050"]["instrument_type"] == "etf"
    assert "9999" not in by_code, "unclassifiable codes must be left out (fail-closed)"


def test_classification_never_claims_a_point_in_time_industry(tmp_path: Path) -> None:
    """Probe §4.3: the official industry label is current and non-unique."""
    store = HistoricalClassificationStore(tmp_path / "cls")
    classifier = TwseHistoricalClassifier(store=store, client=_StubClient(_classification_router))
    rows = classifier.classify_date(PROBE_DATE)
    assert rows
    for row in rows:
        assert row["industry"] is None
        assert row["industry_status"] == "data_insufficient"
        expected = "verified" if row["instrument_type"] == "etf" else "data_insufficient"
        assert row["classification_status"] == expected
        assert row["classification_effective_from"] == PROBE_DATE
        assert PROBE_DATE.isoformat() in row["classification_source"]


def test_classification_queue_is_one_job_per_first_seen_date(tmp_path: Path) -> None:
    census = ObservedUniverseStore(tmp_path / "observed_universe")
    store = HistoricalClassificationStore(tmp_path / "cls")

    def row(code: str, day: date) -> dict:
        return {"date": day, "raw_code": code, "exchange": "TWSE", "observed": True,
                "raw_name": code, "raw_source_category": "t", "open": None, "high": None,
                "low": None, "close": None, "volume": None, "amount": None,
                "instrument_type": None, "instrument_type_status": "data_insufficient",
                "source": "s", "retrieved_at": "t"}

    day1, day2 = date(2015, 1, 5), date(2015, 1, 6)
    census.write("TWSE", day1, [row("2330", day1), row("1101", day1)])
    census.write("TWSE", day2, [row("2330", day2), row("9999", day2)])

    assert first_observed_dates(census, "TWSE") == {
        "2330": day1, "1101": day1, "9999": day2}
    assert classification_queue(census, store) == [day1, day2]

    store.write(day1, [])
    assert classification_queue(census, store) == [day2]


def test_classification_request_budget_is_respected(tmp_path: Path) -> None:
    store = HistoricalClassificationStore(tmp_path / "cls")
    classifier = TwseHistoricalClassifier(store=store, client=_StubClient(_classification_router))
    queue = [PROBE_DATE + timedelta(days=i) for i in range(5)]

    stats = classifier.run(queue, request_budget=REQUESTS_PER_DATE * 2)
    assert stats["dates_done"] == 2
    assert stats["requests_used"] == REQUESTS_PER_DATE * 2
    assert stats["stopped_early"] is True


# ── Primary OOS gating ─────────────────────────────────────────

def test_tpex_rows_can_never_reach_the_primary_verified_universe(tmp_path: Path) -> None:
    """TPEx instrument_type is BLOCKED, so nothing TPEx is verified."""
    census, store, _ = _census(tmp_path)
    store.write("TPEX", PROBE_DATE, census.fetch_tpex(PROBE_DATE))
    classification = HistoricalClassificationStore(tmp_path / "cls")
    classifier = TwseHistoricalClassifier(store=classification,
                                          client=_StubClient(_classification_router))
    classification.write(PROBE_DATE, classifier.classify_date(PROBE_DATE))

    verified = verified_stock_codes(classification)
    assert verified.is_empty(), "industry membership cannot establish a verified common stock"

    observed_tpex = store.read("TPEX")
    tpex_codes = set(observed_tpex["raw_code"].to_list())
    assert tpex_codes, "TPEx observations are still kept as market fact"
    assert not (tpex_codes & set(verified["code"].to_list()) & {"5371", "4130"})


def test_industry_only_membership_cannot_claim_common_stock(tmp_path: Path) -> None:
    classification = HistoricalClassificationStore(tmp_path / "cls")
    classifier = TwseHistoricalClassifier(store=classification,
                                          client=_StubClient(_classification_router))
    classification.write(PROBE_DATE, classifier.classify_date(PROBE_DATE))

    verified = verified_stock_codes(classification)
    codes = set(verified["code"].to_list())
    assert not ({"2330", "1701"} & codes)
    assert "0050" not in codes, "ETFs are classified but are not the stock universe"


# ── Status ─────────────────────────────────────────────────────

def test_status_reports_exact_remaining_request_count(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=1)

    status = worker.status(start=PROBE_DATE, end=PROBE_DATE)
    assert status["census"]["TWSE"]["processed_dates"] == 1
    assert status["census"]["TWSE"]["observed_trading_sessions"] == 1
    assert status["census"]["TWSE"]["total_sessions"] == 1
    assert status["census"]["TWSE"]["processed_percent"] == 100.0
    assert status["census"]["TWSE"]["trading_coverage_ratio"] == 1.0
    assert status["census"]["TWSE"]["completed_sessions"] == 1
    assert status["census"]["TWSE"]["percent"] == 100.0
    assert status["census"]["TWSE"]["earliest_completed"] == PROBE_DATE.isoformat()
    assert status["census"]["TWSE"]["latest_completed"] == PROBE_DATE.isoformat()
    assert status["classification"]["unique_first_seen_dates"] == 1
    assert status["classification"]["pending_jobs"] == 1
    assert status["classification"]["requests_per_job"] == REQUESTS_PER_DATE
    assert status["classification"]["exact_request_count_remaining"] == REQUESTS_PER_DATE
    # must be JSON-serialisable for a future Data Health panel
    json.dumps(status)


def test_ambiguous_empty_partitions_are_processed_but_unresolved(
    tmp_path: Path,
) -> None:
    """Legacy empty partitions have no evidence of a market closure."""
    census = ObservedUniverseStore(tmp_path / "observed_universe")
    first, second = date(2015, 1, 1), date(2015, 1, 2)
    census.write("TWSE", first, [])
    census.write("TWSE", second, [])

    assert census.completed_dates("TWSE") == {first, second}
    assert census.session_dates("TWSE") == set()
    assert census.confirmed_non_trading_dates("TWSE") == set()

    worker = _worker(tmp_path)
    status = worker.status(start=first, end=second)
    assert status["census"]["TWSE"]["processed_dates"] == 2
    assert status["census"]["TWSE"]["observed_trading_sessions"] == 0
    assert status["census"]["TWSE"]["confirmed_non_trading_dates"] == 0
    assert status["census"]["TWSE"]["unresolved_dates"] == 2
    assert status["census"]["TWSE"]["expected_trading_sessions"] == 2
    assert status["census"]["TWSE"]["processed_ratio"] == 1.0
    assert status["census"]["TWSE"]["trading_coverage_ratio"] == 0.0


def test_verified_calendar_closures_reach_full_trading_coverage(tmp_path: Path) -> None:
    """Two synthetic confirmed closures and one observed session are fully covered."""
    closure_1, closure_2 = date(2024, 6, 4), date(2024, 6, 5)
    worker = _worker(
        tmp_path,
        calendar=TaiwanTradingCalendar(known_holidays={closure_1, closure_2}),
    )
    worker.run_census(PROBE_DATE, closure_2, session_budget=1)

    for exchange in ("TWSE", "TPEX"):
        status = worker.status(start=PROBE_DATE, end=closure_2)["census"][exchange]
        assert status["candidate_dates"] == 3
        assert status["processed_dates"] == 3
        assert status["processed_ratio"] == 1.0
        assert status["processed_percent"] == 100.0
        assert status["observed_trading_sessions"] == 1
        assert status["confirmed_non_trading_dates"] == 2
        assert status["unresolved_dates"] == 0
        assert status["expected_trading_sessions"] == 1
        assert status["trading_coverage_ratio"] == 1.0
        assert worker.census_store.confirmed_non_trading_dates(exchange) == {
            closure_1, closure_2}


def test_unprocessed_weekday_remains_in_trading_denominator(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    worker.run_census(PROBE_DATE, PROBE_DATE, session_budget=1)
    status = worker.status(start=PROBE_DATE, end=PROBE_DATE + timedelta(days=1))
    twse = status["census"]["TWSE"]
    assert twse["candidate_dates"] == 2
    assert twse["processed_ratio"] == 0.5
    assert twse["processed_percent"] == 50.0
    assert twse["expected_trading_sessions"] == 2
    assert twse["trading_coverage_ratio"] == 0.5


def test_unknown_provider_empty_never_confirms_a_closure(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    unknown_day = date(2024, 6, 4)
    worker.run_census(unknown_day, unknown_day, session_budget=1)
    for exchange in ("TWSE", "TPEX"):
        status = worker.status(start=unknown_day, end=unknown_day)["census"][exchange]
        assert status["processed_dates"] == 1
        assert status["unknown_empty_dates"] == 1
        assert status["confirmed_non_trading_dates"] == 0
        assert status["unresolved_dates"] == 1
        assert status["expected_trading_sessions"] == 1
        assert status["trading_coverage_ratio"] == 0.0


# ── Helpers ────────────────────────────────────────────────────

def _worker(
    tmp_path: Path,
    router=_census_router,
    calendar: TaiwanTradingCalendar | None = None,
) -> TaiwanHistoricalBackfillWorker:
    store = ObservedUniverseStore(tmp_path / "observed_universe")
    census = ObservedUniverseCensus(store=store, client=_StubClient(router))
    return TaiwanHistoricalBackfillWorker(
        data_dir=tmp_path,
        census_store=store,
        classification_store=HistoricalClassificationStore(tmp_path / "cls"),
        calendar=calendar,
        census=census,
    )


def test_parse_helpers_tolerate_official_blank_cells() -> None:
    payload = _twse_payload({"1234": "測試"})
    payload["tables"][1]["data"][0][5] = "--"      # 開盤價 blank
    rows = parse_twse_census(payload, PROBE_DATE, "now")
    assert rows[0]["open"] is None
    assert rows[0]["close"] == 22.05

    tpex = _tpex_payload({"5678": "測試櫃"})
    tpex["tables"][0]["data"][0][2] = "---"        # 收盤 blank
    rows = parse_tpex_census(tpex, PROBE_DATE, "now")
    assert rows[0]["close"] is None
    assert rows[0]["raw_source_category"] == "上櫃股票行情"


# ── LongRun (unlimited budget) ─────────────────────────────────

def test_unlimited_session_budget_drains_all_pending(tmp_path: Path) -> None:
    """budget 0 means unlimited, not 'do nothing'."""
    from app.taiwan.backfill_worker import UNLIMITED_BUDGET

    worker = _worker(tmp_path)
    end = PROBE_DATE + timedelta(days=10)
    result = worker.run_census(PROBE_DATE, end, session_budget=UNLIMITED_BUDGET)

    candidates = [d for d in (PROBE_DATE + timedelta(days=i) for i in range(11))
                  if d.weekday() < 5]
    assert worker.census_store.completed_dates("TWSE") == set(candidates)
    assert result["TWSE"]["stopped_early"] is False


def test_unlimited_classification_budget_drains_the_queue(tmp_path: Path) -> None:
    store = HistoricalClassificationStore(tmp_path / "cls")
    classifier = TwseHistoricalClassifier(store=store, client=_StubClient(_classification_router))
    queue = [PROBE_DATE + timedelta(days=i) for i in range(4)]

    stats = classifier.run(queue, request_budget=0)
    assert stats["dates_done"] == 4
    assert stats["stopped_early"] is False
    assert stats["requests_used"] == REQUESTS_PER_DATE * 4


def test_long_run_still_stops_cleanly_on_interrupt(tmp_path: Path) -> None:
    """Unlimited must not mean uninterruptible."""
    from app.taiwan.backfill_worker import UNLIMITED_BUDGET, StopSignal

    worker = _worker(tmp_path)
    stop = StopSignal()
    stop.set()
    result = worker.run_census(PROBE_DATE, PROBE_DATE + timedelta(days=10),
                               session_budget=UNLIMITED_BUDGET, should_stop=stop)
    assert result["TWSE"]["stopped_early"] is True
    assert worker.census_store.completed_dates("TWSE") == set()


def test_long_run_resumes_from_checkpoint(tmp_path: Path) -> None:
    from app.taiwan.backfill_worker import UNLIMITED_BUDGET

    worker = _worker(tmp_path)
    end = PROBE_DATE + timedelta(days=10)
    worker.run_census(PROBE_DATE, end, session_budget=3)
    done_first = len(worker.census_store.completed_dates("TWSE"))
    assert done_first == 3

    second = worker.run_census(PROBE_DATE, end, session_budget=UNLIMITED_BUDGET)
    assert second["TWSE"]["sessions"] + second["TWSE"]["empty"] > 0
    assert len(worker.census_store.completed_dates("TWSE")) > done_first


def test_skip_flags_bypass_each_phase(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    result = worker.run_once(start=PROBE_DATE, end=PROBE_DATE,
                             session_budget=5, skip_classification=True)
    assert result["classification"]["dates_done"] == 0
    assert worker.census_store.has("TWSE", PROBE_DATE)

    other = _worker(tmp_path / "other")
    skipped = other.run_once(start=PROBE_DATE, end=PROBE_DATE,
                             session_budget=5, skip_census=True)
    assert skipped["census"] == {}
