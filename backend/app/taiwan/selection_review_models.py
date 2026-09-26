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


class SelectionSnapshot(BaseModel):
    """Immutable snapshot of screener run."""

    snapshot_id: str = Field(..., description="唯一識別碼，格式如 snap_YYYYMMDD_HHMMSS_xxxx")
    created_at: str = Field(..., description="建立時間戳記 ISO 8601")
    strategy_id: str = Field(..., description="策略代號")
    strategy_name: str = Field(..., description="策略名稱")
    as_of_date: str = Field(..., description="選股資料基準日 (YYYY-MM-DD 交易日)")
    market_context_summary: str = Field(..., description="當時大盤環境摘要")
    selected_symbols: list[str] = Field(default_factory=list, description="入選代碼清單")
    items: list[SelectionSnapshotItem] = Field(default_factory=list, description="每檔入選標的明細快照")


class SaveSelectionSnapshotRequest(BaseModel):
    """Request payload to save current screener results as an immutable snapshot."""

    strategy_id: str
    strategy_name: str
    as_of_date: str
    market_context_summary: str = ""
    items: list[SelectionSnapshotItem]


class HorizonReviewItem(BaseModel):
    """Evaluation result for a single stock across 1D, 5D, 20D horizons."""

    symbol: str
    name: str
    rank: int
    entry_price: float
    quant_score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)
    fundamental_summary: str | None = None
    chips_summary: str | None = None
    event_risk_summary: str | None = None

    # 1D horizon (optional / bonus)
    h1d_price: float | None = None
    h1d_return_pct: float | None = None
    h1d_status: HorizonStatus = "pending"
    h1d_bm_return_pct: float | None = None
    h1d_excess_pct: float | None = None

    # 5D horizon
    h5d_price: float | None = None
    h5d_return_pct: float | None = None
    h5d_status: HorizonStatus = "pending"
    h5d_bm_return_pct: float | None = None
    h5d_excess_pct: float | None = None

    # 20D horizon
    h20d_price: float | None = None
    h20d_return_pct: float | None = None
    h20d_status: HorizonStatus = "pending"
    h20d_bm_return_pct: float | None = None
    h20d_excess_pct: float | None = None

    benchmark_symbol: str = "0050.TWSE"
    benchmark_name: str = "台灣50"


class SnapshotReviewDetail(BaseModel):
    """Detailed review response for a specific snapshot."""

    snapshot: SelectionSnapshot
    evaluated_items: list[HorizonReviewItem] = Field(default_factory=list)

    h5d_evaluated_count: int = 0
    h20d_evaluated_count: int = 0

    h5d_avg_return_pct: float | None = None
    h20d_avg_return_pct: float | None = None

    h5d_bm_avg_return_pct: float | None = None
    h20d_bm_avg_return_pct: float | None = None

    h5d_avg_excess_pct: float | None = None
    h20d_avg_excess_pct: float | None = None


class SnapshotListItem(BaseModel):
    """Brief summary item in the snapshot list view."""

    snapshot_id: str
    created_at: str
    strategy_id: str
    strategy_name: str
    as_of_date: str
    selected_count: int

    h5d_evaluated_count: int = 0
    h20d_evaluated_count: int = 0

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
