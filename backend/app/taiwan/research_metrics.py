"""Coverage contracts and conservative session windows for current market research."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.taiwan.observed_universe import ObservedUniverseStore, is_potential_market_session
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

ResearchStatus = Literal["available", "partial", "unavailable"]


class ResearchCoverage(BaseModel):
    expected_days: int
    coverage_days: int
    expected_observations: int
    observed_observations: int
    missing_dates: list[str] = Field(default_factory=list)


class ResearchMetric(BaseModel):
    value: float | None
    unit: str
    source: list[str]
    as_of: str | None
    date: str
    status: ResearchStatus
    coverage: ResearchCoverage


def research_sessions(
    target: date, count: int, known_dates: set[date],
    calendar: TaiwanTradingCalendar, evidence: ObservedUniverseStore,
) -> list[date]:
    """Never slide across a missing weekday; only confirmed closures are skipped.

    Persisted observations also retain exceptional Saturday sessions. Unresolved
    weekdays remain expected slots and therefore cannot fabricate full coverage.
    """
    sessions: list[date] = []
    cursor = target
    for _ in range(count * 5 + 30):
        if cursor in known_dates or is_potential_market_session(cursor, calendar, evidence):
            sessions.append(cursor)
        if len(sessions) == count:
            break
        cursor -= timedelta(days=1)
    return sorted(sessions)


def research_metric(
    value: float | None, unit: str, target: date, sessions: list[date],
    observed_dates: set[date], expected_symbols: int, observed_count: int,
    sources: list[str], *, complete: bool = True,
) -> ResearchMetric:
    expected = len(sessions) * expected_symbols
    status: ResearchStatus = "unavailable" if value is None else (
        "available" if complete and observed_count == expected else "partial"
    )
    return ResearchMetric(
        value=value, unit=unit, source=sorted(set(sources)),
        as_of=str(max(observed_dates)) if observed_dates else None, date=str(target),
        status=status,
        coverage=ResearchCoverage(
            expected_days=len(sessions), coverage_days=len(observed_dates),
            expected_observations=expected, observed_observations=observed_count,
            missing_dates=[str(d) for d in sessions if d not in observed_dates],
        ),
    )
