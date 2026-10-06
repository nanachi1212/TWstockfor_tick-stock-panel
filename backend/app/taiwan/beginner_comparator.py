"""Explain existing beginner selection evidence without changing its ranking."""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import re
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, Field

from app.taiwan.beginner_selection import (
    BeginnerCandidate,
    BeginnerSelectionService,
    MarketSummary,
)
from app.taiwan.symbol import parse_symbol

COMPARATOR_VERSION = "beginner-comparator-v1"
GroupKey = Literal["observe", "wait", "avoid", "insufficient"]
_GROUPS: tuple[tuple[GroupKey, str], ...] = (
    ("observe", "優先觀察"), ("wait", "等待"),
    ("avoid", "暫時不要追／略過"), ("insufficient", "資料不足"),
)
_MISSING_REASONS = {
    "quote_unavailable", "quote_stale", "liquidity_unavailable",
    "corporate_action_unverified", "price_integrity", "risk_unverified",
}
_DIMENSION_TEXT = {
    "trend": ("趨勢偏強", "趨勢方向還不明確", "趨勢偏弱"),
    "capital_flow": ("近 5 日法人在買", "近 5 日法人買賣不明顯", "近 5 日法人在賣"),
    "price_position": ("位在回檔觀察區", "價格仍需等待確認", "短線漲多，追價風險高"),
    "fundamental_context": ("營收條件偏正向", "基本面條件中性", "營收或獲利有警訊"),
    "event_risk": ("事件條件偏正向", "目前沒有已知事件警訊", "目前有事件警訊"),
}


class ComparisonGroup(BaseModel):
    key: GroupKey
    label: str
    symbols: list[str] = Field(default_factory=list)


class ComparisonDifference(BaseModel):
    higher_symbol: str
    lower_symbol: str
    reasons: list[str] = Field(min_length=1, max_length=3)


class BeginnerComparisonResponse(BaseModel):
    version: str = COMPARATOR_VERSION
    generated_at: str
    as_of: str | None = None
    market: MarketSummary
    candidates: list[BeginnerCandidate]
    groups: list[ComparisonGroup]
    differences: list[ComparisonDifference] = Field(default_factory=list)
    disclaimer: str = "沿用今日選股原排序；訊號強不代表現在適合買，資料不足不能當作中性。"


def validate_symbols(raw: str) -> list[str]:
    symbols = raw.split(",")
    if not 2 <= len(symbols) <= 5:
        raise ValueError("請選擇 2 至 5 檔股票比較。")
    for symbol in symbols:
        if not re.fullmatch(r"[0-9]{4,6}[A-Z]?\.(?:TWSE|TPEX)", symbol):
            raise ValueError(f"無效的台股代號：{symbol}")
        if parse_symbol(symbol).canonical != symbol:
            raise ValueError(f"請使用標準台股代號：{symbol}")
    if len(set(symbols)) != len(symbols):
        raise ValueError("比較股票不能重複。")
    return symbols


def comparison_group(candidate: BeginnerCandidate) -> GroupKey:
    # Factual exclusions (e.g. flagged regulatory risk) remain skip, even though
    # the existing selection contract also calls those candidates insufficient.
    if any(reason.reason_code in _MISSING_REASONS for reason in candidate.exclusion_reasons):
        return "insufficient"
    if candidate.selection_state == "watch":
        return "observe"
    if candidate.selection_state in ("wait_pullback", "wait_breakout"):
        return "wait"
    return "avoid"


def _differences(higher: BeginnerCandidate, lower: BeginnerCandidate) -> list[str]:
    """Only supported differences; missing evidence never wins a comparison."""
    if comparison_group(higher) == "insufficient" or comparison_group(lower) == "insufficient":
        return []
    same_group = comparison_group(higher) == comparison_group(lower)
    if same_group and (higher.rank is None or lower.rank is None or higher.rank >= lower.rank):
        return []
    reasons = []
    if higher.selection_state == "watch" and lower.selection_state in ("wait_pullback", "wait_breakout", "no_chase"):
        higher_price = next((d for d in higher.dimensions if d.key == "price_position"), None)
        lower_price = next((d for d in lower.dimensions if d.key == "price_position"), None)
        if all(d is not None and d.status != "unavailable" and d.evidence for d in (higher_price, lower_price)):
            waiting = "短線漲多，暫時不要追" if lower.selection_state == "no_chase" else "仍需等回檔或突破確認"
            reasons.append(f"{higher.name or higher.symbol}已到可觀察的價格條件；{lower.name or lower.symbol}{waiting}。")
    lower_dims = {d.key: d for d in lower.dimensions}
    for dim in higher.dimensions:
        other = lower_dims.get(dim.key)
        if dim.key not in _DIMENSION_TEXT or other is None or not dim.evidence or not other.evidence:
            continue
        # Compare available categorical conclusions, never numeric metrics or a
        # replacement score. Unavailable is deliberately absent from this rule.
        better = (
            dim.status == "positive" and other.status in ("neutral", "negative")
        ) or (dim.status == "neutral" and other.status == "negative")
        if not better:
            continue
        if dim.key == "price_position" and reasons:
            continue
        texts = dict(zip(("positive", "neutral", "negative"), _DIMENSION_TEXT[dim.key], strict=True))
        reasons.append(f"{higher.name or higher.symbol}{texts[dim.status]}；{lower.name or lower.symbol}{texts[other.status]}。")
        if len(reasons) == 3:
            break
    return reasons


def build_comparison(
    symbols: list[str], *, service: BeginnerSelectionService | None = None,
) -> BeginnerComparisonResponse:
    service = service or BeginnerSelectionService()
    frozen = service.evaluate_symbols(symbols)
    # Keep pool rank, with unranked scoped symbols last. Safety grouping is a
    # presentation decision and never changes the original rank or strength.
    candidates = sorted(frozen.candidates, key=lambda c: (c.rank is None, c.rank or 0, c.symbol))
    groups = [ComparisonGroup(
        key=key, label=label,
        symbols=[c.symbol for c in candidates if comparison_group(c) == key],
    ) for key, label in _GROUPS]
    by_symbol = {candidate.symbol: candidate for candidate in candidates}
    display_order = [by_symbol[symbol] for group in groups for symbol in group.symbols]
    differences = [ComparisonDifference(
        higher_symbol=higher.symbol, lower_symbol=lower.symbol, reasons=reasons,
    ) for higher, lower in combinations(display_order, 2) if (reasons := _differences(higher, lower))]
    # Freeze every deterministic conclusion before requesting optional context.
    service._request_intraday(symbols)
    fx = service._fx_context()
    return BeginnerComparisonResponse(
        generated_at=frozen.generated_at, as_of=frozen.as_of, market=frozen.market,
        candidates=[service._with_external(candidate, fx=fx) for candidate in candidates],
        groups=groups, differences=differences,
    )
