"""Taiwan Realtime Market Data Layer.

Exports canonical real-time quotation models, MIS provider, Yahoo provider,
session calendar, and orchestration service.
"""
from __future__ import annotations

from app.taiwan.realtime.calendar import (
    TAIPEI_TZ,
    TaiwanTradingCalendar,
    get_market_status,
    taipei_now,
    taipei_today,
)
from app.taiwan.realtime.mis_provider import TwseMisRealtimeProvider, to_mis_channel
from app.taiwan.realtime.models import (
    MarketStatus,
    RealtimeFreshnessPolicy,
    RealtimeStatus,
    TaiwanRealtimeQuote,
)
from app.taiwan.realtime.service import TaiwanRealtimeService
from app.taiwan.realtime.yahoo_provider import YahooRealtimeProvider, to_yahoo_ticker


_default_service: TaiwanRealtimeService | None = None


def get_realtime_service() -> TaiwanRealtimeService:
    """Get or instantiate global singleton TaiwanRealtimeService."""
    global _default_service
    if _default_service is None:
        _default_service = TaiwanRealtimeService()
    return _default_service
