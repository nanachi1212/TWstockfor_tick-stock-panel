"""财务数据 API — 独立路由, Cap.FINANCIAL 门控。

Phase 8B-5.3: A 股财务分析产品(状态/利润表/资产负债表/现金流量表/历史股本/
AI 财务分析/报告 CRUD)已整体下线。仅保留 /metrics —— 它被 StockPanel /
StockInfoBar 的信息条「财务」字段组 (EPS/BPS/ROE/PE/PB 等) 复用, 与已下线的
财务分析页面无关, 不能一并删除。
"""
from __future__ import annotations

import logging

import polars as pl
from fastapi import APIRouter, Request

from app.services.financial_sync import get_financial_df
from app.tickflow.capabilities import Cap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/financials", tags=["financials"])


def _financial_allowed(capset) -> bool:
    """是否有财务数据访问权限 (TickFlow FINANCIAL 套餐 或 custom 财务源)。"""
    if capset.has(Cap.FINANCIAL):
        return True
    from app.services.financial_sync import _financial_is_custom
    return _financial_is_custom()


def _require_financial(capset) -> None:
    """_require_financial(capset) 的 custom 感知版本。"""
    if not _financial_allowed(capset):
        from app.tickflow.capabilities import CapabilityDenied
        raise CapabilityDenied(Cap.FINANCIAL)


@router.get("/metrics")
def get_metrics(request: Request, symbol: str | None = None):
    """查询核心财务指标。"""
    capset = request.app.state.capabilities
    _require_financial(capset)

    df = get_financial_df(request.app.state.repo.store.data_dir, "metrics")
    if df.is_empty():
        return {"data": []}
    if symbol:
        df = df.filter(pl.col("symbol") == symbol)
    return {"data": df.to_dicts()}
