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
import urllib.error
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


class FinMindError(Exception):
    """Base exception for FinMind API operations."""
    pass


class FinMindAuthError(FinMindError):
    """Authentication or authorization failure (e.g. invalid or revoked token)."""
    pass


class FinMindRateLimitError(FinMindError):
    """Rate limit exceeded (HTTP 429)."""
    pass


class FinMindNetworkError(FinMindError):
    """Network connection timeout or unreachable host."""
    pass


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
        raise_for_status: bool = False,
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
                if raise_for_status:
                    raise FinMindError("FinMind API 回傳非預期格式回應")
                return []

            status = payload.get("status")
            if status not in (200, None):
                msg = str(payload.get("msg", ""))
                logger.warning("FinMind non-200 status %s for %s (%s): %s", status, raw_code, dataset, msg)
                if raise_for_status:
                    if status in (401, 403) or any(k in msg.lower() for k in ("token", "auth", "invalid")):
                        raise FinMindAuthError(f"認證失敗: {msg or status}")
                    elif status == 429 or "rate limit" in msg.lower():
                        raise FinMindRateLimitError(f"頻率限制: {msg or status}")
                    else:
                        raise FinMindError(f"FinMind API 狀態錯誤 {status}: {msg}")
                return []

            rows = payload.get("data", [])
            if not isinstance(rows, list):
                logger.warning("FinMind payload 'data' is not a list for %s (%s)", raw_code, dataset)
                if raise_for_status:
                    raise FinMindError("FinMind 回應中的 data 欄位格式不正確")
                return []

            return rows
        except (FinMindAuthError, FinMindRateLimitError, FinMindError):
            raise
        except urllib.error.HTTPError as e:
            logger.warning("FinMind HTTP error %d on %s: %s", e.code, sanitized_url, e)
            if raise_for_status:
                if e.code in (401, 403):
                    raise FinMindAuthError("FinMind 認證失敗，Token 無效或未被授權") from e
                elif e.code == 429:
                    raise FinMindRateLimitError("FinMind API 已達請求頻率上限 (Rate Limit)") from e
                else:
                    raise FinMindError(f"HTTP 錯誤 {e.code}") from e
            return []
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as e:
            logger.warning("FinMind network error on %s: %s", sanitized_url, e)
            if raise_for_status:
                raise FinMindNetworkError("連線至 FinMind 伺服器失敗或逾時") from e
            return []
        except Exception as e:
            # Strictly do not expose raw_url which might contain the token
            logger.warning("FinMind fetch failed for %s (%s) on %s: %s", raw_code, dataset, sanitized_url, e)
            if raise_for_status:
                safe_err = sanitize_url(str(e))
                raise FinMindError(f"FinMind 請求異常: {safe_err}") from e
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

    def fetch_month_revenue(
        self,
        symbol: str | TaiwanSymbol,
        start_date: str | None = None,
        raise_for_status: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch TaiwanStockMonthRevenue dataset."""
        return self.fetch_dataset("TaiwanStockMonthRevenue", symbol, start_date=start_date, raise_for_status=raise_for_status)

    def fetch_financial_statements(
        self,
        symbol: str | TaiwanSymbol,
        start_date: str | None = None,
        raise_for_status: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch TaiwanStockFinancialStatements dataset."""
        return self.fetch_dataset("TaiwanStockFinancialStatements", symbol, start_date=start_date, raise_for_status=raise_for_status)

    def fetch_shareholding(
        self,
        symbol: str | TaiwanSymbol,
        start_date: str | None = None,
        raise_for_status: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch TaiwanStockShareholding dataset."""
        return self.fetch_dataset("TaiwanStockShareholding", symbol, start_date=start_date, raise_for_status=raise_for_status)

    def fetch_securities_lending(
        self,
        symbol: str | TaiwanSymbol,
        start_date: str | None = None,
        raise_for_status: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch TaiwanStockSecuritiesLending dataset."""
        return self.fetch_dataset("TaiwanStockSecuritiesLending", symbol, start_date=start_date, raise_for_status=raise_for_status)

    def test_connection(self, probe_symbol: str = "2330") -> dict[str, Any]:
        """Diagnostic probe for FinMind connectivity.

        Guarantees:
        - Accurately distinguishes Auth / Rate-limit / Network errors / No data.
        - Fails closed: an empty result from both benchmark probes returns ok=False.
        - Never leaks token in error messages or response.
        """
        now = datetime.now()
        start_price = (now - timedelta(days=60)).strftime("%Y-%m-%d")
        start_rev = (now - timedelta(days=365)).strftime("%Y-%m-%d")

        try:
            # Probe 1: Recent daily price for benchmark stock
            rows = self.fetch_dataset(
                "TaiwanStockPrice",
                probe_symbol,
                start_date=start_price,
                raise_for_status=True,
            )
            if rows:
                return {"ok": True, "message": "FinMind API 連線成功，已取得資料。"}

            # Probe 2: Monthly revenue for benchmark stock
            rev_rows = self.fetch_month_revenue(
                probe_symbol,
                start_date=start_rev,
                raise_for_status=True,
            )
            if rev_rows:
                return {"ok": True, "message": "FinMind API 連線成功，已取得營收資料。"}

            # Both probes succeeded without network/auth error but returned 0 rows:
            # Must NOT report success when data cannot be proven available!
            return {
                "ok": False,
                "error_type": "no_data",
                "message": f"FinMind API 連線正常但未回傳基準標的 ({probe_symbol}) 任何數據，未能驗證資料可用性。",
            }
        except FinMindAuthError as e:
            return {
                "ok": False,
                "error_type": "auth",
                "message": f"FinMind 認證失敗: Token 無效或未被授權 ({e})。",
            }
        except FinMindRateLimitError as e:
            return {
                "ok": False,
                "error_type": "rate_limit",
                "message": f"FinMind 請求已達頻率上限 (Rate Limit): {e}，請稍後再試。",
            }
        except FinMindNetworkError as e:
            return {
                "ok": False,
                "error_type": "network",
                "message": f"FinMind 連線失敗: {e}，請檢查網路連線。",
            }
        except Exception as e:
            safe_msg = sanitize_url(str(e))
            return {
                "ok": False,
                "error_type": "unknown",
                "message": f"FinMind 連線測試失敗: {safe_msg}",
            }
