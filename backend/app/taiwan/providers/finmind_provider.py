"""FinMind API adapter for Taiwan market data.

Source semantics (verified with official documentation and live tests):
  - Base URL: https://api.finmindtrade.com/api/v4/data
  - Dataset: TaiwanStockPrice
  - Volume unit: shares (股)
  - Amount unit: TWD (新台幣元)
  - Price semantics: RAW (unadjusted)
  - Rate limit: 300 requests/hour without token; 600 req/hr with verified token.
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

FINMIND_METADATA = SourceMetadata(
    source_name="finmind",
    volume_unit=VolumeUnit.SHARES,
    amount_unit=AmountUnit.TWD,
    price_semantics=PriceSemantics.RAW,
    rate_limit_rpm=10,
    supports_history=True,
    supports_etf=True,
    supports_tpex=True,
)


class FinMindAdapter:
    """FinMind TaiwanStockPrice adapter."""
    metadata = FINMIND_METADATA

    def __init__(self, token: str = "", timeout: int = 15) -> None:
        self.token = token
        self.timeout = timeout

    def fetch_daily(
        self,
        symbols: list[str | TaiwanSymbol],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType = "stock",
    ) -> pl.DataFrame:
        """Fetch daily raw records for given symbols via FinMind."""
        if not symbols:
            return pl.DataFrame()

        end_dt = end_time or datetime.now()
        start_dt = start_time or (end_dt - timedelta(days=365))
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")

        frames: list[pl.DataFrame] = []

        del asset_type
        for sym in symbols:
            canonical_sym = parse_symbol(sym) if isinstance(sym, str) else sym

            raw_code = to_provider_symbol(canonical_sym, "finmind")
            params = {
                "dataset": "TaiwanStockPrice",
                "data_id": raw_code,
                "start_date": start_str,
                "end_date": end_str,
            }
            if self.token:
                params["token"] = self.token

            url = f"https://api.finmindtrade.com/api/v4/data?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    rows = payload.get("data", [])
                    if not rows:
                        logger.debug("FinMind returned 0 rows for %s", canonical_sym.canonical)
                        continue

                    normalized = normalize_taiwan_daily(
                        rows,
                        metadata=self.metadata,
                        default_symbol=canonical_sym,
                        provenance={
                            "provider": "finmind", "source": "TaiwanStockPrice", "source_url": url,
                            "retrieved_at": datetime.now(TAIPEI).isoformat(), "trade_date": None,
                            "status": "third_party",
                        },
                    )
                    if not normalized.is_empty():
                        frames.append(normalized)
            except Exception as e:
                logger.warning("FinMind fetch failed for %s: %s", canonical_sym.canonical, e)

        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
