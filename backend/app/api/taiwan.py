"""Taiwan Market Domain Specific Endpoints.

Covers:
  - GET /api/taiwan/stocks/{symbol}: Unified Taiwan Stock Research Workspace Detail API.
"""
# ruff: noqa: RUF001, RUF002 -- user-facing Traditional Chinese API messages.
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.taiwan.current_data import (
    TaiwanCurrentDataResponse,
    TaiwanDatasetCapability,
    capability_matrix,
    get_taiwan_current_data_service,
)
from app.taiwan.daily_update import FreshnessStatus, TaiwanDailyUpdateService
from app.taiwan.detail_models import TaiwanStockDetailResponse
from app.taiwan.detail_service import get_taiwan_stock_detail_service
from app.taiwan.industry_intelligence import (
    TaiwanIndustryIntelligenceService,
    TaiwanIndustryIntelligenceSnapshot,
)
from app.taiwan.market_intelligence import (
    TaiwanMarketIntelligenceService,
    TaiwanMarketIntelligenceSnapshot,
)
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerResponse
from app.taiwan.screener_nl import (
    TaiwanScreenerTranslateQuery,
    TaiwanScreenerTranslation,
    TaiwanScreenerTranslator,
)
from app.taiwan.symbol import parse_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taiwan", tags=["taiwan"])


@router.get("/data-status", response_model=FreshnessStatus)
def get_taiwan_data_status():
    """獲取台股市場三大本地數據集 (日線、三大法人、融資券) 之最新落盤日期與市場時效狀況。"""
    svc = TaiwanDailyUpdateService()
    return svc.get_freshness()


@router.get("/capabilities", response_model=list[TaiwanDatasetCapability])
def get_taiwan_data_capabilities():
    """Expose product usage boundaries without fetching network data."""
    return capability_matrix()


@router.get("/data/{symbol}", response_model=TaiwanCurrentDataResponse)
def get_taiwan_current_data(
    symbol: str,
):
    """Return isolated current/reference sections for one Taiwan security."""
    try:
        parse_symbol(symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid Taiwan symbol: {symbol}") from exc
    try:
        return get_taiwan_current_data_service().get_current_data(symbol)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


@router.post("/screener/translate", response_model=TaiwanScreenerTranslation)
async def translate_taiwan_screener_query(payload: TaiwanScreenerTranslateQuery):
    """將自然語言選股描述轉換為強型別 TaiwanScreenerRequest 條件 (純翻譯層，不直出股票)。"""
    translator = TaiwanScreenerTranslator()
    return await translator.translate(payload.query)


@router.get("/market-intelligence", response_model=TaiwanMarketIntelligenceSnapshot)
def get_taiwan_market_intelligence(
    date: str | None = Query(None, description="指定交易日 (YYYY-MM-DD)，預設為最新已完成交易日"),
):
    """取得台股全市場量化統計快照 (包含漲跌家數、漲跌停家數、成交量能、三大法人與資券總計，純本地確定性計算)。"""
    from datetime import date as dt_date
    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanMarketIntelligenceService()
    try:
        return svc.get_snapshot(target_date=target_dt)
    except Exception as e:
        logger.exception("Failed to compute Taiwan market intelligence snapshot: %s", e)
        raise HTTPException(status_code=500, detail=f"市場情報快照生成失敗: {e}") from e


@router.get("/industry-intelligence", response_model=TaiwanIndustryIntelligenceSnapshot)
def get_taiwan_industry_intelligence(
    date: str | None = Query(None, description="指定交易日 (YYYY-MM-DD)，預設為最新已完成交易日"),
    sort_by: str = Query("turnover", description="排序欄位: turnover, median_change_pct, relative_strength_5d, relative_strength_20d, advance_ratio, foreign_net, investment_trust_net"),
    order: str = Query("desc", description="排序順序: desc 或 asc"),
):
    """取得台股各產業類股量化統計快照 (包含類股漲跌中位數、5D/20D 相對強弱 RS、成交占比與三大法人動向，純本地確定性計算)。"""
    from datetime import date as dt_date
    target_dt = None
    if date:
        try:
            target_dt = dt_date.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"無效的日期格式: {date}，請使用 YYYY-MM-DD") from e

    svc = TaiwanIndustryIntelligenceService()
    try:
        return svc.get_snapshot(target_date=target_dt, sort_by=sort_by, order=order)
    except Exception as e:
        logger.exception("Failed to compute Taiwan industry intelligence snapshot: %s", e)
        raise HTTPException(status_code=500, detail=f"產業情報快照生成失敗: {e}") from e

