"""TWSE MIS (Market Information System) Realtime Provider.

First-party web quotation endpoint provided by Taiwan Stock Exchange (TWSE).

Semantics & Boundary Clarification:
  - Base URL: https://mis.twse.com.tw/stock/api/getStockInfo.jsp
  - Source Type: First-party public web endpoint (best_effort_near_realtime).
  - SLA: No formal contractual or published consumer SLA provided by TWSE.
  - Rate Limits: Subject to empirical web scraping protections; no official rate limit SLA.
  - Batching: Supports pipe-delimited channels (e.g. tse_2330.tw|otc_8069.tw)
  - Unit Semantics:
      * Cumulative volume 'v' is in LOTS (張) -> Deterministically multiplied by 1,000 to SHARES.
      * Depth order volumes 'g' (bids) and 'f' (asks) are in LOTS -> Multiplied by 1,000 to SHARES.
  - Price Limits: Includes 5-tier order book (bids/asks).
"""
from __future__ import annotations

from datetime import date, datetime
import json
import logging
import time
import urllib.request


from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import get_market_status, taipei_now
from app.taiwan.realtime.models import MarketStatus, RealtimeStatus, TaiwanRealtimeQuote
from app.taiwan.symbol import TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)

MIS_ENDPOINT = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://mis.twse.com.tw/stock/fibest.jsp?lang=zh_tw",
}


def to_mis_channel(sym: TaiwanSymbol | str) -> str:
    """Convert canonical TaiwanSymbol to TWSE MIS channel string.

    Examples:
      2330.TWSE -> tse_2330.tw
      8069.TPEX -> otc_8069.tw
      0050.TWSE -> tse_0050.tw
    """
    ts = sym if isinstance(sym, TaiwanSymbol) else parse_symbol(sym)
    prefix = "tse" if ts.exchange == "TWSE" else "otc"
    return f"{prefix}_{ts.code}.tw"


def _safe_float(val: Any) -> float | None:
    if val is None or val == "-" or str(val).strip() == "":
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except ValueError:
        return None


def _parse_depth_tier(
    prices_str: str | None,
    volumes_str: str | None,
) -> list[tuple[float, int]]:
    """Parse underscore-delimited prices and lots volumes into [(price, shares), ...]."""
    if not prices_str or not volumes_str:
        return []
    p_parts = [p.strip() for p in prices_str.split("_") if p.strip()]
    v_parts = [v.strip() for v in volumes_str.split("_") if v.strip()]
    tier: list[tuple[float, int]] = []
    for p_s, v_s in zip(p_parts, v_parts):
        p = _safe_float(p_s)
        v_lots = _safe_float(v_s)
        if p is not None and v_lots is not None and p > 0:
            tier.append((p, int(round(v_lots * 1000))))
    return tier


