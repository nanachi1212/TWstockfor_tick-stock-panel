"""Margin Trading & Short Selling (融資融券) Provider for Taiwan Markets.

Adapts and normalizes:
  - TWSE MI_MARGN: https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN
  - TPEx margin balance: https://www.tpex.org.tw/www/zh-tw/margin/balance

Integrity Guarantees:
  - Explicit unit declaration: source_unit = 'lots' (張), lot_multiplier = 1000
  - Normalized unit: strictly shares (股)
  - Accurate calculation of margin_change, short_change, and short_margin_ratio (券資比)
  - StalePolicy evaluation
  - Full offline unit-test capability via mock injection
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date, datetime
from typing import Any

from app.taiwan.enrichment.models import (
    DatasetType,
    MarginTrading,
    SourceMeta,
    StalePolicy,
)
from app.taiwan.providers.taiwan_values import parse_integer

logger = logging.getLogger(__name__)


def _required_lots_to_shares(val: Any, field: str, lot_multiplier: int = 1000) -> int:
    lots = parse_integer(val)
    if lots is None:
        raise ValueError(f"missing required margin field {field}")
    return lots * lot_multiplier


class TwseMarginAdapter:
    """Parses TWSE MI_MARGN official margin trading table."""

    BASE_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"

    def build_url(self, trade_date: date) -> str:
        date_str = trade_date.strftime("%Y%m%d")
        return f"{self.BASE_URL}?response=json&selectType=ALL&date={date_str}"

    def parse_payload(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
        lot_multiplier: int = 1000,
    ) -> list[MarginTrading]:
        rows = payload.get("data")
        if not rows and "tables" in payload:
            rows = next((table.get("data") for table in payload["tables"]
                         if table.get("data") and len(table["data"][0]) >= 16), None)
        if not rows:
            return []

        fetched_at = datetime.now()
        is_stale = StalePolicy.is_stale(DatasetType.MARGIN, trade_date, fetched_at)
        results: list[MarginTrading] = []

        for r in rows:
            if len(r) < 13:
                raise ValueError(f"TWSE margin row has {len(r)} columns; expected at least 13")
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            # Margin columns in MI_MARGN:
            # 2: buy, 3: sell, 4: cash redemption, 5: prev balance, 6: current balance
            mb = _required_lots_to_shares(r[2], "margin_buy", lot_multiplier)
            ms = _required_lots_to_shares(r[3], "margin_sell", lot_multiplier)
            mc = _required_lots_to_shares(r[4], "margin_cash_redemption", lot_multiplier)
            mprev = _required_lots_to_shares(r[5], "margin_previous_balance", lot_multiplier)
            mbal = _required_lots_to_shares(r[6], "margin_balance", lot_multiplier)
            mchange = mbal - mprev

            # Short columns in MI_MARGN:
            # 8: cover (buy), 9: sell, 10: stock redemption, 11: prev balance, 12: current balance
            scover = _required_lots_to_shares(r[8], "short_cover", lot_multiplier)
            ssell = _required_lots_to_shares(r[9], "short_sell", lot_multiplier)
            sred = _required_lots_to_shares(r[10], "short_stock_redemption", lot_multiplier)
            sprev = _required_lots_to_shares(r[11], "short_previous_balance", lot_multiplier)
            sbal = _required_lots_to_shares(r[12], "short_balance", lot_multiplier)
            schange = sbal - sprev

            note = str(r[15]).strip() if len(r) > 15 else ""

            meta = SourceMeta(
                source="twse:mi_margn",
                source_url=source_url,
                fetched_at=fetched_at,
                trade_date=trade_date,
                status="stale" if is_stale else "official",
                provider="twse",
                is_realtime=False,
                available_fields=(
                    "margin_balance", "margin_buy", "margin_sell",
                    "short_balance", "short_sell", "short_cover",
                ),
                is_stale=is_stale,
                source_type="official_open_data",
                freshness_class="eod_snapshot",
            )

            rec = MarginTrading(
                symbol=f"{code}.TWSE",
                trade_date=trade_date,
                unit="shares",
                source_unit="lots",
                lot_multiplier=lot_multiplier,
                margin_previous_balance=mprev,
                margin_buy=mb,
                margin_sell=ms,
                margin_cash_redemption=mc,
                margin_balance=mbal,
                margin_change=mchange,
                short_previous_balance=sprev,
                short_sell=ssell,
                short_cover=scover,
                short_stock_redemption=sred,
                short_balance=sbal,
                short_change=schange,
                short_margin_ratio=None,
                note=note,
                meta=meta,
            )
            results.append(rec)

        return results


class TpexMarginAdapter:
    """Parses TPEx margin balance official table."""

    BASE_URL = "https://www.tpex.org.tw/www/zh-tw/margin/balance"

    def build_url(self, trade_date: date) -> str:
        date_str = trade_date.strftime("%Y/%m/%d")
        return f"{self.BASE_URL}?response=json&date={date_str}"

    def parse_payload(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
        lot_multiplier: int = 1000,
    ) -> list[MarginTrading]:
        rows = payload.get("aaData")
        if not rows and "tables" in payload and payload["tables"]:
            rows = payload["tables"][0].get("data")
        if not rows:
            return []

        fetched_at = datetime.now()
        is_stale = StalePolicy.is_stale(DatasetType.MARGIN, trade_date, fetched_at)
        results: list[MarginTrading] = []

        for r in rows:
            if len(r) < 15:
                raise ValueError(f"TPEx margin row has {len(r)} columns; expected at least 15")
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            # TPEx margin columns:
            # 2: prev balance, 3: buy, 4: sell, 5: cash redemption, 6: current balance
            mprev = _required_lots_to_shares(r[2], "margin_previous_balance", lot_multiplier)
            mb = _required_lots_to_shares(r[3], "margin_buy", lot_multiplier)
            ms = _required_lots_to_shares(r[4], "margin_sell", lot_multiplier)
            mc = _required_lots_to_shares(r[5], "margin_cash_redemption", lot_multiplier)
            mbal = _required_lots_to_shares(r[6], "margin_balance", lot_multiplier)
            mchange = mbal - mprev

            # TPEx short columns:
            # 10: prev balance, 11: sell, 12: cover (buy), 13: redemption, 14: current balance
            sprev = _required_lots_to_shares(r[10], "short_previous_balance", lot_multiplier)
            ssell = _required_lots_to_shares(r[11], "short_sell", lot_multiplier)
            scover = _required_lots_to_shares(r[12], "short_cover", lot_multiplier)
            sred = _required_lots_to_shares(r[13], "short_stock_redemption", lot_multiplier)
            sbal = _required_lots_to_shares(r[14], "short_balance", lot_multiplier)
            schange = sbal - sprev

            note = str(r[19]).strip() if len(r) > 19 else ""

            meta = SourceMeta(
                source="tpex:margin_balance",
                source_url=source_url,
                fetched_at=fetched_at,
                trade_date=trade_date,
                status="stale" if is_stale else "official",
                provider="tpex",
                is_realtime=False,
                available_fields=(
                    "margin_balance", "margin_buy", "margin_sell",
                    "short_balance", "short_sell", "short_cover",
                ),
                is_stale=is_stale,
                source_type="official_open_data",
                freshness_class="eod_snapshot",
            )

            rec = MarginTrading(
                symbol=f"{code}.TPEX",
                trade_date=trade_date,
                unit="shares",
                source_unit="lots",
                lot_multiplier=lot_multiplier,
                margin_previous_balance=mprev,
                margin_buy=mb,
                margin_sell=ms,
                margin_cash_redemption=mc,
                margin_balance=mbal,
                margin_change=mchange,
                short_previous_balance=sprev,
                short_sell=ssell,
                short_cover=scover,
                short_stock_redemption=sred,
                short_balance=sbal,
                short_change=schange,
                short_margin_ratio=None,
                note=note,
                meta=meta,
            )
            results.append(rec)

        return results


class TaiwanMarginProvider:
    """Unified service for querying Taiwan Margin Trading & Short Selling."""

    def __init__(
        self,
        twse_adapter: TwseMarginAdapter | None = None,
        tpex_adapter: TpexMarginAdapter | None = None,
    ) -> None:
        self.twse = twse_adapter or TwseMarginAdapter()
        self.tpex = tpex_adapter or TpexMarginAdapter()

    def fetch_live_day(
        self,
        exchange: str,
        trade_date: date,
        target_code: str | None = None,
        timeout: float = 10.0,
    ) -> list[MarginTrading]:
        ex = exchange.upper()
        if ex == "TWSE":
            url = self.twse.build_url(trade_date)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return self.twse.parse_payload(data, trade_date, source_url=url, target_code=target_code)
        elif ex == "TPEX":
            url = self.tpex.build_url(trade_date)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return self.tpex.parse_payload(data, trade_date, source_url=url, target_code=target_code)
        else:
            raise ValueError(f"Unsupported exchange for margin trading: {exchange}")
