from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest

from app.services import preferences
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.fundamental_chips_service import TaiwanFundamentalChipsService


class _Adapter:
    token = "test"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, bool]] = []

    def fetch_financial_statements(self, symbol, start_date=None, raise_for_status=False):
        self.calls.append(("financial", start_date, raise_for_status))
        return [
            {"date": "2026-06-30", "type": "EPS", "value": 1.25},
            {"date": "2026-06-30", "type": "Revenue", "value": 1000000},
        ]

    def fetch_shareholding(self, symbol, start_date=None, raise_for_status=False):
        self.calls.append(("shareholding", start_date, raise_for_status))
        return [
            {"date": "2026-09-23", "ForeignInvestmentSharesRatio": 12.5, "ForeignInvestmentShares": 1000},
            {"date": "2026-09-24", "ForeignInvestmentSharesRatio": 12.7, "ForeignInvestmentShares": 1100},
        ]

    def fetch_securities_lending(self, symbol, start_date=None, raise_for_status=False):
        self.calls.append(("lending", start_date, raise_for_status))
        return [{"date": "2026-09-24", "volume": 3000, "fee_rate": 0.5}]


def test_finmind_detail_datasets_use_bounded_start_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "get_finmind_enabled", lambda: True)
    adapter = _Adapter()
    service = TaiwanFundamentalChipsService(
        finmind_adapter=adapter,
        cache=FinMindCache(tmp_path),
    )
    monkeypatch.setattr(service, "_cutoff_date", lambda _as_of: date(2026, 9, 29))

    financial = service.get_financial_statements("8358.TPEX")
    shareholding = service.get_foreign_shareholding("8358.TPEX")
    lending = service.get_securities_lending("8358.TPEX")

    assert financial.latest_eps == 1.25
    assert shareholding.ratio == 12.7
    assert lending.latest_volume == 3000
    assert adapter.calls == [
        ("financial", "2021-09-25", True),
        ("shareholding", "2026-04-02", True),
        ("lending", "2026-04-02", True),
    ]


def test_missing_eps_and_shareholding_expose_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "get_finmind_enabled", lambda: True)
    service = TaiwanFundamentalChipsService(
        finmind_adapter=_Adapter(),
        cache=FinMindCache(tmp_path),
    )

    financial = service._process_financial_statements(
        [{"date": "2026-06-30", "type": "NetIncome", "value": 100}],
        "2026-06-30",
        "2026-09-29T00:00:00+08:00",
    )
    shareholding = service._process_shareholding(
        [{"date": "2026-09-24", "ForeignInvestmentShares": 1000}],
        "2026-09-24",
        "2026-09-29T00:00:00+08:00",
    )

    assert financial.meta.status == "available"
    assert financial.latest_eps is None
    assert financial.meta.fallback_reason == "latest financial statement contains no EPS"
    assert shareholding.meta.status == "unavailable"
    assert shareholding.meta.fallback_reason == "shareholding rows contain no usable foreign ratio"


def test_historical_shareholding_uses_selected_date_and_cached_fetch_time(tmp_path):
    cache = FinMindCache(tmp_path)
    rows = [{"date": "2026-11-02", "ForeignInvestmentSharesRatio": 10},
            {"date": "2026-11-10", "ForeignInvestmentSharesRatio": 20}]
    cache.set("TaiwanStockShareholding", "2330.TWSE", rows, data_date="2026-11-10", status="available")
    fetched_at = cache.get("TaiwanStockShareholding", "2330.TWSE")["fetched_at"]
    service = TaiwanFundamentalChipsService(cache=cache)
    result = service.get_foreign_shareholding("2330.TWSE", as_of="2026-11-03")
    assert (result.ratio, result.meta.trade_date, result.meta.fetched_at) == (10, "2026-11-02", fetched_at)
    empty = service.get_foreign_shareholding("2330.TWSE", as_of="2026-11-01")
    assert empty.meta.trade_date is None
    assert empty.meta.fetched_at == fetched_at


