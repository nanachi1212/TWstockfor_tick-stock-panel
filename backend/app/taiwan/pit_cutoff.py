"""Point-in-Time (PIT) Availability Filter for Taiwan Fundamental and Chips Data.

Phase 6G decision (2026-08-31, docs/taiwan-fundamentals-availability-phase-6g.md):

  TaiwanStockMonthRevenue and TaiwanStockFinancialStatements aggregate rows
  carry no stable record-level publication identity, exact first-announcement
  timestamp, or document/revision identifier that can be joined to aggregate
  values.  Filing deadlines are statutory obligations, not verified first-
  availability proofs; using them would allow unknown corrections and
  restatements to silently contaminate historical research contexts.

  Safety matrix outcome (from phase-6g doc):
    financial_statement   Historical PIT eligible: No
    monthly_revenue       Historical PIT eligible: No
    shareholding/lending  Historical PIT eligible: Yes (record date = trade date)

Historical-query contract:
  filter_month_revenue_as_of(rows, as_of)         when as_of is not None  -> []
  filter_financial_statements_as_of(rows, as_of)  when as_of is not None  -> []

Current-query contract (as_of is None):
  Both functions return rows unchanged for downstream latest-row selection.

Daily datasets (shareholding, lending):
  filter_daily_records_as_of: date <= as_of cutoff; unchanged by Phase 6G.
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
        if "T" in cleaned:
            cleaned = cleaned.split("T")[0]
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d").date()
        except Exception:
            logger.warning("Failed to parse as_of date string: %s", as_of)
            return None
    return None


def filter_month_revenue_as_of(
    rows: list[dict[str, Any]],
    as_of: date | str | None,
) -> list[dict[str, Any]]:
    """Return monthly revenue rows available for the given query context.

    Phase 6G: TaiwanStockMonthRevenue aggregate rows have no verified
    record-level publication timestamp or document identity.  Filing
    deadlines (10th of the following month) are not a reliable availability
    proof and may silently import corrections or restatements.

    - as_of is None  (current/latest analysis): all rows returned.
    - as_of is set   (historical analysis): returns [] (fail-closed;
      no verified availability evidence for any row).
    """
    if resolve_as_of_date(as_of) is None:
        # Current / latest analysis: pass all rows for downstream selection.
        return rows
    # Historical query: no verified availability evidence -> fail closed.
    return []


def filter_financial_statements_as_of(
    rows: list[dict[str, Any]],
    as_of: date | str | None,
) -> list[dict[str, Any]]:
    """Return financial statement rows available for the given query context.

    Phase 6G: TaiwanStockFinancialStatements aggregate rows carry no
    document identity, upload timestamp, or revision identifier that can
    establish point-in-time availability.  Quarterly filing deadlines
    (May 15 / Aug 14 / Nov 14 / Mar 31) are not verified first-availability
    and cannot prove which corrected/restated value was public at a past date.

    - as_of is None  (current/latest analysis): all rows returned.
    - as_of is set   (historical analysis): returns [] (fail-closed).
    """
    if resolve_as_of_date(as_of) is None:
        # Current / latest analysis: pass all rows for downstream selection.
        return rows
    # Historical query: no verified availability evidence -> fail closed.
    return []


def filter_daily_records_as_of(
    rows: list[dict[str, Any]],
    as_of: date | str | None,
    date_key: str = "date",
) -> list[dict[str, Any]]:
    """Filter daily market/chips rows (shareholding, lending) by date <= as_of.

    Daily datasets carry the actual trade date as the record date, which is a
    reliable availability signal.  Unchanged by Phase 6G decision.
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
