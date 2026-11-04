"""After-close pre-warm of the per-symbol fundamentals/chips cache the screener reads.

The screener never fetches at request time; without this job its fundamental and
chip filters only see stocks someone happened to open. FinMind is rate limited
(about 600 requests/hour with a token), so only liquid common stocks are warmed;
the service skips symbols whose cache is still valid, so a rerun resumes.
"""
from __future__ import annotations

import json
import logging
import time
from copy import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import polars as pl

from app.taiwan.finmind_cache import FinMindCache

logger = logging.getLogger(__name__)

# Same floor as the screener templates' volume_min (500 lots = 500,000 shares).
MIN_VOLUME_SHARES = 500_000
# Readers keep these caches 84h (weekend bridge); the daily warm refreshes anything older.
REFRESH_AFTER = timedelta(hours=20)
DAILY_DATASETS = {
    "revenue": "TaiwanStockMonthRevenue",
    "shareholding": "TaiwanStockShareholding",
    "lending": "TaiwanStockSecuritiesLending",
}
# A run takes at most ~8h; a dead owner's lock must not block the next day's 17:30 run.
LOCK_MAX_AGE = timedelta(hours=12)


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


def last_run_path():
    """Successful warm marker; individual stock reads never advance startup catch-up."""
    return _lock_path().with_name("fundamentals_warm.last")


def warm_fundamentals(symbols: list[str] | None = None) -> dict[str, Any]:
    """Run one warm; a manual run and the 17:30 schedule never fetch concurrently."""
    from app.taiwan.backfill_worker import WorkerBusyError, WorkerLock

    lock = WorkerLock(_lock_path(), max_age=LOCK_MAX_AGE)
    try:
        # A recorded owner confirmed dead (crash/kill) is reclaimed at once; a live or
        # unverifiable owner still blocks (acquire re-checks liveness under its guard).
        lock.acquire(force=lock.owner_status() == "stale")
    except WorkerBusyError:
        return {"status": "busy", "reason": "another fundamentals warm is running"}
    try:
        result = _warm(symbols)
        if result["status"] == "ok":
            last_run_path().touch()
        return result
    finally:
        lock.release()


def _refresh(svc, dataset: str, symbol: str, fetch):
    """Refresh in isolation; readers keep the old cache until a valid replacement exists."""
    path = svc.cache._file_path(dataset, symbol)
    if path.exists():
        try:
            fetched = datetime.fromisoformat(json.loads(path.read_bytes())["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=UTC)
            if datetime.now(UTC) - fetched < REFRESH_AFTER:
                return fetch(svc)
        except (ValueError, KeyError, TypeError):
            pass

    # Reuse the service's cache injection without mutating the shared singleton.
    # The staging directory is on the same filesystem so publication is atomic.
    with TemporaryDirectory(prefix=".warm-", dir=path.parent) as folder:
        staged = copy(svc)
        staged.cache = FinMindCache(Path(folder))
        result = fetch(staged)
        if result.meta is not None and result.meta.status == "available":
            staged.cache._file_path(dataset, symbol).replace(path)
        return result


def _is_skipped(name: str, meta) -> bool:
    """Only explicit non-applicability is terminal; unknown/empty responses retry."""
    if meta is None:
        return False
    if meta.status in {"not_applicable", "unsupported", "disabled"}:
        return True
    reason = getattr(meta, "fallback_reason", None) or getattr(meta, "reason", None)
    return reason == "FinMind provider disabled" or (
        name == "lending" and reason == "securities lending rows contain no positive transaction volume"
    )


def _warm(symbols: list[str] | None) -> dict[str, Any]:
    from app.taiwan.fundamental_chips_service import get_fundamental_chips_service

    svc = get_fundamental_chips_service()
    symbols = liquid_symbols() if symbols is None else symbols
    started = time.monotonic()
    available = {"valuation": 0, "revenue": 0, "financials": 0, "shareholding": 0, "lending": 0}
    unavailable = dict.fromkeys(available, 0)
    skipped = dict.fromkeys(available, 0)
    failed = 0
    for exchange in (("TWSE", "TPEX") if symbols else ()):
        try:  # one official request per exchange instead of one per symbol
            svc.prime_valuation_cache(exchange)
        except Exception as exc:
            logger.warning("valuation snapshot %s failed: %s", exchange, type(exc).__name__)
    for index, symbol in enumerate(symbols, start=1):
        exchange = symbol.rsplit(".", 1)[-1]
        steps = (
            ("valuation", lambda service, s=symbol, e=exchange: service.get_valuation(s, e)),
            ("revenue", lambda service, s=symbol: service.get_monthly_revenue(s)),
            ("financials", lambda service, s=symbol: service.get_financial_statements(s)),
            ("shareholding", lambda service, s=symbol: service.get_foreign_shareholding(s)),
            ("lending", lambda service, s=symbol: service.get_securities_lending(s)),
        )
        for name, fetch in steps:
            try:
                dataset = DAILY_DATASETS.get(name)
                meta = (_refresh(svc, dataset, symbol, fetch) if dataset else fetch(svc)).meta
                if meta is not None and meta.status == "available":
                    available[name] += 1
                elif _is_skipped(name, meta):
                    skipped[name] += 1
                else:
                    unavailable[name] += 1
            except Exception as exc:  # one symbol/dataset must not stop the warm
                failed += 1
                logger.warning("fundamentals warm %s %s failed: %s", symbol, name, type(exc).__name__)
        if index % 50 == 0:
            logger.info("fundamentals warm %d/%d available=%s", index, len(symbols), available)
    incomplete = failed + sum(unavailable.values())
    status = "ok" if not incomplete else ("partial" if sum(available.values()) else "error")
    if not symbols:
        status = "skipped"
    result = {"status": status, "symbols": len(symbols), "available": available,
              "unavailable": unavailable, "skipped": skipped, "failed": failed,
              "elapsed_seconds": round(time.monotonic() - started, 1)}
    logger.info("fundamentals warm finished: %s", result)
    return result
