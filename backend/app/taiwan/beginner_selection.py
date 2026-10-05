"""Beginner Stock Picker v1: deterministic, explainable screening priority.

This is NOT a return forecast.  The ranking is a fixed, versioned rule over
existing deterministic evidence (screener facts, PIT-adjusted trend, official
regulatory events, the buy-point/TradePlan price levels and market breadth).
AI never ranks, never produces prices and never contributes evidence here.

Unavailable evidence stays ``unavailable``; it is never filled with zero.
Critical evidence gates eligibility (``skip``); optional evidence only lowers
priority and is listed in ``data_gaps``.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.beginner_technical import (
    BeginnerTechnicalPanel,
    build_beginner_technical_panel,
    fugle_inner_outer_evidence,
    with_inner_outer_evidence,
)
from app.taiwan.buy_point import (
    BuyPointConditions,
    BuyPointMarketData,
    BuyPointRiskFilters,
    BuyPointSignal,
    BuyPointStrategy,
    evaluate_buy_point,
)
from app.taiwan.providers.external_models import ExternalProviderResult
from app.taiwan.screener import TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD
from app.taiwan.selection_v2 import INSTITUTIONAL_FLOW_RATIO_MIN
from app.taiwan.trade_plan import build_trade_plan

logger = logging.getLogger(__name__)

BEGINNER_SELECTION_VERSION = "beginner-selection-v1"

# Frozen v1 parameters.  Change them only together with the version string.
CANDIDATE_POOL_SIZE = 200  # most liquid supported common stocks
MIN_AMOUNT_TWD = TREND_LIQUIDITY_V1_MIN_AMOUNT_TWD
PULLBACK_MIN_PCT = 3.0
PULLBACK_MAX_PCT = 6.0
BREAKOUT_WINDOW = 20
NO_CHASE_EXTENSION_PCT = 10.0
REVENUE_YOY_POSITIVE = 10.0
REVENUE_YOY_NEGATIVE = -10.0
MARKET_FAVORABLE_RATIO = 0.6
MARKET_CAUTIOUS_RATIO = 0.4
STOP_LOOKBACK = 10
ATTENTION_LOOKBACK_DAYS = 10
CORPORATE_ACTION_LOOKBACK_DAYS = 40
SOCIAL_MAX_AGE_DAYS = 3
SELECT_MIN_PRIORITY = 2
MEDIUM_MIN_PRIORITY = 3
STRONG_MIN_PRIORITY = 6
# (positive, negative) contribution per dimension; neutral/unavailable add 0.
WEIGHTS: dict[str, tuple[int, int]] = {
    "trend": (3, -3),
    "capital_flow": (2, -2),
    "market_context": (1, -1),
    "price_position": (1, -2),
    "fundamental_context": (1, -1),
    "event_risk": (0, -2),
    "social_attention": (0, 0),  # auxiliary only: never affects rank
}

CRITICAL_EVIDENCE = ("instrument", "quote", "liquidity", "price_history", "corporate_actions", "regulatory")
OPTIONAL_EVIDENCE = (
    "capital_flow", "market_context", "price_position", "fundamental_context",
    "social_attention", "trade_plan",
)

SelectionState = Literal["watch", "wait_pullback", "wait_breakout", "no_chase", "skip"]
SignalStrength = Literal["weak", "medium", "strong"]
Direction = Literal["positive", "neutral", "negative", "unavailable"]
MarketState = Literal["favorable", "neutral", "cautious", "unavailable"]

DIMENSION_LABELS = {
    "trend": "趨勢",
    "capital_flow": "資金",
    "market_context": "市場環境",
    "price_position": "價格位置",
    "fundamental_context": "基本面",
    "event_risk": "事件風險",
    "social_attention": "市場討論",
}
REASON_ORDER = ("trend", "capital_flow", "fundamental_context", "price_position", "market_context")
RISK_ORDER = ("event_risk", "price_position", "trend", "capital_flow", "fundamental_context", "market_context")

_BEGINNER_STRATEGY = BuyPointStrategy(
    id=BEGINNER_SELECTION_VERSION,
    name="初學者選股價位",
    description="回檔 3–6% 觀察區與 20 日高點突破價",
    category="beginner",
    conditions=BuyPointConditions(
        pullback_min_pct=PULLBACK_MIN_PCT,
        pullback_max_pct=PULLBACK_MAX_PCT,
        breakout_window=BREAKOUT_WINDOW,
    ),
    # Regulatory gating is done by the eligibility filter below.
    risk_filters=BuyPointRiskFilters(
        exclude_disposition=False, exclude_suspension=False, exclude_delisting=False,
        exclude_capital_reduction_critical=False, exclude_regulatory_unknown=False,
        exclude_severe_event=False,
    ),
    created_at="1970-01-01T00:00:00+00:00",
    updated_at="1970-01-01T00:00:00+00:00",
)


# ── Contract ─────────────────────────────────────────────────────


class EvidenceReason(BaseModel):
    reason_code: str
    evidence_key: str
    direction: Direction
    display_text: str


class DimensionEvidence(BaseModel):
    key: str
    label: str
    status: Direction
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    source: str


class PlanLevels(BaseModel):
    rule_version: str
    entry_semantics: str
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    reference_high: float | None = None
    breakout_trigger: float | None = None
    stop_price: float
    evidence_as_of: str
    plan_identity: str


class BeginnerCandidate(BaseModel):
    symbol: str
    name: str
    industry: str | None = None
    close: float | None = None
    as_of: str | None = None
    rank: int | None = None
    selection_state: SelectionState
    signal_strength: SignalStrength
    reasons: list[EvidenceReason] = Field(default_factory=list)
    risks: list[EvidenceReason] = Field(default_factory=list)
    exclusion_reasons: list[EvidenceReason] = Field(default_factory=list)
    action_summary: str
    invalidation: str | None = None
    evidence_status: Literal["complete", "partial", "insufficient"]
    data_gaps: list[str] = Field(default_factory=list)
    dimensions: list[DimensionEvidence] = Field(default_factory=list)
    trade_plan: PlanLevels | None = None
    plan_unavailable_reason: str | None = None
    technical_panel: BeginnerTechnicalPanel | None = None
    intraday_context: ExternalProviderResult | None = None
    fx_context: ExternalProviderResult | None = None


class MarketSummary(BaseModel):
    state: MarketState
    headline: str
    explanation: str
    guidance: str
    as_of: str | None = None
    advance_count: int | None = None
    decline_count: int | None = None
    strongest_industries: list[str] = Field(default_factory=list)
    source: str = "market_intelligence+industry_intelligence"


class EvidencePolicy(BaseModel):
    critical: list[str] = Field(default_factory=lambda: list(CRITICAL_EVIDENCE))
    optional: list[str] = Field(default_factory=lambda: list(OPTIONAL_EVIDENCE))


class BeginnerSelectionResponse(BaseModel):
    version: str = BEGINNER_SELECTION_VERSION
    status: Literal["ready", "degraded", "unavailable"]
    as_of: str | None = None
    generated_at: str
    market: MarketSummary
    candidates: list[BeginnerCandidate] = Field(default_factory=list)
    not_selected: list[BeginnerCandidate] = Field(default_factory=list)
    universe_count: int = 0
    eligible_count: int = 0
    data_gaps: list[str] = Field(default_factory=list)
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    disclaimer: str = "訊號強度代表目前條件符合程度，不代表上漲機率。"


class BeginnerSymbolResponse(BaseModel):
    version: str = BEGINNER_SELECTION_VERSION
    generated_at: str
    market: MarketSummary
    candidate: BeginnerCandidate
    disclaimer: str = "訊號強度代表目前條件符合程度，不代表上漲機率。"


@dataclass(frozen=True)
class BeginnerSelectionSnapshot:
    key: tuple[Any, ...]
    response: BeginnerSelectionResponse
    candidates_by_symbol: dict[str, BeginnerCandidate]


class BeginnerFacts(BaseModel):
    """Per-symbol deterministic inputs; ``None`` always means unavailable."""

    symbol: str
    name: str = ""
    industry: str | None = None
    instrument_type: str | None = None
    in_universe: bool = True
    quote_date: str | None = None
    eligible_date: str | None = None
    close: float | None = None
    amount: float | None = None
    trend_status: Literal["verified", "unverified", "unavailable"] = "unavailable"
    adjusted_close: float | None = None
    ma20: float | None = None
    momentum_5d: float | None = None
    flow_ratio_5d: float | None = None
    revenue_yoy: float | None = None
    revenue_status: str = "unavailable"
    latest_eps: float | None = None
    pe: float | None = None
    risk_status: Literal["clear", "flagged", "unavailable"] = "unavailable"
    risk_reason: str | None = None
    attention_recent: bool | None = None
    recent_corporate_action: bool | None = None
    daily: list[dict[str, Any]] = Field(default_factory=list)
    social_status: Literal["available", "partial", "unavailable"] = "unavailable"
    social_mentions: int | None = None
    dcard_status: str | None = None
    technical_metrics: dict[str, Any] = Field(default_factory=dict)


# ── Pure rules ───────────────────────────────────────────────────


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _reason(code: str, key: str, direction: Direction, text: str) -> EvidenceReason:
    return EvidenceReason(reason_code=code, evidence_key=key, direction=direction, display_text=text)


def summarize_market(
    as_of: str | None,
    advance: int | None,
    decline: int | None,
    industries: list[tuple[str, float | None]] | None = None,
) -> MarketSummary:
    """Deterministic breadth summary; never a forecast of tomorrow's direction."""
    if advance is None or decline is None or advance + decline == 0:
        return MarketSummary(
            state="unavailable", headline="資料不足", as_of=as_of,
            explanation="市場漲跌資料不足，暫時無法判斷今天的市場狀態。",
            guidance="資料不足時不急著選股，先確認資料更新。",
        )
    ratio = advance / (advance + decline)
    strongest = [
        name for name, change in sorted(
            (item for item in industries or [] if _finite(item[1]) is not None),
            key=lambda item: (-(item[1] or 0.0), item[0]),
        )
        if (change or 0.0) > 0
    ][:2]
    sector = f"，{'、'.join(strongest)}類股相對強勢" if strongest else ""
    if ratio >= MARKET_FAVORABLE_RATIO:
        state: MarketState = "favorable"
        headline = "今天市場偏強"
        explanation = f"上漲 {advance} 家、下跌 {decline} 家，多數股票表現偏正向{sector}。"
        guidance = "今天較適合尋找趨勢仍在、但沒有過度追高的股票。"
    elif ratio <= MARKET_CAUTIOUS_RATIO:
        state = "cautious"
        headline = "今天市場偏弱"
        explanation = f"上漲 {advance} 家、下跌 {decline} 家，多數股票下跌{sector}。"
        guidance = "今天宜保守，以觀察為主，不急著進場。"
    else:
        state = "neutral"
        headline = "今天市場中性"
        explanation = f"上漲 {advance} 家、下跌 {decline} 家，漲跌互見{sector}。"
        guidance = "只挑趨勢明確、法人也在買的股票，不必勉強出手。"
    return MarketSummary(
        state=state, headline=headline, explanation=explanation, guidance=guidance,
        as_of=as_of, advance_count=advance, decline_count=decline, strongest_industries=strongest,
    )


