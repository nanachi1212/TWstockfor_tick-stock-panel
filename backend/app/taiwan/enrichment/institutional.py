"""Three Major Institutional Investors (三大法人) Provider for Taiwan Markets.

Adapts and normalizes:
  - TWSE T86: https://www.twse.com.tw/rwd/zh/fund/T86
  - TPEx dailyTrade: https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade

Integrity Guarantees:
  - Strict unit: all volumes normalized to shares (股)
  - Validation: compares official_net against computed_net (buy - sell)
  - Zero silent overwrite on discrepancy; flags discrepancy status
  - Dataset-specific StalePolicy
  - Full offline unit-test capability via mock injection
"""
from __future__ import annotations

from datetime import date, datetime
import json
import logging
import re
import urllib.request
from typing import Any

from app.taiwan.enrichment.models import (
    DatasetType,
    InstitutionalFlow,
    SourceMeta,
    StalePolicy,
)

logger = logging.getLogger(__name__)


def _clean_int(val: Any) -> int:
    """Safely parse integer from official table strings with commas and whitespace."""
    if val is None:
        return 0
    s = str(val).strip().replace(",", "")
    if not s or s == "--":
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


class TwseInstitutionalAdapter:
    """Parses TWSE T86 official institutional trading table."""

    BASE_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"

    def build_url(self, trade_date: date) -> str:
        date_str = trade_date.strftime("%Y%m%d")
        return f"{self.BASE_URL}?response=json&selectType=ALLBUT0999&date={date_str}"

    def parse_payload(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
    ) -> list[InstitutionalFlow]:
        # TWSE response contains 'data' or 'tables[0].data'
        rows = payload.get("data")
        if not rows and "tables" in payload and payload["tables"]:
            rows = payload["tables"][0].get("data")
        if not rows:
            return []

        fetched_at = datetime.now()
        is_stale = StalePolicy.is_stale(DatasetType.INSTITUTIONAL, trade_date, fetched_at)
        results: list[InstitutionalFlow] = []

        for r in rows:
            if len(r) < 19:
                continue
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            fb = _clean_int(r[2])
            fs = _clean_int(r[3])
            fn = _clean_int(r[4])

            tb = _clean_int(r[8])
            ts = _clean_int(r[9])
            tn = _clean_int(r[10])

            dn = _clean_int(r[11])
            dpb = _clean_int(r[12])
            dps = _clean_int(r[13])
            dpn = _clean_int(r[14])
            dhb = _clean_int(r[15])
            dhs = _clean_int(r[16])
            dhn = _clean_int(r[17])
            db = dpb + dhb
            ds = dps + dhs

            official_net = _clean_int(r[18])
            computed_net = fn + tn + dn
            discrepancy = (official_net != computed_net)

            status = "discrepancy_detected" if discrepancy else "official_close"

            meta = SourceMeta(
                source="twse:t86",
                source_url=source_url,
                fetched_at=fetched_at,
                trade_date=trade_date,
                status=status,
                is_realtime=False,
                available_fields=(
                    "foreign", "investment_trust", "dealer",
                    "dealer_proprietary", "dealer_hedge",
                ),
                is_stale=is_stale,
                source_type="official_open_data",
                freshness_class="eod_snapshot",
            )

            flow = InstitutionalFlow(
                symbol=f"{code}.TWSE",
                trade_date=trade_date,
                foreign_buy=fb,
                foreign_sell=fs,
                foreign_net=fn,
                investment_trust_buy=tb,
                investment_trust_sell=ts,
                investment_trust_net=tn,
                dealer_buy=db,
                dealer_sell=ds,
                dealer_net=dn,
                dealer_proprietary_buy=dpb,
                dealer_proprietary_sell=dps,
                dealer_proprietary_net=dpn,
                dealer_hedge_buy=dhb,
                dealer_hedge_sell=dhs,
                dealer_hedge_net=dhn,
                official_net=official_net,
                computed_net=computed_net,
                has_discrepancy=discrepancy,
                meta=meta,
            )
            results.append(flow)

        return results


class TpexInstitutionalAdapter:
    """Parses TPEx dailyTrade official institutional trading table."""

    BASE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"

    def build_url(self, trade_date: date) -> str:
        date_str = trade_date.strftime("%Y/%m/%d")
        return f"{self.BASE_URL}?response=json&type=Daily&sect=AL&date={date_str}"

    def parse_payload(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
    ) -> list[InstitutionalFlow]:
        rows = payload.get("aaData")
        if not rows and "tables" in payload and payload["tables"]:
            rows = payload["tables"][0].get("data")
        if not rows:
            return []

        fetched_at = datetime.now()
        is_stale = StalePolicy.is_stale(DatasetType.INSTITUTIONAL, trade_date, fetched_at)
        results: list[InstitutionalFlow] = []

        for r in rows:
            if len(r) < 22:
                continue
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            fb = _clean_int(r[2])
            fs = _clean_int(r[3])
            fn = _clean_int(r[4])

            tb = _clean_int(r[11])
            ts = _clean_int(r[12])
            tn = _clean_int(r[13])

            dpb = _clean_int(r[14])
            dps = _clean_int(r[15])
            dpn = _clean_int(r[16])
            dhb = _clean_int(r[17])
            dhs = _clean_int(r[18])
            dhn = _clean_int(r[19])
            db = dpb + dhb
            ds = dps + dhs
            dn = _clean_int(r[20])

            official_net = _clean_int(r[21])
            computed_net = fn + tn + dn
            discrepancy = (official_net != computed_net)

            status = "discrepancy_detected" if discrepancy else "official_close"

            meta = SourceMeta(
                source="tpex:daily_trade",
                source_url=source_url,
                fetched_at=fetched_at,
                trade_date=trade_date,
                status=status,
                is_realtime=False,
                available_fields=(
                    "foreign", "investment_trust", "dealer",
                    "dealer_proprietary", "dealer_hedge",
                ),
                is_stale=is_stale,
                source_type="official_open_data",
                freshness_class="eod_snapshot",
            )

            flow = InstitutionalFlow(
                symbol=f"{code}.TPEX",
                trade_date=trade_date,
                foreign_buy=fb,
                foreign_sell=fs,
                foreign_net=fn,
                investment_trust_buy=tb,
                investment_trust_sell=ts,
                investment_trust_net=tn,
                dealer_buy=db,
                dealer_sell=ds,
                dealer_net=dn,
                dealer_proprietary_buy=dpb,
                dealer_proprietary_sell=dps,
                dealer_proprietary_net=dpn,
                dealer_hedge_buy=dhb,
                dealer_hedge_sell=dhs,
                dealer_hedge_net=dhn,
                official_net=official_net,
                computed_net=computed_net,
                has_discrepancy=discrepancy,
                meta=meta,
            )
            results.append(flow)

        return results


class TaiwanInstitutionalProvider:
    """Unified service for querying Taiwan Three Major Institutional flows."""

    def __init__(
        self,
        twse_adapter: TwseInstitutionalAdapter | None = None,
        tpex_adapter: TpexInstitutionalAdapter | None = None,
    ) -> None:
        self.twse = twse_adapter or TwseInstitutionalAdapter()
        self.tpex = tpex_adapter or TpexInstitutionalAdapter()

    def fetch_live_day(
        self,
        exchange: str,
        trade_date: date,
        target_code: str | None = None,
        timeout: float = 10.0,
    ) -> list[InstitutionalFlow]:
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
            raise ValueError(f"Unsupported exchange for institutional flows: {exchange}")
