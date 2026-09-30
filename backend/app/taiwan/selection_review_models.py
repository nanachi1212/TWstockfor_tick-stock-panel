"""Models for Taiwan Selection Review and Snapshot tracking (A12).

Strict Guarantees:
- Selection snapshots are immutable once saved.
- Preserves point-in-time facts as observed at screen time (no lookback mutation).
- Tracks 1D, 5D, 20D horizons across actual trading days (not calendar days).
- Graceful degradation: pending when horizon not yet elapsed, unavailable if price missing.
- No fabricated zeros or forward mutation.
"""
# ruff: noqa: RUF001
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HorizonStatus = Literal["completed", "pending", "unavailable"]


class SelectionSnapshotItem(BaseModel):
    """Point-in-time snapshot for a single screened stock."""

    symbol: str = Field(..., description="標準標的代碼，例如 2330.TWSE")
    name: str = Field(..., description="股票名稱，例如 台積電")
    rank: int = Field(..., ge=1, description="選股排序名次")
    quant_score: float | None = Field(None, description="當時 Quant 綜合評分")
    match_reasons: list[str] = Field(default_factory=list, description="入選原因標籤")
    strategy_conditions: dict[str, Any] = Field(default_factory=dict, description="當時策略條件快照")
    price: float = Field(..., description="當時參考收盤價 (entry/reference close)")
    fundamental_summary: str | None = Field(None, description="基本面摘要文字")
    chips_summary: str | None = Field(None, description="籌碼摘要文字")
    event_risk_summary: str | None = Field(None, description="事件風險摘要文字")
    risk_status: Literal["clear", "unknown"] | None = None
    quote_status: Literal["available", "missing"] | None = None


class SelectionSnapshot(BaseModel):
    """Immutable snapshot of screener run."""

    snapshot_id: str = Field(..., description="唯一識別碼，格式如 snap_YYYYMMDD_HHMMSS_xxxx")
    created_at: str = Field(..., description="建立時間戳記 ISO 8601")
    strategy_id: str = Field(..., description="策略代號")
    strategy_version: str | None = Field(None, description="固定策略版本")
    strategy_name: str = Field(..., description="策略名稱")
    as_of_date: str = Field(..., description="選股資料基準日 (YYYY-MM-DD 交易日)")
    market_context_summary: str = Field(..., description="當時大盤環境摘要")
    selected_symbols: list[str] = Field(default_factory=list, description="入選代碼清單")
    items: list[SelectionSnapshotItem] = Field(default_factory=list, description="每檔入選標的明細快照")
    record_type: Literal["research", "forward_batch"] = "research"
    source: Literal["Screener", "Buy Point"] = "Screener"
    locked_at: str | None = None
    source_data_date: str | None = None
    target_trade_date: str | None = None
    target_trade_date_status: Literal["confirmed", "scheduled_unverified"] | None = None
    rule_version: str | None = None
    evaluation_basis: Literal["reference_close", "next_open"] = "reference_close"
    price_adjustment: str = "raw_reference_close"
    cost_assumption: str = "未扣成本與滑價；紙上開盤價不保證成交"
    observation_origin: Literal["a13_server_observed"] | None = None
    strategy_definition_digest: str | None = None
    eligible_total: int | None = None
    primary_observation_count: int | None = None
    missing_quote_count: int | None = None
    quote_coverage_status: Literal["verified", "unavailable"] | None = None
    risk_unknown_count: int | None = None
    risk_source_status: Literal["available", "partial", "unavailable"] | None = None
    risk_source_as_of: str | None = None
    risk_target_date: str | None = None
    selection_indicator_basis: Literal["raw", "pit_adjusted"] = "raw"
    trend_adjustment_status: Literal["verified", "partial", "unavailable"] | None = None
    selection_action_coverage_start: str | None = None
    selection_action_coverage_end: str | None = None
    selection_action_events_sha256: str | None = None
    selection_action_coverage_saved_at: str | None = None
    # Official monthly-revenue observations used by revenue-based strategies.
    revenue_evidence_status: str | None = None
    revenue_evidence_cutoff: str | None = None
    revenue_evidence_digest: str | None = None


class SaveSelectionSnapshotRequest(BaseModel):
    """Request payload to save current screener results as an immutable snapshot."""

    strategy_id: str
    strategy_name: str
    as_of_date: str
    market_context_summary: str = ""
    items: list[SelectionSnapshotItem]
    source: Literal["Screener", "Buy Point"] = "Screener"


