"""Taiwan Market Sentiment Service (A11).

Combines deterministic spot market indicators with Taiwan index futures (TX)
and options (TXO) institutional positions into an interpretable market sentiment summary.

Strict Rules:
- 100% deterministic logic. No black-box AI sentiment scoring or prediction models.
- Transparent evidence checklist: Users can inspect every factor and why it's bullish, bearish, or mixed.
- Fallback resilience: If futures/options are unavailable (rate limit, paid tier, off-hours),
  it degrades gracefully to spot-only analysis without fabricating fake zeros.
- Cached to avoid redundant API polling on Dashboard refreshes.
"""
# ruff: noqa: RUF001
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.market_intelligence import (
    TaiwanMarketIntelligenceService,
    TaiwanMarketIntelligenceSnapshot,
)
from app.taiwan.providers.finmind_provider import (
    FinMindAdapter,
    FinMindAuthError,
    FinMindRateLimitError,
)
from app.taiwan.realtime.calendar import taipei_now

logger = logging.getLogger(__name__)

SentimentDirection = Literal["bullish", "bearish", "neutral"]
MarketSentimentClass = Literal["bullish", "neutral", "bearish", "mixed"]

SENTIMENT_LABELS: dict[MarketSentimentClass, str] = {
    "bullish": "偏多",
    "neutral": "中性",
    "bearish": "偏空",
    "mixed": "分歧",
}


class MarketEvidenceItem(BaseModel):
    """Single transparent indicator evidence."""

    id: str
    category: str = Field(..., description="spot, futures, options")
    label: str = Field(..., description="指標名稱")
    direction: SentimentDirection = Field(..., description="bullish, bearish, neutral")
    description: str = Field(..., description="客觀事實與理由說明")
    weight: float = 1.0
    status: str = Field("available", description="available, unavailable, partial")


class TaiwanMarketSentimentResponse(BaseModel):
    """Unified Taiwan Market Sentiment API Response."""

    as_of_date: str
    sentiment: MarketSentimentClass
    sentiment_label: str
    summary: str
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    evidence: list[MarketEvidenceItem] = Field(default_factory=list)
    spot_details: dict[str, Any] = Field(default_factory=dict)
    futures_details: dict[str, Any] | None = None
    options_details: dict[str, Any] | None = None
    derivatives_status: str = Field("available", description="available, unavailable, rate_limited, auth_required")
    derivatives_status_message: str | None = None
    fetched_at: str


