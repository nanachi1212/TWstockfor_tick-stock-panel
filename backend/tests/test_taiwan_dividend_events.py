from __future__ import annotations

# ruff: noqa: RUF001 -- fixtures mirror official MOPS text exactly.
import copy
import json
from datetime import date, datetime

import httpx
import pytest

from app.taiwan.dividend_events import (
    DividendLifecycleEvent,
    MOPSDividendLifecycleProvider,
    TaiwanDividendEventStore,
    classify_dividend_event,
    get_dividend_events_as_of,
)
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.providers.taiwan_values import TAIPEI

OFFICIAL_CASES = {
    "2330": {
        "exchange": "TWSE",
        "market": "sii",
        "date": "115/08/11",
        "enter_date": "1150811",
        "events": [
            (
                "3",
                "18:53:34",
                "本公司董事會決議股利分派",
                "board_resolution",
                "1. 董事會決議日期：115/08/11\n"
                "　(1)盈餘分配之現金股利(元/股)：7.00000000\n"
                "　(2)法定盈餘公積發放之現金(元/股)：0\n"
                "　(3)資本公積發放之現金(元/股)：0\n"
                "　(5)盈餘轉增資配股(元/股)：0\n"
                "　(6)法定盈餘公積轉增資配股(元/股)：0\n"
                "　(7)資本公積轉增資配股(元/股)：0",
            ),
            (
                "4",
                "19:01:29",
                "本公司訂定民國115年第二季之現金股利除息交易日",
                "ex_date_announcement",
                "4.除權（息）交易日:115/12/10\n"
                "5.最後過戶日:115/12/11\n"
                "8.除權（息）基準日:115/12/16\n"
                "12.普通股現金股利發放日期:116/01/07",
            ),
        ],
        "cash": 7.0,
        "ex_date": "2026-12-10",
        "basis_date": "2026-12-16",
        "payment_date": "2027-01-07",
    },
    "6488": {
        "exchange": "TPEX",
        "market": "otc",
        "date": "115/03/03",
        "enter_date": "1150303",
        "events": [
            (
                "1",
                "16:05:53",
                "公告本公司董事會決議股利分派",
                "board_resolution",
                "1. 董事會決議日期：115/03/03\n"
                "　(1)盈餘分配之現金股利(元/股)：5.70000000\n"
                "　(2)法定盈餘公積發放之現金(元/股)：0\n"
                "　(3)資本公積發放之現金(元/股)：0\n"
                "　(5)盈餘轉增資配股(元/股)：0\n"
                "　(6)法定盈餘公積轉增資配股(元/股)：0\n"
                "　(7)資本公積轉增資配股(元/股)：0",
            ),
            (
                "2",
                "16:06:25",
                "公告本公司董事會決議除息基準日",
                "basis_date_announcement",
                "4.除權（息）交易日:115/07/16\n"
                "5.最後過戶日:115/07/17\n"
                "8.除權（息）基準日:115/07/22\n"
                "12.普通股現金股利發放日期:115/08/14",
            ),
        ],
        "cash": 5.7,
        "ex_date": "2026-07-16",
        "basis_date": "2026-07-22",
        "payment_date": "2026-08-14",
    },
    "2881": {
        "exchange": "TWSE",
        "market": "sii",
        "date": "115/04/30",
        "enter_date": "1150430",
        "events": [
            (
                "7",
                "18:06:38",
                "公告本公司董事會決議普通股股利分派情形",
                "board_resolution",
                "1. 董事會擬議日期：115/04/30\n"
                "　(1)盈餘分配之現金股利(元/股)：4.25000000\n"
                "　(2)法定盈餘公積發放之現金(元/股)：0\n"
                "　(3)資本公積發放之現金(元/股)：0\n"
                "　(5)盈餘轉增資配股(元/股)：0\n"
                "　(6)法定盈餘公積轉增資配股(元/股)：0\n"
                "　(7)資本公積轉增資配股(元/股)：0",
            ),
        ],
        "cash": 4.25,
        "ex_date": None,
        "basis_date": None,
        "payment_date": None,
    },
}


def _payload(data):
    return {"code": 200, "message": "查詢成功", "result": {"data": data}}