def mark_stale(market: MarketSummary, eligible_date: str | None) -> MarketSummary:
    """A breadth snapshot older than the latest session cannot describe today."""
    if market.state == "unavailable" or (eligible_date is not None and market.as_of == eligible_date):
        return market
    return market.model_copy(update={
        "state": "unavailable",
        "headline": "資料尚未更新",
        "explanation": f"最近一個資料日（{market.as_of}）：{market.explanation}",
        "guidance": "行情尚未更新到最新交易日，先更新資料再選股。",
    })


def eligibility_failures(facts: BeginnerFacts) -> list[EvidenceReason]:
    """Critical evidence gate.  Any failure means ``skip``."""
    out: list[EvidenceReason] = []
    neg: Direction = "negative"
    if not facts.in_universe or facts.instrument_type != "stock":
        out.append(_reason("instrument_unsupported", "eligibility.instrument", neg,
                           "不是目前支援的上市櫃普通股（ETF、權證等暫不納入）。"))
    close = _finite(facts.close)
    if close is None or close <= 0 or not facts.quote_date:
        out.append(_reason("quote_unavailable", "eligibility.quote", neg, "最新行情資料不可用。"))
    elif not facts.eligible_date or facts.quote_date != facts.eligible_date:
        out.append(_reason("quote_stale", "eligibility.quote_date", neg,
                           f"行情停在 {facts.quote_date}，不是最新交易日，資料過舊。"))
    amount = _finite(facts.amount)
    if amount is None:
        out.append(_reason("liquidity_unavailable", "eligibility.amount", neg,
                           "成交金額資料不可用，無法確認流動性。"))
    elif amount < MIN_AMOUNT_TWD:
        out.append(_reason("illiquid", "eligibility.amount", neg,
                           "今日成交金額不足 5,000 萬元，流動性偏低。"))
    if facts.trend_status == "unavailable":
        out.append(_reason("corporate_action_unverified", "eligibility.corporate_actions", neg,
                           "除權息等公司行動資料無法驗證，價格趨勢不可靠。"))
    elif facts.trend_status == "unverified":
        out.append(_reason("price_integrity", "eligibility.price_history", neg,
                           "近 20 個交易日價格不完整，或除權息調整無法驗證。"))
    if facts.risk_status == "unavailable":
        out.append(_reason("risk_unverified", "eligibility.regulatory", neg,
                           "無法確認是否為處置、暫停交易等異常股票。"))
    elif facts.risk_status == "flagged":
        out.append(_reason("regulatory_risk", "eligibility.regulatory", neg,
                           f"目前有{facts.risk_reason or '處置或暫停交易'}等風險。"))
    return out


def _price_metrics(facts: BeginnerFacts, signal: BuyPointSignal | None) -> tuple[float | None, float | None]:
    """(pullback % from prior 20D closing high, extension % above PIT MA20)."""
    adj, ma20 = _finite(facts.adjusted_close), _finite(facts.ma20)
    extension = (adj - ma20) / ma20 * 100 if adj is not None and ma20 is not None and ma20 > 0 else None
    pullback = None
    if signal is not None and facts.recent_corporate_action is False:
        high, price = _finite(signal.reference_high), _finite(signal.price)
        if high is not None and price is not None and high > 0:
            pullback = (high - price) / high * 100
    return pullback, extension


