"""Data health transport; aggregation and safe actions belong to domain services."""

# ruff: noqa: RUF001
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.taiwan.data_health_center import HealthAction, HealthReport
from app.taiwan.data_health_jobs import HealthJob, get_health_job_manager

router = APIRouter(prefix="/api/taiwan/data-health", tags=["data-health"])


class HealthActionRequest(BaseModel):
    action: HealthAction


@router.get("", response_model=HealthReport)
def data_health() -> HealthReport:
    return get_health_job_manager().snapshot()


@router.get("/jobs", response_model=list[HealthJob])
def jobs() -> list[HealthJob]:
    return get_health_job_manager().list_jobs()


@router.get("/jobs/{job_id}", response_model=HealthJob)
def job(job_id: str) -> HealthJob:
    result = get_health_job_manager().get(job_id)
    if result is None:
        raise HTTPException(404, "找不到資料健康任務，服務重啟後記錄會清除")
    return result


@router.post("/{dataset}/actions", response_model=HealthJob, status_code=202)
def start_action(dataset: str, payload: HealthActionRequest) -> HealthJob:
    try:
        return get_health_job_manager().start(dataset, payload.action)
    except KeyError as exc:
        raise HTTPException(404, "找不到資料集") from exc
    except ValueError as exc:
        raise HTTPException(409, "此資料集沒有安全的自動修復入口") from exc
