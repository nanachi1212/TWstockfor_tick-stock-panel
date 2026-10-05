from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.corporate_actions import CorporateActionEvent, event_market_open
from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.evaluation import ActionCoverage
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore
from app.taiwan.quant.live_contract import FEATURES
from app.taiwan.quant.oos_diagnostics import (
    OosFold,
    build_forward_labels_fast,
    diagnose_oos,
    score_variants,
    validate_fast_labels_sample,
)


def _event(symbol: str, day: date, factor: float = 0.9) -> CorporateActionEvent:
    return CorporateActionEvent(
        symbol=symbol,
        exchange="TWSE",
        effective_date=day,
        effective_at=event_market_open(day),
        event_type="cash_dividend",
        previous_close=100.0,
        reference_price=100.0 * factor,
        factor=factor,
        cash_dividend=10.0,
        free_share_ratio=None,
        reduction_ratio=None,
        source="TWT49U",
        source_url="https://www.twse.com.tw/",
        retrieved_at=datetime(2026, 10, 2, tzinfo=TAIPEI),
        status="verified",
        precision_method="official_reference_ratio",
    )


def test_fast_labels_match_formal_adjustment_and_keep_missing_sessions_explicit():
    start = date(2024, 1, 2)
    sessions = [start + timedelta(days=index) for index in range(8)]
    symbols = ["1101.TWSE", "2330.TWSE"]
    rows = [
        {"date": day, "symbol": symbol, "close": 100.0}
        for day in sessions
        for symbol in symbols
        if not (symbol == symbols[1] and day == sessions[2])
    ]
    daily = pl.DataFrame(
        rows, schema={"date": pl.Date, "symbol": pl.String, "close": pl.Float64}
    )
    keys = pl.DataFrame(
        {"date": [sessions[0], sessions[0]], "symbol": symbols},
        schema={"date": pl.Date, "symbol": pl.String},
    )
    events = (_event(symbols[0], sessions[3]),)
    coverage = ActionCoverage(sessions[0], sessions[-1], "verified", tuple(SOURCE_URLS))
    labels = build_forward_labels_fast(
        keys,
        daily,
        sessions=sessions,
        events=events,
        action_coverage=coverage,
        horizons=(5,),
    )
    adjusted = labels.filter(pl.col("symbol") == symbols[0]).row(0, named=True)
    missing = labels.filter(pl.col("symbol") == symbols[1]).row(0, named=True)
    assert adjusted["label_status_5d"] == "verified"
    assert adjusted["forward_return_5d"] == pytest.approx(1 / 0.9 - 1)
    assert missing["label_status_5d"] == "missing_session_price"
    assert missing["forward_return_5d"] is None
    validate_fast_labels_sample(
        keys.filter(pl.col("symbol") == symbols[0]),
        labels.filter(pl.col("symbol") == symbols[0]),
        daily.filter(pl.col("symbol") == symbols[0]),
        sessions=sessions,
        events=events,
        horizons=(5,),
        sample_size=1,
    )


def _diagnostic_fixture() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    days = [date(2024, 1, 2), date(2025, 1, 2)]
    symbols = [f"{1100 + index}.TWSE" for index in range(1, 6)]
    matrix_rows = []
    label_rows = []
    benchmark_rows = []
    auxiliary_rows = []
    for day in days:
        for index, symbol in enumerate(symbols, start=1):
            matrix_rows.append({
                "date": day,
                "symbol": symbol,
                "factor_version": "tw-factors-v1",
                "policy_version": "v1",
                "universe_tier": "primary_verified",
                "momentum_5d": float(index),
                "momentum_20d": float(index * 2),
                "momentum_60d": float(index * 3),
            })
            label_rows.append({
                "date": day,
                "symbol": symbol,
                "forward_return_5d": index / 100.0,
                "label_status_5d": "verified",
                "label_end_5d": day + timedelta(days=5),
                "forward_return_20d": index / 50.0,
                "label_status_20d": "verified",
                "label_end_20d": day + timedelta(days=20),
            })
            auxiliary_rows.append({
                "date": day,
                "symbol": symbol,
                "amount": float(index * 1_000_000),
                "adv20_twd": float(index * 2_000_000),
                "close": float(index * 10),
            })
        benchmark_rows.append({
            "date": day,
            "symbol": "0050.TWSE",
            "forward_return_5d": 0.02,
            "label_status_5d": "verified",
            "label_end_5d": day + timedelta(days=5),
            "forward_return_20d": 0.04,
            "label_status_20d": "verified",
            "label_end_20d": day + timedelta(days=20),
        })
    return (
        pl.DataFrame(matrix_rows),
        pl.DataFrame(label_rows),
        pl.DataFrame(benchmark_rows),
        pl.DataFrame(auxiliary_rows),
    )


def test_score_variants_use_one_complete_case_universe_and_higher_is_better():
    matrix, _, _, _ = _diagnostic_fixture()
    scored, variants = score_variants(matrix)
    assert set(variants) == {
        "full",
        *(f"minus_{feature}" for feature in FEATURES),
        *(f"single_{feature}" for feature in FEATURES),
    }
    highest = scored.filter(pl.col("symbol") == "1105.TWSE")
    assert highest["score__full"].to_list() == [1.0, 1.0]
    assert highest["score__single_momentum_5d"].to_list() == [1.0, 1.0]


def test_diagnostics_separate_top_outperformance_and_benchmark_excess():
    matrix, labels, benchmark, auxiliary = _diagnostic_fixture()
    report = diagnose_oos(
        matrix,
        labels,
        benchmark,
        folds=[OosFold(0, date(2024, 1, 1), date(2025, 12, 31))],
        auxiliary=auxiliary,
    )
    five = report["signals"]["full"]["horizons"]["5"]
    assert five["ic"]["mean"] == pytest.approx(1.0)
    assert five["tails"]["top"] == pytest.approx(0.05)
    assert five["tails"]["bottom"] == pytest.approx(0.01)
    assert five["tails"]["top_vs_benchmark"] == pytest.approx(0.03)
    assert [row["bucket"] for row in five["buckets"]] == [1, 2, 3, 4, 5]
    assert report["integrity"]["industry_concentration"]["status"] == "data_insufficient"
    assert report["integrity"]["price_liquidity_concentration"]["status"] == "available"


def test_store_reads_one_successful_run_without_mutating_history(tmp_path):
    store = PrimaryOosRunStore(tmp_path / "runs.sqlite3")
    context = {"health": {}, "a2b": {}, "spec_hash": "spec"}
    store.begin(run_id="run-1", identity_key="identity", recorded_at="start", context=context)
    store.succeed(
        run_id="run-1",
        identity_key="identity",
        recorded_at="done",
        context=context,
        artifact={"evaluation": {"status": "success"}},
    )
    result = store.successful_run("run-1")
    assert result == {
        "run_id": "run-1",
        "identity_key": "identity",
        "recorded_at": "done",
        "artifact": {"evaluation": {"status": "success"}},
    }
    assert store.successful_run("missing") is None
