"""Fail-closed orchestration for optional market-context providers."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.taiwan.providers.external_models import ExternalProviderResult, ExternalStatus
from app.taiwan.providers.fx_context import get_frankfurter_fx_provider
from app.taiwan.realtime.fugle_provider import get_fugle_aggregates_provider


class ExternalContextResponse(BaseModel):
    intraday_context: ExternalProviderResult
    fx_context: ExternalProviderResult


def intraday_context(symbol: str | None = None) -> ExternalProviderResult:
    provider = get_fugle_aggregates_provider()
    if not provider.enabled:
        return ExternalProviderResult.unavailable(
            "fugle_marketdata:websocket:aggregates", "config_missing",
        )
    if symbol:
        provider.request_symbols([symbol])
        observation = provider.observe(symbol)
        snapshot = observation.snapshot
        data: dict[str, Any] | None = None
        if snapshot is not None:
            data = {
                "symbol": snapshot.symbol,
                "last_price": snapshot.last_price,
                "trade_volume": snapshot.trade_volume,
                "trade_value": snapshot.trade_value,
                "trade_volume_at_bid": snapshot.trade_volume_at_bid,
                "trade_volume_at_ask": snapshot.trade_volume_at_ask,
                "bids": snapshot.bids,
                "asks": snapshot.asks,
            }
        status: ExternalStatus
        if observation.status == "available":
            status = "available"
        elif observation.status == "stale":
            status = "stale"
        else:
            status = "unavailable"
        return ExternalProviderResult(
            source="fugle_marketdata:websocket:aggregates",
            status=status,
            as_of=snapshot.observed_at.isoformat() if snapshot else None,
            freshness="realtime" if status == "available" else status,
            data=data,
            error_reason=None if status == "available" else observation.status,
        )
    return ExternalProviderResult.unavailable(
        "fugle_marketdata:websocket:aggregates", "symbol_required",
    )


class ExternalContextService:
    def get(self, symbol: str | None = None) -> ExternalContextResponse:
        try:
            intraday = intraday_context(symbol)
        except Exception:
            intraday = ExternalProviderResult.unavailable(
                "fugle_marketdata:websocket:aggregates", "provider_unavailable",
            )
        try:
            fx = get_frankfurter_fx_provider().get_context()
        except Exception:
            fx = ExternalProviderResult.unavailable(
                "frankfurter:v2:provider:CBC", "provider_unavailable",
            )
        return ExternalContextResponse(intraday_context=intraday, fx_context=fx)
