"""Official MOPS dividend lifecycle events with point-in-time semantics."""
from __future__ import annotations

# ruff: noqa: RUF001 -- official field names contain full-width punctuation.
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.taiwan.providers.taiwan_values import TAIPEI, parse_number, parse_taiwan_date

EVENT_TYPES = {
    "board_resolution",
    "shareholder_resolution",
    "ex_date_announcement",
    "basis_date_announcement",
    "payment_announcement",
    "paid",
}

MOPS_SEARCH_URL = "https://mops.twse.com.tw/mops/api/t05st01"
MOPS_DETAIL_URL = "https://mops.twse.com.tw/mops/api/t05st01_detail"
MOPS_EVIDENCE_URL = "https://mops.twse.com.tw/mops/web/t05st01"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime(value: str | datetime | None) -> datetime | None:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result is not None and result.tzinfo is None:
        raise ValueError("dividend event timestamps must be timezone-aware")
    return result


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


@dataclass(frozen=True)
class DividendLifecycleEvent:
    symbol: str
    event_type: str
    event_timestamp: datetime
    available_at: datetime
    cash_dividend: float | None
    stock_dividend: float | None
    ex_date: str | None
    basis_date: str | None
    record_date: str | None
    payment_date: str | None
    raw_subject: str
    raw_status: str | None
    normalized_status: str
    provider: str
    source: str
    source_url: str
    retrieved_at: datetime
    availability_policy: str
    availability_evidence_source: str
    availability_evidence_url: str
    availability_evidence_identifier: str
    availability_confidence: str
    revision_identity: str
    supersedes_revision: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"invalid dividend event type: {self.event_type}")
        for value in (self.event_timestamp, self.available_at, self.retrieved_at):
            _datetime(value)
        if self.available_at != self.event_timestamp:
            raise ValueError("exact MOPS events must use event_timestamp as available_at")
        if self.availability_policy != "exact_timestamp":
            raise ValueError("MOPS lifecycle event must use exact_timestamp availability")
        if not self.revision_identity or not self.availability_evidence_identifier:
            raise ValueError("stable official event identity is required")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("event_timestamp", "available_at", "retrieved_at"):
            result[name] = _iso(getattr(self, name))
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DividendLifecycleEvent:
        data = {field.name: raw.get(field.name) for field in fields(cls)}
        for name in ("event_timestamp", "available_at", "retrieved_at"):
            data[name] = _datetime(data[name])
        return cls(**data)


def get_dividend_events_as_of(
    events: Iterable[DividendLifecycleEvent],
    symbol: str,
    query_at: datetime,
) -> list[DividendLifecycleEvent]:
    """Return events visible strictly after their exact publication timestamp."""
    checked = _datetime(query_at)
    return sorted(
        (
            event
            for event in events
            if event.symbol == symbol and checked > event.available_at
        ),
        key=lambda event: (event.event_timestamp, event.revision_identity),
    )


