"""Taiwan Realtime Market Data Models.

Defines canonical schema for intraday real-time quotes, market statuses,
depth book (Level 2), and data provenance metadata.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from app.taiwan.enrichment.models import SourceMeta


class RealtimeStatus(str, Enum):
    """Real-time data freshness and provenance status."""
    REALTIME = "realtime"                               # Live streaming / sub-second intraday quote
    DELAYED = "delayed"                                 # Delayed intraday quote (e.g. 15-min delay)
    OFFICIAL_SNAPSHOT = "official_snapshot"             # Official post-close snapshot
    FALLBACK = "fallback"                               # Secondary realtime provider fallback
    OFFICIAL_SNAPSHOT_FALLBACK = "official_snapshot_fallback" # Fallback to official close snapshot
    DAILY_FALLBACK = "daily_fallback"                   # Fallback to latest Daily K-line
    STALE = "stale"                                     # Quote timestamp exceeded freshness threshold
    UNAVAILABLE = "unavailable"                         # Failed across all providers


class MarketStatus(str, Enum):
    """Taiwan stock exchange market operating status."""
    PRE_OPEN = "pre_open"                   # 08:30 - 09:00 (Pre-market trial matching)
    OPEN = "open"                           # 09:00 - 13:30 (Continuous regular trading)
    POST_CLOSE = "post_close"               # 13:30 - 14:30 (Post-market odd-lot & fixed-price)
    CLOSED = "closed"                       # After 14:30 or before 08:30
    NON_TRADING_DAY = "non_trading_day"     # Weekend or statutory holiday
    UNKNOWN = "unknown"


@dataclass
class TaiwanRealtimeQuote:
    """Canonical Taiwan Realtime Intraday Quote Schema.

    Strict semantics:
      - symbol: Canonical symbol, e.g. '2330.TWSE', '8069.TPEX', '0050.TWSE'
      - volume: Always normalized to SHARES (股), strictly deterministic (lots * 1000)
      - amount: Normalized to TWD (新台幣元)
      - bids / asks: Level 2 depth entries [(price, shares), ...]
      - source_meta: Full provenance, fallback_reason, and stale indicators
    """
    symbol: str
    name: str
    exchange: str
    last_price: float | None
    prev_close: float | None
    open: float | None
    high: float | None
    low: float | None
    change: float | None
    change_pct: float | None
    volume: int | None               # Total cumulative trading volume in SHARES (股)
    amount: float | None             # Total cumulative trading amount in TWD
    quote_time: datetime | None      # Official market execution / match time
    trade_date: date                 # Trading date
    market_status: str               # MarketStatus value
    source_meta: SourceMeta
    bid_price: float | None = None   # Best bid price
    ask_price: float | None = None   # Best ask price
    bid_volume: int | None = None    # Best bid volume in SHARES (股)
    ask_volume: int | None = None    # Best ask volume in SHARES (股)
    bids: list[tuple[float, int]] = field(default_factory=list) # 5-tier bids [(price, shares)]
    asks: list[tuple[float, int]] = field(default_factory=list) # 5-tier asks [(price, shares)]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.quote_time:
            d["quote_time"] = self.quote_time.isoformat()
        if self.trade_date:
            d["trade_date"] = self.trade_date.isoformat()
        return d