def build_dimensions(
    facts: BeginnerFacts, market: MarketSummary, signal: BuyPointSignal | None,
) -> list[DimensionEvidence]:
    dims: list[DimensionEvidence] = []

    def add(key: str, status: Direction, text: str, source: str, **evidence: Any) -> None:
        dims.append(DimensionEvidence(
            key=key, label=DIMENSION_LABELS[key], status=status, explanation=text,
            evidence={k: v for k, v in evidence.items() if v is not None}, source=source,
        ))

    adj, ma20, mom = _finite(facts.adjusted_close), _finite(facts.ma20), _finite(facts.momentum_5d)
    if facts.trend_status != "verified" or adj is None or ma20 is None or mom is None:
        add("trend", "unavailable", "趨勢資料不可用。", "screener.trend_pit")
    else:
        trend_ev = {"adjusted_close": adj, "ma20": round(ma20, 4), "momentum_5d_pct": round(mom * 100, 2)}
        if adj > ma20 and mom > 0:
            add("trend", "positive", "股價站在 20 日均線之上，近 5 日也在上漲，趨勢目前偏強。", "screener.trend_pit", **trend_ev)
        elif adj < ma20 and mom < 0:
            add("trend", "negative", "股價在 20 日均線之下，近 5 日走弱，趨勢偏弱。", "screener.trend_pit", **trend_ev)
        else:
            add("trend", "neutral", "股價在 20 日均線附近整理，趨勢方向還不明確。", "screener.trend_pit", **trend_ev)

    ratio = _finite(facts.flow_ratio_5d)
    if ratio is None:
        add("capital_flow", "unavailable", "法人資料目前不可用。", "screener.institutional_5d")
    elif ratio >= INSTITUTIONAL_FLOW_RATIO_MIN:
        add("capital_flow", "positive", "近 5 日三大法人合計買超，資金在流入。", "screener.institutional_5d", flow_ratio_5d=round(ratio, 4))
    elif ratio <= -INSTITUTIONAL_FLOW_RATIO_MIN:
        add("capital_flow", "negative", "近 5 日三大法人合計賣超，資金在流出。", "screener.institutional_5d", flow_ratio_5d=round(ratio, 4))
    else:
        add("capital_flow", "neutral", "近 5 日法人買賣不明顯。", "screener.institutional_5d", flow_ratio_5d=round(ratio, 4))

    sector_note = (
        f"所屬{facts.industry}類股今天相對強勢。"
        if facts.industry and facts.industry in market.strongest_industries else ""
    )
    market_map: dict[MarketState, tuple[Direction, str]] = {
        "favorable": ("positive", "今天多數股票上漲，市場環境有利。"),
        "neutral": ("neutral", "今天漲跌互見，市場環境中性。"),
        "cautious": ("negative", "今天多數股票下跌，市場環境偏保守。"),
        "unavailable": ("unavailable", "市場資料不足。"),
    }
    status, text = market_map[market.state]
    add("market_context", status, text + (sector_note if status != "unavailable" else ""),
        "market_intelligence", market_state=market.state,
        advance_count=market.advance_count, decline_count=market.decline_count)

    pullback, extension = _price_metrics(facts, signal)
    if extension is not None and extension > NO_CHASE_EXTENSION_PCT:
        # PIT-adjusted, so valid even right after a corporate action.
        add("price_position", "negative", f"股價比 20 日均線高出 {extension:.1f}%，短線漲多，追價風險高。",
            "screener.trend_pit", extension_above_ma20_pct=round(extension, 2))
    elif facts.recent_corporate_action is not False:
        add("price_position", "unavailable", "近期有除權息或無法確認，價格位置需調整後才能判斷。", "buy_point.levels")
    elif pullback is None or extension is None:
        add("price_position", "unavailable", "近 20 日價格資料不足，無法判斷價格位置。", "buy_point.levels")
    else:
        ev: dict[str, Any] = {
            "pullback_from_20d_high_pct": round(pullback, 2),
            "extension_above_ma20_pct": round(extension, 2),
            "reference_high": signal.reference_high if signal else None,
        }
        if pullback <= 0:
            add("price_position", "neutral", "股價正在創近 20 日新高。", "buy_point.levels", **ev)
        elif pullback < PULLBACK_MIN_PCT:
            add("price_position", "neutral", f"股價接近近 20 日高點（只差 {pullback:.1f}%）。", "buy_point.levels", **ev)
        elif pullback <= PULLBACK_MAX_PCT:
            add("price_position", "positive", f"股價從近 20 日高點回落 {pullback:.1f}%，位在回檔觀察區。", "buy_point.levels", **ev)
        else:
            add("price_position", "neutral", f"股價距離近 20 日高點 {pullback:.1f}%，仍在整理。", "buy_point.levels", **ev)

    yoy = _finite(facts.revenue_yoy) if facts.revenue_status == "available" else None
    eps = _finite(facts.latest_eps)
    fund_ev = {"revenue_yoy_pct": yoy, "latest_eps": eps, "pe": _finite(facts.pe)}
    if yoy is None:
        add("fundamental_context", "unavailable", "月營收資料目前不可用。", "screener.fundamentals", **fund_ev)
    elif eps is not None and eps < 0:
        add("fundamental_context", "negative", "最近一季財報為虧損。", "screener.fundamentals", **fund_ev)
    elif yoy >= REVENUE_YOY_POSITIVE:
        add("fundamental_context", "positive", f"最新月營收比去年同期成長 {yoy:.1f}%。", "screener.fundamentals", **fund_ev)
    elif yoy <= REVENUE_YOY_NEGATIVE:
        add("fundamental_context", "negative", f"最新月營收比去年同期衰退 {abs(yoy):.1f}%。", "screener.fundamentals", **fund_ev)
    else:
        add("fundamental_context", "neutral", f"最新月營收與去年同期差不多（{yoy:+.1f}%）。", "screener.fundamentals", **fund_ev)

    if facts.risk_status == "unavailable":
        add("event_risk", "unavailable", "事件風險資料目前不可用。", "events.regulatory")
    elif facts.risk_status == "flagged":
        add("event_risk", "negative", f"目前有{facts.risk_reason or '處置或暫停交易'}等風險。", "events.regulatory", risk_reason=facts.risk_reason)
    elif facts.attention_recent:
        add("event_risk", "negative", "近期被列為注意股，股價波動可能較大。", "events.regulatory", attention_recent=True)
    else:
        add("event_risk", "neutral", "目前沒有處置、暫停交易或注意股等已知風險事件。", "events.regulatory", attention_recent=False)

    mentions = facts.social_mentions
    if facts.social_status == "unavailable" or mentions is None:
        add("social_attention", "unavailable", "來源目前不可用。", "social_sentiment.snapshot")
    else:
        text = (f"PTT 與 Dcard 近期提及 {mentions} 次" if facts.dcard_status == "available"
                else f"PTT 近期提及 {mentions} 次；Dcard 來源目前不可用")
        add("social_attention", "neutral", f"{text}。僅供參考，不影響排序。",
            "social_sentiment.snapshot", mentions=mentions, dcard_status=facts.dcard_status)
    return dims


def priority(dimensions: list[DimensionEvidence]) -> int:
    """Internal ordering score only; never shown to users."""
    total = 0
    for dim in dimensions:
        pos, neg = WEIGHTS[dim.key]
        total += pos if dim.status == "positive" else neg if dim.status == "negative" else 0
    return total


def strength(score: int) -> SignalStrength:
    return "strong" if score >= STRONG_MIN_PRIORITY else "medium" if score >= MEDIUM_MIN_PRIORITY else "weak"


