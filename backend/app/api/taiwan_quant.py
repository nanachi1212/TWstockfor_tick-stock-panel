"""Read-only product status for historical Taiwan Quant evaluation."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from app.taiwan.backfill_worker import CENSUS_START, TaiwanHistoricalBackfillWorker, WorkerLock
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.data_health import (
    DataHealth,
    QuantEvaluationReadiness,
    QuantEvaluationStatus,
    ReadinessLevel,
)
from app.taiwan.quant.evaluation_spec import PRIMARY_OOS_SPEC
from app.taiwan.quant.evaluation_store import PrimaryOosRunStore
from app.taiwan.quant.primary_oos_runner import read_primary_oos_preflight

router = APIRouter(prefix="/api/taiwan/quant", tags=["taiwan-quant"])


@router.get("/a2b-status")
def a2b_progress_status() -> dict[str, Any]:
    """Small read-only A2b progress projection, independent of OOS data-health scans."""
    worker = TaiwanHistoricalBackfillWorker()
    classification = worker.status(start=CENSUS_START)["classification"]
    return {
        "completed": classification["completed_jobs"],
        "pending": classification["pending_jobs"],
        "failed": classification["failed_jobs"],
        "total": classification["unique_first_seen_dates"],
        "worker_status": worker.lock.owner_status(),
    }


def evaluation_product_response(
    health: DataHealth,
    progress: dict[str, int],
    readiness: QuantEvaluationReadiness,
    *,
    worker_status: str,
    generated_at: datetime,
    evaluation_artifact: Mapping[str, Any] | None = None,
    evaluation_running: bool = False,
    latest_run_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project existing readiness and evaluation output into the read-only API."""
    primary_ready = health.is_ready(ReadinessLevel.PRIMARY_OOS)
    report = None
    evaluation_timestamp = None
    evaluation_status = "waiting_for_data_health"
    reasons = list(readiness.blocking_reasons)
    provenance = None
    if readiness.status is QuantEvaluationStatus.READY:
        evaluation_status = "ready_for_evaluation"
        reasons = []
        if evaluation_running:
            evaluation_status = "evaluation_running"
        elif (
            latest_run_state is not None
            and latest_run_state.get("event_type") == "failed"
            and evaluation_artifact is None
        ):
            evaluation_status = "failed"
            reasons = [f"Primary OOS run failed ({latest_run_state.get('error_code', 'unknown')})"]
        elif (
            latest_run_state is not None
            and latest_run_state.get("event_type") == "started"
            and evaluation_artifact is None
        ):
            evaluation_status = "failed"
            reasons = ["A previous Primary OOS run ended before publishing a result"]
        if (
            not evaluation_running
            and evaluation_artifact is not None
            and isinstance(evaluation_artifact.get("evaluation"), Mapping)
            and evaluation_artifact["evaluation"].get("primary_oos_ready") is True
            and evaluation_artifact["evaluation"].get("claim_scope") == "primary_verified_oos"
        ):
            evaluation_report = evaluation_artifact["evaluation"]
            report = {
                key: evaluation_report[key]
                for key in (
                    "horizons", "factor_ic", "composite_score_ic",
                    "composite_score_buckets", "walk_forward",
                )
                if key in evaluation_report
            }
            provenance = evaluation_artifact.get("provenance")
            evaluation_timestamp = (
                provenance.get("created_at") if isinstance(provenance, Mapping) else None
            )
            evaluation_status = "available"
            reasons = []

    return {
        "status": readiness.status.value,
        "generated_at": generated_at.isoformat(),
        "evaluation_status": evaluation_status,
        "evaluation_timestamp": evaluation_timestamp,
        "evaluation_provenance": provenance,
        "available_horizons": [5, 20],
        "data_health": {
            **health.describe(),
            "status": "ready" if primary_ready else "blocked",
            "primary_oos_ready": primary_ready,
        },
        "a2b": {
            "completed": progress["completed_jobs"],
            "pending": progress["pending_jobs"],
            "failed": progress["failed_jobs"],
            "total": progress["unique_first_seen_dates"],
            "worker_status": worker_status,
        },
        "evaluation": report,
        "blocking_reasons": reasons,
        "ranking_modes": {
            "live_current": "/api/taiwan/quant/live/models",
            "historical_oos": "/api/taiwan/quant/evaluation",
        },
    }


@router.get("/evaluation")
def quant_evaluation_status() -> dict[str, Any]:
    """Return A2b/DataHealth readiness; never calculate or invent OOS metrics."""
    preflight = read_primary_oos_preflight()
    health = preflight.data_health
    progress = preflight.a2b_progress
    worker_status = preflight.a2b_worker_status
    readiness = preflight.readiness
    store = PrimaryOosRunStore()
    evaluation_running = WorkerLock(
        store.path.with_name(".primary_oos.lock"),
    ).owner_status() == "running"
    evaluation_artifact = None
    latest_run_state = None
    if readiness.status is QuantEvaluationStatus.READY:
        evaluation_artifact = store.latest_success(
            health=health, progress=progress, spec_hash=PRIMARY_OOS_SPEC.fingerprint,
        )
        latest_run_state = store.latest_state(
            health=health, progress=progress, spec_hash=PRIMARY_OOS_SPEC.fingerprint,
        )
    return evaluation_product_response(
        health,
        progress,
        readiness,
        worker_status=worker_status,
        generated_at=datetime.now(TAIPEI),
        evaluation_artifact=evaluation_artifact,
        evaluation_running=evaluation_running,
        latest_run_state=latest_run_state,
    )
