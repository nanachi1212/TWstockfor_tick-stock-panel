"""Point-in-Time (PIT) and Announcement Availability Cutoff for Taiwan Fundamental and Chips Data.

Guarantees 0 look-ahead bias when evaluating historical stock research context,
AI research, and screener analysis.

Taiwan Market Availability Semantics:
1. TaiwanStockMonthRevenue:
   - Monthly revenues are legally required to be published by the 10th of the following month.
   - For example, July revenue (2026-07) is legally available on 2026-08-10.
   - If an explicit announcement_date/publish_date field is present and valid, it is honored.
   - Otherwise, the conservative legal cutoff (10th of following month) is applied.
   - FinMind create_time is NEVER treated as historical announcement date.

2. TaiwanStockFinancialStatements:
   - Quarterly statements legal submission deadlines:
     * Q1 (03-31): May 15th (YYYY-05-15)
     * Q2 (06-30): August 14th (YYYY-08-14)
     * Q3 (09-30): November 14th (YYYY-11-14)
     * Q4 (12-31): March 31st of following year ((YYYY+1)-03-31)
   - If explicit announcement_date is provided, it is honored.
   - Otherwise, the statutory announcement deadline is used.
   - If a record cannot reliably determine historical availability, it is excluded
     under historical as-of context to avoid silent look-ahead leaks.

3. TaiwanStockShareholding & TaiwanStockSecuritiesLending:
   - Daily trading-day datasets announced after market close on trade date.
   - Strict daily cutoff: date <= target_date.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)


def resolve_as_of_date(as_of: date | str | None) -> date | None:
    """Normalize input as_of parameter to a datetime.date object."""
    if as_of is None:
        return None
    if isinstance(as_of, date):
        return as_of
    if isinstance(as_of, str):
        cleaned = as_of.strip()
        if not cleaned:
            return None
        # Handle ISO format with potential time component
        if "T" in cleaned:
            cleaned = cleaned.split("T")[0]
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d").date()
        except Exception:
            logger.warning("Failed to parse as_of date string: %s", as_of)
            return None
    return None


def get_revenue_announcement_date(row: dict[str, Any]) -> date | None:
    """Determine the earliest date when a monthly revenue record became available to the market."""
    # 1. Explicit announcement / publish date if available
    for key in ("announcement_date", "publish_date", "release_date"):
        val = row.get(key)
        if val:
            parsed = resolve_as_of_date(str(val))
            if parsed:
                return parsed

    # 2. Derive statutory availability date from revenue_year & revenue_month
    ry = row.get("revenue_year")
    rm = row.get("revenue_month")
    if ry is not None and rm is not None:
        try:
            y = int(ry)
            m = int(rm)
            if 1 <= m <= 12 and 1990 <= y <= 2100:
                target_y = y if m < 12 else y + 1
                target_m = m + 1 if m < 12 else 1
                return date(target_y, target_m, 10)
        except Exception:
            pass

    # 3. Fallback: Parse date field (typically YYYY-MM-01 or YYYY-MM-10)
    raw_date = row.get("date")
    if raw_date:
        d = resolve_as_of_date(str(raw_date))
        if d:
            # If the record already carries day >= 10, it may be the publication date
            if d.day >= 10:
                return d
            # If marked on the 1st of month, it refers to the reporting period;
            # the availability is the 10th of the following month.
            target_y = d.year if d.month < 12 else d.year + 1
            target_m = d.month + 1 if d.month < 12 else 1
            return date(target_y, target_m, 10)

    return None


def get_financial_statement_announcement_date(row: dict[str, Any]) -> date | None:
    """Determine the earliest statutory date when a financial statement record became available."""
    # 1. Explicit announcement / publish date
    for key in ("announcement_date", "publish_date", "release_date"):
        val = row.get(key)
        if val:
            parsed = resolve_as_of_date(str(val))
            if parsed:
                return parsed

    # 2. Parse quarterly period end date
    raw_date = row.get("date")
    if not raw_date:
        return None

    d = resolve_as_of_date(str(raw_date))
    if not d:
        return None

    y = d.year
    m = d.month
    day = d.day

    # Standard Taiwan quarterly financial statement statutory filing deadlines:
    # Q1 ends 03-31 -> May 15
    # Q2 ends 06-30 -> August 14
    # Q3 ends 09-30 -> November 14
    # Q4 ends 12-31 -> March 31 of next year
    if m == 3 and day >= 28:
        return date(y, 5, 15)
    elif m == 6 and day >= 28:
        return date(y, 8, 14)
    elif m == 9 and day >= 28:
        return date(y, 11, 14)
    elif m == 12 and day >= 28:
        return date(y + 1, 3, 31)

    # If non-standard date, require at least 45 days after period end as conservative cutoff
    try:
        from datetime import timedelta
        return d + timedelta(days=45)
    except Exception:
        return None


def filter_month_revenue_as_of(
    rows: list[dict[str, Any]],
    as_of: date | str | None,
) -> list[dict[str, Any]]:
    """Filter monthly revenue rows to only those available at or before as_of date.
    
    If as_of is None, returns rows untouched (current-date analysis).
    """
    cutoff = resolve_as_of_date(as_of)
    if cutoff is None:
        return rows

    filtered: list[dict[str, Any]] = []
    for r in rows:
        # Physical record date must not exceed cutoff
        raw_d = resolve_as_of_date(r.get("date"))
        if raw_d and raw_d > cutoff:
            continue

        avail_d = get_revenue_announcement_date(r)
        if avail_d is None or avail_d > cutoff:
            # Not yet published at target cutoff date
            continue
        filtered.append(r)

    return filtered


def filter_financial_statements_as_of(
    rows: list[dict[str, Any]],
    as_of: date | str | None,
) -> list[dict[str, Any]]:
    """Filter quarterly statement rows to only those available at or before as_of date.
    
    If as_of is None, returns rows untouched (current-date analysis).
    """
    cutoff = resolve_as_of_date(as_of)
    if cutoff is None:
        return rows

    filtered: list[dict[str, Any]] = []
    for r in rows:
        raw_d = resolve_as_of_date(r.get("date"))
        if raw_d and raw_d > cutoff:
            continue

        avail_d = get_financial_statement_announcement_date(r)
        if avail_d is None or avail_d > cutoff:
            # Statement not yet filed at target cutoff date
            continue
        filtered.append(r)

    return filtered


def filter_daily_records_as_of(
    rows: list[dict[str, Any]],
    as_of: date | str | None,
    date_key: str = "date",
) -> list[dict[str, Any]]:
    """Filter daily market / chips rows (shareholding, lending) by date <= as_of.
    
    If as_of is None, returns rows untouched (current-date analysis).
    """
    cutoff = resolve_as_of_date(as_of)
    if cutoff is None:
        return rows

    cutoff_str = cutoff.strftime("%Y-%m-%d")
    filtered: list[dict[str, Any]] = []
    for r in rows:
        d_val = str(r.get(date_key) or "").strip()
        if d_val and d_val <= cutoff_str:
            filtered.append(r)

    return filtered
