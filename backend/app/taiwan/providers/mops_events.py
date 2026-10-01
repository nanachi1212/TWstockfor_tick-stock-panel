"""MOPS event adapters, restricted to product evidence (never historical PIT).

Reuse the Taiwan HTTP throttle/date/quantity contracts and dividend classifier.
Official daily material/transfer feeds are authoritative; ToAlpha only relays
the conference dataset for which this product has no existing adapter.
"""
# ruff: noqa: RUF001 -- official Traditional Chinese labels.
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.taiwan.dividend_events import MOPS_EVIDENCE_URL, classify_dividend_event
from app.taiwan.providers.http import fetch_json, taiwan_client
from app.taiwan.providers.taiwan_values import TAIPEI, parse_integer, parse_taiwan_date

MOPS_EVENT_TYPES = {"material_information", "investor_conference", "insider_transfer_declaration"}
OFFICIAL_FEEDS = {
    "mops:material:TWSE": ("https://openapi.twse.com.tw/v1/opendata/t187ap04_L", "TWSE", "material_information"),
    "mops:material:TPEX": ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O", "TPEX", "material_information"),
    "mops:transfer:TWSE": ("https://openapi.twse.com.tw/v1/opendata/t187ap12_L", "TWSE", "insider_transfer_declaration"),
    "mops:transfer:TPEX": ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O", "TPEX", "insider_transfer_declaration"),
}
CONFERENCE_SOURCE = "toalpha:mops:t100sb02_1"
CONFERENCE_URL = "https://mops.twse.com.tw/mops/web/t100sb02_1"
TOALPHA_URL = "https://toalpha.tw/mcp/mops"
TRANSFER_URL = "https://mops.twse.com.tw/mops/web/t56sb21"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

# Verified common clauses of TWSE/TPEx art. 4 on 2026-10-01. Clauses outside
# this map remain 'other'; market-specific later clauses are not guessed.
TOPICS: dict[str, tuple[str, tuple[int, ...]]] = {
    "financial": ("財務與財報", (9, 13, 30, 31)),
    "dividend": ("股利決議", (14,)),
    "capital": ("增減資與併購", (4, 11, 16, 36, 38)),
    "assets": ("資產交易與投資", (15, 20, 24)),
    "business": ("營運與合作", (3, 10, 25)),
    "governance": ("公司治理與人事", (6, 7, 8, 17, 18, 21, 29, 34)),
    "guarantee": ("背書保證", (22,)),
    "lending": ("資金貸與", (23,)),
    "legal_credit": ("法律與信用風險", (1, 2, 5, 19, 27, 28)),
    "incident": ("重大事故與資安", (26,)),
    "treasury": ("庫藏股", (35,)),
    "conference": ("法人說明會", (12,)),
}
TOPIC_BY_CLAUSE = {clause: (topic, label) for topic, (label, clauses) in TOPICS.items() for clause in clauses}


def classify_material_clause(clause: str) -> tuple[str, str]:
    match = re.fullmatch(r"第\s*(\d+)\s*款", clause.strip())
    return TOPIC_BY_CLAUSE.get(int(match[1]), ("other", "其他／未分類")) if match else ("other", "其他／未分類")


