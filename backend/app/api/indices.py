"""指数 API。

Phase 8B-5.1: 删除了指数列表/搜索/日K/分钟K/单独同步标的的只读端点及其前端页面
(Indices.tsx, 已确认零 Taiwan/通用消费者)。仅保留 sync_daily —— 它被
Data.tsx(数据管理页, 明确不在本 phase 范围内)和 app.jobs.daily_pipeline 的
手动排程逻辑实际调用, 删除会破坏这两者。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

from app.services import index_sync
from app.tickflow.capabilities import Cap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/index", tags=["index"])


@router.post("/sync_daily")
def sync_index_daily(
    request: Request,
    days: int = Query(365, ge=30, le=5000),
):
    """同步指数日K到独立 parquet。"""
    repo = request.app.state.repo
    capset = request.app.state.capabilities
    if not capset.has(Cap.KLINE_DAILY_BATCH):
        raise HTTPException(status_code=403, detail="需要 Pro+ 权限 (batch K-line)")
    end = datetime.now()
    start = end - timedelta(days=days)
    count = index_sync.sync_index_instruments(repo)
    rows = index_sync.sync_and_persist_index_daily(repo, capset, start_date=start, end_date=end)
    return {"status": "ok", "index_count": count, "rows_written": rows}
