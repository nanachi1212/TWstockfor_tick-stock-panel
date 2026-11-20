"""Strict Taiwan official-value and date normalization."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

# One Asia/Taipei definition for the whole backend (UTC+8, no daylight saving).
from app.taiwan.realtime.calendar import TAIPEI_TZ as TAIPEI

MISSING_VALUES = {"", "-", "--", "N/A"}


def parse_taiwan_date(raw: str) -> date:
    value = str(raw).strip().rstrip("*").strip()
    separated = re.fullmatch(r"([0-9]{2,4})([/.-])([0-9]{1,2})\2([0-9]{1,2})", value)
    if separated:
        year, _, month, day = separated.groups()
        compact = year + month.zfill(2) + day.zfill(2)
    else:
        compact = value
    if len(compact) == 7 and compact.isdigit():
        compact = f"{int(compact[:3]) + 1911:04d}{compact[3:]}"
    elif len(compact) == 6 and compact.isdigit():
        compact = f"{int(compact[:2]) + 1911:04d}{compact[2:]}"
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid Taiwan date: {raw!r}")
    try:
        return datetime.strptime(compact, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid Taiwan date: {raw!r}") from exc


def market_close(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(13, 30), tzinfo=TAIPEI)


class ReportDateMismatchError(ValueError):
    def __init__(self, dataset: str, reported: date, requested: date) -> None:
        self.reported = reported
        super().__init__(f"{dataset} response date {reported} disagrees with requested {requested}")


def require_report_date(payload: dict[str, Any], table: dict[str, Any], requested: date, dataset: str) -> date:
    """Require all date evidence in the official envelope and the selected data table to agree."""
    dates = []
    for section in (payload, table):
        for key in ("date", "reportDate"):
            value = section.get(key)
            if value not in (None, ""):
                dates.append(parse_taiwan_date(value))
        title = section.get("title")
        if isinstance(title, str):
            normalized = title.replace("年", "/").replace("月", "/")
            for value in re.findall(r"(?<![0-9])[0-9]{2,4}[/.-][0-9]{1,2}[/.-][0-9]{1,2}(?![0-9])", normalized):
                dates.append(parse_taiwan_date(value))
    if not dates:
        raise ValueError(f"{dataset} response has no trade date")
    if len(set(dates)) != 1:
        raise ValueError(f"{dataset} response date fields disagree: {dates}")
    if dates[0] != requested:
        raise ReportDateMismatchError(dataset, dates[0], requested)
    return dates[0]


def official_status(trade_date: date, today: date | None = None) -> str:
    current = today or datetime.now(TAIPEI).date()
    business_days = 0
    cursor = trade_date
    while cursor < current:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            business_days += 1
    return "stale" if business_days > 1 else "official"


def parse_number(raw: object) -> float | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if value in MISSING_VALUES:
        return None
    value = value.replace(",", "").replace("\uff0b", "+").replace("\u2212", "-")
    try:
        return float(Decimal(value))
    except InvalidOperation as exc:
        raise ValueError(f"malformed number: {raw!r}") from exc


def parse_integer(raw: object) -> int | None:
    """Parse an official integral quantity without conflating missing and zero."""
    value = parse_number(raw)
    if value is None:
        return None
    if not value.is_integer():
        raise ValueError(f"malformed integer: {raw!r}")
    return int(value)