def decide_state(
    dims: dict[str, DimensionEvidence], pullback: float | None, extension: float | None, score: int,
) -> tuple[SelectionState, EvidenceReason | None]:
    trend = dims["trend"].status
    if trend == "positive":
        if extension is not None and extension > NO_CHASE_EXTENSION_PCT:
            state: SelectionState = "no_chase"
        elif pullback is None:
            state = "watch"
        elif pullback < PULLBACK_MIN_PCT:
            state = "wait_pullback"
        elif pullback <= PULLBACK_MAX_PCT:
            state = "watch"
        else:
            state = "wait_breakout"
    elif trend == "neutral" and pullback is not None and pullback < PULLBACK_MIN_PCT \
            and dims["capital_flow"].status != "negative":
        state = "wait_breakout"
    else:
        text = "趨勢偏弱，暫時略過。" if trend == "negative" else "趨勢還不明確，暫時略過。"
        return "skip", _reason("trend_not_ready", "trend", "negative", text)
    if score < SELECT_MIN_PRIORITY:
        return "skip", _reason("priority_too_low", "trend", "negative", "整體條件不足（資金、市場或風險偏弱），暫時略過。")
    return state, None


def _plan(facts: BeginnerFacts, state: SelectionState, signal: BuyPointSignal | None) -> tuple[PlanLevels | None, str | None]:
    if state not in ("watch", "wait_pullback", "wait_breakout"):
        return None, None
    if facts.recent_corporate_action is not False:
        return None, "近期有除權息或無法確認，暫不提供價位。"
    if signal is None:
        return None, "近 20 日價格資料不足，暫不提供價位。"
    rows = facts.daily[-STOP_LOOKBACK:]
    lows = [v for v in (_finite(r.get("low")) for r in rows) if v is not None and v > 0]
    if len(lows) != STOP_LOOKBACK or str(rows[-1].get("date"))[:10] != facts.quote_date:
        return None, "近 10 日低點資料不完整，暫不提供價位。"
    try:
        plan = build_trade_plan(
            signal, instrument_type="stock",
            entry_semantics="breakout_stop" if state == "wait_breakout" else "pullback_limit",
            stop_reference_price=min(lows), stop_lookback=STOP_LOOKBACK,
        )
    except ValueError as exc:
        logger.debug("beginner plan unavailable for %s: %s", facts.symbol, exc)
        return None, "價位條件不成立（停損價不低於進場價），暫不提供價位。"
    return PlanLevels(
        rule_version=plan.rule_version, entry_semantics=plan.entry_semantics,
        entry_zone_low=plan.entry_zone_low, entry_zone_high=plan.entry_zone_high,
        reference_high=plan.reference_high, breakout_trigger=plan.breakout_trigger,
        stop_price=plan.stop_price,
        evidence_as_of=plan.evidence_as_of.isoformat(), plan_identity=plan.plan_identity,
    ), None


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _action(state: SelectionState, plan: PlanLevels | None, extension: float | None,
            skip_reason: EvidenceReason | None, failures: list[EvidenceReason]) -> tuple[str, str | None]:
    zone = f"{_fmt(plan.entry_zone_low)}～{_fmt(plan.entry_zone_high)}" if plan and plan.entry_zone_low is not None else None
    stop_text = f"若股價跌破 {_fmt(plan.stop_price)}（近 10 日低點），原本的理由就失效。" if plan else None
    ma_text = "若收盤跌破 20 日均線，原本的趨勢理由就失效。"
    if state == "watch":
        summary = (f"股價已回到觀察區 {zone}，趨勢仍偏強，可列入觀察名單。" if zone
                   else "趨勢偏強，可列入觀察名單；目前沒有可靠的觀察價位。")
        return summary, stop_text or ma_text
    if state == "wait_pullback":
        summary = (f"股票目前偏強，但距離近期高點較近。觀察區：{zone}。" if zone
                   else "股票目前偏強，但距離近期高點較近，等回檔再看；目前沒有可靠的觀察價位。")
        return summary, stop_text or ma_text
    if state == "wait_breakout":
        summary = (f"需要看到價格突破 {_fmt(plan.breakout_trigger)} 後再重新評估。" if plan and plan.breakout_trigger is not None
                   else "等價格突破近 20 日高點後再重新評估；目前沒有可靠的突破價位。")
        return summary, stop_text or ma_text
    if state == "no_chase":
        ext = f"（高出 20 日均線 {extension:.1f}%）" if extension is not None else ""
        return f"現價距離合理觀察區過遠{ext}，不宜追價，等拉回再評估。", ma_text
    first = failures[0] if failures else skip_reason
    return (first.display_text if first else "條件不足，暫時略過。"), None


def evaluate_candidate(
    facts: BeginnerFacts, market: MarketSummary, signal: BuyPointSignal | None = None,
) -> tuple[BeginnerCandidate, int]:
    """Pure evaluation of one symbol.  Returns the candidate and internal priority."""
    if signal is None:
        signal = level_signal(facts)
    failures = eligibility_failures(facts)
    dims = build_dimensions(facts, market, signal)
    by_key = {d.key: d for d in dims}
    score = priority(dims)
    pullback, extension = _price_metrics(facts, signal)
    skip_reason: EvidenceReason | None = None
    if failures:
        state: SelectionState = "skip"
    else:
        state, skip_reason = decide_state(by_key, pullback, extension, score)
    plan, plan_reason = _plan(facts, state, signal)
    action_summary, invalidation = _action(state, plan, extension, skip_reason, failures)

    reasons = [
        _reason(f"{key}_positive", key, "positive", by_key[key].explanation)
        for key in REASON_ORDER if by_key[key].status == "positive"
    ][:3]
    risks = [
        _reason(f"{key}_negative", key, "negative", by_key[key].explanation)
        for key in RISK_ORDER if by_key[key].status == "negative"
    ][:3]
    gaps = [f"{d.label}：{d.explanation}" for d in dims if d.status == "unavailable"]
    if plan_reason:
        gaps.append(f"價位：{plan_reason}")
    evidence_status: Literal["complete", "partial", "insufficient"] = (
        "insufficient" if failures else "partial" if gaps else "complete"
    )
    candidate = BeginnerCandidate(
        symbol=facts.symbol, name=facts.name, industry=facts.industry,
        close=_finite(facts.close), as_of=facts.quote_date,
        selection_state=state,
        signal_strength="weak" if state == "skip" else strength(score),
        reasons=reasons if state != "skip" else [],
        risks=risks,
        exclusion_reasons=failures or ([skip_reason] if skip_reason else []),
        action_summary=action_summary, invalidation=invalidation,
        evidence_status=evidence_status, data_gaps=gaps, dimensions=dims,
        trade_plan=plan, plan_unavailable_reason=plan_reason,
    )
    regulatory_risk = (
        f"目前有{facts.risk_reason or '處置或暫停交易'}等風險。"
        if facts.risk_status == "flagged" else None
    )
    candidate.technical_panel = build_beginner_technical_panel(
        current_price=_finite(facts.close), as_of=facts.quote_date, plan=plan,
        metrics=facts.technical_metrics, market=market, industry=facts.industry,
        action_summary=action_summary, regulatory_risk=regulatory_risk,
    )
    return candidate, score


