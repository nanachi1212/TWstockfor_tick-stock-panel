import json
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.market_breadth import router
from app.taiwan.corporate_actions import CorporateActionEvent, event_market_open
from app.taiwan.fundamentals import (
    FundamentalRecord,
    TaiwanFundamentalStore,
    TaiwanOfficialFundamentals,
)
from app.taiwan.market_breadth import accumulate_ad_line, calculate_breadth, metric
from app.taiwan.market_breadth_service import MarketBreadthValuationService
from app.taiwan.market_valuation import calculate_valuation, refresh_valuation
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.taiwan_values import TAIPEI


def sessions(count=280):
    result = []
    cursor = date(2025, 1, 2)
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def bars(days, symbol="OLD.TWSE", direction=1):
    return [{"symbol": symbol, "date": d, "close": 100 + direction * i / 10,
             "high": 101 + direction * i / 10, "low": 99 + direction * i / 10}
            for i, d in enumerate(days)]


def breadth(rows, days, **kwargs):
    options = dict(day=days[-1], market="TWSE", sessions={"TWSE": days}, eligibility={},
                   verified_observed_stocks=[], unresolved_days={}, actions_for_window=lambda a, b: ())
    options.update(kwargs)
    return calculate_breadth(pl.DataFrame(rows), **options)


def record(day, symbol="OLD.TWSE", pe=20.0):
    return FundamentalRecord(symbol, "valuation", None, day.isoformat(), None, None,
                             datetime(2026, 10, 1, tzinfo=TAIPEI), "v1", "TWSE",
                             "official:valuation", "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
                             "data_insufficient", "ratio", values={"pe": pe, "pb": 2.0, "dividend_yield": 0.0})


def test_ma_yearly_high_low_ad_and_ipo_exclusions():
    days = sessions()
    rows = bars(days) + bars(days, "DOWN.TWSE", -1) + bars(days[-3:], "IPO.TWSE")
    got = breadth(rows, days)
    assert got.metrics["ma20"].value == 0.5
    assert got.metrics["ma60"].value == got.metrics["ma240"].value == 0.5
    assert got.metrics["ma240"].included_count == 2
    assert got.metrics["ma240"].excluded_reason_counts == {"insufficient_history": 1}
    assert got.metrics["ma240"].coverage == pytest.approx(2 / 3)
    assert got.metrics["new_high_52w"].value == 1
    assert got.metrics["new_low_52w"].value == 1
    assert got.metrics["new_high_52w"].excluded_count == 1
    assert (got.advances, got.declines, got.unchanged) == (2, 1, 0)
    assert got.historical_eligibility_status == "unverified"
    assert got.universe_label == "依可觀測日 K 計算的研究統計"
    assert not got.universe_complete and not got.strategy_lab_eligible


def test_calendar_gap_missing_bar_and_verified_stock_without_price():
    days = sessions(65)
    rows = bars(days)
    rows = [r for r in rows if r["date"] != days[-5]]
    got = breadth(rows, days, eligibility={"MISSING.TWSE": ("stock", "verified")},
                  verified_observed_stocks=["MISSING.TWSE"])
    assert got.metrics["ma20"].value is None
    assert got.metrics["ma20"].excluded_count == 2
    assert got.metrics["ma20"].excluded_reason_counts["missing_session_price"] == 1
    assert got.metrics["ad_net"].included_count == 1
    gap = breadth(bars(days), days, unresolved_days={"TWSE": {days[-5]}})
    assert gap.metrics["ma20"].excluded_reason_counts == {"calendar_unverified": 1}
    assert gap.metrics["ad_net"].value == 1


def test_no_unverified_corporate_action_coverage_is_not_no_events():
    days = sessions(65)
    got = breadth(bars(days), days, actions_for_window=lambda a, b: None)
    assert got.metrics["ma20"].excluded_reason_counts == {"corporate_action_coverage_unavailable": 1}
    assert got.metrics["ad_net"].value is None
    assert got.advances is None


