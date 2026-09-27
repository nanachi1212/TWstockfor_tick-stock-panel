"""Fixed-session regression tests for the formal forward selection contract."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.corporate_actions import (
    CorporateActionEvent,
    CorporateActionStore,
    event_market_open,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.events_service import MarketEvent, TaiwanEventService
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.screener import (
    DataDatesInfo,
    ScreenerResultItem,
    TaiwanScreenerRequest,
    TaiwanScreenerResponse,
    TaiwanScreenerService,
)
from app.taiwan.screener_strategy_store import TaiwanScreenerStrategyStore
from app.taiwan.selection_review_models import (
    SaveSelectionSnapshotRequest,
    SelectionSnapshot,
    SelectionSnapshotItem,
)
from app.taiwan.selection_review_service import TaiwanSelectionReviewService


def _clock(day: date):
    return datetime.combine(day, time(15), ZoneInfo("Asia/Taipei"))


def _sessions(start: date, count: int, holiday: date) -> list[date]:
    sessions = []
    cursor = start
    while len(sessions) < count:
        if cursor.weekday() < 5 and cursor != holiday:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return sessions


def _seed(tmp_path: Path, *, missing_stock_5d=False, missing_bm_20d=False,
          missing_session_5d=False, missing_stock_entry=False):
    source = date(2026, 8, 3)
    holiday = date(2026, 8, 7)
    sessions = _sessions(source + timedelta(days=1), 20, holiday)
    store = TaiwanDailyStore(tmp_path / "daily")
    rows = []
    for day in [source, *sessions]:
        if missing_session_5d and day == sessions[4]:
            continue
        for symbol in ("2330.TWSE", "0050.TWSE"):
            if missing_stock_entry and day == sessions[0] and symbol == "2330.TWSE":
                continue
            if missing_stock_5d and day == sessions[4] and symbol == "2330.TWSE":
                continue
            if missing_bm_20d and day == sessions[19] and symbol == "0050.TWSE":
                continue
            opening = 100.0 if symbol == "2330.TWSE" else 50.0
            closing = opening + (0.004 if day == sessions[0] else 1.0)
            rows.append({"symbol": symbol, "date": day, "open": opening,
                         "high": closing, "low": opening, "close": closing,
                         "volume": 1_000_000.0, "amount": opening * 1_000_000.0,
                         "quote_ts": 0})
    store.write_batch(pl.DataFrame(rows))
    actions = CorporateActionStore(tmp_path / "adj_factor")
    actions.save([])
    actions.path.with_name("coverage.json").write_text(json.dumps({
        "start": source.isoformat(), "end": sessions[-1].isoformat(),
        "sources": sorted(SOURCE_URLS), "events_sha256": actions.snapshot_digest(),
    }), encoding="utf-8")
    calendar = TaiwanTradingCalendar(known_holidays={holiday},
                                     known_trading_days=set([source, *sessions]))
    service = TaiwanSelectionReviewService(path=tmp_path / "user_data" / "snapshots.json",
                                           daily_store=store, calendar=calendar,
                                           action_store=actions)
    return service, source, sessions


class _FixedScreener:
    def __init__(self, source: date):
        self.source = source

    def run(self, request: TaiwanScreenerRequest) -> TaiwanScreenerResponse:
        assert request.preset == "trend_liquidity_v1"
        return TaiwanScreenerResponse(
            items=[ScreenerResultItem(symbol="2330.TWSE", name="台積電",
                                      exchange="TWSE", instrument_type="stock",
                                      close=101.0, amount=100_000_000, momentum_5d=0.03,
                                      risk_status="unknown")],
            total=1, page=1, page_size=20, sort_by="trend_liquidity_v1",
            sort_order="desc", data_dates=DataDatesInfo(daily_as_of=self.source.isoformat()),
            risk_unknown_count=1, missing_quote_count=0, quote_coverage_status="verified",
            risk_source_status="partial", risk_source_as_of="2026-08-03T14:00:00+00:00",
            trend_indicator_basis="pit_adjusted", trend_adjustment_status="verified",
            risk_target_date=(self.source + timedelta(days=1)).isoformat(),
        )


def test_lock_is_server_owned_idempotent_and_undeletable(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    screen = _FixedScreener(source)
    first = svc.lock_forward_batch(screen)
    second = svc.lock_forward_batch(screen)
    assert first == second
    assert first.record_type == "forward_batch"
    assert first.target_trade_date == sessions[0].isoformat()
    assert first.primary_observation_count == 1
    assert first.risk_unknown_count == 1
    assert first.missing_quote_count == 0
    assert first.risk_source_status == "partial"
    assert first.risk_source_as_of == "2026-08-03T14:00:00+00:00"
    assert first.risk_target_date == sessions[0].isoformat()
    assert first.selection_action_coverage_start == source.isoformat()
    assert first.selection_action_coverage_end == sessions[-1].isoformat()
    assert first.selection_action_events_sha256 == svc.action_store.snapshot_digest()
    assert first.selection_action_coverage_saved_at is not None
    assert first.items[0].price == 101.0
    assert len(svc.list_snapshots()) == 1
    with pytest.raises(PermissionError):
        svc.delete_snapshot(first.snapshot_id)


def test_formal_lock_rejects_unverified_trend_price_basis(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))

    class UnverifiedScreener(_FixedScreener):
        def run(self, request):
            return super().run(request).model_copy(update={"trend_adjustment_status": "unavailable"})

    with pytest.raises(ValueError, match="公司行動來源覆蓋不足"):
        svc.lock_forward_batch(UnverifiedScreener(source))
    assert svc.list_snapshots() == []

    class PartialScreener(_FixedScreener):
        def run(self, request):
            return super().run(request).model_copy(update={"trend_adjustment_status": "partial"})

    with pytest.raises(ValueError, match="公司行動來源覆蓋不足"):
        svc.lock_forward_batch(PartialScreener(source))
    assert svc.list_snapshots() == []


def test_formal_lock_rejects_unverified_latest_quote_coverage(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))

    class IncompleteScreener(_FixedScreener):
        def run(self, request):
            return super().run(request).model_copy(update={
                "missing_quote_count": 1, "quote_coverage_status": "unavailable",
            })

    with pytest.raises(ValueError, match="行情覆蓋無法驗證"):
        svc.lock_forward_batch(IncompleteScreener(source))
    assert svc.list_snapshots() == []


def test_formal_lock_rejects_action_evidence_changed_during_screen(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))

    class ChangingScreener(_FixedScreener):
        def run(self, request):
            marker = svc.action_store.path.with_name("coverage.json")
            coverage = json.loads(marker.read_text(encoding="utf-8"))
            coverage["start"] = (source - timedelta(days=1)).isoformat()
            marker.write_text(json.dumps(coverage), encoding="utf-8")
            return super().run(request)

    with pytest.raises(ValueError, match="公司行動證據在選股期間已更新"):
        svc.lock_forward_batch(ChangingScreener(source))
    assert svc.list_snapshots() == []


def test_formal_lock_rejects_census_changed_during_screen(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    svc.census_store = ObservedUniverseStore(tmp_path / "census")
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))

    class ChangingScreener(_FixedScreener):
        def run(self, request):
            svc.census_store.write("TWSE", source, [], empty_response_rechecked=True)
            return super().run(request)

    with pytest.raises(ValueError, match="官方觀測資料在選股期間已更新"):
        svc.lock_forward_batch(ChangingScreener(source))
    assert svc.list_snapshots() == []


def test_trend_history_never_bridges_missing_market_or_symbol_session(tmp_path):
    sessions = _sessions(date(2026, 8, 3), 21, date(2026, 8, 7))
    missing_day = sessions[-6]
    store = TaiwanDailyStore(tmp_path / "daily")
    store.write_batch(pl.DataFrame([{
        "symbol": symbol, "date": day, "open": 100.0,
        "high": 100.0, "low": 100.0, "close": 100.0,
        "volume": 1_000_000.0, "amount": 100_000_000.0, "quote_ts": 0,
    } for day in sessions for symbol in ("2330.TWSE", "2454.TWSE")
        if not (day == missing_day and symbol == "2330.TWSE")]))
    actions = CorporateActionStore(tmp_path / "adj_factor")
    actions.save([])
    actions.path.with_name("coverage.json").write_text(json.dumps({
        "start": sessions[0].isoformat(), "end": sessions[-1].isoformat(),
        "sources": sorted(SOURCE_URLS), "events_sha256": actions.snapshot_digest(),
    }), encoding="utf-8")
    calendar = TaiwanTradingCalendar(known_holidays={date(2026, 8, 7)},
                                     known_trading_days=set(sessions))
    screen = TaiwanScreenerService(daily_store=store, action_store=actions, calendar=calendar)
    indicators, status, _ = screen._compute_trend_indicators(
        ["2330.TWSE", "2454.TWSE"], sessions[-1]
    )
    assert status == "verified"
    assert indicators["symbol"].to_list() == ["2454.TWSE"]

    # A complete market session exists in the calendar but its partition is lost.
    gap_store = TaiwanDailyStore(tmp_path / "gap_daily")
    gap_store.write_batch(pl.DataFrame([{
        "symbol": "2454.TWSE", "date": day, "open": 100.0,
        "high": 100.0, "low": 100.0, "close": 100.0,
        "volume": 1_000_000.0, "amount": 100_000_000.0, "quote_ts": 0,
    } for day in sessions if day != missing_day]))
    gap_screen = TaiwanScreenerService(daily_store=gap_store, action_store=actions,
                                       calendar=calendar)
    indicators, status, reason = gap_screen._compute_trend_indicators(
        ["2454.TWSE"], sessions[-1]
    )
    assert indicators is None
    assert (status, reason) == ("unavailable", "trend_history")


def test_quote_coverage_distinguishes_official_absence_from_lost_quote(tmp_path):
    as_of = date(2026, 8, 3)
    census = ObservedUniverseStore(tmp_path / "census")
    screen = TaiwanScreenerService(
        daily_store=TaiwanDailyStore(tmp_path / "daily"), census_store=census,
    )
    universe = pl.DataFrame({"symbol": ["2330.TWSE", "2454.TWSE"]})
    latest = pl.DataFrame({"symbol": ["2330.TWSE"], "date": [as_of]})
    assert screen._quote_coverage_status(universe, latest, as_of) == "unavailable"

    def official(code):
        return dict(date=as_of, raw_code=code, exchange="TWSE", observed=True,
                    raw_name=code, raw_source_category="stock", open=100.0,
                    high=100.0, low=100.0, close=100.0, volume=1_000_000.0,
                    amount=100_000_000.0, instrument_type=None,
                    instrument_type_status="data_insufficient", source="TWSE",
                    retrieved_at=_clock(as_of).isoformat())

    census.write("TWSE", as_of, [official("2330")])
    assert screen._quote_coverage_status(universe, latest, as_of) == "verified"
    census.write("TWSE", as_of, [official("2330"), official("2454")])
    assert screen._quote_coverage_status(universe, latest, as_of) == "unavailable"


def test_trend_history_counts_observed_weekend_session(tmp_path):
    weekdays = _sessions(date(2026, 8, 10), 20, date(2026, 8, 7))
    weekend = weekdays[-1] + timedelta(days=1)
    store = TaiwanDailyStore(tmp_path / "daily")
    store.write_batch(pl.DataFrame([{
        "symbol": "2330.TWSE", "date": day, "open": 100.0,
        "high": 100.0, "low": 100.0, "close": 100.0,
        "volume": 1_000_000.0, "amount": 100_000_000.0, "quote_ts": 0,
    } for day in [*weekdays, weekend]]))
    screen = TaiwanScreenerService(daily_store=store)
    assert screen._recent_verified_sessions(weekend, 20) == [*weekdays[1:], weekend]


def test_corrupt_existing_snapshot_file_is_never_overwritten(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    svc.path.parent.mkdir(parents=True, exist_ok=True)
    svc.path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    with pytest.raises(ValueError, match="無法讀取"):
        svc.lock_forward_batch(_FixedScreener(source))
    assert svc.path.read_text(encoding="utf-8") == "{broken"


def test_first_lock_after_target_open_is_rejected_but_existing_batch_is_idempotent(
    tmp_path, monkeypatch,
):
    svc, source, sessions = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now",
                        lambda: datetime.combine(sessions[0], time(12), ZoneInfo("Asia/Taipei")))
    with pytest.raises(ValueError, match="已開盤"):
        svc.lock_forward_batch(_FixedScreener(source))
    assert svc.list_snapshots() == []
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    original = svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now",
                        lambda: datetime.combine(sessions[0], time(12), ZoneInfo("Asia/Taipei")))
    assert svc.lock_forward_batch(_FixedScreener(source)) == original


def test_lock_rechecks_time_after_waiting_for_write_guard(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path)
    clock = [_clock(source)]
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: clock[0])
    acquired_time = [datetime.combine(sessions[0], time(9), ZoneInfo("Asia/Taipei"))]
    original_guard = svc._write_guard

    @contextmanager
    def delayed_guard():
        with original_guard():
            clock[0] = acquired_time[0]
            yield

    monkeypatch.setattr(svc, "_write_guard", delayed_guard)
    with pytest.raises(ValueError, match="已開盤"):
        svc.lock_forward_batch(_FixedScreener(source))
    assert svc.list_snapshots() == []

    clock[0] = _clock(source)
    acquired_time[0] = datetime.combine(sessions[0], time(8, 59), ZoneInfo("Asia/Taipei"))
    batch = svc.lock_forward_batch(_FixedScreener(source))
    assert batch.locked_at == acquired_time[0].isoformat()


def test_lock_rejects_daily_partition_change_during_screening(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    original_guard = svc._write_guard

    @contextmanager
    def refreshed_guard():
        with original_guard():
            svc.daily_store.write_batch(pl.DataFrame([{
                "symbol": "2330.TWSE", "date": source, "open": 100.0,
                "high": 105.0, "low": 100.0, "close": 105.0,
                "volume": 1_000_000.0, "amount": 105_000_000.0, "quote_ts": 0,
            }]))
            yield

    monkeypatch.setattr(svc, "_write_guard", refreshed_guard)
    with pytest.raises(ValueError, match="行情資料已更新"):
        svc.lock_forward_batch(_FixedScreener(source))
    assert svc.list_snapshots() == []


def test_abandoned_lock_sidecar_does_not_block_new_writes(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    svc.path.parent.mkdir(parents=True, exist_ok=True)
    sidecar = svc.path.with_suffix(".lock")
    sidecar.write_bytes(b"0")
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    batch = svc.lock_forward_batch(_FixedScreener(source))
    assert batch.record_type == "forward_batch"
    assert sidecar.exists()


def test_horizon_does_not_slide_and_benchmark_missing_is_explicit(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path, missing_stock_5d=True, missing_bm_20d=True)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    batch = svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now",
                        lambda: _clock(sessions[-1]))
    detail = svc.get_snapshot_review(batch.snapshot_id)
    assert detail is not None
    item = detail.evaluated_items[0]
    assert item.paper_entry_price == 100.0
    assert item.h1d_status == "completed"
    assert item.h1d_return_pct == 0.0
    assert item.h1d_raw_return_pct > 0
    assert item.h1d_reference_close_status == "completed"
    assert item.h1d_reference_close_return_pct == pytest.approx((100.004 / 101.0 - 1) * 100)
    assert item.h5d_status == "unavailable"
    assert item.h5d_return_pct is None
    assert item.h20d_status == "completed"
    assert item.h20d_bm_status == "unavailable"
    assert item.h20d_excess_pct is None
    assert detail.h5d_unavailable_count == 1
    stats = svc.get_forward_batch_stats()
    assert stats.h1d_hit_rate_pct == 100.0
    assert stats.h1d_avg_return_pct is not None
    assert stats.h1d_reference_close_evaluated_count == 1
    assert stats.h1d_bm_evaluated_count == 1
    assert stats.h5d_unavailable_count == 1


def test_whole_market_missing_session_keeps_fifth_day_fixed(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path, missing_session_5d=True)
    assert sessions[4] == date(2026, 8, 11)  # 8/7 is a confirmed holiday.
    assert sessions[4] not in svc.daily_store.available_dates()
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    batch = svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now",
                        lambda: _clock(sessions[5]))
    review = svc.get_snapshot_review(batch.snapshot_id)
    assert review.evaluated_items[0].h5d_status == "unavailable"
    assert review.evaluated_items[0].h5d_return_pct is None


def test_observed_weekend_session_counts_as_trading_day(tmp_path):
    svc, source, sessions = _seed(tmp_path)
    saturday = date(2026, 8, 8)
    svc.daily_store.write_batch(pl.DataFrame([{
        "symbol": symbol, "date": saturday, "open": 100.0,
        "high": 101.0, "low": 100.0, "close": 101.0,
        "volume": 1_000_000.0, "amount": 101_000_000.0, "quote_ts": 0,
    } for symbol in ("2330.TWSE", "0050.TWSE")]))
    days = svc._get_forward_trading_days(source)
    assert days[:5] == [sessions[0], sessions[1], sessions[2], saturday, sessions[3]]


def test_observed_weekend_target_is_not_marked_closed(tmp_path, monkeypatch):
    svc, _, _ = _seed(tmp_path)
    saturday = date(2026, 8, 8)
    svc.daily_store.write_batch(pl.DataFrame([{
        "symbol": symbol, "date": saturday, "open": 100.0,
        "high": 101.0, "low": 100.0, "close": 101.0,
        "volume": 1_000_000.0, "amount": 101_000_000.0, "quote_ts": 0,
    } for symbol in ("2330.TWSE", "0050.TWSE")]))
    snapshot = SelectionSnapshot(
        snapshot_id="weekend-forward", created_at=_clock(date(2026, 8, 6)).isoformat(),
        strategy_id="trend_liquidity_v1", strategy_name="趨勢流動性 v1",
        as_of_date="2026-08-06", source_data_date="2026-08-06",
        target_trade_date=saturday.isoformat(), market_context_summary="",
        record_type="forward_batch", evaluation_basis="next_open",
        items=[SelectionSnapshotItem(symbol="2330.TWSE", name="台積電", rank=1, price=101.0)],
    )
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(saturday))
    item = svc._get_forward_batch_review(snapshot).evaluated_items[0]
    assert item.entry_status == "completed"
    assert item.h1d_status == "completed"
    assert item.h1d_bm_status == "completed"


def test_pending_and_missing_action_coverage_are_distinct(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    batch = svc.lock_forward_batch(_FixedScreener(source))
    before = svc.get_snapshot_review(batch.snapshot_id)
    assert before.evaluated_items[0].h1d_status == "pending"
    svc.action_store.path.with_name("coverage.json").unlink()
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now",
                        lambda: _clock(sessions[4]))
    after = svc.get_snapshot_review(batch.snapshot_id)
    assert after.evaluated_items[0].h1d_status == "unavailable"
    assert after.evaluated_items[0].h20d_status == "pending"


def test_missing_paper_entry_is_unavailable_while_benchmark_tracks(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path, missing_stock_entry=True)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    batch = svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(sessions[0]))
    review = svc.get_snapshot_review(batch.snapshot_id)
    assert review is not None
    item = review.evaluated_items[0]
    assert item.entry_status == "unavailable"
    assert item.h1d_status == item.h5d_status == item.h20d_status == "unavailable"
    assert item.h1d_bm_status == "completed"
    assert item.h5d_bm_status == item.h20d_bm_status == "pending"
    assert review.h5d_pending_count == 0
    assert review.h5d_unavailable_count == 1


def test_expired_unverified_calendar_is_unavailable_not_pending(tmp_path, monkeypatch):
    store = TaiwanDailyStore(tmp_path / "daily")
    source = date(2026, 8, 3)
    store.write_batch(pl.DataFrame([{
        "symbol": "2330.TWSE", "date": source, "open": 100.0,
        "high": 100.0, "low": 100.0, "close": 100.0,
        "volume": 1_000_000.0, "amount": 100_000_000.0, "quote_ts": 0,
    }]))
    actions = CorporateActionStore(tmp_path / "adj_factor")
    actions.save([])
    actions.path.with_name("coverage.json").write_text(json.dumps({
        "start": source.isoformat(), "end": source.isoformat(),
        "sources": sorted(SOURCE_URLS), "events_sha256": actions.snapshot_digest(),
    }), encoding="utf-8")
    svc = TaiwanSelectionReviewService(path=tmp_path / "snapshots.json",
                                       daily_store=store,
                                       calendar=TaiwanTradingCalendar(), action_store=actions)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    batch = svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now",
                        lambda: _clock(date(2026, 8, 12)))
    review = svc.get_snapshot_review(batch.snapshot_id)
    assert review.evaluated_items[0].entry_status == "unavailable"
    assert review.evaluated_items[0].h1d_status == "unavailable"
    assert review.evaluated_items[0].h5d_status == "unavailable"
    assert review.evaluated_items[0].h20d_status == "unavailable"
    assert review.evaluated_items[0].h20d_bm_status == "pending"


def test_old_research_record_is_not_formal_batch(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    old = svc.save_snapshot(SaveSelectionSnapshotRequest(
        strategy_id="old", strategy_name="研究", as_of_date=source.isoformat(),
        items=[SelectionSnapshotItem(symbol="2330.TWSE", name="台積電", rank=1, price=101)],
    ))
    assert old.record_type == "research"
    assert svc.get_forward_batch_stats().batches_count == 0
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    svc.lock_forward_batch(_FixedScreener(source))
    assert svc.get_forward_batch_stats().batches_count == 1
    assert svc.delete_snapshot(old.snapshot_id)


def test_legacy_analytics_exclude_formal_batch_with_same_strategy(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path)
    svc.save_snapshot(SaveSelectionSnapshotRequest(
        strategy_id="trend_liquidity_v1", strategy_name="研究", as_of_date=source.isoformat(),
        items=[SelectionSnapshotItem(symbol="2330.TWSE", name="台積電", rank=1,
                                     price=101.0, match_reasons=["研究條件"])],
    ))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(sessions[-1]))

    strategies = svc.get_strategy_reviews()
    assert len(strategies) == 1
    assert strategies[0].strategy_id == "trend_liquidity_v1"
    assert strategies[0].snapshots_count == 1
    assert strategies[0].evaluated_picks_5d == 1
    assert [condition.condition_label for condition in svc.get_condition_reviews()] == ["研究條件"]
    assert svc.get_forward_batch_stats().batches_count == 1


def test_completed_forward_review_cache_invalidates_on_daily_change(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(sessions[-1]))
    original_review = svc._get_forward_batch_review
    calls = []

    def counted_review(snapshot):
        calls.append(snapshot.snapshot_id)
        return original_review(snapshot)

    monkeypatch.setattr(svc, "_get_forward_batch_review", counted_review)
    first = svc.get_forward_batch_stats()
    assert first.h20d_evaluated_count == 1
    assert svc.get_forward_batch_stats() == first
    assert len(calls) == 1
    assert len(svc.list_snapshots("forward_batch")) == 1
    assert svc.get_snapshot_review(svc.list_snapshots("forward_batch")[0].snapshot_id)
    assert len(calls) == 1

    unrelated_day = source + timedelta(days=120)
    svc.daily_store.write_batch(pl.DataFrame([{
        "symbol": "2330.TWSE", "date": unrelated_day, "open": 100.0,
        "high": 100.0, "low": 100.0, "close": 100.0,
        "volume": 1_000_000.0, "amount": 100_000_000.0, "quote_ts": 0,
    }]))
    assert svc.get_forward_batch_stats() == first
    assert len(calls) == 1

    day = sessions[-1]
    svc.daily_store.write_batch(pl.DataFrame([{
        "symbol": "2330.TWSE", "date": day, "open": 100.0, "high": 103.0,
        "low": 100.0, "close": 103.0, "volume": 1_000_000.0,
        "amount": 103_000_000.0, "quote_ts": 0,
    }]))
    assert svc.get_forward_batch_stats().h20d_evaluated_count == 1
    assert len(calls) == 2


def test_unavailable_forward_review_cache_recovers_when_benchmark_arrives(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path, missing_bm_20d=True)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    svc.lock_forward_batch(_FixedScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(sessions[-1]))
    original_review = svc._get_forward_batch_review
    calls = []

    def counted_review(snapshot):
        calls.append(snapshot.snapshot_id)
        return original_review(snapshot)

    monkeypatch.setattr(svc, "_get_forward_batch_review", counted_review)
    first = svc.get_forward_batch_stats()
    assert first.h20d_evaluated_count == 1
    assert svc.get_forward_batch_stats() == first
    assert len(calls) == 1

    day = sessions[-1]
    svc.daily_store.write_batch(pl.DataFrame([{
        "symbol": "0050.TWSE", "date": day, "open": 50.0, "high": 51.0,
        "low": 50.0, "close": 51.0, "volume": 1_000_000.0,
        "amount": 51_000_000.0, "quote_ts": 0,
    }]))
    assert svc.get_forward_batch_stats().h20d_evaluated_count == 1
    assert len(calls) == 2
    assert next(iter(svc._completed_forward_reviews.values()))[1].evaluated_items[0].h20d_bm_status == "completed"


def test_empty_formal_batch_review_is_cached(tmp_path, monkeypatch):
    svc, source, sessions = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))

    class EmptyScreener(_FixedScreener):
        def run(self, request):
            return super().run(request).model_copy(update={"items": [], "total": 0})

    batch = svc.lock_forward_batch(EmptyScreener(source))
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(sessions[-1]))
    original_review = svc._get_forward_batch_review
    calls = []

    def counted_review(snapshot):
        calls.append(snapshot.snapshot_id)
        return original_review(snapshot)

    monkeypatch.setattr(svc, "_get_forward_batch_review", counted_review)
    assert svc.get_forward_batch_stats().picks_count == 0
    assert len(svc.list_snapshots("forward_batch")) == 1
    assert svc.get_snapshot_review(batch.snapshot_id).evaluated_items == []
    assert calls == [batch.snapshot_id]


def test_research_hit_rate_uses_unrounded_return(tmp_path):
    svc, source, _ = _seed(tmp_path)
    svc.save_snapshot(SaveSelectionSnapshotRequest(
        strategy_id="tiny_gain", strategy_name="微幅上漲", as_of_date=source.isoformat(),
        items=[SelectionSnapshotItem(symbol="2330.TWSE", name="台積電",
                                     rank=1, price=100.996)],
    ))
    stats = next(s for s in svc.get_strategy_reviews() if s.strategy_id == "tiny_gain")
    assert stats.hit_rate_5d == 100.0


def test_forward_api_contract(tmp_path, monkeypatch):
    svc, source, _ = _seed(tmp_path)
    monkeypatch.setattr("app.taiwan.selection_review_service.get_selection_review_service",
                        lambda: svc)
    monkeypatch.setattr("app.taiwan.selection_review_service.taipei_now", lambda: _clock(source))
    monkeypatch.setattr("app.taiwan.screener.TaiwanScreenerService",
                        lambda **kwargs: _FixedScreener(source))
    client = TestClient(app, client=("127.0.0.1", 50000))
    first = client.post("/api/taiwan/selection-review/forward-batches")
    second = client.post("/api/taiwan/selection-review/forward-batches")
    assert first.status_code == second.status_code == 200
    assert first.json()["snapshot_id"] == second.json()["snapshot_id"]
    assert first.json()["evaluation_basis"] == "next_open"
    assert client.get("/api/taiwan/selection-review/snapshots").json() == []
    assert client.get("/api/taiwan/selection-review/snapshots/" + first.json()["snapshot_id"]).status_code == 404
    formal_list = client.get("/api/taiwan/selection-review/forward-batches").json()
    assert [item["snapshot_id"] for item in formal_list] == [first.json()["snapshot_id"]]
    formal_detail = client.get(
        "/api/taiwan/selection-review/forward-batches/" + first.json()["snapshot_id"]
    )
    assert formal_detail.status_code == 200
    assert formal_detail.json()["snapshot"]["evaluation_basis"] == "next_open"
    assert client.delete("/api/taiwan/selection-review/snapshots/" + first.json()["snapshot_id"]).status_code == 409
    assert client.get("/api/taiwan/selection-review/forward-batches/stats").json()["batches_count"] == 1


def test_trend_sort_uses_amount_then_symbol():
    service = object.__new__(TaiwanScreenerService)
    rows = pl.DataFrame({"symbol": ["B.TWSE", "C.TWSE", "A.TWSE", "D.TWSE"],
                         "momentum_5d": [0.1, 0.2, 0.1, 0.1],
                         "amount": [60_000_000, 60_000_000, 60_000_000, 70_000_000]})
    ordered = service._apply_sort(rows, "trend_liquidity_v1", "desc")
    assert ordered["symbol"].to_list() == ["C.TWSE", "D.TWSE", "A.TWSE", "B.TWSE"]


def test_trend_preset_is_not_exposed_in_old_strategy_dropdown(tmp_path):
    # The existing UI drops conditions.preset and would mislabel unfiltered picks.
    assert TaiwanScreenerStrategyStore(path=tmp_path / "strategies.json").get_strategy(
        "trend_liquidity_v1"
    ) is None
    assert TaiwanScreenerRequest(preset="trend_liquidity_v1").preset == "trend_liquidity_v1"


def test_trend_preset_filters_liquidity_risk_and_etf(tmp_path, monkeypatch):
    store = TaiwanDailyStore(tmp_path / "daily")
    sessions = _sessions(date(2026, 8, 3), 26, date(2026, 8, 7))
    rows = []
    for index, session in enumerate(sessions):
        for symbol, amount in (("2330.TWSE", 50_000_000.0),
                               ("2454.TWSE", 49_999_999.0),
                               ("8069.TPEX", 60_000_000.0),
                               ("0050.TWSE", 80_000_000.0)):
            close = 100.0 + index
            rows.append({"symbol": symbol, "date": session, "open": close,
                         "high": close, "low": close, "close": close,
                         "volume": 1_000_000.0, "amount": amount, "quote_ts": 0})
    store.write_batch(pl.DataFrame(rows))
    actions = CorporateActionStore(tmp_path / "adj_factor")
    actions.save([])
    actions.path.with_name("coverage.json").write_text(json.dumps({
        "start": sessions[0].isoformat(), "end": sessions[-1].isoformat(),
        "sources": sorted(SOURCE_URLS), "events_sha256": actions.snapshot_digest(),
    }), encoding="utf-8")

    class Risk:
        def get_cached_regulatory_snapshot(self):
            return [MarketEvent(
                id="disposition-8069", symbol="8069.TPEX", code="8069", name="元太",
                exchange="TPEX", event_date=target.isoformat(), event_type="disposition",
                event_type_label="處置證券", title="處置", summary="處置", source="TPEX",
                retrieved_at=_clock(sessions[-1]).isoformat(),
            )], "partial", "2026-08-28T14:00:00+00:00"

        def check_symbol_risk_status(self, symbol, target_date=None, events=None):
            assert target_date == target
            return TaiwanEventService.check_symbol_risk_status(
                self, symbol, target_date=target_date, events=events
            )

        def get_events(self, **kwargs):
            raise AssertionError("Screening must not fetch regulatory sources")

    target = TaiwanTradingCalendar().next_potential_session(sessions[-1])
    monkeypatch.setattr("app.taiwan.events_service.get_event_service", lambda: Risk())
    response = TaiwanScreenerService(daily_store=store, action_store=actions).run(
        TaiwanScreenerRequest(preset="trend_liquidity_v1", page_size=200)
    )
    assert [item.symbol for item in response.items] == ["2330.TWSE"]
    assert response.items[0].risk_status == "unknown"
    assert response.items[0].amount == 50_000_000.0
    assert response.risk_unknown_count == 1
    assert response.risk_source_status == "partial"
    assert response.risk_source_as_of == "2026-08-28T14:00:00+00:00"
    assert response.risk_target_date == target.isoformat()
    assert response.trend_indicator_basis == "pit_adjusted"
    assert response.trend_adjustment_status == "verified"

    class DelistingRisk(Risk):
        def get_cached_regulatory_snapshot(self):
            events, status, as_of = super().get_cached_regulatory_snapshot()
            events.append(MarketEvent(
                id="delisting-2330", symbol="2330.TWSE", code="2330", name="台積電",
                exchange="TWSE", event_date=target.isoformat(), event_type="delisting",
                event_type_label="終止上市", title="終止上市", summary="終止上市",
                source="TWSE", retrieved_at=_clock(sessions[-1]).isoformat(),
            ))
            return events, status, as_of

    monkeypatch.setattr("app.taiwan.events_service.get_event_service", lambda: DelistingRisk())
    delisting = TaiwanScreenerService(daily_store=store, action_store=actions).run(
        TaiwanScreenerRequest(preset="trend_liquidity_v1")
    )
    assert delisting.items == []


def test_trend_preset_uses_verified_pit_close_after_cash_dividend(tmp_path, monkeypatch):
    sessions = _sessions(date(2026, 8, 3), 26, date(2026, 8, 7))
    store = TaiwanDailyStore(tmp_path / "daily")
    store.write_batch(pl.DataFrame([{
        "symbol": "2330.TWSE", "date": day, "open": close,
        "high": close, "low": close, "close": close,
        "volume": 1_000_000.0, "amount": 60_000_000.0, "quote_ts": 0,
    } for day in sessions for close in [92.0 if day == sessions[-1] else 100.0]]))
    last = sessions[-1]
    action = CorporateActionEvent(
        symbol="2330.TWSE", exchange="TWSE", effective_date=last,
        effective_at=event_market_open(last), event_type="cash_dividend",
        previous_close=100.0, reference_price=90.0, factor=0.9,
        cash_dividend=10.0, free_share_ratio=None, reduction_ratio=None,
        source="TWT49U", source_url="https://www.twse.com.tw/",
        retrieved_at=_clock(last), status="verified",
        precision_method="official_reference_ratio",
    )
    actions = CorporateActionStore(tmp_path / "adj_factor")
    actions.save([action])
    actions.path.with_name("coverage.json").write_text(json.dumps({
        "start": sessions[0].isoformat(), "end": last.isoformat(),
        "sources": sorted(SOURCE_URLS), "events_sha256": actions.snapshot_digest(),
    }), encoding="utf-8")

    class Risk:
        def get_cached_regulatory_snapshot(self):
            return [], "available", _clock(last).isoformat()

        def check_symbol_risk_status(self, symbol, target_date=None, events=None):
            return {"is_disposition": False, "is_suspended": False}

    monkeypatch.setattr("app.taiwan.events_service.get_event_service", lambda: Risk())
    service = TaiwanScreenerService(daily_store=store, action_store=actions)
    response = service.run(TaiwanScreenerRequest(preset="trend_liquidity_v1"))
    assert response.trend_adjustment_status == "verified"
    assert [item.symbol for item in response.items] == ["2330.TWSE"]
    item = response.items[0]
    assert item.close == 92.0
    assert item.trend_adjusted_close == 92.0
    assert item.ma20 < item.trend_adjusted_close
    assert item.momentum_5d > 0

    actions.path.with_name("coverage.json").unlink()
    unavailable = service.run(TaiwanScreenerRequest(preset="trend_liquidity_v1"))
    assert unavailable.items == []
    assert unavailable.trend_adjustment_status == "unavailable"
    assert "corporate_actions" in unavailable.degraded_sections


def test_regulatory_cache_read_never_fetches_and_stale_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr("app.taiwan.events_service.settings.data_dir", tmp_path)
    service = object.__new__(TaiwanEventService)
    service._memory_cache = {}
    service._cache_ttl = 3600
    service.last_status = "available"
    monkeypatch.setattr(service, "get_events", lambda **kwargs: pytest.fail("Unexpected external fetch"))
    event = MarketEvent(
        id="disposition-8069", symbol="8069.TPEX", code="8069", name="元太",
        exchange="TPEX", event_date="2026-08-28", event_type="disposition",
        event_type_label="處置證券", title="處置", summary="處置", source="TPEX",
        retrieved_at="2026-08-28T15:00:00+08:00",
    )
    cache_file = tmp_path / "taiwan" / "events_cache" / "regulatory_events.json"
    assert service.get_cached_regulatory_snapshot() == ([], "unavailable", None)
    assert not cache_file.exists()
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps({
        "saved_at": datetime.now(UTC).timestamp(), "status": "available",
        "events": [event.model_dump()],
    }), encoding="utf-8")
    events, status, as_of = service.get_cached_regulatory_snapshot()
    assert status == "available"
    assert as_of is not None
    assert service.check_symbol_risk_status("8069.TPEX", date(2026, 8, 28), events)["is_disposition"]
    assert not service.check_symbol_risk_status("2330.TWSE", date(2026, 8, 28), events)["is_disposition"]
    cache_file.write_text(json.dumps({
        "saved_at": datetime.now(UTC).timestamp() - 3601, "status": "available",
        "events": [event.model_dump()],
    }), encoding="utf-8")
    assert service.get_cached_regulatory_snapshot() == ([], "unavailable", None)


def test_paper_return_uses_verified_cash_dividend_price_factor():
    start = date(2026, 8, 4)
    end = date(2026, 8, 5)
    action = CorporateActionEvent(
        symbol="2330.TWSE", exchange="TWSE", effective_date=end,
        effective_at=event_market_open(end), event_type="cash_dividend",
        previous_close=100.0, reference_price=90.0, factor=0.9,
        cash_dividend=10.0, free_share_ratio=None, reduction_ratio=None,
        source="TWT49U", source_url="https://www.twse.com.tw/",
        retrieved_at=_clock(end), status="verified",
        precision_method="official_reference_ratio",
    )
    value = TaiwanSelectionReviewService._paper_return(
        "2330.TWSE", start, end,
        {"open": 100.0, "close": 100.0},
        {"open": 90.0, "close": 90.0}, [action],
    )
    assert value == pytest.approx(0.0)
