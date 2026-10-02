# ruff: noqa: RUF001
from __future__ import annotations

import json
from datetime import datetime
from html import escape
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.taiwan.events_service import MarketEvent, TaiwanEventService
from app.taiwan.providers import mops_events as provider
from app.taiwan.providers.mops_events import (
    CONFERENCE_HEADERS,
    CONFERENCE_QUERY_URL,
    CONFERENCE_SOURCE,
    CONFERENCE_URL,
    TOPICS,
    classify_material_clause,
    fetch_conferences,
    normalize_official_row,
    official_link,
    parse_conference_html,
    publication_time,
)
from app.taiwan.providers.taiwan_values import TAIPEI

RETRIEVED = datetime(2026, 10, 1, 10, 0, tzinfo=TAIPEI)


def material(**overrides):
    return {
        "出表日期": "1151001", "公司代號": "2330", "公司名稱": "測試公司",
        "主旨 ": "董事會通過取得設備", "發言日期": "1150930", "發言時間": "60706",
        "符合條款": "第20款", "事實發生日": "1150929", "說明": "測試公告說明", **overrides,
    }


def transfer(**overrides):
    return {
        "出表日期": "1151001", "公司代號": "2330", "公司名稱": "測試公司", "姓名": "測試申報人",
        "申報人身分": "董事", "預定轉讓方式及股數-轉讓方式": "贈與",
        "預定轉讓總股數-自有持股": "1,000", "預定轉讓總股數-保留運用決定權信託股數": "0",
        "有效轉讓期間": "1151001~1151003", **overrides,
    }


def conference_html(*, rows=None, raw_date="115/10/02", clock="14:00", slide=True):
    headers = "".join(f"<th><b>{label}</b></th>" for label in CONFERENCE_HEADERS)
    filename = "233020261002M001.pdf"
    link = f"<a href='#' onclick='document.fm_fileDownload.fileName.value=\"{filename}\";document.fm_fileDownload.submit();'>{filename}</a>" if slide else "內容檔案於當日會後公告於公開資訊觀測站"
    values = ["2330", "測試公司", raw_date, clock, "線上", "正式法說會內容 & 說明", link, "", "", "", "無", ""]
    data = "<tr data-type='body'>" + "".join(f"<td>{v if i == 6 else escape(v)}</td>" for i, v in enumerate(values)) + "</tr>"
    return ("<form id='fm_fileDownload' method='post' action='/server-java/FileDownLoad'>"
            "<input name='step' value='9'><input name='filePath' value='/home/html/nas/STR/'>"
            "<input name='fileName' value=''><input name='functionName' value='t100sb02_1'></form>"
            f"<table id='myTable'><thead><tr>{headers}</tr></thead>{data if rows is None else rows}</table>")


