"""A5 evidence contracts; every fixture is offline and date scoped."""
from datetime import date, datetime

import polars as pl
import pytest

from app.taiwan.historical_classification import (
    HistoricalClassificationStore,
    classification_counts,
)
from app.taiwan.observed_universe import ObservedUniverseStore, census_coverage
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, TradingDayEvidence

D = date(2015, 1, 5)
NOW = datetime(2026, 9, 23, tzinfo=TAIPEI)


@pytest.mark.parametrize("reason", ["pending", "unexplained_empty", "provider_error", "schema_mismatch"])
def test_unresolved_evidence_never_certifies_trading(reason):
    evidence = TradingDayEvidence(D, "TWSE", "unresolved", "twse:MI_INDEX", reason, NOW)
    assert evidence.possible_trading_session
    assert evidence.describe()["status"] == "unresolved"


def test_weekend_and_known_holiday_have_distinct_provenance():
    cal = TaiwanTradingCalendar(known_holidays={D})
    assert cal.day_evidence(date(2015, 1, 4), "TWSE").evidence_source == "calendar_rule"
    assert cal.day_evidence(D, "TWSE").status == "non_trading"
    assert cal.day_evidence(date(2015, 1, 6), "TWSE").reason == "pending"


def test_two_closures_one_snapshot_have_different_denominators(tmp_path):
    store = ObservedUniverseStore(tmp_path)
    days = {date(2015, 1, 1), date(2015, 1, 2), D}
    for day in sorted(days - {D}):
        store.write("TWSE", day, [], confirmed_non_trading_source="official:holiday_archive")
    store.write("TWSE", D, [{"date": D, "raw_code": "2330", "exchange": "TWSE",
                             "observed": True, "source": "twse:MI_INDEX:ALLBUT0999"}])
    assert store.day_evidence("TWSE", D).status == "trading"
    stats = census_coverage(store, "TWSE", days)
    assert stats.processed_dates == 3
    assert stats.processed_ratio == 1
    assert stats.expected_trading_sessions == 1
    assert stats.trading_coverage_ratio == 1
    # Unprocessed weekday must remain in the possible-session denominator.
    later = census_coverage(store, "TWSE", days | {date(2015, 1, 6)})
    assert later.expected_trading_sessions == 2
    assert later.processed_ratio == 3 / 4
    assert later.trading_coverage_ratio == 1 / 2


def test_legacy_empty_partition_is_unresolved(tmp_path):
    store = ObservedUniverseStore(tmp_path)
    path = store.partition_path("TWSE", D)
    path.parent.mkdir(parents=True)
    pl.DataFrame(schema={"raw_code": pl.String}).write_parquet(path)
    assert store.day_evidence("TWSE", D).reason == "unexplained_empty"
    assert census_coverage(store, "TWSE", {D}).expected_trading_sessions == 1


def test_classification_denominator_excludes_verified_non_stock(tmp_path):
    store = HistoricalClassificationStore(tmp_path)
    store.write(D, [
        {"code": code, "exchange": "TWSE", "instrument_type": kind,
         "classification_effective_from": D, "classification_status": "verified",
         "classification_source": "twse:MI_INDEX:historical"}
        for code, kind in [("stock-with-letters", "stock"), ("1234", "etf"),
                           ("5678", "tdr"), ("9", "beneficiary_security")]
    ])
    counts = classification_counts(
        {c: D for c in ["stock-with-letters", "1234", "5678", "9", "0000"]}, store.read())
    assert counts["verified_stock_count"] == 1
    assert counts["verified_etf_count"] == 1
    assert counts["verified_unsupported_count"] == 2
    assert counts["unknown_count"] == 1
    assert counts["unknown_ratio"] == 1 / 5
    assert counts["primary_classification_denominator"] == 2
    assert counts["primary_classification_ratio"] == 1 / 2


def test_future_classification_does_not_resolve_first_observation(tmp_path):
    store = HistoricalClassificationStore(tmp_path)
    later = date(2026, 1, 1)
    store.write(later, [{"code": "2330", "exchange": "TWSE", "instrument_type": "stock",
                        "classification_effective_from": later, "classification_status": "verified"}])
    assert classification_counts({"2330": D}, store.read())["unknown_count"] == 1