def test_reuses_verified_normalization_and_excludes_incomparable_actions():
    days = sessions(65)
    rows = bars(days)
    for row in rows:
        row["close"] = 100.0 if row["date"] < days[-1] else 50.0
    event = CorporateActionEvent(
        "OLD.TWSE", "TWSE", days[-1], event_market_open(days[-1]), "capital_reduction",
        100, 50, 0.5, None, None, None, "TWTAUU", "https://www.twse.com.tw",
        datetime(2026, 10, 1, tzinfo=TAIPEI), status="verified")
    got = breadth(rows, days, actions_for_window=lambda a, b: (event,))
    assert got.metrics["ad_net"].value == 0
    assert got.metrics["ma20"].value == 0
    bad = replace(event, factor=None, status="data_insufficient", reason="missing reference")
    got = breadth(rows, days, actions_for_window=lambda a, b: (bad,))
    assert got.metrics["ad_net"].excluded_reason_counts == {"incomparable_corporate_action": 1}
    assert rows[-2]["close"] == 100


def test_future_events_rows_and_current_master_never_reclassify_history():
    days = sessions(65)
    future = bars([days[-1] + timedelta(days=20)], "FUTURE.TWSE")
    with patch("app.taiwan.universe.get_security_master", side_effect=AssertionError("current master forbidden")):
        got = breadth(bars(days) + future, days,
                      eligibility={"OLD.TWSE": ("stock", "verified")})
    assert got.historical_eligibility_status == "verified"
    assert got.metrics["ma20"].included_count == 1
    assert got.universe_complete is False
    excluded = breadth(bars(days), days, eligibility={"OLD.TWSE": ("etf", "verified")})
    assert excluded.metrics["ma20"].excluded_reason_counts == {"not_ordinary_stock": 1}


def test_bad_identified_action_excludes_only_affected_symbol():
    days = sessions(65)
    event = CorporateActionEvent(
        "OLD.TWSE", "TWSE", days[-1], event_market_open(days[-1]), "capital_reduction",
        None, None, None, None, None, None, "TWTAUU", "https://www.twse.com.tw",
        datetime(2026, 10, 1, tzinfo=TAIPEI), status="provider_error")
    got = breadth(bars(days) + bars(days, "OTHER.TWSE"), days, actions_for_window=lambda a, b: (event,))
    assert got.metrics["ma60"].included_count == 1
    assert got.metrics["ma60"].excluded_reason_counts == {"incomparable_corporate_action": 1}


def test_request_windows_reuse_only_past_verified_prices_and_recompute_new_events():
    from app.taiwan.adjust import adjust_prices_as_of
    from app.taiwan.market_breadth import BreadthWindowCache

    days = sessions(300)
    rows = bars(days)
    events = tuple(CorporateActionEvent(
        "OLD.TWSE", "TWSE", d, event_market_open(d), "capital_reduction",
        100, 50, 0.5, None, None, None, "TWTAUU", "https://www.twse.com.tw",
        datetime(2026, 10, 1, tzinfo=TAIPEI), status="verified") for d in (days[100], days[-5]))
    cache = BreadthWindowCache()
    options = dict(actions_for_window=lambda a, b: tuple(e for e in events if a <= e.effective_date <= b))
    expected = [breadth(rows, days, day=d, **options).model_dump() for d in days[-20:]]
    with patch("app.taiwan.market_breadth.adjust_prices_as_of", wraps=adjust_prices_as_of) as normalize:
        actual = [breadth(rows, days, day=d, window_cache=cache, **options).model_dump() for d in days[-20:]]
    assert actual == expected
    assert normalize.call_count == 2  # first anchor, then the newly effective event
    # A caller going backwards must not reuse a later anchor's adjusted prices.
    assert breadth(rows, days, day=days[-10], window_cache=cache, **options).model_dump() == expected[10]