@pytest.fixture(autouse=True)
def provider_clock(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return RETRIEVED
    monkeypatch.setattr(provider, "datetime", FrozenDatetime)



def normalized_material(**overrides):
    return normalize_official_row(material(**overrides), source="mops:material:TWSE", exchange="TWSE", event_type="material_information", retrieved=RETRIEVED)


def normalized_transfer(**overrides):
    return normalize_official_row(transfer(**overrides), source="mops:transfer:TWSE", exchange="TWSE", event_type="insider_transfer_declaration", retrieved=RETRIEVED)


@pytest.mark.parametrize("topic,label_clauses", list(TOPICS.items()))
def test_twelve_deterministic_clause_topics(topic, label_clauses):
    label, clauses = label_clauses
    assert len(TOPICS) == 12
    for clause in clauses:
        assert classify_material_clause(f"第{clause}款") == (topic, label)
    # Third-party AI/category fields cannot affect the deterministic result.
    result = normalized_material(category="AI recommendation", important=True)
    assert result["details"]["topic"] == "assets"


@pytest.mark.parametrize("clause", ["第53款", "第999款", "", "AI：資產", "第20款、第22款"])
def test_unknown_or_ambiguous_clause_is_not_guessed(clause):
    assert classify_material_clause(clause) == ("other", "其他／未分類")


def test_official_numeric_clock_and_tpex_field_names():
    result = normalized_material()
    assert result["published_at"] == "2026-09-30T06:07:06+08:00"
    assert result["available_at"] == result["published_at"]
    assert result["event_date"] != result["details"]["report_date"]
    assert result["status"] == "available"
    row = material()
    row["SecuritiesCompanyCode"] = row.pop("公司代號")
    row["CompanyName"] = row.pop("公司名稱")
    row["Date"] = row.pop("出表日期")
    parsed = normalize_official_row(row, source="mops:material:TPEX", exchange="TPEX", event_type="material_information", retrieved=RETRIEVED)
    assert parsed["symbol"] == "2330.TPEX"
    assert parsed["published_at"] == result["published_at"]


@pytest.mark.parametrize("clock", ["", "24:00:00", "999999", "14:00", "no time"])
def test_missing_or_invalid_publication_is_insufficient(clock):
    result = normalized_material(發言時間=clock)
    assert result["available_at"] is None
    assert result["published_at"] is None
    assert result["status"] == "data_insufficient"
    assert publication_time("1151002", "12:00:00", RETRIEVED) is None


def test_transfer_is_a_declaration_with_unknown_publication():
    result = normalized_transfer()
    assert result["available_at"] is None and result["published_at"] is None
    assert result["status"] == "data_insufficient"
    assert result["details"]["planned_shares"] == 1000
    assert result["details"]["planned_trust_shares"] == 0
    assert result["details"]["is_actual_transaction"] is False
    assert result["details"]["event_date_kind"] == "report_snapshot_date"
    assert "不代表實際成交或已賣出" in result["summary"]
    assert normalized_transfer(出表日期="1151002")["id"] == result["id"]
    assert normalized_transfer(**{"預定轉讓總股數-自有持股": ""})["details"]["planned_shares"] is None


def test_conference_schedule_is_not_publication_and_preserves_official_post_links():
    result = parse_conference_html(conference_html(raw_date="115/10/02 至 115/10/03"), exchange="TWSE", year=2026, retrieved=RETRIEVED)[0]
    assert result["status"] == "data_insufficient"
    assert result["published_at"] is None and result["available_at"] is None
    assert result["details"]["scheduled_time"] == "14:00"
    assert result["details"]["scheduled_end_date"] == "2026-10-03"
    assert result["details"]["scheduled_date_display"] == "115/10/02 至 115/10/03"
    assert result["summary"] == "正式法說會內容 & 說明"
    link = result["details"]["presentation_links"][0]
    assert link["url"] == "https://mopsov.twse.com.tw/server-java/FileDownLoad"
    assert link["method"] == "POST"
    assert link["parameters"] == {"step": "9", "filePath": "/home/html/nas/STR/", "fileName": "233020261002M001.pdf", "functionName": "t100sb02_1"}
    assert result["source"] == CONFERENCE_SOURCE and result["source_url"] == CONFERENCE_URL
    assert result["details"]["provider"] == "MOPS"
    for url in ["javascript:alert(1)", "http://mops.twse.com.tw/file", "https://mops.twse.com.tw.evil.example/x", "https://user:pass@mops.twse.com.tw/x"]:
        assert official_link(url) is None


def official_client(html=None, failed_board=None):
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) == CONFERENCE_QUERY_URL and request.method == "POST"
        parameters = parse_qs(request.content.decode(), keep_blank_values=True)
        if parameters["TYPEK"] == [failed_board]:
            return httpx.Response(403, text="blocked")
        return httpx.Response(200, headers={"content-type": "text/html; charset=UTF-8"}, text=conference_html() if html is None else html)
    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def test_transport_only_queries_official_boards_and_never_downloads_slides():
    client, requests = official_client()
    with client:
        events, status = fetch_conferences(client=client)
    assert len(events) == 2 and status == "available"
    assert {e["symbol"] for e in events} == {"2330.TWSE", "2330.TPEX"}
    assert len(requests) == 2
    for request in requests:
        parameters = parse_qs(request.content.decode(), keep_blank_values=True)
        assert parameters["year"] == ["115"] and parameters["month"] == [""]
        assert parameters["step"] == ["1"] and parameters["co_id"] == [""]
        assert request.headers["referer"] == CONFERENCE_URL
        assert "mcp-session-id" not in request.headers


def test_official_partial_board_outage_and_verified_empty_are_distinct():
    client, _ = official_client(failed_board="otc")
    with client:
        events, status = fetch_conferences(client=client)
    assert len(events) == 1 and status == "partial"
    client, _ = official_client(conference_html(rows=""))
    with client:
        assert fetch_conferences(client=client) == ([], "available")


@pytest.mark.parametrize("html", [
    "<html>blocked</html>", conference_html().replace("公司代號", "欄位改名"),
    conference_html().replace("/home/html/nas/STR/", "/unknown/"),
    conference_html().replace('document.fm_fileDownload.submit();', 'unexpected();'),
    conference_html().replace("</table>", ""),
    conference_html(raw_date="115/10/03 至 115/10/02"), conference_html(clock="25:00"),
    conference_html(raw_date="114/10/02"),
])
def test_unverified_official_html_fails_closed_without_fallback(html):
    client, requests = official_client(html)
    with client, pytest.raises(ValueError, match="Official MOPS conference source unavailable"):
        fetch_conferences(client=client)
    assert len(requests) == 2