class TwseMisRealtimeProvider:
    """Taiwan Stock Exchange MIS official real-time quotation provider."""

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def fetch_quotes(
        self,
        symbols: list[TaiwanSymbol | str],
        mock_response_json: dict | None = None,
    ) -> dict[str, TaiwanRealtimeQuote]:
        """Fetch real-time quotes in batch for specified symbols."""
        if not symbols:
            return {}

        parsed_symbols = [s if isinstance(s, TaiwanSymbol) else parse_symbol(s) for s in symbols]
        channel_to_canonical = {
            to_mis_channel(ts): ts.canonical
            for ts in parsed_symbols
        }

        # 1. Fetch data (or use injected mock response for tests)
        t0 = time.perf_counter()
        observed_ms: float | None = None
        if mock_response_json is not None:
            raw_data = mock_response_json
            observed_ms = 0.5
        else:
            channels = "|".join(channel_to_canonical.keys())
            url = f"{MIS_ENDPOINT}?ex_ch={channels}&json=1&delay=0"
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw_data = json.loads(resp.read().decode("utf-8"))
                observed_ms = (time.perf_counter() - t0) * 1000
            except Exception as e:
                logger.warning("Failed to fetch quotes from TWSE MIS: %s", e)
                return {}

        now_tpe = taipei_now()
        fetched_at_iso = now_tpe.isoformat()
        current_market_status = get_market_status(now_tpe)

        quotes: dict[str, TaiwanRealtimeQuote] = {}
        for item in raw_data.get("msgArray", []):
            channel = item.get("ch")
            canonical = channel_to_canonical.get(channel)
            if not canonical:
                # Fallback match by code + exchange
                code = item.get("c")
                for ch, c_sym in channel_to_canonical.items():
                    if f"_{code}." in ch:
                        canonical = c_sym
                        break
            if not canonical:
                continue

            name = item.get("n") or item.get("nf") or canonical
            last_price = _safe_float(item.get("z"))
            prev_close = _safe_float(item.get("y"))
            open_p = _safe_float(item.get("o"))
            high_p = _safe_float(item.get("h"))
            low_p = _safe_float(item.get("l"))

            # Volume conversion: MIS 'v' is in LOTS (張) -> multiply by 1,000 to shares
            v_lots = _safe_float(item.get("v"))
            volume_shares = int(round(v_lots * 1000)) if v_lots is not None else None

            # Calculate change and percentage
            change: float | None = None
            change_pct: float | None = None
            if last_price is not None and prev_close is not None and prev_close > 0:
                change = round(last_price - prev_close, 4)
                change_pct = round((change / prev_close) * 100.0, 4)

            # Parse quote official timestamp strictly; refuse to substitute datetime.now()
            d_str = item.get("d")  # "20260828"
            t_str = item.get("t")  # "13:30:00"
            quote_time: datetime | None = None
            trade_date: date | None = None
            timestamp_malformed = False
            if d_str and t_str:
                try:
                    quote_time = datetime.strptime(f"{d_str} {t_str}", "%Y%m%d %H:%M:%S").replace(
                        tzinfo=now_tpe.tzinfo
                    )
                    trade_date = quote_time.date()
                except Exception:
                    timestamp_malformed = True
            else:
                timestamp_malformed = True

            # Date mismatch detection: session open but quote still on prior trading date
            date_mismatch = (
                current_market_status in (MarketStatus.OPEN, MarketStatus.PRE_OPEN)
                and trade_date is not None
                and trade_date != now_tpe.date()
            )

            is_stale = timestamp_malformed or date_mismatch
            fallback_reason: str | None = None
            if timestamp_malformed:
                fallback_reason = "Quote timestamp missing or malformed; refused to fake with local time"
                status = RealtimeStatus.STALE.value
            elif date_mismatch:
                fallback_reason = f"Quote trade date {trade_date} does not match current session date {now_tpe.date()}"
                status = RealtimeStatus.STALE.value
            else:
                status = (
                    RealtimeStatus.REALTIME.value
                    if current_market_status == MarketStatus.OPEN
                    else RealtimeStatus.OFFICIAL_SNAPSHOT.value
                )

            # Parse 5-tier bid / ask depth book
            bids = _parse_depth_tier(item.get("b"), item.get("g"))
            asks = _parse_depth_tier(item.get("a"), item.get("f"))
            bid_p, bid_v = bids[0] if bids else (None, None)
            ask_p, ask_v = asks[0] if asks else (None, None)

            # Available fields metadata
            avail = ["prev_close"]
            if last_price is not None:
                avail.append("last_price")
            if volume_shares is not None:
                avail.append("volume")
            if open_p is not None:
                avail.append("open")
            if high_p is not None:
                avail.append("high")
            if low_p is not None:
                avail.append("low")
            if bids:
                avail.append("bids")
            if asks:
                avail.append("asks")

            meta = SourceMeta(
                source="twse:mis",
                source_url=MIS_ENDPOINT,
                fetched_at=fetched_at_iso,
                trade_date=trade_date,
                status=status,
                is_realtime=(current_market_status == MarketStatus.OPEN and not is_stale),
                fallback_reason=fallback_reason,
                available_fields=tuple(avail),
                is_stale=is_stale,
                source_type="first_party_web_endpoint",
                freshness_class="best_effort_near_realtime",
                is_best_effort=True,
                documented_sla=False,
                observed_latency_ms=round(observed_ms, 2) if observed_ms is not None else None,
            )


            ts_obj = parse_symbol(canonical)
            quote = TaiwanRealtimeQuote(
                symbol=canonical,
                name=name,
                exchange=ts_obj.exchange,
                last_price=last_price,
                prev_close=prev_close,
                open=open_p,
                high=high_p,
                low=low_p,
                change=change,
                change_pct=change_pct,
                volume=volume_shares,
                amount=None,  # MIS does not provide cumulative amount; nullable
                quote_time=quote_time,
                trade_date=trade_date,
                market_status=current_market_status.value,
                source_meta=meta,
                bid_price=bid_p,
                ask_price=ask_p,
                bid_volume=bid_v,
                ask_volume=ask_v,
                bids=bids,
                asks=asks,
            )
            quotes[canonical] = quote

        return quotes
