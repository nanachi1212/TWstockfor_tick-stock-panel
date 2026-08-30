"""Strict Taiwan official-value and date normalization."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
MISSING_VALUES = {"", "-", "--", "N/A"}


def parse_taiwan_date(raw: str) -> date:
    value = str(raw).strip()
    compact = value.replace("/", "")
    if len(compact) == 7 and compact.isdigit():
        compact = f"{int(compact[:3]) + 1911:04d}{compact[3:]}"
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid Taiwan date: {raw!r}")
    try:
        return datetime.strptime(compact, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid Taiwan date: {raw!r}") from exc


def market_close(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(13, 30), tzinfo=TAIPEI)


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
