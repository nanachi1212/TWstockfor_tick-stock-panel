"""FinMind API adapter for Taiwan market data.

Source semantics (verified with official documentation and live tests):
  - Base URL: https://api.finmindtrade.com/api/v4/data
  - Datasets:
      TaiwanStockPrice (OHLCV daily)
      TaiwanStockMonthRevenue (Monthly revenue)
      TaiwanStockFinancialStatements (Quarterly financial statements / EPS)
      TaiwanStockShareholding (Foreign investment shareholding ratio)
      TaiwanStockSecuritiesLending (Securities lending transaction details)
  - Rate limit: 300 requests/hour without token (5 rpm); 600 req/hr with verified token (10 rpm).
  - Security: Tokens are never logged or exposed in error messages.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import polars as pl

from app.data_providers.base import AssetType
from app.taiwan.providers.base import AmountUnit, PriceSemantics, SourceMetadata, VolumeUnit
from app.taiwan.providers.http import fetch_json
from app.taiwan.providers.normalizer import normalize_taiwan_daily
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.symbol import TaiwanSymbol, parse_symbol, to_provider_symbol

logger = logging.getLogger(__name__)

FINMIND_METADATA = SourceMetadata(
    source_name="finmind",
    volume_unit=VolumeUnit.SHARES,
    amount_unit=AmountUnit.TWD,
    price_semantics=PriceSemantics.RAW,
    rate_limit_rpm=5,
    supports_history=True,
    supports_etf=True,
    supports_tpex=True,
)


def sanitize_url(url: str) -> str:
    """Mask token query parameter in URL to prevent credential leakage in logs or exceptions."""
    return re.sub(r"([?&]token=)[^&]+", r"\1***", url)


class FinMindAdapter:
    """FinMind Taiwan API adapter supporting daily prices, fundamentals, and chips."""

    metadata = FINMIND_METADATA

    def __init__(self, token: str = "", timeout: int = 15) -> None:
        self.token = token.strip()
        self.timeout = timeout
        # A supplied token must belong to a registered account:
        # 300 req/hr (~5 rpm) anonymous vs 600 req/hr (~10 rpm) authenticated
        self.metadata = replace(FINMIND_METADATA, rate_limit_rpm=10 if self.token else 5)

    def _build_url(self, dataset: str, data_id: str, start_date: str | None = None, end_date: str | None = None) -> tuple[str, str]:
        """Construct API URL and sanitized URL."""
        params: dict[str, str] = {
            "dataset": dataset,
            "data_id": data_id,
        }
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if self.token:
            params["token"] = self.token

        query = urllib.parse.urlencode(params)
        raw_url = f"https://api.finmindtrade.com/api/v4/data?{query}"
        sanitized_url = sanitize_url(raw_url)
        return raw_url, sanitized_url

    def fetch_dataset(
        self,
        dataset: str,
        symbol_or_code: str | TaiwanSymbol,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch generic FinMind dataset with rate limiting and credential-safe error handling."""
        if isinstance(symbol_or_code, (str, TaiwanSymbol)):
            try:
                canonical = parse_symbol(symbol_or_code)
                raw_code = to_provider_symbol(canonical, "finmind")
            except Exception:
                raw_code = str(symbol_or_code).split(".")[0].strip()
        else:
            raw_code = str(symbol_or_code).strip()

        raw_url, sanitized_url = self._build_url(dataset, raw_code, start_date=start_date, end_date=end_date)

        try:
            payload = fetch_json(
                raw_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=self.timeout,
                rpm=self.metadata.rate_limit_rpm,
            )
            if not isinstance(payload, dict):
                logger.warning("FinMind malformed response for %s on %s", raw_code, dataset)
                return []

            status = payload.get("status")
            if status not in (200, None):
                msg = payload.get("msg", "")
                logger.warning("FinMind non-200 status %s for %s (%s): %s", status, raw_code, dataset, msg)
                return []

            rows = payload.get("data", [])
            if not isinstance(rows, list):
                logger.warning("FinMind payload 'data' is not a list for %s (%s)", raw_code, dataset)
                return []

            return rows
        except Exception as e:
            # Strictly do not expose raw_url which might contain the token
            logger.warning("FinMind fetch failed for %s (%s) on %s: %s", raw_code, dataset, sanitized_url, e)
            return []

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
            raw_url, sanitized_url = self._build_url("TaiwanStockPrice", raw_code, start_date=start_str, end_date=end_str)

            try:
                payload = fetch_json(
                    raw_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=self.timeout,
                    rpm=self.metadata.rate_limit_rpm,
                )
                rows = payload.get("data", []) if isinstance(payload, dict) else []
                if not rows:
                    logger.debug("FinMind returned 0 rows for %s", canonical_sym.canonical)
                    continue

                normalized = normalize_taiwan_daily(
                    rows,
                    metadata=self.metadata,
                    default_symbol=canonical_sym,
                    provenance={
                        "provider": "finmind",
                        "source": "TaiwanStockPrice",
                        "source_url": sanitized_url,
                        "retrieved_at": datetime.now(TAIPEI).isoformat(),
                        "trade_date": None,
                        "status": "third_party",
                    },
                )
                if not normalized.is_empty():
                    frames.append(normalized)
            except Exception as e:
                logger.warning("FinMind fetch failed for %s on %s: %s", canonical_sym.canonical, sanitized_url, e)

        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def fetch_month_revenue(self, symbol: str | TaiwanSymbol, start_date: str | None = None) -> list[dict[str, Any]]:
        """Fetch TaiwanStockMonthRevenue dataset."""
        return self.fetch_dataset("TaiwanStockMonthRevenue", symbol, start_date=start_date)

    def fetch_financial_statements(self, symbol: str | TaiwanSymbol, start_date: str | None = None) -> list[dict[str, Any]]:
        """Fetch TaiwanStockFinancialStatements dataset."""
        return self.fetch_dataset("TaiwanStockFinancialStatements", symbol, start_date=start_date)

    def fetch_shareholding(self, symbol: str | TaiwanSymbol, start_date: str | None = None) -> list[dict[str, Any]]:
        """Fetch TaiwanStockShareholding dataset."""
        return self.fetch_dataset("TaiwanStockShareholding", symbol, start_date=start_date)

    def fetch_securities_lending(self, symbol: str | TaiwanSymbol, start_date: str | None = None) -> list[dict[str, Any]]:
        """Fetch TaiwanStockSecuritiesLending dataset."""
        return self.fetch_dataset("TaiwanStockSecuritiesLending", symbol, start_date=start_date)