def level_signal(facts: BeginnerFacts) -> BuyPointSignal | None:
    """Reuse the deterministic buy-point evaluator for 20D high / pullback levels."""
    close = _finite(facts.close)
    if close is None or not facts.daily:
        return None
    return evaluate_buy_point(_BEGINNER_STRATEGY, BuyPointMarketData(
        symbol=facts.symbol, name=facts.name, data_as_of=facts.quote_date,
        freshness="daily_cached", price=close, daily=facts.daily,
    ))


def rank_candidates(
    evaluated: list[tuple[BeginnerCandidate, int]], amounts: dict[str, float] | None = None,
) -> list[BeginnerCandidate]:
    """Selected candidates by priority desc, turnover desc, symbol asc; ranks from 1."""
    amounts = amounts or {}
    chosen = [(c, s) for c, s in evaluated if c.selection_state != "skip"]
    chosen.sort(key=lambda item: (-item[1], -(amounts.get(item[0].symbol) or 0.0), item[0].symbol))
    return [c.model_copy(update={"rank": i}) for i, (c, _s) in enumerate(chosen, 1)]


# ── Assembly from existing local stores ──────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SNAPSHOT_CONDITION = threading.Condition()
_SNAPSHOT: BeginnerSelectionSnapshot | None = None
_SNAPSHOT_BUILDING = False


