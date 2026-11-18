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
import math
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.taiwan.detail_models import (
    SectionMeta,
    TaiwanBigHolderData,
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
from app.taiwan.fundamentals import TaiwanFundamentalStore, TaiwanOfficialFundamentals
from app.taiwan.pit_cutoff import (
    filter_daily_records_as_of,
    filter_financial_statements_as_of,
    filter_month_revenue_as_of,
    resolve_as_of_date,
)
from app.taiwan.providers.finmind_provider import FinMindAdapter, FinMindError
from app.taiwan.providers.taiwan_values import TAIPEI, parse_number
from app.taiwan.symbol import parse_symbol

logger = logging.getLogger(__name__)

_MARKET_DATES_TTL = 60.0
_market_dates_memo: dict[str, tuple[float, list[date]]] = {}


_WINDOW_DAYS = {"TaiwanStockMonthRevenue": 550, "TaiwanStockShareholding": 180, "TaiwanStockSecuritiesLending": 180}


def finmind_query_start(dataset: str, cutoff: date) -> str:
    return (cutoff - timedelta(days=_WINDOW_DAYS[dataset])).isoformat()


def cached_finmind_window(dataset: str, cached: dict[str, Any] | None, start: str) -> dict[str, Any] | None:
    """Project a fresh cache entry onto the same lower date bound as the provider query."""
    if not cached or not cached.get("data") or cached.get("query_start") == start:
        return cached
    rows = [row for row in cached["data"] if str(row.get("date") or "") >= start]
    if rows:
        return {**cached, "data": rows}
    return {**cached, "data": [], "data_date": None, "status": "unavailable", "error_msg": f"{dataset} returned no rows"}


def _market_dates() -> list[date]:
    """Trading days: the dates the local daily store holds, ascending."""
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.data_root import taiwan_data_root

    root = str(taiwan_data_root())
    hit = _market_dates_memo.get(root)
    if hit is not None and time.monotonic() - hit[0] < _MARKET_DATES_TTL:
        return hit[1]
    dates = TaiwanDailyStore().available_dates()
    # ponytail: 60 s memo so a screener run (one call per symbol) lists the store once;
    # callers that already hold the sessions pass trading_dates instead.
    _market_dates_memo[root] = (time.monotonic(), dates)
    return dates


def _taipei_date(stamp: str | None) -> str | None:
    """Taipei calendar date of an ISO timestamp; naive stamps are UTC like FinMindCache writes."""
    try:
        parsed = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(TAIPEI).date().isoformat()


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
        self._custom_adapter = finmind_adapter is not None
        if finmind_adapter is not None:
            self.finmind = finmind_adapter
        else:
            from app.services import preferences
            token = preferences.get_finmind_token()
            self.finmind = FinMindAdapter(token=token)

    def _get_finmind_adapter(self) -> FinMindAdapter:
        """Resolve current FinMind adapter dynamically to reflect credential changes without restart."""
        if self._custom_adapter:
            return self.finmind
        from app.services import preferences
        current_token = preferences.get_finmind_token()
        if self.finmind.token != current_token:
            self.finmind = FinMindAdapter(token=current_token)
        return self.finmind

    def _to_canonical_str(self, symbol: str) -> str:
        """Safely normalize symbol to canonical format or fallback to raw string."""
        try:
            return parse_symbol(symbol).canonical
        except Exception:
            return str(symbol).strip()

    def _is_finmind_enabled(self) -> bool:
        from app.services import preferences
        return preferences.get_finmind_enabled()

    @staticmethod
    def _cutoff_date(as_of: date | str | None) -> date:
        if as_of is None:
            return datetime.now(TAIPEI).date()
        cutoff = resolve_as_of_date(as_of)
        if cutoff is None:
            raise ValueError("Invalid as_of date")
        return cutoff

    @staticmethod
    def _cached_unavailable_reason(dataset: str, cached: dict[str, Any]) -> str:
        return str(cached.get("error_msg") or f"{dataset} cached response is unavailable")

    # ─────────────────────────────────────────────────────────────
    # 1. Valuation (Official Primary)
    # ─────────────────────────────────────────────────────────────

    def prime_valuation_cache(self, exchange: str) -> int:
        """Fill the per-symbol valuation cache from one official snapshot request."""
        records = self.official.valuation_snapshot(exchange)
        for record in records:
            vals = record.values or {}
            pe, pb, dy = vals.get("pe"), vals.get("pb"), vals.get("dividend_yield")
            self.cache.set(
                "TaiwanValuation", self._to_canonical_str(record.symbol),
                {"pe": pe, "pb": pb, "dividend_yield": dy},
                data_date=record.period_end or None,
                status="available" if (pe is not None or pb is not None or dy is not None) else "unavailable",
            )
        return len(records)

    def get_valuation(
        self, symbol: str, exchange: str, as_of: date | str | None = None,
    ) -> TaiwanValuationData:
        """Use dated local observations for historical queries, and the official feed for current queries."""
        sym_str = self._to_canonical_str(symbol)
        now_iso = datetime.now(TAIPEI).isoformat()

        # Check cache
        cached = self.cache.get("TaiwanValuation", sym_str)
        if as_of is not None:
            return self.historical_valuation(sym_str, exchange, resolve_as_of_date(as_of), cached)
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


    @staticmethod
    def historical_valuation(
        symbol: str, exchange: str, cutoff: date | None, cached: dict[str, Any] | None,
        *, observed_by: date | None = None,
    ) -> TaiwanValuationData:
        from app.config import settings

        unavailable = TaiwanValuationData(meta=SectionMeta(
            source=f"official:{exchange.lower()}", status="unavailable",
            fallback_reason="no dated valuation available as of cutoff",
        ))
        if cutoff is None:
            return unavailable
        observed_by = observed_by or cutoff
        best_key = None
        result = unavailable
        if cached and cached.get("status", "available") == "available" and isinstance(cached.get("data"), dict):
            day = resolve_as_of_date(cached.get("data_date"))
            try:
                stamp = datetime.fromisoformat(cached.get("fetched_at") or "")
            except (TypeError, ValueError):
                stamp = None
            if stamp is not None and stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            values = {k: parse_number(cached["data"].get(k)) for k in ("pe", "pb", "dividend_yield")}
            if (day is not None and day <= cutoff and stamp is not None
                    and stamp.astimezone(TAIPEI).date() <= observed_by and any(v is not None for v in values.values())):
                best_key = (day, stamp)
                result = TaiwanValuationData(**values, meta=SectionMeta(
                    source=f"official:{exchange.lower()}", trade_date=day.isoformat(),
                    fetched_at=cached["fetched_at"], status="available",
                ))

        for record in TaiwanFundamentalStore(settings.data_dir).load():
            if record.symbol != symbol or record.dataset != "valuation":
                continue
            if (record.status not in {"official", "data_insufficient"}
                    or record.retrieved_at.astimezone(TAIPEI).date() > observed_by):
                continue
            day = resolve_as_of_date(record.period_end)
            if day is None or day > cutoff or (best_key is not None and (day, record.retrieved_at) <= best_key):
                continue
            if not any((record.values or {}).get(key) is not None for key in ("pe", "pb", "dividend_yield")):
                continue
            best_key = (day, record.retrieved_at)
            result = TaiwanValuationData(**(record.values or {}), meta=SectionMeta(
                source=record.source, trade_date=day.isoformat(), fetched_at=record.retrieved_at.isoformat(),
                status="available",
            ))
        return result

    # ─────────────────────────────────────────────────────────────
    # 2. Monthly Revenue (TaiwanStockMonthRevenue)
    # ─────────────────────────────────────────────────────────────

    def _range_payload(
        self, symbol: str, dataset: str, as_of: date | str | None,
        fetch: Callable[[FinMindAdapter, str, str], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        start = finmind_query_start(dataset, self._cutoff_date(as_of))
        symbol = self._to_canonical_str(symbol)
        stored = self.cache.get(dataset, symbol)
        cached = cached_finmind_window(dataset, stored, start)
        if cached is not None:
            covered = cached.get("query_start")
            if (isinstance(covered, str) and covered <= start) or (covered is None and as_of is None):
                return cached
        now = datetime.now(UTC).isoformat()
        if not self._is_finmind_enabled():
            return cached if cached is not None else {
                "data": [], "fetched_at": now, "source": "finmind", "error_msg": "FinMind provider disabled",
            }
        try:
            rows = fetch(self._get_finmind_adapter(), symbol, start)
            status = "available" if rows else "unavailable"
            reason = None if rows else f"{dataset} returned no rows"
        except FinMindError as exc:
            rows, status, reason = [], "error", f"{dataset} request failed: {exc}"
        data_date = max((row["date"] for row in rows if isinstance(row.get("date"), str)), default=None)
        if rows or not (stored and stored.get("data")):
            self.cache.set(dataset, symbol, rows, query_start=start, fetched_at=now,
                           data_date=data_date, status=status, error_msg=reason)
        return {"data": rows, "data_date": data_date, "fetched_at": now, "status": status, "error_msg": reason}

    def get_monthly_revenue(self, symbol: str, as_of: date | str | None = None) -> TaiwanRevenueData:
        dataset = "TaiwanStockMonthRevenue"
        payload = self._range_payload(
            symbol, dataset, as_of,
            lambda adapter, code, start: adapter.fetch_month_revenue(code, start_date=start, raise_for_status=True),
        )
        if payload.get("status", "available") != "available" or not payload.get("data"):
            return TaiwanRevenueData(meta=SectionMeta(
                source=payload.get("source", "finmind"), fetched_at=payload.get("fetched_at"), status="unavailable",
                fallback_reason=self._cached_unavailable_reason(dataset, payload),
            ))
        return self._process_month_revenue(payload["data"], payload.get("data_date"), payload["fetched_at"], as_of=as_of)

    @staticmethod
    def _process_month_revenue(
        rows: list[dict[str, Any]],
        data_date: str | None,
        fetched_at: str,
        as_of: date | str | None = None,
    ) -> TaiwanRevenueData:
        # Enforce point-in-time cutoff
        rows = filter_month_revenue_as_of(rows, as_of)
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
                trade_date=latest_item.date if latest_item else (data_date if as_of is None else None),
                fetched_at=fetched_at,
                status="available" if latest_rev is not None else "unavailable",
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # 3. Financial Statements (TaiwanStockFinancialStatements)
    # ─────────────────────────────────────────────────────────────

    def get_financial_statements(self, symbol: str, as_of: date | str | None = None) -> TaiwanProfitabilityData:
        """Fetch quarterly financial statement metrics via cache or FinMind with optional point-in-time cutoff."""
        sym_str = self._to_canonical_str(symbol)
        dataset = "TaiwanStockFinancialStatements"
        cutoff = self._cutoff_date(as_of)
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
                    meta=SectionMeta(
                        source=f"finmind:{dataset}", trade_date=data_date, fetched_at=cached.get("fetched_at"),
                        status="unavailable", fallback_reason=self._cached_unavailable_reason(dataset, cached),
                    )
                )
        else:
            if not self._is_finmind_enabled():
                return TaiwanProfitabilityData(
                    meta=SectionMeta(source="finmind", fetched_at=now_iso, status="unavailable", fallback_reason="FinMind provider disabled")
                )
            adapter = self._get_finmind_adapter()
            start_date = (cutoff - timedelta(days=5 * 366)).isoformat()
            try:
                raw_rows = adapter.fetch_financial_statements(
                    sym_str, start_date=start_date, raise_for_status=True,
                )
            except FinMindError as exc:
                reason = f"{dataset} request failed: {exc}"
                self.cache.set(dataset, sym_str, [], status="error", error_msg=reason)
                return TaiwanProfitabilityData(
                    meta=SectionMeta(source=f"finmind:{dataset}", fetched_at=now_iso,
                                     status="unavailable", fallback_reason=reason)
                )
            if not raw_rows:
                reason = f"{dataset} returned no rows"
                self.cache.set(dataset, sym_str, [], status="unavailable", error_msg=reason)
                return TaiwanProfitabilityData(
                    meta=SectionMeta(source=f"finmind:{dataset}", fetched_at=now_iso,
                                     status="unavailable", fallback_reason=reason)
                )
            latest_row_date = raw_rows[-1].get("date") if raw_rows else None
            self.cache.set(dataset, sym_str, raw_rows, data_date=latest_row_date, status="available")
            data_date = latest_row_date

        fetched_at = cached["fetched_at"] if cached is not None else datetime.now(TAIPEI).isoformat()
        return self._process_financial_statements(raw_rows, data_date, fetched_at, as_of=as_of)

    @staticmethod
    def _process_financial_statements(
        rows: list[dict[str, Any]],
        data_date: str | None,
        fetched_at: str,
        as_of: date | str | None = None,
    ) -> TaiwanProfitabilityData:
        # Enforce point-in-time cutoff
        rows = filter_financial_statements_as_of(rows, as_of)
        if not rows:
            return TaiwanProfitabilityData(
                meta=SectionMeta(source="finmind:TaiwanStockFinancialStatements", trade_date=data_date, fetched_at=fetched_at,
                                 status="unavailable", fallback_reason="no financial statements available as of cutoff")
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
                meta=SectionMeta(source="finmind:TaiwanStockFinancialStatements", trade_date=data_date, fetched_at=fetched_at,
                                 status="unavailable", fallback_reason="financial statements contain no usable metrics")
            )

        # Sort dates ascending
        sorted_dates = sorted(by_date.keys())
        latest_date = sorted_dates[-1]
        latest_metrics = by_date[latest_date]

        # Extract primary metrics
        latest_eps = latest_metrics.get("EPS")
        op_rev = latest_metrics.get("Revenue", latest_metrics.get("OperatingRevenue"))
        gross_profit = latest_metrics.get("GrossProfit")
        op_income = latest_metrics.get("OperatingIncome")
        net_income = latest_metrics.get("IncomeAfterTaxes", latest_metrics.get("NetIncome"))

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
                fallback_reason="latest financial statement contains no EPS" if latest_eps is None else None,
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # 4. Foreign Shareholding (TaiwanStockShareholding)
    # ─────────────────────────────────────────────────────────────

    def get_foreign_shareholding(self, symbol: str, as_of: date | str | None = None) -> TaiwanForeignShareholdingData:
        dataset = "TaiwanStockShareholding"
        payload = self._range_payload(
            symbol, dataset, as_of,
            lambda adapter, code, start: adapter.fetch_shareholding(code, start_date=start, raise_for_status=True),
        )
        if payload.get("status", "available") != "available" or not payload.get("data"):
            return TaiwanForeignShareholdingData(meta=SectionMeta(
                source=payload.get("source", f"finmind:{dataset}"), fetched_at=payload.get("fetched_at"), status="unavailable",
                trade_date=payload.get("data_date") if as_of is None else None,
                fallback_reason=self._cached_unavailable_reason(dataset, payload),
            ))
        return self._process_shareholding(payload["data"], payload.get("data_date"), payload["fetched_at"], as_of=as_of)

    @staticmethod
    def _process_shareholding(
        rows: list[dict[str, Any]],
        data_date: str | None,
        fetched_at: str,
        as_of: date | str | None = None,
    ) -> TaiwanForeignShareholdingData:
        # Enforce point-in-time cutoff
        rows = filter_daily_records_as_of(rows, as_of)
        if as_of is not None:
            data_date = None  # The cache date does not establish a historical observation.
        if not rows:
            return TaiwanForeignShareholdingData(
                meta=SectionMeta(source="finmind:TaiwanStockShareholding", trade_date=data_date, fetched_at=fetched_at,
                                 status="unavailable", fallback_reason="no shareholding rows available as of cutoff")
            )

        sorted_rows = sorted(
            [r for r in rows if r.get("date") and r.get("ForeignInvestmentSharesRatio") is not None],
            key=lambda x: str(x.get("date")),
        )
        if not sorted_rows:
            return TaiwanForeignShareholdingData(
                meta=SectionMeta(source="finmind:TaiwanStockShareholding", trade_date=data_date, fetched_at=fetched_at,
                                 status="unavailable", fallback_reason="shareholding rows contain no usable foreign ratio")
            )

        latest = sorted_rows[-1]
        cur_ratio = float(latest["ForeignInvestmentSharesRatio"])
        shares = int(latest["ForeignInvestmentShares"]) if latest.get("ForeignInvestmentShares") is not None else None

        # 5D / 20D ago: only when the history really spans that many rows (no shorter-window substitute)
        chg_5d = None
        if len(sorted_rows) >= 6:
            chg_5d = round(cur_ratio - float(sorted_rows[-6]["ForeignInvestmentSharesRatio"]), 2)
        chg_20d = None
        if len(sorted_rows) >= 21:
            chg_20d = round(cur_ratio - float(sorted_rows[-21]["ForeignInvestmentSharesRatio"]), 2)

        # Trend direction
        if chg_20d is None:
            trend = "unavailable"
        elif chg_20d >= 0.5:
            trend = "increasing"
        elif chg_20d <= -0.5:
            trend = "decreasing"
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
                trade_date=str(latest["date"]),
                fetched_at=fetched_at,
                status="available",
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # 5. Securities Lending (TaiwanStockSecuritiesLending)
    # ─────────────────────────────────────────────────────────────

    def get_securities_lending(self, symbol: str, as_of: date | str | None = None) -> TaiwanSecuritiesLendingData:
        dataset = "TaiwanStockSecuritiesLending"
        payload = self._range_payload(
            symbol, dataset, as_of,
            lambda adapter, code, start: adapter.fetch_securities_lending(code, start_date=start, raise_for_status=True),
        )
        if payload.get("status", "available") != "available" or not payload.get("data"):
            return TaiwanSecuritiesLendingData(meta=SectionMeta(
                source=payload.get("source", f"finmind:{dataset}"), fetched_at=payload.get("fetched_at"), status="unavailable",
                trade_date=payload.get("data_date") if as_of is None else None,
                fallback_reason=self._cached_unavailable_reason(dataset, payload),
            ))
        return self._process_securities_lending(payload["data"], payload.get("data_date"), payload["fetched_at"], as_of=as_of)

    @staticmethod
    def _process_securities_lending(
        rows: list[dict[str, Any]],
        data_date: str | None,
        fetched_at: str,
        as_of: date | str | None = None,
        trading_dates: Sequence[date | str] | None = None,
    ) -> TaiwanSecuritiesLendingData:
        """Latest lending volume plus 5 / 20 trading-day sums and the anomaly flag.

        ``trading_dates`` are the market's sessions (default: the local daily store).
        FinMind lists individual lending transactions, so a session without rows had no
        lending and counts as zero, as long as the rows cover it: sessions on or after
        the fetch date are left out unless they have rows, and a window that starts
        before the first row is unknown (None / "unavailable"), not quiet.
        """
        # Enforce point-in-time cutoff
        rows = filter_daily_records_as_of(rows, as_of)
        if as_of is not None:
            data_date = None  # A cache-wide date cannot describe an empty historical result.
        if not rows:
            return TaiwanSecuritiesLendingData(
                meta=SectionMeta(source="finmind:TaiwanStockSecuritiesLending", trade_date=data_date, fetched_at=fetched_at,
                                 status="unavailable", fallback_reason="no securities lending rows available as of cutoff")
            )

        # Volume is known even if one of the rates needed for its weighted average is missing.
        daily_agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            d = str(r.get("date") or "").strip()
            vol = parse_number(r.get("volume")) or 0.0
            if not d or not math.isfinite(vol) or vol <= 0:
                continue
            day = daily_agg.setdefault(d, {"volume": 0, "fee_sum": 0.0})
            day["volume"] += int(vol)
            try:
                fee = parse_number(r.get("fee_rate"))
            except ValueError:
                fee = None
            if fee is None or not math.isfinite(fee):
                day["fee_sum"] = None
            elif day["fee_sum"] is not None:
                day["fee_sum"] += fee * vol

        if not daily_agg:
            return TaiwanSecuritiesLendingData(
                meta=SectionMeta(source="finmind:TaiwanStockSecuritiesLending", trade_date=data_date, fetched_at=fetched_at,
                                 status="unavailable", fallback_reason="securities lending rows contain no positive transaction volume")
            )

        sorted_dates = sorted(daily_agg.keys())
        latest_d = sorted_dates[-1]
        latest_item = daily_agg[latest_d]
        latest_vol = latest_item["volume"]
        fee_sum = latest_item["fee_sum"]
        avg_fee = round(fee_sum / latest_vol, 2) if fee_sum is not None and latest_vol > 0 else None

        cutoff = str(as_of)[:10] if as_of else None
        fetched_on = _taipei_date(fetched_at)
        market = _market_dates() if trading_dates is None else trading_dates
        known = {
            day for day in (str(d)[:10] for d in market)
            if (cutoff is None or day <= cutoff) and (fetched_on is None or day < fetched_on)
        }
        # A day with lending rows is a session even when the daily store has a gap there.
        sessions = sorted(known | set(sorted_dates)) if known else []

        def window_volume(size: int) -> int | None:
            window = sessions[-size:]
            if len(window) < size or sorted_dates[0] > window[0]:
                return None
            return sum(daily_agg[d]["volume"] for d in window if d in daily_agg)

        vol_5d = window_volume(5)
        vol_20d = window_volume(20)

        # Anomaly detection: 5D daily average vs 20D daily average
        # Standardized enum: 'surge' (abnormal spike), 'drop' (abnormal fall), 'normal'
        anomaly = "unavailable"  # no covered 20-session window, or fewer than 10 lending days in it
        if vol_5d is not None and vol_20d is not None and sum(d in daily_agg for d in sessions[-20:]) >= 10:
            anomaly = "normal"
            avg_20d = vol_20d / 20
            avg_5d = vol_5d / 5
            if avg_20d > 0:
                if avg_5d >= 2.0 * avg_20d and avg_5d >= 50000:
                    anomaly = "surge"
                elif avg_5d <= 0.3 * avg_20d:
                    anomaly = "drop"

        return TaiwanSecuritiesLendingData(
            latest_volume=latest_vol,
            avg_fee_rate=avg_fee,
            volume_5d=vol_5d,
            volume_20d=vol_20d,
            anomaly_status=anomaly,
            meta=SectionMeta(
                source="finmind:TaiwanStockSecuritiesLending",
                trade_date=latest_d,
                fetched_at=fetched_at,
                status="available",
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # Composite Methods
    # ─────────────────────────────────────────────────────────────

    def get_fundamentals_bundle(
        self,
        symbol: str,
        exchange: str,
        as_of: date | str | None = None,
    ) -> TaiwanFundamentalData:
        """Aggregate valuation, revenue, and profitability into unified fundamental bundle."""
        val = self.get_valuation(symbol, exchange, as_of=as_of)
        rev = self.get_monthly_revenue(symbol, as_of=as_of)
        prof = self.get_financial_statements(symbol, as_of=as_of)

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

    def get_big_holders(self, symbol: str, as_of: date | str | None = None) -> TaiwanBigHolderData:
        """Local TDCC weekly snapshot only; never triggers a download."""
        from app.taiwan.shareholding_store import TDCC_URL, TaiwanShareholdingStore

        canonical = self._to_canonical_str(symbol)
        try:
            rows = TaiwanShareholdingStore().latest_with_change([canonical], before=self._cutoff_date(as_of))
        except Exception as exc:  # unreadable store stays unavailable, not empty
            logger.warning("big holder read failed for %s: %s", canonical, exc)
            rows = None
        if rows is None or rows.is_empty():
            return TaiwanBigHolderData(meta=SectionMeta(source="tdcc:opendata", status="unavailable",
                                                        reason="尚無集保股權分散表資料"))
        row = rows.row(0, named=True)
        return TaiwanBigHolderData(
            big_holder_pct=row["big_holder_pct"], big_holders=row["big_holders"],
            total_holders=row["total_holders"], change_1w=row["big_holder_pct_change"],
            previous_date=str(row["previous_date"]) if row["previous_date"] else None,
            meta=SectionMeta(source="tdcc:opendata", trade_date=str(row["date"]), status="available",
                             fetched_at=row["retrieved_at"], reason=f"來源 {TDCC_URL}"),
        )

    def get_extra_chips_bundle(
        self,
        symbol: str,
        as_of: date | str | None = None,
    ) -> TaiwanExtraChipsData:
        """Aggregate foreign shareholding and securities lending into unified chips bundle."""
        fsh = self.get_foreign_shareholding(symbol, as_of=as_of)
        sl = self.get_securities_lending(symbol, as_of=as_of)

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
            big_holders=self.get_big_holders(symbol, as_of=as_of),
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


def reset_fundamental_chips_service() -> None:
    """Reset the global fundamental chips service singleton.
    
    Ensures that when preferences (FinMind token, enabled state) change,
    subsequent requests immediately rebuild the service and adapter with
    new credentials.
    """
    global _fundamental_chips_svc_singleton
    _fundamental_chips_svc_singleton = None
