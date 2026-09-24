from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.providers.corporate_actions import SOURCE_URLS
from app.taiwan.providers.taiwan_values import TAIPEI, market_close
from app.taiwan.quant.data_health import evaluate_data_health
from app.taiwan.quant.evaluation import ActionCoverage
from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore
from app.taiwan.quant.feature_manifest import EligibilityResolution, FeatureVerdict
from app.taiwan.quant.live_contract import FEATURES, LIVE_TIER, canonical_hash
from app.taiwan.quant.primary_oos_runner import (
    PrimaryOosEvaluationInputs,
    PrimaryOosInputError,
    PrimaryOosNotReadyError,
    PrimaryOosPreflight,
    PrimaryOosRunFailedError,
    run_primary_oos_evaluation,
)
from app.taiwan.quant.training import TrainingMatrixResult
from app.taiwan.quant.validation.folds import FoldConfig


def _sessions(count: int) -> list[date]:
    result = []
    day = date(2020, 1, 1)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


def _preflight(*, ready: bool = True) -> PrimaryOosPreflight:
    health = evaluate_data_health(
        census_sessions=2850,
        census_total_sessions=2850,
        twse_codes_observed=100,
        twse_codes_classified=100 if ready else 40,
    )
    progress = {
        "completed_jobs": 478 if ready else 220,
        "pending_jobs": 0 if ready else 258,
        "failed_jobs": 0,
        "unique_first_seen_dates": 478,
    }
    return PrimaryOosPreflight(health, progress, "idle" if ready else "running", date(2026, 9, 23))


def _spec():
    return replace(
        PRIMARY_OOS_SPEC,
        version="test-primary-oos-v1",
        fold_config=FoldConfig(
            train_sessions=30,
            val_sessions=8,
            test_sessions=15,
            step_sessions=15,
            label_horizon=20,
            embargo_sessions=5,
        ),
    )


def _inputs(preflight: PrimaryOosPreflight | None = None) -> PrimaryOosEvaluationInputs:
    preflight = preflight or _preflight()
    feature_sessions = _sessions(120)
    market_sessions = _sessions(140)
    symbols = [f"{code}.TWSE" for code in ("1101", "1216", "1301", "2002", "2330")]
    feature_rows = []
    price_rows = []
    for session_index, day in enumerate(feature_sessions):
        for symbol_index, symbol in enumerate(symbols):
            rank = float(symbol_index + 1)
            feature_rows.append({
                "date": day,
                "symbol": symbol,
                "momentum_5d": rank + session_index * 0.0001,
                "momentum_20d": rank + session_index * 0.0002,
                "momentum_60d": rank + session_index * 0.0003,
                "universe_tier": "primary_verified",
                "usage_scope": "pit_feature",
                "adjustment_as_of": market_close(day).isoformat(),
                "adjustment_status": "verified",
            })
    for session_index, day in enumerate(market_sessions):
        for symbol_index, symbol in enumerate(symbols):
            base = 100.0 + symbol_index * 2
            close = base + session_index * (symbol_index + 1) * 0.1 + session_index**2 * 0.001
            price_rows.append({"date": day, "symbol": symbol, "close": close})
    matrix = pl.DataFrame(feature_rows)
    resolution = EligibilityResolution("fixture-feature-schema", tuple(
        FeatureVerdict(name, "corporate_action", "technical", True) for name in FEATURES
    ))
    admission = TrainingMatrixResult(matrix, resolution, (), {})
    daily = pl.DataFrame(price_rows, schema={
        "date": pl.Date, "symbol": pl.String, "close": pl.Float64,
    })
    coverage = ActionCoverage(
        feature_sessions[0], market_sessions[-1], "verified", tuple(sorted(SOURCE_URLS)),
    )
    return PrimaryOosEvaluationInputs(
        preflight=preflight,
        admission=admission,
        daily=daily,
        exchange_by_symbol={symbol: "TWSE" for symbol in symbols},
        sessions_by_exchange={"TWSE": market_sessions},
        walk_forward_sessions=feature_sessions,
        events=(),
        action_coverage={"TWSE": coverage},
        classification_identity="fixture-classification-sha256",
        latest_market_date=market_sessions[-1],
    )


def _run(path, inputs, *, spec=None):
    fixed_now = datetime(2026, 9, 24, 16, 30, tzinfo=TAIPEI)
    return run_primary_oos_evaluation(
        inputs.preflight,
        input_loader=lambda _: inputs,
        store=PrimaryOosRunStore(path),
        spec=spec or _spec(),
        code_sha="a" * 40,
        now=lambda: fixed_now,
    )


