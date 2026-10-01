"""Thin endpoints for the Market Research breadth/valuation tab component."""
from datetime import date

import polars as pl
from fastapi import APIRouter, HTTPException, Query

from app.taiwan.market_breadth_service import (
    BreadthValuationResponse,
    Market,
    MarketBreadthValuationService,
    fundamental_store,
)
from app.taiwan.market_valuation import refresh_valuation

router = APIRouter()


@router.get("/market-research/breadth-valuation", response_model=BreadthValuationResponse)
def get_breadth_valuation(as_of: date | None = None, market: Market = "composite",
                         days: int = Query(default=20, ge=1, le=60)) -> BreadthValuationResponse:
    try:
        return MarketBreadthValuationService().snapshot(as_of, market, days)
    except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
        raise HTTPException(503, "Market research source data unavailable") from exc


@router.post("/market-research/breadth-valuation/refresh")
def refresh_breadth_valuation() -> dict[str, object]:
    try:
        return refresh_valuation(fundamental_store())
    except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
        raise HTTPException(503, "Market valuation persistence unavailable") from exc
