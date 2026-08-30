"""Yahoo Finance Realtime Provider (Secondary Fallback).

Secondary fallback provider for Taiwan real-time quotes using Yahoo Finance
v8 chart/quote endpoints.
"""
from __future__ import annotations

from datetime import date, datetime
import json
import logging
import urllib.request

from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import get_market_status, taipei_now
from app.taiwan.realtime.models import MarketStatus, RealtimeStatus, TaiwanRealtimeQuote
from app.taiwan.symbol import TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)

YAHOO_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def to_yahoo_ticker(sym: TaiwanSymbol | str) -> str:
    """Convert canonical Taiwan symbol to Yahoo ticker.

    2330.TWSE -> 2330.TW
    8069.TPEX -> 8069.TWO
    0050.TWSE -> 0050.TW
    """
    ts = sym if isinstance(sym, TaiwanSymbol) else parse_symbol(sym)
    suffix = ".TW" if ts.exchange == "TWSE" else ".TWO"
    return f"{ts.code}{suffix}"


class YahooRealtimeProvider:
    """Yahoo Finance secondary fallback real-time quote provider."""

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def fetch_single_quote(
        self,
        symbol: TaiwanSymbol | str,
        mock_response_json: dict | None = None,
    ) -> TaiwanRealtimeQuote | None:
        """Fetch single quote via Yahoo Finance v8 chart API."""
        ts = symbol if isinstance(symbol, TaiwanSymbol) else parse_symbol(symbol)
        ticker = to_yahoo_ticker(ts)
        url = f"{YAHOO_CHART_BASE}/{ticker}?interval=1d&range=1d"

        if mock_response_json is not None:
            raw = mock_response_json
        else:
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                logger.warning("Failed to fetch quote from Yahoo for %s: %s", ts.canonical, e)
                return None

        result_list = raw.get("chart", {}).get("result", [])
        if not result_list:
            return None

        meta = result_list[0].get("meta", {})
        last_price = meta.get("regularMarketPrice")
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        volume = meta.get("regularMarketVolume")  # Yahoo volume is already in shares
        ts_unix = meta.get("regularMarketTime")

        now_tpe = taipei_now()
        quote_time = (
            datetime.fromtimestamp(ts_unix, tz=now_tpe.tzinfo)
            if ts_unix
            else now_tpe
        )
        trade_date = quote_time.date()

        change: float | None = None
        change_pct: float | None = None
        if last_price is not None and prev_close is not None and prev_close > 0:
            change = round(last_price - prev_close, 4)
            change_pct = round((change / prev_close) * 100.0, 4)

        current_market_status = get_market_status(now_tpe)

        avail = ["last_price", "prev_close"]
        if volume is not None:
            avail.append("volume")

        source_meta = SourceMeta(
            source="yahoo:chart",
            source_url=url,
            fetched_at=now_tpe.isoformat(),
            trade_date=trade_date,
            status=RealtimeStatus.FALLBACK.value,
            is_realtime=(current_market_status == MarketStatus.OPEN),
            fallback_reason="Primary MIS unavailable or missing symbol",
            available_fields=tuple(avail),
            is_stale=False,
        )

        return TaiwanRealtimeQuote(
            symbol=ts.canonical,
            name=meta.get("shortName") or ts.canonical,
            exchange=ts.exchange,
            last_price=last_price,
            prev_close=prev_close,
            open=meta.get("regularMarketDayHigh"),  # approximated
            high=meta.get("regularMarketDayHigh"),
            low=meta.get("regularMarketDayLow"),
            change=change,
            change_pct=change_pct,
            volume=int(volume) if volume is not None else None,
            amount=None,
            quote_time=quote_time,
            trade_date=trade_date,
            market_status=current_market_status.value,
            source_meta=source_meta,
        )

    def fetch_quotes(
        self,
        symbols: list[TaiwanSymbol | str],
        mock_response_json: dict | None = None,
    ) -> dict[str, TaiwanRealtimeQuote]:
        """Fetch quotes sequentially (best effort) for multiple symbols."""
        quotes: dict[str, TaiwanRealtimeQuote] = {}
        for s in symbols:
            q = self.fetch_single_quote(s, mock_response_json=mock_response_json)
            if q is not None:
                quotes[q.symbol] = q
        return quotes