class TaiwanDividendEventStore:
    """Small append-only JSONL store keyed by official material-event identity."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "taiwan" / "fundamentals" / "dividend_events.jsonl"

    def load(self) -> list[DividendLifecycleEvent]:
        if not self.path.exists():
            return []
        return [
            DividendLifecycleEvent.from_dict(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def save(self, events: Iterable[DividendLifecycleEvent]) -> int:
        merged = {event.revision_identity: event for event in self.load()}
        for event in events:
            merged.setdefault(event.revision_identity, event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
            for event in sorted(
                merged.values(),
                key=lambda item: (item.event_timestamp, item.revision_identity),
            )
        )
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        temporary.replace(self.path)
        return len(merged)

    def get_as_of(
        self,
        symbol: str,
        query_at: datetime,
    ) -> list[DividendLifecycleEvent]:
        return get_dividend_events_as_of(self.load(), symbol, query_at)


class MOPSDividendLifecycleProvider:
    """Read stable, timed dividend events from MOPS historical material information."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30.0)

    def events(
        self,
        symbol: str,
        exchange: str,
        start_date: date,
        end_date: date,
        *,
        security_type: str = "stock",
    ) -> list[DividendLifecycleEvent]:
        if security_type == "etf":
            return []
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        code = symbol.split(".")[0]
        retrieved = datetime.now(TAIPEI)
        events: list[DividendLifecycleEvent] = []
        for year, month, first_day, last_day in _month_ranges(start_date, end_date):
            response = self.client.post(
                MOPS_SEARCH_URL,
                json={
                    "companyId": code,
                    "year": str(year - 1911),
                    "month": str(month),
                    "firstDay": str(first_day),
                    "lastDay": str(last_day),
                },
                headers={"Referer": MOPS_EVIDENCE_URL},
            )
            response.raise_for_status()
            rows = _result_data(response.json(), "historical material information")
            for row in rows:
                if not isinstance(row, list) or len(row) < 6 or str(row[0]).strip() != code:
                    raise ValueError("MOPS historical material-information schema changed")
                detail = row[5]
                if not isinstance(detail, dict) or not isinstance(detail.get("parameters"), dict):
                    raise ValueError("MOPS material-information identity missing")
                parameters = detail["parameters"]
                event_type = classify_dividend_event(str(row[4]).strip())
                if event_type is None:
                    continue
                events.append(
                    self._detail_event(
                        symbol,
                        exchange,
                        event_type,
                        parameters,
                        retrieved,
                    )
                )
        return sorted(
            {event.revision_identity: event for event in events}.values(),
            key=lambda event: (event.event_timestamp, event.revision_identity),
        )

    def _detail_event(
        self,
        symbol: str,
        exchange: str,
        event_type: str,
        parameters: dict[str, Any],
        retrieved: datetime,
    ) -> DividendLifecycleEvent:
        response = self.client.post(
            MOPS_DETAIL_URL,
            json=parameters,
            headers={"Referer": MOPS_EVIDENCE_URL},
        )
        response.raise_for_status()
        rows = _result_data(response.json(), "material-information detail")
        if len(rows) != 1 or not isinstance(rows[0], list) or len(rows[0]) < 10:
            raise ValueError("MOPS material-information detail schema changed")
        row = rows[0]
        serial = str(parameters.get("serialNumber") or "").strip()
        issuer = str(parameters.get("companyId") or "").strip()
        enter_date = str(parameters.get("enterDate") or "").strip()
        market = str(parameters.get("marketKind") or "").strip()
        if not all((serial, issuer, enter_date, market)):
            raise ValueError("MOPS stable material-information identity incomplete")
        if issuer != symbol.split(".")[0]:
            raise ValueError("MOPS detail issuer mismatch")
        expected_market = "sii" if exchange == "TWSE" else "otc"
        if market != expected_market:
            raise ValueError("MOPS detail market mismatch")
        subject = str(row[6]).strip()
        if classify_dividend_event(subject) != event_type:
            raise ValueError("MOPS material-information subject changed")
        event_date = parse_taiwan_date(str(row[1]))
        try:
            event_time = datetime.strptime(str(row[2]).strip(), "%H:%M:%S").time()
        except ValueError as exc:
            raise ValueError(f"invalid MOPS event time: {row[2]!r}") from exc
        timestamp = datetime.combine(event_date, event_time, tzinfo=TAIPEI)
        details = str(row[9])
        cash, stock = _dividend_amounts(details)
        identity = f"{market}/{issuer}/{enter_date}/{serial}"
        return DividendLifecycleEvent(
            symbol=symbol,
            event_type=event_type,
            event_timestamp=timestamp,
            available_at=timestamp,
            cash_dividend=cash,
            stock_dividend=stock,
            ex_date=_labeled_date(details, "除權（息）交易日"),
            basis_date=_labeled_date(details, "除權（息）基準日"),
            record_date=_labeled_date(details, "最後過戶日"),
            payment_date=_labeled_date(details, "普通股現金股利發放日期"),
            raw_subject=subject,
            raw_status=None,
            normalized_status=(
                "board_approved" if event_type == "board_resolution" else "announced"
            ),
            provider="TWSE" if exchange == "TWSE" else "TPEx",
            source="mops:historical_material_information",
            source_url=MOPS_SEARCH_URL,
            retrieved_at=retrieved,
            availability_policy="exact_timestamp",
            availability_evidence_source="MOPS",
            availability_evidence_url=MOPS_DETAIL_URL,
            availability_evidence_identifier=identity,
            availability_confidence="verified",
            revision_identity=identity,
        )


def classify_dividend_event(subject: str) -> str | None:
    """Conservatively classify only explicit issuer-level dividend subjects."""
    normalized = re.sub(r"\s+", "", subject)
    if "代子公司" in normalized or "特別股" in normalized:
        return None
    if "董事會" in normalized and "股利分派" in normalized:
        return "board_resolution"
    if ("除息交易日" in normalized or "除權交易日" in normalized or "除權息交易日" in normalized):
        return "ex_date_announcement"
    if (
        ("除息" in normalized or "除權" in normalized or "現金股利" in normalized)
        and ("基準日" in normalized or "配發基準日" in normalized)
    ):
        return "basis_date_announcement"
    return None


def _result_data(payload: object, context: str) -> list[Any]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise ValueError(f"MOPS {context} request failed")
    result = payload.get("result")
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        raise ValueError(f"MOPS {context} schema changed")
    return data


def _month_ranges(start: date, end: date) -> list[tuple[int, int, int, int]]:
    result: list[tuple[int, int, int, int]] = []
    current = date(start.year, start.month, 1)
    while current <= end:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        first_day = start.day if current.year == start.year and current.month == start.month else 1
        month_end = (next_month - date.resolution).day
        last_day = end.day if current.year == end.year and current.month == end.month else month_end
        result.append((current.year, current.month, first_day, last_day))
        current = next_month
    return result


def _field(details: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\r\n]+)", details)
    return _optional_text(match.group(1)) if match else None


def _labeled_date(details: str, label: str) -> str | None:
    raw = _field(details, label)
    if raw is None or raw in {"不適用", "NA", "N/A", "-", "--"}:
        return None
    return parse_taiwan_date(raw).isoformat()


def _dividend_amounts(details: str) -> tuple[float | None, float | None]:
    cash_labels = (
        "盈餘分配之現金股利(元/股)",
        "法定盈餘公積發放之現金(元/股)",
        "資本公積發放之現金(元/股)",
    )
    stock_labels = (
        "盈餘轉增資配股(元/股)",
        "法定盈餘公積轉增資配股(元/股)",
        "資本公積轉增資配股(元/股)",
    )

    def total(labels: tuple[str, ...]) -> float | None:
        values = [parse_number(_field(details, label)) for label in labels]
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    return total(cash_labels), total(stock_labels)