def test_calendar_conflict_is_unresolved_in_both_interfaces():
    cal = TaiwanTradingCalendar(known_holidays={D}, known_trading_days={D})
    assert cal.day_evidence(D, "TWSE").status == "unresolved"
    assert cal.is_trading_day(D) is None


def test_explicit_weekend_trading_evidence_overrides_calendar_rule():
    saturday = date(2015, 1, 3)
    cal = TaiwanTradingCalendar(known_trading_days={saturday})
    assert cal.day_evidence(saturday, "TWSE").status == "trading"
    assert cal.is_trading_day(saturday) is True


def test_provider_error_does_not_become_a_completed_empty_partition(tmp_path):
    import json

    from app.taiwan.backfill_worker import TaiwanHistoricalBackfillWorker
    from app.taiwan.observed_universe import ObservedUniverseCensus

    class Response:
        content = json.dumps({"stat": "temporary upstream error", "tables": []}).encode()

        def raise_for_status(self):
            return None

    class Client:
        def get(self, url):
            return Response()

    store = ObservedUniverseStore(tmp_path / "census")
    census = ObservedUniverseCensus(store=store, client=Client())
    worker = TaiwanHistoricalBackfillWorker(data_dir=tmp_path, census=census, census_store=store)
    worker.run_census(D, D, session_budget=1)
    for exchange in ("TWSE", "TPEX"):
        assert not store.has(exchange, D)
        info = worker.status(start=D, end=D)["census"][exchange]
        assert info["processed_dates"] == 0
        assert info["expected_trading_sessions"] == 1
        assert info["failed_date_evidence"][0]["reason"] == "provider_error"


def test_new_unresolved_classification_cannot_resurrect_old_verified_type(tmp_path):
    from app.taiwan.pit_universe import PitUniverse

    store = HistoricalClassificationStore(tmp_path / "classification")
    census = ObservedUniverseStore(tmp_path / "census")
    later = date(2015, 1, 6)
    base = {"code": "test", "exchange": "TWSE", "instrument_type": "stock",
            "classification_effective_from": D, "classification_status": "verified"}
    store.write(D, [base])
    store.write(later, [base | {"classification_effective_from": later,
                               "instrument_type": None, "classification_status": "data_insufficient"}])
    census.write("TWSE", later, [{"date": later, "raw_code": "test", "exchange": "TWSE", "observed": True}])
    result = PitUniverse(census, store).as_of(later).row(0, named=True)
    assert result["instrument_type_status"] == "data_insufficient"
    assert result["instrument_type"] is None


def test_conflicting_classification_rows_are_unknown_regardless_of_order(tmp_path):
    store = HistoricalClassificationStore(tmp_path)
    base = {"code": "test", "exchange": "TWSE", "classification_effective_from": D,
            "classification_status": "verified"}
    for types in (("stock", "etf"), ("etf", "stock")):
        store.write(D, [base | {"instrument_type": kind} for kind in types])
        counts = classification_counts({"test": D}, store.read())
        assert counts["unknown_count"] == 1


def test_legacy_industry_claim_is_downgraded_without_rewriting_file(tmp_path):
    store = HistoricalClassificationStore(tmp_path)
    path = store.partition_path(D)
    path.parent.mkdir(parents=True)
    pl.DataFrame({"code": ["any-shape"], "exchange": ["TWSE"], "instrument_type": ["stock"],
                  "classification_effective_from": [D], "classification_status": ["verified"],
                  "classification_source": ["twse:MI_INDEX:industry_tables@2015-01-05"]}).write_parquet(path)
    before = path.read_bytes()
    assert store.read()["instrument_type"].to_list() == [None]
    assert store.needs_upgrade(D)
    assert path.read_bytes() == before


@pytest.mark.parametrize("reason", ["provider_error", "schema_mismatch"])
def test_failed_attempt_has_evidence_without_a_completion_partition(tmp_path, reason):
    store = ObservedUniverseStore(tmp_path)
    e = store.day_evidence("TWSE", D, failure={"reason": reason, "last_attempt": NOW.isoformat()})
    assert e.status == "unresolved"
    assert e.reason == reason
    assert e.retrieved_at == NOW
    assert not store.has("TWSE", D)
    assert census_coverage(store, "TWSE", {D}).processed_dates == 0