@pytest.mark.parametrize("fault", ["missing", "invalid_high", "invalid_close", "bad_event"])
def test_request_window_cache_does_not_hide_later_gaps_or_bad_prices(fault):
    from app.taiwan.market_breadth import BreadthWindowCache

    days = sessions(285)
    rows = bars(days)
    event = CorporateActionEvent(
        "OLD.TWSE", "TWSE", days[100], event_market_open(days[100]), "capital_reduction",
        100, 50, 0.5, None, None, None, "TWTAUU", "https://www.twse.com.tw",
        datetime(2026, 10, 1, tzinfo=TAIPEI), status="verified")
    events = [event]
    if fault == "missing":
        rows.pop(-3)
    elif fault.startswith("invalid"):
        rows[-3]["high" if fault == "invalid_high" else "close"] = float("nan")
    else:
        events.append(replace(event, effective_date=days[-3], effective_at=event_market_open(days[-3]),
                              status="provider_error", factor=None))
    options = dict(actions_for_window=lambda a, b: tuple(e for e in events if a <= e.effective_date <= b))
    cache = BreadthWindowCache()
    for day in days[-6:]:
        assert breadth(rows, days, day=day, window_cache=cache, **options).model_dump() == breadth(
            rows, days, day=day, **options).model_dump()


def test_event_serialization_preserves_existing_digest_contract():
    import hashlib
    from dataclasses import asdict

    day = date(2026, 9, 29)
    event = CorporateActionEvent(
        "OLD.TWSE", "TWSE", day, event_market_open(day), "capital_reduction",
        100, 50, 0.5, None, None, None, "TWTAUU", "https://www.twse.com.tw",
        datetime(2026, 10, 1, tzinfo=TAIPEI), status="verified")
    original = asdict(event)
    assert event.to_dict() == original
    for key in ("retrieved_at", "revision_status", "source_url"):
        original.pop(key)
    assert event.content_hash == hashlib.sha256(json.dumps(original, sort_keys=True, default=str).encode()).hexdigest()


def test_bounded_census_sessions_do_not_open_unrelated_history(tmp_path):
    store = ObservedUniverseStore(tmp_path)
    bad = store.partition_path("TWSE", date(2020, 1, 1))
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"invalid parquet")
    assert store.session_dates("TWSE", date(2026, 1, 1), date(2026, 1, 2)) == set()


def test_verified_action_snapshot_checks_digest_bounds_and_generation(tmp_path, monkeypatch):
    from app.taiwan.corporate_actions import CorporateActionStore
    from app.taiwan.providers.corporate_actions import SOURCE_URLS

    days = sessions(65)
    event = CorporateActionEvent(
        "OLD.TWSE", "TWSE", days[-1], event_market_open(days[-1]), "capital_reduction",
        None, None, None, None, None, None, "TWTAUU", "https://www.twse.com.tw",
        datetime(2026, 10, 1, tzinfo=TAIPEI), status="provider_error")
    store = CorporateActionStore(tmp_path)
    store.save([event])
    marker = store.path.with_name("coverage.json")
    marker.write_text(json.dumps({"sources": list(SOURCE_URLS), "start": str(days[0]), "end": str(days[-1]),
                                  "events_sha256": store.snapshot_digest()}), encoding="utf8")
    assert store.read_verified_coverage()[2][0].status == "provider_error"
    assert store.read_verified_window(days[0], days[-1]) is None
    assert store.read_verified_window(days[0] - timedelta(days=1), days[-2]) is None
    assert store.read_verified_window(days[0], days[-2]) == ()
    original_read = store.read
    def concurrent_read():
        events = original_read()
        marker.write_text("{}", encoding="utf8")
        return events
    monkeypatch.setattr(store, "read", concurrent_read)
    # Invalidate the successful process snapshot before exercising a racing read.
    marker.write_text(marker.read_text(encoding="utf8") + " ", encoding="utf8")
    assert store.read_verified_coverage() is None