def official_link(value: object) -> str | None:
    """Only expose returned official document URLs, never invent filenames."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return None
        if parsed.hostname not in {"mops.twse.com.tw", "mopsov.twse.com.tw", "doc.twse.com.tw"}:
            return None
        if parsed.port not in (None, 443):
            return None
        return value.strip()
    except ValueError:
        return None


def publication_time(raw_date: str, raw_time: str, retrieved: datetime) -> str | None:
    """Official speech date/time only. Report generation dates are never used."""
    try:
        clock = raw_time.strip()
        if re.fullmatch(r"\d{1,6}", clock):
            clock = clock.zfill(6)
            clock = f"{clock[:2]}:{clock[2:4]}:{clock[4:]}"
        parsed = datetime.combine(parse_taiwan_date(raw_date), datetime.strptime(clock, "%H:%M:%S").time(), TAIPEI)
        return parsed.isoformat() if parsed <= retrieved else None
    except ValueError:
        return None


def _identity(parts: list[object]) -> str:
    canonical = [" ".join(unicodedata.normalize("NFC", str(p)).split()) for p in parts]
    return "evt_mops_" + hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode()).hexdigest()[:24]


def normalize_official_row(
    row: dict[str, Any], *, source: str, exchange: str, event_type: str, retrieved: datetime,
) -> dict[str, Any]:
    fields = {k.strip(): v for k, v in row.items()}
    code = str(fields.get("公司代號") or fields.get("SecuritiesCompanyCode") or "").strip()
    name = str(fields.get("公司名稱") or fields.get("CompanyName") or "").strip()
    if not re.fullmatch(r"[0-9]{4}[0-9A-Z]{0,2}", code) or not name:
        raise ValueError("MOPS issuer identity missing")
    report_date = parse_taiwan_date(str(fields.get("出表日期") or fields.get("Date") or "")).isoformat()
    common: dict[str, Any] = {
        "symbol": f"{code}.{exchange}", "code": code, "name": name, "exchange": exchange,
        "event_type": event_type, "source": source, "retrieved_at": retrieved.isoformat(),
        "freshness": "fresh", "published_at": None, "available_at": None, "status": "data_insufficient",
    }
    if event_type == "material_information":
        title = str(fields.get("主旨") or "").strip()
        if not title or "發言日期" not in fields or "發言時間" not in fields or "符合條款" not in fields:
            raise ValueError("MOPS material-information schema changed")
        event_date = parse_taiwan_date(str(fields["發言日期"])).isoformat()
        published = publication_time(str(fields["發言日期"]), str(fields["發言時間"]), retrieved)
        topic, label = classify_material_clause(str(fields["符合條款"]))
        common.update(
            id=_identity([source, code, event_date, fields["發言時間"], title]),
            event_date=event_date, event_type_label="重大訊息", severity="info", title=title,
            summary=str(fields.get("說明") or "").strip()[:1200], source_url=MOPS_EVIDENCE_URL,
            published_at=published, available_at=published,
            status="available" if published else "data_insufficient",
            details={
                "clause": str(fields["符合條款"]), "topic": topic, "topic_label": label,
                "classification_source": "deterministic_clause", "classification_version": "mops-clause-v1",
                "dividend_lifecycle_type": classify_dividend_event(title),
                "fact_date": str(fields.get("事實發生日") or "") or None,
                "report_date": report_date, "event_date_kind": "official_speech_date",
                "publication_evidence_url": OFFICIAL_FEEDS[source][0],
            },
        )
    else:
        required = ("姓名", "預定轉讓方式及股數-轉讓方式", "預定轉讓總股數-自有持股", "有效轉讓期間")
        if any(key not in fields for key in required):
            raise ValueError("MOPS transfer-declaration schema changed")
        declarant = str(fields["姓名"]).strip()
        method = str(fields["預定轉讓方式及股數-轉讓方式"]).strip()
        period = str(fields["有效轉讓期間"]).strip()
        shares = parse_integer(fields["預定轉讓總股數-自有持股"])
        trust_shares = parse_integer(fields.get("預定轉讓總股數-保留運用決定權信託股數"))
        if not declarant or not method or not period:
            raise ValueError("MOPS declaration identity missing")
        common.update(
            id=_identity([source, code, declarant, method, period, shares, trust_shares]),
            event_date=report_date, event_type_label="內部人持股轉讓申報", severity="info",
            title=f"{declarant}持股轉讓事前申報（{method}）",
            summary=f"預定轉讓自有持股：{f'{shares:,} 股' if shares is not None else '未提供'}；有效期間：{period}。此為事前申報，不代表實際成交或已賣出。",
            source_url=TRANSFER_URL,
            details={
                "declarant": declarant, "role": fields.get("申報人身分") or fields.get("申請人身分"),
                "transfer_method": method, "planned_shares": shares, "planned_trust_shares": trust_shares,
                "valid_period": period, "is_actual_transaction": False, "record_kind": "pre_transfer_declaration",
                "report_date": report_date, "event_date_kind": "report_snapshot_date",
                "publication_reason": "官方日報未提供可確認的逐筆公告時間；出表日期僅為資料日期。",
            },
        )
    return common


def fetch_official_events(source: str) -> list[dict[str, Any]]:
    url, exchange, event_type = OFFICIAL_FEEDS[source]
    payload = fetch_json(url, timeout=8.0)
    if not isinstance(payload, list) or len(payload) > 20000:
        raise ValueError("MOPS daily feed schema changed")
    if any(not isinstance(row, dict) for row in payload):
        raise ValueError("MOPS daily feed row schema changed")
    retrieved = datetime.now(TAIPEI)
    return [normalize_official_row(row, source=source, exchange=exchange, event_type=event_type, retrieved=retrieved) for row in payload]


def _rpc(client: httpx.Client, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    with client.stream("POST", TOALPHA_URL, json=payload, headers=headers) as response:
        response.raise_for_status()
        if response.headers.get("mcp-session-id"):
            headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("ToAlpha response exceeds limit")
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8")
        if "id" not in payload:
            return {}
        if "text/event-stream" in response.headers.get("content-type", ""):
            messages = []
            for block in raw.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                if data:
                    messages.append(json.loads(data))
        else:
            messages = [json.loads(raw)]
        matches = [m for m in messages if isinstance(m, dict) and m.get("id") == payload["id"]]
        if len(matches) != 1 or "error" in matches[0] or not isinstance(matches[0].get("result"), dict):
            raise ValueError("ToAlpha RPC failed")
        return dict(matches[0]["result"])


def fetch_conferences(
    resolve: Callable[[str], tuple[str, str, str, str, bool]],
    *, client: httpx.Client | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Port the existing mystocktracer bounded MCP transport; one allowed tool."""
    owned = client is None
    client = client or taiwan_client(timeout=8.0)
    headers = {"Accept": "application/json, text/event-stream"}
    try:
        initialized = _rpc(client, headers, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "twstock-events", "version": "1"},
        }})
        # Streamable HTTP permits stateless servers with no session header.
        if initialized.get("protocolVersion") != "2025-03-26":
            raise ValueError("ToAlpha protocol version unsupported")
        headers["MCP-Protocol-Version"] = "2025-03-26"
        _rpc(client, headers, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        result = _rpc(client, headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "investor_conferences", "arguments": {"days_ahead": 60, "limit": 100},
        }})
        content = result.get("content")
        if result.get("isError") or not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
            raise ValueError("ToAlpha conference tool failed")
        payload = json.loads(content[0]["text"])
        rows = payload.get("rows")
        count = payload.get("count")
        if not isinstance(rows, list) or len(rows) > 100 or not isinstance(count, int) or count < len(rows):
            raise ValueError("ToAlpha conference schema changed")
        retrieved = datetime.now(TAIPEI)
        events = [normalize_conference(row, resolve=resolve, retrieved=retrieved) for row in rows]
        # Reaching the cap cannot prove completeness even if count equals limit.
        status = "partial" if count > len(rows) or len(rows) == 100 else "available"
        return events, status
    finally:
        if owned:
            client.close()