class HorizonReviewItem(BaseModel):
    """Evaluation result for a single stock across 1D, 5D, 20D horizons."""

    symbol: str
    name: str
    rank: int
    entry_price: float
    paper_entry_price: float | None = None
    quant_score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)
    fundamental_summary: str | None = None
    chips_summary: str | None = None
    event_risk_summary: str | None = None

    # 1D horizon (optional / bonus)
    h1d_reference_close_return_pct: float | None = None
    h1d_reference_close_status: HorizonStatus = "pending"
    h1d_price: float | None = None
    h1d_return_pct: float | None = None
    h1d_raw_return_pct: float | None = None
    h1d_status: HorizonStatus = "pending"
    h1d_outcome_date: str | None = None
    h1d_bm_return_pct: float | None = None
    h1d_raw_bm_return_pct: float | None = None
    h1d_bm_status: HorizonStatus = "pending"
    h1d_excess_pct: float | None = None
    h1d_raw_excess_pct: float | None = None

    # 5D horizon
    h5d_reference_close_return_pct: float | None = None
    h5d_reference_close_status: HorizonStatus = "pending"
    h5d_price: float | None = None
    h5d_return_pct: float | None = None
    h5d_raw_return_pct: float | None = None
    h5d_status: HorizonStatus = "pending"
    h5d_outcome_date: str | None = None
    h5d_bm_return_pct: float | None = None
    h5d_raw_bm_return_pct: float | None = None
    h5d_bm_status: HorizonStatus = "pending"
    h5d_excess_pct: float | None = None
    h5d_raw_excess_pct: float | None = None

    # 20D horizon
    h20d_reference_close_return_pct: float | None = None
    h20d_reference_close_status: HorizonStatus = "pending"
    h20d_price: float | None = None
    h20d_return_pct: float | None = None
    h20d_raw_return_pct: float | None = None
    h20d_status: HorizonStatus = "pending"
    h20d_outcome_date: str | None = None
    h20d_bm_return_pct: float | None = None
    h20d_raw_bm_return_pct: float | None = None
    h20d_bm_status: HorizonStatus = "pending"
    h20d_excess_pct: float | None = None
    h20d_raw_excess_pct: float | None = None

    benchmark_symbol: str = "0050.TWSE"
    benchmark_name: str = "台灣50"
    entry_date: str | None = None
    entry_status: HorizonStatus = "pending"
    price_adjustment: str = "raw_reference_close"
    status_reasons: dict[str, str] = Field(default_factory=dict)


class ForwardCohortStats(BaseModel):
    """Aggregated descriptive metrics for one immutable rank cohort."""

    batch_count: int = 0
    matured_batch_count: int = 0
    pick_count: int = 0
    evaluable_count: int = 0
    pending_count: int = 0
    unavailable_count: int = 0
    positive_return_count: int = 0
    hit_rate: float | None = None
    average_return_pct: float | None = None
    median_return_pct: float | None = None
    benchmark_evaluable_count: int = 0
    average_benchmark_return_pct: float | None = None
    excess_evaluable_count: int = 0
    average_excess_return_pct: float | None = None
    median_excess_return_pct: float | None = None
    beat_benchmark_count: int = 0
    beat_benchmark_rate: float | None = None


class ForwardTimelineMetric(BaseModel):
    """Per-batch horizon metric used by the Selection Review timeline."""

    matured: bool = False
    evaluable_count: int = 0
    pending_count: int = 0
    unavailable_count: int = 0
    hit_rate: float | None = None
    average_return_pct: float | None = None
    average_excess_return_pct: float | None = None


class ForwardBatchTimeline(BaseModel):
    """One formal batch in the cumulative forward performance timeline."""

    snapshot_id: str
    source_date: str
    target_entry_date: str | None = None
    candidate_count: int = 0
    top10: dict[str, ForwardTimelineMetric] = Field(default_factory=dict)
    full_batch: dict[str, ForwardTimelineMetric] = Field(default_factory=dict)


class SnapshotReviewDetail(BaseModel):
    """Detailed review response for a specific snapshot."""

    snapshot: SelectionSnapshot
    evaluated_items: list[HorizonReviewItem] = Field(default_factory=list)

    h1d_evaluated_count: int = 0
    h1d_avg_return_pct: float | None = None
    h1d_bm_avg_return_pct: float | None = None
    h1d_avg_excess_pct: float | None = None
    h1d_bm_evaluated_count: int = 0
    h1d_excess_evaluated_count: int = 0
    h1d_reference_close_evaluated_count: int = 0
    h1d_reference_close_avg_return_pct: float | None = None

    h5d_evaluated_count: int = 0
    h5d_reference_close_evaluated_count: int = 0
    h5d_reference_close_avg_return_pct: float | None = None
    h5d_bm_evaluated_count: int = 0
    h5d_excess_evaluated_count: int = 0
    h20d_evaluated_count: int = 0
    h20d_reference_close_evaluated_count: int = 0
    h20d_reference_close_avg_return_pct: float | None = None
    h20d_bm_evaluated_count: int = 0
    h20d_excess_evaluated_count: int = 0

    h5d_avg_return_pct: float | None = None
    h20d_avg_return_pct: float | None = None

    h5d_bm_avg_return_pct: float | None = None
    h20d_bm_avg_return_pct: float | None = None

    h5d_avg_excess_pct: float | None = None
    h20d_avg_excess_pct: float | None = None
    h1d_pending_count: int = 0
    h1d_unavailable_count: int = 0
    h5d_pending_count: int = 0
    h5d_unavailable_count: int = 0
    h20d_pending_count: int = 0
    h20d_unavailable_count: int = 0
    cohorts: dict[str, dict[str, ForwardCohortStats]] = Field(default_factory=dict)


