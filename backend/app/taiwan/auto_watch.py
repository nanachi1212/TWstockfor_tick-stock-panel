"""Watchlist TradePlan orchestration shared by the API and daily scheduler."""
from __future__ import annotations

import logging
import threading

from app.services import watchlist
from app.taiwan.beginner_radar import taiwan_symbols
from app.taiwan.beginner_selection import BeginnerSelectionService

logger = logging.getLogger(__name__)

# One sync at a time: watchlist snapshot -> plan evaluation -> rule publication.
# A sync that starts later waits and then reads the newer watchlist, so an older
# snapshot can never be published after a newer one.
_SYNC_LOCK = threading.Lock()

_watched: tuple[int, frozenset[str]] | None = None

_pending = False
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def sync_watchlist_plans() -> dict:
    """Evaluate only Taiwan watchlist symbols through the existing plan service."""
    from app.taiwan.realtime.monitor_engine import get_monitor_engine

    with _SYNC_LOCK:
        symbols = taiwan_symbols(row.get("symbol") for row in watchlist.list_symbols())
        plans = BeginnerSelectionService().evaluate_symbols(symbols).candidates if symbols else []
        # Evaluation failure propagates before replacement, preserving the old rules.
        return get_monitor_engine().sync_plan_rules(plans)


def watched_symbols() -> frozenset[str]:
    """Current Taiwan watchlist symbols, re-read only when the watchlist revision changes."""
    global _watched
    revision = watchlist.revision()
    cached = _watched
    if cached is not None and cached[0] == revision:
        return cached[1]
    symbols = frozenset(taiwan_symbols(row.get("symbol") for row in watchlist.list_symbols()))
    if watchlist.revision() == revision:  # do not cache a read that raced a write
        _watched = (revision, symbols)
    return symbols


def request_sync() -> None:
    """Re-sync in the background after a watchlist write; bursts coalesce into one more run."""
    global _pending, _worker
    with _worker_lock:
        _pending = True
        if _worker is None:
            _worker = threading.Thread(target=_drain, name="auto-watch-sync", daemon=True)
            _worker.start()


def _drain() -> None:
    global _pending, _worker
    while True:
        with _worker_lock:
            if not _pending:
                _worker = None
                return
            _pending = False
        try:
            sync_watchlist_plans()
        except Exception:
            # Old rules stay; plan alerts are still limited to watched_symbols().
            logger.exception("Auto Watch sync after watchlist change failed")