def normalize_conference(
    row: Any, *, resolve: Callable[[str], tuple[str, str, str, str, bool]], retrieved: datetime,
) -> dict[str, Any]:
    if not isinstance(row, dict) or not all(k in row for k in ("id", "date", "time", "place", "summary", "slide_zh", "slide_en")):
        raise ValueError("ToAlpha conference row schema changed")
    code = str(row["id"]).strip()
    if not re.fullmatch(r"[0-9]{4}[0-9A-Z]{0,2}", code):
        raise ValueError("ToAlpha conference issuer missing")
    symbol, clean_code, name, exchange, resolvable = resolve(code)
    event_date = parse_taiwan_date(str(row["date"])).isoformat()
    clock = str(row["time"] or "").strip()
    if clock and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("ToAlpha conference time invalid")
    slides = [{"label": label, "url": url} for label, key in (("中文簡報", "slide_zh"), ("英文簡報", "slide_en")) if (url := official_link(row[key]))]
    return {
        "id": _identity([CONFERENCE_SOURCE, code, event_date, clock, row["place"]]),
        "symbol": symbol, "code": clean_code, "name": name if resolvable else str(row.get("name") or code),
        "exchange": exchange, "is_resolvable": resolvable,
        "event_type": "investor_conference", "event_type_label": "法人說明會", "severity": "info",
        "event_date": event_date, "title": f"法人說明會 {clock}".strip(), "summary": str(row["summary"])[:1200],
        "source": CONFERENCE_SOURCE, "source_url": CONFERENCE_URL, "retrieved_at": retrieved.isoformat(),
        "freshness": "fresh", "published_at": None, "available_at": None, "status": "data_insufficient",
        "details": {
            "event_date_kind": "conference_date", "scheduled_time": clock or None, "place": str(row["place"]),
            "invited": row.get("invited"), "presentation_links": slides,
            "provider": "ToAlpha", "upstream_source": "MOPS:t100sb02_1", "provider_url": "https://toalpha.tw/conference",
            "publication_reason": "法說會日期時間是舉行時間，來源未提供可確認的公告時間。",
        },
    }