def test_flat_empty_invalid_and_ad_gap_reset():
    days = sessions(25)
    rows = bars(days)
    for row in rows:
        row["close"] = 100.0
    flat = breadth(rows, days)
    assert flat.metrics["ma20"].value == 0
    assert flat.metrics["ad_net"].value == 0 and flat.unchanged == 1
    empty = metric([], Counter(), unit="stocks")
    assert empty.coverage is None and empty.value is None
    rows[-1]["close"] = float("nan")
    assert breadth(rows, days).metrics["ad_net"].excluded_reason_counts == {"invalid_price": 1}
    a = breadth(bars(days), days)
    b = breadth(bars(days), days, actions_for_window=lambda a, b: None)
    c = a.model_copy(deep=True)
    c.as_of = days[-1] + timedelta(days=1)
    accumulate_ad_line([a, b, c])
    assert a.metrics["ad_line"].value == 1
    assert b.metrics["ad_line"].value is None
    assert c.metrics["ad_line"].value == 1 and c.ad_segment_start == c.as_of


def test_valuation_positive_only_pooled_composite_and_no_date_substitution():
    day = date(2026, 9, 30)
    rows = [record(day, pe=10), record(day, "OTHER.TWSE", 30), record(day, "LOSS.TWSE", -5),
            record(day, "MISSING.TWSE", None), record(day, "OTC.TPEX", 50)]
    got = calculate_valuation(rows, day=day, market="composite", expected_symbols={"ABSENT.TWSE"})
    assert got.metrics["pe"].value == 30
    assert got.metrics["pe"].included_count == 3 and got.metrics["pe"].excluded_count == 3
    assert got.metrics["pe"].coverage == 0.5
    assert got.metrics["dividend_yield"].value == 0
    assert got.available_at is None and got.usage_scope == "descriptive_history"
    wrong_date = replace(rows[-1], period_end="2026-09-29")
    got = calculate_valuation([*rows[:-1], wrong_date], day=day, market="composite")
    assert got.status == "unavailable" and got.metrics["pe"].value is None
    assert calculate_valuation(rows, day=day, market="TWSE").metrics["pe"].value == 20
    assert calculate_valuation(rows, day=day, market="TPEX").metrics["pe"].value == 50


def test_percentile_uses_prior_sessions_midrank_revisions_and_excludes_future():
    days = sessions(22)
    rows = [record(d, pe=20) for d in days]
    rows.append(record(days[-1] + timedelta(days=1), pe=1000))
    got = calculate_valuation(rows, day=days[-1], market="TWSE")
    assert got.metrics["pe"].percentile == 0.5
    assert got.metrics["pe"].percentile_sample_count == 21
    assert got.metrics["pe"].percentile_end == days[-2]
    short = calculate_valuation(rows[:4], day=days[3], market="TWSE")
    assert short.metrics["pe"].percentile is None
    newer = replace(rows[0], retrieved_at=datetime(2026, 10, 2, tzinfo=TAIPEI), values={"pe": 40})
    revised = calculate_valuation([*rows, newer], day=days[-1], market="TWSE")
    assert revised.metrics["pe"].percentile_sample_count == 21
    assert revised.metrics["pe"].percentile < 0.5


def test_refresh_two_requests_persistence_provenance_and_isolated_failure(tmp_path):
    requests = []
    def respond(request):
        requests.append(str(request.url))
        if "tpex" in request.url.host:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"Code": "2330", "Date": "1150930", "PEratio": "20", "PBratio": "2"}])
    provider = TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(respond)))
    store = TaiwanFundamentalStore(tmp_path)
    store.save([record(date(2026, 9, 29), "OTC.TPEX")])
    result = refresh_valuation(store, provider)
    assert len(requests) == 2 and result["status"] == "partial"
    assert len(store.load()) == 2
    saved = next(r for r in store.load() if r.symbol == "2330.TWSE")
    assert saved.available_at is None and saved.period_end == "2026-09-30"
    assert saved.retrieved_at.tzinfo and saved.source_url.endswith("BWIBBU_ALL")


