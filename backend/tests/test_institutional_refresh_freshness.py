from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.enrichment.models import InstitutionalFlow, SourceMeta
from app.taiwan.institutional_margin_refresh import TaiwanInstitutionalRefreshService
from app.taiwan.institutional_store import TaiwanInstitutionalStore


def _adapter(symbol: str, source: str) -> SimpleNamespace:
    def parse_payload(_payload, trade_date, url):
        return [
            InstitutionalFlow(
                symbol=symbol,
                trade_date=trade_date,
                foreign_buy=10,
                foreign_sell=5,
                foreign_net=5,
                investment_trust_buy=2,
                investment_trust_sell=1,
                investment_trust_net=1,
                dealer_buy=3,
                dealer_sell=1,
                dealer_net=2,
                meta=SourceMeta(
                    source=source,
                    source_url=url,
                    fetched_at="2026-10-06T00:00:00",
                    trade_date=trade_date,
                    status="stale",
                    source_type="official_open_data",
                ),
            )
        ]

    return SimpleNamespace(
        build_url=lambda trade_date: f"mock://{source}/{trade_date}",
        parse_payload=parse_payload,
    )


def test_persisted_official_history_remains_valid_for_session_windows(tmp_path):
    store = TaiwanInstitutionalStore(tmp_path / "institutional")
    provider = SimpleNamespace(
        twse=_adapter("2330.TWSE", "twse:t86"),
        tpex=_adapter("8069.TPEX", "tpex:daily_trade"),
    )
    service = TaiwanInstitutionalRefreshService(store=store, provider=provider)
    service._fetch_json = lambda _url: {}

    result = service.refresh_dates(date(2026, 10, 1), date(2026, 10, 1))

    assert result["dates_fetched"] == 1
    assert set(store.read_all()["status"].to_list()) == {"official"}


def test_existing_complete_official_rows_are_normalized_on_read(tmp_path):
    store = TaiwanInstitutionalStore(tmp_path / "institutional")
    store.write_batch(
        pl.DataFrame(
            {
                "symbol": ["2330.TWSE", "9999.TWSE"],
                "date": [date(2026, 10, 1)] * 2,
                "foreign_net": [5, 5],
                "investment_trust_net": [1, 1],
                "dealer_net": [2, 2],
                "status": ["stale", "stale"],
                "source": ["twse:t86", "unknown"],
            }
        )
    )

    statuses = dict(store.read_all().select("symbol", "status").iter_rows())

    assert statuses == {"2330.TWSE": "official", "9999.TWSE": "stale"}


@pytest.mark.parametrize("twse_date,tpex_date,table_date", [
    ("20260827", "20260828", "115/08/28"), ("20260828", "115/08/27", "115/08/27"),
    ("20260828", "20260828", "115/08/27"), (None, "20260828", "115/08/28"),
    ("bad date", "20260828", "115/08/28"),
])
def test_official_response_date_required_before_persisting(tmp_path, twse_date, tpex_date, table_date):
    from app.taiwan.observed_universe import ObservedUniverseStore

    day = date(2026, 8, 28)
    store = TaiwanInstitutionalStore(tmp_path / "institutional")
    service = TaiwanInstitutionalRefreshService(store=store, evidence_store=ObservedUniverseStore(tmp_path / "sessions"))
    twse = {"date": twse_date, "data": [["2330", "fixture", *(["0"] * 17)]]}
    tpex = {"date": tpex_date, "tables": [{"date": table_date, "data": [["6488", "fixture", *(["0"] * 22)]]}]}
    service._fetch_json = lambda url: tpex if "tpex" in url else twse
    assert service.refresh_dates(day, day)["failed_dates"]
    assert not store.available_dates()
    twse["date"], tpex["date"], tpex["tables"][0]["date"] = "20260828", "20260828", "115/08/28"
    assert service.refresh_dates(day, day)["total_rows_written"] == 2
    assert store.read_all()["trade_date"].to_list() == [day, day]


@pytest.fixture
def official_market_rows(tmp_path):
    from app.taiwan.observed_universe import ObservedUniverseStore

    day = date(2026, 8, 28)
    store = TaiwanInstitutionalStore(tmp_path / "institutional")
    service = TaiwanInstitutionalRefreshService(store=store, evidence_store=ObservedUniverseStore(tmp_path / "census"))
    twse = {"date": "20260828", "data": [[code, "fixture", *(["0"] * 17)] for code in ("2330", "2317", "2454")]}
    tpex = {"date": "20260828", "tables": [{"date": "115/08/28", "data": [
        [code, "fixture", *(["0"] * 22)] for code in ("6488", "8069")]}]}
    service._fetch_json = lambda url: tpex if "tpex" in url else twse
    return day, store, service, twse, tpex


def test_per_security_rejection_replaces_old_values_and_accepts_other_rows(official_market_rows):
    day, store, service, twse, tpex = official_market_rows
    assert service.refresh_dates(day, day)["total_rows_written"] == 5
    twse["data"][1][4] = "--"
    twse["data"].pop()  # The replacement no longer reports 2454.
    tpex["tables"][0]["data"][1][13] = "12x3"
    report = service.refresh_dates(day, day, force=True)
    assert {r["symbol"]: r["reason"] for r in report["rejected_rows"]} == {
        "2317.TWSE": "missing required institutional field foreign_net",
        "8069.TPEX": "invalid required institutional field investment_trust_net",
    }
    assert report["dates_fetched"] == 1
    assert not report["failed_dates"] and not report["pending_dates"]
    saved = {r["symbol"]: r for r in store.read_range(None, day, day).to_dicts()}
    assert set(saved) == {"2330.TWSE", "2317.TWSE", "2454.TWSE", "6488.TPEX", "8069.TPEX"}
    for symbol in ("2330.TWSE", "6488.TPEX", "2454.TWSE"):
        assert saved[symbol]["status"] == "official" and saved[symbol]["foreign_net"] == 0
    for symbol in ("2317.TWSE", "8069.TPEX"):
        assert saved[symbol]["status"] == "data_incomplete" and saved[symbol]["status_reason"]
        assert all(saved[symbol][field] is None for field in
                   ("foreign_net", "investment_trust_net", "dealer_net", "official_net", "computed_net"))
        assert store.read_latest_per_symbol([symbol])["foreign_net"][0] is None
    twse["data"][1][4] = tpex["tables"][0]["data"][1][13] = "0"
    assert not service.refresh_dates(day, day, force=True)["rejected_rows"]
    assert set(store.read_all()["status"]) == {"official"}
    assert store.read_all()["status_reason"].null_count() == 5


@pytest.mark.parametrize("failure", ["empty_exchange", "all_invalid", "short_row", "missing_code", "duplicate_code"])
def test_whole_exchange_or_structural_failure_never_publishes_a_partial_market(official_market_rows, failure):
    day, store, service, twse, tpex = official_market_rows
    rows = tpex["tables"][0]["data"]
    if failure == "empty_exchange":
        rows.clear()
        twse["data"][1][4] = "--"
    elif failure == "all_invalid":
        for row in rows:
            row[10] = "--"
    elif failure == "short_row":
        rows[1].pop()
    elif failure == "missing_code":
        rows[1][0] = None
    else:
        rows[1][0] = rows[0][0]
    result = service.refresh_dates(day, day)
    assert result["pending_dates"] if failure == "empty_exchange" else result["failed_dates"]
    if failure == "empty_exchange":
        assert [r["symbol"] for r in result["rejected_rows"]] == ["2317.TWSE"]
    assert result["dates_fetched"] == 0 and not store.available_dates()
