"""Thin routes for current Taiwan market research."""
from datetime import date
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query

from app.taiwan.industry_intelligence import (
    IndustryRotationSnapshot,
    TaiwanIndustryIntelligenceService,
)
from app.taiwan.institutional_statistics import (
    InstitutionalStatisticsSnapshot,
    InstitutionalWindow,
    TaiwanInstitutionalStatisticsService,
)

router = APIRouter()


@router.get("/institutional-statistics", response_model=InstitutionalStatisticsSnapshot)
def institutional_statistics(
    target_date: Annotated[date | None, Query(alias="date")] = None, window: int = 5,
) -> InstitutionalStatisticsSnapshot:
    if window not in (5, 10, 20, 45, 60):
        raise HTTPException(status_code=422, detail="window must be one of 5, 10, 20, 45, 60")
    return TaiwanInstitutionalStatisticsService().get_snapshot(target_date, cast(InstitutionalWindow, window))


@router.get("/industry-rotation", response_model=IndustryRotationSnapshot)
def industry_rotation(target_date: Annotated[date | None, Query(alias="date")] = None) -> IndustryRotationSnapshot:
    return TaiwanIndustryIntelligenceService().get_rotation(target_date)
