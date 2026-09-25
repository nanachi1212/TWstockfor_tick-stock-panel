"""Official corporate-action transport/parser; arithmetic is in the domain layer."""
# ruff: noqa: RUF001 -- Official response text and units must remain exact.
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.taiwan.corporate_actions import (
    SOURCE_EXCHANGE,
    CorporateActionEvent,
    derive_factor,
    event_market_open,
    resolve_event_conflicts,
)
from app.taiwan.providers.http import DEFAULT_USER_AGENT, taiwan_client
from app.taiwan.providers.taiwan_values import TAIPEI, parse_taiwan_date

SOURCE_URLS = {
    "TWT49U": "https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
    "TWTAUU": "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU",
    "TWTB8U": "https://www.twse.com.tw/rwd/zh/change/TWTB8U",
    "exDailyQ": "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ",
    "revivt": "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt",
}
DETAIL_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail"
_FIELDS = {
    "TWT49U": ("資料日期", "股票代號", "除權息前收盤價", "減除股利參考價"),
    "exDailyQ": ("除權息日期", "代號", "除權息前收盤價", "減除股利參考價"),
    "TWTAUU": ("恢復買賣日期", "股票代號", "停止買賣前收盤價格", "恢復買賣參考價"),
    "TWTB8U": ("恢復買賣日期", "股票代號", "停止買賣前收盤價格", "恢復買賣參考價"),
    "revivt": ("恢復買賣日期", "股票代號", "最後交易日之收盤價格", "減資恢復買賣開始日參考價格"),
}
_CASH_DETAIL = "(每股配發現金股利)除息"
_FREE_DETAIL = "A. 按普通股股東持股比例每千股無償配股"


class CorporateActionSourceError(ValueError):
    """No safe event identity/schema exists; reject the entire source batch."""

    status = "provider_error"


