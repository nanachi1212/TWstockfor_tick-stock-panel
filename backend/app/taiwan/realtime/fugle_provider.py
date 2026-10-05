"""Optional Fugle MarketData WebSocket ``aggregates`` adapter.

Fugle is used only for live intraday evidence.  It does not replace official
daily, PIT, institutional, MOPS, corporate-action, or TradePlan sources.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now

logger = logging.getLogger(__name__)

FUGLE_STOCK_STREAM_URL = "wss://api.fugle.tw/marketdata/v1.0/stock/streaming"
FUGLE_AGGREGATES_SOURCE = "fugle_marketdata:websocket:aggregates"
FUGLE_REGULAR_LOT_SIZE = 1_000

FugleObservationStatus = Literal["disabled", "waiting", "stale", "available"]


@dataclass(frozen=True)
class FugleAggregatesSnapshot:
    symbol: str
    trade_date: date
    observed_at: datetime
    trade_volume_at_bid: float
    trade_volume_at_ask: float
    last_price: float | None
    trade_volume: float | None
    trade_value: float | None
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    is_close: bool
    source: str = FUGLE_AGGREGATES_SOURCE

    def is_usable(self, *, now: datetime | None = None, max_age_seconds: float = 120.0) -> bool:
        current = now or taipei_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=TAIPEI_TZ)
        observed = self.observed_at.astimezone(TAIPEI_TZ)
        if self.trade_date != current.astimezone(TAIPEI_TZ).date():
            return False
        if self.is_close:
            return True
        age = current.astimezone(TAIPEI_TZ) - observed
        return timedelta(seconds=-5) <= age <= timedelta(seconds=max_age_seconds)


@dataclass(frozen=True)
class FugleAggregatesObservation:
    status: FugleObservationStatus
    snapshot: FugleAggregatesSnapshot | None = None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number >= 0 else None


def _timestamp(value: Any) -> datetime | None:
    number = _number(value)
    if number is None:
        return None
    if number >= 1_000_000_000_000_000:
        seconds = number / 1_000_000
    elif number >= 1_000_000_000_000:
        seconds = number / 1_000
    else:
        seconds = number
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).astimezone(TAIPEI_TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _depth(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list):
        return ()
    levels: list[tuple[float, float]] = []
    for row in value[:5]:
        if not isinstance(row, dict):
            continue
        price = _number(row.get("price"))
        size = _number(row.get("size"))
        if price is not None and size is not None:
            levels.append((price, size * FUGLE_REGULAR_LOT_SIZE))
    return tuple(levels)


def parse_fugle_aggregates_message(
    message: str | bytes | dict[str, Any],
) -> FugleAggregatesSnapshot | None:
    """Parse one official Fugle aggregates data event; malformed input fails closed."""
    try:
        payload = json.loads(message) if isinstance(message, (str, bytes)) else message
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("event") != "data" or payload.get("channel") != "aggregates":
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("isTrial") is True:
        return None
    total = data.get("total")
    if not isinstance(total, dict):
        return None
    inner = _number(total.get("tradeVolumeAtBid"))
    outer = _number(total.get("tradeVolumeAtAsk"))
    observed_at = _timestamp(total.get("time") or data.get("lastUpdated"))
    raw_symbol = str(data.get("symbol") or "").strip().upper()
    exchange = str(data.get("exchange") or "").strip().upper()
    suffix = (
        "TWSE" if exchange in {"TWSE", "TSE"}
        else "TPEX" if exchange in {"TPEX", "OTC"}
        else ""
    )
    try:
        trade_date = date.fromisoformat(str(data.get("date") or ""))
    except ValueError:
        return None
    if (
        inner is None
        or outer is None
        or observed_at is None
        or not raw_symbol
        or not suffix
        or observed_at.date() != trade_date
    ):
        return None
    return FugleAggregatesSnapshot(
        symbol=f"{raw_symbol}.{suffix}",
        trade_date=trade_date,
        observed_at=observed_at,
        trade_volume_at_bid=inner * FUGLE_REGULAR_LOT_SIZE,
        trade_volume_at_ask=outer * FUGLE_REGULAR_LOT_SIZE,
        last_price=_number(data.get("lastPrice")),
        trade_volume=(
            volume * FUGLE_REGULAR_LOT_SIZE
            if (volume := _number(total.get("tradeVolume"))) is not None else None
        ),
        trade_value=_number(total.get("tradeValue")),
        bids=_depth(data.get("bids")),
        asks=_depth(data.get("asks")),
        is_close=data.get("isClose") is True,
    )


class FugleAggregatesProvider:
    """Lazy, optional stream cache for requested symbols.

    No connection is attempted without an API key.  Requests never wait for a
    WebSocket response; consumers remain ``data_insufficient`` while the first
    reliable snapshot is pending.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        if api_key is None:
            from app.config import settings

            api_key = settings.fugle_api_key
        self._api_key = api_key.strip()
        self._snapshots: dict[str, FugleAggregatesSnapshot] = {}
        self._requested: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connect_factory = connect_factory
        self._last_attempt: datetime | None = None
        self._last_success: datetime | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def request_symbols(self, symbols: list[str]) -> None:
        if not self.enabled:
            return
        codes = {symbol.split(".", 1)[0].strip().upper() for symbol in symbols if symbol.strip()}
        if not codes:
            return
        with self._lock:
            # Keep the stream bounded. The product only needs the current stock
            # plus a small Dashboard set, never a market-wide subscription.
            retained = sorted(self._requested - codes)
            self._requested = set((sorted(codes) + retained)[:6])
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_forever,
                name="fugle-aggregates",
                daemon=True,
            )
            self._thread.start()

    def observe(
        self, symbol: str, *, now: datetime | None = None,
    ) -> FugleAggregatesObservation:
        if not self.enabled:
            return FugleAggregatesObservation(status="disabled")
        with self._lock:
            snapshot = self._snapshots.get(symbol.upper())
        if snapshot is None:
            return FugleAggregatesObservation(status="waiting")
        if not snapshot.is_usable(now=now):
            return FugleAggregatesObservation(status="stale", snapshot=snapshot)
        if snapshot.trade_volume_at_bid + snapshot.trade_volume_at_ask <= 0:
            return FugleAggregatesObservation(status="waiting", snapshot=snapshot)
        return FugleAggregatesObservation(status="available", snapshot=snapshot)

    def ingest_message(self, message: str | bytes | dict[str, Any]) -> FugleAggregatesSnapshot | None:
        snapshot = parse_fugle_aggregates_message(message)
        if snapshot is None:
            return None
        with self._lock:
            previous = self._snapshots.get(snapshot.symbol)
            if previous is None or snapshot.observed_at >= previous.observed_at:
                self._snapshots[snapshot.symbol] = snapshot
        return snapshot

    def close(self) -> None:
        self._stop.set()

    def health_metadata(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "provider": "Fugle", "enabled": False, "auth_configured": False,
                "status": "config_missing", "source": FUGLE_AGGREGATES_SOURCE,
                "freshness": "unavailable", "reason": "未設定 FUGLE_API_KEY",
                "error": "config_missing",
            }
        with self._lock:
            snapshots = list(self._snapshots.values())
            last_attempt = self._last_attempt
            last_success = self._last_success
            last_error = self._last_error
        latest = max(snapshots, key=lambda item: item.observed_at, default=None)
        if latest is not None and latest.is_usable():
            status, reason = "current", "Fugle aggregates 即時資料可用"
        elif latest is not None:
            status, reason = "stale", "Fugle aggregates 最新資料已過期"
        elif last_error:
            status, reason = "provider_error", "Fugle WebSocket 目前無法取得資料"
        else:
            status, reason = "not_run", "尚未訂閱需要盤中資料的股票"
        return {
            "provider": "Fugle", "enabled": True, "auth_configured": True,
            "status": status, "source": FUGLE_AGGREGATES_SOURCE,
            "data_date": latest.trade_date.isoformat() if latest else None,
            "as_of": latest.observed_at.isoformat() if latest else None,
            "freshness": "realtime" if status == "current" else status,
            "reason": reason,
            "last_attempt": last_attempt.isoformat() if last_attempt else None,
            "last_success": last_success.isoformat() if last_success else None,
            "error": last_error,
        }

    def _run_forever(self) -> None:
        connect = self._connect_factory
        if connect is None:
            try:
                from websockets.sync.client import connect
            except ImportError:
                logger.warning("Fugle aggregates disabled because websockets is unavailable")
                with self._lock:
                    self._last_error = "websocket_dependency_unavailable"
                return
        assert connect is not None
        delay = 1.0
        while not self._stop.is_set():
            with self._lock:
                self._last_attempt = taipei_now()
            try:
                with connect(FUGLE_STOCK_STREAM_URL, open_timeout=10, close_timeout=5) as socket:
                    socket.send(json.dumps({"event": "auth", "data": {"apikey": self._api_key}}))
                    authenticated = False
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and not authenticated:
                        raw = socket.recv(timeout=max(0.1, deadline - time.monotonic()))
                        payload = json.loads(raw)
                        if payload.get("event") == "authenticated":
                            authenticated = True
                        elif payload.get("event") == "error":
                            raise RuntimeError("Fugle authentication failed")
                    if not authenticated:
                        raise TimeoutError("Fugle authentication timed out")

                    with self._lock:
                        self._last_success = taipei_now()
                        self._last_error = None

                    subscribed: set[str] = set()
                    delay = 1.0
                    while not self._stop.is_set():
                        with self._lock:
                            requested = set(self._requested)
                            pending = sorted(requested - subscribed)
                        if subscribed - requested:
                            # Reconnect to release symbols that are no longer visible.
                            break
                        if pending:
                            socket.send(json.dumps({
                                "event": "subscribe",
                                "data": {"channel": "aggregates", "symbols": pending},
                            }))
                            subscribed.update(pending)
                        try:
                            self.ingest_message(socket.recv(timeout=1.0))
                        except TimeoutError:
                            continue
            except Exception as exc:
                if not self._stop.is_set():
                    with self._lock:
                        self._last_error = type(exc).__name__
                    logger.warning("Fugle aggregates connection unavailable: %s", type(exc).__name__)
                    self._stop.wait(delay)
                    delay = min(delay * 2, 30.0)


_PROVIDER: FugleAggregatesProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def get_fugle_aggregates_provider() -> FugleAggregatesProvider:
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            _PROVIDER = FugleAggregatesProvider()
        return _PROVIDER
