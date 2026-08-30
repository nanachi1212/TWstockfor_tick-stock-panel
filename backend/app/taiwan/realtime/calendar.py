"""Taiwan Market Calendar and Trading Session Utilities.

Governs operating hours for Taiwan Stock Exchange (TWSE) and Taipei Exchange (TPEx)
under Asia/Taipei timezone (UTC+8, no daylight saving time).

Operating Schedule:
  - 08:30 - 09:00: PRE_OPEN (Pre-market trial matching)
  - 09:00 - 13:30: OPEN (Continuous regular session, 270 minutes, no lunch break)
  - 13:30 - 14:30: POST_CLOSE (Post-market odd-lot & fixed-price trading)
  - All other weekday times: CLOSED
  - Weekends & Public Holidays: NON_TRADING_DAY
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone

from app.taiwan.realtime.models import MarketStatus

TAIPEI_TZ = timezone(timedelta(hours=8))

PRE_OPEN_START = dt_time(8, 30)
REGULAR_OPEN_START = dt_time(9, 0)
REGULAR_OPEN_END = dt_time(13, 30)
POST_CLOSE_END = dt_time(14, 30)


def taipei_now() -> datetime:
    """Current timestamp in Asia/Taipei timezone."""
    return datetime.now(TAIPEI_TZ)


def taipei_today() -> date:
    """Current date in Asia/Taipei timezone."""
    return datetime.now(TAIPEI_TZ).date()


def get_market_status(
    dt: datetime | None = None,
    holidays: set[date] | None = None,
) -> MarketStatus:
    """Determine Taiwan market operating status for a given datetime.

    Args:
        dt: Datetime to inspect (defaults to current taipei_now()).
        holidays: Optional set of known statutory market holidays.

    Returns:
        MarketStatus enum indicating current market session state.
    """
    now = dt if dt is not None else taipei_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    else:
        now = now.astimezone(TAIPEI_TZ)

    d = now.date()
    # 1. Weekends
    if d.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return MarketStatus.NON_TRADING_DAY

    # 2. Known Statutory Holidays
    if holidays and d in holidays:
        return MarketStatus.NON_TRADING_DAY

    # 3. Time session divisions
    t = now.time()
    if t < PRE_OPEN_START:
        return MarketStatus.CLOSED
    elif t < REGULAR_OPEN_START:
        return MarketStatus.PRE_OPEN
    elif t <= REGULAR_OPEN_END:
        return MarketStatus.OPEN
    elif t <= POST_CLOSE_END:
        return MarketStatus.POST_CLOSE
    else:
        return MarketStatus.CLOSED
