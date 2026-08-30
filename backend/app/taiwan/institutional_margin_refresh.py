"""Incremental refresh services for Taiwan Institutional and Margin datasets.

Design:
  - Granularity-aware: Since official TWSE & TPEx feeds provide full-market
    snapshots for each trading day in 1 HTTP call per exchange, the refresh
    service operates at the trading-day level.
  - Resume-capable: Inspects persisted store dates and skips dates already complete.
  - Calendar-aware: Skips weekends and confirmed market holidays.
  - Rate-limit friendly: Bounded timeout and retry policy.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

import polars as pl

from app.taiwan.enrichment.institutional import (
    TaiwanInstitutionalProvider,
    TwseInstitutionalAdapter,
    TpexInstitutionalAdapter,
)
from app.taiwan.enrichment.margin import (
    TaiwanMarginProvider,
    TwseMarginAdapter,
    TpexMarginAdapter,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_BACKOFF_SEC = 1.0


def _fetch_json_with_retry(
    url: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF_SEC,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch JSON with minimal finite retry for transient HTTP/network errors.

    Retryable:
      - Timeouts (TimeoutError, urllib.error.URLError wrapping timeout)
      - Connection reset / network dropped
      - HTTP 429 (Too Many Requests)
      - HTTP 5xx (500, 502, 503, 504)

    Non-retryable:
      - HTTP 4xx (400, 401, 403, 404, etc. except 429)
      - JSON decode / malformed data errors
    """
    attempt = 0
    backoff = initial_backoff

    while attempt < max_attempts:
        attempt += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # 429 or 5xx are transient; other 4xx are permanent
            is_retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not is_retryable or attempt >= max_attempts:
                raise
            logger.warning(
                "HTTP %d on %s (attempt %d/%d), retrying in %.1fs...",
                exc.code, url, attempt, max_attempts, backoff,
            )
            time.sleep(backoff)
            backoff *= 2.0
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "Network/timeout error on %s: %s (attempt %d/%d), retrying in %.1fs...",
                url, exc, attempt, max_attempts, backoff,
            )
            time.sleep(backoff)
            backoff *= 2.0
        except json.JSONDecodeError:
            # Data parsing error is permanent; do not retry
            raise

    raise RuntimeError(f"Failed to fetch {url} after {max_attempts} attempts")


