"""Official monthly trading-day tables as evidence for empty census partitions.

An empty A2a partition only records that the official daily snapshot had no
rows.  It does not prove a closure (``observed_universe`` docs).  The exchanges
also publish a *monthly* table with one row per session that actually traded:

* TWSE ``FMTQIK`` (每日市場成交資訊)
* TPEx ``indexInfo/inx`` (櫃買指數月查詢)

A weekday that is absent from a valid month table, while a *later* session of
the same month is present, is an official statement that the market did not
trade that day (covers statutory holidays, Lunar New Year and typhoon closures,
which the annual TWSE holiday schedule only lists from 2021).

Guards, all fail-closed:

* the month table must be a valid, non-empty, same-month response;
* every observed census partition in that month must appear in the table -
  otherwise the whole month is left untouched and reported as a conflict;
* a date present in the table but empty in the census is a trading day with
  missing data, never a holiday;
* dates after the last published row of the month stay unresolved.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.providers.http import fetch_json

logger = logging.getLogger(__name__)

TWSE_MONTH_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
TPEX_MONTH_URL = "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx"

TWSE_EVIDENCE_SOURCE = "twse:FMTQIK:month_table_absence"
TPEX_EVIDENCE_SOURCE = "tpex:inx:month_table_absence"

JsonFetcher = Callable[[str], Any]


class MonthTableError(ValueError):
    """The official month table is missing, malformed or for another month."""


def _fetch(url: str) -> Any:
    return fetch_json(url, timeout=60.0, max_attempts=3)


def _parse_roc(text: str) -> date:
    year, month, day = (int(part) for part in str(text).strip().split("/"))
    return date(year + 1911, month, day)


def fetch_month_sessions(
    exchange: str, year: int, month: int, *, fetch: JsonFetcher = _fetch,
) -> frozenset[date]:
    """Return the sessions the exchange published for one month."""
    if exchange == "TWSE":
        payload = fetch(f"{TWSE_MONTH_URL}?date={year:04d}{month:02d}01&response=json")
        if not isinstance(payload, dict) or str(payload.get("stat", "")).strip() != "OK":
            raise MonthTableError("TWSE month table is not OK")
        if str(payload.get("date", ""))[:6] != f"{year:04d}{month:02d}":
            raise MonthTableError("TWSE month table date mismatch")
        fields = list(payload.get("fields") or [])
        if not fields or fields[0] != "日期":
            raise MonthTableError("TWSE month table schema changed")
        try:
            days = [_parse_roc(row[0]) for row in payload.get("data") or []]
        except (ValueError, IndexError, TypeError) as exc:
            raise MonthTableError("TWSE month table row is malformed") from exc
    elif exchange == "TPEX":
        payload = fetch(f"{TPEX_MONTH_URL}?date={year:04d}/{month:02d}/01&id=&response=json")
        if not isinstance(payload, dict) or str(payload.get("stat", "")).strip().lower() != "ok":
            raise MonthTableError("TPEx month table is not OK")
        if str(payload.get("date", ""))[:6] != f"{year:04d}{month:02d}":
            raise MonthTableError("TPEx month table date mismatch")
        tables = payload.get("tables") or []
        if len(tables) != 1 or list((tables[0].get("fields") or [])[:1]) != ["日期"]:
            raise MonthTableError("TPEx month table schema changed")
        try:
            days = [date(*(int(part) for part in str(row[0]).split("/")))
                    for row in tables[0].get("data") or []]
        except (ValueError, IndexError, TypeError) as exc:
            raise MonthTableError("TPEx month table row is malformed") from exc
    else:
        raise ValueError("unsupported exchange")
    if not days or len(set(days)) != len(days) or days != sorted(days):
        raise MonthTableError("month table has no unique ordered sessions")
    if any((day.year, day.month) != (year, month) for day in days):
        raise MonthTableError("month table contains a session outside its month")
    return frozenset(days)


def _months(start: date, end: date) -> list[tuple[int, int]]:
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def verify_empty_days(
    store: ObservedUniverseStore,
    exchange: str,
    *,
    start: date,
    end: date,
    fetch: JsonFetcher = _fetch,
    census_rows: Callable[[date], list[dict[str, Any]]] | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Reconcile the census with the official month tables.

    * unexplained-empty weekdays absent from a complete month table become
      confirmed non-trading days;
    * an empty weekday that the month table lists as a session is re-fetched, never
      treated as a holiday;
    * official sessions on a Saturday (make-up trading days, which the weekday
      candidate list never queries) are fetched through ``census_rows`` and
      stored as ordinary observed sessions when the official snapshot has rows.

    A month is complete once it is over; the current month is only trusted up to
    its last published session. With ``apply=False`` nothing is written.
    """
    source = TWSE_EVIDENCE_SOURCE if exchange == "TWSE" else TPEX_EVIDENCE_SOURCE
    statuses = {day: status for day, status in store.partition_statuses(exchange).items()
                if start <= day <= end}
    report: dict[str, Any] = {
        "exchange": exchange, "months_checked": 0, "observed_agree": 0,
        "confirmed_non_trading": [], "observed_conflicts": [],
        "empty_but_official_session": [], "empty_session_recovered": [],
        "after_last_published_session": [],
        "weekend_sessions_added": [], "weekend_sessions_missing": [],
        "weekend_sessions_missing_open": [], "month_errors": [],
    }
    for year, month in _months(start, end):
        in_month = {day: status for day, status in statuses.items()
                    if (day.year, day.month) == (year, month)}
        try:
            sessions = fetch_month_sessions(exchange, year, month, fetch=fetch)
        except Exception as exc:
            logger.warning("%s %04d-%02d month table unavailable: %s", exchange, year, month, exc)
            report["month_errors"].append(f"{year:04d}-{month:02d}:{type(exc).__name__}")
            continue
        report["months_checked"] += 1
        observed = [day for day, status in in_month.items() if status == "observed"]
        disagreement = [day for day in observed if day not in sessions]
        report["observed_agree"] += len(observed) - len(disagreement)
        if disagreement:
            report["observed_conflicts"].extend(day.isoformat() for day in disagreement)
            continue
        month_complete = (year, month) < (end.year, end.month)
        last_published = max(sessions)
        for day in sorted(d for d, status in in_month.items() if status == "empty_unknown"):
            if day in sessions:
                report["empty_but_official_session"].append(day.isoformat())
                rows = census_rows(day) if apply and census_rows is not None else []
                if rows:
                    store.write(exchange, day, rows)
                    report["empty_session_recovered"].append(day.isoformat())
            elif not month_complete and day > last_published:
                report["after_last_published_session"].append(day.isoformat())
            else:
                if apply:
                    store.write(exchange, day, [], confirmed_non_trading_source=source)
                report["confirmed_non_trading"].append(day.isoformat())
        for day in sorted(d for d in sessions if d.weekday() >= 5 and start <= d <= end):
            if store.has(exchange, day) and store.partition_status(exchange, day) != "empty_unknown":
                continue
            report["weekend_sessions_missing"].append(day.isoformat())
            if apply and census_rows is not None:
                rows = census_rows(day)
                # An empty answer is stored as an unresolved partition: the session is
                # official, so it stays in the readiness denominator until rows exist.
                store.write(exchange, day, rows)
                if rows:
                    report["weekend_sessions_added"].append(day.isoformat())
                else:
                    report["weekend_sessions_missing_open"].append(day.isoformat())
    clean = (
        not report["month_errors"] and not report["observed_conflicts"]
        and not report["weekend_sessions_missing_open"]
        and len(report["empty_but_official_session"]) == len(report["empty_session_recovered"])
    )
    if apply and census_rows is not None and clean:
        store.record_month_verification(exchange, start, end)
    return report