@pytest.mark.parametrize(("observation", "expected"), [
    ("2026-10-09", 22.3), ("2026-10-08", 22.3), ("2026-11-09", None), (None, None), ("--", None),
])
def test_valuation_bundle_respects_trade_date_cutoff(tmp_path, monkeypatch, observation, expected):
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 10, 9, 5, tzinfo=UTC)
    monkeypatch.setattr("app.taiwan.finmind_cache.datetime", clock)
    cache = FinMindCache(tmp_path)
    symbol = "2330.TWSE"
    cache.set("TaiwanValuation", symbol, {"pe": 22.3}, data_date=observation)
    for dataset in ("TaiwanStockMonthRevenue", "TaiwanStockFinancialStatements"):
        cache.set(dataset, symbol, [], status="unavailable")
    official = Mock()
    service = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=_Adapter(), official_fundamentals=official)
    historical = service.get_fundamentals_bundle(symbol, "TWSE", as_of=date(2026, 10, 9)).valuation
    assert historical.pe == expected
    assert historical.meta.trade_date == (observation if expected is not None else None)
    assert historical.meta.status == ("available" if expected is not None else "unavailable")
    assert service.get_valuation(symbol, "TWSE").pe == 22.3
    official.valuation.assert_not_called()


def test_valuation_history_selects_saved_observation_when_cache_is_newer(tmp_path, monkeypatch):
    from app.config import settings
    from app.taiwan.fundamentals import FundamentalRecord, TaiwanFundamentalStore

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    symbol = "2330.TWSE"
    cache = FinMindCache(tmp_path / "cache")
    cache.set("TaiwanValuation", symbol, {"pe": 30}, data_date="2026-11-09")
    records = [FundamentalRecord(
        symbol=symbol, dataset="valuation", period_start=None, period_end=f"2026-10-{day:02d}",
        published_at=None, available_at=None, retrieved_at=datetime(2026, 10, day, 8, tzinfo=UTC),
        revision=str(day), provider="TWSE", source="twse:valuation", source_url="", status="official",
        normalized_unit="ratio", values={"pe": day, "dividend_yield": 0},
    ) for day in (7, 8, 12)]
    TaiwanFundamentalStore(tmp_path).save(records)
    official = Mock()
    service = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=_Adapter(), official_fundamentals=official)
    for dataset in ("TaiwanStockMonthRevenue", "TaiwanStockFinancialStatements"):
        cache.set(dataset, symbol, [], status="unavailable")
    historical = service.get_fundamentals_bundle(symbol, "TWSE", as_of="2026-10-09").valuation
    assert (historical.meta.trade_date, historical.pe, historical.dividend_yield) == ("2026-10-08", 8, 0)
    assert historical.meta.fetched_at == records[1].retrieved_at.isoformat()
    official.valuation.assert_not_called()


@pytest.mark.parametrize(("dataset", "method", "rows", "field", "row_day"), [
    ("TaiwanStockMonthRevenue", "get_monthly_revenue", [
        {"date": "2026-09-01", "create_time": "2026-10-08", "revenue": 100},
        {"date": "2026-11-01", "create_time": "2026-11-09", "revenue": 150},
    ], "latest_revenue", "2026-09-01"),
    ("TaiwanStockSecuritiesLending", "get_securities_lending", [
        {"date": "2026-10-08", "volume": 100, "fee_rate": 1.2},
        {"date": "2026-11-09", "volume": 400, "fee_rate": 1.5},
    ], "latest_volume", "2026-10-08"),
])
def test_historical_value_and_date_come_from_same_row(tmp_path, dataset, method, rows, field, row_day):
    cache = FinMindCache(tmp_path)
    symbol = "2330.TWSE"
    cache.set(dataset, symbol, rows, data_date=rows[-1]["date"])
    fetched_at = cache.get(dataset, symbol)["fetched_at"]
    read = getattr(TaiwanFundamentalChipsService(cache=cache, finmind_adapter=_Adapter()), method)
    result = read(symbol, as_of="2026-10-09")
    assert (getattr(result, field), result.meta.trade_date, result.meta.fetched_at) == (100, row_day, fetched_at)
    assert read(symbol, as_of="2026-09-01").meta.trade_date is None
    assert read(symbol).meta.trade_date == rows[-1]["date"]


@pytest.mark.parametrize(("first_rate", "second_rate", "average"), [
    (None, None, None), (None, 2.0, None), (1.0, "--", None), ("invalid", 2.0, None),
    (0.0, 0.0, 0.0), (0.0, 2.0, 1.5),
])
def test_lending_average_needs_rates_for_all_traded_volume(tmp_path, first_rate, second_rate, average):
    cache = FinMindCache(tmp_path)
    rows = [
        {"date": "2026-11-16", "volume": 200},
        {"date": "2026-11-17", "volume": 100, "fee_rate": first_rate},
        {"date": "2026-11-17", "volume": 300, "fee_rate": second_rate},
    ]
    cache.set("TaiwanStockSecuritiesLending", "2330.TWSE", rows, data_date="2026-11-17")
    service = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=_Adapter())
    result = service.get_securities_lending("2330.TWSE")
    assert result.latest_volume == 400
    assert result.meta.status == "available"
    assert result.avg_fee_rate == average


