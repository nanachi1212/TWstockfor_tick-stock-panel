"""Taiwan Daily Incremental Update Service (Phase 6B/Orchestration).

Production-ready orchestrator for Taiwan market daily data maintenance:
  1. Daily OHLCV (TaiwanDailyStore)
  2. Institutional Flows (TaiwanInstitutionalStore)
  3. Margin Balances (TaiwanMarginStore)

Core Design Principles:
  - Resolution of target trading date(s) using TaiwanTradingCalendar and local data provenance.
  - Catch-up behavior across multiple missing trading dates.
  - Granularity and Cost Awareness:
      * Institutional & Margin: Market-wide official endpoints (2 HTTP calls per date each). Safe for auto-refresh.
      * Daily OHLCV: Per-symbol requests (~2,335 HTTP calls for full market). Kept in manual/safe mode by default
        to prevent provider rate limits or system saturation until full-market batch endpoint is available.
  - Partial failure semantics: Reports per-dataset status (success/failed/skipped) and overall_status
    ('success' | 'partial' | 'failed').
  - Bounded concurrency, idempotency, and 0 rewrite of existing partitions.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Literal
from pydantic import BaseModel, Field

from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_margin_refresh import (
    TaiwanInstitutionalRefreshService,
    TaiwanMarginRefreshService,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now, taipei_today

logger = logging.getLogger(__name__)

# Post-close cutoff when official post-market data is expected to be published
DEFAULT_POST_CLOSE_HOUR = 16
DEFAULT_POST_CLOSE_MINUTE = 0

UpdateStatus = Literal["success", "partial", "failed", "skipped"]


class DatasetRefreshStats(BaseModel):
    status: UpdateStatus = "skipped"
    dates_requested: int = 0
    dates_fetched: int = 0
    dates_skipped: int = 0
    failed_dates: list[dict[str, Any]] = Field(default_factory=list)
    rows_written: int = 0
    note: str | None = None


class FreshnessStatus(BaseModel):
    daily_as_of: str | None = None
    institutional_as_of: str | None = None
    margin_as_of: str | None = None
    target_latest_trading_date: str
    is_fully_current: bool = False


class TaiwanDailyUpdateResult(BaseModel):
    run_started_at: str
    run_finished_at: str
    target_start_date: str
    target_end_date: str
    target_latest_trading_date: str
    daily: DatasetRefreshStats
    institutional: DatasetRefreshStats
    margin: DatasetRefreshStats
    freshness: FreshnessStatus
    overall_status: UpdateStatus


def resolve_target_latest_trading_date(
    calendar: TaiwanTradingCalendar | None = None,
    as_of_dt: datetime | None = None,
    post_close_hour: int = DEFAULT_POST_CLOSE_HOUR,
    post_close_minute: int = DEFAULT_POST_CLOSE_MINUTE,
) -> date:
    """Resolve the latest trading date that should be available.

    Rules:
      - If today is a weekend or confirmed holiday, step back to the most recent potential trading day.
      - If today is a potential trading day:
          * If current time is AFTER post-market publication cutoff (e.g. >= 16:00 Asia/Taipei),
            target is today.
          * If current time is BEFORE cutoff, today's full-market daily/inst/margin data is not yet
            finalized/published by exchanges, so target is the PREVIOUS trading day.
      - Never fabricates unconfirmed future dates.
    """
    cal = calendar or TaiwanTradingCalendar()
    dt = as_of_dt or taipei_now()
    cur = dt.date()

    # If current day is Saturday, Sunday, or confirmed holiday, step back
    while cal.is_trading_day(cur) is False:
        cur -= timedelta(days=1)

    # If cur is today and we haven't reached market publication cutoff time, step back
    if cur == dt.date():
        cutoff = dt.replace(hour=post_close_hour, minute=post_close_minute, second=0, microsecond=0)
        if dt < cutoff:
            cur -= timedelta(days=1)
            while cal.is_trading_day(cur) is False:
                cur -= timedelta(days=1)

    return cur


def resolve_missing_date_range(
    earliest_available: date | None,
    target_latest: date,
    calendar: TaiwanTradingCalendar | None = None,
) -> tuple[date, date] | None:
    """Determine the start and end dates needed to catch up.

    If earliest_available is >= target_latest, no catch-up needed.
    Otherwise, returns (earliest_available + 1 day, target_latest).
    """
    if earliest_available is None:
        return target_latest, target_latest
    if earliest_available >= target_latest:
        return None

    cal = calendar or TaiwanTradingCalendar()
    # Find next candidate date
    cur = earliest_available + timedelta(days=1)
    while cur <= target_latest and cal.is_trading_day(cur) is False:
        cur += timedelta(days=1)

    if cur > target_latest:
        return None
    return cur, target_latest


class TaiwanDailyUpdateService:
    """Orchestrator for daily incremental updates of Taiwan market datasets."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        inst_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        daily_service: TaiwanDailyRefreshService | None = None,
        inst_service: TaiwanInstitutionalRefreshService | None = None,
        margin_service: TaiwanMarginRefreshService | None = None,
        calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.inst_store = inst_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()

        self.daily_service = daily_service or TaiwanDailyRefreshService(
            store=self.daily_store, calendar=self.calendar
        )
        self.inst_service = inst_service or TaiwanInstitutionalRefreshService(
            store=self.inst_store, calendar=self.calendar
        )
        self.margin_service = margin_service or TaiwanMarginRefreshService(
            store=self.margin_store, calendar=self.calendar
        )

    def get_freshness(self, target_date: date | None = None) -> FreshnessStatus:
        """Inspect and return current storage freshness across all 3 datasets."""
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        d_dates = self.daily_store.available_dates()
        i_dates = self.inst_store.available_dates()
        m_dates = self.margin_store.available_dates()

        d_as_of = max(d_dates) if d_dates else None
        i_as_of = max(i_dates) if i_dates else None
        m_as_of = max(m_dates) if m_dates else None

        is_current = (
            d_as_of is not None and d_as_of >= target
            and i_as_of is not None and i_as_of >= target
            and m_as_of is not None and m_as_of >= target
        )

        return FreshnessStatus(
            daily_as_of=str(d_as_of) if d_as_of else None,
            institutional_as_of=str(i_as_of) if i_as_of else None,
            margin_as_of=str(m_as_of) if m_as_of else None,
            target_latest_trading_date=str(target),
            is_fully_current=is_current,
        )

    def run_update(
        self,
        target_date: date | None = None,
        refresh_daily: bool = False,
        daily_symbols: list[str] | None = None,
        force: bool = False,
    ) -> TaiwanDailyUpdateResult:
        """Execute daily incremental refresh across datasets.

        Args:
            target_date: Override target trading date (defaults to resolved latest trading day).
            refresh_daily: Whether to trigger Daily OHLCV refresh. Defaults to False to avoid
                           invoking ~2,335 HTTP calls per symbol without explicit intent.
            daily_symbols: Subset of symbols to refresh for Daily OHLCV if enabled (None = full universe).
            force: Refetch even if dates already exist in stores.

        Returns:
            Structured TaiwanDailyUpdateResult.
        """
        run_start = taipei_now()
        target = target_date or resolve_target_latest_trading_date(self.calendar, as_of_dt=run_start)

        # 1. Determine date ranges per dataset
        d_dates = self.daily_store.available_dates()
        i_dates = self.inst_store.available_dates()
        m_dates = self.margin_store.available_dates()

        d_max = max(d_dates) if d_dates else None
        i_max = max(i_dates) if i_dates else None
        m_max = max(m_dates) if m_dates else None

        # Determine overall target range
        min_available = min(filter(None, [d_max, i_max, m_max]), default=target)
        overall_range = resolve_missing_date_range(min_available, target, self.calendar)
        start_bound = overall_range[0] if overall_range else target
        end_bound = target

        logger.info(
            "Starting TaiwanDailyUpdateService run: target=%s, start_bound=%s, daily_enabled=%s",
            target, start_bound, refresh_daily,
        )

        # 2. Daily OHLCV Refresh
        daily_stats = DatasetRefreshStats()
        if not refresh_daily:
            daily_stats.status = "skipped"
            daily_stats.note = (
                "Daily full-market refresh requires per-symbol requests (~2,335 calls). "
                "Skipped in automatic flow; invoke with refresh_daily=True or specific daily_symbols."
            )
        else:
            try:
                d_range = resolve_missing_date_range(d_max, target, self.calendar) if not force else (start_bound, end_bound)
                if not d_range and not force:
                    daily_stats.status = "success"
                    daily_stats.dates_skipped = 1
                    daily_stats.note = "Daily store already current"
                else:
                    d_start, d_end = d_range if d_range else (start_bound, end_bound)
                    res = self.daily_service.refresh_symbols(symbols=daily_symbols, start=d_start, end=d_end)
                    if "error" in res:
                        daily_stats.status = "failed"
                        daily_stats.failed_dates.append({"error": res["error"]})
                    else:
                        daily_stats.status = "success"
                        daily_stats.dates_fetched = 1 if res.get("symbols_fetched", 0) > 0 else 0
                        daily_stats.dates_skipped = 1 if res.get("symbols_fetched", 0) == 0 else 0
                        daily_stats.rows_written = res.get("rows_written", 0)
            except Exception as e:
                logger.exception("Daily refresh failed: %s", e)
                daily_stats.status = "failed"
                daily_stats.failed_dates.append({"error": str(e)})

        # 3. Institutional Refresh (TWSE + TPEx = 2 HTTP calls per date)
        inst_stats = DatasetRefreshStats()
        try:
            i_range = resolve_missing_date_range(i_max, target, self.calendar) if not force else (start_bound, end_bound)
            if not i_range and not force:
                inst_stats.status = "success"
                inst_stats.dates_skipped = 1
                inst_stats.note = "Institutional store already current"
            else:
                i_start, i_end = i_range if i_range else (start_bound, end_bound)
                res = self.inst_service.refresh_dates(i_start, i_end, force=force)
                inst_stats.dates_requested = res.get("dates_requested", 0)
                inst_stats.dates_fetched = res.get("dates_fetched", 0)
                inst_stats.dates_skipped = res.get("dates_skipped", 0)
                inst_stats.rows_written = res.get("total_rows_written", 0)
                inst_stats.failed_dates = res.get("failed_dates", [])
                if inst_stats.failed_dates:
                    inst_stats.status = "failed" if inst_stats.dates_fetched == 0 else "partial"
                else:
                    inst_stats.status = "success"
        except Exception as e:
            logger.exception("Institutional refresh failed: %s", e)
            inst_stats.status = "failed"
            inst_stats.failed_dates.append({"error": str(e)})

        # 4. Margin Refresh (TWSE + TPEx = 2 HTTP calls per date)
        margin_stats = DatasetRefreshStats()
        try:
            m_range = resolve_missing_date_range(m_max, target, self.calendar) if not force else (start_bound, end_bound)
            if not m_range and not force:
                margin_stats.status = "success"
                margin_stats.dates_skipped = 1
                margin_stats.note = "Margin store already current"
            else:
                m_start, m_end = m_range if m_range else (start_bound, end_bound)
                res = self.margin_service.refresh_dates(m_start, m_end, force=force)
                margin_stats.dates_requested = res.get("dates_requested", 0)
                margin_stats.dates_fetched = res.get("dates_fetched", 0)
                margin_stats.dates_skipped = res.get("dates_skipped", 0)
                margin_stats.rows_written = res.get("total_rows_written", 0)
                margin_stats.failed_dates = res.get("failed_dates", [])
                if margin_stats.failed_dates:
                    margin_stats.status = "failed" if margin_stats.dates_fetched == 0 else "partial"
                else:
                    margin_stats.status = "success"
        except Exception as e:
            logger.exception("Margin refresh failed: %s", e)
            margin_stats.status = "failed"
            margin_stats.failed_dates.append({"error": str(e)})

        # 5. Evaluate Freshness and Overall Status
        run_finish = taipei_now()
        freshness = self.get_freshness(target_date=target)

        executed_statuses = []
        if refresh_daily:
            executed_statuses.append(daily_stats.status)
        executed_statuses.extend([inst_stats.status, margin_stats.status])

        if all(s == "success" for s in executed_statuses):
            overall_status = "success"
        elif any(s == "failed" for s in executed_statuses):
            if any(s == "success" for s in executed_statuses):
                overall_status = "partial"
            else:
                overall_status = "failed"
        elif any(s == "partial" for s in executed_statuses):
            overall_status = "partial"
        else:
            overall_status = "success"

        result = TaiwanDailyUpdateResult(
            run_started_at=run_start.isoformat(),
            run_finished_at=run_finish.isoformat(),
            target_start_date=str(start_bound),
            target_end_date=str(end_bound),
            target_latest_trading_date=str(target),
            daily=daily_stats,
            institutional=inst_stats,
            margin=margin_stats,
            freshness=freshness,
            overall_status=overall_status,
        )

        logger.info(
            "TaiwanDailyUpdateService finished: overall_status=%s, daily=%s, inst=%s, margin=%s, is_fully_current=%s",
            overall_status, daily_stats.status, inst_stats.status, margin_stats.status, freshness.is_fully_current,
        )
        return result


def main():
    """Manual entry point for python -m app.taiwan.daily_update."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Taiwan Daily Incremental Update CLI")
    parser.add_argument("--status-only", action="store_true", help="Only display current data freshness")
    parser.add_argument("--refresh-daily", action="store_true", help="Enable Daily OHLCV refresh (per-symbol calls)")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to refresh for Daily OHLCV")
    parser.add_argument("--target-date", type=str, help="Target date YYYY-MM-DD override")
    parser.add_argument("--force", action="store_true", help="Force refetch existing dates")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    svc = TaiwanDailyUpdateService()
    t_date = date.fromisoformat(args.target_date) if args.target_date else None

    if args.status_only:
        freshness = svc.get_freshness(target_date=t_date)
        print(json.dumps(freshness.model_dump(), indent=2, ensure_ascii=False))
        return

    res = svc.run_update(
        target_date=t_date,
        refresh_daily=args.refresh_daily,
        daily_symbols=args.symbols,
        force=args.force,
    )
    print(json.dumps(res.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
