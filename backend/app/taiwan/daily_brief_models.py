"""Models for Taiwan Daily Brief and Deterministic Evidence Layer (A12).

Strict Guarantees:
- Deterministic layer collates objective market facts from existing modules only.
- AI interpretation is strictly optional, triggered only on explicit user request.
- Fixed 7-section AI interpretation schema (A through G).
- Daily brief history saved safely in user_data/taiwan_daily_briefs.json (private, non-bundled, no secrets).
- Missing values preserved; never faked as zeros or phantom events.
"""
# ruff: noqa: RUF001
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SectorSummaryItem(BaseModel):
    industry: str
    change_pct: float | None = None
    turnover: float = 0.0
    advance_ratio: float | None = None


class MarketFactBrief(BaseModel):
    """Deterministic market facts: TAIEX, breadth, institutional, sentiment, sectors."""

    trade_date: str
    taiex_close: float | None = None
    taiex_change: float | None = None
    taiex_change_pct: float | None = None

    advance_count: int = 0
    decline_count: int = 0
    flat_count: int = 0
    upper_limit_count: int = 0
    lower_limit_count: int = 0
    total_turnover: float = 0.0

    foreign_net: float | None = None  # in shares or TWD
    investment_trust_net: float | None = None
    dealer_net: float | None = None
    total_institutional_net: float | None = None

    sentiment_label: str = "中性"  # 偏多, 中性, 偏空, 分歧
    sentiment_description: str = ""

    strongest_sectors: list[SectorSummaryItem] = Field(default_factory=list)
    weakest_sectors: list[SectorSummaryItem] = Field(default_factory=list)


class PortfolioHoldingItem(BaseModel):
    symbol: str
    name: str = ""
    shares: int = 0
    average_cost: float = 0.0
    close: float | None = None
    change_pct: float | None = None
    quant_score: float | None = None
    quant_rank: int | None = None
    events: list[str] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)


class PortfolioFactBrief(BaseModel):
    """User portfolio status collated deterministically."""

    holdings_count: int = 0
    biggest_movers: list[PortfolioHoldingItem] = Field(default_factory=list)
    quant_changes: list[dict[str, Any]] = Field(default_factory=list)
    event_risks: list[dict[str, Any]] = Field(default_factory=list)
    active_alerts: list[dict[str, Any]] = Field(default_factory=list)


class WatchlistFactItem(BaseModel):
    symbol: str
    name: str = ""
    close: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    vol_ratio_5d: float | None = None
    quant_score: float | None = None
    events: list[str] = Field(default_factory=list)


class WatchlistFactBrief(BaseModel):
    """User watchlist updates collated deterministically."""

    items_count: int = 0
    quant_leaders: list[WatchlistFactItem] = Field(default_factory=list)
    unusual_volume: list[WatchlistFactItem] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class CandidateItem(BaseModel):
    symbol: str
    name: str
    reason_type: Literal["new_top10", "dropped_top10", "strategy_match"]
    source_name: str  # e.g., "Live Quant Top 10" or strategy name
    quant_score: float | None = None
    rank: int | None = None
    close: float | None = None
    change_pct: float | None = None
    match_reasons: list[str] = Field(default_factory=list)


class CandidateFactBrief(BaseModel):
    """Actionable candidates: new top 10, dropped top 10, screener strategy matches."""

    new_top10: list[CandidateItem] = Field(default_factory=list)
    dropped_top10: list[CandidateItem] = Field(default_factory=list)
    strategy_matches: list[CandidateItem] = Field(default_factory=list)


class EventFactBrief(BaseModel):
    """Deterministic event center facts: risk and attention items."""

    risk_events: list[dict[str, Any]] = Field(default_factory=list)  # 處置, 暫停, 下市
    attention_events: list[dict[str, Any]] = Field(default_factory=list)  # 注意, 減資, 除權息, 財報營收


class NewsFactBrief(BaseModel):
    """Existing relevant stock news items without crawlers or hallucination."""

    items: list[dict[str, Any]] = Field(default_factory=list)


class DeterministicDailyBrief(BaseModel):
    """Complete objective daily brief assembled before any AI call."""

    brief_date: str
    generated_at: str
    market: MarketFactBrief
    portfolio: PortfolioFactBrief
    watchlist: WatchlistFactBrief
    candidates: CandidateFactBrief
    events: EventFactBrief
    news: NewsFactBrief


class DailyBriefAISummary(BaseModel):
    """Strict 7-section structured AI interpretation."""

    section_a_market: str = Field(..., description="A. 今日市場概況與盤勢解讀")
    section_b_key_changes: list[str] = Field(..., description="B. 最重要的 3～5 個市場/持股變化")
    section_c_portfolio: str = Field(..., description="C. 我的持股重點追蹤")
    section_d_watchlist: str = Field(..., description="D. 我的觀察清單動態")
    section_e_candidates: str = Field(..., description="E. 今日候選股與新機會亮點")
    section_f_risks: str = Field(..., description="F. 官方重大事件與風險警示")
    section_g_tracking: str = Field(..., description="G. 明日／下一交易日核心觀察重點")
    evidence_sources: list[str] = Field(default_factory=list, description="引用的客觀依據項目")


class SavedDailyBrief(BaseModel):
    """Persisted daily brief history entry."""

    brief_id: str
    brief_date: str
    generated_at: str
    data_as_of: str
    structured_brief: DeterministicDailyBrief
    ai_summary: DailyBriefAISummary | None = None
    ai_status: Literal["success", "not_generated", "failed", "live_ai_not_tested"] = "not_generated"
    ai_error: str | None = None


class CreateDailyBriefRequest(BaseModel):
    """Client request to collate today's deterministic daily brief."""

    target_date: str | None = None
    portfolio_holdings: list[dict[str, Any]] = Field(default_factory=list)