@pytest.mark.parametrize("status,content_type,content", [
    (403, "text/html", b"blocked"), (302, "text/html", b"redirect"),
    (200, "application/json", b"{}"),
    (200, "text/html", b"x" * (provider.MAX_RESPONSE_BYTES + 1)),
], ids=["forbidden", "redirect", "not-html", "oversized"])
def test_official_http_content_and_size_fail_closed(status, content_type, content):
    def handler(request):
        assert str(request.url) == CONFERENCE_QUERY_URL
        return httpx.Response(status, headers={"content-type": content_type}, content=content)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(ValueError):
        fetch_conferences(client=client)


def test_conferences_cross_year_query_and_calendar_window(monkeypatch):
    class DecemberDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 15, 10, tzinfo=TAIPEI)
    monkeypatch.setattr(provider, "datetime", DecemberDatetime)
    client, requests = official_client(conference_html(rows=""))
    with client:
        assert fetch_conferences(client=client) == ([], "available")
    assert len(requests) == 4
    assert {parse_qs(r.content.decode())["year"][0] for r in requests} == {"115", "116"}


def test_missing_presentation_remains_empty_and_out_of_window_is_not_returned():
    result = parse_conference_html(conference_html(slide=False), exchange="TPEX", year=2026, retrieved=RETRIEVED)[0]
    assert result["details"]["presentation_links"] == []
    client, _ = official_client(conference_html(raw_date="115/01/02"))
    with client:
        assert fetch_conferences(client=client) == ([], "available")



def test_empty_official_feed_and_schema_failure_are_distinct(monkeypatch):
    monkeypatch.setattr(provider, "fetch_json", lambda *a, **k: [])
    assert provider.fetch_official_events("mops:material:TWSE") == []
    for payload in [{"error": "blocked"}, [{}], [None]]:
        monkeypatch.setattr(provider, "fetch_json", lambda *a, value=payload, **k: value)
        with pytest.raises(ValueError):
            provider.fetch_official_events("mops:material:TWSE")


@pytest.fixture
def service(tmp_path, monkeypatch):
    from app.taiwan import events_service as module
    monkeypatch.setattr(module, "_cache_path", lambda: tmp_path / "regulatory_events.json")
    monkeypatch.setattr(module, "taipei_now", lambda: RETRIEVED)
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(module, "fetch_official_events", lambda source: [normalized_material()] if source == "mops:material:TWSE" else [])
    monkeypatch.setattr(module, "fetch_conferences", lambda *a: ([], "available"))
    master = MagicMock()
    master.get_instrument.return_value = None
    result = TaiwanEventService(security_master=master)
    result.get_all_regulatory_and_official_events = MagicMock(return_value=[])
    return result


def test_cache_rollover_restart_and_source_outage_preserve_provenance(service, monkeypatch, tmp_path):
    from app.taiwan import events_service as module
    original = service.get_mops_events()
    assert len(original) == 1
    assert (tmp_path / "mops_events.json").exists()
    monkeypatch.setattr(module, "fetch_official_events", lambda source: [])
    rolled = service.get_mops_events(force_refresh=True)
    assert len(rolled) == 1 and rolled[0].freshness == "cached"
    assert rolled[0].retrieved_at == original[0].retrieved_at
    restarted = TaiwanEventService(security_master=MagicMock())
    assert restarted.get_mops_events()[0].id == original[0].id
    calls = []
    def failure(*args):
        calls.append(args)
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(module, "fetch_official_events", failure)
    monkeypatch.setattr(module, "fetch_conferences", failure)
    before = (tmp_path / "mops_events.json").read_bytes()
    stale = service.get_mops_events(force_refresh=True)
    assert stale[0].freshness == "stale"
    assert stale[0].published_at == original[0].published_at
    assert (tmp_path / "mops_events.json").read_bytes() == before
    attempted = len(calls)
    fallback = service.get_mops_events()
    assert fallback[0].freshness == "stale"
    assert fallback[0].retrieved_at == original[0].retrieved_at
    assert len(calls) == attempted


def test_all_source_failure_uses_memory_only_retry_backoff(service, monkeypatch, tmp_path):
    from app.taiwan import events_service as module
    clock = [100.0]
    calls = []
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    def failure(*args):
        calls.append(args)
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(module, "fetch_official_events", failure)
    monkeypatch.setattr(module, "fetch_conferences", failure)

    assert service.get_mops_events() == []
    source_count = len(module.OFFICIAL_FEEDS) + 1
    assert len(calls) == source_count
    assert not (tmp_path / "mops_events.json").exists()
    assert service._mops_retry_after == 100.0 + module.MOPS_RETRY_BACKOFF_SECONDS
    source_statuses = service.get_product_sources_status()[1]
    assert all(source_statuses[source] == "unavailable"
               for source in (*module.OFFICIAL_FEEDS, module.CONFERENCE_SOURCE))

    assert service.get_mops_events() == []
    assert len(calls) == source_count
    assert not (tmp_path / "mops_events.json").exists()


