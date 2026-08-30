"""Official Taiwan Closing Quote & Snapshot Provider with Explicit Fallback.

Adapts:
  - TWSE: https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
  - TPEx: https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes

Design Principles:
  - Explicitly documents that this feed represents official closing snapshots, not tick-by-tick streaming.
  - Zero silent fallback: if official quote fails, fallbacks must record status='daily_kline_fallback'
    along with the precise fallback_reason in SourceMeta.
  - StalePolicy evaluation (Quote threshold: 4 hours past session close).
"""
from __future__ import annotations

from datetime import date, datetime
import json
import logging
import urllib.request
from typing import Any

from app.taiwan.enrichment.models import (
    DatasetType,
    MarketQuote,
    SourceMeta,
    StalePolicy,
)

logger = logging.getLogger(__name__)


def _clean_float(val: Any) -> float:
    if val is None:
        return 0.0
    s = str(val).strip().replace(",", "")
    if not s or s == "--":
        return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _clean_int(val: Any) -> int:
    if val is None:
        return 0
    s = str(val).strip().replace(",", "")
    if not s or s == "--":
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


class TaiwanOfficialQuoteProvider:
    """Provides official TWSE/TPEx daily closing quotes with transparent fallback."""

    TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"

    def __init__(self, twse_url: str | None = None, tpex_url: str | None = None) -> None:
        self.twse_url = twse_url or self.TWSE_URL
        self.tpex_url = tpex_url or self.TPEX_URL

    def parse_twse_rows(
        self,
        rows: list[dict[str, Any]],
        target_code: str,
        source_url: str,
    ) -> MarketQuote | None:
        for r in rows:
            if str(r.get("Code", "")).strip() == target_code:
                name = str(r.get("Name", "")).strip()
                close_px = _clean_float(r.get("ClosingPrice"))
                open_px = _clean_float(r.get("OpeningPrice")) or close_px
                high_px = _clean_float(r.get("HighestPrice")) or close_px
                low_px = _clean_float(r.get("LowestPrice")) or close_px
                change = _clean_float(r.get("Change"))
                vol = _clean_int(r.get("TradeVolume"))
                amt = _clean_float(r.get("TradeValue"))

                prev_close = round(close_px - change, 4) if close_px > 0 else 0.0
                change_pct = round((change / prev_close * 100.0), 2) if prev_close > 0 else 0.0

                fetched_at = datetime.now()
                meta = SourceMeta(
                    source="twse:STOCK_DAY_ALL",
                    source_url=source_url,
                    fetched_at=fetched_at,
                    trade_date=date.today(),
                    status="official_close",
                    is_realtime=False,
                    available_fields=("open", "high", "low", "close", "change", "volume", "amount"),
                    is_stale=StalePolicy.is_stale(DatasetType.QUOTE, date.today(), fetched_at),
                    source_type="official_open_data",
                    freshness_class="eod_snapshot",
                )

                return MarketQuote(
                    symbol=f"{target_code}.TWSE",
                    name=name,
                    price=close_px,
                    open=open_px,
                    high=high_px,
                    low=low_px,
                    previous_close=prev_close,
                    change=change,
                    change_pct=change_pct,
                    volume=vol,
                    amount=amt,
                    trade_time=datetime.now(),
                    meta=meta,
                )
        return None

    def parse_tpex_rows(
        self,
        rows: list[dict[str, Any]],
        target_code: str,
        source_url: str,
    ) -> MarketQuote | None:
        for r in rows:
            if str(r.get("SecuritiesCompanyCode", "")).strip() == target_code:
                name = str(r.get("CompanyName", "")).strip()
                close_px = _clean_float(r.get("Close"))
                open_px = _clean_float(r.get("Open")) or close_px
                high_px = _clean_float(r.get("High")) or close_px
                low_px = _clean_float(r.get("Low")) or close_px
                change = _clean_float(r.get("Change"))
                vol = _clean_int(r.get("TradingShares"))
                amt = _clean_float(r.get("TransactionAmount"))

                prev_close = round(close_px - change, 4) if close_px > 0 else 0.0
                change_pct = round((change / prev_close * 100.0), 2) if prev_close > 0 else 0.0

                fetched_at = datetime.now()
                meta = SourceMeta(
                    source="tpex:mainboard_quotes",
                    source_url=source_url,
                    fetched_at=fetched_at,
                    trade_date=date.today(),
                    status="official_close",
                    is_realtime=False,
                    available_fields=("open", "high", "low", "close", "change", "volume", "amount"),
                    is_stale=StalePolicy.is_stale(DatasetType.QUOTE, date.today(), fetched_at),
                    source_type="official_open_data",
                    freshness_class="eod_snapshot",
                )

                return MarketQuote(
                    symbol=f"{target_code}.TPEX",
                    name=name,
                    price=close_px,
                    open=open_px,
                    high=high_px,
                    low=low_px,
                    previous_close=prev_close,
                    change=change,
                    change_pct=change_pct,
                    volume=vol,
                    amount=amt,
                    trade_time=datetime.now(),
                    meta=meta,
                )
        return None

    def get_quote_with_fallback(
        self,
        symbol: str,
        name: str,
        live_rows: list[dict[str, Any]] | None = None,
        fallback_daily_row: dict[str, Any] | None = None,
    ) -> MarketQuote:
        """Fetch official quote; if unavailable or failed, transparently fall back to Daily K."""
        code, exchange = symbol.split(".")
        quote: MarketQuote | None = None

        if live_rows is not None:
            if exchange == "TWSE":
                quote = self.parse_twse_rows(live_rows, code, self.twse_url)
            elif exchange == "TPEX":
                quote = self.parse_tpex_rows(live_rows, code, self.tpex_url)

        if quote is not None:
            return quote

        # Explicit fallback to Daily K
        if fallback_daily_row:
            close_px = float(fallback_daily_row.get("close", 0.0))
            open_px = float(fallback_daily_row.get("open", close_px))
            high_px = float(fallback_daily_row.get("high", close_px))
            low_px = float(fallback_daily_row.get("low", close_px))
            change = float(fallback_daily_row.get("change", 0.0))
            change_pct = float(fallback_daily_row.get("change_pct", 0.0))
            vol = int(fallback_daily_row.get("volume", 0))
            amt = float(fallback_daily_row.get("amount", 0.0))
            prev_close = float(fallback_daily_row.get("pre_close", close_px - change))
            trade_dt = fallback_daily_row.get("date", date.today())

            meta = SourceMeta(
                source="fallback:daily_kline",
                source_url="internal:daily_storage",
                fetched_at=datetime.now(),
                trade_date=trade_dt,
                status="official_monthly_fallback",
                is_realtime=False,
                fallback_reason=f"Official snapshot unavailable for {symbol}; fell back to latest Daily K",
                available_fields=("open", "high", "low", "close", "change_pct", "volume"),
                is_stale=True,
                source_type="local_store",
                freshness_class="daily_cached",
            )

            return MarketQuote(
                symbol=symbol,
                name=name,
                price=close_px,
                open=open_px,
                high=high_px,
                low=low_px,
                previous_close=prev_close,
                change=change,
                change_pct=change_pct,
                volume=vol,
                amount=amt,
                trade_time=datetime.now(),
                meta=meta,
            )

        raise ValueError(f"Quote unavailable and no fallback row provided for {symbol}")
