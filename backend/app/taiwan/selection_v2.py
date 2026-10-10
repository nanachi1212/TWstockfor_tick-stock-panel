"""Fixed, forward-only selection strategies built on screener observations.

The functions in this module are deliberately pure.  They only evaluate
columns already assembled by :mod:`app.taiwan.screener`; they do not fetch
data, call AI, or learn weights from historical performance.
"""
from __future__ import annotations

from typing import Any

import polars as pl

STRATEGY_IDS = (
    "trend_liquidity_v1",
    "institutional_momentum_v1",
    "growth_trend_v1",
    "breakout_v1",
    "multi_factor_consensus_v1",
    "pullback_support_v1",
    "foreign_trend_v1",
    "oversold_rebound_v1",
)

V2_STRATEGY_IDS = STRATEGY_IDS[1:]
V2_MIN_AMOUNT_TWD = 50_000_000
INSTITUTIONAL_FLOW_RATIO_MIN = 0.01
BREAKOUT_VOLUME_RATIO_MIN = 1.2
CONSENSUS_MIN_HITS = 2
PULLBACK_MAX_ABOVE_MA20 = 0.03
OVERSOLD_RSI_MAX = 35.0

_METADATA: dict[str, dict[str, Any]] = {
    "trend_liquidity_v1": {
        "name": "趨勢流動性 v1",
        "version": "v1",
        "description": "凍結的 20 日趨勢與成交流動性規則",
    },
    "institutional_momentum_v1": {
        "name": "法人動能 v1",
        "version": "v1",
        "description": "5 日法人淨流量、成交量相對流量與 MA20",
    },
    "growth_trend_v1": {
        "name": "成長趨勢 v1",
        "version": "v1",
        "description": "月營收 YoY、YoY 改善、均線與動能",
    },
    "breakout_v1": {
        "name": "突破轉強 v1",
        "version": "v1",
        "description": "20/60 日突破、量能與動能加速度",
    },
    "multi_factor_consensus_v1": {
        "name": "多策略共識 v1",
        "version": "v1",
        "description": "前三個客觀策略命中至少兩個",
    },
    "pullback_support_v1": {
        "name": "多頭回檔 v1",
        "version": "v1",
        "description": "MA20 在 MA60 之上、近 5 日回檔且收盤貼近 MA20（0～3%）",
    },
    "foreign_trend_v1": {
        "name": "外資跟買 v1",
        "version": "v1",
        "description": "5 日外資淨買超、站上 MA20 且 MA20 在 MA60 之上、20 日動能為正",
    },
    "oversold_rebound_v1": {
        "name": "超跌反彈 v1",
        "version": "v1",
        "description": "RSI14 ≤ 35 且當日上漲、收盤站回 MA5",
    },
}


REVENUE_EVIDENCE_REASONS = {
    "missing": "月營收官方公告 evidence 缺失",
    "not_observed_before_cutoff": "月營收官方公告時間無法證明早於選股 cutoff",
    "stale": "月營收官方 evidence 已過期",
    "incomplete": "月營收官方 evidence 不完整: 缺少應觀測頁面",
}


def _revenue_reasons(
    available: int, evidence_status: str | None, mismatch_count: int
) -> list[str]:
    reasons: list[str] = []
    if evidence_status is not None and evidence_status != "available":
        reasons.append(REVENUE_EVIDENCE_REASONS.get(evidence_status, "月營收官方 evidence 狀態不明"))
    elif available == 0:
        reasons.append("月營收資料不可用")
    if mismatch_count:
        reasons.append(f"月營收官方來源數值不一致 {mismatch_count} 檔")
    return reasons


def is_supported_strategy(strategy_id: str | None) -> bool:
    return strategy_id in STRATEGY_IDS


def strategy_metadata(strategy_id: str) -> dict[str, Any]:
    if strategy_id not in _METADATA:
        raise ValueError(f"不支援的選股策略: {strategy_id}")
    return {"strategy_id": strategy_id, **_METADATA[strategy_id]}


