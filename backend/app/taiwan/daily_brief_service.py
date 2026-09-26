"""Taiwan Daily Brief Service (A12).

Orchestrates:
1. Deterministic evidence collation (Market, Portfolio, Watchlist, Candidates, Events, News).
2. Explicit, user-triggered AI synthesis with strict 7-section schema (A through G).
3. Secure local persistence in user_data/taiwan_daily_briefs.json.

Strict Constraints:
- AI is NEVER called automatically in the background.
- Zero web crawling or synthetic content.
- Preserves missing data as None, never 0.
- Strict No-Recommendation policy: No buy/sell orders, no target prices.
"""
# ruff: noqa: RUF001
from __future__ import annotations

import contextlib
import json
import logging
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.services import watchlist
from app.services.ai_provider import (
    AIOutputTruncated,
    AIProviderConfigSnapshot,
    generate_ai_text,
    snapshot_ai_provider_config,
)
from app.strategy.custom_signals_ai import _extract_json_object
from app.taiwan.daily_brief_models import (
    CandidateFactBrief,
    CandidateItem,
    DailyBriefAISummary,
    DeterministicDailyBrief,
    EventFactBrief,
    MarketFactBrief,
    NewsFactBrief,
    PortfolioFactBrief,
    PortfolioHoldingItem,
    SavedDailyBrief,
    SectorSummaryItem,
    WatchlistFactBrief,
    WatchlistFactItem,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.events_service import get_event_service
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.market_sentiment_service import get_market_sentiment_service
from app.taiwan.news_service import get_news_service
from app.taiwan.quant.live_contract import LiveModel
from app.taiwan.quant.live_store import LiveLedger
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_today
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.screener_strategy_store import get_screener_strategy_store

logger = logging.getLogger(__name__)


def _storage_path() -> Path:
    p = settings.data_dir / "user_data" / "taiwan_daily_briefs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class TaiwanDailyBriefService:
    """Collates deterministic daily evidence and triggers on-demand AI interpretation."""

    def __init__(
        self,
        path: Path | None = None,
        daily_store: TaiwanDailyStore | None = None,
    ) -> None:
        self.path = path or _storage_path()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.calendar = TaiwanTradingCalendar()
        self._lock = threading.Lock()

    def _read_saved_briefs_raw(self) -> list[SavedDailyBrief]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [SavedDailyBrief(**item) for item in raw]
        except Exception as e:
            logger.warning("Failed to load saved daily briefs from %s: %s", self.path, e)
        return []

    def _save_briefs_raw(self, briefs: list[SavedDailyBrief]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        payload = [b.model_dump() for b in briefs]
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    # ── Deterministic Layer ─────────────────────────────────────────

    def build_deterministic_brief(
        self,
        target_date: date | None = None,
        portfolio_holdings: list[dict[str, Any]] | None = None,
    ) -> DeterministicDailyBrief:
        """Assemble structured, factual brief across all existing modules."""
        trade_d = target_date or taipei_today()
        # Find latest available date in store if target_date is not populated
        avail_dates = self.daily_store.available_dates()
        effective_date = trade_d
        if avail_dates and trade_d not in avail_dates:
            prior = [d for d in avail_dates if d <= trade_d]
            if prior:
                effective_date = prior[-1]
            elif avail_dates:
                effective_date = avail_dates[-1]

        now_iso = datetime.now(UTC).isoformat()

        # 1. Market Overview & Intelligence
        market_intel_svc = TaiwanMarketIntelligenceService(daily_store=self.daily_store, calendar=self.calendar)
        market_snap = market_intel_svc.get_snapshot(effective_date)

        sentiment_svc = get_market_sentiment_service()
        try:
            sentiment_resp = sentiment_svc.get_market_sentiment(effective_date)
            sentiment_label = sentiment_resp.sentiment_label
            sentiment_desc = sentiment_resp.summary
        except Exception as e:
            logger.debug("Market sentiment lookup failed: %s", e)
            sentiment_label = "中性"
            sentiment_desc = "市場情緒暫無數據"

        industry_svc = TaiwanIndustryIntelligenceService(daily_store=self.daily_store, calendar=self.calendar)
        ind_snap = industry_svc.get_snapshot(effective_date)

        sorted_by_change = sorted(
            [i for i in ind_snap.industries if i.average_change_pct is not None],
            key=lambda x: x.average_change_pct or 0.0,
            reverse=True,
        )
        strong_sec = [
            SectorSummaryItem(
                industry=s.industry,
                change_pct=s.average_change_pct,
                turnover=s.turnover,
                advance_ratio=s.advance_ratio,
            )
            for s in sorted_by_change[:3]
        ]
        weak_sec = [
            SectorSummaryItem(
                industry=s.industry,
                change_pct=s.average_change_pct,
                turnover=s.turnover,
                advance_ratio=s.advance_ratio,
            )
            for s in sorted_by_change[-3:]
        ]

        taiex_obj = market_snap.indexes.taiex if market_snap.indexes else None
        inst_obj = market_snap.institutional

        market_fact = MarketFactBrief(
            trade_date=str(effective_date),
            taiex_close=taiex_obj.close if taiex_obj else None,
            taiex_change=taiex_obj.change if taiex_obj else None,
            taiex_change_pct=taiex_obj.change_pct if taiex_obj else None,
            advance_count=market_snap.market_totals.advance_count,
            decline_count=market_snap.market_totals.decline_count,
            flat_count=market_snap.market_totals.flat_count,
            upper_limit_count=market_snap.market_totals.upper_limit_count,
            lower_limit_count=market_snap.market_totals.lower_limit_count,
            total_turnover=market_snap.market_totals.turnover,
            foreign_net=inst_obj.foreign_net if inst_obj else None,
            investment_trust_net=inst_obj.investment_trust_net if inst_obj else None,
            dealer_net=inst_obj.dealer_net if inst_obj else None,
            total_institutional_net=inst_obj.total_net if inst_obj else None,
            sentiment_label=sentiment_label,
            sentiment_description=sentiment_desc,
            strongest_sectors=strong_sec,
            weakest_sectors=weak_sec,
        )

        # 2. Portfolio Fact Layer
        event_svc = get_event_service()
        port_items: list[PortfolioHoldingItem] = []
        if portfolio_holdings:
            port_syms = [h.get("symbol", "") for h in portfolio_holdings if h.get("symbol")]
            port_df = self.daily_store.read_range(port_syms, effective_date, effective_date)
            quote_map = {r["symbol"]: r for r in port_df.to_dicts()} if not port_df.is_empty() else {}

            for h in portfolio_holdings:
                sym = h.get("symbol", "")
                if not sym:
                    continue
                q = quote_map.get(sym, {})
                c_val = q.get("close")
                risk_status = event_svc.check_symbol_risk_status(sym)
                events_list = []
                if risk_status.get("is_disposition"):
                    events_list.append("處置股票")
                if risk_status.get("is_warning"):
                    events_list.append("注意股票")
                if risk_status.get("is_suspended"):
                    events_list.append("暫停交易")

                port_items.append(
                    PortfolioHoldingItem(
                        symbol=sym,
                        name=h.get("name", sym),
                        shares=int(h.get("shares", 0)),
                        average_cost=float(h.get("average_cost", 0.0)),
                        close=float(c_val) if c_val is not None else None,
                        change_pct=float(h.get("return_pct", 0.0)) if h.get("return_pct") is not None else None,
                        events=events_list,
                    )
                )

        # Sort biggest movers
        port_items.sort(key=lambda x: abs(x.change_pct or 0.0), reverse=True)
        port_fact = PortfolioFactBrief(
            holdings_count=len(port_items),
            biggest_movers=port_items[:5],
        )

        # 3. Watchlist Fact Layer
        watch_entries = watchlist.list_symbols()
        watch_syms = [w.get("symbol", "") for w in watch_entries if w.get("symbol")]
        watch_items: list[WatchlistFactItem] = []
        if watch_syms:
            watch_df = self.daily_store.read_range(watch_syms, effective_date, effective_date)
            w_quote_map = {r["symbol"]: r for r in watch_df.to_dicts()} if not watch_df.is_empty() else {}
            for w_entry in watch_entries:
                sym = w_entry.get("symbol", "")
                if not sym:
                    continue
                q = w_quote_map.get(sym, {})
                c_val = q.get("close")
                vol_val = q.get("volume")
                watch_items.append(
                    WatchlistFactItem(
                        symbol=sym,
                        name=w_entry.get("name", sym),
                        close=float(c_val) if c_val is not None else None,
                        volume=float(vol_val) if vol_val is not None else None,
                    )
                )

        watchlist_fact = WatchlistFactBrief(
            items_count=len(watch_items),
            quant_leaders=watch_items[:5],
            unusual_volume=[w for w in watch_items if (w.volume or 0) > 1_000_000][:5],
        )

        # 4. Candidates Layer (Live Quant Top 10 + Strategy Matches)
        new_top10: list[CandidateItem] = []
        dropped_top10: list[CandidateItem] = []
        with contextlib.suppress(Exception):
            ledger = LiveLedger()
            model = LiveModel()
            runs = ledger.list_runs(limit=2)
            if len(runs) >= 1:
                cur_run_session = runs[0].get("session")
                cur_data = ledger.read_run(model.key, cur_run_session) if cur_run_session else None
                cur_signals = (cur_data or {}).get("signals", [])
                cur_top10 = cur_signals[:10]
                cur_syms = {s["symbol"] for s in cur_top10}

                prev_syms: set[str] = set()
                if len(runs) >= 2:
                    prev_session = runs[1].get("session")
                    prev_data = ledger.read_run(model.key, prev_session) if prev_session else None
                    prev_top10 = (prev_data or {}).get("signals", [])[:10]
                    prev_syms = {s["symbol"] for s in prev_top10}

                # New to top 10
                for rank, s in enumerate(cur_top10, start=1):
                    sym = s.get("symbol", "")
                    if sym not in prev_syms:
                        new_top10.append(
                            CandidateItem(
                                symbol=sym,
                                name=s.get("name", sym),
                                reason_type="new_top10",
                                source_name="Live Quant Top 10 (新入選)",
                                quant_score=s.get("score"),
                                rank=rank,
                            )
                        )

                # Dropped from top 10
                if prev_syms:
                    for s in prev_top10:
                        sym = s.get("symbol", "")
                        if sym not in cur_syms:
                            dropped_top10.append(
                                CandidateItem(
                                    symbol=sym,
                                    name=s.get("name", sym),
                                    reason_type="dropped_top10",
                                    source_name="Live Quant Top 10 (掉出)",
                                    quant_score=s.get("score"),
                                )
                            )

        # Strategy matches via preset screener
        strategy_matches: list[CandidateItem] = []
        with contextlib.suppress(Exception):
            screener_svc = TaiwanScreenerService()
            strat_store = get_screener_strategy_store()
            strats = strat_store.list_strategies()
            if strats:
                target_strat = strats[0]
                conds = target_strat.conditions
                req = TaiwanScreenerRequest(**conds, page=1, page_size=5)
                res = screener_svc.run(req)
                for item in res.items:
                    strategy_matches.append(
                        CandidateItem(
                            symbol=item.symbol,
                            name=item.name,
                            reason_type="strategy_match",
                            source_name=target_strat.name,
                            quant_score=item.quant_score,
                            rank=item.rank if hasattr(item, "rank") else 1,
                            close=item.close,
                            change_pct=item.change_pct,
                            match_reasons=item.match_reasons,
                        )
                    )

        candidate_fact = CandidateFactBrief(
            new_top10=new_top10,
            dropped_top10=dropped_top10,
            strategy_matches=strategy_matches,
        )

        # 5. Events Layer
        all_events = event_svc.get_events(scope="all", limit=20)
        risk_evs = [e.model_dump() for e in all_events if getattr(e, "severity", None) == "risk"]
        attention_evs = [
            e.model_dump() for e in all_events if getattr(e, "severity", None) in ("attention", "info")
        ][:15]

        event_fact = EventFactBrief(
            risk_events=risk_evs,
            attention_events=attention_evs,
        )

        # 6. News Layer (Relevant news for top candidates or portfolio)
        relevant_news: list[dict[str, Any]] = []
        news_svc = get_news_service()
        lookup_syms = [c.symbol for c in (new_top10 + strategy_matches)[:3]]
        for sym in lookup_syms:
            with contextlib.suppress(Exception):
                resp = news_svc.get_recent_news(sym, limit=2)
                for news_item in resp.items:
                    relevant_news.append(news_item.model_dump())

        news_fact = NewsFactBrief(items=relevant_news[:6])

        return DeterministicDailyBrief(
            brief_date=str(effective_date),
            generated_at=now_iso,
            market=market_fact,
            portfolio=port_fact,
            watchlist=watchlist_fact,
            candidates=candidate_fact,
            events=event_fact,
            news=news_fact,
        )

    # ── AI Summary Layer (User-Triggered ONLY) ──────────────────────

    async def generate_ai_summary(
        self,
        brief: DeterministicDailyBrief,
        config_snapshot: AIProviderConfigSnapshot | None = None,
    ) -> DailyBriefAISummary:
        """Call AI provider strictly on user demand to synthesize the daily brief."""
        config = config_snapshot or snapshot_ai_provider_config()

        system_prompt = (
            "你是一位嚴謹客觀的台股每日市場與投資工作流分析助理。\n"
            "【嚴格紀律】：\n"
            "1. 必須嚴格根據提供的結構化客觀事實（Deterministic Daily Brief）進行解讀。\n"
            "2. 嚴禁編造、幻想價格、數值或事件；遇到 missing 數據說明無資料，絕對不可補 0 或偽裝正常。\n"
            "3. 明確區分官方監管公告/重大事件與媒體新聞，不得混淆客觀事實與新聞評論。\n"
            "4. 絕對不可給出任何買賣點、目標價、合理估值或投資買賣指令。\n"
            "5. 必須嚴格輸出合法的 JSON 物件，格式鍵名固定如下：\n"
            "{\n"
            '  "section_a_market": "A. 今日市場概況與盤勢解讀",\n'
            '  "section_b_key_changes": ["變化 1", "變化 2", "變化 3"],\n'
            '  "section_c_portfolio": "C. 我的持股重點追蹤與損益變化",\n'
            '  "section_d_watchlist": "D. 我的觀察清單動態與異動",\n'
            '  "section_e_candidates": "E. 今日候選股、新入選 Top 10 與策略匹配重點",\n'
            '  "section_f_risks": "F. 官方重大事件、處置注意與風險警示",\n'
            '  "section_g_tracking": "G. 明日／下一交易日核心觀察指標與關鍵事件",\n'
            '  "evidence_sources": ["引用依據 1", "引用依據 2"]\n'
            "}"
        )

        user_content = json.dumps(brief.model_dump(), ensure_ascii=False, indent=2)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下是今日結構化台股市場與個人關注事實清單，請產出每日 AI 摘要：\n\n```json\n{user_content}\n```"},
        ]

        _BRIEF_RETRY_MSG = (
            "前次輸出已超出 token 上限截斷。"
            "請重新輸出完整 JSON，每個字串欄位縮短至 100 字以內，"
            "section_b_key_changes 限 3 項，evidence_sources 限 3 項。"
        )
        try:
            text = await generate_ai_text(
                messages=messages,
                temperature=0.2,
                max_tokens=3500,
                timeout=55.0,
                config_snapshot=config,
            )
        except AIOutputTruncated as trunc_exc:
            retry_msgs = list(messages) + [
                {"role": "assistant", "content": trunc_exc.partial_content},
                {"role": "user", "content": _BRIEF_RETRY_MSG},
            ]
            try:
                text = await generate_ai_text(
                    messages=retry_msgs,
                    temperature=0.2,
                    max_tokens=3500,
                    timeout=55.0,
                    config_snapshot=config,
                )
            except AIOutputTruncated:
                raise ValueError("AI 回覆超過輸出長度限制，請重新產生。")

        extracted = _extract_json_object(text)
        if not extracted or not isinstance(extracted, dict):
            raise ValueError(f"AI response did not return a valid JSON object: {text[:200]}")

        sec_b = extracted.get("section_b_key_changes")
        if isinstance(sec_b, str):
            sec_b = [sec_b]
        elif not isinstance(sec_b, list):
            sec_b = []

        sec_ev = extracted.get("evidence_sources")
        if isinstance(sec_ev, str):
            sec_ev = [sec_ev]
        elif not isinstance(sec_ev, list):
            sec_ev = []

        return DailyBriefAISummary(
            section_a_market=str(extracted.get("section_a_market", "")).strip(),
            section_b_key_changes=[str(x).strip() for x in sec_b if str(x).strip()],
            section_c_portfolio=str(extracted.get("section_c_portfolio", "")).strip(),
            section_d_watchlist=str(extracted.get("section_d_watchlist", "")).strip(),
            section_e_candidates=str(extracted.get("section_e_candidates", "")).strip(),
            section_f_risks=str(extracted.get("section_f_risks", "")).strip(),
            section_g_tracking=str(extracted.get("section_g_tracking", "")).strip(),
            evidence_sources=[str(x).strip() for x in sec_ev if str(x).strip()],
        )

    # ── History Persistence Layer ───────────────────────────────────

    def save_brief(
        self,
        brief: DeterministicDailyBrief,
        ai_summary: DailyBriefAISummary | None = None,
        ai_status: str = "not_generated",
        ai_error: str | None = None,
    ) -> SavedDailyBrief:
        """Persist a daily brief to user_data/taiwan_daily_briefs.json."""
        now_dt = datetime.now(UTC)
        brief_id = f"brief_{now_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        entry = SavedDailyBrief(
            brief_id=brief_id,
            brief_date=brief.brief_date,
            generated_at=now_dt.isoformat(),
            data_as_of=brief.market.trade_date,
            structured_brief=brief,
            ai_summary=ai_summary,
            ai_status=ai_status,  # type: ignore[arg-type]
            ai_error=ai_error,
        )

        with self._lock:
            existing = self._read_saved_briefs_raw()
            existing.append(entry)
            self._save_briefs_raw(existing)

        logger.info("Saved daily brief %s for date %s (AI status: %s)", brief_id, entry.brief_date, ai_status)
        return entry

    def list_saved_briefs(self) -> list[SavedDailyBrief]:
        """List all saved daily briefs, sorted newest first."""
        with self._lock:
            existing = self._read_saved_briefs_raw()
        existing.sort(key=lambda b: b.generated_at, reverse=True)
        return existing

    def get_saved_brief(self, brief_id: str) -> SavedDailyBrief | None:
        """Get a saved daily brief by ID."""
        with self._lock:
            for b in self._read_saved_briefs_raw():
                if b.brief_id == brief_id:
                    return b
        return None


_daily_brief_instance: TaiwanDailyBriefService | None = None
_daily_brief_lock = threading.Lock()


def get_daily_brief_service() -> TaiwanDailyBriefService:
    global _daily_brief_instance
    with _daily_brief_lock:
        if _daily_brief_instance is None:
            _daily_brief_instance = TaiwanDailyBriefService()
        return _daily_brief_instance
