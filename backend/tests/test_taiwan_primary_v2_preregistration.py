from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.quant.live_contract import canonical_hash
from app.taiwan.quant.primary_v2_preregistration import (
    CANDIDATES,
    PREREGISTRATION_ID,
    BenchmarkResult,
    CandidateSelectionMetrics,
    CandidateSpec,
    ConfirmatoryStatus,
    EvaluationPurpose,
    build_confirmatory_run_contract,
    build_confirmatory_window,
    describe_preregistration,
    guard_evaluation_frame,
    preregistration_content,
    select_candidate,
)
from app.taiwan.quant.primary_v2_status import model_validation_status


def _verified_sessions(count: int) -> list[date]:
    start = date(2026, 8, 5)
    return [start + timedelta(days=index) for index in range(count)]


def test_candidate_identities_are_deterministic_and_contract_sensitive() -> None:
    assert all(candidate.identity == candidate.identity for candidate in CANDIDATES)
    baseline = CANDIDATES[0]
    changed = CandidateSpec(baseline.key, baseline.factors, (0.2, 0.3, 0.5))
    assert changed.identity != baseline.identity


def test_preregistration_id_is_deterministic_and_selection_sensitive() -> None:
    assert canonical_hash(preregistration_content()) == PREREGISTRATION_ID
    assert describe_preregistration()["preregistration_id"] == PREREGISTRATION_ID
    changed = deepcopy(preregistration_content())
    changed["selection_rule"]["minimum_valid_dates"] = 41
    assert canonical_hash(changed) != PREREGISTRATION_ID


def test_selector_uses_metrics_then_preregistered_tie_break() -> None:
    tied = CandidateSelectionMetrics(0.01, 0.02, 40, 1200, 30)
    selected = select_candidate({candidate.key: tied for candidate in reversed(CANDIDATES)})
    assert selected == CANDIDATES[0]
    assert select_candidate({CANDIDATES[0].key: CandidateSelectionMetrics(
        None, 0.02, 40, 1200, 30,
    )}) is None


def test_confirmatory_start_uses_first_verified_session_after_v1() -> None:
    sessions = [date(2026, 8, 4), date(2026, 8, 6), date(2026, 8, 7)]
    window = build_confirmatory_window(sessions)
    assert window.start_session == date(2026, 8, 6)
    assert window.status is ConfirmatoryStatus.WAITING_FOR_SESSIONS


def test_confirmatory_readiness_waits_for_sessions_and_labels() -> None:
    short = build_confirmatory_window(_verified_sessions(62))
    assert short.status is ConfirmatoryStatus.WAITING_FOR_SESSIONS

    pending = build_confirmatory_window(_verified_sessions(63 + 19))
    assert pending.status is ConfirmatoryStatus.WAITING_FOR_LABELS
    assert pending.label_maturity == {5: True, 20: False}

    ready = build_confirmatory_window(_verified_sessions(63 + 20))
    assert ready.status is ConfirmatoryStatus.READY
    assert ready.label_maturity == {5: True, 20: True}


def test_development_guard_excludes_confirmatory_block() -> None:
    window = build_confirmatory_window(_verified_sessions(40))
    frame = pl.DataFrame({
        "date": [date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)],
        "value": [1, 2, 3],
    })
    guarded = guard_evaluation_frame(
        frame, purpose=EvaluationPurpose.DEVELOPMENT_SELECTION, window=window,
    )
    assert guarded["date"].to_list() == [date(2026, 8, 4)]

    no_calendar_evidence = build_confirmatory_window([])
    guarded_without_start = guard_evaluation_frame(
        frame, purpose=EvaluationPurpose.RETROSPECTIVE_DIAGNOSTIC,
        window=no_calendar_evidence,
    )
    assert guarded_without_start["date"].to_list() == [date(2026, 8, 4)]


def test_confirmatory_guard_fails_closed_until_final_20d_label_matures() -> None:
    frame = pl.DataFrame({"date": _verified_sessions(83), "value": range(83)})
    pending = build_confirmatory_window(_verified_sessions(82))
    with pytest.raises(RuntimeError, match="waiting_for_labels"):
        guard_evaluation_frame(
            frame, purpose=EvaluationPurpose.CONFIRMATORY_EVALUATION, window=pending,
        )
    ready = build_confirmatory_window(_verified_sessions(83))
    guarded = guard_evaluation_frame(
        frame, purpose=EvaluationPurpose.CONFIRMATORY_EVALUATION, window=ready,
    )
    assert guarded.height == 63
    assert guarded["date"].max() == ready.block_end_session

    contract = build_confirmatory_run_contract(
        window=ready,
        selected_candidate_key="primary_v2_momentum_60d",
        dataset_identity="future-dataset",
    )
    assert contract.preregistration_id == PREREGISTRATION_ID
    assert contract.run_identity == contract.run_identity
    assert contract.describe()["selection_rule"]["outer_test_access"] == "prohibited"

    with pytest.raises(RuntimeError, match="waiting_for_labels"):
        build_confirmatory_run_contract(
            window=pending,
            selected_candidate_key="primary_v2_momentum_60d",
            dataset_identity="future-dataset",
        )


def test_missing_benchmark_is_none_not_zero() -> None:
    assert BenchmarkResult().describe() == {
        "benchmark_symbol": None,
        "benchmark_return": None,
        "top_minus_benchmark": None,
        "valid_benchmark_dates": 0,
    }


def test_model_status_reads_old_artifact_without_optional_benchmark(tmp_path) -> None:
    class RunStore:
        @staticmethod
        def successful_run(_run_id):
            return {
                "artifact": {
                    "provenance": {"created_at": "2026-10-02T15:36:21+08:00"},
                    "evaluation": {
                        "walk_forward": {
                            "oos": {
                                "composite_score_ic": {
                                    "5": {"ic_mean": -0.03, "ic_positive_ratio": 0.4,
                                          "n_dates": 63},
                                    "20": {"ic_mean": -0.01, "ic_positive_ratio": 0.49,
                                           "n_dates": 63},
                                },
                                "composite_score_buckets": {
                                    "5": {"top_bucket_future_return": 0.01,
                                          "bottom_bucket_future_return": 0.0,
                                          "long_short_spread": 0.01},
                                    "20": {"top_bucket_future_return": 0.02,
                                           "bottom_bucket_future_return": 0.01,
                                           "long_short_spread": 0.01},
                                },
                            }
                        }
                    },
                }
            }

    class CensusStore:
        @staticmethod
        def session_dates(_exchange, start=None, end=None):
            return set(_verified_sessions(40))

    response = model_validation_status(
        run_store=RunStore(), census_store=CensusStore(),
        diagnostics_path=tmp_path / "missing.json", today=date(2026, 10, 5),
    )
    five = response["primary_oos"]["horizons"]["5"]
    assert five["top_bucket_return"] == 0.01
    assert five["top_minus_benchmark"] is None
    assert five["valid_benchmark_dates"] == 0
    assert response["v2"]["confirmatory_window"]["status"] == "waiting_for_sessions"
