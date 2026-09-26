"""Taiwan Fundamental and Extra Chips Aggregation Service.

Orchestrates:
  - Official Valuation (PER, PBR, Dividend Yield via TWSE/TPEx official open data)
  - FinMind Monthly Revenue (TaiwanStockMonthRevenue) with MoM, YoY, and 6-12M trend
  - FinMind Financial Statements (TaiwanStockFinancialStatements) with EPS, Revenue, GP, OP, Net Income
  - FinMind Foreign Shareholding (TaiwanStockShareholding) with ratio, 5D/20D changes, trend
  - FinMind Securities Lending (TaiwanStockSecuritiesLending) with volume, avg fee, 5D/20D, anomaly
  - Multi-tier Local Cache to prevent repetitive external API calls and rate-limit exhaustion.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.taiwan.detail_models import (
    SectionMeta,
    TaiwanExtraChipsData,
    TaiwanForeignShareholdingData,
    TaiwanFundamentalData,
    TaiwanMonthRevenueTrendItem,
    TaiwanProfitabilityData,
    TaiwanRevenueData,
    TaiwanSecuritiesLendingData,
    TaiwanValuationData,
)
from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.fundamentals import TaiwanOfficialFundamentals
from app.taiwan.providers.finmind_provider import FinMindAdapter
from app.taiwan.providers.taiwan_values import TAIPEI, parse_number
from app.taiwan.symbol import parse_symbol

logger = logging.getLogger(__name__)


class TaiwanFundamentalChipsService:
    """Service to acquire fundamental data and extra chips data for Taiwan securities."""

    def __init__(
        self,
        finmind_adapter: FinMindAdapter | None = None,
        cache: FinMindCache | None = None,
        official_fundamentals: TaiwanOfficialFundamentals | None = None,
    ) -> None:
        self.cache = cache or FinMindCache()
        self.official = official_fundamentals or TaiwanOfficialFundamentals()
        if finmind_adapter is not None:
            self.finmind = finmind_adapter
        else:
            from app.services import preferences
            token = preferences.get_finmind_token()
            self.finmind = FinMindAdapter(token=token)

    def _is_finmind_enabled(self) -> bool:
        from app.services import preferences
        return preferences.get_finmind_enabled()

    # ─────────────────────────────────────────────────────────────
    # 1. Valuation (Official Primary)
    # ─────────────────────────────────────────────────────────────

    def get_valuation(self, symbol: str, exchange: str) -> TaiwanValuationData:
        """Fetch valuation from official TWSE/TPEx open data with cache fallback."""
        canonical_sym = parse_symbol(symbol)
        sym_str = canonical_sym.canonical
        now_iso = datetime.now(TAIPEI).isoformat()

        # Check cache
        cached = self.cache.get("TaiwanValuation", sym_str)
        if cached is not None and cached.get("data"):
            data = cached["data"]
            pe = data.get("pe")
            pb = data.get("pb")
            dy = data.get("dividend_yield")
            status = cached.get("status", "available")
            return TaiwanValuationData(
                pe=pe,
                pb=pb,
                dividend_yield=dy,
                meta=SectionMeta(
                    source=f"official:{exchange.lower()}",
                    trade_date=cached.get("data_date"),
                    fetched_at=cached.get("fetched_at", now_iso),
                    status=status,
                    is_stale=False,
                ),
            )

        try:
            record = self.official.valuation(symbol, exchange)
            vals = record.values or {}
            pe = vals.get("pe")
            pb = vals.get("pb")
            dy = vals.get("dividend_yield")
            status = "available" if (pe is not None or pb is not None or dy is not None) else "unavailable"

            self.cache.set(
                "TaiwanValuation",
                sym_str,
                {"pe": pe, "pb": pb, "dividend_yield": dy},
                data_date=record.period_end or None,
                status=status,
            )

            return TaiwanValuationData(
                pe=pe,
                pb=pb,
                dividend_yield=dy,
                meta=SectionMeta(
                    source=f"official:{exchange.lower()}",
                    trade_date=record.period_end or None,
                    fetched_at=record.retrieved_at.isoformat() if record.retrieved_at else now_iso,
                    status=status,
                    is_stale=False,
                ),
            )
        except Exception as e:
            logger.debug("Official valuation lookup failed for %s: %s", symbol, e)
            return TaiwanValuationData(
                pe=None,
                pb=None,
                dividend_yield=None,
                meta=SectionMeta(
                    source=f"official:{exchange.lower()}",
                    fetched_at=now_iso,
                    status="unavailable",
                    fallback_reason=str(e),
                ),
            )


    # ─────────────────────────────────────────────────────────────
    # 2. Monthly Revenue (TaiwanStockMonthRevenue)
    # ─────────────────────────────────────────────────────────────

    def get_monthly_revenue(self, symbol: str) -> TaiwanRevenueData:
        """Fetch monthly revenue via cache or FinMind."""
        canonical_sym = parse_symbol(symbol)
        sym_str = canonical_sym.canonical
        dataset = "TaiwanStockMonthRevenue"
        now_iso = datetime.now(TAIPEI).isoformat()

        # Check Cache
        cached = self.cache.get(dataset, sym_str)
        raw_rows: list[dict[str, Any]] = []

        if cached is not None:
            raw_rows = cached.get("data") or []
            data_date = cached.get("data_date")
            status = cached.get("status", "available")
            if status != "available" or not raw_rows:
                return TaiwanRevenueData(
                    meta=SectionMeta(source="finmind", fetched_at=cached.get("fetched_at"), status="unavailable")
                )
        else:
            if not self._is_finmind_enabled():
                return TaiwanRevenueData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable", fallback_reason="FinMind provider disabled")
                )
            # Query FinMind
            raw_rows = self.finmind.fetch_month_revenue(sym_str)
            if not raw_rows:
                self.cache.set(dataset, sym_str, [], status="unavailable")
                return TaiwanRevenueData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable")
                )
            # Cache it
            latest_row_date = raw_rows[-1].get("date") if raw_rows else None
            self.cache.set(dataset, sym_str, raw_rows, data_date=latest_row_date, status="available")
            data_date = latest_row_date

        # Process rows
        return self._process_month_revenue(raw_rows, data_date, now_iso)

    def _process_month_revenue(self, rows: list[dict[str, Any]], data_date: str | None, fetched_at: str) -> TaiwanRevenueData:
        if not rows:
            return TaiwanRevenueData(
                meta=SectionMeta(source="finmind", fetched_at=fetched_at, status="unavailable")
            )

        # Sort by date ascending
        sorted_rows = sorted(
            [r for r in rows if r.get("date") and r.get("revenue") is not None],
            key=lambda x: str(x.get("date")),
        )
        if not sorted_rows:
            return TaiwanRevenueData(
                meta=SectionMeta(source="finmind", fetched_at=fetched_at, status="unavailable")
            )

        # Build map of (year, month) -> revenue
        rev_map: dict[tuple[int, int], float] = {}
        for r in sorted_rows:
            try:
                y = int(r.get("revenue_year") or str(r["date"])[:4])
                m = int(r.get("revenue_month") or str(r["date"])[5:7])
                val = float(r["revenue"])
                rev_map[(y, m)] = val
            except Exception:
                continue

        # Build trend for up to recent 12 items
        trend_items: list[TaiwanMonthRevenueTrendItem] = []
        recent_rows = sorted_rows[-12:]

        for r in recent_rows:
            try:
                y = int(r.get("revenue_year") or str(r["date"])[:4])
                m = int(r.get("revenue_month") or str(r["date"])[5:7])
                cur_val = float(r["revenue"])
                d_str = str(r["date"])

                # MoM
                prev_y = y if m > 1 else y - 1
                prev_m = m - 1 if m > 1 else 12
                prev_val = rev_map.get((prev_y, prev_m))
                mom = round((cur_val - prev_val) / prev_val * 100, 2) if (prev_val and prev_val > 0) else None

                # YoY
                yoy_val = rev_map.get((y - 1, m))
                yoy = round((cur_val - yoy_val) / yoy_val * 100, 2) if (yoy_val and yoy_val > 0) else None

                trend_items.append(
                    TaiwanMonthRevenueTrendItem(
                        date=d_str,
                        year_month=f"{y:04d}-{m:02d}",
                        revenue=cur_val,
                        mom=mom,
                        yoy=yoy,
                    )
                )
            except Exception:
                continue

        latest_item = trend_items[-1] if trend_items else None
        latest_rev = latest_item.revenue if latest_item else None
        latest_ym = latest_item.year_month if latest_item else None
        latest_mom = latest_item.mom if latest_item else None
        latest_yoy = latest_item.yoy if latest_item else None

        return TaiwanRevenueData(
            latest_revenue=latest_rev,
            latest_year_month=latest_ym,
            mom=latest_mom,
            yoy=latest_yoy,
            trend=trend_items,
            meta=SectionMeta(
                source="finmind:TaiwanStockMonthRevenue",
                trade_date=data_date or (latest_item.date if latest_item else None),
                fetched_at=fetched_at,
                status="available" if latest_rev is not None else "unavailable",
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # 3. Financial Statements (TaiwanStockFinancialStatements)
    # ─────────────────────────────────────────────────────────────

    def get_financial_statements(self, symbol: str) -> TaiwanProfitabilityData:
        """Fetch quarterly financial statement metrics via cache or FinMind."""
        canonical_sym = parse_symbol(symbol)
        sym_str = canonical_sym.canonical
        dataset = "TaiwanStockFinancialStatements"
        now_iso = datetime.now(TAIPEI).isoformat()

        # Check Cache
        cached = self.cache.get(dataset, sym_str)
        raw_rows: list[dict[str, Any]] = []

        if cached is not None:
            raw_rows = cached.get("data") or []
            data_date = cached.get("data_date")
            status = cached.get("status", "available")
            if status != "available" or not raw_rows:
                return TaiwanProfitabilityData(
                    meta=SectionMeta(source="finmind", fetched_at=cached.get("fetched_at"), status="unavailable")
                )
        else:
            if not self._is_finmind_enabled():
                return TaiwanProfitabilityData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable", fallback_reason="FinMind provider disabled")
                )
            raw_rows = self.finmind.fetch_financial_statements(sym_str)
            if not raw_rows:
                self.cache.set(dataset, sym_str, [], status="unavailable")
                return TaiwanProfitabilityData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable")
                )
            latest_row_date = raw_rows[-1].get("date") if raw_rows else None
            self.cache.set(dataset, sym_str, raw_rows, data_date=latest_row_date, status="available")
            data_date = latest_row_date

        return self._process_financial_statements(raw_rows, data_date, now_iso)

    def _process_financial_statements(self, rows: list[dict[str, Any]], data_date: str | None, fetched_at: str) -> TaiwanProfitabilityData:
        if not rows:
            return TaiwanProfitabilityData(
                meta=SectionMeta(source="finmind", fetched_at=fetched_at, status="unavailable")
            )

        # Group by date
        by_date: dict[str, dict[str, float]] = {}
        for r in rows:
            d = str(r.get("date") or "").strip()
            t = str(r.get("type") or "").strip()
            val = parse_number(r.get("value"))
            if d and t and val is not None:
                if d not in by_date:
                    by_date[d] = {}
                by_date[d][t] = val

        if not by_date:
            return TaiwanProfitabilityData(
                meta=SectionMeta(source="finmind", fetched_at=fetched_at, status="unavailable")
            )

        # Sort dates ascending
        sorted_dates = sorted(by_date.keys())
        latest_date = sorted_dates[-1]
        latest_metrics = by_date[latest_date]

        # Extract primary metrics
        latest_eps = latest_metrics.get("EPS")
        op_rev = latest_metrics.get("Revenue") or latest_metrics.get("OperatingRevenue")
        gross_profit = latest_metrics.get("GrossProfit")
        op_income = latest_metrics.get("OperatingIncome")
        net_income = latest_metrics.get("IncomeAfterTaxes") or latest_metrics.get("NetIncome")

        # Collect recent 4 quarters EPS (never sum them across quarters)
        recent_eps_list = []
        for d in sorted_dates[-4:]:
            d_eps = by_date[d].get("EPS")
            if d_eps is not None:
                recent_eps_list.append({"date": d, "eps": d_eps})

        status = "available" if (latest_eps is not None or net_income is not None) else "unavailable"

        return TaiwanProfitabilityData(
            quarter=latest_date,
            latest_eps=latest_eps,
            operating_revenue=op_rev,
            gross_profit=gross_profit,
            operating_income=op_income,
            net_income=net_income,
            recent_eps=recent_eps_list,
            meta=SectionMeta(
                source="finmind:TaiwanStockFinancialStatements",
                trade_date=data_date or latest_date,
                fetched_at=fetched_at,
                status=status,
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # 4. Foreign Shareholding (TaiwanStockShareholding)
    # ─────────────────────────────────────────────────────────────

    def get_foreign_shareholding(self, symbol: str) -> TaiwanForeignShareholdingData:
        """Fetch foreign investment shareholding ratio via cache or FinMind."""
        canonical_sym = parse_symbol(symbol)
        sym_str = canonical_sym.canonical
        dataset = "TaiwanStockShareholding"
        now_iso = datetime.now(TAIPEI).isoformat()

        # Check Cache
        cached = self.cache.get(dataset, sym_str)
        raw_rows: list[dict[str, Any]] = []

        if cached is not None:
            raw_rows = cached.get("data") or []
            data_date = cached.get("data_date")
            status = cached.get("status", "available")
            if status != "available" or not raw_rows:
                return TaiwanForeignShareholdingData(
                    meta=SectionMeta(source="finmind", fetched_at=cached.get("fetched_at"), status="unavailable")
                )
        else:
            if not self._is_finmind_enabled():
                return TaiwanForeignShareholdingData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable", fallback_reason="FinMind provider disabled")
                )
            raw_rows = self.finmind.fetch_shareholding(sym_str)
            if not raw_rows:
                self.cache.set(dataset, sym_str, [], status="unavailable")
                return TaiwanForeignShareholdingData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable")
                )
            latest_row_date = raw_rows[-1].get("date") if raw_rows else None
            self.cache.set(dataset, sym_str, raw_rows, data_date=latest_row_date, status="available")
            data_date = latest_row_date

        return self._process_shareholding(raw_rows, data_date, now_iso)

    def _process_shareholding(self, rows: list[dict[str, Any]], data_date: str | None, fetched_at: str) -> TaiwanForeignShareholdingData:
        if not rows:
            return TaiwanForeignShareholdingData(
                meta=SectionMeta(source="finmind", fetched_at=fetched_at, status="unavailable")
            )

        sorted_rows = sorted(
            [r for r in rows if r.get("date") and r.get("ForeignInvestmentSharesRatio") is not None],
            key=lambda x: str(x.get("date")),
        )
        if not sorted_rows:
            return TaiwanForeignShareholdingData(
                meta=SectionMeta(source="finmind", fetched_at=fetched_at, status="unavailable")
            )

        latest = sorted_rows[-1]
        cur_ratio = float(latest["ForeignInvestmentSharesRatio"])
        shares = int(latest["ForeignInvestmentShares"]) if latest.get("ForeignInvestmentShares") is not None else None

        # 5D ago
        idx_5d = max(0, len(sorted_rows) - 6)
        ratio_5d = float(sorted_rows[idx_5d]["ForeignInvestmentSharesRatio"]) if len(sorted_rows) > 1 else None
        chg_5d = round(cur_ratio - ratio_5d, 2) if ratio_5d is not None else None

        # 20D ago
        idx_20d = max(0, len(sorted_rows) - 21)
        ratio_20d = float(sorted_rows[idx_20d]["ForeignInvestmentSharesRatio"]) if len(sorted_rows) > 5 else None
        chg_20d = round(cur_ratio - ratio_20d, 2) if ratio_20d is not None else None

        # Trend direction
        if chg_20d is not None:
            if chg_20d >= 0.5:
                trend = "increasing"
            elif chg_20d <= -0.5:
                trend = "decreasing"
            else:
                trend = "flat"
        else:
            trend = "flat"

        return TaiwanForeignShareholdingData(
            ratio=cur_ratio,
            shares=shares,
            change_5d=chg_5d,
            change_20d=chg_20d,
            trend=trend,
            meta=SectionMeta(
                source="finmind:TaiwanStockShareholding",
                trade_date=data_date or str(latest.get("date")),
                fetched_at=fetched_at,
                status="available",
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # 5. Securities Lending (TaiwanStockSecuritiesLending)
    # ─────────────────────────────────────────────────────────────

    def get_securities_lending(self, symbol: str) -> TaiwanSecuritiesLendingData:
        """Fetch securities lending transaction details via cache or FinMind."""
        canonical_sym = parse_symbol(symbol)
        sym_str = canonical_sym.canonical
        dataset = "TaiwanStockSecuritiesLending"
        now_iso = datetime.now(TAIPEI).isoformat()

        # Check Cache
        cached = self.cache.get(dataset, sym_str)
        raw_rows: list[dict[str, Any]] = []

        if cached is not None:
            raw_rows = cached.get("data") or []
            data_date = cached.get("data_date")
            status = cached.get("status", "available")
            if status != "available" or not raw_rows:
                return TaiwanSecuritiesLendingData(
                    meta=SectionMeta(source="finmind", fetched_at=cached.get("fetched_at"), status="unavailable")
                )
        else:
            if not self._is_finmind_enabled():
                return TaiwanSecuritiesLendingData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable", fallback_reason="FinMind provider disabled")
                )
            raw_rows = self.finmind.fetch_securities_lending(sym_str)
            if not raw_rows:
                self.cache.set(dataset, sym_str, [], status="unavailable")
                return TaiwanSecuritiesLendingData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable")
                )
            latest_row_date = raw_rows[-1].get("date") if raw_rows else None
            self.cache.set(dataset, sym_str, raw_rows, data_date=latest_row_date, status="available")
            data_date = latest_row_date

        return self._process_securities_lending(raw_rows, data_date, now_iso)

    def _process_securities_lending(self, rows: list[dict[str, Any]], data_date: str | None, fetched_at: str) -> TaiwanSecuritiesLendingData:
        if not rows:
            return TaiwanSecuritiesLendingData(
                meta=SectionMeta(source="finmind", fetched_at=fetched_at, status="unavailable")
            )

        # Aggregate by date: daily_volume = sum(volume), fee_rate = weighted or avg
        daily_agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            d = str(r.get("date") or "").strip()
            vol = parse_number(r.get("volume")) or 0.0
            fee = parse_number(r.get("fee_rate")) or 0.0
            if d and vol > 0:
                if d not in daily_agg:
                    daily_agg[d] = {"volume": 0, "fee_sum": 0.0, "count": 0}
                daily_agg[d]["volume"] += int(vol)
                daily_agg[d]["fee_sum"] += fee * vol
                daily_agg[d]["count"] += 1

        if not daily_agg:
            return TaiwanSecuritiesLendingData(
                meta=SectionMeta(source="finmind:TaiwanStockSecuritiesLending", fetched_at=fetched_at, status="unavailable")
            )

        sorted_dates = sorted(daily_agg.keys())
        latest_d = sorted_dates[-1]
        latest_item = daily_agg[latest_d]
        latest_vol = latest_item["volume"]
        avg_fee = round(latest_item["fee_sum"] / latest_vol, 2) if latest_vol > 0 else 0.0

        # Rolling 5D volume
        recent_5_dates = sorted_dates[-5:]
        vol_5d = sum(daily_agg[d]["volume"] for d in recent_5_dates)

        # Rolling 20D volume
        recent_20_dates = sorted_dates[-20:]
        vol_20d = sum(daily_agg[d]["volume"] for d in recent_20_dates)

        # Anomaly detection: 5D daily average vs 20D daily average
        anomaly = "normal"
        if len(recent_20_dates) >= 10:
            avg_20d = vol_20d / len(recent_20_dates)
            avg_5d = vol_5d / len(recent_5_dates)
            if avg_20d > 0:
                if avg_5d >= 2.0 * avg_20d and avg_5d >= 50000:
                    anomaly = "abnormal_increase"
                elif avg_5d <= 0.3 * avg_20d:
                    anomaly = "abnormal_decrease"

        return TaiwanSecuritiesLendingData(
            latest_volume=latest_vol,
            avg_fee_rate=avg_fee,
            volume_5d=vol_5d,
            volume_20d=vol_20d,
            anomaly_status=anomaly,
            meta=SectionMeta(
                source="finmind:TaiwanStockSecuritiesLending",
                trade_date=data_date or latest_d,
                fetched_at=fetched_at,
                status="available",
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # Composite Methods
    # ─────────────────────────────────────────────────────────────

    def get_fundamentals_bundle(self, symbol: str, exchange: str) -> TaiwanFundamentalData:
        """Aggregate valuation, revenue, and profitability into unified fundamental bundle."""
        val = self.get_valuation(symbol, exchange)
        rev = self.get_monthly_revenue(symbol)
        prof = self.get_financial_statements(symbol)

        # Overall bundle status
        statuses = [val.meta.status if val.meta else "unavailable",
                    rev.meta.status if rev.meta else "unavailable",
                    prof.meta.status if prof.meta else "unavailable"]
        if all(s == "available" for s in statuses):
            status = "available"
        elif any(s == "available" for s in statuses):
            status = "partial"
        else:
            status = "unavailable"

        now_iso = datetime.now(TAIPEI).isoformat()
        return TaiwanFundamentalData(
            status=status,
            valuation=val,
            revenue=rev,
            profitability=prof,
            meta=SectionMeta(
                source="mixed:official+finmind",
                fetched_at=now_iso,
                status=status,
            ),
        )

    def get_extra_chips_bundle(self, symbol: str) -> TaiwanExtraChipsData:
        """Aggregate foreign shareholding and securities lending into unified chips bundle."""
        fsh = self.get_foreign_shareholding(symbol)
        sl = self.get_securities_lending(symbol)

        f_status = fsh.meta.status if fsh.meta else "unavailable"
        s_status = sl.meta.status if sl.meta else "unavailable"

        if f_status == "available" and s_status == "available":
            status = "available"
        elif f_status == "available" or s_status == "available":
            status = "partial"
        else:
            status = "unavailable"

        now_iso = datetime.now(TAIPEI).isoformat()
        return TaiwanExtraChipsData(
            status=status,
            foreign_shareholding=fsh,
            securities_lending=sl,
            meta=SectionMeta(
                source="finmind:chips",
                fetched_at=now_iso,
                status=status,
            ),
        )


_fundamental_chips_svc_singleton: TaiwanFundamentalChipsService | None = None


def get_fundamental_chips_service() -> TaiwanFundamentalChipsService:
    global _fundamental_chips_svc_singleton
    if _fundamental_chips_svc_singleton is None:
        _fundamental_chips_svc_singleton = TaiwanFundamentalChipsService()
    return _fundamental_chips_svc_singleton
