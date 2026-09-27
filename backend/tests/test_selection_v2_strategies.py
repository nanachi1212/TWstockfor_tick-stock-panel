from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl

from app.taiwan.screener import (
    DataDatesInfo,
    ScreenerResultItem,
    TaiwanScreenerRequest,
    TaiwanScreenerResponse,
)
from app.taiwan.selection_review_models import (
    HorizonReviewItem,
    SelectionSnapshot,
    SelectionSnapshotItem,
    SnapshotReviewDetail,
)
from app.taiwan.selection_review_service import TaiwanSelectionReviewService
from app.taiwan.selection_v2 import (
    STRATEGY_IDS,
    apply_strategy,
    rank_strategy,
    strategy_readiness,
)


def _frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["2330.TWSE", "2454.TWSE", "2303.TWSE"],
        "institutional_status": ["available", "available", "unavailable"],
        "foreign_net_5d": [2_000_000.0, 1_000_000.0, None],
        "investment_trust_net_5d": [1_000_000.0, 2_000_000.0, None],
        "institutional_flow_ratio_5d": [0.03, 0.02, None],
        "close": [110.0, 105.0, 120.0],
        "ma20": [100.0, 100.0, 100.0],
        "ma60": [90.0, 100.0, 100.0],
        "amount": [80_000_000.0, 60_000_000.0, 90_000_000.0],
        "revenue_status": ["available", "available", "unavailable"],
        "revenue_yoy": [20.0, 5.0, None],
        "revenue_yoy_improving": [True, False, None],
        "revenue_yoy_improvement": [3.0, -1.0, None],
        "momentum_5d": [0.08, 0.05, 0.2],
        "momentum_20d": [0.15, 0.03, 0.2],
        "breakout_20d_high": [100.0, 110.0, 100.0],
        "breakout_60d_high": [100.0, 110.0, 100.0],
        "vol_ratio_20d": [1.5, 1.3, None],
        "momentum_acceleration": [0.02, 0.01, None],
        "breakout_20d_strength": [0.1, -0.04, None],
        "breakout_60d_strength": [0.1, -0.04, None],
    })


def test_all_v2_strategies_are_deterministic_and_fail_closed_on_nulls():
    frame = _frame()
    for strategy_id in STRATEGY_IDS[1:]:
        first = rank_strategy(apply_strategy(frame, strategy_id), strategy_id)["symbol"].to_list()
        second = rank_strategy(apply_strategy(frame, strategy_id), strategy_id)["symbol"].to_list()
        assert first == second
        assert "2303.TWSE" not in first


def test_consensus_is_independent_of_input_order_and_shows_hits():
    frame = _frame()
    ordered = rank_strategy(apply_strategy(frame, "multi_factor_consensus_v1"), "multi_factor_consensus_v1")
    shuffled = rank_strategy(
        apply_strategy(frame.sample(fraction=1.0, shuffle=True, seed=7), "multi_factor_consensus_v1"),
        "multi_factor_consensus_v1",
    )
    assert ordered["symbol"].to_list() == shuffled["symbol"].to_list()
    assert ordered["consensus_hit_count"].to_list() == [3]


def test_readiness_reports_coverage_without_fabricating_candidates():
    frame = _frame().with_columns(pl.lit("unavailable").alias("revenue_status"))
    readiness, reasons, coverage = strategy_readiness(
        frame, "growth_trend_v1", quote_coverage_status="verified", risk_source_status="available"
    )
    assert readiness == "degraded"
    assert "月營收資料不可用" in reasons
    assert coverage["revenue_available_count"] == 0
    assert apply_strategy(frame, "growth_trend_v1").is_empty()


def _snapshot(strategy_id: str, snapshot_id: str) -> SelectionSnapshot:
    return SelectionSnapshot(
        snapshot_id=snapshot_id,
        created_at="2026-09-01T00:00:00+00:00",
        strategy_id=strategy_id,
        strategy_version="v1",
        strategy_name=strategy_id,
        as_of_date="2026-09-01",
        market_context_summary="synthetic",
        source_data_date="2026-09-01",
        record_type="forward_batch",
        selected_symbols=["2330.TWSE"],
        items=[SelectionSnapshotItem(symbol="2330.TWSE", name="台積電", rank=1, price=100.0)],
    )


