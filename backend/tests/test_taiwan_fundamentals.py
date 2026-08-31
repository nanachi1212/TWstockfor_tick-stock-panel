from __future__ import annotations

# ruff: noqa: RUF001 -- fixtures mirror official field names exactly.
from datetime import datetime

import httpx
import pytest

from app.taiwan.fundamentals import (
    FundamentalRecord,
    ShareCapital,
    TaiwanFundamentalStore,
    TaiwanOfficialFundamentals,
    latest_as_of,
    normalize_dividend_status,
    roc_month,
    thousand_twd,
)
from app.taiwan.providers.taiwan_values import TAIPEI, parse_number


def _record(available: str, revenue: int, revision: str) -> FundamentalRecord:
    return FundamentalRecord(
        symbol="2330.TWSE", dataset="financial_statement",
        period_start="2026-01-01", period_end="2026-03-31",
        published_at=datetime.fromisoformat(available),
        available_at=datetime.fromisoformat(available),
        retrieved_at=datetime(2026, 5, 30, tzinfo=TAIPEI), revision=revision,
        provider="TWSE", source="twse:fixture", source_url="https://example.invalid",
        status="official", normalized_unit="TWD", raw_unit="thousand_TWD",
        values={"revenue": revenue}, accounting_category="ci",
    )


def test_strict_availability_and_revision_selection():
    a = _record("2026-05-10T00:00:00+08:00", 100, "A")
    b = _record("2026-05-20T00:00:00+08:00", 110, "B")
    assert latest_as_of([a, b], a.symbol, datetime.fromisoformat("2026-05-09T23:59:59+08:00")) is None
    assert latest_as_of([a, b], a.symbol, datetime.fromisoformat("2026-05-10T00:00:00+08:00")) is None
    assert latest_as_of([a, b], a.symbol, datetime.fromisoformat("2026-05-10T00:00:01+08:00")) == a
    assert latest_as_of([a, b], a.symbol, datetime.fromisoformat("2026-05-15T00:00:00+08:00")) == a
    assert latest_as_of([a, b], a.symbol, datetime.fromisoformat("2026-05-21T00:00:00+08:00")) == b


def test_store_preserves_revisions_and_round_trips(tmp_path):
    store = TaiwanFundamentalStore(tmp_path)
    assert store.save([_record("2026-05-10T00:00:00+08:00", 100, "A")]) == 1
    assert store.save([_record("2026-05-20T00:00:00+08:00", 110, "B")]) == 2
    selected = store.get_as_of("2330.TWSE", datetime.fromisoformat("2026-05-15T00:00:00+08:00"))
    assert selected is not None and selected.values["revenue"] == 100


def test_number_month_and_unit_contract():
    assert parse_number("N/A") is None
    assert parse_number("0") == 0
    with pytest.raises(ValueError):
        parse_number("abc")
    assert thousand_twd("1,000") == 1_000_000
    assert roc_month("11507") == "2026-07"


def test_official_monthly_revenue_has_provenance_but_no_invented_available_time():
    row = {
        "出表日期": "1150831", "資料年月": "11507", "公司代號": "2330",
        "營業收入-當月營收": "1,000", "營業收入-上月營收": "0",
        "營業收入-去年當月營收": "N/A", "營業收入-上月比較增減(%)": "10",
        "營業收入-去年同月增減(%)": "--", "累計營業收入-當月累計營收": "2,000",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    got = TaiwanOfficialFundamentals(httpx.Client(transport=transport)).monthly_revenue("2330.TWSE", "TWSE")
    assert got.values == {"revenue": 1_000_000, "previous_month": 0, "previous_year_month": None, "mom": 10, "yoy": None, "cumulative": 2_000_000}
    assert got.provider == "TWSE" and got.source_url and got.raw_unit == "thousand_TWD"
    assert got.available_at is None and got.published_at is None and got.status == "data_insufficient"


def test_statement_category_is_resolved_by_unique_official_dataset_not_symbol_heuristic():
    income = {"出表日期": "1150830", "年度": "115", "季別": "2", "公司代號": "2881", "淨收益": "100", "繼續營業單位稅前損益": "80", "本期稅後淨利（淨損）": "60", "基本每股盈餘（元）": "1.5"}
    balance = {"公司代號": "2881", "資產總計": "1,000", "負債總計": "700", "權益總計": "300"}
    def handler(request):
        if request.url.path.endswith("06_L_fh"):
            return httpx.Response(200, json=[income])
        if request.url.path.endswith("07_L_fh"):
            return httpx.Response(200, json=[balance])
        return httpx.Response(200, json=[])
    got = TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(handler))).financial_statement("2881.TWSE", "TWSE")
    assert got.accounting_category == "fh" and got.statement_type == "unknown"
    assert got.values["revenue"] == 100_000 and got.values["assets"] == 1_000_000
    assert got.available_at is None and got.published_at is None
    assert got.status == "data_insufficient"


