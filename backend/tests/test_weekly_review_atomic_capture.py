"""Use the public lock entry point and isolated exchange-format Parquet fixtures."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from threading import Barrier
from types import SimpleNamespace

import polars as pl
import pytest
from test_weekly_selection_review import STRATEGY, prices

from app.taiwan import events_service, selection_review_service, weekly_selection_review
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.selection_review_service import TaiwanSelectionReviewService
from app.taiwan.weekly_review_report import generate_report

SYMBOL = "2330.TWSE"


@pytest.fixture
def close_run(taiwan_data_env, monkeypatch):
    day = taiwan_data_env["target"]
    days = taiwan_data_env["trading_days"]
    clock = [datetime.combine(day, time(17), TAIPEI_TZ)]
    monkeypatch.setattr(selection_review_service, "taipei_now", lambda: clock[0])
    monkeypatch.setattr(weekly_selection_review, "taipei_now", lambda: clock[0])
    daily, inst = TaiwanDailyStore(), TaiwanInstitutionalStore()
    calendar = TaiwanTradingCalendar(known_trading_days=set(days))

    def quote(session, close):
        daily.write_batch(prices(session, [SYMBOL], close).with_columns(
            pl.lit(100_000_000.).alias("amount"), pl.lit(1_000_000.).alias("volume"),
        ))

    for i, session in enumerate(days):
        quote(session, 81 if session == day else 100 - i)
    inst.write_batch(pl.DataFrame([{
        "symbol": SYMBOL, "date": day, "trade_date": day, "status": "official", "source": "twse:t86",
        "foreign_net": 1, "investment_trust_net": 0, "dealer_net": -1,
    }]))
    monkeypatch.setattr(TaiwanSelectionReviewService, "_action_coverage_evidence", lambda self: {
        "start": day.isoformat(), "end": day.isoformat(), "events_sha256": "fixture", "saved_at": clock[0].isoformat(),
    })
    risk = SimpleNamespace(get_cached_regulatory_snapshot=lambda: ([], "available", day.isoformat()),
                           check_symbol_risk_status=lambda *args, **kw: {"is_disposition": False, "is_suspended": False})
    monkeypatch.setattr(events_service, "get_event_service", lambda: risk)
    screen = TaiwanScreenerService(daily_store=daily, institutional_store=inst, calendar=calendar)
    coverage = ["verified"]
    monkeypatch.setattr(screen, "_quote_coverage_status", lambda *args: coverage[0])
    margin_verified = [True]
    monkeypatch.setattr(TaiwanMarginStore, "has_verified_publication", lambda self, target: margin_verified[0])

    def service():
        return TaiwanSelectionReviewService(daily_store=daily, calendar=calendar)

    ledger = service().path.parent / "weekly_selection" / day.strftime("%G-W%V") / f"{STRATEGY}.jsonl"
    return SimpleNamespace(service=service, screen=screen, ledger=ledger, clock=clock, risk=risk,
                           coverage=coverage, margin_verified=margin_verified, days=days, quote=quote)


def test_weekly_lock_does_not_wait_for_margin_publication(close_run):
    # No formal strategy reads margin, and it is published hours after the other inputs.
    run = close_run
    run.margin_verified[0] = False
    assert SYMBOL in run.service().lock_forward_batch(run.screen, STRATEGY).selected_symbols
    assert run.ledger.is_file()


@pytest.mark.parametrize("interrupted", [False, True])
def test_short_week_formal_risk_target_matches_offline_calendar(tmp_path, monkeypatch, interrupted):
    import conftest

    thursday = date(2026, 8, 27)
    friday, monday = thursday + timedelta(days=1), thursday + timedelta(days=4)
    monkeypatch.setattr(conftest, "TAIWAN_FIXTURE_TARGET", thursday)
    data = conftest.taiwan_data_env.__wrapped__(tmp_path, monkeypatch)
    run = close_run.__wrapped__(data, monkeypatch)
    monkeypatch.setattr(weekly_selection_review, "_schedule", lambda year: frozenset({friday}))
    monkeypatch.setattr(TaiwanScreenerService, "_quote_coverage_status", lambda *args: "verified")
    run.clock[0] += timedelta(days=1)  # Past Friday's 09:00; only the official closure can extend the deadline.
    if interrupted:
        def crash(*args):
            raise OSError("fixture: interrupted publish")

        with monkeypatch.context() as patch:
            patch.setattr(weekly_selection_review, "finish_week", crash)
            with pytest.raises(ValueError, match="weekly_capture_io_failure"):
                run.service().lock_forward_batch(strategy_id=STRATEGY)
        assert not run.ledger.exists()
        monkeypatch.setattr(weekly_selection_review, "_schedule", lambda year: pytest.fail("offline recovery"))
        from app.taiwan import selection_v2

        monkeypatch.setattr(selection_v2, "STRATEGY_IDS", [STRATEGY])
        selection_review_service.lock_daily_forward_batches(run.service(), only_missing_for=thursday, refresh_events=False)
        assert run.ledger.is_file(), "the evening/startup job must finish the bound short week"
    batch = run.service().lock_forward_batch(strategy_id=STRATEGY)
    assert batch.risk_target_date == batch.target_trade_date == monday.isoformat()
    records = weekly_selection_review.read_records(run.ledger)
    record = records[0]
    assert record["calendar_evidence"]["closures"] == {
        friday.isoformat(): {"TWSE": "twse:holidaySchedule", "TPEX": "twse:holidaySchedule"},
    }
    assert friday.isoformat() not in json.dumps(record["snapshot"])
    assert weekly_selection_review.recorded_next_session(record, TaiwanTradingCalendar(), None, bound=True) == monday
    assert weekly_selection_review.recorded_next_session(record, TaiwanTradingCalendar(), None, bound=False) == friday
    report = generate_report(tmp_path, friday)
    assert report["weekly_record_count"] == len(records) and not report["rejected_weekly_batches"]
    store = ObservedUniverseStore(tmp_path / "taiwan" / "observed_universe")
    store.write("TPEX", friday, [{"date": friday, "exchange": "TPEX", "raw_code": "8069", "observed": True}])
    disputed = generate_report(tmp_path, friday)
    assert disputed["weekly_record_count"] == 0
    assert disputed["rejected_weekly_batches"][0]["reason"] == "weekly_calendar_evidence_conflict"


@pytest.mark.parametrize("day,source", [("2025-08-28", "twse:holidaySchedule"), ("2026-08-28", "unverified")])
def test_invalid_or_stale_closure_cannot_extend_bound_deadline(day, source):
    record = {"last_trading_day": "2026-08-27", "calendar_evidence": {
        "next_session": "2026-08-31", "closures": {day: {"TWSE": source, "TPEX": source}}}}
    with pytest.raises(ValueError, match="invalid_weekly_calendar_evidence"):
        weekly_selection_review.recorded_next_session(record, TaiwanTradingCalendar(), None, bound=True)


def test_retryable_gate_leaves_room_for_the_real_formal_batch(close_run):
    run = close_run
    run.coverage[0] = "unavailable"
    with pytest.raises(ValueError, match="行情覆蓋"):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    assert not run.ledger.exists() and not run.ledger.with_suffix(".pending").exists()
    run.coverage[0] = "verified"
    formal = run.service().lock_forward_batch(run.screen, STRATEGY)
    rows = weekly_selection_review.read_records(run.ledger)
    assert SYMBOL in formal.selected_symbols
    assert next(r for r in rows if r["symbol"] == SYMBOL)["decision"] == "signal"
    assert any(r["reason"] == "data_incomplete" for r in rows)
    assert all(r["formal_batch_id"] == formal.snapshot_id for r in rows)


def test_missing_whole_institutional_session_is_retryable(close_run, tmp_path, monkeypatch):
    run = close_run
    day = run.days[-1]
    frame = run.screen.institutional_store.read_range([SYMBOL], day, day)
    store = TaiwanInstitutionalStore(tmp_path / "late_institutional")
    monkeypatch.setattr(run.screen, "institutional_store", store)
    with pytest.raises(ValueError, match="weekly_institutional_session_pending"):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    assert not run.ledger.exists() and run.service().read_snapshots() == []
    store.write_batch(frame)
    formal = run.service().lock_forward_batch(run.screen, STRATEGY)
    rows = weekly_selection_review.read_records(run.ledger)
    assert SYMBOL in formal.selected_symbols
    assert next(r for r in rows if r["symbol"] == SYMBOL)["decision"] == "signal"
    assert any(r["reason"] == "data_incomplete" for r in rows)


def test_daily_institutional_pick_with_missing_five_day_input_records_no_signal(close_run):
    run = close_run
    for i, session in enumerate(run.days):
        run.quote(session, 50 + i)
    for i, session in enumerate(run.days[-5:]):
        run.screen.institutional_store.write_batch(pl.DataFrame([{
            "symbol": SYMBOL, "date": session, "trade_date": session, "source": "twse:t86",
            "status": "official", "foreign_net": None if i == 2 else 100_000,
            "investment_trust_net": 50_000, "dealer_net": 0,
        }]))
    strategy = "institutional_momentum_v1"
    formal = run.service().lock_forward_batch(run.screen, strategy)
    assert SYMBOL in formal.selected_symbols
    rows = weekly_selection_review.read_records(run.ledger.with_name(f"{strategy}.jsonl"))
    row = next(r for r in rows if r["symbol"] == SYMBOL)
    assert (row["formal_batch_id"], row["decision"], row["reason"]) == (formal.snapshot_id, "no_signal", "data_incomplete")
    assert row["snapshot"]["daily_inputs"]["foreign_net_5d"] is None
    assert "foreign_net_5d" in row["snapshot"]["missing_fields"]


def test_legacy_daily_batch_without_original_factors_cannot_be_reconstructed(close_run):
    run = close_run
    formal = run.service()._lock_daily_output(run.screen, STRATEGY)
    assert SYMBOL in formal.selected_symbols
    for i, session in enumerate(run.days[:-1]):
        run.quote(session, 50 + i)
    observed = weekly_selection_review.WeeklyCandidates()
    revised = run.screen.run_with_weekly_output(TaiwanScreenerRequest(preset=STRATEGY), observed)
    assert SYMBOL not in [r.symbol for r in revised.items] and observed.facts[SYMBOL]["rsi_14"] > 99
    with pytest.raises(ValueError, match="weekly_original_observations_unavailable"):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    assert not run.ledger.exists()


def test_legacy_self_hashed_ledger_cannot_prove_same_run_factors(close_run):
    run = close_run
    svc = run.service()
    svc.lock_forward_batch(run.screen, STRATEGY)
    stored = json.loads(svc.path.read_text(encoding="utf8"))
    del stored[0]["weekly_records_sha256"]
    svc.path.write_text(json.dumps(stored), encoding="utf8")
    original = run.ledger.read_bytes()
    assert weekly_selection_review.read_records(run.ledger)
    with pytest.raises(ValueError, match="weekly_original_observations_unavailable"):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    assert run.ledger.read_bytes() == original


def test_saturday_risk_preserves_legal_daily_lock_and_permanently_rejects_week(close_run, monkeypatch):
    run = close_run
    run.clock[0] += timedelta(days=1)
    saturday = run.clock[0].date().isoformat()
    monkeypatch.setattr(run.risk, "get_cached_regulatory_snapshot", lambda: ([], "available", saturday))
    with pytest.raises(ValueError, match=r"weekly_capture_rejected.*future_snapshot_date"):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    batch, = run.service().read_snapshots()
    assert SYMBOL in batch.selected_symbols
    assert batch.as_of_date == run.days[-1].isoformat() and batch.risk_source_as_of == saturday
    assert batch.weekly_capture_error == "future_snapshot_date" and batch.weekly_records_sha256 is None
    assert not run.ledger.exists() and not run.ledger.with_suffix(".pending").exists()
    monkeypatch.setattr(run.risk, "get_cached_regulatory_snapshot", lambda: ([], "available", batch.as_of_date))
    monkeypatch.setattr(run.screen, "run_with_weekly_output", lambda *args: pytest.fail("unexpected re-screen"))
    with pytest.raises(ValueError, match=r"weekly_capture_rejected.*future_snapshot_date"):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    assert run.service().read_snapshots() == [batch] and not run.ledger.exists()


def test_preexisting_orphan_ledger_never_gets_associated_with_new_formal_output(close_run):
    run = close_run
    run.ledger.parent.mkdir(parents=True)
    run.ledger.write_bytes(b"existing immutable artifact")
    with pytest.raises(ValueError, match="weekly_unbound_ledger_exists"):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    batch, = run.service().read_snapshots()
    assert SYMBOL in batch.selected_symbols and batch.weekly_records_sha256 is None
    assert run.ledger.read_bytes() == b"existing immutable artifact"


@pytest.mark.parametrize("recovery", ["same_bytes", "corrupt", "expired"])
def test_interrupted_publication_can_only_use_original_bytes_before_open(close_run, monkeypatch, recovery):
    run = close_run

    def offline(*args):
        raise OSError("simulated interrupted publication")

    with monkeypatch.context() as patch:
        patch.setattr(weekly_selection_review, "finish_week", offline)
        with pytest.raises(ValueError, match="weekly_capture_io_failure"):
            run.service().lock_forward_batch(run.screen, STRATEGY)
    batch, = run.service().read_snapshots()
    pending = run.ledger.with_suffix(".pending")
    original = pending.read_bytes()
    assert weekly_selection_review.file_sha256(pending) == batch.weekly_records_sha256
    assert not run.ledger.exists()
    for i, session in enumerate(run.days[:-1]):
        run.quote(session, 50 + i)
    monkeypatch.setattr(run.screen, "run_with_weekly_output", lambda *args: pytest.fail("unexpected re-screen"))
    if recovery == "same_bytes":
        assert run.service().lock_forward_batch(run.screen, STRATEGY) == batch
        assert run.ledger.read_bytes() == original and not pending.exists()
        chosen = next(r for r in weekly_selection_review.read_records(run.ledger) if r["symbol"] == SYMBOL)
        assert chosen["decision"] == "signal" and chosen["snapshot"]["daily_inputs"]["rsi_14"] <= 35
        return
    if recovery == "corrupt":
        pending.write_bytes(original[:-1])
        reason = "weekly_batch_digest_mismatch"
    else:
        run.clock[0] = datetime.combine(run.days[-1] + timedelta(days=3), time(9), TAIPEI_TZ)
        reason = "outside_weekly_capture_window"
    with pytest.raises(ValueError, match=reason):
        run.service().lock_forward_batch(run.screen, STRATEGY)
    assert not run.ledger.exists()


def test_stage_without_committed_batch_is_replaced_by_first_successful_lock(close_run, monkeypatch):
    run = close_run
    svc = run.service()

    def interrupted(*args):
        raise OSError("simulated interrupted daily commit")

    monkeypatch.setattr(svc, "_save_snapshots_raw", interrupted)
    with pytest.raises(ValueError, match="weekly_capture_io_failure"):
        svc.lock_forward_batch(run.screen, STRATEGY)
    assert not svc.path.exists() and not run.ledger.exists()
    assert run.ledger.with_suffix(".pending").is_file()
    for i, session in enumerate(run.days[:-1]):
        run.quote(session, 50 + i)
    batch = run.service().lock_forward_batch(run.screen, STRATEGY)
    assert SYMBOL not in batch.selected_symbols
    chosen = next(r for r in weekly_selection_review.read_records(run.ledger) if r["symbol"] == SYMBOL)
    assert chosen["decision"] == "no_signal" and chosen["snapshot"]["daily_inputs"]["rsi_14"] > 99


def test_concurrent_formal_locks_share_one_committed_week(close_run, monkeypatch):
    run = close_run
    barrier = Barrier(2)
    screen = run.screen.run_with_weekly_output

    def race(req, observed):
        result = screen(req, observed)
        barrier.wait(timeout=15)
        return result

    monkeypatch.setattr(run.screen, "run_with_weekly_output", race)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run.service().lock_forward_batch, run.screen, STRATEGY) for _ in range(2)]
        first, second = (job.result(timeout=30) for job in jobs)
    assert run.service().read_snapshots() == [first] == [second]
    assert weekly_selection_review.file_sha256(run.ledger) == first.weekly_records_sha256
    assert not run.ledger.with_suffix(".pending").exists()
