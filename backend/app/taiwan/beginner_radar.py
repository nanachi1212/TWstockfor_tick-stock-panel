"""Daily Entry Radar v1: where a live price sits against the beginner TradePlan.

The end-of-day beginner selection (state, reasons, plan levels) stays the only
authority.  The radar adds two things on top of it:

* the user's own stocks (holdings and watchlist), evaluated by the same rules
  without a pool rank;
* a live status that compares the current price with the plan levels.

A quote that the monitor engine refuses (closed session, stale, delayed, daily
fallback) gets no live status here either.  This is not a trade instruction.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.taiwan.beginner_selection import (
    BEGINNER_SELECTION_VERSION,
    BeginnerCandidate,
    BeginnerSelectionService,
    MarketSummary,
    PlanLevels,
)
from app.taiwan.realtime.models import TaiwanRealtimeQuote
from app.taiwan.realtime.monitor_engine import quote_quality_gate
from app.taiwan.realtime.monitor_models import EvaluationStatus

logger = logging.getLogger(__name__)

RADAR_VERSION = "entry-radar-v1"
NEAR_PCT = 1.5  # "near" band above the entry zone / below the breakout trigger
MAX_OWN_SYMBOLS = 50
PICK_LIMIT = 20
_TAIWAN_SYMBOL = re.compile(r"[0-9]{4,6}[A-Z]?\.(?:TWSE|TPEX)")

LiveStatus = Literal[
    "in_zone", "near_zone", "below_zone", "breakout", "near_breakout",
    "waiting", "below_stop", "unavailable",
]
Source = Literal["pick", "holding", "watchlist"]

LIVE_LABEL: dict[str, str] = {
    "in_zone": "已進承接區",
    "near_zone": "接近承接區",
    "below_zone": "低於承接區",
    "breakout": "已突破",
    "near_breakout": "接近突破",
    "waiting": "尚未到位",
    "below_stop": "跌破失效位",
    "unavailable": "無盤中判斷",
}

_GATE_NOTE: dict[EvaluationStatus, str] = {
    EvaluationStatus.SKIPPED_MARKET_CLOSED: "目前不是盤中時段，請看收盤後的狀態。",
    EvaluationStatus.SKIPPED_MARKET_UNVERIFIED: "今天是否開盤尚未確認，暫不做盤中判斷。",
    EvaluationStatus.SKIPPED_STALE_DATA: "即時報價已過期，暫不做盤中判斷。",
    EvaluationStatus.SKIPPED_DAILY_FALLBACK: "只拿到日線資料，不是即時報價。",
    EvaluationStatus.SKIPPED_DELAYED_SOURCE: "報價有延遲，暫不做盤中判斷。",
}


class RadarLive(BaseModel):
    status: LiveStatus
    label: str
    price: float | None = None
    quote_time: str | None = None
    note: str | None = None


class RadarItem(BaseModel):
    candidate: BeginnerCandidate
    sources: list[Source]
    live: RadarLive


class RadarResponse(BaseModel):
    version: str = RADAR_VERSION
    selection_version: str = BEGINNER_SELECTION_VERSION
    status: Literal["ready", "degraded", "unavailable"]
    as_of: str | None = None
    generated_at: str
    market_session: str
    market: MarketSummary
    items: list[RadarItem] = Field(default_factory=list)
    not_selected: list[BeginnerCandidate] = Field(default_factory=list)
    universe_count: int = 0
    eligible_count: int = 0
    data_gaps: list[str] = Field(default_factory=list)
    disclaimer: str = "雷達只比對現價和計畫價位，不是買賣指令，也不代表上漲機率。"


def parse_holdings(raw: str) -> list[str]:
    """Validate the comma-separated holdings sent by the client (trust boundary)."""
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    for symbol in symbols:
        if not _TAIWAN_SYMBOL.fullmatch(symbol):
            raise ValueError(f"無效的台股代號：{symbol}")
    return list(dict.fromkeys(symbols))


def taiwan_symbols(raw: Iterable[object]) -> list[str]:
    """Keep Taiwan symbols only; the watchlist can also hold other markets."""
    cleaned = (str(item or "").strip().upper() for item in raw)
    return list(dict.fromkeys(s for s in cleaned if _TAIWAN_SYMBOL.fullmatch(s)))


def live_plan_status(plan: PlanLevels, price: float) -> LiveStatus:
    """Compare one live price with the frozen plan levels.  Pure rule."""
    if price < plan.stop_price:
        return "below_stop"
    if plan.entry_semantics == "breakout_stop":
        trigger = plan.breakout_trigger
        if trigger is None:
            return "unavailable"
        if price >= trigger:
            return "breakout"
        return "near_breakout" if price >= trigger * (1 - NEAR_PCT / 100) else "waiting"
    low, high = plan.entry_zone_low, plan.entry_zone_high
    if low is None or high is None:
        return "unavailable"
    if price > high:
        return "near_zone" if price <= high * (1 + NEAR_PCT / 100) else "waiting"
    return "in_zone" if price >= low else "below_zone"


def _unavailable(note: str, price: float | None = None, quote_time: str | None = None) -> RadarLive:
    return RadarLive(status="unavailable", label=LIVE_LABEL["unavailable"],
                     price=price, quote_time=quote_time, note=note)


def radar_live(candidate: BeginnerCandidate, quote: TaiwanRealtimeQuote | None) -> RadarLive:
    if quote is None:
        return _unavailable("目前拿不到即時報價。")
    blocked = quote_quality_gate(quote)
    if blocked is not None:
        return _unavailable(_GATE_NOTE.get(blocked[0], "即時報價不可用。"))
    price = quote.last_price
    if price is None or not math.isfinite(price) or price <= 0:
        return _unavailable("即時價格缺漏。")
    quote_time = quote.quote_time.isoformat() if quote.quote_time else None
    if candidate.trade_plan is None:
        return _unavailable(candidate.plan_unavailable_reason or "這檔目前不提供計畫價位。", price, quote_time)
    status = live_plan_status(candidate.trade_plan, price)
    if status == "unavailable":
        return _unavailable("計畫價位不完整。", price, quote_time)
    return RadarLive(status=status, label=LIVE_LABEL[status], price=price, quote_time=quote_time)


def build_radar(
    service: BeginnerSelectionService,
    *,
    holdings: list[str],
    watchlist: list[str],
    get_quotes: Callable[[list[str]], dict[str, TaiwanRealtimeQuote]],
    market_session: Callable[[], str],
) -> RadarResponse:
    selection = service.build(limit=PICK_LIMIT)
    picks = selection.candidates
    gaps = list(selection.data_gaps)

    sources: dict[str, list[Source]] = {c.symbol: ["pick"] for c in picks}
    own: list[str] = []
    truncated = False
    own_sources: tuple[tuple[Source, list[str]], ...] = (("holding", holdings), ("watchlist", watchlist))
    for source, symbols in own_sources:
        for symbol in symbols:
            if symbol not in sources:
                if len(own) >= MAX_OWN_SYMBOLS:
                    truncated = True
                    continue
                own.append(symbol)
                sources[symbol] = []
            if source not in sources[symbol]:
                sources[symbol].append(source)
    if truncated:
        gaps.append(f"持股與自選股超過 {MAX_OWN_SYMBOLS} 檔，只顯示前 {MAX_OWN_SYMBOLS} 檔（持股優先）")

    extra = service.evaluate_symbols(own).candidates if own else []
    candidates = [*picks, *extra]
    try:
        quotes = get_quotes([c.symbol for c in candidates]) if candidates else {}
    except Exception as exc:
        logger.warning("entry radar quotes unavailable: %s", type(exc).__name__)
        quotes = {}
        gaps.append("即時報價暫時不可用")
    session = next((q.market_status for q in quotes.values()), None) or market_session()

    return RadarResponse(
        status=selection.status,
        as_of=selection.as_of,
        generated_at=datetime.now(UTC).isoformat(),
        market_session=session,
        market=selection.market,
        items=[
            RadarItem(candidate=c, sources=sources[c.symbol], live=radar_live(c, quotes.get(c.symbol)))
            for c in candidates
        ],
        not_selected=selection.not_selected,
        universe_count=selection.universe_count,
        eligible_count=selection.eligible_count,
        data_gaps=gaps,
    )
