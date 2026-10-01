# ruff: noqa: RUF001
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.taiwan.events_service import MarketEvent, TaiwanEventService
from app.taiwan.providers import mops_events as provider
from app.taiwan.providers.mops_events import (
    CONFERENCE_SOURCE,
    TOPICS,
    classify_material_clause,
    fetch_conferences,
    normalize_conference,
    normalize_official_row,
    official_link,
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


def resolve(code):
    return f"{code}.TWSE", code, "測試公司", "TWSE", True


def conference(**overrides):
    return {
        "id": "2330", "date": "2026-10-02", "time": "14:00", "place": "線上",
        "summary": "正式法說會內容", "slide_zh": "https://mopsov.twse.com.tw/server-java/FileDownLoad?fileName=test.pdf",
        "slide_en": None, "invited": False, **overrides,
    }


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


def test_conference_schedule_is_not_publication_and_only_official_links():
    result = normalize_conference(conference(slide_en="https://evil.example/mops.pdf"), resolve=resolve, retrieved=RETRIEVED)
    assert result["status"] == "data_insufficient"
    assert result["published_at"] is None and result["available_at"] is None
    assert result["details"]["scheduled_time"] == "14:00"
    assert len(result["details"]["presentation_links"]) == 1
    assert result["source"] == CONFERENCE_SOURCE
    for url in ["javascript:alert(1)", "http://mops.twse.com.tw/file", "https://mops.twse.com.tw.evil.example/x", "https://user:pass@mops.twse.com.tw/x"]:
        assert official_link(url) is None


def mcp_client(payload=None, error=False, stateless=False):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            return httpx.Response(200, headers={} if stateless else {"mcp-session-id": "test-session"}, json={"id": 1, "result": {"protocolVersion": "2025-03-26"}})
        result = {"isError": error, "content": [{"type": "text", "text": json.dumps(payload or {"rows": [conference()], "count": 1})}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=f"event: message\ndata: {json.dumps({'id': 2, 'result': result})}\n\n")
    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def test_mcp_transport_only_calls_conferences_and_never_downloads_slides():
    client, requests = mcp_client()
    with client:
        events, status = fetch_conferences(resolve, client=client)
    assert len(events) == 1 and status == "available"
    assert [r["method"] for r in requests] == ["initialize", "notifications/initialized", "tools/call"]
    assert requests[-1]["params"]["name"] == "investor_conferences"


def test_live_protocol_supports_stateless_session():
    client, _ = mcp_client(stateless=True)
    with client:
        assert len(fetch_conferences(resolve, client=client)[0]) == 1


@pytest.mark.parametrize("payload", [{"count": 1, "rows": []}, {"count": 100, "rows": [conference()] * 100}])
def test_conference_truncated_feed_is_partial(payload):
    client, _ = mcp_client(payload)
    with client:
        assert fetch_conferences(resolve, client=client)[1] == "partial"


def test_mcp_tool_error_and_changed_schema_fail_closed():
    client, _ = mcp_client(error=True)
    with client, pytest.raises(ValueError):
        fetch_conferences(resolve, client=client)
    client, _ = mcp_client({"count": 1, "rows": [{"date": "2026-10-01"}]})
    with client, pytest.raises(ValueError):
        fetch_conferences(resolve, client=client)


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
    def failure(*args):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(module, "fetch_official_events", failure)
    monkeypatch.setattr(module, "fetch_conferences", failure)
    before = (tmp_path / "mops_events.json").read_bytes()
    stale = service.get_mops_events(force_refresh=True)
    assert stale[0].freshness == "stale"
    assert stale[0].published_at == original[0].published_at
    assert (tmp_path / "mops_events.json").read_bytes() == before


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
