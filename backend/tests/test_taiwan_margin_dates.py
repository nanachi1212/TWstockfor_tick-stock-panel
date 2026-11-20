from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.jobs.daily_pipeline import _lock_forward_selections
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.institutional_margin_refresh import TaiwanMarginRefreshService
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.observed_universe import ObservedUniverseStore

DAY = date(2026, 11, 20)


@pytest.fixture
def market(tmp_path):
    store = TaiwanMarginStore(tmp_path / "margin")
    evidence = ObservedUniverseStore(tmp_path / "sessions")
    refresh = TaiwanMarginRefreshService(store=store, evidence_store=evidence)
    twse = {"date": "20261120", "tables": [
        {"title": "115年11月20日 信用交易統計", "data": [["融資(交易單位)", 1, 2, 3, 4, 5]]},
        {"title": "115年11月20日 融資融券彙總 (全部)", "data": [["2330", "上市", *(["1"] * 14)]]},
    ]}
    tpex = {"date": "20261120", "tables": [
        {"date": "115/11/20", "title": "上櫃股票融資融券餘額", "data": [["6488", "上櫃", *(["1"] * 18)]]},
    ]}
    refresh._fetch_json = lambda url: tpex if "tpex" in url else twse
    return store, refresh, evidence, twse, tpex


def test_previous_session_responses_cannot_release_the_scheduled_lock(market, monkeypatch):
    store, refresh, evidence, twse, tpex = market
    twse["date"] = tpex["date"] = "20261119"
    twse["tables"][1]["title"] = "115年11月19日 融資融券彙總 (全部)"
    tpex["tables"][0]["date"] = "115/11/19"
    published = SimpleNamespace(available_dates=lambda: [DAY], partial_dates=set)
    update = TaiwanDailyUpdateService(daily_store=published, inst_store=published, margin_store=store,
                                     margin_service=refresh, evidence_store=evidence)
    locks = []
    monkeypatch.setattr("app.taiwan.selection_review_service.lock_daily_forward_batches",
                        lambda **kwargs: locks.append(kwargs))
    result = update.run_update(target_date=DAY)
    if result.daily.status == result.overall_status == "success":
        _lock_forward_selections(result, only_missing=True)
    assert (store.available_dates(), result.margin.status, locks) == ([], "failed", [])


@pytest.mark.parametrize("exchange", ["twse", "tpex"])
@pytest.mark.parametrize("issue", ["stale_table", "stale_envelope", "missing", "unpublished"])
def test_invalid_or_unpublished_market_cannot_replace_a_good_partition(market, exchange, issue):
    store, refresh, _, twse, tpex = market
    assert refresh.refresh_dates(DAY, DAY)["total_rows_written"] == 2
    path = store._data_dir / f"date={DAY}" / "part.parquet"
    original = path.read_bytes()
    payload, table = (twse, twse["tables"][1]) if exchange == "twse" else (tpex, tpex["tables"][0])
    if issue == "stale_table":
        if exchange == "twse":
            table["title"] = "115年11月19日 融資融券彙總 (全部)"
        else:
            table["date"] = "115/11/19"
    elif issue == "stale_envelope":
        payload["date"] = "20261119"
    elif issue == "missing":
        del payload["date"]
        table.pop("date", None)
        table["title"] = "融資融券"
    else:
        table["data"].clear()
    result = refresh.refresh_dates(DAY, DAY, force=True)
    assert result["pending_dates"] if issue == "unpublished" else result["failed_dates"]
    assert result["dates_fetched"] == result["total_rows_written"] == 0
    assert path.read_bytes() == original