class TaiwanInstitutionalRefreshService:
    """Refreshes institutional flows from official TWSE/TPEx endpoints into TaiwanInstitutionalStore."""

    def __init__(
        self,
        store: TaiwanInstitutionalStore | None = None,
        provider: TaiwanInstitutionalProvider | None = None,
        calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self._store = store or TaiwanInstitutionalStore()
        self._provider = provider or TaiwanInstitutionalProvider()
        self._calendar = calendar or TaiwanTradingCalendar()

    def refresh_dates(
        self,
        start_date: date,
        end_date: date,
        force: bool = False,
    ) -> dict[str, Any]:
        """Fetch and persist market-wide institutional flows for all trading days in [start_date, end_date]."""
        available = set(self._store.available_dates()) if not force else set()
        stats = {
            "dates_requested": 0,
            "dates_fetched": 0,
            "dates_skipped": 0,
            "total_rows_written": 0,
            "failed_dates": [],
        }

        cur = start_date
        while cur <= end_date:
            # Skip confirmed non-trading days
            if self._calendar.is_trading_day(cur) is False:
                cur += timedelta(days=1)
                continue

            stats["dates_requested"] += 1

            if cur in available:
                stats["dates_skipped"] += 1
                cur += timedelta(days=1)
                continue

            try:
                # 1 HTTP call for TWSE, 1 HTTP call for TPEx
                flows_twse = self._provider.twse.parse_payload(
                    self._fetch_json(self._provider.twse.build_url(cur)),
                    cur,
                    self._provider.twse.build_url(cur),
                )
                flows_tpex = self._provider.tpex.parse_payload(
                    self._fetch_json(self._provider.tpex.build_url(cur)),
                    cur,
                    self._provider.tpex.build_url(cur),
                )
                all_flows = flows_twse + flows_tpex
                if all_flows:
                    rows = [
                        {
                            "symbol": f.symbol,
                            "date": cur,
                            "trade_date": cur,
                            "foreign_buy": f.foreign_buy,
                            "foreign_sell": f.foreign_sell,
                            "foreign_net": f.foreign_net,
                            "investment_trust_buy": f.investment_trust_buy,
                            "investment_trust_sell": f.investment_trust_sell,
                            "investment_trust_net": f.investment_trust_net,
                            "dealer_buy": f.dealer_buy,
                            "dealer_sell": f.dealer_sell,
                            "dealer_net": f.dealer_net,
                            "dealer_proprietary_buy": f.dealer_proprietary_buy,
                            "dealer_proprietary_sell": f.dealer_proprietary_sell,
                            "dealer_proprietary_net": f.dealer_proprietary_net,
                            "dealer_hedge_buy": f.dealer_hedge_buy,
                            "dealer_hedge_sell": f.dealer_hedge_sell,
                            "dealer_hedge_net": f.dealer_hedge_net,
                            "official_net": f.official_net,
                            "computed_net": f.computed_net,
                            "has_discrepancy": f.has_discrepancy,
                            "status": f.meta.status if f.meta else "official",
                            "source": f.meta.source if f.meta else "official",
                        }
                        for f in all_flows
                    ]
                    df = pl.DataFrame(rows)
                    written = self._store.write_batch(df, partition_date=cur)
                    stats["total_rows_written"] += written
                    stats["dates_fetched"] += 1
                else:
                    stats["dates_skipped"] += 1
            except Exception as exc:
                logger.warning("Institutional refresh failed for %s: %s", cur, exc)
                stats["failed_dates"].append({"date": str(cur), "error": str(exc)})

            cur += timedelta(days=1)

        return stats

    def _fetch_json(self, url: str) -> dict[str, Any]:
        return _fetch_json_with_retry(url, timeout=15.0)


class TaiwanMarginRefreshService:
    """Refreshes margin trading balances from official TWSE/TPEx endpoints into TaiwanMarginStore."""

    def __init__(
        self,
        store: TaiwanMarginStore | None = None,
        provider: TaiwanMarginProvider | None = None,
        calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self._store = store or TaiwanMarginStore()
        self._provider = provider or TaiwanMarginProvider()
        self._calendar = calendar or TaiwanTradingCalendar()

    def refresh_dates(
        self,
        start_date: date,
        end_date: date,
        force: bool = False,
    ) -> dict[str, Any]:
        """Fetch and persist market-wide margin records for all trading days in [start_date, end_date]."""
        available = set(self._store.available_dates()) if not force else set()
        stats = {
            "dates_requested": 0,
            "dates_fetched": 0,
            "dates_skipped": 0,
            "total_rows_written": 0,
            "failed_dates": [],
        }

        cur = start_date
        while cur <= end_date:
            # Skip confirmed non-trading days
            if self._calendar.is_trading_day(cur) is False:
                cur += timedelta(days=1)
                continue

            stats["dates_requested"] += 1

            if cur in available:
                stats["dates_skipped"] += 1
                cur += timedelta(days=1)
                continue

            try:
                # 1 HTTP call for TWSE, 1 HTTP call for TPEx
                margins_twse = self._provider.twse.parse_payload(
                    self._fetch_json(self._provider.twse.build_url(cur)),
                    cur,
                    self._provider.twse.build_url(cur),
                )
                margins_tpex = self._provider.tpex.parse_payload(
                    self._fetch_json(self._provider.tpex.build_url(cur)),
                    cur,
                    self._provider.tpex.build_url(cur),
                )
                all_margins = margins_twse + margins_tpex
                if all_margins:
                    rows = [
                        {
                            "symbol": m.symbol,
                            "date": cur,
                            "trade_date": cur,
                            "margin_previous_balance": m.margin_previous_balance,
                            "margin_buy": m.margin_buy,
                            "margin_sell": m.margin_sell,
                            "margin_cash_redemption": m.margin_cash_redemption,
                            "margin_balance": m.margin_balance,
                            "margin_change": m.margin_change,
                            "short_previous_balance": m.short_previous_balance,
                            "short_sell": m.short_sell,
                            "short_cover": m.short_cover,
                            "short_stock_redemption": m.short_stock_redemption,
                            "short_balance": m.short_balance,
                            "short_change": m.short_change,
                            "short_margin_ratio": m.short_margin_ratio,
                            "unit": m.unit,
                            "status": m.meta.status if m.meta else "official",
                            "source": m.meta.source if m.meta else "official",
                        }
                        for m in all_margins
                    ]
                    df = pl.DataFrame(rows)
                    written = self._store.write_batch(df, partition_date=cur)
                    stats["total_rows_written"] += written
                    stats["dates_fetched"] += 1
                else:
                    stats["dates_skipped"] += 1
            except Exception as exc:
                logger.warning("Margin refresh failed for %s: %s", cur, exc)
                stats["failed_dates"].append({"date": str(cur), "error": str(exc)})

            cur += timedelta(days=1)

        return stats

    def _fetch_json(self, url: str) -> dict[str, Any]:
        return _fetch_json_with_retry(url, timeout=15.0)
