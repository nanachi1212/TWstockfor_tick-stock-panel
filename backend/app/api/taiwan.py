"""Taiwan Market Domain Specific Endpoints.

Covers:
  - GET /api/taiwan/stocks/{symbol}: Unified Taiwan Stock Research Workspace Detail API.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Query, Request

from app.taiwan.detail_models import TaiwanStockDetailResponse
from app.taiwan.detail_service import get_taiwan_stock_detail_service
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerResponse
from app.taiwan.symbol import parse_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taiwan", tags=["taiwan"])


@router.get("/stocks/{symbol}", response_model=TaiwanStockDetailResponse)
def get_taiwan_stock_detail(
    symbol: str,
    days: int = Query(120, ge=10, le=1000, description="歷史日 K 線根數"),
):
    """獲取單一台股標的完整研究工作台資料 (一站式聚合，不因單一來源失敗中斷)。"""
    try:
        # Validate symbol format (e.g. 2330.TWSE, 8069.TPEX)
        parse_symbol(symbol)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"無效的台股代碼格式: {symbol}。請使用標準規範代碼，例如 2330.TWSE, 8069.TPEX",
        ) from e

    svc = get_taiwan_stock_detail_service()
    try:
        return svc.get_stock_detail(symbol, days=days)
    except Exception as e:
        logger.exception("Failed to aggregate Taiwan stock detail for %s: %s", symbol, e)
        raise HTTPException(
            status_code=500,
            detail=f"台股個股資訊聚合失敗: {e}",
        ) from e


@router.post("/screener/run", response_model=TaiwanScreenerResponse)
def run_taiwan_screener(request: TaiwanScreenerRequest):
    """執行台股批次選股 (基於本地 Parquet 持久化資料庫與 Security Master)。"""
    from app.taiwan.screener import TaiwanScreenerService
    svc = TaiwanScreenerService()
    try:
        return svc.run(request)
    except Exception as e:
        logger.exception("Taiwan screener execution failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"台股選股執行失敗: {e}",
        ) from e