def test_changed_official_snapshot_preserves_descriptive_revisions(tmp_path):
    row = {"Code": "2330", "Date": "1150930", "PEratio": "20"}
    provider = TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=[row] if "twse" in r.url.host else []))))
    store = TaiwanFundamentalStore(tmp_path)
    refresh_valuation(store, provider)
    row["PEratio"] = "21"
    refresh_valuation(store, provider)
    assert len(store.load()) == 2
    assert {r.values["pe"] for r in store.load()} == {20, 21}
    assert all(r.available_at is None for r in store.load())


@pytest.mark.parametrize("payload", [[], [{"Date": "1150930"}], [{"Code": "2330", "Date": "invalid"}]])
def test_bad_official_snapshot_does_not_erase_saved_data(tmp_path, payload):
    store = TaiwanFundamentalStore(tmp_path)
    store.save([record(date(2026, 9, 29))])
    provider = TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=payload))))
    assert refresh_valuation(store, provider)["status"] == "unavailable"
    assert len(store.load()) == 1


def test_observed_range_ignores_unrelated_corrupt_partition(tmp_path):
    store = ObservedUniverseStore(tmp_path)
    bad = store.partition_path("TWSE", date(2020, 1, 1))
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"invalid parquet")
    assert store.read_range("TWSE", date(2026, 1, 1), date(2026, 1, 2)).is_empty()


def test_local_empty_service_and_api_validation_have_no_http(tmp_path):
    from app.taiwan.benchmark_store import TaiwanBenchmarkStore
    from app.taiwan.corporate_actions import CorporateActionStore
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.historical_classification import HistoricalClassificationStore
    from app.taiwan.pit_universe import PitUniverse

    service = MarketBreadthValuationService(
        daily=TaiwanDailyStore(tmp_path / "daily"),
        universe=PitUniverse(ObservedUniverseStore(tmp_path / "census"),
                             HistoricalClassificationStore(tmp_path / "classification")),
        benchmarks=TaiwanBenchmarkStore(tmp_path / "benchmark.parquet"),
        actions=CorporateActionStore(tmp_path / "actions"), fundamentals=TaiwanFundamentalStore(tmp_path))
    app = FastAPI()
    app.include_router(router, prefix="/api/taiwan")
    client = TestClient(app)
    with patch("app.api.market_breadth.MarketBreadthValuationService", return_value=service), patch.object(
            httpx.Client, "get", side_effect=AssertionError("GET must not fetch network")):
        # The test client's get is inherited from httpx, so use request directly.
        response = client.request("GET", "/api/taiwan/market-research/breadth-valuation?as_of=2026-09-30")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable" and response.json()["latest"] is None
    assert client.get("/api/taiwan/market-research/breadth-valuation?market=invalid").status_code == 422
    assert client.get("/api/taiwan/market-research/breadth-valuation?days=61").status_code == 422
    assert client.get("/api/taiwan/market-research/breadth-valuation?sections=invalid").status_code == 422
    assert client.get("/api/taiwan/market-research/breadth-valuation?as_of=bad").status_code == 422
    with patch("app.api.market_breadth.MarketBreadthValuationService", side_effect=OSError("private detail")):
        response = client.get("/api/taiwan/market-research/breadth-valuation")
        assert response.status_code == 503 and "private detail" not in response.text