class ForwardBatchStats(BaseModel):
    strategy_id: str | None = None
    strategy_name: str | None = None
    batches_count: int = 0
    picks_count: int = 0
    h1d_evaluated_count: int = 0
    h1d_pending_count: int = 0
    h1d_unavailable_count: int = 0
    h1d_hit_rate_pct: float | None = None
    h1d_avg_return_pct: float | None = None
    h1d_bm_evaluated_count: int = 0
    h1d_bm_avg_return_pct: float | None = None
    h1d_excess_evaluated_count: int = 0
    h1d_avg_excess_pct: float | None = None
    h1d_reference_close_evaluated_count: int = 0
    h1d_reference_close_avg_return_pct: float | None = None
    h5d_evaluated_count: int = 0
    h5d_pending_count: int = 0
    h5d_unavailable_count: int = 0
    h5d_hit_rate_pct: float | None = None
    h5d_avg_return_pct: float | None = None
    h5d_bm_evaluated_count: int = 0
    h5d_bm_avg_return_pct: float | None = None
    h5d_excess_evaluated_count: int = 0
    h5d_avg_excess_pct: float | None = None
    h5d_reference_close_evaluated_count: int = 0
    h5d_reference_close_avg_return_pct: float | None = None
    h20d_evaluated_count: int = 0
    h20d_pending_count: int = 0
    h20d_unavailable_count: int = 0
    h20d_hit_rate_pct: float | None = None
    h20d_avg_return_pct: float | None = None
    h20d_bm_evaluated_count: int = 0
    h20d_bm_avg_return_pct: float | None = None
    h20d_excess_evaluated_count: int = 0
    h20d_avg_excess_pct: float | None = None
    h20d_reference_close_evaluated_count: int = 0
    h20d_reference_close_avg_return_pct: float | None = None
    hit_rate_definition: str = "未四捨五入報酬率 > 0%"
    horizons: dict[str, dict[str, ForwardCohortStats]] = Field(default_factory=dict)
    timeline: list[ForwardBatchTimeline] = Field(default_factory=list)


class SnapshotListItem(BaseModel):
    """Brief summary item in the snapshot list view."""

    snapshot_id: str
    created_at: str
    strategy_id: str
    strategy_version: str | None = None
    strategy_name: str
    source: Literal["Screener", "Buy Point"] = "Screener"
    as_of_date: str
    selected_count: int
    record_type: Literal["research", "forward_batch"] = "research"
    observation_origin: Literal["a13_server_observed"] | None = None
    locked_at: str | None = None
    source_data_date: str | None = None
    target_trade_date: str | None = None
    target_trade_date_status: Literal["confirmed", "scheduled_unverified"] | None = None
    rule_version: str | None = None
    evaluation_basis: Literal["reference_close", "next_open"] = "reference_close"
    risk_source_status: Literal["available", "partial", "unavailable"] | None = None
    risk_source_as_of: str | None = None
    risk_target_date: str | None = None
    selection_indicator_basis: Literal["raw", "pit_adjusted"] = "raw"
    trend_adjustment_status: Literal["verified", "partial", "unavailable"] | None = None

    h5d_evaluated_count: int = 0
    h20d_evaluated_count: int = 0
    h1d_pending_count: int = 0
    h5d_pending_count: int = 0
    h20d_pending_count: int = 0
    h1d_matured: bool = False
    h5d_matured: bool = False
    h20d_matured: bool = False

    h5d_avg_return_pct: float | None = None
    h20d_avg_return_pct: float | None = None

    h5d_bm_return_pct: float | None = None
    h20d_bm_return_pct: float | None = None

    h5d_excess_pct: float | None = None
    h20d_excess_pct: float | None = None


class StrategyReviewStats(BaseModel):
    """Aggregated performance review for a saved strategy."""

    strategy_id: str
    strategy_name: str
    snapshots_count: int
    evaluated_picks_5d: int
    evaluated_picks_20d: int

    avg_return_5d: float | None = None
    avg_return_20d: float | None = None

    hit_rate_5d: float | None = None  # Percentage 0.0~100.0
    hit_rate_20d: float | None = None  # Percentage 0.0~100.0
    hit_rate_definition: str = "個股期間報酬率 > 0% 之比率"

    bm_excess_5d: float | None = None
    bm_excess_20d: float | None = None


class ConditionReviewStats(BaseModel):
    """Descriptive performance metrics grouped by match reason or condition."""

    condition_label: str
    sample_count_5d: int
    sample_count_20d: int
    is_sample_sufficient: bool = True  # False if sample < 5

    avg_return_5d: float | None = None
    avg_return_20d: float | None = None

    hit_rate_5d: float | None = None
    hit_rate_20d: float | None = None

    disclaimer: str = "僅為歷史樣本敘述性統計，不代表因果關係，未自動調整任何策略權重"
