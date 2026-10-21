"""Thin routes for current Taiwan market research."""
from datetime import date
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query

from app.taiwan.industry_intelligence import (
    IndustryRotationSnapshot,
    TaiwanIndustryIntelligenceService,
)
from app.taiwan.institutional_statistics import (
    InstitutionalFlowSeries,
    InstitutionalStatisticsSnapshot,
    InstitutionalWindow,
    TaiwanInstitutionalStatisticsService,
)
from app.taiwan.market_heatmap import TaiwanMarketHeatmapService

router = APIRouter()


@router.get("/institutional-statistics", response_model=InstitutionalStatisticsSnapshot)
def institutional_statistics(
    target_date: Annotated[date | None, Query(alias="date")] = None, window: int = 5,
) -> InstitutionalStatisticsSnapshot:
    if window not in (5, 10, 20, 45, 60):
        raise HTTPException(status_code=422, detail="window must be one of 5, 10, 20, 45, 60")
    return TaiwanInstitutionalStatisticsService().get_snapshot(target_date, cast(InstitutionalWindow, window))


@router.get("/institutional-flow-series", response_model=InstitutionalFlowSeries)
def institutional_flow_series(target_date: Annotated[date | None, Query(alias="date")] = None) -> InstitutionalFlowSeries:
    return TaiwanInstitutionalStatisticsService().get_market_flow_series(target_date)


@router.get("/industry-rotation", response_model=IndustryRotationSnapshot)
def industry_rotation(target_date: Annotated[date | None, Query(alias="date")] = None) -> IndustryRotationSnapshot:
    return TaiwanIndustryIntelligenceService().get_rotation(target_date)


@router.get("/market-heatmap")
def market_heatmap(target_date: Annotated[date | None, Query(alias="date")] = None) -> dict:
    return TaiwanMarketHeatmapService().snapshot(target_date)


@router.get("/chip-ranking")
def chip_ranking(target_date: Annotated[str | None, Query(alias="date")] = None) -> dict:
    """Latest stored session only; see chip_ranking module docstring."""
    from app.taiwan.chip_ranking import TaiwanChipRankingService

    if target_date is not None:
        # Reject old bookmarks instead of returning today's ranking under a past date.
        raise HTTPException(status_code=422, detail="籌碼排行只提供最新已入庫交易日 不支援 date 參數")

    return TaiwanChipRankingService().snapshot()


@router.get("/big-holder-ranking")
def big_holder_ranking(target_date: Annotated[str | None, Query(alias="date")] = None) -> dict:
    """Latest stored TDCC week only; see big_holder_ranking module docstring."""
    from app.taiwan.big_holder_ranking import TaiwanBigHolderRankingService

    if target_date is not None:
        raise HTTPException(status_code=422, detail="大戶集中度只提供最新已入庫集保週 不支援 date 參數")

    return TaiwanBigHolderRankingService().snapshot()


@router.get("/chip-ranking/tracking")
def chip_ranking_tracking() -> dict:
    """Forward-only outcomes of frozen daily rankings; see chip_ranking_tracking."""
    from app.taiwan.chip_ranking_tracking import ChipRankingTracker

    return ChipRankingTracker().report()
