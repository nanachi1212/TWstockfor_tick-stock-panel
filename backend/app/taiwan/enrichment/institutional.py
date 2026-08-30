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

import json
import logging
import urllib.request
from datetime import date, datetime
from typing import Any

from app.taiwan.enrichment.models import (
    DatasetType,
    InstitutionalFlow,
    SourceMeta,
    StalePolicy,
)
from app.taiwan.providers.taiwan_values import parse_integer

logger = logging.getLogger(__name__)


def _required_int(val: Any, field: str) -> int:
    parsed = parse_integer(val)
    if parsed is None:
        raise ValueError(f"missing required institutional field {field}")
    return parsed


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
                raise ValueError(f"TWSE institutional row has {len(r)} columns; expected at least 19")
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            fb = _required_int(r[2], "foreign_buy")
            fs = _required_int(r[3], "foreign_sell")
            fn = _required_int(r[4], "foreign_net")

            tb = _required_int(r[8], "investment_trust_buy")
            ts = _required_int(r[9], "investment_trust_sell")
            tn = _required_int(r[10], "investment_trust_net")

            official_dealer_net = _required_int(r[11], "dealer_net")
            dpb = _required_int(r[12], "dealer_proprietary_buy")
            dps = _required_int(r[13], "dealer_proprietary_sell")
            dpn = _required_int(r[14], "dealer_proprietary_net")
            dhb = _required_int(r[15], "dealer_hedge_buy")
            dhs = _required_int(r[16], "dealer_hedge_sell")
            dhn = _required_int(r[17], "dealer_hedge_net")
            db = dpb + dhb
            ds = dps + dhs
            dn = dpn + dhn

            official_net = _required_int(r[18], "total_net")
            computed_net = fn + tn + dn
            discrepancy = any((fn != fb - fs, tn != tb - ts, dpn != dpb - dps,
                               dhn != dhb - dhs, official_dealer_net != dn,
                               official_net != computed_net))
            status = "stale" if is_stale else "official"

            meta = SourceMeta(
                source="twse:t86",
                source_url=source_url,
                fetched_at=fetched_at,
                trade_date=trade_date,
                status=status,
                provider="twse",
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
            if len(r) < 24:
                raise ValueError(f"TPEx institutional row has {len(r)} columns; expected at least 24")
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            fb = _required_int(r[2], "foreign_buy")
            fs = _required_int(r[3], "foreign_sell")
            fn = _required_int(r[4], "foreign_net")

            tb = _required_int(r[11], "investment_trust_buy")
            ts = _required_int(r[12], "investment_trust_sell")
            tn = _required_int(r[13], "investment_trust_net")

            dpb = _required_int(r[14], "dealer_proprietary_buy")
            dps = _required_int(r[15], "dealer_proprietary_sell")
            dpn = _required_int(r[16], "dealer_proprietary_net")
            dhb = _required_int(r[17], "dealer_hedge_buy")
            dhs = _required_int(r[18], "dealer_hedge_sell")
            dhn = _required_int(r[19], "dealer_hedge_net")
            db = _required_int(r[20], "dealer_buy")
            ds = _required_int(r[21], "dealer_sell")
            official_dealer_net = _required_int(r[22], "dealer_net")
            dn = dpn + dhn

            official_net = _required_int(r[23], "total_net")
            computed_net = fn + tn + dn
            discrepancy = any((fn != fb - fs, tn != tb - ts, dpn != dpb - dps,
                               dhn != dhb - dhs, official_dealer_net != dn,
                               official_net != computed_net))
            status = "stale" if is_stale else "official"

            meta = SourceMeta(
                source="tpex:daily_trade",
                source_url=source_url,
                fetched_at=fetched_at,
                trade_date=trade_date,
                status=status,
                provider="tpex",
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