def _path_generation(path: Path | None) -> tuple[Any, ...] | None:
    """Return a cheap file identity for cache invalidation."""
    if path is None:
        return None
    try:
        path = Path(path)
        if path.is_file():
            stat = path.stat()
            return (str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        if not path.is_dir():
            return (str(path), "missing")
        files = []
        for item in path.rglob("*"):
            if item.is_file():
                stat = item.stat()
                files.append((str(item.relative_to(path)), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
        return (str(path), tuple(sorted(files)))
    except OSError:
        return (str(path), "unavailable")


def clear_beginner_selection_snapshot() -> None:
    """Clear the process-local snapshot. Intended for tests and controlled refreshes."""
    global _SNAPSHOT
    with _SNAPSHOT_CONDITION:
        _SNAPSHOT = None


class BeginnerSelectionService:
    """Collects existing deterministic evidence and applies the pure v1 rules."""

    def __init__(self, screener: Any = None) -> None:
        if screener is None:
            from app.taiwan.screener import TaiwanScreenerService

            screener = TaiwanScreenerService()
        self.screener = screener

    def build(self, limit: int = 20) -> BeginnerSelectionResponse:
        key = self._cache_key()
        snapshot = self._get_or_build_snapshot(key) if key is not None else self._build_snapshot(None)
        response = snapshot.response.model_copy(deep=True, update={
            "generated_at": _now(),
            "candidates": snapshot.response.candidates[:limit],
            "not_selected": snapshot.response.not_selected[:limit],
        })
        # External context is an overlay only. It cannot enter the cached rank,
        # selection state, TradePlan, or any scoring input.
        visible = response.candidates[:6]
        self._request_intraday([candidate.symbol for candidate in visible])
        return response

    def _build_snapshot(self, key: tuple[Any, ...] | None) -> BeginnerSelectionSnapshot:
        eligible_date = key[1] if key is not None and len(key) > 1 else None
        market, facts, amounts, gaps, universe = self._collect(None, eligible_date=eligible_date)
        evaluated = [evaluate_candidate(f, market) for f in facts]
        ranked = rank_candidates(evaluated, amounts)
        skipped = sorted(
            (c for c, _s in evaluated if c.selection_state == "skip"),
            key=lambda c: (-(amounts.get(c.symbol) or 0.0), c.symbol),
        )
        eligible = sum(1 for c, _s in evaluated if c.evidence_status != "insufficient")
        status: Literal["ready", "degraded", "unavailable"] = (
            "unavailable" if not facts else "degraded" if gaps or eligible == 0 else "ready"
        )
        response = BeginnerSelectionResponse(
            status=status, as_of=market.as_of, generated_at=_now(), market=market,
            candidates=ranked, not_selected=skipped,
            universe_count=universe, eligible_count=eligible, data_gaps=gaps,
        )
        return BeginnerSelectionSnapshot(
            key=key or ("uncached",),
            response=response,
            candidates_by_symbol={candidate.symbol: candidate for candidate in [*ranked, *skipped]},
        )

    def _cache_key(self) -> tuple[Any, ...] | None:
        daily_dir = getattr(getattr(self.screener, "daily_store", None), "_data_dir", None)
        if daily_dir is None:
            return None
        try:
            from app.config import settings

            taiwan_root = Path(settings.data_dir) / "taiwan"
            cache_dir = getattr(getattr(self.screener, "cache", None), "cache_dir", None)
            paths: list[Path | None] = [
                Path(daily_dir),
                getattr(getattr(self.screener, "institutional_store", None), "_data_dir", None),
                getattr(getattr(self.screener, "margin_store", None), "_data_dir", None),
                getattr(getattr(self.screener, "security_master", None), "cache_path", None),
                getattr(getattr(self.screener, "action_store", None), "path", None),
                getattr(getattr(self.screener, "revenue_evidence_store", None), "ledger", None),
                taiwan_root / "events_cache" / "regulatory_events.json",
                Path(settings.data_dir) / "social_sentiment" / "latest.json",
            ]
            if cache_dir is not None:
                paths.extend(Path(cache_dir) / dataset for dataset in (
                    "TaiwanStockMonthRevenue",
                    "TaiwanStockFinancialStatements",
                    "TaiwanValuation",
                    "TaiwanStockShareholding",
                    "TaiwanStockSecuritiesLending",
                ))
            generations = tuple(generation for path in paths if (generation := _path_generation(path)) is not None)
            return (BEGINNER_SELECTION_VERSION, self._eligible_date(), generations) if generations else None
        except Exception as exc:
            logger.debug("beginner selection cache key unavailable: %s", type(exc).__name__)
            return None

    def _get_cached_snapshot(self, key: tuple[Any, ...]) -> BeginnerSelectionSnapshot | None:
        global _SNAPSHOT
        with _SNAPSHOT_CONDITION:
            while _SNAPSHOT_BUILDING:
                _SNAPSHOT_CONDITION.wait()
            return _SNAPSHOT if _SNAPSHOT is not None and _SNAPSHOT.key == key else None

    def _get_or_build_snapshot(self, key: tuple[Any, ...]) -> BeginnerSelectionSnapshot:
        global _SNAPSHOT, _SNAPSHOT_BUILDING
        with _SNAPSHOT_CONDITION:
            while True:
                if _SNAPSHOT is not None and _SNAPSHOT.key == key:
                    return _SNAPSHOT
                if not _SNAPSHOT_BUILDING:
                    _SNAPSHOT_BUILDING = True
                    break
                _SNAPSHOT_CONDITION.wait()
        try:
            snapshot = self._build_snapshot(key)
        except Exception:
            with _SNAPSHOT_CONDITION:
                _SNAPSHOT_BUILDING = False
                _SNAPSHOT_CONDITION.notify_all()
            raise
        with _SNAPSHOT_CONDITION:
            _SNAPSHOT = snapshot
            _SNAPSHOT_BUILDING = False
            _SNAPSHOT_CONDITION.notify_all()
        return snapshot

    def _snapshot_candidate(self, symbol: str) -> BeginnerSymbolResponse | None:
        key = self._cache_key()
        if key is None:
            return None
        snapshot = self._get_cached_snapshot(key)
        if snapshot is None:
            return None
        candidate = snapshot.candidates_by_symbol.get(symbol)
        if candidate is None:
            return None
        return BeginnerSymbolResponse(
            generated_at=_now(),
            market=snapshot.response.market.model_copy(deep=True),
            candidate=candidate.model_copy(deep=True),
        )

    def _build_uncached_symbol(self, symbol: str) -> BeginnerSymbolResponse:
        market, facts, _amounts, _gaps, _universe = self._collect([symbol])
        target = facts[0] if facts else BeginnerFacts(symbol=symbol, in_universe=False)
        candidate, _score = evaluate_candidate(target, market)
        return BeginnerSymbolResponse(generated_at=_now(), market=market, candidate=candidate)

    def evaluate_symbol(self, symbol: str) -> BeginnerSymbolResponse:
        cached = self._snapshot_candidate(symbol)
        response = cached if cached is not None else self._build_uncached_symbol(symbol)
        self._request_intraday([response.candidate.symbol])
        response.candidate = self._with_external(response.candidate, fx=self._fx_context())
        return response

    @staticmethod
    def _fx_context() -> ExternalProviderResult:
        try:
            from app.taiwan.providers.fx_context import get_frankfurter_fx_provider

            return get_frankfurter_fx_provider().cached_context()
        except Exception as exc:
            logger.debug("beginner FX context unavailable: %s", type(exc).__name__)
            return ExternalProviderResult.unavailable(
                "frankfurter:v2:provider:CBC", "provider_unavailable",
            )

    @staticmethod
    def _request_intraday(symbols: list[str]) -> None:
        try:
            from app.taiwan.realtime.fugle_provider import get_fugle_aggregates_provider

            get_fugle_aggregates_provider().request_symbols(symbols)
        except Exception as exc:
            logger.debug("beginner Fugle subscription unavailable: %s", type(exc).__name__)

    @staticmethod
    def _with_external(
        candidate: BeginnerCandidate, *, fx: ExternalProviderResult,
    ) -> BeginnerCandidate:
        copied = candidate.model_copy(deep=True, update={"fx_context": fx.model_copy(deep=True)})
        try:
            from app.taiwan.external_context import intraday_context
            from app.taiwan.realtime.fugle_provider import get_fugle_aggregates_provider

            observation = get_fugle_aggregates_provider().observe(copied.symbol)
            copied.intraday_context = intraday_context(copied.symbol)
            if copied.technical_panel is not None:
                copied.technical_panel = with_inner_outer_evidence(
                    copied.technical_panel,
                    fugle_inner_outer_evidence(observation),
                )
        except Exception as exc:
            logger.debug("beginner intraday context unavailable: %s", type(exc).__name__)
            copied.intraday_context = ExternalProviderResult.unavailable(
                "fugle_marketdata:websocket:aggregates", "provider_unavailable",
            )
        return copied

    def _collect(self, scope: list[str] | None, *, eligible_date: str | None = None):
        # The collectors below only read local stores; failures degrade to unavailable.
        from app.taiwan.screener import TaiwanScreenerRequest

        req = TaiwanScreenerRequest(
            instrument="ALL" if scope else "stock",
            amount_min=None if scope else MIN_AMOUNT_TWD,
            sort_by="amount", sort_order="desc", page=1,
            page_size=len(scope) if scope else CANDIDATE_POOL_SIZE,
            symbol_scope=scope, extended_factors=True,
        )
        screen = self.screener.run(req)
        items = screen.items
        as_of = screen.data_dates.daily_as_of
        gaps: list[str] = []
        eligible_date = eligible_date if eligible_date is not None else self._eligible_date()
        market = self._market(as_of)
        if as_of is not None and as_of != eligible_date:
            gaps.append(f"行情停在 {as_of}，尚未更新到最新交易日")
            market = mark_stale(market, eligible_date)
        elif market.state == "unavailable":
            gaps.append("市場環境資料不足")
        if not items or as_of is None:
            return market, [], {}, gaps or ["行情資料不可用"], 0
        as_of_date = date.fromisoformat(as_of)
        symbols = [item.symbol for item in items]

        trend = self._trend(symbols, as_of_date)
        if trend is None:
            gaps.append("除權息驗證資料不可用")
        recent_actions = self._recent_actions(as_of_date)
        risk_ctx = self._risk_context()
        if risk_ctx is None:
            gaps.append("事件風險資料不可用")
        daily = self._daily_rows(symbols, as_of_date)
        technical = self._technical_metrics(symbols, as_of_date)
        social = self._social(as_of_date)
        if social["status"] == "unavailable":
            gaps.append("市場討論來源目前不可用")
        elif social["dcard_status"] != "available":
            gaps.append("Dcard 來源目前不可用")

        facts: list[BeginnerFacts] = []
        for item in items:
            t = trend.get(item.symbol) if trend is not None else None
            risk_status, risk_reason, attention = self._risk_for(item.symbol, as_of_date, risk_ctx)
            technical_metrics = dict(technical.get(item.symbol, {}))
            institutional_5d = (
                getattr(item, "foreign_net_5d", None),
                getattr(item, "investment_trust_net_5d", None),
                getattr(item, "dealer_net_5d", None),
            )
            technical_metrics.update({
                "foreign_net_5d": institutional_5d[0],
                "investment_trust_net_5d": institutional_5d[1],
                "dealer_net_5d": institutional_5d[2],
                "institutional_complete_sessions": (
                    5 if all(value is not None for value in institutional_5d) else 0
                ),
                "institutional_as_of": getattr(item, "institutional_date", None),
                "institutional_status": getattr(item, "institutional_status", "unavailable"),
                "margin_balance": getattr(item, "margin_balance", None),
                "margin_change": getattr(item, "margin_balance_change", None),
                "short_balance": getattr(item, "short_balance", None),
                "short_change": getattr(item, "short_balance_change", None),
                "margin_as_of": getattr(item, "margin_date", None),
                "margin_status": getattr(item, "margin_status", "unavailable"),
                "revenue_yoy": getattr(item, "revenue_yoy", None),
                "revenue_mom": getattr(item, "revenue_mom", None),
                "eps": getattr(item, "latest_eps", None),
                "pe": getattr(item, "pe", None),
                "revenue_status": getattr(item, "revenue_status", "unavailable"),
                "revenue_as_of": getattr(item, "revenue_latest_period", None),
                "financials_as_of": getattr(item, "financials_as_of", None),
                "valuation_as_of": getattr(item, "valuation_as_of", None),
                "quote_freshness": (
                    "current" if item.quote_date == eligible_date else "stale"
                ),
            })
            facts.append(BeginnerFacts(
                symbol=item.symbol, name=item.name, industry=item.industry,
                instrument_type=item.instrument_type, quote_date=item.quote_date,
                eligible_date=eligible_date, close=item.close, amount=item.amount,
                trend_status="unavailable" if trend is None else "verified" if t else "unverified",
                adjusted_close=t[0] if t else None, ma20=t[1] if t else None,
                momentum_5d=t[2] if t else None,
                flow_ratio_5d=item.institutional_flow_ratio_5d,
                revenue_yoy=item.revenue_yoy, revenue_status=item.revenue_status,
                latest_eps=item.latest_eps, pe=item.pe,
                risk_status=risk_status, risk_reason=risk_reason, attention_recent=attention,
                recent_corporate_action=(item.symbol in recent_actions) if recent_actions is not None else None,
                daily=daily.get(item.symbol, []),
                social_status=social["status"],
                social_mentions=(social["mentions"].get(item.symbol, 0) if social["status"] != "unavailable" else None),
                dcard_status=social["dcard_status"],
                technical_metrics=technical_metrics,
            ))
        amounts = {item.symbol: float(item.amount) for item in items if item.amount is not None}
        return market, facts, amounts, gaps, len(items)

    def _market(self, as_of: str | None) -> MarketSummary:
        if as_of is None:
            return summarize_market(None, None, None)
        try:
            from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
            from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService

            target = date.fromisoformat(as_of)
            totals = TaiwanMarketIntelligenceService().get_snapshot(target).market_totals
            try:
                industries = [
                    (row.industry, row.average_change_pct)
                    for row in TaiwanIndustryIntelligenceService().get_snapshot(target).industries
                ]
            except Exception as exc:
                logger.debug("beginner industry context unavailable: %s", type(exc).__name__)
                industries = []
            if totals.traded_count == 0:
                return summarize_market(as_of, None, None)
            return summarize_market(as_of, totals.advance_count, totals.decline_count, industries)
        except Exception as exc:
            logger.debug("beginner market context unavailable: %s", type(exc).__name__)
            return summarize_market(as_of, None, None)

    @staticmethod
    def _eligible_date() -> str | None:
        try:
            from app.taiwan.daily_update import resolve_target_latest_trading_date

            return resolve_target_latest_trading_date().isoformat()
        except Exception as exc:
            logger.debug("beginner eligible date unavailable: %s", type(exc).__name__)
            return None

    def _trend(self, symbols: list[str], as_of: date) -> dict[str, tuple[float, float, float]] | None:
        """PIT-adjusted close/MA20/5D momentum from the screener's canonical trend rule."""
        try:
            frame, _status, _section = self.screener._compute_trend_indicators(symbols, as_of)
        except Exception as exc:
            logger.debug("beginner trend unavailable: %s", type(exc).__name__)
            return None
        if frame is None:
            return None
        out: dict[str, tuple[float, float, float]] = {}
        for row in frame.iter_rows(named=True) if not frame.is_empty() else []:
            values = (_finite(row.get("trend_adjusted_close")), _finite(row.get("trend_ma20")),
                      _finite(row.get("trend_momentum_5d")))
            if all(v is not None for v in values):
                out[row["symbol"]] = values  # type: ignore[assignment]
        return out

    def _recent_actions(self, as_of: date) -> set[str] | None:
        try:
            events = self.screener.action_store.read_verified_window(
                as_of - timedelta(days=CORPORATE_ACTION_LOOKBACK_DAYS), as_of
            )
        except Exception as exc:
            logger.debug("beginner corporate actions unavailable: %s", type(exc).__name__)
            return None
        return None if events is None else {event.symbol for event in events}

    def _risk_context(self) -> tuple[Any, list[Any], Any, Any] | None:
        try:
            from app.taiwan.events_service import get_event_service

            svc = get_event_service()
            events, status, _as_of = svc.get_cached_regulatory_snapshot()
        except Exception as exc:
            logger.debug("beginner regulatory evidence unavailable: %s", type(exc).__name__)
            return None
        if status != "available":
            return None
        return (
            svc,
            events,
            getattr(self.screener, "calendar", None),
            getattr(self.screener, "census_store", None),
        )

    @staticmethod
    def _risk_for(symbol: str, as_of: date, ctx: tuple[Any, list[Any], Any, Any] | None):
        if ctx is None:
            return "unavailable", None, None
        svc, events, calendar, census_store = ctx
        try:
            if calendar is None:
                return "unavailable", None, None

            def observed(day: date):
                if census_store is None:
                    return calendar.day_evidence(day, "TWSE")
                return census_store.day_evidence("TWSE", day, calendar=calendar)

            risk_target_date = calendar.next_potential_session(as_of, observed)
            risk = svc.check_symbol_risk_status(
                symbol, target_date=risk_target_date, events=events
            )
        except Exception:
            return "unavailable", None, None
        code = symbol.split(".", 1)[0].upper()
        since = (as_of - timedelta(days=ATTENTION_LOOKBACK_DAYS)).isoformat()
        attention = any(
            getattr(ev, "event_type", None) == "warning"
            and (str(getattr(ev, "symbol", "")).upper() == symbol.upper() or str(getattr(ev, "code", "")).upper() == code)
            and since <= str(getattr(ev, "event_date", "")) <= as_of.isoformat()
            for ev in events
        )
        if risk.get("is_disposition") or risk.get("is_suspended") or risk.get("has_risk_event"):
            return "flagged", str(risk.get("risk_reason") or "") or None, attention
        return "clear", None, attention

    def _daily_rows(self, symbols: list[str], as_of: date) -> dict[str, list[dict[str, Any]]]:
        try:
            store = self.screener.daily_store
            dates = [d for d in store.available_dates() if d <= as_of]
            if not dates:
                return {}
            frame = store.read_range(symbols, dates[-(BREAKOUT_WINDOW + 5)], as_of)
        except Exception as exc:
            logger.debug("beginner daily rows unavailable: %s", type(exc).__name__)
            return {}
        out: dict[str, list[dict[str, Any]]] = {}
        if frame.is_empty():
            return out
        for row in frame.sort("symbol", "date").iter_rows(named=True):
            out.setdefault(row["symbol"], []).append({
                "date": str(row.get("date"))[:10], "close": row.get("close"), "low": row.get("low"),
            })
        return out

    def _technical_metrics(self, symbols: list[str], as_of: date) -> dict[str, dict[str, Any]]:
        """Reuse the canonical PIT factor panel for current beginner evidence."""
        try:
            import polars as pl

            from app.taiwan.adjust import adjust_prices_as_of
            from app.taiwan.providers.taiwan_values import market_close
            from app.taiwan.quant.panel import build_factor_panel

            store = self.screener.daily_store
            dates = [day for day in store.available_dates() if day <= as_of]
            if len(dates) < 2:
                return {}
            start = dates[max(0, len(dates) - 65)]
            benchmark_symbol = "0050.TWSE"
            history = store.read_range([*symbols, benchmark_symbol], start, as_of)
            if history is None or history.is_empty():
                return {}
            events = self.screener.action_store.read_verified_window(start, as_of)
            if events is None:
                return {}

            benchmark_raw = history.filter(pl.col("symbol") == benchmark_symbol)
            market = pl.DataFrame()
            if not benchmark_raw.is_empty():
                adjusted_benchmark = adjust_prices_as_of(
                    benchmark_raw, as_of=as_of, events=events,
                )
                if adjusted_benchmark.status == "verified":
                    market = pl.DataFrame([
                        {
                            "date": row["date"],
                            "close": row["close"],
                            "available_at": market_close(row["date"]),
                        }
                        for row in adjusted_benchmark.to_frame().select("date", "close").iter_rows(named=True)
                    ])

            stock_history = history.filter(pl.col("symbol").is_in(symbols))
            if stock_history.is_empty():
                return {}
            factor_panel = build_factor_panel(
                stock_history,
                events=events,
                policy_version="beginner-technical-v1",
                universe_tier="current_live_verified",
                market=market,
                as_of=as_of,
            )
            factor_rows = {
                row["symbol"]: row for row in factor_panel.values.iter_rows(named=True)
            }
            result: dict[str, dict[str, Any]] = {}
            for symbol in symbols:
                row = factor_rows.get(symbol)
                if row is None:
                    continue
                metrics: dict[str, Any] = {
                    "adjusted_close": None,
                    "ma5": row.get("ma5"),
                    "ma20": row.get("ma20"),
                    "ma60": row.get("ma60"),
                    "atr_14": row.get("atr_14"),
                    "volume_ratio_20d": row.get("relative_volume"),
                    "stock_return_20d": (
                        row["stock_return_20d"] * 100.0
                        if row.get("stock_return_20d") is not None else None
                    ),
                    "benchmark_return_20d": (
                        row["market_return_20d"] * 100.0
                        if row.get("market_return_20d") is not None else None
                    ),
                    "relative_return_20d": (
                        row["relative_to_market_20d"] * 100.0
                        if row.get("relative_to_market_20d") is not None else None
                    ),
                }
                raw_series = stock_history.filter(pl.col("symbol") == symbol).sort("date")
                adjusted = adjust_prices_as_of(raw_series, as_of=as_of, events=events)
                if adjusted.status == "verified":
                    bars = adjusted.to_frame().sort("date").to_dicts()
                    if bars:
                        current = _finite(bars[-1].get("close"))
                        metrics["adjusted_close"] = current
                        metrics["today_volume"] = _finite(bars[-1].get("volume"))
                        if len(bars) >= 2:
                            previous = _finite(bars[-2].get("close"))
                            metrics["price_change_pct"] = (
                                (current / previous - 1.0) * 100.0
                                if current is not None and previous is not None and previous > 0 else None
                            )
                        recent = bars[-20:]
                        lows = [_finite(bar.get("low")) for bar in recent]
                        highs = [_finite(bar.get("high")) for bar in recent]
                        if len(recent) == 20 and all(value is not None for value in [*lows, *highs]):
                            low = min(value for value in lows if value is not None)
                            high = max(value for value in highs if value is not None)
                            metrics["low_20d"] = low
                            metrics["high_20d"] = high
                            metrics["range_position_pct"] = (
                                (current - low) / (high - low) * 100.0
                                if current is not None and high > low else None
                            )
                ratio = _finite(metrics.get("volume_ratio_20d"))
                today_volume = _finite(metrics.get("today_volume"))
                metrics["average_volume_20d"] = (
                    today_volume / ratio if today_volume is not None and ratio is not None and ratio > 0 else None
                )
                result[symbol] = metrics
            return result
        except Exception as exc:
            logger.debug("beginner technical evidence unavailable: %s", type(exc).__name__)
            return {}

    @staticmethod
    def _social(as_of: date) -> dict[str, Any]:
        unavailable: dict[str, Any] = {"status": "unavailable", "dcard_status": None, "mentions": {}}
        try:
            from app.taiwan.social_sentiment import load_social_sentiment

            payload = load_social_sentiment()
        except Exception:
            return unavailable
        if not payload or payload.get("status") not in ("available", "partial"):
            return unavailable
        try:
            snapshot_day = date.fromisoformat(str(payload.get("as_of"))[:10])
        except ValueError:
            return unavailable
        if (as_of - snapshot_day).days > SOCIAL_MAX_AGE_DAYS:
            return unavailable
        mentions = {
            str(row.get("symbol")): int(row.get("total_mentions") or 0)
            for row in payload.get("rankings", []) if isinstance(row, dict) and row.get("symbol")
        }
        dcard = (payload.get("sources") or {}).get("dcard") or {}
        return {"status": payload["status"], "dcard_status": dcard.get("status", "unavailable"), "mentions": mentions}


# ── Frozen AI evidence ───────────────────────────────────────────

BEGINNER_EVIDENCE_REGISTRY_KEYS = frozenset({
    "beginner_selection.selection_state",
    "beginner_selection.signal_strength",
    "beginner_selection.reasons",
    "beginner_selection.risks",
    "beginner_selection.action_summary",
    "beginner_selection.invalidation",
    "beginner_selection.technical_panel.summary",
    "beginner_selection.technical_panel.current_price",
    "beginner_selection.technical_panel.support.support_zone_low",
    "beginner_selection.technical_panel.support.support_zone_high",
    "beginner_selection.technical_panel.resistance.resistance",
    "beginner_selection.technical_panel.invalidation.invalidation",
    "beginner_selection.technical_panel.moving_averages.state",
    "beginner_selection.technical_panel.moving_averages.ma5",
    "beginner_selection.technical_panel.moving_averages.ma20",
    "beginner_selection.technical_panel.moving_averages.ma60",
    "beginner_selection.technical_panel.inner_outer.status",
    "beginner_selection.technical_panel.volume.ratio",
    "beginner_selection.technical_panel.volume.pattern",
    "beginner_selection.technical_panel.institutional.state",
    "beginner_selection.technical_panel.institutional.total_net_5d",
    "beginner_selection.technical_panel.margin.margin_state",
    "beginner_selection.technical_panel.margin.short_state",
    "beginner_selection.technical_panel.relative_strength.stock_return_pct",
    "beginner_selection.technical_panel.relative_strength.benchmark_return_pct",
    "beginner_selection.technical_panel.relative_strength.excess_return_pct",
    "beginner_selection.technical_panel.range_position.position_pct",
    "beginner_selection.technical_panel.volatility.level",
    "beginner_selection.technical_panel.volatility.atr_pct",
    "beginner_selection.technical_panel.market_context.market_state",
    "beginner_selection.technical_panel.market_context.industry_state",
    "beginner_selection.technical_panel.fundamentals.revenue_yoy_pct",
    "beginner_selection.technical_panel.fundamentals.revenue_mom_pct",
    "beginner_selection.technical_panel.fundamentals.eps",
    "beginner_selection.technical_panel.fundamentals.pe",
    "beginner_selection.technical_panel.key_risks",
    "beginner_selection.intraday_context",
    "beginner_selection.fx_context",
})


def selection_evidence(symbol: str, service: BeginnerSelectionService | None = None) -> dict[str, Any] | None:
    """Compact deterministic selection result for AI evidence; AI may not re-rank it."""
    from app.taiwan.symbol import parse_symbol

    try:
        canonical = parse_symbol(symbol).canonical
        candidate = (service or BeginnerSelectionService()).evaluate_symbol(canonical).candidate
    except Exception as exc:
        logger.debug("beginner selection evidence unavailable for %s: %s", symbol, type(exc).__name__)
        return None
    return {
        "version": BEGINNER_SELECTION_VERSION,
        "semantics": "deterministic_screening_priority_not_return_forecast",
        "selection_state": candidate.selection_state,
        "signal_strength": candidate.signal_strength,
        "reasons": [r.model_dump() for r in candidate.reasons],
        "risks": [r.model_dump() for r in candidate.risks],
        "exclusion_reasons": [r.model_dump() for r in candidate.exclusion_reasons],
        "action_summary": candidate.action_summary,
        "invalidation": candidate.invalidation,
        "evidence_status": candidate.evidence_status,
        "data_gaps": candidate.data_gaps,
        "trade_plan": candidate.trade_plan.model_dump() if candidate.trade_plan else None,
        "technical_panel": (
            candidate.technical_panel.model_dump() if candidate.technical_panel else None
        ),
        "intraday_context": (
            candidate.intraday_context.model_dump() if candidate.intraday_context else None
        ),
        "fx_context": candidate.fx_context.model_dump() if candidate.fx_context else None,
    }