class TaiwanMarketSentimentService:
    """Service assembling deterministic market sentiment."""

    def __init__(
        self,
        market_intel_svc: TaiwanMarketIntelligenceService | None = None,
        finmind_adapter: FinMindAdapter | None = None,
        finmind_cache: FinMindCache | None = None,
    ) -> None:
        self.market_intel_svc = market_intel_svc or TaiwanMarketIntelligenceService()
        self.finmind = finmind_adapter or FinMindAdapter()
        self.cache = finmind_cache or FinMindCache()

    def _fetch_tx_futures(self, target_date: date) -> tuple[dict[str, Any] | None, str, str | None]:
        """Fetch foreign TX futures positions (last 2 sessions for change calculation)."""
        dataset = "TaiwanFuturesInstitutionalInvestors"
        symbol = "TX"

        # Check cache
        cached = self.cache.get(dataset, symbol)
        if cached and isinstance(cached.get("data"), list) and cached.get("data"):
            rows = cached.get("data", [])
            parsed = self._parse_futures_rows(rows)
            if parsed:
                return parsed, "available", None

        start_date = (target_date - timedelta(days=7)).isoformat()
        end_date = target_date.isoformat()

        try:
            rows = self.finmind.fetch_dataset(
                dataset=dataset,
                symbol_or_code=symbol,
                start_date=start_date,
                end_date=end_date,
                raise_for_status=True,
            )
            if not rows:
                return None, "unavailable", "近期無台指期三大法人數據"

            self.cache.set(dataset, symbol, rows, data_date=end_date, status="available")
            parsed = self._parse_futures_rows(rows)
            return parsed, "available", None
        except FinMindAuthError:
            self.cache.set(dataset, symbol, [], status="auth_required")
            return None, "auth_required", "目前資料來源方案不提供期權資料 (需有效金鑰)"
        except FinMindRateLimitError:
            return None, "rate_limited", "期貨資料查詢已達頻率上限"
        except Exception as e:
            logger.warning("Failed to fetch TX futures from FinMind: %s", e)
            return None, "unavailable", f"期貨資料暫不可用: {e}"

    def _parse_futures_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Parse raw futures rows to find foreign investor net OI and day-over-day change."""
        # Filter for foreign investors ('外資及陸資' or contains '外資')
        foreign_rows = [
            r for r in rows
            if "外資" in str(r.get("institutional_investors") or "")
            and str(r.get("futures_id") or "").upper() in ("TX", "TXF")
        ]
        if not foreign_rows:
            return None

        # Sort by date ascending
        foreign_rows.sort(key=lambda r: str(r.get("date") or ""))
        latest = foreign_rows[-1]
        prev = foreign_rows[-2] if len(foreign_rows) >= 2 else None

        long_oi = int(latest.get("long_open_interest_balance_volume") or 0)
        short_oi = int(latest.get("short_open_interest_balance_volume") or 0)
        net_oi = long_oi - short_oi

        prev_net_oi = None
        net_oi_change = None
        if prev:
            p_long = int(prev.get("long_open_interest_balance_volume") or 0)
            p_short = int(prev.get("short_open_interest_balance_volume") or 0)
            prev_net_oi = p_long - p_short
            net_oi_change = net_oi - prev_net_oi

        return {
            "date": latest.get("date"),
            "long_oi": long_oi,
            "short_oi": short_oi,
            "net_oi": net_oi,
            "prev_net_oi": prev_net_oi,
            "net_oi_change": net_oi_change,
        }

    def _fetch_txo_options(self, target_date: date) -> tuple[dict[str, Any] | None, str, str | None]:
        """Fetch foreign TXO options positions."""
        dataset = "TaiwanOptionInstitutionalInvestors"
        symbol = "TXO"

        cached = self.cache.get(dataset, symbol)
        if cached and isinstance(cached.get("data"), list) and cached.get("data"):
            rows = cached.get("data", [])
            parsed = self._parse_options_rows(rows)
            if parsed:
                return parsed, "available", None

        start_date = (target_date - timedelta(days=7)).isoformat()
        end_date = target_date.isoformat()

        try:
            rows = self.finmind.fetch_dataset(
                dataset=dataset,
                symbol_or_code=symbol,
                start_date=start_date,
                end_date=end_date,
                raise_for_status=True,
            )
            if not rows:
                return None, "unavailable", "近期無選擇權三大法人數據"

            self.cache.set(dataset, symbol, rows, data_date=end_date, status="available")
            parsed = self._parse_options_rows(rows)
            return parsed, "available", None
        except FinMindAuthError:
            self.cache.set(dataset, symbol, [], status="auth_required")
            return None, "auth_required", "目前資料來源方案不提供選擇權資料"
        except FinMindRateLimitError:
            return None, "rate_limited", "選擇權資料查詢已達頻率上限"
        except Exception as e:
            logger.warning("Failed to fetch TXO options from FinMind: %s", e)
            return None, "unavailable", f"選擇權資料暫不可用: {e}"

    def _parse_options_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Parse raw options rows to calculate foreign Call/Put net positions."""
        foreign_rows = [
            r for r in rows
            if "外資" in str(r.get("institutional_investors") or "")
            and str(r.get("option_id") or "").upper() in ("TXO",)
        ]
        if not foreign_rows:
            return None

        # Group by date
        by_date: dict[str, list[dict[str, Any]]] = {}
        for r in foreign_rows:
            d = str(r.get("date") or "")
            by_date.setdefault(d, []).append(r)

        sorted_dates = sorted(by_date.keys())
        if not sorted_dates:
            return None

        latest_date = sorted_dates[-1]
        latest_items = by_date[latest_date]

        call_long = 0
        call_short = 0
        put_long = 0
        put_short = 0

        for it in latest_items:
            cp = str(it.get("call_put") or "")
            long_v = int(it.get("long_open_interest_balance_volume") or 0)
            short_v = int(it.get("short_open_interest_balance_volume") or 0)
            if "買權" in cp or "Call" in cp:
                call_long += long_v
                call_short += short_v
            elif "賣權" in cp or "Put" in cp:
                put_long += long_v
                put_short += short_v

        call_net = call_long - call_short
        put_net = put_long - put_short

        return {
            "date": latest_date,
            "call_net_oi": call_net,
            "put_net_oi": put_net,
            "call_long_oi": call_long,
            "put_long_oi": put_long,
        }

    def get_market_sentiment(
        self,
        target_date: date | None = None,
    ) -> TaiwanMarketSentimentResponse:
        """Calculate and return deterministic market sentiment and evidence checklist."""
        now = taipei_now()
        now_iso = now.isoformat()
        ref_date = target_date or now.date()

        # 1. Fetch deterministic Spot Market Intelligence
        intel: TaiwanMarketIntelligenceSnapshot = self.market_intel_svc.get_snapshot(target_date=ref_date)
        as_of_date_str = intel.trade_date or ref_date.isoformat()

        evidence: list[MarketEvidenceItem] = []

        # ── Indicator A1: Benchmark Index (TAIEX) ──
        taiex = intel.indexes.taiex
        taiex_pct = taiex.change_pct if taiex else None
        if taiex_pct is not None:
            if taiex_pct >= 0.002:  # +0.2%
                ev_dir: SentimentDirection = "bullish"
                desc = f"加權指數上漲 {taiex.change:+.2f} 點 ({taiex_pct * 100:+.2f}%)，大盤維持上漲動能"
            elif taiex_pct <= -0.002:  # -0.2%
                ev_dir = "bearish"
                desc = f"加權指數下跌 {taiex.change:+.2f} 點 ({taiex_pct * 100:+.2f}%)，大盤承受回檔壓力"
            else:
                ev_dir = "neutral"
                desc = f"加權指數平盤震盪 ({taiex_pct * 100:+.2f}%)，方向未明"
            evidence.append(
                MarketEvidenceItem(
                    id="spot_index_taiex",
                    category="spot",
                    label="加權指數表現",
                    direction=ev_dir,
                    description=desc,
                )
            )

        # ── Indicator A2: Market Breadth (Advances vs Declines) ──
        breadth = intel.market_totals
        adv = breadth.advance_count
        dec = breadth.decline_count
        total_active = adv + dec
        if total_active > 0:
            ratio = adv / max(dec, 1)
            if ratio >= 1.25:
                ev_dir = "bullish"
                desc = f"全市場上漲家數 ({adv}) 明顯大於下跌家數 ({dec})，漲跌比 {ratio:.2f}，市場廣度偏強"
            elif ratio <= 0.8:
                ev_dir = "bearish"
                desc = f"全市場下跌家數 ({dec}) 明顯大於上漲家數 ({adv})，跌家占優，市場廣度偏弱"
            else:
                ev_dir = "neutral"
                desc = f"上漲家數 ({adv}) 與下跌家數 ({dec}) 接近，多空僵持"
            evidence.append(
                MarketEvidenceItem(
                    id="spot_breadth",
                    category="spot",
                    label="市場漲跌廣度",
                    direction=ev_dir,
                    description=desc,
                )
            )

        # ── Indicator A3: Institutional Investors Net Flows ──
        inst = intel.institutional
        if inst.status in ("current", "stale") and inst.foreign_net is not None:
            foreign_net = inst.foreign_net  # in shares
            total_net = inst.total_net or 0
            # in lots (張)
            f_lots = foreign_net / 1000
            t_lots = total_net / 1000

            if f_lots > 5000 and t_lots > 0:
                ev_dir = "bullish"
                desc = f"外資現貨買超 {f_lots:,.0f} 張，三大法人合計買超 {t_lots:,.0f} 張"
            elif f_lots < -5000 and t_lots < 0:
                ev_dir = "bearish"
                desc = f"外資現貨賣超 {abs(f_lots):,.0f} 張，三大法人合計賣超 {abs(t_lots):,.0f} 張"
            else:
                ev_dir = "neutral"
                desc = f"三大法人動向平緩 (外資 {f_lots:+,.0f} 張，合計 {t_lots:+,.0f} 張)"
            evidence.append(
                MarketEvidenceItem(
                    id="spot_institutional",
                    category="spot",
                    label="現貨三大法人籌碼",
                    direction=ev_dir,
                    description=desc,
                )
            )

        # ── Indicator A4: Margin Balance Change ──
        margin = intel.margin
        if margin.status in ("current", "stale") and margin.margin_balance_change is not None:
            m_change = margin.margin_balance_change / 1000  # in lots (張)
            if m_change > 8000:
                ev_dir = "neutral"
                desc = f"融資餘額大幅增加 {m_change:,.0f} 張，散戶積極進場"
            elif m_change < -8000:
                ev_dir = "neutral"
                desc = f"融資餘額大幅減少 {abs(m_change):,.0f} 張，籌碼逐步沉澱"
            else:
                ev_dir = "neutral"
                desc = f"融資增減 {m_change:+,.0f} 張，融資槓桿持穩"
            evidence.append(
                MarketEvidenceItem(
                    id="spot_margin",
                    category="spot",
                    label="信用交易融資變化",
                    direction=ev_dir,
                    description=desc,
                )
            )

        # ── Indicator B: TX Futures Foreign Positioning ──
        actual_date = date.fromisoformat(as_of_date_str)
        futures_info, f_status, f_msg = self._fetch_tx_futures(actual_date)
        if futures_info:
            net_oi = futures_info["net_oi"]
            change = futures_info.get("net_oi_change")
            change_text = f"今日變化 {change:+d} 口" if change is not None else ""

            if net_oi > 0:
                if change is not None and change < -1500:
                    ev_dir = "neutral"
                    desc = f"台指期外資淨多單 {net_oi:,d} 口，但多單減碼 ({change_text})"
                else:
                    ev_dir = "bullish"
                    desc = f"台指期外資淨多單 {net_oi:,d} 口 ({change_text})，期貨偏多佈局"
            else:
                # Net short
                if change is not None and change > 2000:
                    ev_dir = "neutral"
                    desc = f"台指期外資淨空單 {abs(net_oi):,d} 口，空單明顯回補 ({change_text})"
                elif change is not None and change < -1500:
                    ev_dir = "bearish"
                    desc = f"台指期外資淨空單擴大至 {abs(net_oi):,d} 口 ({change_text})，避險空方加碼"
                elif abs(net_oi) > 30000:
                    ev_dir = "bearish"
                    desc = f"台指期外資維持高水位淨空單 {abs(net_oi):,d} 口 ({change_text})，期貨防禦戒心重"
                else:
                    ev_dir = "neutral"
                    desc = f"台指期外資淨部位 {net_oi:,d} 口 ({change_text})"

            evidence.append(
                MarketEvidenceItem(
                    id="futures_tx_foreign",
                    category="futures",
                    label="台指期外資未平倉",
                    direction=ev_dir,
                    description=desc,
                )
            )

        # ── Indicator C: TXO Options Foreign Positions ──
        options_info, _o_status, o_msg = self._fetch_txo_options(actual_date)
        if options_info:
            c_net = options_info.get("call_net_oi", 0)
            p_net = options_info.get("put_net_oi", 0)
            if c_net > 5000 and p_net < 0:
                ev_dir = "bullish"
                desc = f"選擇權外資偏多：淨 Call {c_net:,d} 口，淨 Put {p_net:,d} 口"
            elif p_net > 5000 and c_net < 0:
                ev_dir = "bearish"
                desc = f"選擇權外資偏空：淨 Put 避險達 {p_net:,d} 口，淨 Call {c_net:,d} 口"
            else:
                ev_dir = "neutral"
                desc = f"選擇權外資部位均衡 (淨 Call {c_net:,d} 口，淨 Put {p_net:,d} 口)"

            evidence.append(
                MarketEvidenceItem(
                    id="options_txo_foreign",
                    category="options",
                    label="台指選擇權外資籌碼",
                    direction=ev_dir,
                    description=desc,
                )
            )

        # Count evidence directions
        bullish_count = sum(1 for e in evidence if e.direction == "bullish")
        bearish_count = sum(1 for e in evidence if e.direction == "bearish")
        neutral_count = sum(1 for e in evidence if e.direction == "neutral")

        # ── Deterministic Sentiment Synthesis ──
        # Check for sharp contradiction between spot strength and institutional/futures hedge
        # e.g., Index up + Breadth up, BUT Foreign sold spot + Foreign built huge futures shorts
        has_spot_bull = any(e.category == "spot" and e.direction == "bullish" for e in evidence)
        has_deriv_bear = any(e.category in ("futures", "options") and e.direction == "bearish" for e in evidence)
        has_spot_bear = any(e.category == "spot" and e.direction == "bearish" for e in evidence)
        has_deriv_bull = any(e.category in ("futures", "options") and e.direction == "bullish" for e in evidence)

        if (has_spot_bull and has_deriv_bear) or (has_spot_bear and has_deriv_bull) or (bullish_count >= 2 and bearish_count >= 2):
            sentiment: MarketSentimentClass = "mixed"
            summary = "市場多空訊號分歧：現貨走勢與衍生品或法人籌碼出現對立分歧，宜提高警覺並注意類股輪動。"
        elif bullish_count >= 2 and bearish_count == 0:
            sentiment = "bullish"
            summary = "市場整體氛圍偏多：指數與多數個股同步上揚，籌碼面具支撐力道。"
        elif bullish_count >= 3 and bearish_count <= 1:
            sentiment = "bullish"
            summary = "市場偏多佔優：多數客觀指標呈現正面訊號，唯需留意部分指標震盪。"
        elif bearish_count >= 2 and bullish_count == 0:
            sentiment = "bearish"
            summary = "市場整體氛圍偏空：指數與廣度呈現下跌承壓，法人避險態度明顯。"
        elif bearish_count >= 3 and bullish_count <= 1:
            sentiment = "bearish"
            summary = "市場偏空佔優：空方訊號較多，建議注重部位風險控管。"
        else:
            sentiment = "neutral"
            summary = "市場表現中性或處於平衡震盪期，多空勢力均等，缺乏單邊突破訊號。"

        derivatives_status = f_status if f_status in ("auth_required", "rate_limited") else (
            "available" if futures_info else "unavailable"
        )
        derivatives_msg = f_msg or o_msg

        return TaiwanMarketSentimentResponse(
            as_of_date=as_of_date_str,
            sentiment=sentiment,
            sentiment_label=SENTIMENT_LABELS[sentiment],
            summary=summary,
            bullish_count=bullish_count,
            bearish_count=bearish_count,
            neutral_count=neutral_count,
            evidence=evidence,
            spot_details={
                "taiex": taiex.model_dump() if taiex else None,
                "breadth": breadth.model_dump(),
                "institutional": inst.model_dump(),
                "margin": margin.model_dump(),
            },
            futures_details=futures_info,
            options_details=options_info,
            derivatives_status=derivatives_status,
            derivatives_status_message=derivatives_msg,
            fetched_at=now_iso,
        )


_sentiment_service_instance: TaiwanMarketSentimentService | None = None


def get_market_sentiment_service() -> TaiwanMarketSentimentService:
    global _sentiment_service_instance
    if _sentiment_service_instance is None:
        _sentiment_service_instance = TaiwanMarketSentimentService()
    return _sentiment_service_instance
