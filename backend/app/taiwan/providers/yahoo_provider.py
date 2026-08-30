"""Yahoo Finance chart API adapter for Taiwan market data.

Source semantics (verified with official tests):
  - Base URL: https://query1.finance.yahoo.com/v8/finance/chart/{provider_symbol}
  - Symbols: .TW (TWSE), .TWO (TPEx)
  - Volume unit: shares (股) — corresponds to continuous session volume
  - Amount unit: UNAVAILABLE (Yahoo does not provide turnover in chart API)
  - Price semantics: RAW (uses indicators.quote, NOT adjclose)
  - Historical coverage: up to multi-decade history
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import polars as pl

from app.data_providers.base import AssetType
from app.taiwan.providers.base import AmountUnit, PriceSemantics, SourceMetadata, VolumeUnit
from app.taiwan.providers.normalizer import normalize_taiwan_daily
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.symbol import TaiwanSymbol, parse_symbol, to_provider_symbol

logger = logging.getLogger(__name__)

YAHOO_METADATA = SourceMetadata(
    source_name="yahoo",
    volume_unit=VolumeUnit.SHARES,
    amount_unit=AmountUnit.UNAVAILABLE,
    price_semantics=PriceSemantics.RAW,
    rate_limit_rpm=30,
    supports_history=True,
    supports_etf=True,
    supports_tpex=True,
)


class YahooFinanceAdapter:
    """Yahoo Finance Taiwan chart adapter."""
    metadata = YAHOO_METADATA

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def fetch_daily(
        self,
        symbols: list[str | TaiwanSymbol],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType = "stock",
    ) -> pl.DataFrame:
        """Fetch daily raw records for given symbols via Yahoo Finance."""
        if not symbols:
            return pl.DataFrame()

        end_dt = end_time or datetime.now()
        start_dt = start_time or (end_dt - timedelta(days=365))
        p1 = int(start_dt.timestamp())
        p2 = int(end_dt.timestamp())

        frames: list[pl.DataFrame] = []

        del asset_type
        for sym in symbols:
            canonical_sym = parse_symbol(sym) if isinstance(sym, str) else sym

            provider_sym = to_provider_symbol(canonical_sym, "yahoo")
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(provider_sym)}?period1={p1}&period2={p2}&interval=1d"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    chart = payload.get("chart", {})
                    results = chart.get("result", [])
                    if not results:
                        logger.debug("Yahoo returned 0 results for %s", provider_sym)
                        continue

                    res = results[0]
                    timestamps = res.get("timestamp", [])
                    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
                    if not timestamps or not quote:
                        continue

                    # Construct rows
                    opens = quote.get("open", [])
                    highs = quote.get("high", [])
                    lows = quote.get("low", [])
                    closes = quote.get("close", [])
                    volumes = quote.get("volume", [])

                    rows: list[dict] = []
                    for i, ts in enumerate(timestamps):
                        # Ensure index bounds
                        op = opens[i] if i < len(opens) else None
                        hi = highs[i] if i < len(highs) else None
                        lo = lows[i] if i < len(lows) else None
                        cl = closes[i] if i < len(closes) else None
                        vol = volumes[i] if i < len(volumes) else None
                        if cl is None:
                            continue

                        rows.append({
                            "symbol": canonical_sym.canonical,
                            "date": ts,
                            "open": op,
                            "high": hi,
                            "low": lo,
                            "close": cl,
                            "volume": vol or 0,
                            "amount": None,
                            "quote_ts": int(ts * 1000),
                        })

                    normalized = normalize_taiwan_daily(
                        rows,
                        metadata=self.metadata,
                        default_symbol=canonical_sym,
                        provenance={
                            "provider": "yahoo", "source": "chart", "source_url": url,
                            "retrieved_at": datetime.now(TAIPEI).isoformat(), "trade_date": None,
                            "status": "third_party",
                        },
                    )
                    if not normalized.is_empty():
                        frames.append(normalized)
            except Exception as e:
                logger.warning("Yahoo Finance fetch failed for %s: %s", provider_sym, e)

        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
