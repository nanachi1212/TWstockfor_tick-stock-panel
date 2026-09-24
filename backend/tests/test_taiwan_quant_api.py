from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.api import taiwan_quant
from app.taiwan.backfill_worker import WorkerLock
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.data_health import (
    QuantEvaluationStatus,
    evaluate_data_health,
    quant_evaluation_readiness,
)
from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore


def _health(*, classified: int = 100):
    return evaluate_data_health(
        census_sessions=2850,
        census_total_sessions=2850,
        twse_codes_observed=100,
        twse_codes_classified=classified,
    )


def _progress(*, completed: int = 478, pending: int = 0, failed: int = 0):
    return {
        "completed_jobs": completed,
        "pending_jobs": pending,
        "failed_jobs": failed,
        "unique_first_seen_dates": 478,
    }


def _artifact():
    summary = {"ic_mean": 0.12, "ic_std": 0.03, "ic_positive_ratio": 0.7, "n_dates": 80}
    return {
        "provenance": {
            "run_id": "fixture-run-1",
            "created_at": "2026-09-24T15:00:00+08:00",
            "code_sha": "a" * 40,
            "evaluation_spec_version": PRIMARY_OOS_SPEC.version,
            "evaluation_spec_hash": PRIMARY_OOS_SPEC.fingerprint,
            "dataset_identity": "fixture-dataset",
            "latest_market_date": "2026-09-23",
            "a2b_classification_identity": "fixture-classification",
            "random_seed": None,
        },
        "evaluation": {
            "primary_oos_ready": True,
            "claim_scope": "primary_verified_oos",
            "horizons": [5, 20],
            "factor_ic": {"momentum_5d": {"5": summary}},
            "composite_score_ic": {"5": summary, "20": summary},
            "composite_score_buckets": {
                "20": {
                    "top_bucket_future_return": 0.08,
                    "bottom_bucket_future_return": -0.02,
                    "long_short_spread": 0.1,
                    "n_dates": 60,
                },
            },
            "walk_forward": {
                "folds": [{"index": 0, "test_start": "2025-01-01", "test_end": "2025-06-01"}],
                "oos": {"composite_score_ic": {"5": summary}},
            },
        },
    }


def _readiness(health=None, progress=None, worker_status="idle"):
    return quant_evaluation_readiness(
        health or _health(), progress or _progress(), worker_status=worker_status,
    )


def test_readiness_distinguishes_processing_ready_blocked_and_failed():
    health = _health()
    assert quant_evaluation_readiness(
        health, _progress(completed=192, pending=286), worker_status="running",
    ).status is QuantEvaluationStatus.PROCESSING
    assert quant_evaluation_readiness(
        health, _progress(), worker_status="idle",
    ).status is QuantEvaluationStatus.READY
    assert quant_evaluation_readiness(
        _health(classified=70), _progress(), worker_status="idle",
    ).status is QuantEvaluationStatus.BLOCKED
    assert quant_evaluation_readiness(
        health, _progress(completed=477, failed=1), worker_status="running",
    ).status is QuantEvaluationStatus.FAILED


def test_a2b_progress_endpoint_returns_worker_counts_without_oos_evaluation(monkeypatch):
    class Worker:
        lock = SimpleNamespace(owner_status=lambda: "running")

        def status(self, *, start):
            assert start is not None
            return {"classification": {
                "completed_jobs": 232, "pending_jobs": 246, "failed_jobs": 0,
                "unique_first_seen_dates": 478,
            }}

    monkeypatch.setattr(taiwan_quant, "TaiwanHistoricalBackfillWorker", Worker)

    assert taiwan_quant.a2b_progress_status() == {
        "completed": 232, "pending": 246, "failed": 0, "total": 478,
        "worker_status": "running",
    }


def test_processing_snapshot_never_exposes_oos_metrics_even_if_artifact_is_supplied():
    health = _health()
    progress = _progress(completed=192, pending=286)
    readiness = _readiness(health, progress, "running")
    response = taiwan_quant.evaluation_product_response(
        health, progress, readiness, worker_status="running",
        generated_at=datetime.now(TAIPEI), evaluation_artifact=_artifact(),
    )
    assert response["status"] == "processing"
    assert response["evaluation_status"] == "waiting_for_data_health"
    assert response["evaluation"] is None


def test_ready_snapshot_exposes_only_a_verified_primary_artifact():
    health = _health()
    progress = _progress()
    response = taiwan_quant.evaluation_product_response(
        health, progress, _readiness(), worker_status="idle",
        generated_at=datetime.now(TAIPEI), evaluation_artifact=_artifact(),
    )
    assert response["status"] == "ready"
    assert response["evaluation_status"] == "available"
    assert response["evaluation"]["composite_score_ic"]["5"]["ic_mean"] == 0.12
    assert response["evaluation"]["composite_score_buckets"]["20"]["long_short_spread"] == 0.1
    assert response["evaluation_provenance"]["evaluation_spec_version"] == PRIMARY_OOS_SPEC.version
    assert response["ranking_modes"]["live_current"] != response["ranking_modes"]["historical_oos"]