def _rows(payload: dict[str, Any], required: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise CorporateActionSourceError("corporate-action payload is not an object")
    stat = str(payload.get("stat", "OK")).strip()
    if stat.upper() != "OK":
        if stat == "很抱歉，沒有符合條件的資料!":
            return []
        raise CorporateActionSourceError("official corporate-action provider error")
    tables = payload.get("tables", [payload])
    if not isinstance(tables, list):
        raise CorporateActionSourceError("corporate-action tables schema mismatch")
    matches = [t for t in tables if isinstance(t, dict)
               and set(required).issubset(t.get("fields") or [])]
    if len(matches) != 1:
        raise CorporateActionSourceError("corporate-action fields schema mismatch")
    table = matches[0]
    fields = table["fields"]
    if len(fields) != len(set(fields)) or not isinstance(table.get("data"), list):
        raise CorporateActionSourceError("ambiguous fields or missing data array")
    result = []
    for raw in table["data"]:
        if not isinstance(raw, list) or len(raw) != len(fields):
            raise CorporateActionSourceError("corporate-action row schema mismatch")
        result.append(dict(zip(fields, raw, strict=True)))
    return result


def _optional_number(raw: object) -> float | None:
    try:
        number = Decimal(str(raw).strip().replace(",", ""))
        return float(number) if number.is_finite() else None
    except InvalidOperation:
        return None


def parse_detail(payload: dict[str, Any], code: str) -> dict[str, Any]:
    """Strip only the official cash-per-share and shares-per-thousand unit suffixes."""
    rows = _rows(payload, ("股票代號", _CASH_DETAIL, _FREE_DETAIL))
    if len(rows) != 1 or str(rows[0]["股票代號"]).strip() != code:
        raise CorporateActionSourceError("duplicate/mismatched TWSE detail identity")
    row = rows[0]
    parsed = {}
    for key, unit in ((_CASH_DETAIL, "元／股"), (_FREE_DETAIL, "股")):
        raw = str(row[key]).strip()
        value = raw.removesuffix(unit).strip()
        if _optional_number(value) is None:
            raise CorporateActionSourceError("missing/malformed TWSE detail amount")
        parsed[key] = value
    return {"detail": parsed, "detail_raw": row}


def parse_corporate_actions(
    payload: dict[str, Any], *, source: str, source_url: str,
    retrieved_at: datetime,
) -> tuple[CorporateActionEvent, ...]:
    """Normalize official fields only; factors remain None until derive_factor."""
    if source not in _FIELDS:
        raise CorporateActionSourceError("unsupported corporate-action source")
    day_key, code_key, previous_key, reference_key = _FIELDS[source]
    rows = _rows(payload, _FIELDS[source])
    events = []
    for row in rows:
        try:
            # TWT49U uses 114年01月06日; the other tables use slash/compact ROC.
            day_text = re.sub(r"年|月", "/", str(row[day_key])).removesuffix("日")
            day = parse_taiwan_date(day_text)
            code = str(row[code_key]).strip()
            if not code:
                raise ValueError("empty security code")
        except ValueError as exc:
            raise CorporateActionSourceError("corporate-action identity schema mismatch") from exc
        kind = {"TWTAUU": "capital_reduction", "revivt": "capital_reduction",
                "TWTB8U": "par_change"}.get(source)
        if kind is None:
            kind = {"息": "cash_dividend", "權": "stock_dividend", "權息": "stock_dividend",
                    "除息": "cash_dividend", "除權": "stock_dividend", "除權息": "stock_dividend"}.get(
                str(row.get("權/息", "")).strip(), "unsupported")
        exchange = SOURCE_EXCHANGE[source]
        event = CorporateActionEvent(
            symbol=f"{code}.{exchange}", exchange=exchange, effective_date=day,
            effective_at=event_market_open(day), event_type=kind,
            previous_close=_optional_number(row[previous_key]),
            reference_price=_optional_number(row[reference_key]), factor=None,
            cash_dividend=None, free_share_ratio=None, reduction_ratio=None,
            source=source, source_url=source_url, retrieved_at=retrieved_at,
            raw_fields=json.dumps(row, ensure_ascii=False, sort_keys=True),
        )
        events.append(event)
    return resolve_event_conflicts(events)


class CorporateActionProvider:
    """Bounded date-range fetch, using the existing namespaced rate limiter.

    No automatic historical job and no changes to product snapshot ingestion.
    Only TWSE events whose two official references differ need a detail request.
    """

    def __init__(self, client: Any = None) -> None:
        self.client = client or taiwan_client(timeout=60, headers={"User-Agent": DEFAULT_USER_AGENT})
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _get(self, url: str, attempts: int = 3) -> dict[str, Any]:
        """GET + parse. TPEx drops large bodies mid-stream, so transport errors and
        truncated JSON are retried a bounded number of times (each attempt takes its
        own rate-limit slot); HTTP status errors are not."""
        last: Exception | None = None
        for _ in range(attempts):
            try:
                response = self.client.get(url)
                response.raise_for_status()
                return json.loads(response.content.decode("utf-8-sig"))
            except (httpx.TransportError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                last = exc
            except Exception as exc:
                raise CorporateActionSourceError(
                    f"corporate-action fetch failed: {type(exc).__name__}") from exc
        raise CorporateActionSourceError(
            f"corporate-action fetch failed: {type(last).__name__}") from last

    def fetch(self, source: str, start: date, end: date) -> tuple[CorporateActionEvent, ...]:
        if source not in SOURCE_URLS or start > end:
            raise ValueError("invalid corporate-action source/range")
        fmt = "%Y%m%d" if SOURCE_EXCHANGE[source] == "TWSE" else "%Y/%m/%d"
        url = f"{SOURCE_URLS[source]}?startDate={start:{fmt}}&endDate={end:{fmt}}&response=json"
        events = parse_corporate_actions(self._get(url), source=source, source_url=url,
                                         retrieved_at=datetime.now(TAIPEI))
        output = []
        for event in events:
            if not start <= event.effective_date <= end:
                raise CorporateActionSourceError("provider ignored historical range")
            raw = json.loads(event.raw_fields)
            ex_ref = _optional_number(raw.get("除權息參考價"))
            if (source == "TWT49U" and not event.reason and ex_ref is not None
                    and event.reference_price is not None and ex_ref != event.reference_price):
                detail_url = (f"{DETAIL_URL}?STK_NO={event.symbol.split('.')[0]}"
                              f"&T1={event.effective_date:%Y%m%d}&response=json")
                try:
                    raw.update(parse_detail(self._get(detail_url), event.symbol.split(".")[0]))
                    raw["detail_source_url"] = detail_url
                    event = replace(event, raw_fields=json.dumps(raw, ensure_ascii=False, sort_keys=True))
                except CorporateActionSourceError:
                    event = replace(event, status="provider_error", reason="detail_provider_error")
            output.append(derive_factor(event))
        return resolve_event_conflicts(output)