@pytest.mark.parametrize("cutoff", ["2026-10-08", "2026-10-09"])
def test_historical_valuation_uses_revision_known_at_cutoff(tmp_path, monkeypatch, cutoff):
    from dataclasses import replace

    from app.config import settings
    from app.taiwan.fundamentals import FundamentalRecord, TaiwanFundamentalStore

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    symbol = "2330.TWSE"
    first = FundamentalRecord(
        symbol=symbol, dataset="valuation", period_start=None, period_end="2026-10-08",
        published_at=None, available_at=None, retrieved_at=datetime(2026, 10, 8, 5, tzinfo=UTC),
        revision="v1", provider="TWSE", source="twse:valuation", source_url="", status="official",
        normalized_unit="ratio", values={"pe": 10},
    )
    second = replace(first, revision="v2", retrieved_at=datetime(2026, 11, 4, 5, tzinfo=UTC), values={"pe": 30})
    cache = Mock(spec=FinMindCache)
    cache.get.return_value = {"data": {"pe": 30}, "data_date": first.period_end,
                              "fetched_at": second.retrieved_at.isoformat()}
    svc = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=_Adapter(), official_fundamentals=Mock())
    assert svc.get_valuation(symbol, "TWSE", as_of=cutoff).pe is None
    TaiwanFundamentalStore(tmp_path).save([first, second])
    assert svc.get_valuation(symbol, "TWSE", as_of=cutoff).pe == 10
    assert svc.get_valuation(symbol, "TWSE", as_of="2026-11-04").pe == 30
    cache.get.return_value = None
    assert svc.get_valuation(symbol, "TWSE", as_of=cutoff).pe == 10


@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize(("dataset", "method"), [
    ("TaiwanStockFinancialStatements", "get_financial_statements"),
    ("TaiwanStockShareholding", "get_foreign_shareholding"),
    ("TaiwanStockSecuritiesLending", "get_securities_lending"),
])
def test_invalid_explicit_cutoff_is_rejected_before_lookup(tmp_path, cached, dataset, method):
    cache = FinMindCache(tmp_path)
    if cached:
        cache.set(dataset, "2330.TWSE", [{"date": "2026-09-30", "type": "EPS", "value": 5,
                                          "volume": 10, "ForeignInvestmentSharesRatio": 20}])
    adapter = _Adapter()
    service = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=adapter)
    with pytest.raises(ValueError, match="as_of"):
        getattr(service, method)("2330.TWSE", as_of="2026-02-30")
    assert adapter.calls == []


def test_pit_filters_reject_invalid_dates(tmp_path):
    from app.taiwan.pit_cutoff import filter_daily_records_as_of, filter_financial_statements_as_of

    cache = FinMindCache(tmp_path)
    rows = [{"date": day, "ForeignInvestmentSharesRatio": 90} for day in ("--", "2026-02-30", "2026-10-01Tbad")]
    cache.set("TaiwanStockShareholding", "2330.TWSE", rows)
    svc = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=_Adapter())
    historical = svc.get_foreign_shareholding("2330.TWSE", as_of="2026-10-09")
    assert (historical.ratio, historical.meta.trade_date, historical.meta.status) == (None, None, "unavailable")
    assert filter_financial_statements_as_of(rows, "2026-02-30") == []
    assert filter_daily_records_as_of(rows, "2026-02-30") == []
    assert filter_financial_statements_as_of(rows, None) == rows


@pytest.mark.parametrize("with_aliases", [False, True])
def test_financial_zero_metrics_are_preserved(tmp_path, with_aliases):
    rows = [{"date": "2026-06-30", "type": name, "value": 0} for name in ("Revenue", "IncomeAfterTaxes")]
    if with_aliases:
        rows.extend({"date": "2026-06-30", "type": name, "value": 50} for name in ("OperatingRevenue", "NetIncome"))
    cache = FinMindCache(tmp_path)
    cache.set("TaiwanStockFinancialStatements", "2330.TWSE", rows)
    result = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=_Adapter()).get_financial_statements("2330.TWSE")
    assert (result.operating_revenue, result.net_income, result.meta.status) == (0, 0, "available")