def test_forward_stats_filter_strategies_and_keep_zero_return(tmp_path, monkeypatch):
    service = TaiwanSelectionReviewService(path=tmp_path / "snapshots.json")
    v1 = _snapshot("trend_liquidity_v1", "forward_trend_liquidity_v1_20260901")
    v2 = _snapshot("breakout_v1", "forward_breakout_v1_20260901")
    service._save_snapshots_raw([v1, v2])

    def review(snapshot):
        return SnapshotReviewDetail(
            snapshot=snapshot,
            evaluated_items=[HorizonReviewItem(
                symbol="2330.TWSE", name="台積電", rank=1, entry_price=100.0,
                h1d_status="completed", h1d_raw_return_pct=0.0, h1d_return_pct=0.0,
                h1d_bm_status="completed", h1d_raw_bm_return_pct=-1.0,
                h1d_bm_return_pct=-1.0, h1d_excess_pct=1.0, h1d_raw_excess_pct=1.0,
            )],
        )

    monkeypatch.setattr(service, "_forward_review_inputs", lambda: ((), (), ()))
    monkeypatch.setattr(service, "_get_forward_batch_review", review)
    monkeypatch.setattr(service, "_is_forward_horizon_matured", lambda snapshot, horizon: horizon == 1)

    stats = service.get_forward_batch_stats(strategy_id="breakout_v1")
    summary = stats.horizons["1D"]["full_batch"]
    assert stats.batches_count == 1
    assert summary.evaluable_count == 1
    assert summary.positive_return_count == 0
    assert summary.hit_rate == 0.0
    assert summary.average_return_pct == 0.0
    assert summary.beat_benchmark_count == 1
    assert service.get_forward_batch_stats(strategy_id="growth_trend_v1").batches_count == 0


def test_same_source_date_allows_multiple_strategy_batches_but_is_idempotent(tmp_path, monkeypatch):
    service = TaiwanSelectionReviewService(path=tmp_path / "snapshots.json")
    source = "2026-09-24"
    target = "2026-09-25"
    evidence = {"start": source, "end": source, "events_sha256": "x", "saved_at": "now"}
    monkeypatch.setattr(service, "_daily_generation", lambda: ())
    monkeypatch.setattr(service, "_census_generation", lambda: ())
    monkeypatch.setattr(service, "_action_coverage_evidence", lambda: evidence)
    monkeypatch.setattr(service.daily_store, "available_dates", lambda: [date.fromisoformat(source)])
    monkeypatch.setattr(service, "_next_potential_session", lambda _: date.fromisoformat(target))
    monkeypatch.setattr(
        "app.taiwan.selection_review_service.taipei_now",
        lambda: datetime(2026, 9, 24, 16, 0, tzinfo=UTC),
    )

    class FakeScreener:
        def run(self, request: TaiwanScreenerRequest):
            return TaiwanScreenerResponse(
                items=[ScreenerResultItem(
                    symbol="2330.TWSE", name="台積電", exchange="TWSE", instrument_type="stock",
                    close=100.0, risk_status="clear", strategy_id=request.preset,
                    strategy_version="v1", strategy_signals=[request.preset or ""],
                )],
                total=1, page=1, page_size=20, sort_by=request.preset or "symbol", sort_order="desc",
                data_dates=DataDatesInfo(daily_as_of=source), quote_coverage_status="verified",
                risk_source_status="available", risk_unknown_count=0, risk_target_date=target,
                strategy_id=request.preset, strategy_name=request.preset, strategy_version="v1",
                strategy_readiness="ready",
            )

    first = service.lock_forward_batch(FakeScreener(), strategy_id="institutional_momentum_v1")
    second = service.lock_forward_batch(FakeScreener(), strategy_id="breakout_v1")
    retry = service.lock_forward_batch(FakeScreener(), strategy_id="institutional_momentum_v1")
    assert first.snapshot_id != second.snapshot_id
    assert retry == first
    assert {s.strategy_id for s in service._read_snapshots_raw()} == {
        "institutional_momentum_v1", "breakout_v1",
    }
