"""盤後管道 API — 非同步觸發 + 進度跟蹤。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.services.pipeline_jobs import (
    job_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    # 每次輪詢都檢查卡死 job — 前端持續輪詢, 進度停滯超閾值後必定自愈,
    # 無需使用者再次手動點「同步」。
    job_store.reap_stale()
    j = job_store.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """手動取消一個 running 的 job(協作式: 拉取執行緒在當前分塊完成後自行退出)。"""
    j = job_store.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if j["status"] not in ("running", "pending"):
        raise HTTPException(status_code=400, detail=f"job status is {j['status']}, cannot cancel")
    job_store.terminate(job_id, "使用者手動取消")
    return {"cancelled": job_id}


@router.get("/jobs")
def list_jobs(limit: int = 20) -> dict:
    return {
        "active_id": job_store.active_id(),
        "jobs": job_store.list_recent(limit=limit),
    }
