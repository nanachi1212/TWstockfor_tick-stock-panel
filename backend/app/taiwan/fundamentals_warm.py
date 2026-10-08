"""After-close pre-warm of the per-symbol fundamentals/chips cache the screener reads.

The screener never fetches at request time; without this job its fundamental and
chip filters only see stocks someone happened to open. FinMind is rate limited
(about 600 requests/hour with a token), so only liquid common stocks are warmed;
the service skips symbols whose cache is still valid, so a rerun resumes.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

# Same floor as the screener templates' volume_min (500 lots = 500,000 shares).
MIN_VOLUME_SHARES = 500_000


def liquid_symbols(min_volume: int = MIN_VOLUME_SHARES) -> list[str]:
    """Common stocks at or above the volume floor, largest turnover first."""
    from app.taiwan.screener import TaiwanScreenerService

    screener = TaiwanScreenerService()
    universe = screener._get_universe("ALL", "stock")
    if universe.is_empty():
        return []
    latest = screener.daily_store.read_latest_per_symbol(universe["symbol"].to_list())
    if latest.is_empty():
        return []
    return (latest.filter(pl.col("volume") >= min_volume)
            .sort(["amount", "symbol"], descending=[True, False])["symbol"].to_list())


def _lock_path():
    from app.config import settings

    return settings.data_dir / "taiwan" / "fundamentals_warm.lock"


def warm_fundamentals(symbols: list[str] | None = None) -> dict[str, Any]:
    """Run one warm; a manual run and the 17:30 schedule never fetch concurrently."""
    from app.taiwan.backfill_worker import WorkerBusyError, WorkerLock

    lock = WorkerLock(_lock_path())
    try:
        lock.acquire()
    except WorkerBusyError:
        return {"status": "busy", "reason": "another fundamentals warm is running"}
    try:
        return _warm(symbols)
    finally:
        lock.release()


def _warm(symbols: list[str] | None) -> dict[str, Any]:
    from app.taiwan.fundamental_chips_service import get_fundamental_chips_service

    svc = get_fundamental_chips_service()
    symbols = liquid_symbols() if symbols is None else symbols
    started = time.monotonic()
    available = {"valuation": 0, "revenue": 0, "financials": 0, "shareholding": 0, "lending": 0}
    failed = 0
    for index, symbol in enumerate(symbols, start=1):
        exchange = symbol.rsplit(".", 1)[-1]
        steps = (
            ("valuation", lambda: svc.get_valuation(symbol, exchange)),
            ("revenue", lambda: svc.get_monthly_revenue(symbol)),
            ("financials", lambda: svc.get_financial_statements(symbol)),
            ("shareholding", lambda: svc.get_foreign_shareholding(symbol)),
            ("lending", lambda: svc.get_securities_lending(symbol)),
        )
        for name, fetch in steps:
            try:
                meta = fetch().meta
                if meta is not None and meta.status == "available":
                    available[name] += 1
            except Exception as exc:  # one symbol/dataset must not stop the warm
                failed += 1
                logger.warning("fundamentals warm %s %s failed: %s", symbol, name, type(exc).__name__)
        if index % 50 == 0:
            logger.info("fundamentals warm %d/%d available=%s", index, len(symbols), available)
    return {"status": "ok", "symbols": len(symbols), "available": available, "failed": failed,
            "elapsed_seconds": round(time.monotonic() - started, 1)}