def test_ready_without_report_and_running_states_are_distinct():
    ready = taiwan_quant.evaluation_product_response(
        _health(), _progress(), _readiness(), worker_status="idle",
        generated_at=datetime.now(TAIPEI),
    )
    assert ready["evaluation_status"] == "ready_for_evaluation"
    assert ready["evaluation"] is None

    running = taiwan_quant.evaluation_product_response(
        _health(), _progress(), _readiness(), worker_status="idle",
        generated_at=datetime.now(TAIPEI), evaluation_running=True,
    )
    assert running["evaluation_status"] == "evaluation_running"
    assert running["evaluation"] is None


def test_failed_run_state_does_not_replace_a_success_artifact():
    response = taiwan_quant.evaluation_product_response(
        _health(), _progress(), _readiness(), worker_status="idle",
        generated_at=datetime.now(TAIPEI), evaluation_artifact=_artifact(),
        latest_run_state={"event_type": "failed", "error_code": "RuntimeError"},
    )
    assert response["evaluation_status"] == "available"
    assert response["evaluation"] is not None


def test_append_only_store_reuses_identical_result_and_keeps_failed_event_separate(tmp_path):
    health = _health()
    progress = _progress()
    store = PrimaryOosRunStore(tmp_path / "primary_oos_runs.sqlite3")
    context = {
        "health": health.describe(),
        "a2b": store.progress_snapshot(progress),
        "spec_hash": PRIMARY_OOS_SPEC.fingerprint,
        "dataset_identity": "fixture-dataset",
        "code_sha": "a" * 40,
    }
    artifact = _artifact()
    store.begin(
        run_id="fixture-run-1", identity_key="same-identity", recorded_at="2026-09-24T15:00:00+08:00",
        context=context,
    )
    store.succeed(
        run_id="fixture-run-1", identity_key="same-identity",
        recorded_at="2026-09-24T15:00:01+08:00", context=context, artifact=artifact,
    )
    reused = store.begin(
        run_id="fixture-run-2", identity_key="same-identity", recorded_at="2026-09-24T15:01:00+08:00",
        context=context,
    )
    assert reused is not None and reused["reused"] is True
    assert reused["run_id"] == "fixture-run-1"

    store.fail(
        run_id="fixture-run-3", identity_key="other-identity",
        recorded_at="2026-09-24T15:02:00+08:00", context=context, error_code="RuntimeError",
    )
    assert store.latest_success(
        health=health, progress=progress, spec_hash=PRIMARY_OOS_SPEC.fingerprint,
    )["evaluation"]["primary_oos_ready"] is True
    assert store.latest_state(
        health=health, progress=progress, spec_hash=PRIMARY_OOS_SPEC.fingerprint,
    )["event_type"] == "failed"

    import sqlite3

    with sqlite3.connect(store.path) as db:
        try:
            db.execute("UPDATE run_events SET event_type='failed'")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("run events must be immutable")


def test_worker_lock_status_is_read_only_and_distinguishes_running(tmp_path):
    lock = WorkerLock(tmp_path / "backfill.lock")
    assert lock.owner_status() == "idle"
    with lock:
        assert lock.owner_status() == "running"
    assert lock.owner_status() == "idle"


def test_endpoint_returns_processing_status_from_preflight(monkeypatch, tmp_path):
    progress = _progress(completed=220, pending=258)

    class FakeLock:
        @staticmethod
        def owner_status():
            return "running"

    class FakeStore:
        path = tmp_path / "primary_oos_runs.sqlite3"

        @staticmethod
        def latest_success(**kwargs):
            raise AssertionError("unready states must not read historical OOS output")

        @staticmethod
        def latest_state(**kwargs):
            raise AssertionError("unready states must not read run status")

    class FakePreflight:
        data_health = _health()
        a2b_progress = progress
        a2b_worker_status = "running"
        readiness = quant_evaluation_readiness(data_health, progress, worker_status="running")

    monkeypatch.setattr(taiwan_quant, "read_primary_oos_preflight", lambda: FakePreflight())
    monkeypatch.setattr(taiwan_quant, "PrimaryOosRunStore", FakeStore)
    monkeypatch.setattr(taiwan_quant, "WorkerLock", lambda path: FakeLock())
    response = taiwan_quant.quant_evaluation_status()
    assert response["status"] == "processing"
    assert response["a2b"] == {
        "completed": 220, "pending": 258, "failed": 0, "total": 478,
        "worker_status": "running",
    }
    assert response["evaluation"] is None