def strategy_readiness(
    frame: pl.DataFrame,
    strategy_id: str,
    *,
    quote_coverage_status: str | None,
    risk_source_status: str | None,
    revenue_evidence_status: str | None = None,
    revenue_mismatch_count: int = 0,
) -> tuple[str, list[str], dict[str, int]]:
    """Return global readiness without turning missing data into candidates."""
    if strategy_id == "trend_liquidity_v1":
        return "ready", [], {}
    reasons: list[str] = []
    coverage: dict[str, int] = {}
    if quote_coverage_status != "verified":
        reasons.append("行情覆蓋未驗證")
    if risk_source_status != "available":
        reasons.append("事件風險來源未完整")
    if strategy_id == "institutional_momentum_v1":
        available = frame.filter(
            pl.col("institutional_status").is_in(["available", "official"])
        ).height
        coverage["institutional_available_count"] = available
        if available == 0:
            reasons.append("法人資料不可用")
    elif strategy_id == "growth_trend_v1":
        available = frame.filter(pl.col("revenue_status") == "available").height
        coverage["revenue_available_count"] = available
        reasons.extend(_revenue_reasons(available, revenue_evidence_status, revenue_mismatch_count))
    elif strategy_id == "breakout_v1":
        available = frame.filter(
            pl.col("breakout_20d_strength").is_not_null()
            | pl.col("breakout_60d_strength").is_not_null()
        ).height
        coverage["technical_history_count"] = available
        if available == 0:
            reasons.append("技術歷史資料不可用")
    elif strategy_id == "multi_factor_consensus_v1":
        institutional_available = frame.filter(
            pl.col("institutional_status").is_in(["available", "official"])
        ).height
        revenue_available = frame.filter(pl.col("revenue_status") == "available").height
        technical_available = frame.filter(
            pl.col("breakout_20d_strength").is_not_null()
            | pl.col("breakout_60d_strength").is_not_null()
        ).height
        coverage["institutional_available_count"] = institutional_available
        coverage["revenue_available_count"] = revenue_available
        coverage["technical_history_count"] = technical_available
        if institutional_available == 0:
            reasons.append("法人資料不可用")
        reasons.extend(
            _revenue_reasons(revenue_available, revenue_evidence_status, revenue_mismatch_count)
        )
        if technical_available == 0:
            reasons.append("技術歷史資料不可用")
        coverage["objective_signal_count"] = frame.filter(
            pl.col("consensus_hit_count") >= CONSENSUS_MIN_HITS
        ).height
    elif strategy_id == "foreign_trend_v1":
        available = frame.filter(
            pl.col("institutional_status").is_in(["available", "official"])
        ).height
        coverage["institutional_available_count"] = available
        if available == 0:
            reasons.append("法人資料不可用")
    elif strategy_id in ("pullback_support_v1", "oversold_rebound_v1"):
        column = "ma60" if strategy_id == "pullback_support_v1" else "rsi_14"
        available = frame.filter(pl.col(column).is_not_null()).height
        coverage["technical_history_count"] = available
        if available == 0:
            reasons.append("技術歷史資料不可用")
    return ("ready" if not reasons else "degraded"), reasons, coverage