def test_financial_cache_hit_keeps_original_fetch_time(tmp_path, monkeypatch):
    from datetime import timedelta

    cache = FinMindCache(tmp_path)
    stamp = datetime.now(UTC) - timedelta(days=1)
    clock = Mock(wraps=datetime)
    clock.now.return_value = stamp
    with monkeypatch.context() as frozen:
        frozen.setattr("app.taiwan.finmind_cache.datetime", clock)
        cache.set("TaiwanStockFinancialStatements", "2330.TWSE",
                  [{"date": "2026-09-30", "type": "EPS", "value": 5}], data_date="2026-09-30")
    adapter = _Adapter()
    service = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=adapter)
    for cutoff, expected in [(None, 5), (date(2026, 10, 9), None)]:
        result = service.get_financial_statements("2330.TWSE", as_of=cutoff)
        assert result.latest_eps == expected
        assert result.meta.fetched_at == stamp.isoformat()
    assert adapter.calls == []


@pytest.mark.parametrize(("method", "fetch", "field", "row_fields"), [
    ("get_foreign_shareholding", "fetch_shareholding", "ratio", {"ForeignInvestmentSharesRatio": 12}),
    ("get_securities_lending", "fetch_securities_lending", "latest_volume", {"volume": 12, "fee_rate": 1}),
    ("get_monthly_revenue", "fetch_month_revenue", "latest_revenue", {"revenue": 12}),
])
def test_historical_range_expands_warm_cache(tmp_path, monkeypatch, method, fetch, field, row_fields):
    monkeypatch.setattr(preferences, "get_finmind_enabled", lambda: True)
    symbol = "2330.TWSE"
    rows = [dict(row_fields, date=d, create_time=d) for d in ("2024-02-27", "2026-11-17")]
    adapter = Mock()
    request = getattr(adapter, fetch)
    request.side_effect = lambda symbol, start_date, raise_for_status: [r for r in rows if r["date"] >= start_date]
    cache = FinMindCache(tmp_path)
    read = getattr(TaiwanFundamentalChipsService(cache=cache, finmind_adapter=adapter), method)
    assert read(symbol, as_of="2026-11-18").meta.trade_date == "2026-11-17"
    old = read(symbol, as_of="2024-03-02")
    assert (getattr(old, field), old.meta.trade_date) == (12, "2024-02-27")
    calls = request.call_count
    assert calls == 2
    assert read(symbol, as_of="2024-03-02").meta.trade_date == "2024-02-27"
    assert read(symbol, as_of="2026-11-18").meta.trade_date == "2026-11-17"
    assert request.call_count == calls


def test_range_expansion_failure_preserves_cached_observations(tmp_path, monkeypatch):
    from app.taiwan.providers.finmind_provider import FinMindError

    monkeypatch.setattr(preferences, "get_finmind_enabled", lambda: True)
    cache = FinMindCache(tmp_path)
    sym = "2330.TWSE"
    # Older bundles do not declare which dates were queried.
    cache.set("TaiwanStockShareholding", sym, [{"date": "2026-11-17", "ForeignInvestmentSharesRatio": 25}],
              data_date="2026-11-17")
    adapter = Mock()
    adapter.fetch_shareholding.side_effect = FinMindError("temporary upstream failure")
    read = TaiwanFundamentalChipsService(cache=cache, finmind_adapter=adapter).get_foreign_shareholding
    assert read(sym, as_of="2024-03-02").meta.status == "unavailable"
    adapter.fetch_shareholding.assert_called_once()
    assert read(sym).ratio == 25


@pytest.mark.parametrize(("method", "fetch", "fields"), [
    ("get_foreign_shareholding", "fetch_shareholding", {"ForeignInvestmentSharesRatio": 12}),
    ("get_securities_lending", "fetch_securities_lending", {"volume": 12, "fee_rate": 1}),
    ("get_monthly_revenue", "fetch_month_revenue", {"revenue": 12}),
])
def test_narrow_query_matches_cold_result_after_historical_lookup(tmp_path, monkeypatch, method, fetch, fields):
    monkeypatch.setattr(preferences, "get_finmind_enabled", lambda: True)
    row = {**fields, "date": "2024-02-27", "create_time": "2024-02-27"}
    adapter = Mock()
    request = getattr(adapter, fetch)
    request.side_effect = lambda symbol, start_date, raise_for_status: [row] if start_date <= row["date"] else []

    def reader(directory):
        return getattr(TaiwanFundamentalChipsService(cache=FinMindCache(directory), finmind_adapter=adapter), method)

    warm = reader(tmp_path / "warm")
    assert warm("2330.TWSE", as_of="2024-03-02").meta.status == "available"
    expected = reader(tmp_path / "cold")("2330.TWSE", as_of="2026-11-18").meta
    actual = warm("2330.TWSE", as_of="2026-11-18").meta
    assert (actual.status, actual.trade_date) == (expected.status, expected.trade_date) == ("unavailable", None)
    assert request.call_count == 2
