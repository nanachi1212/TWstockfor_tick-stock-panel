"""Watchlist TradePlan orchestration shared by the API and daily scheduler."""
from __future__ import annotations

from app.services import watchlist
from app.taiwan.beginner_radar import taiwan_symbols
from app.taiwan.beginner_selection import BeginnerSelectionService


def sync_watchlist_plans() -> dict:
    """Evaluate only Taiwan watchlist symbols through the existing plan service."""
    from app.taiwan.realtime.monitor_engine import get_monitor_engine

    symbols = taiwan_symbols(row.get("symbol") for row in watchlist.list_symbols())
    plans = BeginnerSelectionService().evaluate_symbols(symbols).candidates if symbols else []
    # Evaluation failure propagates before replacement, preserving the old rules.
    return get_monitor_engine().sync_plan_rules(plans)