def test_legacy_target_needs_two_verified_responses_before_publication(market):
    store, refresh, _, _, tpex = market
    old = date(2026, 11, 19)
    for day in (old, DAY):
        store.write_batch(pl.DataFrame({"date": [day], "symbol": ["2317.TWSE"], "margin_balance": [42]}))
    old_path = store._data_dir / f"date={old}" / "part.parquet"
    target_path = store._data_dir / f"date={DAY}" / "part.parquet"
    original, prior = target_path.read_bytes(), old_path.read_bytes()
    assert not store.has_verified_publication(DAY)
    tpex["tables"][0]["date"] = "115/11/19"
    rejected = refresh.refresh_dates(old, DAY)
    assert rejected["dates_skipped"] == 1 and rejected["failed_dates"]
    assert target_path.read_bytes() == original and not store.has_verified_publication(DAY)
    tpex["tables"][0]["date"] = "115/11/20"
    verified = refresh.refresh_dates(old, DAY)
    assert verified["dates_skipped"] == 1 and verified["total_rows_written"] == 2
    assert store.has_verified_publication(DAY)
    assert set(store.read_range(None, DAY, DAY)["symbol"]) == {"2330.TWSE", "6488.TPEX"}
    assert old_path.read_bytes() == prior and not store.has_verified_publication(old)
    assert refresh.refresh_dates(DAY, DAY)["dates_skipped"] == 1


def test_daily_update_rechecks_only_legacy_target_until_both_exchanges_publish(market, monkeypatch):
    store, refresh, evidence, _, tpex = market
    old = date(2026, 11, 19)
    for day in (old, DAY):
        store.write_batch(pl.DataFrame({"date": [day], "symbol": ["2317.TWSE"], "margin_balance": [42]}))
    other_datasets = SimpleNamespace(available_dates=lambda: [old, DAY], partial_dates=set)
    update = TaiwanDailyUpdateService(daily_store=other_datasets, inst_store=other_datasets, margin_store=store,
                                     margin_service=refresh, evidence_store=evidence)
    attempts = []
    def refresh_target(start, end, force=False):
        attempts.append((start, end))
        return TaiwanMarginRefreshService.refresh_dates(refresh, start, end, force=force)
    monkeypatch.setattr(refresh, "refresh_dates", refresh_target)
    locks = []
    monkeypatch.setattr("app.taiwan.selection_review_service.lock_daily_forward_batches",
                        lambda **kwargs: locks.append(kwargs))
    rows = tpex["tables"][0].pop("data")
    first = update.run_update(target_date=DAY)
    assert first.margin.pending_dates and not first.margin_publication_verified
    _lock_forward_selections(first, only_missing=True)
    assert locks == []
    tpex["tables"][0]["data"] = rows
    second = update.run_update(target_date=DAY)
    assert second.margin.dates_fetched == 1 and second.margin_publication_verified
    _lock_forward_selections(second, only_missing=True)
    assert locks == [{"only_missing_for": DAY}]
    third = update.run_update(target_date=DAY)
    assert third.margin.dates_skipped == 1 and third.margin_publication_verified
    assert attempts == [(DAY, DAY), (DAY, DAY)]


def test_table_only_dates_and_ordinary_upsert_do_not_share_verification(market):
    store, refresh, _, twse, tpex = market
    del twse["date"], tpex["date"]
    assert refresh.refresh_dates(DAY, DAY)["dates_fetched"] == 1
    assert store.has_verified_publication(DAY)
    store.write_batch(pl.DataFrame({"date": [DAY], "symbol": ["2317.TWSE"], "margin_balance": [42]}))
    assert not store.has_verified_publication(DAY)


def test_unreadable_footer_can_be_recovered_only_with_verified_responses(market):
    store, refresh, _, _, tpex = market
    path = store._data_dir / f"date={DAY}" / "part.parquet"
    path.parent.mkdir()
    path.write_bytes(b"incomplete fixture")
    rows = tpex["tables"][0].pop("data")
    assert not store.has_verified_publication(DAY)
    assert refresh.refresh_dates(DAY, DAY)["pending_dates"]
    assert path.read_bytes() == b"incomplete fixture"
    tpex["tables"][0]["data"] = rows
    assert refresh.refresh_dates(DAY, DAY)["dates_fetched"] == 1
    assert store.has_verified_publication(DAY)