def test_not_ready_refuses_before_loading_or_creating_a_run(tmp_path):
    preflight = _preflight(ready=False)
    called = False

    def loader(_):
        nonlocal called
        called = True
        raise AssertionError("input loading must not begin before readiness")

    store = PrimaryOosRunStore(tmp_path / "runs.sqlite3")
    with pytest.raises(PrimaryOosNotReadyError) as exc:
        run_primary_oos_evaluation(preflight, input_loader=loader, store=store)
    assert exc.value.readiness.status.value == "processing"
    assert called is False
    assert not store.path.exists()


def test_formal_entry_has_no_force_or_health_override_parameter():
    inputs = _inputs()
    with pytest.raises(TypeError, match="force"):
        run_primary_oos_evaluation(inputs.preflight, force=True)  # type: ignore[call-arg]


def test_ready_run_saves_complete_provenance_and_reproducible_metrics(tmp_path):
    inputs = _inputs()
    first = _run(tmp_path / "first.sqlite3", inputs)
    second = _run(tmp_path / "second.sqlite3", inputs)
    assert first["status"] == "evaluation_available"
    assert first["evaluation"]["primary_oos_ready"] is True
    assert first["evaluation"]["claim_scope"] == "primary_verified_oos"
    assert first["evaluation"]["horizons"] == [5, 20]
    assert canonical_hash(first["evaluation"]) == canonical_hash(second["evaluation"])
    assert first["provenance"]["dataset_identity"] == second["provenance"]["dataset_identity"]
    assert first["provenance"]["code_sha"] == "a" * 40
    assert first["provenance"]["a2b_classification_identity"] == "fixture-classification-sha256"
    assert first["provenance"]["evaluation_spec_version"] == _spec().version
    assert first["provenance"]["latest_market_date"] == inputs.latest_market_date.isoformat()
    assert first["provenance"]["random_seed"] is None


def test_duplicate_same_code_spec_and_dataset_reuses_immutable_result(tmp_path):
    inputs = _inputs()
    store = PrimaryOosRunStore(tmp_path / "runs.sqlite3")
    first = run_primary_oos_evaluation(
        inputs.preflight, input_loader=lambda _: inputs, store=store,
        spec=_spec(), code_sha="b" * 40,
    )
    second = run_primary_oos_evaluation(
        inputs.preflight, input_loader=lambda _: inputs, store=store,
        spec=_spec(), code_sha="b" * 40,
    )
    assert second["reused"] is True
    assert second["provenance"]["run_id"] == first["provenance"]["run_id"]
    state = store.latest_state(
        health=inputs.preflight.data_health,
        progress=inputs.preflight.a2b_progress,
        spec_hash=_spec().fingerprint,
    )
    assert state["event_type"] == "succeeded"


@pytest.mark.parametrize("mutate", ["live_universe", "future_label"])
def test_runner_rejects_live_universe_and_future_labels(tmp_path, mutate):
    inputs = _inputs()
    matrix = inputs.admission.matrix
    if mutate == "live_universe":
        matrix = matrix.with_columns(pl.lit(LIVE_TIER).alias("universe_tier"))
    else:
        matrix = matrix.with_columns(pl.lit(0.0).alias("forward_return_5d"))
    invalid = replace(inputs, admission=replace(inputs.admission, matrix=matrix))
    with pytest.raises(PrimaryOosInputError):
        _run(tmp_path / f"{mutate}.sqlite3", invalid)


def test_failed_new_dataset_run_does_not_replace_previous_success(tmp_path, monkeypatch):
    import app.taiwan.quant.primary_oos_runner as runner

    inputs = _inputs()
    store = PrimaryOosRunStore(tmp_path / "runs.sqlite3")
    success = run_primary_oos_evaluation(
        inputs.preflight, input_loader=lambda _: inputs, store=store,
        spec=_spec(), code_sha="c" * 40,
    )
    changed = replace(inputs, classification_identity="revised-classification-snapshot")

    def fail_evaluation(*args, **kwargs):
        raise RuntimeError("synthetic evaluation failure")

    monkeypatch.setattr(runner, "evaluate_quant", fail_evaluation)
    with pytest.raises(PrimaryOosRunFailedError):
        run_primary_oos_evaluation(
            changed.preflight, input_loader=lambda _: changed, store=store,
            spec=_spec(), code_sha="c" * 40,
        )
    previous = store.latest_success(
        health=inputs.preflight.data_health,
        progress=inputs.preflight.a2b_progress,
        spec_hash=_spec().fingerprint,
    )
    assert previous["provenance"]["dataset_identity"] == success["provenance"]["dataset_identity"]
    assert previous["evaluation"]["primary_oos_ready"] is True
    assert store.latest_state(
        health=inputs.preflight.data_health,
        progress=inputs.preflight.a2b_progress,
        spec_hash=_spec().fingerprint,
    )["event_type"] == "failed"