def test_valuation_trade_date_and_retrieval_do_not_create_availability():
    row = {
        "Date": "1150831", "Code": "2330", "Name": "台積電",
        "PEratio": "20", "PBratio": "5", "DividendYield": "2",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    got = TaiwanOfficialFundamentals(httpx.Client(transport=transport)).valuation(
        "2330.TWSE", "TWSE"
    )
    assert got.period_end == "2026-08-31"
    assert got.values == {"pe": 20, "pb": 5, "dividend_yield": 2}
    assert got.retrieved_at is not None
    assert got.available_at is None and got.published_at is None
    assert got.status == "data_insufficient"


def test_etf_company_fundamentals_fail_closed():
    provider = TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    for dataset, method in (
        ("monthly_revenue", provider.monthly_revenue),
        ("financial_statement", provider.financial_statement),
        ("share_capital_record", provider.share_capital),
    ):
        got = method("0050.TWSE", "TWSE", security_type="etf")
        assert got.dataset == dataset and got.status == "unsupported" and got.values is None


@pytest.mark.parametrize(
    ("exchange", "row", "expected"),
    [
        (
            "TWSE",
            {
                "出表日期": "1150830", "公司代號": "2330",
                "普通股每股面額": "新台幣 10.0000元", "實收資本額": "259323700670",
                "已發行普通股數或TDR原股發行股數": "25932370067",
            },
            (25_932_370_067, 259_323_700_670, 10.0),
        ),
        (
            "TPEX",
            {
                "Date": "1150830", "SecuritiesCompanyCode": "6488",
                "ParValueOfCommonStock": "新台幣 10.0000元",
                "Paidin.Capital.NTDollars": "4781137250", "IssueShares": "478113725",
            },
            (478_113_725, 4_781_137_250, 10.0),
        ),
    ],
)
def test_current_share_capital_preserves_distinct_concepts_without_historical_availability(
    exchange, row, expected,
):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    symbol = "2330.TWSE" if exchange == "TWSE" else "6488.TPEX"
    got = TaiwanOfficialFundamentals(httpx.Client(transport=transport)).share_capital(
        symbol, exchange
    )
    assert got.period_end == "2026-08-30"
    assert got.values == {
        "total_shares": None,
        "issued_shares": expected[0],
        "float_shares": None,
        "capital_twd": expected[1],
        "par_value_twd": expected[2],
    }
    assert got.available_at is None and got.published_at is None
    assert got.status == "data_insufficient"


def test_share_capital_concepts_are_not_substituted():
    capital = ShareCapital(issued_shares=1000, capital_twd=10_000)
    assert capital.issued_shares == 1000
    assert capital.total_shares is None and capital.float_shares is None
    assert capital.status == "data_insufficient"


def test_dividend_status_contract():
    assert normalize_dividend_status("董事會決議") == "board_approved"
    assert normalize_dividend_status("股東會通過") == "shareholder_approved"
    assert normalize_dividend_status("") == "unknown"
