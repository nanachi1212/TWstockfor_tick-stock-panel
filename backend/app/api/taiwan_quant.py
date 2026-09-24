"""Read-only product status for historical Taiwan Quant evaluation."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter

from app.taiwan.backfill_worker import CENSUS_START, TaiwanHistoricalBackfillWorker
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quant.data_health import (
    DataHealth,
    QuantEvaluationReadiness,
    QuantEvaluationStatus,
    ReadinessLevel,
    health_from_stores,
    quant_evaluation_readiness,
)
from app.taiwan.quant.evaluation_store import QuantEvaluationReportStore

router = APIRouter(prefix="/api/taiwan/quant", tags=["taiwan-quant"])


def evaluation_product_response(
    health: DataHealth,
    progress: dict[str, int],
    readiness: QuantEvaluationReadiness,
    *,
    worker_status: str,
    generated_at: datetime,
    evaluation_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project existing readiness and evaluation output into the read-only API."""
    primary_ready = health.is_ready(ReadinessLevel.PRIMARY_OOS)
    report = None
    evaluation_timestamp = None
    evaluation_status = "waiting_for_data_health"
    reasons = list(readiness.blocking_reasons)
    if readiness.status is QuantEvaluationStatus.READY:
        evaluation_status = "waiting_for_report"
        reasons = ["Historical evaluation has not been materialized yet"]
        if (
            evaluation_report is not None
            and evaluation_report.get("primary_oos_ready") is True
            and evaluation_report.get("claim_scope") == "primary_verified_oos"
        ):
            report = {
                key: evaluation_report[key]
                for key in (
                    "horizons", "factor_ic", "composite_score_ic",
                    "composite_score_buckets", "walk_forward",
                )
                if key in evaluation_report
            }
            evaluation_timestamp = evaluation_report.get("generated_at")
            evaluation_status = "available"
            reasons = []

    return {
        "status": readiness.status.value,
        "generated_at": generated_at.isoformat(),
        "evaluation_status": evaluation_status,
        "evaluation_timestamp": evaluation_timestamp,
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
    worker = TaiwanHistoricalBackfillWorker()
    progress_snapshot = worker.status(start=CENSUS_START)
    progress = progress_snapshot["classification"]
    end = date.fromisoformat(progress_snapshot["end_date"])
    health = health_from_stores(
        worker.census_store,
        worker.classification_store,
        start=CENSUS_START,
        end=end,
    )
    worker_status = worker.lock.owner_status()
    readiness = quant_evaluation_readiness(
        health, progress, worker_status=worker_status,
    )
    evaluation_report = QuantEvaluationReportStore().read(
        health=health, progress=progress, worker_status=worker_status,
    )
    return evaluation_product_response(
        health,
        progress,
        readiness,
        worker_status=worker_status,
        generated_at=datetime.now(TAIPEI),
        evaluation_report=evaluation_report,
    )