def test_mops_retry_after_deadline_and_force_refresh_bypass(service, monkeypatch):
    from app.taiwan import events_service as module
    clock = [1_000.0]
    calls = []
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    def failure(*args):
        calls.append(args)
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(module, "fetch_official_events", failure)
    monkeypatch.setattr(module, "fetch_conferences", failure)
    source_count = len(module.OFFICIAL_FEEDS) + 1

    assert service.get_mops_events() == []
    assert len(calls) == source_count
    clock[0] += module.MOPS_RETRY_BACKOFF_SECONDS - 0.001
    assert service.get_mops_events() == []
    assert len(calls) == source_count

    clock[0] += 0.001
    assert service.get_mops_events() == []
    assert len(calls) == source_count * 2
    assert service.get_mops_events(force_refresh=True) == []
    assert len(calls) == source_count * 3


def test_mops_is_product_opt_in_and_does_not_enter_historical_pit(service, monkeypatch):
    from app.taiwan import events_service as module
    assert service.get_events() == []
    assert service.get_events(symbol="233.TWSE", include_mops=True) == []
    assert len(service.get_events(symbol="2330.TWSE", include_mops=True)) == 1
    monkeypatch.setattr(module.CorporateActionStore, "read", lambda self: [])
    assert service.get_pit_events("2330.TWSE", RETRIEVED.date()) == []
    assert service.get_cached_regulatory_snapshot()[0] == []


def test_api_preserves_transfer_semantics_and_null_times(service, monkeypatch):
    from app.main import app
    from app.taiwan import events_service as module
    monkeypatch.setattr(module, "get_event_service", lambda: service)
    monkeypatch.setattr(module, "fetch_official_events", lambda source: [normalized_transfer()] if source == "mops:transfer:TWSE" else [])
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/taiwan/events?event_types=insider_transfer_declaration&symbol=2330.TWSE")
    assert response.status_code == 200
    result = response.json()
    event = result["events"][0]
    assert event["published_at"] is None and event["available_at"] is None
    assert event["details"]["is_actual_transaction"] is False
    assert result["status"] == "partial"
    assert result["sources_status"]["mops:transfer:TWSE"] == "data_insufficient"


def test_old_cached_events_remain_readable():
    raw = normalized_material()
    for field in ("published_at", "available_at", "status"):
        raw.pop(field)
    parsed = MarketEvent.model_validate(raw)
    assert parsed.available_at is None and parsed.status == "data_insufficient"


@pytest.mark.parametrize("storage", ["disk", "memory"])
def test_unapproved_cached_sources_cannot_return_or_be_resaved(service, monkeypatch, tmp_path, storage):
    from app.taiwan import events_service as module
    service.get_mops_events()
    path = tmp_path / "mops_events.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    retired = {**normalized_material(), "id": "retired", "event_type": "investor_conference", "source": "toalpha:mops:t100sb02_1"}
    snapshot["events"].append(retired)
    snapshot["sources_status"][retired["source"]] = "available"
    if storage == "disk":
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        service._mops_cache = None
    else:
        service._mops_cache = snapshot
    result = service.get_mops_events()
    assert [e.id for e in result] == [normalized_material()["id"]]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert retired["source"] not in saved["sources_status"]
    assert all(e["source"] != retired["source"] for e in saved["events"])
    assert retired["source"] not in service.get_product_sources_status()[1]
    # Even with all official sources failing, the retired evidence never returns.
    service._mops_cache = snapshot
    def failure(*args):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(module, "fetch_official_events", failure)
    monkeypatch.setattr(module, "fetch_conferences", failure)
    assert [e.id for e in service.get_mops_events()] == [normalized_material()["id"]]


def test_api_reports_official_conference_unavailable_without_fabricated_events(service, monkeypatch):
    from app.main import app
    from app.taiwan import events_service as module
    monkeypatch.setattr(module, "get_event_service", lambda: service)
    def unavailable():
        raise ValueError("official schema unverified")
    monkeypatch.setattr(module, "fetch_conferences", unavailable)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/taiwan/events?event_types=investor_conference").json()
    assert result["events"] == [] and result["status"] == "partial"
    assert result["sources_status"][CONFERENCE_SOURCE] == "unavailable"