def test_schema_mismatch_is_not_an_empty_holiday():
    from app.taiwan.observed_universe import CensusSchemaError, parse_tpex_census, parse_twse_census
    with pytest.raises(CensusSchemaError):
        parse_twse_census({"stat": "OK", "tables": []}, D, NOW.isoformat())
    with pytest.raises(CensusSchemaError):
        parse_tpex_census({"tables": [{"fields": ["wrong"], "data": [["x"]]}]}, D, NOW.isoformat())


def test_http_success_with_provider_error_does_not_become_empty_success():
    from app.taiwan.observed_universe import CensusProviderError, parse_twse_census
    with pytest.raises(CensusProviderError):
        parse_twse_census({"stat": "provider down"}, D, NOW.isoformat())


def test_old_industry_classification_is_readable_but_not_common_stock_evidence(tmp_path):
    from app.taiwan.historical_classification import classification_queue
    store = HistoricalClassificationStore(tmp_path / "classification")
    path = store.partition_path(D)
    path.parent.mkdir(parents=True)
    pl.DataFrame([{"code": "2330", "exchange": "TWSE", "instrument_type": "stock",
                   "classification_effective_from": D, "classification_status": "verified",
                   "classification_source": f"twse:MI_INDEX:industry_tables@{D}"}]).write_parquet(path)
    before = path.read_bytes()
    frame = store.read()
    assert frame["instrument_type"].to_list() == [None]
    assert frame["instrument_type_status"].to_list() == ["data_insufficient"]
    assert frame["primary_oos_eligible_type"].to_list() == [False]
    assert path.read_bytes() == before
    census = ObservedUniverseStore(tmp_path / "census")
    census.write("TWSE", D, [{"date": D, "raw_code": "2330", "exchange": "TWSE"}])
    assert classification_queue(census, store) == [D]
    assert classification_counts({"2330": D}, frame)["industry_only_unresolved_count"] == 1


def test_unsupported_historical_table_is_parsed_by_field_not_position(tmp_path, monkeypatch):
    import json

    from app.taiwan.historical_classification import TwseHistoricalClassifier
    def current_master_forbidden(*args, **kwargs):
        raise AssertionError("current master must never be consulted")
    monkeypatch.setattr("app.taiwan.universe.get_security_master", current_master_forbidden)
    class Response:
        def __init__(self, codes):
            self.content = json.dumps({"date": "20150105", "stat": "OK", "tables": [{
                "title": "每日收盤行情(存託憑證)", "fields": ["暫停交易", "證券代號", "證券名稱"],
                "data": [["", c, "irrelevant name"] for c in codes]}]}).encode()
        def raise_for_status(self):
            return None
    class Client:
        def get(self, url):
            return Response(["ABC", "1234"] if "type=9299&" in url else [])
    classifier = TwseHistoricalClassifier(HistoricalClassificationStore(tmp_path), Client())
    rows = classifier.classify_date(D)
    assert {r["code"] for r in rows} == {"ABC", "1234"}
    assert all(r["instrument_type"] == "tdr" and r["instrument_type_status"] == "verified"
               and r["primary_oos_eligible_type"] is False for r in rows)


def test_later_classification_does_not_erase_earlier_verified_record(tmp_path):
    from app.taiwan.pit_universe import PitUniverse
    store = HistoricalClassificationStore(tmp_path / "classification")
    for day in (D, date(2026, 1, 1)):
        store.write(day, [{"code": "2330", "exchange": "TWSE", "instrument_type": "stock",
                           "classification_effective_from": day, "classification_status": "verified"}])
    census = ObservedUniverseStore(tmp_path / "census")
    census.write("TWSE", D, [{"date": D, "raw_code": "2330", "exchange": "TWSE"}])
    assert PitUniverse(census, store).as_of(D)["instrument_type_status"].to_list() == ["verified"]


@pytest.mark.parametrize("stat", ["temporary upstream error", ""])
def test_classification_provider_error_never_completes_a_partition(tmp_path, stat):
    import json

    from app.taiwan.historical_classification import REQUESTS_PER_DATE, TwseHistoricalClassifier

    class Response:
        content = json.dumps({"stat": stat, "tables": []}).encode()

        def raise_for_status(self):
            return None

    class Client:
        def get(self, url):
            return Response()

    store = HistoricalClassificationStore(tmp_path)
    stats = TwseHistoricalClassifier(store, Client()).run([D], request_budget=REQUESTS_PER_DATE)
    assert stats["dates_done"] == 0
    assert len(stats["failed_dates"]) == 1
    assert not store.has(D)
