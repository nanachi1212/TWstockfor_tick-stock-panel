from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.providers.taiwan_values import market_close
from app.taiwan.quant.data_health import ReadinessLevel, evaluate_data_health
from app.taiwan.quant.evaluation import (
    ActionCoverage,
    build_forward_labels,
    evaluate_quant,
)
from app.taiwan.quant.feature_manifest import EligibilityResolution, FeatureVerdict
from app.taiwan.quant.live_contract import FEATURES
from app.taiwan.quant.panel import build_factor_panel
from app.taiwan.quant.training import TrainingMatrixResult
from app.taiwan.quant.validation.folds import FoldConfig


def _inputs(*, count: int = 60, missing: tuple[str, date] | None = None):
    start = date(2020, 1, 1)
    sessions = [start + timedelta(days=i) for i in range(count)]
    symbols = [f"{code}.TWSE" for code in ("1101", "1216", "1301", "2002", "2330")]
    matrix_rows = []
    price_rows = []
    for day_index, day in enumerate(sessions):
        for symbol_index, symbol in enumerate(symbols):
            rank = float(symbol_index + 1)
            matrix_rows.append({
                "date": day,
                "symbol": symbol,
                "momentum_5d": rank,
                "momentum_20d": rank,
                "momentum_60d": rank,
                "factor_alpha": rank,
                "universe_tier": "primary_verified",
                "usage_scope": "pit_feature",
                "adjustment_as_of": market_close(day).isoformat(),
                "adjustment_status": "verified",
            })
            if missing != (symbol, day):
                price_rows.append({"date": day, "symbol": symbol,
                                   "close": 100.0 + day_index * (symbol_index + 1)})
    features = ("factor_alpha", *FEATURES)
    resolution = EligibilityResolution("eval-v1", tuple(
        FeatureVerdict(name, "daily", "technical", True) for name in features
    ))
    admission = TrainingMatrixResult(
        pl.DataFrame(matrix_rows), resolution, (), {},
    )
    daily = pl.DataFrame(price_rows, schema={
        "date": pl.Date, "symbol": pl.String, "close": pl.Float64,
    })
    coverage = ActionCoverage(sessions[0], sessions[-1], "verified", tuple(SOURCE_URLS))
    return admission, daily, sessions, symbols, coverage


def _evaluate(admission, daily, sessions, symbols, coverage):
    config = FoldConfig(train_sessions=2, val_sessions=1, test_sessions=2,
                        step_sessions=2, label_horizon=20, embargo_sessions=0)
    return evaluate_quant(
        admission,
        daily,
        exchange_by_symbol={symbol: "TWSE" for symbol in symbols},
        sessions_by_exchange={"TWSE": sessions},
        walk_forward_sessions=sessions,
        events=(),
        action_coverage={"TWSE": coverage},
        horizons=(5, 20),
        fold_config=config,
    )


def test_labels_use_exact_future_sessions_and_remain_outside_features():
    admission, daily, sessions, symbols, coverage = _inputs()
    labels = build_forward_labels(
        admission, daily,
        exchange_by_symbol={symbol: "TWSE" for symbol in symbols},
        sessions_by_exchange={"TWSE": sessions}, events=(),
        action_coverage={"TWSE": coverage}, horizons=(5, 20),
    )
    first = labels.filter((pl.col("symbol") == symbols[0]) &
                          (pl.col("date") == sessions[0])).row(0, named=True)
    assert first["label_status_5d"] == "verified"
    assert first["label_end_5d"] == sessions[5]
    assert first["forward_return_5d"] == pytest.approx(0.05)
    assert first["label_end_20d"] == sessions[20]
    assert "forward_return_5d" not in admission.matrix.columns
    assert "forward_return_20d" not in admission.matrix.columns


def test_future_price_change_changes_only_evaluation_label_not_feature_rows():
    admission, daily, sessions, symbols, coverage = _inputs()
    changed = daily.with_columns(
        pl.when((pl.col("symbol") == symbols[0]) & (pl.col("date") == sessions[5]))
        .then(pl.col("close") * 2)
        .otherwise(pl.col("close")).alias("close")
    )
    kwargs = {
        "exchange_by_symbol": {symbol: "TWSE" for symbol in symbols},
        "sessions_by_exchange": {"TWSE": sessions},
        "events": (), "action_coverage": {"TWSE": coverage}, "horizons": (5,),
    }
    before = build_forward_labels(admission, daily, **kwargs)
    after = build_forward_labels(admission, changed, **kwargs)
    assert admission.matrix.columns == [
        "date", "symbol", *FEATURES, "factor_alpha", "universe_tier",
        "usage_scope", "adjustment_as_of", "adjustment_status",
    ]
    before_value = before.filter((pl.col("symbol") == symbols[0]) &
                                 (pl.col("date") == sessions[0]))["forward_return_5d"].item()
    after_value = after.filter((pl.col("symbol") == symbols[0]) &
                               (pl.col("date") == sessions[0]))["forward_return_5d"].item()
    assert after_value != before_value

    history_rows = [{
        "date": day, "symbol": symbols[0], "open": close, "high": close + 1,
        "low": close - 1, "close": close, "volume": 1000.0, "amount": 100_000.0,
    } for index, day in enumerate(sessions[:25]) for close in [100.0 + index]]
    original_history = pl.DataFrame(history_rows)
    future_changed_history = original_history.with_columns(
        pl.when(pl.col("date") >= sessions[5])
        .then(pl.col("close") * 2).otherwise(pl.col("close")).alias("close"),
        pl.when(pl.col("date") >= sessions[5])
        .then(pl.col("open") * 2).otherwise(pl.col("open")).alias("open"),
        pl.when(pl.col("date") >= sessions[5])
        .then(pl.col("high") * 2).otherwise(pl.col("high")).alias("high"),
        pl.when(pl.col("date") >= sessions[5])
        .then(pl.col("low") * 2).otherwise(pl.col("low")).alias("low"),
    )
    panel_args = {"events": (), "policy_version": "p1", "universe_tier": "primary_verified",
                  "as_of": sessions[0]}
    before_panel = build_factor_panel(original_history, **panel_args)
    after_panel = build_factor_panel(future_changed_history, **panel_args)
    assert before_panel.values.equals(after_panel.values)