def test_service_tracks_stale_target_and_does_not_use_current_master():
    days = sessions(65)
    daily, universe, benchmarks, actions, fundamentals = [MagicMock() for _ in range(5)]
    daily.read_range.return_value = pl.DataFrame(bars(days))
    universe.sessions.return_value = days
    universe.as_of.return_value = pl.DataFrame(schema={"market_symbol": pl.String, "instrument_type": pl.String,
                                                       "instrument_type_status": pl.String})
    universe.census.read_range.return_value = pl.DataFrame(schema={"date": pl.Date, "retrieved_at": pl.String})
    universe.census.day_evidence.return_value = MagicMock(status="non_trading")
    benchmarks.read.return_value = pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date})
    actions.read_verified_coverage.return_value = (days[0], days[-1], ())
    fundamentals.load.return_value = [record(days[-1])]
    service = MarketBreadthValuationService(daily=daily, universe=universe, benchmarks=benchmarks,
                                           actions=actions, fundamentals=fundamentals)
    with patch("app.taiwan.universe.get_security_master", side_effect=AssertionError):
        got = service.snapshot(days[-1] + timedelta(days=1), "TWSE", 3)
    assert got.as_of == days[-1] and got.stale and got.status == "partial"
    assert got.latest.metrics["ma20"].value == 1
    assert len(got.history) == 3 and got.latest.metrics["ad_line"].value == 3
    universe.census.read_range.assert_called_once_with(None, days[-3], days[-1])
    universe.classification.read.assert_called_once()
    actions.read_verified_coverage.return_value = None
    fundamentals.load.return_value = []
    unavailable = service.snapshot(days[-1] + timedelta(days=1), "TWSE", 3)
    assert unavailable.stale and unavailable.status == "unavailable"
    assert all(m.value is None for m in unavailable.latest.metrics.values())


def test_valuation_section_never_reads_breadth_inputs_and_preserves_unavailable():
    daily, universe, benchmarks, actions, fundamentals = [MagicMock() for _ in range(5)]
    day = date(2026, 9, 29)
    daily.read_range.return_value = pl.DataFrame(bars([day]))
    fundamentals.load.return_value = [record(day)]
    service = MarketBreadthValuationService(daily=daily, universe=universe, benchmarks=benchmarks,
                                           actions=actions, fundamentals=fundamentals)
    with patch("app.taiwan.market_breadth_service.calculate_breadth", side_effect=AssertionError("breadth forbidden")):
        got = service.snapshot(day, "TWSE", sections="valuation")
        assert got.valuation.metrics["pe"].value == 20
        assert got.status == "available" and got.sections == "valuation"
        assert not got.history and got.latest is None
        # Historical valuation must not silently substitute a saved earlier day.
        missing = service.snapshot(day + timedelta(days=1), "TWSE", sections="valuation")
        assert missing.as_of == day + timedelta(days=1)
        assert missing.status == "unavailable" and missing.valuation.metrics["pe"].value is None
    universe.sessions.assert_not_called()
    universe.as_of.assert_not_called()
    universe.census.read_range.assert_not_called()
    universe.classification.read.assert_not_called()
    benchmarks.read.assert_not_called()
    actions.read_verified_coverage.assert_not_called()
    assert all(call.args[1] == call.args[2] for call in daily.read_range.call_args_list)


def test_verified_coverage_process_reuse_invalidation_and_deletion(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from app.taiwan.corporate_actions import CorporateActionStore
    from app.taiwan.providers.corporate_actions import SOURCE_URLS

    store = CorporateActionStore(tmp_path)
    store.save([])
    marker = store.path.with_name("coverage.json")
    payload = {"sources": list(SOURCE_URLS), "start": "2025-01-01", "end": "2026-09-29",
               "events_sha256": store.snapshot_digest()}
    marker.write_text(json.dumps(payload), encoding="utf8")
    expected = store.read_verified_coverage()
    with (patch.object(CorporateActionStore, "read", side_effect=AssertionError("same generation must reuse")),
          ThreadPoolExecutor(max_workers=4) as pool):
        assert list(pool.map(lambda _: CorporateActionStore(tmp_path).read_verified_coverage(), range(8))) == [expected] * 8
    payload["end"] = "2026-09-30"
    marker.write_text(json.dumps(payload), encoding="utf8")
    assert store.read_verified_coverage()[1] == date(2026, 9, 30)
    # Changed observations with an old digest cannot use the previous snapshot.
    frame = pl.read_parquet(store.path)
    frame.write_parquet(store.path, compression="uncompressed")
    with patch.object(store, "snapshot_digest", return_value="different"):
        assert store.read_verified_coverage() is None
    marker.unlink()
    assert store.read_verified_coverage() is None