def apply_strategy(
    frame: pl.DataFrame, strategy_id: str, *, filter_candidates: bool = True
) -> pl.DataFrame:
    """Add objective hit columns and optionally filter a fixed v1 strategy."""
    if strategy_id not in STRATEGY_IDS:
        raise ValueError(f"不支援的選股策略: {strategy_id}")
    if strategy_id == "trend_liquidity_v1":
        return frame

    available = pl.col("institutional_status").is_in(["available", "official"])
    institutional_hit = (
        available
        & (pl.col("foreign_net_5d") > 0)
        & (pl.col("investment_trust_net_5d") > 0)
        & (pl.col("institutional_flow_ratio_5d") >= INSTITUTIONAL_FLOW_RATIO_MIN)
        & (pl.col("close") > pl.col("ma20"))
        & (pl.col("amount") >= V2_MIN_AMOUNT_TWD)
    ).fill_null(False)

    revenue_available = pl.col("revenue_status") == "available"
    growth_hit = (
        revenue_available
        & (pl.col("revenue_yoy") > 0)
        & (pl.col("revenue_yoy_improving") == True)  # noqa: E712
        & (pl.col("close") > pl.col("ma20"))
        & (pl.col("close") > pl.col("ma60"))
        & (pl.col("momentum_5d") > 0)
        & (pl.col("momentum_20d") > 0)
        & (pl.col("amount") >= V2_MIN_AMOUNT_TWD)
    ).fill_null(False)

    breakout_hit = (
        ((pl.col("breakout_20d_strength") > 0)
         | (pl.col("breakout_60d_strength") > 0))
        & (pl.col("vol_ratio_20d") >= BREAKOUT_VOLUME_RATIO_MIN)
        & (pl.col("momentum_acceleration") > 0)
        & (pl.col("close") > pl.col("ma20"))
        & (pl.col("amount") >= V2_MIN_AMOUNT_TWD)
    ).fill_null(False)

    frame = frame.with_columns([
        institutional_hit.alias("_v2_institutional_hit"),
        growth_hit.alias("_v2_growth_hit"),
        breakout_hit.alias("_v2_breakout_hit"),
    ]).with_columns(
        pl.sum_horizontal(
            pl.col("_v2_institutional_hit").cast(pl.Int8),
            pl.col("_v2_growth_hit").cast(pl.Int8),
            pl.col("_v2_breakout_hit").cast(pl.Int8),
        ).alias("consensus_hit_count")
    ).with_columns(
        pl.concat_str(
            [
                pl.when(pl.col("_v2_institutional_hit")).then(pl.lit("法人動能 v1")).otherwise(None),
                pl.when(pl.col("_v2_growth_hit")).then(pl.lit("成長趨勢 v1")).otherwise(None),
                pl.when(pl.col("_v2_breakout_hit")).then(pl.lit("突破轉強 v1")).otherwise(None),
            ],
            separator="、",
        ).alias("consensus_strategy_names")
    )

    if not filter_candidates:
        return frame

    if strategy_id == "institutional_momentum_v1":
        return frame.filter(pl.col("_v2_institutional_hit"))
    if strategy_id == "growth_trend_v1":
        return frame.filter(pl.col("_v2_growth_hit"))
    if strategy_id == "breakout_v1":
        return frame.filter(pl.col("_v2_breakout_hit"))
    liquid = pl.col("amount") >= V2_MIN_AMOUNT_TWD
    if strategy_id == "pullback_support_v1":
        above_ma20 = pl.col("close") / pl.col("ma20") - 1.0
        return frame.filter((
            liquid
            & (pl.col("ma20") > pl.col("ma60"))
            & (pl.col("close") > pl.col("ma60"))
            & (above_ma20 >= 0) & (above_ma20 <= PULLBACK_MAX_ABOVE_MA20)
            & (pl.col("momentum_5d") < 0)
        ).fill_null(False))
    if strategy_id == "foreign_trend_v1":
        return frame.filter((
            liquid
            & pl.col("institutional_status").is_in(["available", "official"])
            & (pl.col("foreign_net_5d") > 0)
            & (pl.col("close") > pl.col("ma20"))
            & (pl.col("ma20") > pl.col("ma60"))
            & (pl.col("momentum_20d") > 0)
        ).fill_null(False))
    if strategy_id == "oversold_rebound_v1":
        return frame.filter((
            liquid
            & (pl.col("rsi_14") <= OVERSOLD_RSI_MAX)
            & (pl.col("change_pct") > 0)
            & (pl.col("close") > pl.col("ma5"))
        ).fill_null(False))
    return frame.filter(pl.col("consensus_hit_count") >= CONSENSUS_MIN_HITS)


def rank_strategy(frame: pl.DataFrame, strategy_id: str) -> pl.DataFrame:
    """Rank with fixed signal priority and symbol ASC as the final tie-breaker."""
    columns: list[str]
    if strategy_id == "institutional_momentum_v1":
        columns = ["institutional_flow_ratio_5d", "foreign_net_5d", "investment_trust_net_5d", "amount"]
    elif strategy_id == "growth_trend_v1":
        columns = ["revenue_yoy", "revenue_yoy_improvement", "momentum_20d", "momentum_5d", "amount"]
    elif strategy_id == "breakout_v1":
        columns = ["breakout_60d_strength", "breakout_20d_strength", "vol_ratio_20d", "momentum_acceleration", "amount"]
    elif strategy_id == "multi_factor_consensus_v1":
        columns = ["consensus_hit_count", "institutional_flow_ratio_5d", "revenue_yoy", "breakout_60d_strength", "amount"]
    elif strategy_id == "pullback_support_v1":
        columns = ["momentum_20d", "amount"]
    elif strategy_id == "foreign_trend_v1":
        columns = ["foreign_net_5d", "momentum_20d", "amount"]
    elif strategy_id == "oversold_rebound_v1":
        columns = ["change_pct", "vol_ratio_5d", "amount"]
    else:
        return frame
    available = [column for column in columns if column in frame.columns]
    return frame.sort([*available, "symbol"], descending=[True] * len(available) + [False])