def test_feature_timestamp_must_match_its_session_close():
    admission, daily, sessions, symbols, coverage = _inputs()
    matrix = admission.matrix.with_columns(
        pl.when(pl.col("date") == sessions[0])
        .then(pl.lit(market_close(sessions[1]).isoformat()))
        .otherwise(pl.col("adjustment_as_of")).alias("adjustment_as_of")
    )
    invalid = TrainingMatrixResult(matrix, admission.resolution, (), {})
    with pytest.raises(ValueError, match=r"feature timestamp|historical feature row"):
        build_forward_labels(
            invalid, daily,
            exchange_by_symbol={symbol: "TWSE" for symbol in symbols},
            sessions_by_exchange={"TWSE": sessions}, events=(),
            action_coverage={"TWSE": coverage}, horizons=(5,),
        )


def test_missing_future_bar_is_counted_as_insufficient_not_skipped():
    admission, daily, sessions, symbols, coverage = _inputs(missing=("1101.TWSE", date(2020, 1, 6)))
    report = _evaluate(admission, daily, sessions, symbols, coverage)
    assert report["label_coverage"]["5"]["by_status"]["missing_session_price"] >= 1
    missing_label = build_forward_labels(
        admission, daily,
        exchange_by_symbol={symbol: "TWSE" for symbol in symbols},
        sessions_by_exchange={"TWSE": sessions}, events=(),
        action_coverage={"TWSE": coverage}, horizons=(5,),
    ).filter((pl.col("symbol") == "1101.TWSE") &
             (pl.col("date") == date(2020, 1, 1))).row(0, named=True)
    assert missing_label["label_status_5d"] == "missing_session_price"
    assert missing_label["forward_return_5d"] is None


def test_walk_forward_is_ordered_disjoint_and_reproducible():
    admission, daily, sessions, symbols, coverage = _inputs()
    first = _evaluate(admission, daily, sessions, symbols, coverage)
    second = _evaluate(admission, daily, sessions, symbols, coverage)
    assert first == second
    folds = first["walk_forward"]["folds"]
    assert len(folds) > 1
    assert all(folds[i]["test_end"] < folds[i + 1]["test_start"]
               for i in range(len(folds) - 1))
    assert folds[0]["test_start"] > folds[0]["validation_end"]
    assert first["walk_forward"]["oos"]["composite_score_ic"]["5"]["n_dates"] > 0
    assert first["composite_score_buckets"]["5"]["long_short_spread"] > 0
    assert first["claim_scope"] == "primary_oos_readiness_blocked"
    assert first["primary_oos_ready"] is False


def test_primary_oos_claim_requires_existing_data_health_gate():
    admission, daily, sessions, symbols, coverage = _inputs()
    health = evaluate_data_health(
        census_sessions=2850, census_total_sessions=2850,
        twse_codes_observed=100, twse_codes_classified=100,
    )
    config = FoldConfig(train_sessions=2, val_sessions=1, test_sessions=2,
                        step_sessions=2, label_horizon=20, embargo_sessions=0)
    report = evaluate_quant(
        admission, daily,
        exchange_by_symbol={symbol: "TWSE" for symbol in symbols},
        sessions_by_exchange={"TWSE": sessions}, walk_forward_sessions=sessions,
        events=(), action_coverage={"TWSE": coverage}, data_health=health,
        horizons=(5, 20), fold_config=config,
    )
    assert health.is_ready(ReadinessLevel.PRIMARY_OOS)
    assert report["claim_scope"] == "primary_verified_oos"
    assert report["primary_oos_ready"] is True


def test_current_live_universe_cannot_be_used_for_historical_evaluation():
    admission, daily, sessions, symbols, coverage = _inputs()
    matrix = admission.matrix.with_columns(pl.lit("current_live_verified").alias("universe_tier"))
    live = TrainingMatrixResult(matrix, admission.resolution, (), {})
    with pytest.raises(ValueError, match="current live universe"):
        _evaluate(live, daily, sessions, symbols, coverage)
