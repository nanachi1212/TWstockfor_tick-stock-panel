"""Shared deterministic buy-point assembly for API and AI advice consumers."""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.taiwan.buy_point import (
    BuyPointMarketData,
    BuyPointSignal,
    BuyPointStrategy,
    BuyPointStrategyStore,
    evaluate_buy_point,
    strategy_catalog,
)
from app.taiwan.quant.live_contract import canonical_hash
from app.taiwan.trade_plan import TradePlan, build_trade_plan

logger = logging.getLogger(__name__)


class BuyPointCandidate(BaseModel):
    """Frozen server-built signal and optional executable plan."""

    strategy: BuyPointStrategy
    strategy_definition_digest: str
    signal: BuyPointSignal
    trade_plan: TradePlan | None = None
    plan_unavailable_reason: str | None = None


def strategy_definition_digest(strategy: BuyPointStrategy) -> str:
    """Hash only behavior-affecting fields, never generated catalogue timestamps."""
    return canonical_hash(
        {
            "id": strategy.id,
            "source_preset_id": strategy.source_preset_id,
            "enabled": strategy.enabled,
            "conditions": strategy.conditions.model_dump(mode="json"),
            "risk_filters": strategy.risk_filters.model_dump(mode="json"),
            "alert_channels": strategy.alert_channels,
        }
    )


def catalog_map(store: BuyPointStrategyStore) -> dict[str, BuyPointStrategy]:
    return {item.id: item for item in strategy_catalog(store)}


def daily_data(symbol: str) -> tuple[list[dict[str, Any]], str | None]:
    from app.taiwan.daily_store import TaiwanDailyStore

    frame = TaiwanDailyStore().read_all([symbol])
    if frame.is_empty():
        return [], None
    frame = frame.sort("date").tail(90)
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        item = dict(row)
        item["date"] = str(item.get("date"))[:10]
        rows.append(item)
    return rows, rows[-1].get("date") if rows else None


