from __future__ import annotations

from datetime import datetime

from app.api import taiwan_quant
from app.taiwan.backfill_worker import WorkerLock
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.data_health import (
    QuantEvaluationStatus,
    evaluate_data_health,
    quant_evaluation_readiness,
)
from app.taiwan.quant.evaluation_store import QuantEvaluationReportStore


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


def _report():
    summary = {"ic_mean": 0.12, "ic_std": 0.03, "ic_positive_ratio": 0.7, "n_dates": 80}
    return {
        "primary_oos_ready": True,
        "claim_scope": "primary_verified_oos",
        "generated_at": "2026-09-24T15:00:00+08:00",
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
        "walk_forward": {"folds": [{"index": 0, "test_start": "2025-01-01", "test_end": "2025-06-01"}],
                         "oos": {"composite_score_ic": {"5": summary}}},
    }


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


def test_processing_snapshot_never_exposes_oos_metrics_even_if_report_is_supplied():
    health = _health()
    progress = _progress(completed=192, pending=286)
    readiness = quant_evaluation_readiness(health, progress, worker_status="running")
    response = taiwan_quant.evaluation_product_response(
        health, progress, readiness, worker_status="running",
        generated_at=datetime.now(TAIPEI), evaluation_report=_report(),
    )
    assert response["status"] == "processing"
    assert response["evaluation_status"] == "waiting_for_data_health"
    assert response["evaluation"] is None


def test_ready_snapshot_exposes_only_a_verified_primary_report():
    health = _health()
    progress = _progress()
    readiness = quant_evaluation_readiness(health, progress, worker_status="idle")
    response = taiwan_quant.evaluation_product_response(
        health, progress, readiness, worker_status="idle",
        generated_at=datetime.now(TAIPEI), evaluation_report=_report(),
    )
    assert response["status"] == "ready"
    assert response["evaluation_status"] == "available"
    assert response["evaluation"]["composite_score_ic"]["5"]["ic_mean"] == 0.12
    assert response["evaluation"]["composite_score_buckets"]["20"]["long_short_spread"] == 0.1
    assert response["ranking_modes"]["live_current"] != response["ranking_modes"]["historical_oos"]


def test_ready_data_without_report_does_not_claim_evaluation_is_available():
    health = _health()
    progress = _progress()
    readiness = quant_evaluation_readiness(health, progress, worker_status="idle")
    response = taiwan_quant.evaluation_product_response(
        health, progress, readiness, worker_status="idle",
        generated_at=datetime.now(TAIPEI),
    )
    assert response["status"] == "ready"
    assert response["evaluation_status"] == "waiting_for_report"
    assert response["evaluation"] is None


def test_report_store_requires_readiness_and_invalidates_changed_health(tmp_path):
    health = _health()
    progress = _progress()
    store = QuantEvaluationReportStore(tmp_path / "primary_oos_report.json")
    store.save(_report(), health=health, progress=progress, worker_status="idle")
    assert store.read(health=health, progress=progress, worker_status="idle") == _report()
    changed_progress = _progress(completed=477, pending=1)
    assert store.read(health=health, progress=changed_progress, worker_status="running") is None

    processing_progress = _progress(completed=477, pending=1)
    try:
        store.save(_report(), health=health, progress=processing_progress, worker_status="running")
    except ValueError as exc:
        assert "before readiness is ready" in str(exc)
    else:
        raise AssertionError("report was persisted while A2b remained in progress")


def test_worker_lock_status_is_read_only_and_distinguishes_running(tmp_path):
    lock = WorkerLock(tmp_path / "backfill.lock")
    assert lock.owner_status() == "idle"
    with lock:
        assert lock.owner_status() == "running"
    assert lock.owner_status() == "idle"


def test_endpoint_returns_processing_status_from_read_only_sources(monkeypatch):
    progress = _progress(completed=194, pending=284)

    class FakeLock:
        @staticmethod
        def owner_status():
            return "running"

    class FakeWorker:
        census_store = object()
        classification_store = object()
        lock = FakeLock()

        @staticmethod
        def status(*, start):
            return {"end_date": "2026-09-23", "classification": progress}

    monkeypatch.setattr(taiwan_quant, "TaiwanHistoricalBackfillWorker", FakeWorker)
    monkeypatch.setattr(taiwan_quant, "health_from_stores", lambda *args, **kwargs: _health())
    response = taiwan_quant.quant_evaluation_status()
    assert response["status"] == "processing"
    assert response["a2b"] == {
        "completed": 194, "pending": 284, "failed": 0, "total": 478,
        "worker_status": "running",
    }
    assert response["evaluation"] is None
