"""台股交易時段計時。"""
from __future__ import annotations

from datetime import datetime


def trading_minutes_per_session() -> float:
    """Derive duration from the authoritative Taiwan session without import cycles."""
    from app.taiwan.realtime.calendar import REGULAR_OPEN_END, REGULAR_OPEN_START

    return float((REGULAR_OPEN_END.hour - REGULAR_OPEN_START.hour) * 60
                 + REGULAR_OPEN_END.minute - REGULAR_OPEN_START.minute)


def trading_minutes_elapsed_from_dt(dt: datetime) -> float:
    """Clock-time progress through the regular Taiwan session (0..270 minutes).

    Naive input is interpreted as Taipei time for compatibility. Trading-day
    eligibility belongs to TaiwanTradingCalendar and the caller, not this clock.
    """
    from app.taiwan.realtime.calendar import REGULAR_OPEN_START, TAIPEI_TZ

    local = dt.replace(tzinfo=TAIPEI_TZ) if dt.tzinfo is None else dt.astimezone(TAIPEI_TZ)
    opening = local.replace(hour=REGULAR_OPEN_START.hour, minute=REGULAR_OPEN_START.minute,
                            second=0, microsecond=0)
    elapsed = (local - opening).total_seconds() / 60.0
    return min(trading_minutes_per_session(), max(0.0, elapsed))


def trading_minutes_elapsed() -> float:
    """Current Taiwan session progress, used only when a quote timestamp is absent."""
    from app.taiwan.realtime.calendar import taipei_now

    return trading_minutes_elapsed_from_dt(taipei_now())


def trading_minutes_elapsed_from_ts(ts_ms: int | float | None) -> float:
    """Read a millisecond quote timestamp; unavailable timestamps retain full-day semantics."""
    if not ts_ms:
        return trading_minutes_per_session()
    from app.taiwan.realtime.calendar import TAIPEI_TZ

    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=TAIPEI_TZ)
    except (OverflowError, ValueError, TypeError, OSError):
        return trading_minutes_per_session()
    return trading_minutes_elapsed_from_dt(dt)