def build_market_data(
    symbol: str,
    screen: Any = None,
    item: Any = None,
    event_service: Any = None,
    events: list[Any] | None = None,
    risk_source_status: str | None = None,
    eligible_date: str | None = None,
    *,
    load_screen: bool = True,
    daily_loader: Any = daily_data,
) -> BuyPointMarketData:
    """Build a local snapshot; missing enrichment stays explicitly unavailable."""
    rows, data_as_of = daily_loader(symbol)
    if load_screen and screen is None:
        try:
            from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService

            screen = TaiwanScreenerService().run(
                TaiwanScreenerRequest(page_size=1, sort_by="symbol", symbol_scope=[symbol])
            )
            item = next((candidate for candidate in screen.items if candidate.symbol == symbol), None)
        except Exception as exc:
            logger.debug(
                "buy-point screener enrichment unavailable for %s: %s",
                symbol,
                type(exc).__name__,
            )

    last = rows[-1] if rows else {}
    prior_volume_rows = rows[-21:-1]
    volumes = [float(row["volume"]) for row in prior_volume_rows if row.get("volume") is not None]
    volume_average = sum(volumes) / len(volumes) if len(volumes) == 20 else None
    try:
        parsed_price = float(last["close"]) if last.get("close") is not None else None
    except (TypeError, ValueError):
        parsed_price = None
    price = parsed_price if parsed_price is not None and math.isfinite(parsed_price) else None
    ma20 = getattr(item, "ma20", None)
    price_extension = (
        (price - float(ma20)) / float(ma20) * 100
        if price is not None and ma20 is not None and float(ma20) > 0
        else None
    )
    foreign_net = getattr(item, "foreign_net", None)
    foreign_net_5d = getattr(item, "foreign_net_5d", None)
    trust_net = getattr(item, "investment_trust_net", None)
    trust_net_5d = getattr(item, "investment_trust_net_5d", None)
    institutional_day = (
        sum(float(value) for value in (foreign_net, trust_net) if value is not None)
        if foreign_net is not None or trust_net is not None
        else None
    )
    institutional_5d_average = (
        sum(float(value) for value in (foreign_net_5d, trust_net_5d) if value is not None) / 5
        if foreign_net_5d is not None or trust_net_5d is not None
        else None
    )

    matching_events: list[Any] = []
    risk: dict[str, Any] | None = None
    try:
        reference_date = date.fromisoformat(data_as_of) if data_as_of else None
    except ValueError:
        reference_date = None
    if events is not None and reference_date is not None:
        clean_symbol = symbol.strip().upper()
        clean_code = clean_symbol.split(".", 1)[0]
        matching_events = [
            event
            for event in events
            if (
                str(getattr(event, "symbol", "")).upper() == clean_symbol
                or str(getattr(event, "code", "")).upper() == clean_code
            )
            and str(getattr(event, "event_date", "")) <= reference_date.isoformat()
        ]
    if risk_source_status == "available" and event_service is not None and reference_date is not None:
        try:
            risk = event_service.check_symbol_risk_status(
                symbol,
                target_date=reference_date,
                events=events or [],
            )
        except Exception as exc:
            logger.debug(
                "buy-point risk evaluation unavailable for %s: %s",
                symbol,
                type(exc).__name__,
            )
    recent_cutoff = reference_date - timedelta(days=30) if reference_date is not None else None
    positive_event = (
        any(
            getattr(event, "severity", None) == "info"
            and recent_cutoff.isoformat() <= str(getattr(event, "event_date", ""))
            for event in matching_events
        )
        if risk_source_status == "available" and recent_cutoff is not None
        else None
    )
    capital_reduction = (
        any(
            getattr(event, "event_type", None) == "capital_reduction"
            and recent_cutoff.isoformat() <= str(getattr(event, "event_date", ""))
            for event in matching_events
        )
        if risk_source_status == "available" and recent_cutoff is not None
        else None
    )
    return BuyPointMarketData(
        symbol=symbol,
        name=getattr(item, "name", "") or "",
        data_as_of=data_as_of,
        freshness=(
            "daily_cached"
            if rows and data_as_of == eligible_date and price is not None
            else "stale"
            if rows and eligible_date is not None and data_as_of != eligible_date
            else "unavailable"
        ),
        price=price,
        quant_score=getattr(item, "quant_score", None),
        quant_universe=True if item is not None else None,
        daily=rows,
        volume=float(last["volume"]) if last.get("volume") is not None else None,
        volume_average_20d=volume_average,
        foreign_net=foreign_net,
        foreign_net_5d=foreign_net_5d,
        investment_trust_net=trust_net,
        institutional_trend=(
            [institutional_5d_average, institutional_day]
            if institutional_5d_average is not None and institutional_day is not None
            else []
        ),
        foreign_shareholding_change_20d=getattr(item, "foreign_shareholding_change_20d", None),
        revenue_yoy=getattr(item, "revenue_yoy", None),
        revenue_mom=getattr(item, "revenue_mom", None),
        eps=getattr(item, "latest_eps", None),
        pe=getattr(item, "pe", None),
        pb=getattr(item, "pb", None),
        positive_event=positive_event,
        price_extension_pct=price_extension,
        risk_data_available=(risk_source_status == "available") if risk_source_status is not None else None,
        disposition=bool(risk.get("is_disposition")) if risk is not None else None,
        suspended=bool(risk.get("is_suspended")) if risk is not None else None,
        delisted=(risk.get("risk_reason") == "終止上市") if risk is not None else None,
        capital_reduction_critical=capital_reduction,
        regulatory_unknown=(risk_source_status != "available" or risk is None),
        severe_event_risk=bool(risk.get("has_risk_event")) if risk is not None else None,
    )


def signals_for_symbols(store: BuyPointStrategyStore, symbols: list[str]) -> list[BuyPointSignal]:
    catalog = catalog_map(store)
    assignments = store.assignments()
    output: list[BuyPointSignal] = []
    items: dict[str, Any] = {}
    assigned_symbols = [symbol for symbol in dict.fromkeys(symbols) if assignments.get(symbol)]
    try:
        from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService

        screener = TaiwanScreenerService()
        for offset in range(0, len(assigned_symbols), 200):
            scope = assigned_symbols[offset : offset + 200]
            screen = screener.run(
                TaiwanScreenerRequest(
                    page_size=max(1, len(scope)),
                    sort_by="symbol",
                    symbol_scope=scope,
                )
            )
            items.update({candidate.symbol: candidate for candidate in screen.items})
    except Exception as exc:
        logger.debug("buy-point batch screener enrichment unavailable: %s", type(exc).__name__)
    event_service = None
    events: list[Any] | None = None
    risk_source_status: str | None = None
    try:
        from app.taiwan.events_service import get_event_service

        event_service = get_event_service()
        events, risk_source_status, _risk_as_of = event_service.get_cached_regulatory_snapshot()
    except Exception as exc:
        logger.debug("buy-point regulatory evidence unavailable: %s", type(exc).__name__)
    eligible_date: str | None = None
    try:
        from app.taiwan.daily_update import resolve_target_latest_trading_date

        eligible_date = resolve_target_latest_trading_date().isoformat()
    except Exception as exc:
        logger.debug("buy-point eligible trading date unavailable: %s", type(exc).__name__)
    for symbol in symbols:
        data = build_market_data(
            symbol,
            item=items.get(symbol),
            event_service=event_service,
            events=events,
            risk_source_status=risk_source_status,
            eligible_date=eligible_date,
            load_screen=False,
        )
        for strategy_id in assignments.get(symbol, []):
            strategy = catalog.get(strategy_id)
            if strategy is not None and strategy.enabled:
                output.append(evaluate_buy_point(strategy, data))
    return output


