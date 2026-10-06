"""Deterministic beginner-facing technical evidence.

This module translates existing price, plan, chip, and market facts. It does
not rank candidates and does not create support, resistance, or stop prices.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceStatus = Literal["available", "unavailable", "data_insufficient"]
TrendState = Literal["strong", "neutral", "weak", "unavailable"]


class PriceLevelsEvidence(BaseModel):
    status: EvidenceStatus
    current_price: float | None = None
    support_zone_low: float | None = None
    support_zone_high: float | None = None
    resistance: float | None = None
    invalidation: float | None = None
    support_distance_low_pct: float | None = None
    support_distance_high_pct: float | None = None
    resistance_distance_pct: float | None = None
    explanation: str
    source: str = "trade_plan"
    as_of: str | None = None
    freshness: str = "unavailable"


class MovingAverageEvidence(BaseModel):
    status: EvidenceStatus
    state: TrendState = "unavailable"
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    bullish_alignment: bool | None = None
    explanation: str
    source: str = "pit_adjusted_daily"
    as_of: str | None = None
    freshness: str = "unavailable"


class InnerOuterEvidence(BaseModel):
    status: EvidenceStatus = "data_insufficient"
    outer_pct: float | None = None
    inner_pct: float | None = None
    last_price: float | None = None
    trade_volume: float | None = None
    trade_value: float | None = None
    bids: list[tuple[float, float]] = Field(default_factory=list)
    asks: list[tuple[float, float]] = Field(default_factory=list)
    explanation: str = "目前沒有可靠的即時內外盤資料。"
    source: str = "none"
    as_of: str | None = None
    freshness: str = "unavailable"
    disclaimer: str = "內外盤反映成交主動性，不等於真正買方／賣方人數，不能單獨作為買賣依據。"


VolumePattern = Literal[
    "price_up_volume_up", "price_up_volume_down", "price_down_volume_up",
    "price_down_volume_down", "neutral", "unavailable",
]


class VolumeEvidence(BaseModel):
    status: EvidenceStatus
    today_volume: float | None = None
    average_20d: float | None = None
    ratio: float | None = None
    pattern: VolumePattern = "unavailable"
    explanation: str
    source: str = "taiwan_daily_store"
    as_of: str | None = None
    freshness: str = "unavailable"


ChipState = Literal["buy", "neutral", "sell", "increase_fast", "decrease", "stable", "unavailable"]


class InstitutionalEvidence(BaseModel):
    status: EvidenceStatus
    state: ChipState = "unavailable"
    total_net_5d: float | None = None
    foreign_net_5d: float | None = None
    investment_trust_net_5d: float | None = None
    dealer_net_5d: float | None = None
    complete_sessions: int = 0
    explanation: str
    source: str = "taiwan_institutional_store"
    as_of: str | None = None
    freshness: str = "unavailable"


class MarginEvidence(BaseModel):
    status: EvidenceStatus
    margin_state: ChipState = "unavailable"
    short_state: ChipState = "unavailable"
    margin_balance: float | None = None
    margin_change: float | None = None
    short_balance: float | None = None
    short_change: float | None = None
    explanation: str
    source: str = "taiwan_margin_store"
    as_of: str | None = None
    freshness: str = "unavailable"


class RelativeStrengthEvidence(BaseModel):
    status: EvidenceStatus
    state: Literal["stronger", "similar", "weaker", "unavailable"] = "unavailable"
    period_sessions: int = 20
    stock_return_pct: float | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    benchmark_symbol: str = "0050.TWSE"
    explanation: str
    source: str = "pit_adjusted_daily+0050"
    as_of: str | None = None
    freshness: str = "unavailable"


class RangePositionEvidence(BaseModel):
    status: EvidenceStatus
    low_20d: float | None = None
    high_20d: float | None = None
    position_pct: float | None = None
    explanation: str
    source: str = "pit_adjusted_daily"
    as_of: str | None = None
    freshness: str = "unavailable"


class VolatilityEvidence(BaseModel):
    status: EvidenceStatus
    level: Literal["low", "normal", "high", "unavailable"] = "unavailable"
    atr_14: float | None = None
    atr_pct: float | None = None
    explanation: str
    source: str = "pit_adjusted_daily"
    as_of: str | None = None
    freshness: str = "unavailable"


class TechnicalMarketContext(BaseModel):
    status: EvidenceStatus
    market_state: str = "unavailable"
    industry_state: Literal["strong", "neutral", "unavailable"] = "unavailable"
    industry: str | None = None
    explanation: str
    source: str = "market_intelligence+industry_intelligence"
    as_of: str | None = None
    freshness: str = "unavailable"


class FundamentalsEvidence(BaseModel):
    status: EvidenceStatus
    revenue_yoy_pct: float | None = None
    revenue_mom_pct: float | None = None
    eps: float | None = None
    pe: float | None = None
    warning: str | None = None
    explanation: str
    source: str = "screener.fundamentals"
    as_of: str | None = None
    revenue_as_of: str | None = None
    financials_as_of: str | None = None
    valuation_as_of: str | None = None
    freshness: str = "unavailable"


class TechnicalRisk(BaseModel):
    code: str
    text: str
    source: str


class BeginnerTechnicalPanel(BaseModel):
    summary: str
    current_price: float | None = None
    support: PriceLevelsEvidence
    resistance: PriceLevelsEvidence
    invalidation: PriceLevelsEvidence
    moving_averages: MovingAverageEvidence
    inner_outer: InnerOuterEvidence = Field(default_factory=InnerOuterEvidence)
    volume: VolumeEvidence
    institutional: InstitutionalEvidence
    margin: MarginEvidence
    relative_strength: RelativeStrengthEvidence
    range_position: RangePositionEvidence
    volatility: VolatilityEvidence
    market_context: TechnicalMarketContext
    fundamentals: FundamentalsEvidence
    key_risks: list[TechnicalRisk] = Field(min_length=2, max_length=4)


def fugle_inner_outer_evidence(observation: Any) -> InnerOuterEvidence:
    """Translate an optional Fugle observation without inventing missing flow."""
    source = "fugle_marketdata:websocket:aggregates"
    status = getattr(observation, "status", "disabled")
    snapshot = getattr(observation, "snapshot", None)
    unavailable = {
        "disabled": "目前沒有可靠的即時內外盤資料。",
        "waiting": "Fugle aggregates 尚未收到可靠的即時內外盤資料。",
        "stale": "Fugle aggregates 資料已過期，暫不顯示內外盤比例。",
    }
    if status != "available" or snapshot is None:
        return InnerOuterEvidence(
            explanation=unavailable.get(status, "目前沒有可靠的即時內外盤資料。"),
            source=source,
            as_of=(snapshot.observed_at.isoformat() if snapshot is not None else None),
            freshness="stale" if status == "stale" else "unavailable",
        )
    inner = _finite(snapshot.trade_volume_at_bid)
    outer = _finite(snapshot.trade_volume_at_ask)
    total = (inner or 0.0) + (outer or 0.0)
    if inner is None or outer is None or total <= 0:
        return InnerOuterEvidence(
            explanation="Fugle aggregates 尚無可用的累計內外盤成交量。",
            source=source,
            as_of=snapshot.observed_at.isoformat(),
        )
    inner_pct = round(inner / total * 100.0, 1)
    outer_pct = round(outer / total * 100.0, 1)
    direction = "外盤較強" if outer_pct > inner_pct else "內盤較強" if inner_pct > outer_pct else "內外盤接近"
    return InnerOuterEvidence(
        status="available",
        outer_pct=outer_pct,
        inner_pct=inner_pct,
        last_price=_finite(snapshot.last_price),
        trade_volume=_finite(snapshot.trade_volume),
        trade_value=_finite(snapshot.trade_value),
        bids=list(snapshot.bids),
        asks=list(snapshot.asks),
        explanation=f"內盤 {inner_pct:.1f}%、外盤 {outer_pct:.1f}%，{direction}。",
        source=source,
        as_of=snapshot.observed_at.isoformat(),
        freshness="session_close" if snapshot.is_close else "realtime",
    )


def with_inner_outer_evidence(
    panel: BeginnerTechnicalPanel, evidence: InnerOuterEvidence,
) -> BeginnerTechnicalPanel:
    """Overlay live flow evidence without changing any selection calculation."""
    risks = [risk for risk in panel.key_risks if risk.code != "inner_outer_unavailable"]
    if evidence.status != "available" and not any(r.code == "inner_outer_unavailable" for r in risks):
        risks.append(TechnicalRisk(
            code="inner_outer_unavailable",
            text=evidence.explanation,
            source=evidence.source,
        ))
    while len(risks) < 2:
        risks.append(TechnicalRisk(
            code=f"data_review_{len(risks) + 1}",
            text="部分資料仍需配合價格與市場環境一起判斷。",
            source="beginner_technical",
        ))
    return panel.model_copy(update={"inner_outer": evidence, "key_risks": risks[:4]})


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _pct(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0:
        return None
    return round((value / base - 1.0) * 100.0, 2)


def _daily_freshness(inputs: dict[str, Any]) -> str:
    return "stale" if inputs.get("quote_freshness") == "stale" else "current"


def _price_evidence(
    current: float | None, plan: Any, as_of: str | None, freshness: str,
) -> tuple[PriceLevelsEvidence, PriceLevelsEvidence, PriceLevelsEvidence]:
    if current is None or plan is None:
        unavailable = PriceLevelsEvidence(
            status="data_insufficient", current_price=current,
            explanation="目前資料不足，無法可靠計算支撐／壓力。",
            as_of=as_of,
        )
        return unavailable, unavailable.model_copy(deep=True), unavailable.model_copy(deep=True)
    low = _finite(getattr(plan, "entry_zone_low", None))
    high = _finite(getattr(plan, "entry_zone_high", None))
    trigger = _finite(getattr(plan, "breakout_trigger", None))
    reference = _finite(getattr(plan, "reference_high", None))
    resistance = trigger if trigger is not None and trigger > current else (
        reference if reference is not None and reference > current else None
    )
    stop = _finite(getattr(plan, "stop_price", None))
    def evidence(status: EvidenceStatus, explanation: str) -> PriceLevelsEvidence:
        return PriceLevelsEvidence(
            status=status,
            current_price=current,
            support_zone_low=low,
            support_zone_high=high,
            resistance=resistance,
            invalidation=stop,
            support_distance_low_pct=_pct(low, current),
            support_distance_high_pct=_pct(high, current),
            resistance_distance_pct=_pct(resistance, current),
            explanation=explanation,
            source="trade_plan",
            as_of=getattr(plan, "evidence_as_of", as_of),
            freshness=freshness,
        )

    support = evidence(
        "available" if low is not None and high is not None else "data_insufficient",
        "支撐區沿用正式 TradePlan。" if low is not None and high is not None
        else "正式 TradePlan 沒有可用的回檔支撐區。",
    )
    resistance_evidence = evidence(
        "available" if resistance is not None else "data_insufficient",
        "壓力位沿用正式 TradePlan。" if resistance is not None
        else "正式 TradePlan 沒有高於現價的可靠壓力位。",
    )
    invalidation_evidence = evidence(
        "available" if stop is not None else "data_insufficient",
        "失效位沿用正式 TradePlan。" if stop is not None
        else "正式 TradePlan 沒有可用的失效位置。",
    )
    return support, resistance_evidence, invalidation_evidence


def _moving_averages(inputs: dict[str, Any], as_of: str | None) -> MovingAverageEvidence:
    current = _finite(inputs.get("adjusted_close"))
    ma5, ma20, ma60 = (_finite(inputs.get(name)) for name in ("ma5", "ma20", "ma60"))
    if None in (current, ma5, ma20, ma60):
        return MovingAverageEvidence(
            status="data_insufficient", explanation="均線資料不足，無法判斷趨勢。", as_of=as_of,
        )
    assert current is not None and ma5 is not None and ma20 is not None and ma60 is not None
    alignment = ma5 > ma20 > ma60
    if current > ma5 and current > ma20 and current > ma60:
        state: TrendState = "strong"
        text = "現價站在 5 日、20 日、60 日均線之上，中短期趨勢目前偏強。"
    elif current < ma5 and current < ma20 and current < ma60:
        state = "weak"
        text = "現價位於 5 日、20 日、60 日均線之下，趨勢目前偏弱。"
    else:
        state = "neutral"
        text = "現價與 5 日、20 日、60 日均線交錯，趨勢目前中性。"
    if alignment:
        text += "短、中期均線依序向上（多頭排列）。"
    return MovingAverageEvidence(
        status="available", state=state, ma5=round(ma5, 4), ma20=round(ma20, 4),
        ma60=round(ma60, 4), bullish_alignment=alignment, explanation=text,
        as_of=as_of, freshness=_daily_freshness(inputs),
    )


def _volume(inputs: dict[str, Any], as_of: str | None) -> VolumeEvidence:
    today = _finite(inputs.get("today_volume"))
    ratio = _finite(inputs.get("volume_ratio_20d"))
    average = _finite(inputs.get("average_volume_20d"))
    change = _finite(inputs.get("price_change_pct"))
    if None in (today, ratio, average, change):
        return VolumeEvidence(
            status="data_insufficient", explanation="成交量或 20 日均量資料不足。", as_of=as_of,
        )
    assert ratio is not None and change is not None
    expanded = ratio >= 1.0
    if change > 0:
        pattern: VolumePattern = "price_up_volume_up" if expanded else "price_up_volume_down"
        text = ("股價上漲且成交量同步放大，今天的上漲有較多成交參與。" if expanded
                else "股價雖上漲，但量能沒有跟上，追價力道較弱。")
    elif change < 0:
        pattern = "price_down_volume_up" if expanded else "price_down_volume_down"
        text = ("下跌同時成交量放大，今天賣壓較明顯。" if expanded
                else "股價回落但量能縮小，賣壓沒有明顯擴大。")
    else:
        pattern, text = "neutral", "股價變化不大，量價關係目前中性。"
    return VolumeEvidence(
        status="available", today_volume=today, average_20d=average, ratio=round(ratio, 2),
        pattern=pattern, explanation=text, as_of=as_of, freshness=_daily_freshness(inputs),
    )


def _institutional(inputs: dict[str, Any]) -> InstitutionalEvidence:
    values = [_finite(inputs.get(name)) for name in (
        "foreign_net_5d", "investment_trust_net_5d", "dealer_net_5d",
    )]
    complete = int(inputs.get("institutional_complete_sessions") or 0)
    as_of = inputs.get("institutional_as_of")
    source_status = str(inputs.get("institutional_status") or "unavailable")
    if source_status not in {"available", "official", "current"} or complete != 5 or any(value is None for value in values):
        return InstitutionalEvidence(
            status="data_insufficient", complete_sessions=complete,
            explanation="法人資料未涵蓋完整 5 個交易日。", as_of=as_of,
        )
    foreign, trust, dealer = values
    assert foreign is not None and trust is not None and dealer is not None
    total = foreign + trust + dealer
    state: ChipState = "buy" if total > 0 else "sell" if total < 0 else "neutral"
    leader_name, leader = max(
        (("外資", foreign), ("投信", trust), ("自營商", dealer)), key=lambda item: abs(item[1]),
    )
    if state == "neutral":
        text = "近 5 個交易日法人買賣大致中性。"
    else:
        direction = "買超" if total > 0 else "賣超"
        lead = f"，其中以{leader_name}{'買超' if leader > 0 else '賣超'}為主" if leader else ""
        text = f"近 5 個交易日法人整體偏{direction[0]}{lead}。"
    return InstitutionalEvidence(
        status="available", state=state, total_net_5d=total,
        foreign_net_5d=foreign, investment_trust_net_5d=trust, dealer_net_5d=dealer,
        complete_sessions=5, explanation=text, as_of=as_of, freshness="latest_official",
    )


def _balance_state(balance: float | None, change: float | None) -> ChipState:
    if balance is None or change is None:
        return "unavailable"
    if balance == 0 and change == 0:
        return "stable"
    prior = balance - change
    if prior <= 0:
        return "unavailable"
    pct = change / prior * 100.0
    return "increase_fast" if pct >= 2.0 else "decrease" if pct <= -2.0 else "stable"


def _margin(inputs: dict[str, Any]) -> MarginEvidence:
    balance = _finite(inputs.get("margin_balance"))
    change = _finite(inputs.get("margin_change"))
    short = _finite(inputs.get("short_balance"))
    short_change = _finite(inputs.get("short_change"))
    as_of = inputs.get("margin_as_of")
    source_status = str(inputs.get("margin_status") or "unavailable")
    margin_state, short_state = _balance_state(balance, change), _balance_state(short, short_change)
    if (source_status not in {"available", "official", "current"} or not as_of
            or margin_state == "unavailable" or short_state == "unavailable"):
        return MarginEvidence(
            status="data_insufficient", margin_balance=balance, margin_change=change,
            short_balance=short, short_change=short_change,
            explanation="融資融券資料不足，無法判斷近期變化。", as_of=as_of,
        )
    labels = {"increase_fast": "快速增加", "decrease": "減少", "stable": "變化不大"}
    text = f"融資{labels[margin_state]}，融券{labels[short_state]}。"
    if margin_state == "increase_fast":
        text += "近期融資增加較快，短線籌碼可能較擁擠。"
    return MarginEvidence(
        status="available", margin_state=margin_state, short_state=short_state,
        margin_balance=balance, margin_change=change, short_balance=short,
        short_change=short_change, explanation=text, as_of=as_of,
        freshness="latest_official",
    )


def _relative(inputs: dict[str, Any], as_of: str | None) -> RelativeStrengthEvidence:
    stock = _finite(inputs.get("stock_return_20d"))
    benchmark = _finite(inputs.get("benchmark_return_20d"))
    excess = _finite(inputs.get("relative_return_20d"))
    if None in (stock, benchmark, excess):
        return RelativeStrengthEvidence(
            status="data_insufficient", explanation="0050 同期資料不足，無法比較相對強弱。", as_of=as_of,
        )
    assert excess is not None and stock is not None and benchmark is not None
    if excess >= 3.0:
        state: Literal["stronger", "similar", "weaker"] = "stronger"
        text = "這檔最近表現明顯強於大盤。"
    elif excess <= -3.0:
        state, text = "weaker", "這檔最近表現明顯弱於大盤。"
    else:
        state, text = "similar", "這檔最近表現與大盤接近。"
    return RelativeStrengthEvidence(
        status="available", state=state, stock_return_pct=stock,
        benchmark_return_pct=benchmark, excess_return_pct=excess,
        explanation=text, as_of=as_of, freshness=_daily_freshness(inputs),
    )


def _range_position(inputs: dict[str, Any], as_of: str | None) -> RangePositionEvidence:
    low, high, position = (_finite(inputs.get(name)) for name in ("low_20d", "high_20d", "range_position_pct"))
    if None in (low, high, position):
        return RangePositionEvidence(
            status="data_insufficient", explanation="近 20 日價格區間資料不足。", as_of=as_of,
        )
    assert position is not None
    text = f"現價位於近 20 日區間約 {position:.0f}% 的位置。"
    if position >= 80:
        text += "已接近近期高位，現在追價的安全空間較小。"
    return RangePositionEvidence(
        status="available", low_20d=low, high_20d=high, position_pct=position,
        explanation=text, as_of=as_of, freshness=_daily_freshness(inputs),
    )


def _volatility(inputs: dict[str, Any], current: float | None, as_of: str | None) -> VolatilityEvidence:
    atr = _finite(inputs.get("atr_14"))
    atr_pct = _pct(current + atr, current) if current is not None and atr is not None else None
    if atr is None or atr_pct is None:
        return VolatilityEvidence(
            status="data_insufficient", explanation="波動資料不足。", as_of=as_of,
        )
    level: Literal["low", "normal", "high"] = (
        "low" if atr_pct < 2.0 else "normal" if atr_pct <= 4.0 else "high"
    )
    text = {
        "low": "近期每天上下震盪幅度較小。",
        "normal": "近期每天上下震盪幅度一般。",
        "high": "近期每天上下震盪幅度較大，進場後價格可能快速波動。",
    }[level]
    return VolatilityEvidence(
        status="available", level=level, atr_14=atr, atr_pct=atr_pct,
        explanation=text, as_of=as_of, freshness=_daily_freshness(inputs),
    )


def _market(inputs: dict[str, Any], market: Any, industry: str | None) -> TechnicalMarketContext:
    state = str(getattr(market, "state", "unavailable"))
    as_of = getattr(market, "as_of", None)
    if state == "unavailable":
        return TechnicalMarketContext(
            status="data_insufficient", market_state=state, industry=industry,
            explanation="大盤或類股資料不足。", as_of=as_of,
        )
    strongest = set(getattr(market, "strongest_industries", []) or [])
    industry_state: Literal["strong", "neutral", "unavailable"] = (
        "strong" if industry and industry in strongest else "neutral" if industry else "unavailable"
    )
    market_text = {"favorable": "大盤環境偏正向", "neutral": "大盤環境中性", "cautious": "大盤環境偏保守"}.get(state, "大盤資料不足")
    industry_text = "所屬類股相對強" if industry_state == "strong" else "所屬類股未列入今日強勢類股" if industry else "所屬類股資料不足"
    return TechnicalMarketContext(
        status="available", market_state=state, industry_state=industry_state,
        industry=industry, explanation=f"{market_text}，{industry_text}。",
        as_of=as_of, freshness="current",
    )


def _fundamentals(inputs: dict[str, Any]) -> FundamentalsEvidence:
    revenue_available = inputs.get("revenue_status") == "available"
    yoy = _finite(inputs.get("revenue_yoy")) if revenue_available else None
    mom = _finite(inputs.get("revenue_mom")) if revenue_available else None
    eps = _finite(inputs.get("eps"))
    pe = _finite(inputs.get("pe"))
    revenue_as_of = str(inputs["revenue_as_of"]) if inputs.get("revenue_as_of") else None
    financials_as_of = str(inputs["financials_as_of"]) if inputs.get("financials_as_of") else None
    valuation_as_of = str(inputs["valuation_as_of"]) if inputs.get("valuation_as_of") else None
    dated = [value for value in (revenue_as_of, financials_as_of, valuation_as_of) if value]
    evidence_as_of = max(dated, default=None)
    if yoy is None and mom is None and eps is None and pe is None:
        return FundamentalsEvidence(
            status="data_insufficient", explanation="月營收、EPS 與本益比資料不足。",
            as_of=evidence_as_of, revenue_as_of=revenue_as_of,
            financials_as_of=financials_as_of, valuation_as_of=valuation_as_of,
        )
    warning = "年增幅很大，可能也受到去年同期基期影響，不能只看單一百分比。" if yoy is not None and yoy > 200 else None
    parts = []
    if yoy is not None:
        parts.append(f"月營收年增 {yoy:+.1f}%")
    if mom is not None:
        parts.append(f"月增 {mom:+.1f}%")
    if eps is not None:
        parts.append(f"EPS {eps:g}")
    if pe is not None:
        parts.append(f"本益比 {pe:g}")
    return FundamentalsEvidence(
        status="available", revenue_yoy_pct=yoy, revenue_mom_pct=mom, eps=eps, pe=pe,
        warning=warning, explanation="、".join(parts) + "。",
        as_of=evidence_as_of, revenue_as_of=revenue_as_of,
        financials_as_of=financials_as_of, valuation_as_of=valuation_as_of,
        freshness="latest_available" if dated else "source_date_unavailable",
    )


def _risks(
    *, levels: PriceLevelsEvidence, averages: MovingAverageEvidence, volume: VolumeEvidence,
    institutional: InstitutionalEvidence, margin: MarginEvidence, relative: RelativeStrengthEvidence,
    position: RangePositionEvidence, volatility: VolatilityEvidence,
    market: TechnicalMarketContext, inner_outer: InnerOuterEvidence, regulatory_risk: str | None,
) -> list[TechnicalRisk]:
    candidates: list[tuple[int, TechnicalRisk]] = []

    def add(priority: int, code: str, text: str, source: str) -> None:
        candidates.append((priority, TechnicalRisk(code=code, text=text, source=source)))

    if regulatory_risk:
        add(100, "regulatory_risk", regulatory_risk, "events.regulatory")
    distance = levels.resistance_distance_pct
    if distance is not None and 0 <= distance <= 3:
        add(90, "near_resistance", "現價已接近上方壓力，追價空間有限。", levels.source)
    support_high = levels.support_distance_high_pct
    if support_high is not None and support_high <= -6:
        add(85, "far_from_support", "現價離支撐區較遠，回檔空間可能較大。", levels.source)
    if market.market_state == "cautious":
        add(80, "market_cautious", "今天大盤偏弱，不適合積極追價。", market.source)
    if volume.pattern == "price_down_volume_up":
        add(75, "price_down_volume_up", volume.explanation, volume.source)
    if institutional.state == "sell":
        add(70, "institutional_sell", "近 5 個交易日法人整體偏賣。", institutional.source)
    if margin.margin_state == "increase_fast":
        add(65, "margin_crowded", "融資增加較快，短線籌碼可能較擁擠。", margin.source)
    if volatility.level == "high":
        add(60, "high_volatility", volatility.explanation, volatility.source)
    if relative.state == "weaker":
        add(55, "relative_weak", "近 20 日表現弱於 0050。", relative.source)
    if averages.state == "weak":
        add(50, "weak_trend", "現價位於主要均線之下，趨勢偏弱。", averages.source)
    if position.position_pct is not None and position.position_pct >= 80:
        add(45, "near_range_high", "現價接近近 20 日高位，追價安全空間較小。", position.source)
    unavailable = [
        (levels.status, "價位"), (averages.status, "均線"), (volume.status, "量價"),
        (institutional.status, "法人"), (margin.status, "融資融券"),
        (relative.status, "相對強弱"), (volatility.status, "波動"),
    ]
    for status, label in unavailable:
        if status != "available":
            add(20, f"{label}_unavailable", f"{label}資料不足，判斷需保守。", "data_availability")
    if inner_outer.status != "available":
        add(10, "inner_outer_unavailable", inner_outer.explanation, inner_outer.source)
    if len(candidates) < 2 and volume.status == "available" and volume.ratio is not None and volume.ratio < 1:
        add(5, "volume_not_expanded", "量能低於 20 日平均，追價力道有限。", volume.source)
    if len(candidates) < 2 and market.industry_state != "strong":
        add(4, "industry_not_strong", "所屬類股未列入今日強勢類股。", market.source)
    candidates.sort(key=lambda item: (-item[0], item[1].code))
    unique: list[TechnicalRisk] = []
    seen: set[str] = set()
    for _priority, risk in candidates:
        if risk.code not in seen:
            unique.append(risk)
            seen.add(risk.code)
        if len(unique) == 4:
            break
    while len(unique) < 2:
        unique.append(TechnicalRisk(
            code=f"data_review_{len(unique) + 1}",
            text="部分資料仍需配合價格與市場環境一起判斷。",
            source="beginner_technical",
        ))
    return unique


def build_beginner_technical_panel(
    *, current_price: float | None, as_of: str | None, plan: Any,
    metrics: dict[str, Any], market: Any, industry: str | None,
    action_summary: str, regulatory_risk: str | None = None,
) -> BeginnerTechnicalPanel:
    """Build display evidence without changing the selection decision."""
    current = _finite(current_price)
    support, resistance, invalidation = _price_evidence(
        current, plan, as_of, _daily_freshness(metrics),
    )
    averages = _moving_averages(metrics, as_of)
    inner_outer = InnerOuterEvidence()
    volume = _volume(metrics, as_of)
    institutional = _institutional(metrics)
    margin = _margin(metrics)
    relative = _relative(metrics, as_of)
    position = _range_position(metrics, as_of)
    volatility = _volatility(metrics, current, as_of)
    market_context = _market(metrics, market, industry)
    fundamentals = _fundamentals(metrics)
    summary_parts = [{"strong": "趨勢偏強", "neutral": "趨勢中性", "weak": "趨勢偏弱"}.get(averages.state, "趨勢資料不足")]
    if resistance.resistance_distance_pct is not None and 0 <= resistance.resistance_distance_pct <= 3:
        summary_parts.append("已接近上方壓力")
    if market_context.market_state == "cautious":
        summary_parts.append("今天大盤偏弱")
    summary = "，".join(summary_parts) + f"。{action_summary}"
    risks = _risks(
        levels=support, averages=averages, volume=volume, institutional=institutional,
        margin=margin, relative=relative, position=position, volatility=volatility,
        market=market_context, inner_outer=inner_outer, regulatory_risk=regulatory_risk,
    )
    return BeginnerTechnicalPanel(
        summary=summary, current_price=current,
        support=support, resistance=resistance, invalidation=invalidation,
        moving_averages=averages,
        inner_outer=inner_outer, volume=volume, institutional=institutional,
        margin=margin, relative_strength=relative, range_position=position,
        volatility=volatility, market_context=market_context,
        fundamentals=fundamentals, key_risks=risks,
    )