def _client_for(case):
    details = {
        event[0]: [
            event[0],
            case["date"],
            event[1],
            "發言人",
            "職稱",
            "電話",
            event[2],
            "第14款",
            case["date"],
            event[4],
        ]
        for event in case["events"]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("t05st01_detail"):
            return httpx.Response(200, json=_payload([details[body["serialNumber"]]]))
        rows = [
            [
                body["companyId"],
                "公司",
                case["date"],
                event[1],
                event[2],
                {
                    "apiName": "t05st01_detail",
                    "parameters": {
                        "companyId": body["companyId"],
                        "marketKind": case["market"],
                        "enterDate": case["enter_date"],
                        "serialNumber": event[0],
                    },
                },
            ]
            for event in case["events"]
        ]
        return httpx.Response(200, json=_payload(rows))

    return httpx.Client(transport=httpx.MockTransport(handler))


def _event(identity: str, available_at: str, *, symbol: str = "2330.TWSE"):
    timestamp = datetime.fromisoformat(available_at)
    return DividendLifecycleEvent(
        symbol=symbol,
        event_type="board_resolution",
        event_timestamp=timestamp,
        available_at=timestamp,
        cash_dividend=None,
        stock_dividend=None,
        ex_date=None,
        basis_date=None,
        record_date=None,
        payment_date=None,
        raw_subject="董事會決議股利分派",
        raw_status=None,
        normalized_status="board_approved",
        provider="TWSE",
        source="mops:historical_material_information",
        source_url="https://mops.twse.com.tw/mops/api/t05st01",
        retrieved_at=datetime(2026, 8, 31, tzinfo=TAIPEI),
        availability_policy="exact_timestamp",
        availability_evidence_source="MOPS",
        availability_evidence_url="https://mops.twse.com.tw/mops/api/t05st01_detail",
        availability_evidence_identifier=identity,
        availability_confidence="verified",
        revision_identity=identity,
    )


def test_exact_timestamp_boundary_is_strict():
    event = _event("sii/2330/1150811/3", "2026-08-11T18:53:34+08:00")
    assert get_dividend_events_as_of(
        [event], event.symbol, datetime.fromisoformat("2026-08-11T18:53:33+08:00")
    ) == []
    assert get_dividend_events_as_of(
        [event], event.symbol, datetime.fromisoformat("2026-08-11T18:53:34+08:00")
    ) == []
    assert get_dividend_events_as_of(
        [event], event.symbol, datetime.fromisoformat("2026-08-11T18:53:35+08:00")
    ) == [event]


def test_store_preserves_distinct_serials_and_later_truth(tmp_path):
    first = _event("sii/2330/1150811/3", "2026-08-11T18:53:34+08:00")
    later = _event("sii/2330/1150811/9", "2026-08-20T12:00:00+08:00")
    store = TaiwanDividendEventStore(tmp_path)
    assert store.save([first, later, first]) == 2
    assert store.get_as_of(
        first.symbol, datetime.fromisoformat("2026-08-12T00:00:00+08:00")
    ) == [first]
    assert store.get_as_of(
        first.symbol, datetime.fromisoformat("2026-08-21T00:00:00+08:00")
    ) == [first, later]


def test_missing_fields_stay_none_and_etf_does_not_request_network():
    provider = MOPSDividendLifecycleProvider(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(AssertionError(request.url))
            )
        )
    )
    assert provider.events(
        "0050.TWSE",
        "TWSE",
        date(2026, 8, 11),
        date(2026, 8, 11),
        security_type="etf",
    ) == []
    event = _event("sii/2330/1150811/3", "2026-08-11T18:53:34+08:00")
    assert event.cash_dividend is None
    assert event.stock_dividend is None
    assert event.payment_date is None
    assert event.raw_status is None
    assert event.supersedes_revision is None


def test_classification_is_explicit_and_conservative():
    assert classify_dividend_event("本公司董事會決議股利分派") == "board_resolution"
    assert classify_dividend_event("本公司訂定現金股利除息交易日") == "ex_date_announcement"
    assert classify_dividend_event("公告本公司現金股利配發基準日") == "basis_date_announcement"
    assert classify_dividend_event("代子公司公告董事會決議股利分派") is None
    assert classify_dividend_event("董事會通過財務報告") is None
    assert classify_dividend_event("股東會重要決議事項") is None


def test_malformed_official_event_time_fails_closed():
    case = copy.deepcopy(OFFICIAL_CASES["2330"])
    event = list(case["events"][0])
    event[1] = "18:99:34"
    case["events"] = [tuple(event)]
    provider = MOPSDividendLifecycleProvider(_client_for(case))
    with pytest.raises(ValueError, match="invalid MOPS event time"):
        provider.events(
            "2330.TWSE",
            "TWSE",
            date(2026, 8, 11),
            date(2026, 8, 11),
        )


def test_official_historical_cases_and_provenance():
    for code, case in OFFICIAL_CASES.items():
        provider = MOPSDividendLifecycleProvider(_client_for(case))
        day = parse_case_date(case["date"])
        events = provider.events(
            f"{code}.{'TWSE' if case['exchange'] == 'TWSE' else 'TPEX'}",
            case["exchange"],
            day,
            day,
        )
        assert [event.event_type for event in events] == [
            item[3] for item in case["events"]
        ]
        board = events[0]
        assert board.cash_dividend == case["cash"]
        assert board.stock_dividend == 0
        assert board.availability_policy == "exact_timestamp"
        assert board.availability_confidence == "verified"
        assert board.revision_identity == (
            f"{case['market']}/{code}/{case['enter_date']}/{case['events'][0][0]}"
        )
        assert board.availability_evidence_identifier == board.revision_identity
        if len(events) > 1:
            announced = events[1]
            assert announced.ex_date == case["ex_date"]
            assert announced.basis_date == case["basis_date"]
            assert announced.payment_date == case["payment_date"]
            assert announced.cash_dividend is None
            assert announced.stock_dividend is None


def test_hybrid_provider_exposes_existing_official_seam(monkeypatch):
    case = OFFICIAL_CASES["2330"]
    monkeypatch.setattr(
        "app.taiwan.fundamentals.TaiwanOfficialFundamentals.__init__",
        lambda self, client=None: setattr(self, "client", _client_for(case)),
    )
    provider = TaiwanHybridProvider()
    day = date(2026, 8, 11)
    events = provider.get_fundamentals(
        "2330.TWSE",
        "dividend_lifecycle_event",
        exchange="TWSE",
        start_date=day,
        end_date=day,
    )
    assert [event.event_type for event in events] == [
        "board_resolution",
        "ex_date_announcement",
    ]


def parse_case_date(raw: str) -> date:
    year, month, day = (int(value) for value in raw.split("/"))
    return date(year + 1911, month, day)