class BuyPointService:
    """Single-symbol deterministic candidate service shared outside the HTTP layer."""

    def __init__(self, data_dir: Path, *, stop_lookback: int = 10) -> None:
        self.store = BuyPointStrategyStore(
            Path(data_dir) / "user_data" / "taiwan_buy_point_strategies.json"
        )
        self.stop_lookback = stop_lookback

    def candidate(
        self,
        symbol: str,
        strategy_id: str,
        *,
        instrument_type: str,
        evidence_as_of: str,
    ) -> BuyPointCandidate:
        strategy = catalog_map(self.store).get(strategy_id)
        if strategy is None or not strategy.enabled:
            raise ValueError("buy-point strategy is missing or disabled")
        try:
            from app.taiwan.daily_update import resolve_target_latest_trading_date

            eligible_date = resolve_target_latest_trading_date().isoformat()
        except Exception:
            eligible_date = None
        event_service = None
        events: list[Any] | None = None
        risk_source_status: str | None = None
        try:
            from app.taiwan.events_service import get_event_service

            event_service = get_event_service()
            events, risk_source_status, _risk_as_of = (
                event_service.get_cached_regulatory_snapshot()
            )
        except Exception as exc:
            logger.debug(
                "buy-point regulatory evidence unavailable for %s: %s",
                symbol,
                type(exc).__name__,
            )
        market_data = build_market_data(
            symbol,
            event_service=event_service,
            events=events,
            risk_source_status=risk_source_status,
            eligible_date=eligible_date,
        )
        signal = evaluate_buy_point(strategy, market_data)
        strategy_digest = strategy_definition_digest(strategy)
        if signal.data_as_of != evidence_as_of:
            return BuyPointCandidate(
                strategy=strategy,
                strategy_definition_digest=strategy_digest,
                signal=signal,
                plan_unavailable_reason="buy_point_evidence_date_mismatch",
            )
        if signal.status != "triggered":
            return BuyPointCandidate(
                strategy=strategy,
                strategy_definition_digest=strategy_digest,
                signal=signal,
                plan_unavailable_reason=f"buy_point_{signal.status}",
            )
        stop_rows = market_data.daily[-self.stop_lookback :]
        if not stop_rows or str(stop_rows[-1].get("date"))[:10] != evidence_as_of:
            return BuyPointCandidate(
                strategy=strategy,
                strategy_definition_digest=strategy_digest,
                signal=signal,
                plan_unavailable_reason="raw_stop_window_evidence_date_mismatch",
            )
        lows: list[float] = []
        for row in stop_rows:
            value = row.get("low")
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0:
                lows.append(number)
        if len(lows) != self.stop_lookback:
            return BuyPointCandidate(
                strategy=strategy,
                strategy_definition_digest=strategy_digest,
                signal=signal,
                plan_unavailable_reason="complete_raw_stop_lows_unavailable",
            )
        try:
            plan = build_trade_plan(
                signal,
                instrument_type="etf" if instrument_type == "etf" else "stock",
                stop_reference_price=min(lows),
                stop_lookback=self.stop_lookback,
            )
        except ValueError as exc:
            return BuyPointCandidate(
                strategy=strategy,
                strategy_definition_digest=strategy_digest,
                signal=signal,
                plan_unavailable_reason=str(exc),
            )
        return BuyPointCandidate(
            strategy=strategy,
            strategy_definition_digest=strategy_digest,
            signal=signal,
            trade_plan=plan,
        )
