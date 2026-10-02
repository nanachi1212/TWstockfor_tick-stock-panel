"""Strategy Lab query endpoints; all admission and statistics live in the service."""
# ruff: noqa: RUF001
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.taiwan.quant.live_store import LiveLedger
from app.taiwan.selection_review_service import get_selection_review_service
from app.taiwan.strategy_lab import LabFilters, LabOverview, ObservationPage, StrategyLabService

router = APIRouter(prefix="/api/taiwan/strategy-lab", tags=["strategy-lab"])


def service() -> StrategyLabService:
    return StrategyLabService(get_selection_review_service(), LiveLedger())


@router.get("", response_model=LabOverview)
def overview(
    filters: Annotated[LabFilters, Depends()],
    lab: Annotated[StrategyLabService, Depends(service)],
) -> LabOverview:
    try:
        return lab.overview(filters)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "策略觀察來源無法驗證或讀取，已停止統計") from exc


@router.get("/observations", response_model=ObservationPage)
def observations(
    filters: Annotated[LabFilters, Depends()],
    lab: Annotated[StrategyLabService, Depends(service)],
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
) -> ObservationPage:
    try:
        return lab.drilldown(filters, offset, limit)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "策略觀察來源無法驗證或讀取，已停止統計") from exc
